"""Read task-observation data from the request-owned SQLAlchemy Session."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from .domain import TeamMembershipRecord
from .ports import TaskObservationUnitOfWork, UnitOfWorkFactory
from .tables import (
    ai_tasks,
    asset_tables,
    billing_units,
    chapters,
    chat_messages,
    task_submissions,
    team_members,
    users,
)


def _row_mapping(row: RowMapping | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _row_mappings(rows: Sequence[RowMapping]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


class SqlAlchemyTaskObservationUnitOfWork(TaskObservationUnitOfWork):
    """A supplied Session adapter that exposes reads and cleanup only."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._memberships_by_id: dict[str, TeamMembershipRecord] = {}

    def load_owned_task(self, actor_id: str, task_id: str) -> dict[str, Any] | None:
        result = self._session.execute(
            select(ai_tasks).where(
                ai_tasks.c.id == task_id,
                ai_tasks.c.user_id == actor_id,
            )
        )
        return _row_mapping(result.mappings().one_or_none())

    def load_task(self, task_id: str) -> dict[str, Any] | None:
        result = self._session.execute(select(ai_tasks).where(ai_tasks.c.id == task_id))
        return _row_mapping(result.mappings().one_or_none())

    def find_owned_by_message(
        self,
        actor_id: str,
        message_id: str,
        limit: int = 2,
    ) -> list[dict[str, Any]]:
        result = self._session.execute(
            select(ai_tasks)
            .where(
                ai_tasks.c.user_id == actor_id,
                ai_tasks.c.message_id == message_id,
            )
            .limit(limit)
        )
        return _row_mappings(result.mappings().all())

    def count_owned_tasks(self, actor_id: str) -> int:
        result = self._session.execute(
            select(func.count()).select_from(ai_tasks).where(ai_tasks.c.user_id == actor_id)
        )
        return result.scalar_one()

    def list_owned_tasks(
        self,
        actor_id: str,
        offset: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        result = self._session.execute(
            select(ai_tasks)
            .where(ai_tasks.c.user_id == actor_id)
            .order_by(ai_tasks.c.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return _row_mappings(result.mappings().all())

    def load_messages(self, message_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        if not message_ids:
            return {}
        result = self._session.execute(
            select(chat_messages).where(chat_messages.c.id.in_(message_ids))
        )
        return {row["id"]: row for row in _row_mappings(result.mappings().all())}

    def load_chapter_titles(self, chapter_ids: set[str]) -> dict[str, str]:
        if not chapter_ids:
            return {}
        result = self._session.execute(
            select(chapters.c.id, chapters.c.title).where(chapters.c.id.in_(chapter_ids))
        )
        return {row["id"]: row["title"] for row in result.mappings().all()}

    def load_asset_names(
        self,
        asset_ids_by_kind: Mapping[str, Sequence[str]],
    ) -> dict[str, dict[str, str | None]]:
        names: dict[str, dict[str, str | None]] = {
            kind: {} for kind in asset_tables
        }
        for kind, (table, name_column) in asset_tables.items():
            asset_ids = asset_ids_by_kind.get(kind, ())
            if not asset_ids:
                continue
            result = self._session.execute(
                select(table.c.id, table.c[name_column]).where(table.c.id.in_(asset_ids))
            )
            names[kind] = {
                row["id"]: row[name_column] for row in result.mappings().all()
            }
        return names

    def list_ai_review_count_candidates(
        self,
        actor_id: str,
        message_id: str,
    ) -> list[dict[str, Any]]:
        result = self._session.execute(
            select(ai_tasks).where(
                ai_tasks.c.type == "ai-review",
                ai_tasks.c.user_id == actor_id,
                ai_tasks.c.status.in_(["queued", "processing", "completed"]),
                ai_tasks.c.request_data.like(
                    f'%"source_message_id": "{message_id}"%'
                ),
            )
        )
        return _row_mappings(result.mappings().all())

    def list_running_batch_optimize_candidates(
        self,
        actor_id: str,
    ) -> list[dict[str, Any]]:
        result = self._session.execute(
            select(ai_tasks)
            .where(
                ai_tasks.c.type == "batch-optimize",
                ai_tasks.c.user_id == actor_id,
                ai_tasks.c.status.in_(["queued", "processing", "cancelling"]),
            )
            .order_by(ai_tasks.c.created_at.desc())
        )
        return _row_mappings(result.mappings().all())

    def first_billing_unit(self, task_id: str) -> dict[str, Any] | None:
        result = self._session.execute(
            select(billing_units)
            .where(billing_units.c.task_id == task_id)
            .order_by(billing_units.c.created_at.asc())
        )
        return _row_mapping(result.mappings().first())

    def find_submission(
        self,
        actor_id: str,
        operation: str,
        raw_key: str,
    ) -> dict[str, Any] | None:
        result = self._session.execute(
            select(task_submissions).where(
                task_submissions.c.user_id == actor_id,
                task_submissions.c.operation == operation,
                task_submissions.c.idempotency_key == raw_key,
            )
        )
        return _row_mapping(result.mappings().one_or_none())

    def unique_billing_unit(self, task_id: str) -> dict[str, Any] | None:
        result = self._session.execute(
            select(billing_units).where(billing_units.c.task_id == task_id)
        )
        return _row_mapping(result.mappings().one_or_none())

    def load_sql_superuser(self, actor_id: str) -> bool:
        result = self._session.execute(
            select(users.c.is_superuser).where(users.c.id == actor_id)
        )
        return bool(result.scalar_one_or_none())

    def load_membership(
        self,
        team_id: str,
        user_id: str,
    ) -> TeamMembershipRecord | None:
        result = self._session.execute(
            select(team_members).where(
                team_members.c.team_id == team_id,
                team_members.c.user_id == user_id,
            )
        )
        row = result.mappings().one_or_none()
        if row is None:
            return None

        membership_id = row["id"]
        membership = self._memberships_by_id.get(membership_id)
        if membership is None:
            membership = TeamMembershipRecord(
                team_id=row["team_id"],
                user_id=row["user_id"],
                role=row["role"],
                permissions=row["permissions"],
            )
            self._memberships_by_id[membership_id] = membership
        return membership

    def rollback(self) -> None:
        self._session.rollback()

    def close(self) -> None:
        self._session.close()


def task_observation_unit_of_work_factory(
    session_factory: Callable[[], Session],
) -> UnitOfWorkFactory:
    """Bind the module to the application's existing request-session factory."""

    def create_unit_of_work() -> TaskObservationUnitOfWork:
        return SqlAlchemyTaskObservationUnitOfWork(session_factory())

    return create_unit_of_work
