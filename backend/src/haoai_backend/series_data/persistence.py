"""SQLAlchemy Core adapter for series, chapter and storyboard operations."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, insert, select, update, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from haoai_backend.authentication.avatar import resolve_avatar_url
from haoai_backend.series_access.application import require_series_access
from haoai_backend.series_access.domain import ActorIdentity, parse_team_permissions
from haoai_backend.series_access.errors import SeriesAccessDenied, SeriesNotFound
from haoai_backend.series_access.persistence import SqlAlchemySeriesAccessReader
from haoai_backend.shared.identity import TrustedActor

from .domain import ChapterRecord, SeriesRecord, StoryboardAssetRecord, parse_source_frames, serialize_content, source_asset_ids
from .media_writes import UNSET_CONTENT, reconcile_source_media_state
from .errors import SeriesDataBadRequest, SeriesDataConflict, SeriesDataForbidden, SeriesDataNotFound
from .chapter_edit_guard import (
    ChapterEditGuardConflict,
    ChapterEditGuardInvalid,
    decode_chapter_content,
    strict_json_equal,
    validate_frame_sources,
)
from .ports import SeriesDataUnitOfWork
from .schemas import ChapterEditGuard
from .storyboard import chat_index_mapping
from .tables import (
    ai_tasks,
    asset_duplicate_exclusions,
    canvas_documents,
    chapter_locks,
    chapters,
    characters,
    chat_messages,
    fused_assets,
    props,
    prompt_configs,
    rough_cut_drafts,
    scenes,
    series,
    storyboard_assets,
    system_configs,
    system_prompts,
    team_members,
    teams,
    users,
)


class SqlAlchemySeriesDataUnitOfWork(SeriesDataUnitOfWork):
    """One request-scoped Session; each legacy phase is committed by the use case."""

    def __init__(
        self,
        session: Session,
        *,
        now: Callable[[], datetime] | None = None,
        new_id: Callable[[], str] | None = None,
    ) -> None:
        self._session = session
        self._now = now or _utcnow
        self._new_id = new_id or (lambda: str(uuid4()))

    def ensure_clean(self) -> None:
        if self._session.new or self._session.dirty or self._session.deleted:
            raise RuntimeError("series-data requires a clean request-scoped Session")

    def require_access(self, actor: TrustedActor, series_id: str) -> None:
        try:
            require_series_access(
                SqlAlchemySeriesAccessReader(self._session),
                ActorIdentity(actor.user_id),
                series_id,
            )
        except SeriesNotFound as exc:
            raise SeriesDataNotFound("剧集不存在") from exc
        except SeriesAccessDenied as exc:
            raise SeriesDataForbidden(exc.detail) from exc

    def load_series(self, series_id: str) -> SeriesRecord | None:
        row = self._session.execute(
            select(series).where(series.c.id == series_id)
        ).mappings().first()
        return _series_record(row) if row else None

    def list_series_for_actor(self, actor: TrustedActor) -> list[dict[str, Any]]:
        team_ids = list(
            self._session.execute(
                select(team_members.c.team_id).where(team_members.c.user_id == actor.user_id)
            ).scalars()
        )
        statement = select(series)
        if team_ids:
            statement = statement.where(
                (series.c.user_id == actor.user_id) | series.c.team_id.in_(team_ids)
            )
        else:
            statement = statement.where(series.c.user_id == actor.user_id)
        rows = self._session.execute(statement.order_by(series.c.updated_at.desc())).mappings().all()
        records = [_series_record(row) for row in rows]
        if not records:
            return []

        owner_ids = {record.user_id for record in records}
        claimed_ids = {record.claimed_by for record in records if record.claimed_by}
        user_rows = self._session.execute(
            select(users.c.id, users.c.username, users.c.email).where(
                users.c.id.in_(owner_ids | claimed_ids)
            )
        ).mappings().all()
        user_map = {row["id"]: row for row in user_rows}
        team_map: dict[str, str] = {}
        if team_ids:
            team_rows = self._session.execute(
                select(teams.c.id, teams.c.name).where(teams.c.id.in_(set(team_ids)))
            ).mappings().all()
            team_map = {row["id"]: row["name"] for row in team_rows}
        membership_rows = self._session.execute(
            select(team_members.c.team_id, team_members.c.role, team_members.c.permissions).where(
                team_members.c.user_id == actor.user_id,
                team_members.c.team_id.in_(set(team_ids)) if team_ids else False,
            )
        ).mappings().all() if team_ids else []
        membership_map = {row["team_id"]: row for row in membership_rows}

        result: list[dict[str, Any]] = []
        for record in records:
            prompt = self.resolve_prompt(record.style_prompt_id)
            owner = user_map.get(record.user_id)
            claimant = user_map.get(record.claimed_by) if record.claimed_by else None
            if not record.claimed_by or record.claimed_by == actor.user_id:
                can_enter = True
            else:
                membership = membership_map.get(record.team_id)
                permissions = parse_team_permissions(membership["permissions"]) if membership else frozenset()
                can_enter = bool(membership and (membership["role"] == "owner" or "enter_claimed_series" in permissions))
            result.append({
                "id": record.id,
                "user_id": record.user_id,
                "name": record.name,
                "description": record.description,
                "image_url": record.image_url,
                "style_prompt_id": record.style_prompt_id,
                "style_prompt_name": prompt.get("name") if prompt else None,
                "style_prompt_owner_name": prompt.get("owner_name") if prompt else None,
                "style_prompt": prompt.get("content") if prompt else None,
                "team_id": record.team_id,
                "team_name": team_map.get(record.team_id),
                "owner_name": owner["username"] if owner else None,
                "claimed_by": record.claimed_by,
                "claimed_by_username": claimant["username"] if claimant else "",
                "claimed_by_avatar_url": resolve_avatar_url(claimant["email"]) if claimant else None,
                "can_enter": can_enter,
                "created_at": record.created_at,
                "updated_at": record.updated_at,
            })
        return result

    def resolve_prompt(self, prompt_id: str | None) -> dict[str, Any] | None:
        if not prompt_id:
            return None
        row = self._session.execute(
            select(prompt_configs).where(prompt_configs.c.id == prompt_id)
        ).mappings().first()
        if row:
            owner_name = None
            if row["user_id"]:
                owner_name = self._session.execute(
                    select(users.c.username).where(users.c.id == row["user_id"])
                ).scalar_one_or_none()
            return {
                "name": row["name"],
                "owner_name": owner_name,
                "content": row["user_prompt"] or row["system_prompt"] or None,
            }
        row = self._session.execute(
            select(system_prompts).where(
                system_prompts.c.id == prompt_id,
                system_prompts.c.is_active.is_(True),
            )
        ).mappings().first()
        if row:
            return {
                "name": row["name"],
                "owner_name": "系统",
                "content": row["user_prompt"] or row["system_prompt"] or None,
            }
        return None

    def load_detail_names(self, record: SeriesRecord) -> tuple[str | None, str | None]:
        owner_name = self._session.execute(select(users.c.username).where(users.c.id == record.user_id)).scalar_one_or_none()
        team_name = None
        if record.team_id:
            team_name = self._session.execute(select(teams.c.name).where(teams.c.id == record.team_id)).scalar_one_or_none()
        return team_name, owner_name

    def create_series(self, actor: TrustedActor, values: dict[str, Any]) -> SeriesRecord:
        now = self._now()
        row = {
            "id": self._new_id(), "user_id": actor.user_id, "name": values["name"],
            "description": values.get("description"), "image_url": values.get("image_url"),
            "style_prompt_id": values.get("style_prompt_id"), "team_id": None,
            "claimed_by": None, "claimed_at": None, "created_at": now, "updated_at": now,
        }
        self._session.execute(insert(series).values(**row))
        return _series_record(row)

    def update_series(self, series_id: str, values: dict[str, Any]) -> SeriesRecord:
        current = self.load_series(series_id)
        if current is None:
            raise SeriesDataNotFound("剧集不存在")
        changed = {key: value for key, value in values.items() if value is not None and getattr(current, key) != value}
        if changed:
            changed["updated_at"] = self._now()
            result = self._session.execute(update(series).where(series.c.id == series_id).values(**changed))
            _require_single_row_update(result, "series update")
        return self.load_series(series_id) or current

    def delete_series(self, actor: TrustedActor, series_id: str) -> None:
        record = self.load_series(series_id)
        if record is None:
            raise SeriesDataNotFound("剧集不存在")
        allowed = record.user_id == actor.user_id
        if not allowed:
            allowed = bool(self._session.execute(
                select(users.c.is_superuser).where(users.c.id == actor.user_id)
            ).scalar_one_or_none())
        if not allowed and record.team_id:
            membership = self._session.execute(
                select(team_members.c.role, team_members.c.permissions).where(
                    team_members.c.team_id == record.team_id,
                    team_members.c.user_id == actor.user_id,
                )
            ).mappings().first()
            if membership:
                allowed = membership["role"] == "owner" or "delete_series" in parse_team_permissions(membership["permissions"])
        if not allowed:
            raise SeriesDataForbidden("没有权限删除该剧集")

        chapter_ids = list(self._session.execute(
            select(chapters.c.id).where(chapters.c.series_id == series_id)
        ).scalars())
        if chapter_ids:
            self._session.execute(delete(rough_cut_drafts).where(rough_cut_drafts.c.chapter_id.in_(chapter_ids)))
            message_ids = list(self._session.execute(
                select(chat_messages.c.id).where(chat_messages.c.chapter_id.in_(chapter_ids))
            ).scalars())
            if message_ids:
                self._session.execute(delete(ai_tasks).where(ai_tasks.c.message_id.in_(message_ids)))
            self._session.execute(delete(chat_messages).where(chat_messages.c.chapter_id.in_(chapter_ids)))
            self._session.execute(delete(storyboard_assets).where(storyboard_assets.c.chapter_id.in_(chapter_ids)))
            self._session.execute(delete(canvas_documents).where(canvas_documents.c.chapter_id.in_(chapter_ids)))
            self._session.execute(delete(chapter_locks).where(chapter_locks.c.chapter_id.in_(chapter_ids)))
            self._session.execute(delete(chapters).where(chapters.c.id.in_(chapter_ids)))
        self._session.execute(delete(storyboard_assets).where(storyboard_assets.c.series_id == series_id))
        self._session.execute(delete(chapter_locks).where(chapter_locks.c.chapter_id.in_(
            select(chapters.c.id).where(chapters.c.series_id == series_id)
        )))
        self._session.execute(delete(asset_duplicate_exclusions).where(asset_duplicate_exclusions.c.series_id == series_id))
        self._session.execute(delete(fused_assets).where(fused_assets.c.series_id == series_id))
        for table in (characters, scenes, props):
            self._session.execute(delete(table).where(table.c.series_id == series_id))
        self._session.execute(delete(series).where(series.c.id == series_id))

    def list_chapters(self, series_id: str, actor: TrustedActor, now: datetime) -> list[dict[str, Any]]:
        self.require_access(actor, series_id)
        rows = self._session.execute(
            select(chapters).where(chapters.c.series_id == series_id).order_by(
                chapters.c.order.desc(), chapters.c.created_at.desc()
            )
        ).mappings().all()
        if not rows:
            return []
        chapter_ids = [row["id"] for row in rows]
        lock_rows = self._session.execute(
            select(chapter_locks).where(
                chapter_locks.c.chapter_id.in_(chapter_ids), chapter_locks.c.expires_at > now
            )
        ).mappings().all()
        lock_user_ids = {row["user_id"] for row in lock_rows}
        email_map: dict[str, str | None] = {}
        if lock_user_ids:
            email_map = {
                row["id"]: row["email"] for row in self._session.execute(
                    select(users.c.id, users.c.email).where(users.c.id.in_(lock_user_ids))
                ).mappings().all()
            }
        locks = {
            row["chapter_id"]: {
                "locked": True,
                "locked_by_username": row["username"],
                "locked_by_avatar": resolve_avatar_url(email_map.get(row["user_id"])),
                "is_mine": row["user_id"] == actor.user_id,
                "expires_at": row["expires_at"].isoformat() if row["expires_at"] else None,
            }
            for row in lock_rows
        }
        return [
            {"record": _chapter_record(row), "lock": locks.get(row["id"])}
            for row in rows
        ]

    def reorder_chapter(self, series_id: str, item: dict[str, Any]) -> None:
        chapter_id = item["id"]
        row = self._session.execute(
            select(chapters.c.id, chapters.c.order).where(
                chapters.c.id == chapter_id,
                chapters.c.series_id == series_id,
            )
        ).mappings().first()
        if row is None:
            return
        order = item["order"]
        if row["order"] != order:
            result = self._session.execute(
                update(chapters)
                .where(chapters.c.id == chapter_id, chapters.c.series_id == series_id)
                .values(order=order, updated_at=self._now())
            )
            _require_single_row_update(result, "chapter reorder")

    def max_chapter_order(self, series_id: str) -> int | None:
        return self._session.execute(select(func.max(chapters.c.order)).where(chapters.c.series_id == series_id)).scalar_one_or_none()

    def create_chapter(self, series_id: str, title: str, content: list[dict[str, Any]], order: int) -> ChapterRecord:
        now = self._now()
        row = {"id": self._new_id(), "series_id": series_id, "title": title,
               "content": serialize_content(content), "order": order, "created_at": now, "updated_at": now}
        self._session.execute(insert(chapters).values(**row))
        return _chapter_record(row)

    def load_chapter(self, chapter_id: str) -> ChapterRecord | None:
        row = self._session.execute(select(chapters).where(chapters.c.id == chapter_id)).mappings().first()
        return _chapter_record(row) if row else None

    def update_chapter_fields(
        self, chapter: ChapterRecord, values: dict[str, Any], actor: TrustedActor, now: datetime,
    ) -> ChapterRecord:
        current = self.load_chapter(chapter.id)
        if current is None:
            raise SeriesDataNotFound("章节不存在")
        changes: dict[str, Any] = {}
        if values.get("title") is not None and values["title"] != current.title:
            changes["title"] = values["title"]
        content_value = values.get("content", _UNSET)
        if content_value is not _UNSET and content_value is not None:
            encoded = serialize_content(content_value)
            if encoded != current.content:
                reconcile_source_media_state(self._session, current, actor, content_override=encoded)
                changes["content"] = encoded
        if values.get("order") is not None and values["order"] != current.order:
            changes["order"] = values["order"]
        if changes:
            changes["updated_at"] = now
            result = self._session.execute(update(chapters).where(chapters.c.id == current.id).values(**changes))
            _require_single_row_update(result, "chapter update")
        self._refresh_lock(current.id, actor.user_id, now)
        return self.load_chapter(current.id) or current

    def update_chapter_guarded(
        self,
        actor: TrustedActor,
        chapter_id: str,
        content: list[dict[str, Any]],
        guard: ChapterEditGuard,
        now: datetime,
    ) -> ChapterRecord:
        """Conditionally update a complete chapter in one series-data transaction."""
        self._session.begin()
        dialect = self._session.get_bind().dialect.name
        if dialect == "sqlite":
            self._session.connection().exec_driver_sql("BEGIN IMMEDIATE")

        try:
            locator = self._session.execute(
                select(chapters.c.series_id).where(chapters.c.id == chapter_id)
            ).scalar_one_or_none()
            if locator is None:
                raise SeriesDataNotFound("章节不存在")

            series_row = self._session.execute(
                select(series).where(series.c.id == locator).with_for_update()
            ).mappings().first()
            if series_row is None:
                raise SeriesDataNotFound("剧集不存在")
            chapter_row = self._session.execute(
                select(chapters).where(chapters.c.id == chapter_id).with_for_update()
            ).mappings().first()
            if chapter_row is None:
                raise SeriesDataNotFound("章节不存在")
            if chapter_row["series_id"] != locator:
                raise SeriesDataConflict("章节所属剧集已变化")
            self.require_access(actor, locator)

            if guard.expected_chapter_id != chapter_id or guard.expected_series_id != locator:
                raise ChapterEditGuardConflict("章节或剧集身份与编辑基线不一致")
            if guard.expected_order != chapter_row["order"]:
                raise ChapterEditGuardConflict("章节顺序已变化")

            old_frames = decode_chapter_content(chapter_row["content"])
            if not strict_json_equal(old_frames, guard.expected_content):
                raise ChapterEditGuardConflict("章节内容已变化")

            baseline_rows = self._session.execute(
                select(storyboard_assets)
                .where(storyboard_assets.c.chapter_id == chapter_id)
                .order_by(storyboard_assets.c.id)
                .with_for_update()
            ).mappings().all()
            expected_rows = sorted(
                (
                    item.id,
                    item.series_id,
                    item.chapter_id,
                    item.frame_index,
                )
                for item in guard.expected_storyboards
            )
            actual_rows = sorted(
                (
                    row["id"],
                    row["series_id"],
                    row["chapter_id"],
                    row["frame_index"],
                )
                for row in baseline_rows
            )
            if expected_rows != actual_rows:
                raise ChapterEditGuardConflict("分镜素材目录已变化")

            source_map = validate_frame_sources(
                old_frames,
                content,
                [item.model_dump() for item in guard.frame_sources],
                guard.deleted_source_indexes,
            )
            rows_by_id = {row["id"]: row for row in baseline_rows}
            if len(rows_by_id) != len(baseline_rows):
                raise ChapterEditGuardConflict("分镜素材编号重复，无法安全编辑")
            for old_index, frame in enumerate(old_frames or []):
                storyboard_id = _guard_storyboard_id(frame)
                if storyboard_id is None:
                    continue
                row = rows_by_id.get(storyboard_id)
                if (
                    row is None
                    or row["series_id"] != locator
                    or row["chapter_id"] != chapter_id
                    or row["frame_index"] != old_index
                ):
                    raise ChapterEditGuardConflict("原镜头分镜素材身份或位置已变化")

            lock_row = self._session.execute(
                select(chapter_locks)
                .where(chapter_locks.c.chapter_id == chapter_id)
                .with_for_update()
            ).mappings().first()
            lock_identity = guard.expected_lock_identity
            if series_row["team_id"]:
                acquired_at = _parse_lock_timestamp(lock_identity.acquired_at) if lock_identity else None
                if (
                    lock_row is None
                    or lock_identity is None
                    or lock_row["user_id"] != actor.user_id
                    or lock_identity.user_id != actor.user_id
                    or _as_utc_naive(lock_row["acquired_at"]) != acquired_at
                    or _as_utc_naive(lock_row["expires_at"]) <= _as_utc_naive(now)
                ):
                    raise ChapterEditGuardConflict("编辑锁已变化或失效")
            elif lock_identity is not None:
                raise ChapterEditGuardInvalid("个人剧集不能提交团队编辑锁身份")

            referenced: dict[Any, set[str]] = {
                characters: set(),
                scenes: set(),
                props: set(),
            }
            for frame in content:
                for field, table in (("character", characters), ("scene", scenes), ("prop", props)):
                    values = frame.get(field, [])
                    if isinstance(values, list):
                        referenced[table].update(values)
            for table, asset_ids in referenced.items():
                if not asset_ids:
                    continue
                rows = self._session.execute(
                    select(table.c.id, table.c.series_id)
                    .where(table.c.id.in_(sorted(asset_ids)))
                    .order_by(table.c.id)
                    .with_for_update()
                ).mappings().all()
                actual = {row["id"] for row in rows if row["series_id"] == locator}
                if actual != asset_ids:
                    raise ChapterEditGuardConflict("镜头引用的素材不存在或不属于当前剧集")

            deleted_storyboard_ids = {
                storyboard_id
                for source_index in guard.deleted_source_indexes
                if old_frames is not None
                for storyboard_id in [_guard_storyboard_id(old_frames[source_index])]
                if storyboard_id is not None
            }
            content_storyboard_ids = {
                storyboard_id
                for frame in old_frames or []
                for storyboard_id in [_guard_storyboard_id(frame)]
                if storyboard_id is not None
            }
            preserved_unreferenced_state_ids = {
                row["id"] for row in baseline_rows
            } - content_storyboard_ids - deleted_storyboard_ids
            encoded_content = serialize_content(content, ensure_ascii=False)
            current = _chapter_record(chapter_row)
            reconcile_source_media_state(
                self._session,
                current,
                actor,
                content_override=encoded_content,
                preserve_unreferenced_state_ids=preserved_unreferenced_state_ids,
            )
            content_predicate = (
                chapters.c.content.is_(None)
                if chapter_row["content"] is None
                else chapters.c.content == chapter_row["content"]
            )
            updated = self._session.execute(
                update(chapters)
                .where(
                    chapters.c.id == chapter_id,
                    chapters.c.series_id == locator,
                    chapters.c.order == chapter_row["order"],
                    content_predicate,
                )
                .values(content=encoded_content, updated_at=now)
            )
            if updated.rowcount != 1:
                raise ChapterEditGuardConflict("章节在保存期间已变化")

            self.sync_storyboard_assets(
                chapter_id,
                locator,
                preserve_unreferenced_rows=True,
                explicitly_deleted_ids=deleted_storyboard_ids,
                preserve_unreferenced_state_ids=preserved_unreferenced_state_ids,
            )
            self.update_chat_mapping_explicit(chapter_id, source_map)

            if series_row["team_id"]:
                commit_now = _as_utc_naive(self._now())
                if _as_utc_naive(lock_row["expires_at"]) <= commit_now:
                    raise ChapterEditGuardConflict("编辑锁在提交前已过期")
                idle = self._session.execute(
                    select(system_configs.c.chapter_lock_idle_minutes).limit(1)
                ).scalar_one_or_none()
                minutes = idle if idle else 15
                lock_refresh = self._session.execute(
                    update(chapter_locks)
                    .where(
                        chapter_locks.c.id == lock_row["id"],
                        chapter_locks.c.chapter_id == chapter_id,
                        chapter_locks.c.user_id == actor.user_id,
                        chapter_locks.c.acquired_at == lock_row["acquired_at"],
                        chapter_locks.c.expires_at > commit_now,
                    )
                    .values(
                        last_active_at=commit_now,
                        expires_at=commit_now + timedelta(minutes=minutes),
                    )
                )
                if lock_refresh.rowcount != 1:
                    raise ChapterEditGuardConflict("编辑锁在保存期间已失效")

            result = self._session.execute(
                select(chapters).where(chapters.c.id == chapter_id)
            ).mappings().first()
            if result is None:
                raise SeriesDataNotFound("章节不存在")
            self._session.commit()
            return _chapter_record(result)
        except ChapterEditGuardInvalid as exc:
            self._session.rollback()
            raise SeriesDataBadRequest(str(exc)) from exc
        except ChapterEditGuardConflict as exc:
            self._session.rollback()
            raise SeriesDataConflict(str(exc)) from exc
        except Exception:
            self._session.rollback()
            raise

    def delete_frame_first_phase(self, chapter: ChapterRecord, actor: TrustedActor, frame_index: int, frames: list[Any]) -> ChapterRecord:
        encoded = serialize_content(frames, ensure_ascii=False)
        reconcile_source_media_state(self._session, chapter, actor, content_override=encoded)
        self.delete_chat_frame_and_shift(chapter.id, frame_index)
        result = self._session.execute(
            update(chapters).where(chapters.c.id == chapter.id).values(content=encoded, updated_at=self._now())
        )
        _require_single_row_update(result, "chapter frame deletion")
        return self.load_chapter(chapter.id) or chapter

    def _refresh_lock(self, chapter_id: str, user_id: str, now: datetime) -> None:
        lock = self._session.execute(
            select(chapter_locks).where(chapter_locks.c.chapter_id == chapter_id)
        ).mappings().first()
        if not lock or lock["user_id"] != user_id:
            return
        idle = self._session.execute(
            select(system_configs.c.chapter_lock_idle_minutes).limit(1)
        ).scalar_one_or_none()
        minutes = idle if idle else 15
        result = self._session.execute(
            update(chapter_locks).where(chapter_locks.c.id == lock["id"]).values(
                last_active_at=now, expires_at=now + timedelta(minutes=minutes)
            )
        )
        _require_single_row_update(result, "chapter lock refresh")

    def delete_chapter(self, chapter: ChapterRecord) -> tuple[str, dict[str, set[object]]]:
        refs = asset_ids_from_frames(chapter.content)
        chat_ids = list(self._session.execute(
            select(chat_messages.c.id).where(chat_messages.c.chapter_id == chapter.id)
        ).scalars())
        if chat_ids:
            self._session.execute(delete(ai_tasks).where(ai_tasks.c.message_id.in_(chat_ids)))
        self._session.execute(delete(chapter_locks).where(chapter_locks.c.chapter_id == chapter.id))
        self._session.execute(delete(rough_cut_drafts).where(rough_cut_drafts.c.chapter_id == chapter.id))
        self._session.execute(delete(storyboard_assets).where(storyboard_assets.c.chapter_id == chapter.id))
        self._session.execute(delete(canvas_documents).where(canvas_documents.c.chapter_id == chapter.id))
        self._session.execute(delete(chat_messages).where(chat_messages.c.chapter_id == chapter.id))
        self._session.execute(delete(chapters).where(chapters.c.id == chapter.id))
        return chapter.series_id, refs

    def cleanup_orphans(self, series_id: str, refs: dict[str, set[object]]) -> None:
        if not any(refs.values()):
            return
        rows = self._session.execute(select(chapters.c.content).where(chapters.c.series_id == series_id)).scalars().all()
        remaining: dict[str, set[object]] = {"character": set(), "scene": set(), "prop": set()}
        for content in rows:
            parsed = source_asset_ids(content)
            for kind in remaining:
                remaining[kind].update(parsed[kind])
        for kind, table in (("character", characters), ("scene", scenes), ("prop", props)):
            orphan_ids = refs[kind] - remaining[kind]
            if orphan_ids:
                self._session.execute(delete(table).where(table.c.series_id == series_id, table.c.id.in_(orphan_ids)))

    def list_storyboard_assets(self, series_id: str, chapter_id: str | None) -> list[StoryboardAssetRecord]:
        statement = select(storyboard_assets).where(storyboard_assets.c.series_id == series_id)
        if chapter_id:
            statement = statement.where(storyboard_assets.c.chapter_id == chapter_id)
        rows = self._session.execute(statement.order_by(storyboard_assets.c.frame_index)).mappings().all()
        return [_asset_record(row) for row in rows]

    def sync_storyboard_assets(
        self,
        chapter_id: str,
        series_id: str,
        *,
        final_content: object | None = None,
        preserve_unreferenced_rows: bool = False,
        explicitly_deleted_ids: set[object] | None = None,
        preserve_unreferenced_state_ids: set[object] | None = None,
    ) -> ChapterRecord | None:
        chapter = self.load_chapter(chapter_id)
        if chapter is None:
            return None
        frames = parse_source_frames(chapter.content)
        initial_rows = self._session.execute(
            select(storyboard_assets).where(storyboard_assets.c.chapter_id == chapter_id)
        ).mappings().all()
        existing_by_id = {row["id"]: dict(row) for row in initial_rows}
        referenced_ids: set[object] = set()
        pending_asset_updates: list[tuple[object, dict[str, Any]]] = []
        refs_changed = False

        for index, frame in enumerate(frames):
            if not isinstance(frame, dict):
                continue
            if "storyboard" not in frame:
                frame["storyboard"] = []
                refs_changed = True
            raw_refs = frame.get("storyboard", [])
            if isinstance(raw_refs, list) and raw_refs:
                asset_id = raw_refs[0]
                if asset_id in existing_by_id:
                    asset = existing_by_id[asset_id]
                    referenced_ids.add(asset_id)
                    description = frame.get("text", "") or frame.get("original_text", "")
                    values = {
                        "frame_index": index,
                        "name": f"分镜 {index + 1}",
                        "description": description,
                    }
                    if any(asset.get(key) != value for key, value in values.items()):
                        values["updated_at"] = self._now()
                        pending_asset_updates.append((asset_id, dict(values)))
                        asset.update(values)
                    continue

            asset_id = self._new_id()
            asset_row = {
                "id": asset_id, "series_id": series_id, "chapter_id": chapter_id,
                "frame_index": index, "name": f"分镜 {index + 1}",
                "description": frame.get("text", "") or frame.get("original_text", ""),
                "image_url": None, "created_at": self._now(), "updated_at": self._now(),
            }
            reconcile_source_media_state(
                self._session,
                chapter,
                TrustedActor(""),
                new_assets=(asset_row,),
                preserve_unreferenced_state_ids=preserve_unreferenced_state_ids,
            )
            self._flush_storyboard_asset_updates(pending_asset_updates)
            pending_asset_updates.clear()
            self._session.execute(insert(storyboard_assets).values(**asset_row))
            referenced_ids.add(asset_id)
            frame["storyboard"] = [asset_id]
            refs_changed = True

        unreferenced_ids = {row["id"] for row in initial_rows if row["id"] not in referenced_ids}
        if explicitly_deleted_ids is not None and not set(explicitly_deleted_ids) <= unreferenced_ids:
            raise RuntimeError("explicit storyboard deletions must reference currently unreferenced rows")
        deleted_ids = (
            set(explicitly_deleted_ids or ())
            if preserve_unreferenced_rows
            else unreferenced_ids
        )
        next_content = final_content if final_content is not None else (serialize_content(frames, ensure_ascii=False) if refs_changed else chapter.content)
        content_changed = next_content != chapter.content
        if content_changed or deleted_ids:
            reconcile_source_media_state(
                self._session,
                chapter,
                TrustedActor(""),
                content_override=next_content if content_changed else _UNSET,
                deleted_asset_ids=deleted_ids,
                preserve_unreferenced_state_ids=preserve_unreferenced_state_ids,
            )
        self._flush_storyboard_asset_updates(pending_asset_updates)
        if deleted_ids:
            self._session.execute(delete(storyboard_assets).where(storyboard_assets.c.id.in_(deleted_ids)))
        if content_changed:
            result = self._session.execute(
                update(chapters).where(chapters.c.id == chapter_id).values(content=next_content, updated_at=self._now())
            )
            _require_single_row_update(result, "chapter storyboard sync")
        return self.load_chapter(chapter_id)

    def _flush_storyboard_asset_updates(
        self,
        pending_updates: list[tuple[object, dict[str, Any]]],
    ) -> None:
        for asset_id, values in pending_updates:
            result = self._session.execute(
                update(storyboard_assets)
                .where(storyboard_assets.c.id == asset_id)
                .values(**values)
            )
            _require_single_row_update(result, "storyboard asset update")

    def list_chat_messages(self, chapter_id: str) -> list[dict[str, Any]]:
        return [dict(row) for row in self._session.execute(
            select(chat_messages).where(chat_messages.c.chapter_id == chapter_id)
        ).mappings().all()]

    def update_chat_mapping(self, chapter_id: str, old_frames: list[Any], new_frames: list[Any]) -> None:
        mapping = chat_index_mapping(old_frames, new_frames)
        if not mapping:
            return
        rows = self._session.execute(
            select(chat_messages.c.id, chat_messages.c.frame_index).where(
                chat_messages.c.chapter_id == chapter_id,
                chat_messages.c.frame_index.is_not(None),
            )
        ).mappings().all()
        for row in rows:
            next_index = mapping.get(row["frame_index"])
            if next_index is None:
                self._session.execute(delete(chat_messages).where(chat_messages.c.id == row["id"]))
            else:
                result = self._session.execute(update(chat_messages).where(chat_messages.c.id == row["id"]).values(frame_index=next_index))
                _require_single_row_update(result, "chat frame remap")

    def update_chat_mapping_explicit(
        self,
        chapter_id: str,
        old_to_new: dict[int, int | None],
    ) -> None:
        """Apply a complete source-index mapping, including an empty mapping."""
        rows = self._session.execute(
            select(chat_messages.c.id, chat_messages.c.frame_index)
            .where(
                chat_messages.c.chapter_id == chapter_id,
                chat_messages.c.frame_index.is_not(None),
            )
            .order_by(chat_messages.c.id)
            .with_for_update()
        ).mappings().all()
        for row in rows:
            old_index = row["frame_index"]
            if type(old_index) is not int or old_index not in old_to_new:
                raise SeriesDataConflict("聊天记录关联了无法映射的原镜头")
            new_index = old_to_new[old_index]
            if new_index is None:
                self._session.execute(delete(chat_messages).where(chat_messages.c.id == row["id"]))
            else:
                result = self._session.execute(
                    update(chat_messages)
                    .where(chat_messages.c.id == row["id"])
                    .values(frame_index=new_index)
                )
                _require_single_row_update(result, "explicit chat frame remap")

    def delete_chat_frame_and_shift(self, chapter_id: str, frame_index: int) -> None:
        self._session.execute(delete(chat_messages).where(
            chat_messages.c.chapter_id == chapter_id,
            chat_messages.c.frame_index == frame_index,
            chat_messages.c.frame_index.is_not(None),
        ))
        rows = self._session.execute(select(chat_messages.c.id, chat_messages.c.frame_index).where(
            chat_messages.c.chapter_id == chapter_id,
            chat_messages.c.frame_index > frame_index,
            chat_messages.c.frame_index.is_not(None),
        )).mappings().all()
        for row in rows:
            result = self._session.execute(update(chat_messages).where(chat_messages.c.id == row["id"]).values(frame_index=row["frame_index"] - 1))
            _require_single_row_update(result, "chat frame shift")

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()

    def close(self) -> None:
        self._session.close()

    def is_integrity_error(self, error: BaseException) -> bool:
        return isinstance(error, IntegrityError)



def _require_single_row_update(result: Any, operation: str) -> None:
    if result.rowcount != 1:
        raise RuntimeError(f"{operation} expected one row to change; got {result.rowcount}")



def asset_ids_from_frames(content: object) -> dict[str, set[object]]:
    return source_asset_ids(content)



_UNSET = UNSET_CONTENT


def _series_record(row: Any) -> SeriesRecord:
    return SeriesRecord(
        id=row["id"], user_id=row["user_id"], name=row["name"],
        description=row["description"], image_url=row["image_url"],
        style_prompt_id=row["style_prompt_id"], team_id=row["team_id"], claimed_by=row["claimed_by"],
        created_at=row["created_at"], updated_at=row["updated_at"],
    )


def _chapter_record(row: Any) -> ChapterRecord:
    return ChapterRecord(
        id=row["id"], series_id=row["series_id"], title=row["title"],
        content=row["content"], order=row["order"], created_at=row["created_at"], updated_at=row["updated_at"],
    )


def _asset_record(row: Any) -> StoryboardAssetRecord:
    return StoryboardAssetRecord(
        id=row["id"], series_id=row["series_id"], chapter_id=row["chapter_id"],
        frame_index=row["frame_index"], name=row["name"], description=row["description"],
        image_url=row["image_url"], created_at=row["created_at"], updated_at=row["updated_at"],
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _guard_storyboard_id(frame: dict[str, Any]) -> str | None:
    value = frame.get("storyboard", [])
    if value == []:
        return None
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], str):
        raise ChapterEditGuardConflict("章节原分镜素材引用无法核对")
    return value[0]


def _parse_lock_timestamp(value: str) -> datetime:
    try:
        return _as_utc_naive(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except (TypeError, ValueError) as exc:
        raise ChapterEditGuardInvalid("编辑锁时间格式无效") from exc


def _as_utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)

