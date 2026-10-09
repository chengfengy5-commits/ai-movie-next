"""Batched SQLAlchemy Core reads for team reporting."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.sql.schema import Table

from haoai_backend.teams import tables
from haoai_backend.teams.domain import TeamMembershipRecord, TeamRecord
from haoai_backend.teams.ports import TeamsUnitOfWork, TeamsUnitOfWorkFactory

from .ports import ReportingUnitOfWork, ReportingUnitOfWorkFactory


class SqlAlchemyReportingUnitOfWork(ReportingUnitOfWork):
    """Narrow reporting reader over one supplied shared request UoW."""

    def __init__(self, uow: TeamsUnitOfWork) -> None:
        self._uow = uow

    def ensure_clean(self) -> None:
        self._uow.ensure_clean()

    def rollback(self) -> None:
        self._uow.rollback()

    def close(self) -> None:
        self._uow.close()

    def load_team(self, team_id: str) -> TeamRecord | None:
        return self._uow.load_team(team_id)

    def load_membership(
        self,
        team_id: str,
        user_id: str,
    ) -> TeamMembershipRecord | None:
        return self._uow.load_membership(team_id, user_id)

    def list_member_tasks(
        self,
        user_id: str,
        page: int,
        page_size: int,
    ) -> tuple[list[Mapping[str, Any]], int]:
        tasks = tables.ai_tasks
        total = self._uow.execute_immediate(
            select(func.count()).select_from(tasks).where(tasks.c.user_id == user_id)
        ).scalar_one()
        rows = self._uow.execute_immediate(
            select(tasks)
            .where(tasks.c.user_id == user_id)
            .order_by(tasks.c.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).mappings().all()
        return [dict(row) for row in rows], total

    def load_messages(
        self,
        message_ids: Sequence[str],
    ) -> Mapping[str, Mapping[str, Any]]:
        if not message_ids:
            return {}
        rows = self._uow.execute_immediate(
            select(tables.chat_messages).where(tables.chat_messages.c.id.in_(message_ids))
        ).mappings().all()
        return {row["id"]: dict(row) for row in rows}

    def load_chapter_titles(self, chapter_ids: Sequence[str]) -> Mapping[str, str]:
        return self._load_names(
            tables.chapters,
            "title",
            chapter_ids,
            empty_value="",
        )

    def load_asset_names(
        self,
        kind: str,
        asset_ids: Sequence[str],
    ) -> Mapping[str, str | None]:
        asset_tables: dict[str, tuple[Table, str]] = {
            "character": (tables.characters, "name"),
            "scene": (tables.scenes, "title"),
            "prop": (tables.props, "name"),
            "storyboard": (tables.storyboard_assets, "name"),
        }
        try:
            table, name_column = asset_tables[kind]
        except KeyError as exc:
            raise ValueError(f"unsupported reporting asset kind: {kind}") from exc
        rows = self._load_named_rows(table, name_column, asset_ids)
        if kind == "character":
            return {row["id"]: row[name_column] for row in rows}
        return {row["id"]: row[name_column] or None for row in rows}

    def load_user_credit(self, user_id: str) -> int:
        row = self._uow.execute_immediate(
            select(tables.user_credits.c.credits).where(
                tables.user_credits.c.user_id == user_id
            )
        ).first()
        return row[0] if row is not None else 0

    def list_team_series(
        self,
        team_id: str,
        *,
        newest_first: bool,
    ) -> list[Mapping[str, Any]]:
        statement = select(tables.series).where(tables.series.c.team_id == team_id)
        if newest_first:
            statement = statement.order_by(tables.series.c.updated_at.desc())
        rows = self._uow.execute_immediate(statement).mappings().all()
        return [dict(row) for row in rows]

    def load_team_series(
        self,
        team_id: str,
        series_id: str,
    ) -> Mapping[str, Any] | None:
        row = self._uow.execute_immediate(
            select(tables.series).where(
                tables.series.c.id == series_id,
                tables.series.c.team_id == team_id,
            )
        ).mappings().first()
        return dict(row) if row is not None else None

    def list_series_chapters(
        self,
        series_id: str,
    ) -> list[Mapping[str, Any]]:
        rows = self._uow.execute_immediate(
            select(tables.chapters)
            .where(tables.chapters.c.series_id == series_id)
            .order_by(tables.chapters.c.order.desc())
        ).mappings().all()
        return [dict(row) for row in rows]

    def load_team_usage_rows(
        self,
        series_ids: Sequence[str],
        start_time: datetime | None,
        end_time: datetime | None,
    ) -> list[Sequence[Any]]:
        tasks, messages, chapters = tables.ai_tasks, tables.chat_messages, tables.chapters
        statement = (
            select(
                chapters.c.series_id,
                tasks.c.model_name,
                tasks.c.user_id,
                tasks.c.status,
                func.count(tasks.c.id).label("calls"),
                func.coalesce(func.sum(tasks.c.credit_cost), 0).label("credits"),
            )
            .select_from(
                tasks.join(messages, tasks.c.message_id == messages.c.id).join(
                    chapters, messages.c.chapter_id == chapters.c.id
                )
            )
            .where(
                chapters.c.series_id.in_(series_ids),
                tasks.c.status.in_(["completed", "failed"]),
            )
            .group_by(
                chapters.c.series_id,
                tasks.c.model_name,
                tasks.c.user_id,
                tasks.c.status,
            )
        )
        statement = self._apply_date_range(statement, start_time, end_time)
        return list(self._uow.execute_immediate(statement).all())

    def load_chapter_scope(
        self,
        series_ids: Sequence[str],
        start_time: datetime | None,
        end_time: datetime | None,
    ) -> list[Sequence[Any]]:
        tasks, messages, chapters = tables.ai_tasks, tables.chat_messages, tables.chapters
        statement = (
            select(tasks.c.user_id, chapters.c.series_id, messages.c.chapter_id)
            .select_from(
                tasks.join(messages, tasks.c.message_id == messages.c.id).join(
                    chapters, messages.c.chapter_id == chapters.c.id
                )
            )
            .where(
                chapters.c.series_id.in_(series_ids),
                tasks.c.status.in_(["completed", "failed"]),
            )
            .distinct()
        )
        statement = self._apply_date_range(statement, start_time, end_time)
        return list(self._uow.execute_immediate(statement).all())

    def load_series_usage_rows(
        self,
        series_id: str,
        start_time: datetime | None,
        end_time: datetime | None,
    ) -> list[Sequence[Any]]:
        tasks, messages, chapters = tables.ai_tasks, tables.chat_messages, tables.chapters
        statement = (
            select(
                messages.c.chapter_id,
                tasks.c.model_name,
                tasks.c.user_id,
                tasks.c.status,
                func.count(tasks.c.id).label("calls"),
                func.coalesce(func.sum(tasks.c.credit_cost), 0).label("credits"),
            )
            .select_from(
                tasks.join(messages, tasks.c.message_id == messages.c.id).join(
                    chapters, messages.c.chapter_id == chapters.c.id
                )
            )
            .where(
                chapters.c.series_id == series_id,
                tasks.c.status.in_(["completed", "failed"]),
            )
            .group_by(
                messages.c.chapter_id,
                tasks.c.model_name,
                tasks.c.user_id,
                tasks.c.status,
            )
        )
        statement = self._apply_date_range(statement, start_time, end_time)
        return list(self._uow.execute_immediate(statement).all())

    def load_model_usage_rows(
        self,
        series_ids: Sequence[str],
        model_name: str,
        start_time: datetime | None,
        end_time: datetime | None,
    ) -> list[Sequence[Any]]:
        tasks, messages, chapters = tables.ai_tasks, tables.chat_messages, tables.chapters
        statement = (
            select(
                chapters.c.series_id,
                tasks.c.user_id,
                tasks.c.status,
                func.count(tasks.c.id).label("calls"),
                func.coalesce(func.sum(tasks.c.credit_cost), 0).label("credits"),
            )
            .select_from(
                tasks.join(messages, tasks.c.message_id == messages.c.id).join(
                    chapters, messages.c.chapter_id == chapters.c.id
                )
            )
            .where(
                chapters.c.series_id.in_(series_ids),
                tasks.c.model_name == model_name,
                tasks.c.status.in_(["completed", "failed"]),
            )
            .group_by(chapters.c.series_id, tasks.c.user_id, tasks.c.status)
        )
        statement = self._apply_date_range(statement, start_time, end_time)
        return list(self._uow.execute_immediate(statement).all())

    def load_export_usage_rows(
        self,
        series_ids: Sequence[str],
        start_time: datetime | None,
        end_time: datetime | None,
    ) -> list[Sequence[Any]]:
        tasks, messages, chapters = tables.ai_tasks, tables.chat_messages, tables.chapters
        statement = (
            select(
                tasks.c.user_id,
                chapters.c.series_id,
                messages.c.chapter_id,
                tasks.c.model_name,
                tasks.c.status,
                func.count(tasks.c.id).label("calls"),
                func.coalesce(func.sum(tasks.c.credit_cost), 0).label("credits"),
            )
            .select_from(
                tasks.join(messages, tasks.c.message_id == messages.c.id).join(
                    chapters, messages.c.chapter_id == chapters.c.id
                )
            )
            .where(
                chapters.c.series_id.in_(series_ids),
                tasks.c.status.in_(["completed", "failed"]),
            )
            .group_by(
                tasks.c.user_id,
                chapters.c.series_id,
                messages.c.chapter_id,
                tasks.c.model_name,
                tasks.c.status,
            )
        )
        statement = self._apply_date_range(statement, start_time, end_time)
        return list(self._uow.execute_immediate(statement).all())

    def load_users(self, user_ids: Sequence[str]) -> Mapping[str, Any]:
        return self._uow.load_users(user_ids)

    def load_export_model_types(
        self,
        model_names: Sequence[str],
    ) -> list[Sequence[Any]]:
        if not model_names:
            return []
        rows = self._uow.execute_immediate(
            select(tables.ai_tasks.c.model_name, tables.ai_tasks.c.type)
            .where(tables.ai_tasks.c.model_name.in_(model_names))
            .distinct()
        ).all()
        return list(rows)

    def _load_names(
        self,
        table: Table,
        name_column: str,
        identifiers: Sequence[str],
        *,
        empty_value: str,
    ) -> Mapping[str, str]:
        rows = self._load_named_rows(table, name_column, identifiers)
        return {row["id"]: row[name_column] or empty_value for row in rows}

    def _load_named_rows(
        self,
        table: Table,
        name_column: str,
        identifiers: Sequence[str],
    ) -> list[Mapping[str, Any]]:
        if not identifiers:
            return []
        rows = self._uow.execute_immediate(
            select(table.c.id, table.c[name_column]).where(
                table.c.id.in_(identifiers)
            )
        ).mappings().all()
        return [dict(row) for row in rows]

    @staticmethod
    def _apply_date_range(
        statement: Any,
        start_time: datetime | None,
        end_time: datetime | None,
    ) -> Any:
        predicates = []
        if start_time is not None:
            predicates.append(tables.ai_tasks.c.created_at >= start_time)
        if end_time is not None:
            predicates.append(tables.ai_tasks.c.created_at <= end_time)
        return statement.where(and_(*predicates)) if predicates else statement


def adapt_reporting_uow_factory(
    uow_factory: TeamsUnitOfWorkFactory,
) -> ReportingUnitOfWorkFactory:
    """Adapt one supplied shared-session factory to the read-only family port."""

    def create_reporting_uow() -> ReportingUnitOfWork:
        return SqlAlchemyReportingUnitOfWork(uow_factory())

    return create_reporting_uow
