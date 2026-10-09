"""SQLAlchemy Core unit of work for asset-data operations."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from haoai_backend.series_access.domain import ActorIdentity
from haoai_backend.series_access.errors import SeriesAccessDenied, SeriesNotFound
from haoai_backend.series_access.persistence import SqlAlchemySeriesAccessReader
from haoai_backend.shared.identity import TrustedActor

from .domain import ASSET_KINDS, AssetKind
from .errors import AssetDataForbidden, AssetDataNotFound, AssetDataWriteConflict
from .media_writes import (
    reconcile_source_media_state,
)
from .naming import dump_aliases, make_key
from .tables import (
    ASSET_TABLES,
    chapter_locks,
    chapters,
    storyboard_assets,
    system_configs,
)


class SqlAlchemyAssetDataUnitOfWork:
    """Request-scoped Core adapter; callers own commit, rollback and close."""

    def __init__(
        self,
        session: Session,
        *,
        now: Callable[[], datetime] | None = None,
        new_id: Callable[[], str] | None = None,
    ) -> None:
        self._session = session
        self._now = now or datetime.utcnow
        self._new_id = new_id or (lambda: str(uuid4()))
        self._access_reader = SqlAlchemySeriesAccessReader(session)

    def ensure_clean(self) -> None:
        if self._session.new or self._session.dirty or self._session.deleted:
            raise RuntimeError("asset-data requires a clean request-scoped Session")

    def current_time(self) -> datetime:
        return self._now()

    def new_id(self) -> str:
        return self._new_id()

    def load_series(self, series_id: str):
        return self._access_reader.load_series(series_id)

    def load_team_membership(self, team_id: str, user_id: str):
        return self._access_reader.load_team_membership(team_id, user_id)

    def load_username(self, user_id: str) -> str | None:
        return self._access_reader.load_username(user_id)

    def load_chapter(
        self,
        chapter_id: str,
        *,
        lock: bool = False,
    ) -> Mapping[str, Any] | None:
        statement = select(chapters).where(chapters.c.id == chapter_id)
        if lock:
            statement = statement.with_for_update()
        row = self._session.execute(statement).mappings().first()
        return dict(row) if row is not None else None

    def list_chapters(self, series_id: str) -> Sequence[Mapping[str, Any]]:
        rows = self._session.execute(
            select(chapters).where(chapters.c.series_id == series_id)
        ).mappings().all()
        return [dict(row) for row in rows]

    def lock_chapters(self, chapter_ids: Sequence[str]) -> Sequence[Mapping[str, Any]]:
        locked: list[Mapping[str, Any]] = []
        for chapter_id in sorted(set(chapter_ids)):
            row = self._session.execute(
                select(chapters)
                .where(chapters.c.id == chapter_id)
                .with_for_update()
            ).mappings().first()
            if row is not None:
                locked.append(dict(row))
        return locked

    def list_assets(
        self,
        kind: AssetKind,
        series_id: str,
        *,
        chapter_id: str | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        table = ASSET_TABLES[kind]
        statement = select(table).where(table.c.series_id == series_id)
        if chapter_id is not None:
            statement = statement.where(table.c.chapter_id == chapter_id)
        return [dict(row) for row in self._session.execute(statement).mappings().all()]

    def load_asset(
        self,
        kind: AssetKind | str,
        asset_id: str,
        *,
        lock: bool = False,
    ) -> Mapping[str, Any] | None:
        table = self._table_for(kind)
        statement = select(table).where(table.c.id == asset_id)
        if lock:
            statement = statement.with_for_update()
        row = self._session.execute(statement).mappings().first()
        return dict(row) if row is not None else None

    def create_asset(
        self,
        kind: AssetKind | str,
        values: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        table = self._table_for(kind)
        now = self._now()
        row = dict(values)
        if "id" not in row:
            row["id"] = self._new_id()
        row.setdefault("created_at", now)
        row.setdefault("updated_at", now)
        if kind in ASSET_KINDS:
            name_field = "title" if kind == "scene" else "name"
            row["canonical_key"] = make_key(kind, row.get(name_field))
            if not row.get("aliases"):
                row["aliases"] = dump_aliases([])
        result = self._session.execute(insert(table).values(**row))
        if result.rowcount != 1:
            raise AssetDataWriteConflict("素材创建未能写入")
        stored = self._session.execute(
            select(table).where(table.c.id == row["id"])
        ).mappings().one()
        return dict(stored)

    def update_asset(
        self,
        kind: AssetKind | str,
        asset_id: str,
        values: Mapping[str, Any],
        *,
        assigned_fields: frozenset[str],
        expected_storyboard_facts: tuple[str, object] | None = None,
    ) -> Mapping[str, Any] | None:
        table = self._table_for(kind)
        if expected_storyboard_facts is not None and kind != "storyboard":
            raise ValueError("expected storyboard facts require a storyboard asset")
        row = self._session.execute(
            select(table).where(table.c.id == asset_id)
        ).mappings().first()
        if row is None:
            return None

        current = dict(row)
        # Values and fields are the write set calculated from the first row by
        # the application layer. Do not rebase that intent on this existence read.
        changes = {field: values[field] for field in assigned_fields if field in values}
        if changes:
            changes["updated_at"] = self._now()
            statement = update(table).where(table.c.id == asset_id)
            if expected_storyboard_facts is not None:
                expected_chapter_id, expected_image_url = expected_storyboard_facts
                statement = statement.where(table.c.chapter_id == expected_chapter_id)
                if expected_image_url is None:
                    statement = statement.where(table.c.image_url.is_(None))
                else:
                    statement = statement.where(table.c.image_url == expected_image_url)
            result = self._session.execute(statement.values(**changes))
            _require_single_row_update(result, f"{kind} update")
            current.update(changes)
        return current

    def delete_asset(self, kind: AssetKind | str, asset_id: str) -> bool:
        table = self._table_for(kind)
        result = self._session.execute(delete(table).where(table.c.id == asset_id))
        _require_single_row_update(result, f"{kind} delete")
        return True

    def update_chapter_content(
        self,
        chapter_id: str,
        expected_content: object,
        content: str,
        *,
        updated_at: datetime,
    ) -> bool:
        statement = update(chapters).where(chapters.c.id == chapter_id)
        if expected_content is None:
            statement = statement.where(chapters.c.content.is_(None))
        else:
            statement = statement.where(chapters.c.content == expected_content)
        result = self._session.execute(
            statement.values(content=content, updated_at=updated_at)
        )
        return result.rowcount == 1

    def reconcile_chapter_media(
        self,
        chapter: Mapping[str, Any],
        actor: TrustedActor,
        *,
        content_override: object | None = None,
        new_assets: Sequence[Mapping[str, Any]] = (),
        asset_image_overrides: Mapping[str, object] | None = None,
        deleted_asset_ids: set[str] | None = None,
    ) -> None:
        kwargs: dict[str, Any] = {
            "new_assets": new_assets,
            "asset_image_overrides": asset_image_overrides,
            "deleted_asset_ids": deleted_asset_ids,
        }
        if content_override is not None:
            kwargs["content_override"] = content_override
        reconcile_source_media_state(
            self._session,
            str(chapter["id"]),
            actor,
            **kwargs,
        )

    def refresh_chapter_lock(self, chapter_id: str, user_id: str) -> None:
        lock = self._session.execute(
            select(chapter_locks).where(chapter_locks.c.chapter_id == chapter_id)
        ).mappings().first()
        if lock is None or lock["user_id"] != user_id:
            return

        idle_minutes = self._session.execute(
            select(system_configs.c.chapter_lock_idle_minutes).limit(1)
        ).scalar_one_or_none()
        minutes = idle_minutes if idle_minutes else 15
        now = self._now()
        result = self._session.execute(
            update(chapter_locks)
            .where(chapter_locks.c.id == lock["id"])
            .values(
                last_active_at=now,
                expires_at=now + timedelta(minutes=minutes),
            )
        )
        _require_single_row_update(result, "chapter lock refresh")

    def is_integrity_error(self, error: BaseException) -> bool:
        return isinstance(error, IntegrityError)

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()

    def close(self) -> None:
        self._session.close()

    @staticmethod
    def _table_for(kind: AssetKind | str):
        if kind in ASSET_TABLES:
            return ASSET_TABLES[kind]
        if kind == "storyboard":
            return storyboard_assets
        raise ValueError(f"unsupported asset kind: {kind}")


def _require_single_row_update(result: Any, operation: str) -> None:
    if result.rowcount != 1:
        raise AssetDataWriteConflict(f"{operation} 未能更新唯一目标")
