"""One-transaction SQL adapter for guarded Canvas source commands."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from haoai_backend.asset_data.domain import AssetKind
from haoai_backend.asset_data.persistence import SqlAlchemyAssetDataUnitOfWork
from haoai_backend.asset_data.presentation import asset_response, storyboard_asset_response
from haoai_backend.authentication.avatar import resolve_avatar_url
from haoai_backend.series_access.application import require_series_access
from haoai_backend.series_access.domain import ActorIdentity
from haoai_backend.series_access.errors import SeriesAccessDenied, SeriesNotFound
from haoai_backend.series_access.persistence import SqlAlchemySeriesAccessReader
from haoai_backend.series_data.domain import ChapterRecord
from haoai_backend.series_data.presentation import chapter_payload
from haoai_backend.series_data.tables import (
    characters,
    chapter_locks,
    chapters,
    chat_messages,
    props,
    scenes,
    series,
    storyboard_assets,
    system_configs,
    users,
)
from haoai_backend.personal_production.notes import stage_media_reconciliation
from haoai_backend.personal_production.notes.tables import (
    personal_production_notes,
    storyboard_media_states,
)
from haoai_backend.shared.identity import TrustedActor

from .domain import SourceRows, decode_frames, referenced_storyboard_ids
from .errors import CanvasSourceConflict, CanvasSourceForbidden, CanvasSourceNotFound
from .schemas import SourceSnapshot


class SqlAlchemyCanvasSourceUnitOfWork:
    """Caller-owned Session adapter; it never commits or closes the Session."""

    def __init__(
        self,
        session: Session,
        *,
        now: Callable[[], datetime] | None = None,
        new_id: Callable[[], str] | None = None,
    ) -> None:
        self.session = session
        self.now = now or _utcnow_naive
        self.new_id = new_id or (lambda: str(uuid4()))
        self.assets = SqlAlchemyAssetDataUnitOfWork(
            session,
            now=self.now,
            new_id=self.new_id,
        )
        self._write_started = False

    def begin_write(self) -> None:
        if self._write_started:
            return
        connection = self.session.connection()
        dialect = connection.dialect.name
        if dialect == "sqlite":
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        elif dialect != "postgresql":
            raise RuntimeError("canvas-source writes require PostgreSQL or SQLite")
        self._write_started = True

    def load_source(
        self,
        chapter_id: str,
        actor: TrustedActor,
        *,
        expected_series_id: str | None = None,
        write: bool = False,
    ) -> SourceRows:
        if write:
            self.begin_write()
        chapter = self._row(
            select(chapters).where(chapters.c.id == chapter_id)
        )
        if chapter is None:
            raise CanvasSourceNotFound()
        actual_series_id = str(chapter["series_id"])
        self._require_access(actor, actual_series_id)
        if expected_series_id is not None and actual_series_id != expected_series_id:
            raise CanvasSourceConflict("章节所属剧集已变化")

        if write and self.session.get_bind().dialect.name == "postgresql":
            self._lock_postgresql_scope(actual_series_id, chapter_id)
            chapter = self._row(
                select(chapters).where(chapters.c.id == chapter_id)
            )
            if chapter is None:
                raise CanvasSourceNotFound()
            if chapter["series_id"] != actual_series_id:
                raise CanvasSourceConflict("章节所属剧集已变化")
            self._require_access(actor, actual_series_id)

        series_context = self._row(
            select(series.c.id, series.c.user_id, series.c.team_id).where(
                series.c.id == actual_series_id
            )
        )
        if series_context is None:
            raise CanvasSourceNotFound("剧集不存在")

        storyboard_rows = self._rows(
            select(storyboard_assets)
            .where(storyboard_assets.c.chapter_id == chapter_id)
            .order_by(storyboard_assets.c.id)
        )
        if any(row["series_id"] != actual_series_id for row in storyboard_rows):
            raise CanvasSourceConflict("章节包含其他剧集的故事板素材")
        character_rows = self._rows(
            select(characters).where(characters.c.series_id == actual_series_id).order_by(characters.c.id)
        )
        scene_rows = self._rows(
            select(scenes).where(scenes.c.series_id == actual_series_id).order_by(scenes.c.id)
        )
        prop_rows = self._rows(
            select(props).where(props.c.series_id == actual_series_id).order_by(props.c.id)
        )
        lock_statement = select(chapter_locks).where(
            chapter_locks.c.chapter_id == chapter_id
        ).order_by(chapter_locks.c.id)
        if write and self.session.get_bind().dialect.name == "postgresql":
            lock_statement = lock_statement.with_for_update()
        lock_rows = self._rows(lock_statement)
        if len(lock_rows) > 1:
            raise CanvasSourceConflict("章节锁记录重复，不能安全写入")
        lock = lock_rows[0] if lock_rows else None
        return SourceRows(
            series_context=series_context,
            chapter=chapter,
            storyboard_assets=tuple(storyboard_rows),
            characters=tuple(character_rows),
            scenes=tuple(scene_rows),
            props=tuple(prop_rows),
            lock=lock,
        )

    def snapshot(self, rows: SourceRows, actor: TrustedActor) -> SourceSnapshot:
        chapter = dict(rows.chapter)
        source_frames = decode_frames(chapter["content"], allow_null=True)
        referenced_storyboard_ids(
            source_frames,
            rows.storyboard_assets,
            chapter_id=str(chapter["id"]),
            series_id=str(chapter["series_id"]),
        )
        now = self.now()
        lock_payload = None
        if rows.lock is not None and rows.lock["expires_at"] > now:
            locked_user_email = self.session.execute(
                select(users.c.email).where(users.c.id == rows.lock["user_id"])
            ).scalar_one_or_none()
            lock_payload = {
                "locked": True,
                "locked_by_username": rows.lock["username"],
                "locked_by_avatar": resolve_avatar_url(locked_user_email),
                "is_mine": rows.lock["user_id"] == actor.user_id,
                "expires_at": rows.lock["expires_at"].isoformat(),
            }
        chapter_record = ChapterRecord(
            id=chapter["id"],
            series_id=chapter["series_id"],
            title=chapter["title"],
            content=chapter["content"],
            order=chapter["order"],
            created_at=chapter["created_at"],
            updated_at=chapter["updated_at"],
        )
        return SourceSnapshot.model_validate(
            {
                "capability": "canvas-source-v1",
                "series_id": chapter["series_id"],
                "chapter_id": chapter["id"],
                "requires_lock": rows.requires_lock,
                "version": rows.version,
                "chapter": chapter_payload(chapter_record, lock_payload),
                "storyboard_assets": [
                    storyboard_asset_response(row) for row in rows.storyboard_assets
                ],
                "characters": [asset_response("character", row) for row in rows.characters],
                "scenes": [asset_response("scene", row) for row in rows.scenes],
                "props": [asset_response("prop", row) for row in rows.props],
            }
        )

    def require_write_lock(self, rows: SourceRows, actor: TrustedActor) -> None:
        if not rows.requires_lock:
            return
        lock = rows.lock
        if (
            lock is None
            or lock["user_id"] != actor.user_id
            or lock["expires_at"] <= self.now()
        ):
            raise CanvasSourceConflict("编辑前请先取得当前章节锁")

    def refresh_write_lock(self, chapter_id: str, actor: TrustedActor) -> None:
        chapter = self._row(
            select(chapters.c.series_id).where(chapters.c.id == chapter_id)
        )
        if chapter is None:
            raise CanvasSourceNotFound()
        series_context = self._row(
            select(series.c.id, series.c.user_id, series.c.team_id).where(
                series.c.id == chapter["series_id"]
            )
        )
        if series_context is None:
            raise CanvasSourceNotFound("剧集不存在")
        if not series_context["team_id"]:
            return

        lock = self._row(
            select(chapter_locks)
            .where(chapter_locks.c.chapter_id == chapter_id)
            .with_for_update()
        )
        now = self.now()
        if lock is None or lock["user_id"] != actor.user_id or lock["expires_at"] <= now:
            raise CanvasSourceConflict("章节锁已失效或已被其他用户取得")
        idle_minutes = self.session.execute(
            select(system_configs.c.chapter_lock_idle_minutes).limit(1)
        ).scalar_one_or_none()
        minutes = idle_minutes if idle_minutes and idle_minutes > 0 else 15
        result = self.session.execute(
            update(chapter_locks)
            .where(
                chapter_locks.c.id == lock["id"],
                chapter_locks.c.user_id == actor.user_id,
                chapter_locks.c.expires_at > now,
            )
            .values(
                last_active_at=now,
                expires_at=now + timedelta(minutes=minutes),
            )
        )
        if result.rowcount != 1:
            raise CanvasSourceConflict("章节锁在保存时已变化")

    def compare_version(self, rows: SourceRows, expected_version: str) -> None:
        if rows.version != expected_version:
            raise CanvasSourceConflict()

    def update_chapter_content(
        self,
        rows: SourceRows,
        content: str | None,
        *,
        updated_at: datetime | None = None,
    ) -> None:
        current = rows.chapter["content"]
        statement = update(chapters).where(
            chapters.c.id == rows.chapter["id"],
            chapters.c.series_id == rows.chapter["series_id"],
        )
        statement = statement.where(
            chapters.c.content.is_(None) if current is None else chapters.c.content == current
        )
        values: dict[str, Any] = {"content": content}
        if updated_at is not None:
            values["updated_at"] = updated_at
        result = self.session.execute(statement.values(**values))
        if result.rowcount != 1:
            raise CanvasSourceConflict("章节原文在保存期间已变化")

    def update_asset(
        self,
        kind: AssetKind,
        asset_id: str,
        values: Mapping[str, Any],
        assigned_fields: frozenset[str],
    ) -> Mapping[str, Any] | None:
        return self.assets.update_asset(
            kind,
            asset_id,
            values,
            assigned_fields=assigned_fields,
        )

    def load_asset(self, kind: AssetKind, asset_id: str) -> Mapping[str, Any] | None:
        return self.assets.load_asset(kind, asset_id)

    def create_asset(self, kind: AssetKind, values: Mapping[str, Any]) -> Mapping[str, Any]:
        return self.assets.create_asset(kind, values)

    def create_storyboard_asset(self, values: Mapping[str, Any]) -> Mapping[str, Any]:
        return self.assets.create_asset("storyboard", values)

    def update_storyboard_description(
        self,
        asset: Mapping[str, Any],
        *,
        index: int,
        frame: Mapping[str, Any],
    ) -> None:
        description = frame.get("text") or frame.get("original_text") or ""
        values = {"description": description}
        changes = {key: value for key, value in values.items() if asset.get(key) != value}
        if not changes:
            return
        result = self.session.execute(
            update(storyboard_assets)
            .where(
                storyboard_assets.c.id == asset["id"],
                storyboard_assets.c.series_id == asset["series_id"],
                storyboard_assets.c.chapter_id == asset["chapter_id"],
                storyboard_assets.c.frame_index == asset["frame_index"],
                storyboard_assets.c.image_url.is_(None)
                if asset["image_url"] is None
                else storyboard_assets.c.image_url == asset["image_url"],
            )
            .values(**changes, updated_at=self.now())
        )
        if result.rowcount != 1:
            raise CanvasSourceConflict("故事板素材在保存期间已变化")

    def shift_storyboard_indexes(
        self,
        chapter_id: str,
        *,
        asset_ids: Sequence[str],
        delta: int,
    ) -> None:
        if not asset_ids:
            return
        statement = select(storyboard_assets).where(
            storyboard_assets.c.chapter_id == chapter_id,
            storyboard_assets.c.id.in_(asset_ids),
        ).order_by(storyboard_assets.c.frame_index, storyboard_assets.c.id)
        rows = self._rows(statement)
        ordered = list(reversed(rows)) if delta > 0 else rows
        for row in ordered:
            next_index = row["frame_index"] + delta
            result = self.session.execute(
                update(storyboard_assets)
                .where(
                    storyboard_assets.c.id == row["id"],
                    storyboard_assets.c.chapter_id == chapter_id,
                    storyboard_assets.c.frame_index == row["frame_index"],
                )
                .values(
                    frame_index=next_index,
                    name=f"分镜 {next_index + 1}",
                    updated_at=self.now(),
                )
            )
            if result.rowcount != 1:
                raise CanvasSourceConflict("故事板素材在调整位置时已变化")

    def delete_storyboard_asset(self, asset: Mapping[str, Any]) -> None:
        result = self.session.execute(
            delete(storyboard_assets).where(
                storyboard_assets.c.id == asset["id"],
                storyboard_assets.c.series_id == asset["series_id"],
                storyboard_assets.c.chapter_id == asset["chapter_id"],
                storyboard_assets.c.frame_index == asset["frame_index"],
                storyboard_assets.c.image_url.is_(None)
                if asset["image_url"] is None
                else storyboard_assets.c.image_url == asset["image_url"],
            )
        )
        if result.rowcount != 1:
            raise CanvasSourceConflict("故事板素材在删除期间已变化")

    def reconcile_media(
        self,
        chapter_id: str,
        *,
        content: object,
        new_assets: Sequence[Mapping[str, Any]] = (),
        deleted_asset_ids: set[str] | None = None,
        preserve_unreferenced_state_ids: Sequence[str] = (),
    ) -> None:
        stage_media_reconciliation(
            self.session,
            chapter_id,
            chapter_content_override=content,
            new_assets=new_assets,
            deleted_asset_ids=deleted_asset_ids,
            preserve_unreferenced_state_ids=preserve_unreferenced_state_ids,
        )

    def shift_chat_indexes(self, chapter_id: str, *, start_index: int, delta: int) -> None:
        predicate = (
            chat_messages.c.frame_index >= start_index
            if delta > 0
            else chat_messages.c.frame_index > start_index
        )
        rows = self._rows(
            select(chat_messages.c.id, chat_messages.c.frame_index)
            .where(chat_messages.c.chapter_id == chapter_id, predicate)
            .order_by(chat_messages.c.frame_index, chat_messages.c.id)
        )
        ordered = rows if delta > 0 else list(reversed(rows))
        for row in ordered:
            result = self.session.execute(
                update(chat_messages)
                .where(
                    chat_messages.c.id == row["id"],
                    chat_messages.c.frame_index == row["frame_index"],
                )
                .values(frame_index=row["frame_index"] + delta)
            )
            if result.rowcount != 1:
                raise CanvasSourceConflict("对话分镜位置在保存期间已变化")

    def delete_chat_frame(self, chapter_id: str, index: int) -> None:
        self.session.execute(
            delete(chat_messages).where(
                chat_messages.c.chapter_id == chapter_id,
                chat_messages.c.frame_index == index,
            )
        )

    def load_series_chapters(self, series_id: str) -> list[dict[str, Any]]:
        return self._rows(
            select(chapters).where(chapters.c.series_id == series_id).order_by(chapters.c.id)
        )

    def load_creation_side_effects(
        self,
        chapter_id: str,
        *,
        lock: bool = False,
    ) -> dict[str, Any]:
        def maybe_lock(statement):
            return statement.with_for_update() if lock else statement

        messages = self._rows(maybe_lock(
            select(chat_messages)
            .where(chat_messages.c.chapter_id == chapter_id)
            .order_by(chat_messages.c.id)
        ))
        media_states = self._rows(maybe_lock(
            select(storyboard_media_states)
            .where(storyboard_media_states.c.chapter_id == chapter_id)
            .order_by(storyboard_media_states.c.id)
        ))
        notes = self._rows(maybe_lock(
            select(personal_production_notes)
            .where(personal_production_notes.c.chapter_id == chapter_id)
            .order_by(personal_production_notes.c.id)
        ))
        return {"messages": messages, "media_states": media_states, "notes": notes}

    def delete_created_asset(
        self,
        kind: AssetKind,
        asset_id: str,
        *,
        series_id: str,
    ) -> None:
        table = self.assets._table_for(kind)
        result = self.session.execute(
            delete(table).where(table.c.id == asset_id, table.c.series_id == series_id)
        )
        if result.rowcount != 1:
            raise CanvasSourceConflict("待撤销素材已变化")

    def has_chat_asset_reference(self, kind: str, asset_id: str) -> bool:
        return (
            self.session.execute(
                select(chat_messages.c.id)
                .where(
                    chat_messages.c.asset_type == kind,
                    chat_messages.c.asset_id == asset_id,
                )
                .limit(1)
            ).scalar_one_or_none()
            is not None
        )

    def delete_created_storyboard_asset(self, chapter_id: str, series_id: str, asset_id: str) -> None:
        result = self.session.execute(
            delete(storyboard_assets).where(
                storyboard_assets.c.id == asset_id,
                storyboard_assets.c.chapter_id == chapter_id,
                storyboard_assets.c.series_id == series_id,
            )
        )
        if result.rowcount != 1:
            raise CanvasSourceConflict("待撤销故事板素材已变化")

    def delete_created_asset_media_state(self, chapter_id: str, asset_id: str) -> None:
        self.session.execute(
            delete(storyboard_media_states).where(
                storyboard_media_states.c.chapter_id == chapter_id,
                storyboard_media_states.c.storyboard_asset_id == asset_id,
            )
        )

    def remove_media_states_not_in(self, chapter_id: str, original_ids: Sequence[str]) -> None:
        statement = delete(storyboard_media_states).where(
            storyboard_media_states.c.chapter_id == chapter_id
        )
        if original_ids:
            statement = statement.where(storyboard_media_states.c.id.not_in(original_ids))
        self.session.execute(statement)

    def fingerprint_side_effects(self, chapter_id: str) -> str:
        from .domain import row_fingerprint

        return row_fingerprint(self.load_creation_side_effects(chapter_id, lock=True))

    def list_chapter_messages(self, chapter_id: str) -> list[dict[str, Any]]:
        return self._rows(select(chat_messages).where(chat_messages.c.chapter_id == chapter_id))

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()

    def close(self) -> None:
        self.session.close()

    def _row(self, statement) -> dict[str, Any] | None:
        row = self.session.execute(statement).mappings().first()
        return dict(row) if row is not None else None

    def _rows(self, statement) -> list[dict[str, Any]]:  # type: ignore[override]
        return [dict(row) for row in self.session.execute(statement).mappings().all()]

    def _require_access(self, actor: TrustedActor, series_id: str) -> None:
        try:
            require_series_access(
                SqlAlchemySeriesAccessReader(self.session),
                ActorIdentity(actor.user_id),
                series_id,
            )
        except SeriesNotFound as exc:
            raise CanvasSourceNotFound("剧集不存在") from exc
        except SeriesAccessDenied as exc:
            raise CanvasSourceForbidden(exc.detail) from exc

    def _lock_postgresql_scope(self, series_id: str, chapter_id: str) -> None:
        if self.session.get_bind().dialect.name != "postgresql":
            return
        self.session.execute(select(series).where(series.c.id == series_id).with_for_update())
        self.session.execute(
            select(chapters)
            .where(chapters.c.series_id == series_id)
            .order_by(chapters.c.id)
            .with_for_update()
        ).all()
        self.session.execute(
            select(storyboard_assets)
            .where(storyboard_assets.c.chapter_id == chapter_id)
            .order_by(storyboard_assets.c.id)
            .with_for_update()
        ).all()
        for table in (characters, scenes, props):
            self.session.execute(
                select(table)
                .where(table.c.series_id == series_id)
                .order_by(table.c.id)
                .with_for_update()
            ).all()


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
