"""SQL predicates for quarantining persisted HaoAI Group task markers."""

from __future__ import annotations

from sqlalchemy import exists, or_, select
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.schema import Table

from haoai_backend.generation_core.base_schema import AITaskRow, BillingUnitRow
from haoai_backend.generation_core.schema import (
    ExecutionAttemptGroupRow,
    PolicyStopEventRow,
)
from haoai_backend.generation_runtime.registry import HAOAI_ATTEMPT_GROUP


def has_haoai_group_candidate_marker(
    task_table: Table | None = None,
) -> ColumnElement[bool]:
    """Match any durable Group association or the immutable admission hint.

    This deliberately treats malformed and partial Group graphs as Group-owned
    candidates. Ordinary video claim and due writers must not reinterpret them
    as single-step tasks when a Group marker remains in the database.
    """

    task = AITaskRow.__table__ if task_table is None else task_table
    unit = BillingUnitRow.__table__
    group = ExecutionAttemptGroupRow.__table__
    event = PolicyStopEventRow.__table__

    unit_marker = exists(
        select(unit.c.id).where(
            unit.c.task_id == task.c.id,
            unit.c.step_graph_version == HAOAI_ATTEMPT_GROUP.registry_version,
        )
    )
    group_row = exists(
        select(group.c.unit_id)
        .select_from(group.join(unit, group.c.unit_id == unit.c.id))
        .where(unit.c.task_id == task.c.id)
    )
    event_row = exists(
        select(event.c.event_id)
        .select_from(event.join(unit, event.c.unit_id == unit.c.id))
        .where(unit.c.task_id == task.c.id)
    )
    version = HAOAI_ATTEMPT_GROUP.registry_version
    request_hint = or_(
        task.c.request_data.like(f'%"step_graph_version":"{version}"%'),
        task.c.request_data.like(f'%"graph_version":"{version}"%'),
    )
    return or_(unit_marker, group_row, event_row, request_hint)


__all__ = ("has_haoai_group_candidate_marker",)
