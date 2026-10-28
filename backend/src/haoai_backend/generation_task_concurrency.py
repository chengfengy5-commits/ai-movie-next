"""Shared user task-capacity policy for registered generation operations."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.task_policy import (
    IN_FLIGHT_STATES,
    OPERATION_TASK_TYPES,
    SOURCE_WORKER_TYPES,
    TaskShape,
    graph_tasks_match_shape,
    task_shape_for_operation,
    task_type_for_operation,
)
from haoai_backend.generation_core.base_schema import AITaskRow

def count_user_in_flight_tasks(session: Session, user_id: str) -> int:
    """Count task rows across the shared five-type/four-state policy."""

    return int(
        session.execute(
            select(func.count())
            .select_from(AITaskRow)
            .where(
                AITaskRow.user_id == user_id,
                AITaskRow.type.in_(SOURCE_WORKER_TYPES),
                AITaskRow.status.in_(IN_FLIGHT_STATES),
                AITaskRow.billing_status != "refunded",
            )
        ).scalar_one()
    )


def session_uses_fresh_postgres_statements(session: Session) -> bool:
    """Fail closed unless PostgreSQL admissions see each statement's latest commit."""

    try:
        if session.get_bind().dialect.name != "postgresql":
            return True
        isolation_level = session.connection().get_isolation_level()
    except Exception:
        return False
    return (
        isinstance(isolation_level, str)
        and isolation_level.replace("_", " ").strip().casefold() == "read committed"
    )


__all__ = (
    "IN_FLIGHT_STATES",
    "OPERATION_TASK_TYPES",
    "SOURCE_WORKER_TYPES",
    "TaskShape",
    "count_user_in_flight_tasks",
    "graph_tasks_match_shape",
    "session_uses_fresh_postgres_statements",
    "task_shape_for_operation",
    "task_type_for_operation",
)
