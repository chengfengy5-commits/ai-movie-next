"""SQLAlchemy Core adapter for series exchange."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, insert, select, update
from sqlalchemy.orm import Session

from haoai_backend.asset_data.persistence import SqlAlchemyAssetDataUnitOfWork
from haoai_backend.series_access.persistence import SqlAlchemySeriesAccessReader
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.series_data.tables import (
    chapters,
    characters,
    props,
    scenes,
    series,
    storyboard_assets,
)
from haoai_backend.teams.tables import team_members, teams

from .domain import FusedAssetRecord, ImportPlan
from .ports import SeriesExchangeUnitOfWork
from .tables import fused_assets


class SqlAlchemySeriesExchangeUnitOfWork(SeriesExchangeUnitOfWork):
    """A single request-owned business Session with explicit transaction control."""

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
        self._asset_data = SqlAlchemyAssetDataUnitOfWork(
            session,
            now=self._now,
            new_id=self._new_id,
        )

    def load_series(self, series_id: str):
        return self._access_reader.load_series(series_id)

    def load_team_membership(self, team_id: str, user_id: str):
        return self._access_reader.load_team_membership(team_id, user_id)

    def load_username(self, user_id: str) -> str | None:
        return self._access_reader.load_username(user_id)

    def new_id(self) -> str:
        return self._new_id()

    def current_time(self) -> datetime:
        return self._now()

    def load_fused_asset(self, asset_id: str) -> FusedAssetRecord | None:
        row = self._session.execute(
            select(fused_assets).where(fused_assets.c.id == asset_id)
        ).mappings().first()
        return _fused_record(row) if row is not None else None

    def list_fused_assets(self, series_id: str) -> Sequence[FusedAssetRecord]:
        rows = self._session.execute(
            select(fused_assets)
            .where(fused_assets.c.series_id == series_id)
            .order_by(fused_assets.c.created_at.desc())
        ).mappings().all()
        return [_fused_record(row) for row in rows]

    def update_fused_asset_fields(
        self, asset_id: str, values: Mapping[str, Any]
    ) -> None:
        if not values:
            return
        allowed_fields = {"name", "member_type", "members", "image_url", "size"}
        if not set(values).issubset(allowed_fields):
            raise ValueError("unsupported fused-asset field")
        self._session.execute(
            update(fused_assets)
            .where(fused_assets.c.id == asset_id)
            .values(**dict(values), updated_at=self.current_time())
        )

    def delete_fused_asset(self, asset_id: str) -> bool:
        result = self._session.execute(
            delete(fused_assets).where(fused_assets.c.id == asset_id)
        )
        return result.rowcount == 1

    def list_chapters(self, series_id: str) -> Sequence[Mapping[str, Any]]:
        return self._asset_data.list_chapters(series_id)

    def lock_chapters(self, chapter_ids: Sequence[str]) -> Sequence[Mapping[str, Any]]:
        return self._asset_data.lock_chapters(chapter_ids)

    def update_chapter_content(
        self,
        chapter_id: str,
        expected_content: object,
        content: str,
        *,
        updated_at: datetime,
    ) -> bool:
        return self._asset_data.update_chapter_content(
            chapter_id,
            expected_content,
            content,
            updated_at=updated_at,
        )

    def reconcile_chapter_media(
        self,
        chapter: Mapping[str, Any],
        actor: TrustedActor,
        *,
        content_override: object | None = None,
    ) -> None:
        self._asset_data.reconcile_chapter_media(
            chapter,
            actor,
            content_override=content_override,
        )

    def load_series_for_export(self, series_id: str) -> Mapping[str, Any] | None:
        row = self._session.execute(
            select(series.c.id, series.c.name, series.c.description).where(
                series.c.id == series_id
            )
        ).mappings().first()
        return dict(row) if row is not None else None

    def list_export_chapters(self, series_id: str) -> Sequence[Mapping[str, Any]]:
        rows = self._session.execute(
            select(
                chapters.c.id,
                chapters.c.title,
                chapters.c.content,
                chapters.c.order,
                chapters.c.created_at,
            )
            .where(chapters.c.series_id == series_id)
            .order_by(chapters.c.order.desc(), chapters.c.created_at.desc())
        ).mappings().all()
        return [dict(row) for row in rows]

    def list_export_characters(self, series_id: str) -> Sequence[Mapping[str, Any]]:
        rows = self._session.execute(
            select(
                characters.c.id,
                characters.c.name,
                characters.c.gender,
                characters.c.age,
                characters.c.role,
                characters.c.appearance,
                characters.c.description,
                characters.c.image_url,
                characters.c.audio_url,
            ).where(characters.c.series_id == series_id)
        ).mappings().all()
        return [dict(row) for row in rows]

    def list_export_scenes(self, series_id: str) -> Sequence[Mapping[str, Any]]:
        rows = self._session.execute(
            select(
                scenes.c.id,
                scenes.c.title,
                scenes.c.description,
                scenes.c.image_url,
            ).where(scenes.c.series_id == series_id)
        ).mappings().all()
        return [dict(row) for row in rows]

    def list_export_props(self, series_id: str) -> Sequence[Mapping[str, Any]]:
        rows = self._session.execute(
            select(
                props.c.id,
                props.c.name,
                props.c.description,
                props.c.image_url,
            ).where(props.c.series_id == series_id)
        ).mappings().all()
        return [dict(row) for row in rows]

    def list_export_storyboard_assets(
        self, series_id: str
    ) -> Sequence[Mapping[str, Any]]:
        rows = self._session.execute(
            select(
                storyboard_assets.c.id,
                storyboard_assets.c.chapter_id,
                storyboard_assets.c.name,
                storyboard_assets.c.description,
                storyboard_assets.c.image_url,
                storyboard_assets.c.frame_index,
            ).where(storyboard_assets.c.series_id == series_id)
        ).mappings().all()
        return [dict(row) for row in rows]

    def team_exists(self, team_id: str) -> bool:
        return (
            self._session.execute(
                select(teams.c.id).where(teams.c.id == team_id)
            ).scalar_one_or_none()
            is not None
        )

    def is_team_member(self, team_id: str, user_id: str) -> bool:
        return (
            self._session.execute(
                select(team_members.c.id).where(
                    team_members.c.team_id == team_id,
                    team_members.c.user_id == user_id,
                )
            ).scalar_one_or_none()
            is not None
        )

    def insert_import_plan(self, plan: ImportPlan) -> None:
        self._session.execute(insert(series).values(**dict(plan.series)))
        self._insert_rows(characters, plan.characters)
        self._insert_rows(scenes, plan.scenes)
        self._insert_rows(props, plan.props)
        self._insert_rows(chapters, plan.chapters)
        self._insert_rows(storyboard_assets, plan.storyboard_assets)

    def refresh_imported_series(self, series_id: str) -> Mapping[str, Any] | None:
        row = self._session.execute(
            select(series.c.id, series.c.name).where(series.c.id == series_id)
        ).mappings().first()
        return dict(row) if row is not None else None

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()

    def close(self) -> None:
        self._session.close()

    def _insert_rows(self, table: Any, rows: Sequence[Mapping[str, Any]]) -> None:
        if rows:
            self._session.execute(insert(table), [dict(row) for row in rows])


def series_exchange_uow_factory(
    session_factory: Callable[[], Session],
    *,
    now: Callable[[], datetime] | None = None,
    new_id: Callable[[], str] | None = None,
) -> Callable[[], SqlAlchemySeriesExchangeUnitOfWork]:
    """Create request-local UoWs without opening a Session during composition."""

    def create() -> SqlAlchemySeriesExchangeUnitOfWork:
        return SqlAlchemySeriesExchangeUnitOfWork(
            session_factory(),
            now=now,
            new_id=new_id,
        )

    return create


def _fused_record(row: Mapping[str, Any]) -> FusedAssetRecord:
    return FusedAssetRecord(
        id=row["id"],
        series_id=row["series_id"],
        member_type=row["member_type"],
        members=row["members"],
        name=row["name"],
        image_url=row["image_url"],
        size=row["size"],
        status=row["status"],
        error_message=row["error_message"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
