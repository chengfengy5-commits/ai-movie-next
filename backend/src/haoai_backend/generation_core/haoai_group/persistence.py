"""Shared, owner-scoped SQL graph locking for HaoAI Group consumers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
    TaskSubmissionRow,
    UserRow,
)
from haoai_backend.generation_core.schema import ExecutionAttemptGroupRow
from haoai_backend.generation_credentials.haoai_group_execution_snapshot import (
    PersistedHaoaiGroupExecutionBinding,
    persisted_haoai_group_graph_matches,
)
from haoai_backend.generation_runtime.registry import HAOAI_ATTEMPT_GROUP


@dataclass(frozen=True, slots=True)
class LockedHaoaiGroupGraph:
    owner_id: str
    task_id: str
    task: dict[str, object]
    parent_submission: dict[str, object]
    unit: dict[str, object]
    steps: tuple[dict[str, object], ...]
    external_submissions: tuple[dict[str, object], ...]
    group: dict[str, object]
    request_data: dict[str, object]
    binding: PersistedHaoaiGroupExecutionBinding


def lock_haoai_group_graph(
    session: Session,
    owner_id: str,
    task_id: str,
) -> LockedHaoaiGroupGraph | None:
    """Lock and validate one Group using the common User→Task→graph order."""

    if not isinstance(session, Session) or not _nonempty(owner_id) or not _nonempty(task_id):
        return None
    owner = session.execute(
        select(UserRow.__table__.c.id)
        .where(UserRow.__table__.c.id == owner_id)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()
    if owner is None:
        return None

    task_row = session.execute(
        select(AITaskRow.__table__)
        .where(
            AITaskRow.__table__.c.id == task_id,
            AITaskRow.__table__.c.user_id == owner_id,
            AITaskRow.__table__.c.type == "video-single",
        )
        .with_for_update(skip_locked=True)
    ).mappings().one_or_none()
    if task_row is None:
        return None
    task = dict(task_row)
    request_data = _decode_json_object(task.get("request_data"))
    if request_data is None:
        return None

    unit_rows = session.execute(
        select(BillingUnitRow.__table__)
        .where(BillingUnitRow.__table__.c.task_id == task_id)
        .order_by(BillingUnitRow.__table__.c.id)
        .with_for_update()
    ).mappings().all()
    unit_ids = tuple(row["id"] for row in unit_rows)
    step_rows = (
        session.execute(
            select(ExecutionStepRow.__table__)
            .where(ExecutionStepRow.__table__.c.billing_unit_id.in_(unit_ids))
            .order_by(
                ExecutionStepRow.__table__.c.billing_unit_id,
                ExecutionStepRow.__table__.c.step_order,
                ExecutionStepRow.__table__.c.id,
            )
            .with_for_update()
        ).mappings().all()
        if unit_ids
        else []
    )
    if len(unit_rows) != 1:
        return None
    unit = dict(unit_rows[0])
    steps = tuple(dict(row) for row in step_rows)
    submission_id = request_data.get("submission_id")
    if not _nonempty(submission_id):
        return None
    parent_row = session.execute(
        select(TaskSubmissionRow.__table__)
        .where(TaskSubmissionRow.__table__.c.id == submission_id)
        .with_for_update(skip_locked=True)
    ).mappings().one_or_none()
    if parent_row is None:
        return None
    parent = dict(parent_row)

    step_ids = tuple(step.get("id") for step in steps)
    external_rows = session.execute(
        select(ExternalSubmissionRow.__table__)
        .where(ExternalSubmissionRow.__table__.c.step_id.in_(step_ids))
        .order_by(ExternalSubmissionRow.__table__.c.step_id, ExternalSubmissionRow.__table__.c.id)
        .with_for_update()
    ).mappings().all()

    group_row = session.execute(
        select(ExecutionAttemptGroupRow.__table__)
        .where(
            ExecutionAttemptGroupRow.__table__.c.unit_id == unit["id"],
            ExecutionAttemptGroupRow.__table__.c.group_key
            == HAOAI_ATTEMPT_GROUP.group_key,
        )
        .with_for_update(skip_locked=True)
    ).mappings().one_or_none()
    if group_row is None:
        return None
    group = dict(group_row)
    binding = _binding_from_graph(task, parent, unit, steps, group, request_data)
    if binding is None or not persisted_haoai_group_graph_matches(
        binding, task, parent, unit, steps, group, request_data
    ):
        return None
    return LockedHaoaiGroupGraph(
        owner_id=owner_id,
        task_id=task_id,
        task=task,
        parent_submission=parent,
        unit=unit,
        steps=steps,
        external_submissions=tuple(dict(row) for row in external_rows),
        group=group,
        request_data=request_data,
        binding=binding,
    )


def _binding_from_graph(
    task: dict[str, object],
    parent: dict[str, object],
    unit: dict[str, object],
    steps: tuple[dict[str, object], ...],
    group: dict[str, object],
    request_data: dict[str, object],
) -> PersistedHaoaiGroupExecutionBinding | None:
    attempt_index = group.get("active_attempt")
    step_ids = group.get("attempt_step_ids")
    if (
        type(attempt_index) is not int
        or not 0 <= attempt_index <= HAOAI_ATTEMPT_GROUP.max_retries
        or not isinstance(step_ids, list)
        or len(step_ids) != 3
        or len(steps) != 3
    ):
        return None
    step = steps[attempt_index]
    snapshot = step.get("input_snapshot")
    if not isinstance(snapshot, dict):
        return None
    values = (
        task.get("user_id"),
        task.get("id"),
        unit.get("id"),
        group.get("group_key"),
        step.get("id"),
        parent.get("id"),
        parent.get("idempotency_key"),
        parent.get("operation"),
        unit.get("step_graph_version"),
        task.get("type"),
        request_data.get("model_id"),
        request_data.get("model_source"),
        task.get("model_name"),
        unit.get("input_digest"),
        request_data.get("rule_digest"),
        request_data.get("snapshot_digest"),
        snapshot.get("server_attempt_id"),
        snapshot.get("prepared_payload_id"),
        snapshot.get("protected_ref"),
        snapshot.get("ordered_manifest_digest"),
        unit.get("quoted_amount"),
    )
    if not all(isinstance(value, str) and value for value in values[:-1]) or type(values[-1]) is not int:
        return None
    model_owner_id = request_data.get("model_owner_id")
    if model_owner_id is not None and not isinstance(model_owner_id, str):
        return None
    task_message_id = task.get("message_id") or None
    if task_message_id is not None and not isinstance(task_message_id, str):
        return None
    try:
        return PersistedHaoaiGroupExecutionBinding(
            owner_id=values[0],
            task_id=values[1],
            unit_id=values[2],
            group_key=values[3],
            step_id=values[4],
            attempt_index=attempt_index,
            submission_id=values[5],
            raw_key=values[6],
            operation=values[7],
            graph_version=values[8],
            task_type=values[9],
            task_model_id=values[10],
            task_model_source=values[11],
            task_model_owner_id=model_owner_id,
            task_model_name=values[12],
            request_digest=values[13],
            rule_digest=values[14],
            snapshot_digest=values[15],
            server_attempt_id=values[16],
            prepared_payload_id=values[17],
            protected_ref=values[18],
            ordered_manifest_digest=values[19],
            quoted_amount=values[20],
            task_message_id=task_message_id,
        )
    except (IndexError, TypeError, ValueError):
        return None


def _decode_json_object(value: object) -> dict[str, object] | None:
    if not isinstance(value, str):
        return None
    try:
        result = json.loads(
            value,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (TypeError, ValueError, UnicodeError):
        return None
    return result if isinstance(result, dict) else None


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate task request data key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite task request number: {value}")


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value)


__all__ = ("LockedHaoaiGroupGraph", "lock_haoai_group_graph")
