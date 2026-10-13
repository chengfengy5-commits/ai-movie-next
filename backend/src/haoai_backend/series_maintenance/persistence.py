"""SQLAlchemy Core persistence for series asset-maintenance routes."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    Column, DateTime, Index, MetaData, String, Table, Text,
    delete, insert, select, update,
)
from sqlalchemy.orm import Session

from haoai_backend.asset_data.tables import ASSET_TABLES, chapters
from haoai_backend.series_access.persistence import SqlAlchemySeriesAccessReader

from .ports import SeriesMaintenanceUnitOfWork


metadata = MetaData()

duplicate_exclusions = Table(
    "asset_duplicate_exclusions",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("series_id", String(36), nullable=False),
    Column("asset_type", String(20), nullable=False),
    Column("asset_id_a", String(36), nullable=False),
    Column("asset_id_b", String(36), nullable=False),
    Column("created_by", String(36), nullable=True),
    Column("created_at", DateTime, nullable=False),
    Index(
        "ix_series_maintenance_duplicate_scope",
        "series_id",
        "asset_type",
        "created_at",
    ),
)

fused_assets = Table(
    "fused_assets",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("series_id", String(36), nullable=False),
    Column("member_type", String(20), nullable=False),
    Column("members", Text, nullable=True),
    Column("name", String(255), nullable=False),
    Column("image_url", String(500), nullable=True),
    Column("size", String(50), nullable=True),
    Column("status", String(20), nullable=False),
    Column("error_message", Text, nullable=True),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    Index("ix_series_maintenance_fused_scope", "series_id", "member_type"),
)


class SqlAlchemySeriesMaintenanceUnitOfWork(SeriesMaintenanceUnitOfWork):
    """Request-scoped adapter; use cases own commit, rollback and close."""

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

    def list_chapters(self, series_id: str) -> Sequence[Mapping[str, Any]]:
        rows = self._session.execute(
            select(chapters).where(chapters.c.series_id == series_id)
        ).mappings().all()
        return [dict(row) for row in rows]

    def list_assets(self, asset_type: str, series_id: str) -> Sequence[Mapping[str, Any]]:
        table = ASSET_TABLES[asset_type]
        rows = self._session.execute(
            select(table).where(table.c.series_id == series_id)
        ).mappings().all()
        return [dict(row) for row in rows]

    def list_assets_by_ids(
        self, asset_type: str, series_id: str, asset_ids: Sequence[str]
    ) -> Sequence[Mapping[str, Any]]:
        if not asset_ids:
            return []
        table = ASSET_TABLES[asset_type]
        rows = self._session.execute(
            select(table).where(
                table.c.id.in_(tuple(asset_ids)),
                table.c.series_id == series_id,
            )
        ).mappings().all()
        return [dict(row) for row in rows]

    def load_asset(
        self,
        asset_type: str,
        asset_id: str,
        *,
        series_id: str | None = None,
    ) -> Mapping[str, Any] | None:
        table = ASSET_TABLES[asset_type]
        statement = select(table).where(table.c.id == asset_id)
        if series_id is not None:
            statement = statement.where(table.c.series_id == series_id)
        row = self._session.execute(statement).mappings().first()
        return dict(row) if row is not None else None

    def list_duplicate_exclusions(
        self,
        series_id: str,
        asset_type: str,
        *,
        newest_first: bool = False,
    ) -> Sequence[Mapping[str, Any]]:
        statement = select(duplicate_exclusions).where(
            duplicate_exclusions.c.series_id == series_id,
            duplicate_exclusions.c.asset_type == asset_type,
        )
        if newest_first:
            statement = statement.order_by(duplicate_exclusions.c.created_at.desc())
        rows = self._session.execute(statement).mappings().all()
        return [dict(row) for row in rows]

    def delete_duplicate_exclusions(self, exclusion_ids: Sequence[str]) -> None:
        if exclusion_ids:
            self._session.execute(
                delete(duplicate_exclusions).where(
                    duplicate_exclusions.c.id.in_(tuple(exclusion_ids))
                )
            )

    def delete_duplicate_pairs(
        self,
        series_id: str,
        asset_type: str,
        pairs: Sequence[tuple[str, str]],
    ) -> int:
        removed = 0
        for asset_id_a, asset_id_b in pairs:
            result = self._session.execute(
                delete(duplicate_exclusions).where(
                    duplicate_exclusions.c.series_id == series_id,
                    duplicate_exclusions.c.asset_type == asset_type,
                    duplicate_exclusions.c.asset_id_a == asset_id_a,
                    duplicate_exclusions.c.asset_id_b == asset_id_b,
                )
            )
            removed += max(result.rowcount or 0, 0)
        return removed

    def add_duplicate_exclusion(
        self,
        *,
        series_id: str,
        asset_type: str,
        pair: tuple[str, str],
        created_by: str,
    ) -> None:
        self._session.execute(
            insert(duplicate_exclusions).values(
                id=self.new_id(),
                series_id=series_id,
                asset_type=asset_type,
                asset_id_a=pair[0],
                asset_id_b=pair[1],
                created_by=created_by,
                created_at=self.current_time(),
            )
        )

    def list_fused_assets(
        self, series_id: str, asset_type: str
    ) -> Sequence[Mapping[str, Any]]:
        rows = self._session.execute(
            select(fused_assets).where(
                fused_assets.c.series_id == series_id,
                fused_assets.c.member_type == asset_type,
            )
        ).mappings().all()
        return [dict(row) for row in rows]

    def update_fused_members(self, fused_asset_id: str, members: object) -> None:
        self._session.execute(
            update(fused_assets)
            .where(fused_assets.c.id == fused_asset_id)
            .values(members=members, updated_at=self.current_time())
        )

    def update_asset_fields(
        self, asset_type: str, asset_id: str, values: Mapping[str, Any]
    ) -> None:
        if not values:
            return
        table = ASSET_TABLES[asset_type]
        self._session.execute(
            update(table)
            .where(table.c.id == asset_id)
            .values(**dict(values), updated_at=self.current_time())
        )

    def update_chapter_content(self, chapter_id: str, content: str) -> None:
        self._session.execute(
            update(chapters)
            .where(chapters.c.id == chapter_id)
            .values(content=content, updated_at=self.current_time())
        )

    def delete_asset(self, asset_type: str, asset_id: str) -> None:
        table = ASSET_TABLES[asset_type]
        self._session.execute(delete(table).where(table.c.id == asset_id))

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()

    def close(self) -> None:
        self._session.close()


def series_maintenance_uow_factory(
    session_factory: Callable[[], Session],
) -> Callable[[], SqlAlchemySeriesMaintenanceUnitOfWork]:
    """Create database sessions lazily, only when a request opens its unit of work."""

    def create() -> SqlAlchemySeriesMaintenanceUnitOfWork:
        return SqlAlchemySeriesMaintenanceUnitOfWork(session_factory())

    return create
