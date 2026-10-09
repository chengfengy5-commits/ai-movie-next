"""SQLAlchemy adapter for the series-family query and write-intent port."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Column,
    DateTime,
    MetaData,
    String,
    Table,
    Text,
    delete,
    func,
    select,
)
from sqlalchemy.orm import Session

from haoai_backend.teams import assignment_plan, tables
from haoai_backend.teams.domain import TeamRecord
from haoai_backend.teams.persistence import (
    TeamSqlAlchemyUnitOfWork,
    epoch_seconds,
    new_id,
    new_invite_code,
    now_utc_naive,
)
from haoai_backend.teams.ports import TeamsUnitOfWork
from .domain import TeamSeriesPage
from .ports import SeriesUnitOfWorkFactory


_COUNT_TABLES = {
    "chapter_count": tables.chapters,
    "characters": tables.characters,
    "scenes": tables.scenes,
    "props": tables.props,
    "storyboard": tables.storyboard_assets,
}


def _series_table(uow: TeamsUnitOfWork) -> Table:
    """Keep the source updated_at default local and flush-time evaluated."""
    metadata = MetaData()

    def updated_at(_context: Any) -> datetime:
        return uow.now_utc_naive()

    return Table(
        "series",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("user_id", String(36), nullable=False),
        Column("name", String(255), nullable=False),
        Column("description", Text, nullable=True),
        Column("image_url", String(500), nullable=True),
        Column("style_prompt_id", String(36), nullable=True),
        Column("team_id", String(36), nullable=True),
        Column("claimed_by", String(36), nullable=True),
        Column("claimed_at", DateTime, nullable=True),
        Column("created_at", DateTime, nullable=False),
        Column("updated_at", DateTime, nullable=False, onupdate=updated_at),
    )


class SeriesSqlAlchemyUnitOfWork(TeamSqlAlchemyUnitOfWork):
    """Shared request UoW plus the series family's explicit SQL adapter."""

    def __init__(
        self,
        session: Session,
        *,
        utc_clock: Callable[[], datetime] = now_utc_naive,
        epoch_clock: Callable[[], float] = epoch_seconds,
        id_source: Callable[[], str] = new_id,
        invite_code_source: Callable[[], str] = new_invite_code,
    ) -> None:
        super().__init__(
            session,
            utc_clock=utc_clock,
            epoch_clock=epoch_clock,
            id_source=id_source,
            invite_code_source=invite_code_source,
        )
        self._series_table = _series_table(self)

    def load_series(
        self,
        series_id: str,
        *,
        team_id: str | None = None,
    ) -> Mapping[str, Any] | None:
        if team_id is None:
            return self.load_row(self._series_table, {"id": series_id})
        return self.load_row(
            self._series_table,
            {"id": series_id},
            self._series_table.c.team_id == team_id,
        )

    def list_team_series_page(
        self,
        team_id: str,
        page: int,
        page_size: int,
    ) -> TeamSeriesPage:
        total = int(
            self.execute_immediate(
                select(func.count())
                .select_from(tables.series)
                .where(tables.series.c.team_id == team_id)
            ).scalar_one()
        )
        rows = self.execute_immediate(
            select(tables.series)
            .where(tables.series.c.team_id == team_id)
            .order_by(tables.series.c.updated_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).mappings().all()

        series_ids = [row["id"] for row in rows]
        user_ids = [row["user_id"] for row in rows]
        user_ids.extend(row["claimed_by"] for row in rows if row["claimed_by"])
        users = self.load_users(user_ids)

        counts: dict[str, dict[str, int]] = {name: {} for name in _COUNT_TABLES}
        if series_ids:
            for name, table in _COUNT_TABLES.items():
                grouped = self.execute_immediate(
                    select(table.c.series_id, func.count(table.c.id))
                    .where(table.c.series_id.in_(series_ids))
                    .group_by(table.c.series_id)
                ).all()
                counts[name] = {
                    series_id: int(count)
                    for series_id, count in grouped
                }

        return TeamSeriesPage(
            total=total,
            rows=tuple(rows),
            users=users,
            counts=counts,
        )

    def stage_series_insert(self, values: Mapping[str, Any]) -> None:
        self.stage_insert(self._series_table, values)

    def stage_series_assignment(
        self,
        row: Mapping[str, Any],
        assigned_values: Mapping[str, Any],
    ) -> None:
        plan = assignment_plan(
            self._series_table,
            {"id": row["id"]},
            row,
            assigned_values,
        )
        self.stage_assignment(plan)

    def release_series_locks(self, series_id: str) -> None:
        chapter_ids = self.execute_immediate(
            select(tables.chapters.c.id).where(
                tables.chapters.c.series_id == series_id
            )
        ).scalars().all()
        if not chapter_ids:
            return
        self.execute_immediate(
            delete(tables.chapter_locks).where(
                tables.chapter_locks.c.chapter_id.in_(list(chapter_ids))
            )
        )

    def refresh_series(self, series_id: str) -> Mapping[str, Any] | None:
        return self.refresh_row(self._series_table, {"id": series_id})

    def refresh_team(self, team_id: str) -> TeamRecord | None:
        row = self.refresh_row(tables.teams, {"id": team_id})
        if row is None:
            return None
        return TeamRecord(
            id=row["id"],
            name=row["name"],
            owner_id=row["owner_id"],
            created_at=row["created_at"],
        )


def create_series_uow_factory(
    session_factory: Callable[[], Session],
    *,
    utc_clock: Callable[[], datetime] = now_utc_naive,
    epoch_clock: Callable[[], float] = epoch_seconds,
    id_source: Callable[[], str] = new_id,
    invite_code_source: Callable[[], str] = new_invite_code,
) -> SeriesUnitOfWorkFactory:
    """Create one family UoW per call using only the supplied Session factory."""

    def create() -> SeriesSqlAlchemyUnitOfWork:
        return SeriesSqlAlchemyUnitOfWork(
            session_factory(),
            utc_clock=utc_clock,
            epoch_clock=epoch_clock,
            id_source=id_source,
            invite_code_source=invite_code_source,
        )

    return create
