"""Lock and authenticate one immutable registered frame.optimize batch."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping

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
from haoai_backend.generation_credentials.domain import CredentialCrypto, HmacProofAuthority
from haoai_backend.generation_credentials.frame_optimize_snapshot_reader import (
    PersistedFrameOptimizeExecutionBinding,
    VerifiedFrameOptimizeExecutionBatch,
    VerifiedFrameOptimizeExecutionItem,
    read_original_frame_optimize_execution_batch,
)
from haoai_backend.generation_credentials.persistence import prepared_payloads

_OPERATION = "frame.optimize"
_GRAPH_VERSION = "frame-optimize-v1"


@dataclass(frozen=True, slots=True)
class LockedFrameOptimizeBatch:
    """A complete locked graph plus the reader-authenticated immutable input."""

    task: Mapping[str, object]
    submission: Mapping[str, object]
    units: tuple[Mapping[str, object], ...]
    steps: tuple[Mapping[str, object], ...]
    external_submissions: tuple[Mapping[str, object], ...]
    binding: PersistedFrameOptimizeExecutionBinding
    verified: VerifiedFrameOptimizeExecutionBatch

    @property
    def units_by_id(self) -> dict[str, Mapping[str, object]]:
        return {str(unit["id"]): unit for unit in self.units}

    @property
    def steps_by_unit(self) -> dict[str, Mapping[str, object]]:
        return {str(step["billing_unit_id"]): step for step in self.steps}

    @property
    def external_by_step(self) -> dict[str, Mapping[str, object]]:
        return {str(row["step_id"]): row for row in self.external_submissions}

    @property
    def items_by_unit(self) -> dict[str, VerifiedFrameOptimizeExecutionItem]:
        return {item.unit_id: item for item in self.verified.items}


def lock_frame_optimize_batch(
    session: Session,
    owner_id: str,
    task_id: str,
    *,
    crypto: CredentialCrypto,
    proof_authority: HmacProofAuthority,
) -> LockedFrameOptimizeBatch | None:
    """Lock in User→Task→Units→Steps→Submissions order and verify the whole batch."""

    if (
        not isinstance(session, Session)
        or not isinstance(owner_id, str)
        or not owner_id
        or not isinstance(task_id, str)
        or not task_id
    ):
        return None
    user_id = session.execute(
        select(UserRow.id).where(UserRow.id == owner_id).with_for_update(skip_locked=True)
    ).scalar_one_or_none()
    if user_id is None:
        return None
    task_row = session.execute(
        select(AITaskRow.__table__)
        .where(
            AITaskRow.id == task_id,
            AITaskRow.user_id == owner_id,
            AITaskRow.type == "batch-optimize",
        )
        .with_for_update(skip_locked=True)
    ).mappings().one_or_none()
    if task_row is None:
        return None
    task = dict(task_row)
    try:
        request_data = json.loads(task["request_data"])
        submission_id = request_data["submission_id"]
        items = request_data["items"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if (
        not isinstance(request_data, dict)
        or request_data.get("operation") != _OPERATION
        or request_data.get("graph_version") != _GRAPH_VERSION
        or not isinstance(submission_id, str)
        or not submission_id
        or not isinstance(items, list)
        or not items
    ):
        return None

    unit_rows = session.execute(
        select(BillingUnitRow.__table__)
        .where(BillingUnitRow.task_id == task_id)
        .order_by(BillingUnitRow.id)
        .with_for_update()
    ).mappings().all()
    units = tuple(dict(row) for row in unit_rows)
    expected_unit_ids = {item.get("unit_id") for item in items if isinstance(item, dict)}
    if len(units) != len(items) or {unit["id"] for unit in units} != expected_unit_ids:
        return None

    step_rows = session.execute(
        select(ExecutionStepRow.__table__)
        .where(ExecutionStepRow.billing_unit_id.in_(tuple(expected_unit_ids)))
        .order_by(
            ExecutionStepRow.billing_unit_id,
            ExecutionStepRow.step_order,
            ExecutionStepRow.id,
        )
        .with_for_update()
    ).mappings().all()
    steps = tuple(dict(row) for row in step_rows)
    expected_step_ids = {item.get("step_id") for item in items if isinstance(item, dict)}
    if len(steps) != len(items) or {step["id"] for step in steps} != expected_step_ids:
        return None

    parent_row = session.execute(
        select(TaskSubmissionRow.__table__)
        .where(TaskSubmissionRow.id == submission_id)
        .with_for_update()
    ).mappings().one_or_none()
    if parent_row is None:
        return None
    parent = dict(parent_row)

    external_rows = session.execute(
        select(ExternalSubmissionRow.__table__)
        .where(ExternalSubmissionRow.step_id.in_(tuple(expected_step_ids)))
        .order_by(ExternalSubmissionRow.id)
        .with_for_update()
    ).mappings().all()
    external_submissions = tuple(dict(row) for row in external_rows)
    if len({row["step_id"] for row in external_submissions}) != len(external_submissions):
        return None

    payload_ids = tuple(sorted(str(item.get("prepared_payload_id", "")) for item in items))
    payload_rows = session.execute(
        select(prepared_payloads.c.prepared_payload_id)
        .where(
            prepared_payloads.c.actor_id == owner_id,
            prepared_payloads.c.operation == _OPERATION,
            prepared_payloads.c.prepared_payload_id.in_(payload_ids),
        )
        .order_by(prepared_payloads.c.prepared_payload_id)
        .with_for_update()
    ).scalars().all()
    if len(payload_rows) != len(items) or set(payload_rows) != set(payload_ids):
        return None

    binding = _execution_binding(task, parent, request_data)
    if binding is None:
        return None
    verified = read_original_frame_optimize_execution_batch(
        session,
        binding,
        crypto=crypto,
        proof_authority=proof_authority,
    )
    if (
        verified is None
        or verified.task_id != task_id
        or verified.submission_id != submission_id
        or len(verified.items) != len(items)
    ):
        return None
    return LockedFrameOptimizeBatch(
        task=task,
        submission=parent,
        units=units,
        steps=steps,
        external_submissions=external_submissions,
        binding=binding,
        verified=verified,
    )


def initial_generation_candidate(graph: LockedFrameOptimizeBatch) -> bool:
    """Whether this whole immutable graph is still eligible for first execution."""

    task = graph.task
    return bool(
        task.get("status") in {"queued", "processing", "reconciling"}
        and task.get("billing_status") == "unbilled"
        and task.get("user_cancelled_at") is None
        and task.get("result") is None
        and task.get("credit_cost") == 0
        and all(
            unit.get("billing_status") == "unbilled"
            and unit.get("result_status") == "pending"
            and unit.get("debited_amount") == 0
            and unit.get("refunded_amount") == 0
            and unit.get("first_intent_at") is None
            and unit.get("deadline_at") is None
            and unit.get("user_cancelled_at") is None
            for unit in graph.units
        )
        and all(step.get("execution_status") == "prepared" for step in graph.steps)
        and not graph.external_submissions
    )


def has_any_intent(graph: LockedFrameOptimizeBatch) -> bool:
    return bool(graph.external_submissions) or any(
        unit.get("first_intent_at") is not None for unit in graph.units
    )


def valid_intent_projection(graph: LockedFrameOptimizeBatch) -> bool:
    """Validate each mutable intent projection against its immutable batch item."""

    units = graph.units_by_id
    steps = graph.steps_by_unit
    external = graph.external_by_step
    if len(units) != len(graph.verified.items) or len(steps) != len(units):
        return False
    for unit_id, unit in units.items():
        step = steps.get(unit_id)
        if step is None:
            return False
        submission = external.get(str(step["id"]))
        first_intent = unit.get("first_intent_at")
        deadline = unit.get("deadline_at")
        if submission is None:
            if first_intent is not None or deadline is not None or step.get("execution_status") != "prepared":
                return False
            continue
        if first_intent is None or deadline is None or not intent_row_matches(
            submission, graph, unit, step
        ):
            return False
    return len(external) == len(graph.external_submissions)


def item_for_unit(
    graph: LockedFrameOptimizeBatch,
    unit_id: str,
) -> VerifiedFrameOptimizeExecutionItem | None:
    for item in graph.verified.items:
        if item.unit_id == unit_id:
            return item
    return None


def intent_snapshot(
    graph: LockedFrameOptimizeBatch,
    unit: Mapping[str, object],
    step: Mapping[str, object],
    *,
    snapshot_ref: str,
) -> dict[str, object] | None:
    item = item_for_unit(graph, str(unit.get("id", "")))
    if item is None:
        return None
    binding = item.credential_binding
    provider = item.snapshot.model_ref.provider
    model_id = item.snapshot.model_ref.model_id
    if not isinstance(provider, str) or not provider or not isinstance(model_id, str) or not model_id:
        return None
    request_data = graph.task.get("request_data")
    try:
        request_digest = graph.binding.request_digest
        request_value = json.loads(request_data) if isinstance(request_data, str) else None
        item_data = next(
            entry for entry in request_value["items"] if entry["unit_id"] == item.unit_id
        )
    except (KeyError, StopIteration, TypeError, ValueError, json.JSONDecodeError):
        return None
    return {
        "owner_id": graph.binding.owner_id,
        "task_id": graph.binding.task_id,
        "submission_id": graph.binding.submission_id,
        "unit_id": item.unit_id,
        "step_id": str(step.get("id", "")),
        "operation": _OPERATION,
        "graph_version": _GRAPH_VERSION,
        "attempt_id": item.item_id,
        "item_order": item.order,
        "frame_index": item.frame_index,
        "prepared_payload_id": item.prepared_payload_id,
        "protected_ref": item_data.get("protected_ref"),
        "request_digest": request_digest,
        "rule_digest": item_data.get("rule_digest"),
        "snapshot_digest": item_data.get("snapshot_digest"),
        "manifest_snapshot_digest": graph.binding.manifest_snapshot_digest,
        "snapshot_ref": snapshot_ref,
        "credential_binding_ref": item.snapshot.credential_binding_ref,
        "binding_version": binding.binding_version,
        "principal_id": binding.original_principal_id,
        "provider": provider,
        "model_id": model_id,
        "quoted_amount": unit.get("quoted_amount"),
    }


def intent_row_matches(
    row: Mapping[str, object],
    graph: LockedFrameOptimizeBatch,
    unit: Mapping[str, object],
    step: Mapping[str, object],
) -> bool:
    """Check immutable intent fields while allowing later mutable submit projection."""

    expected_key = _submission_key(str(unit.get("id", "")), str(step.get("id", "")))
    snapshot = row.get("request_snapshot")
    if not isinstance(snapshot, dict):
        return False
    expected_ref = _snapshot_ref(graph, unit)
    expected = intent_snapshot(graph, unit, step, snapshot_ref=expected_ref)
    return bool(
        expected is not None
        and row.get("step_id") == step.get("id")
        and row.get("submission_key") == expected_key
        and row.get("request_digest") == graph.binding.request_digest
        and row.get("provider") == expected.get("provider")
        and row.get("model_ref") == expected.get("model_id")
        and snapshot == expected
        and row.get("submission_status") in {"submitting", "completed", "unknown", "failed"}
        and row.get("intent_at") is not None
        and row.get("deadline_at") is not None
        and unit.get("first_intent_at") is not None
        and unit.get("deadline_at") is not None
        and _same_time(row.get("intent_at"), unit.get("first_intent_at"))
        and _same_time(row.get("deadline_at"), unit.get("deadline_at"))
        and _deadline_is_fixed(unit.get("first_intent_at"), unit.get("deadline_at"))
        and step.get("execution_status") in {
            "submitting", "submitted", "result_observed", "applied", "reconciling",
            "failed", "deterministic_empty",
        }
    )


def _execution_binding(task, parent, request_data):
    try:
        binding = PersistedFrameOptimizeExecutionBinding(
            owner_id=str(task["user_id"]),
            task_id=str(task["id"]),
            submission_id=str(parent["id"]),
            task_message_id=str(task["message_id"]),
            task_model_name=str(task["model_name"]),
            request_digest=str(request_data["request_digest"]),
            graph_digest=str(request_data["graph_digest"]),
            manifest_snapshot_digest=str(request_data["manifest_snapshot_digest"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
    if (
        binding.submission_id != request_data.get("submission_id")
        or request_data.get("task_id") != binding.task_id
        or request_data.get("frame_count") != len(request_data.get("items", []))
        or parent.get("id") != binding.submission_id
        or parent.get("user_id") != binding.owner_id
        or parent.get("operation") != _OPERATION
        or parent.get("task_ids") != [binding.task_id]
    ):
        return None
    return binding


def _snapshot_ref(graph: LockedFrameOptimizeBatch, unit: Mapping[str, object]) -> str:
    item = item_for_unit(graph, str(unit.get("id", "")))
    if item is None:
        return ""
    return (
        f"frame-optimize-v1:{item.prepared_payload_id}:"
        f"{graph.binding.manifest_snapshot_digest}:{item.order}"
    )


def _submission_key(unit_id: str, step_id: str) -> str:
    value = f"frame-optimize-generate-v1:{unit_id}:{step_id}"
    if len(value) > 255:
        raise ValueError("optimize intent key exceeds the stable submission limit")
    return value


def _same_time(left: object, right: object) -> bool:
    return _utc(left) == _utc(right)


def _utc(value: object):
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _deadline_is_fixed(first_intent_at: object, deadline_at: object) -> bool:
    first_intent = _utc(first_intent_at)
    deadline = _utc(deadline_at)
    return bool(
        first_intent is not None
        and deadline is not None
        and deadline == first_intent + timedelta(seconds=7200)
    )


__all__ = [
    "LockedFrameOptimizeBatch",
    "has_any_intent",
    "initial_generation_candidate",
    "intent_row_matches",
    "intent_snapshot",
    "item_for_unit",
    "lock_frame_optimize_batch",
    "valid_intent_projection",
]
