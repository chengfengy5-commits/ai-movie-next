"""Authenticated, immutable controls for expired frame.optimize intents."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from haoai_backend.generation_core.base_schema import ResultEvidenceRow
from haoai_backend.generation_core.frame_optimize_execution import (
    LockedFrameOptimizeBatch,
    intent_row_matches,
)

_DUE_EVIDENCE_KIND = "frame_optimize_due_v1"
_DUE_EVIDENCE_SOURCE = "recovery"
_DUE_EVIDENCE_PREFIX = "frame-optimize-due-v1:"
DUE_TERMINAL_ERROR = "frame_optimize_result_unknown_after_deadline"
_DUE_FIELDS = frozenset(
    {
        "owner_id",
        "task_id",
        "unit_id",
        "step_id",
        "submission_id",
        "submission_key",
        "request_digest",
        "first_intent_at",
        "deadline_at",
        "received_at",
    }
)
_DUE_FACT_FINGERPRINT_FIELDS = (
    "id",
    "step_id",
    "submission_id",
    "evidence_key",
    "source",
    "evidence_kind",
    "payload_digest",
    "result_location",
    "result_digest",
    "received_at",
    "validity",
    "application_status",
    "applied_at",
)


def due_evidence_key(submission_id: str) -> str:
    return f"{_DUE_EVIDENCE_PREFIX}{submission_id}"


def due_fact_fingerprint(row: Mapping[str, object]) -> tuple[object, ...] | None:
    """Capture every persisted field that identifies one immutable due fact."""

    if not all(field in row for field in _DUE_FACT_FINGERPRINT_FIELDS):
        return None
    values = []
    for field in _DUE_FACT_FINGERPRINT_FIELDS:
        value = row.get(field)
        if field in {"received_at", "applied_at"} and value is not None:
            normalized = _utc(value)
            if normalized is None:
                return None
            value = normalized
        values.append(value)
    return tuple(values)


def lock_due_facts(
    session: Session, graph: LockedFrameOptimizeBatch
) -> dict[str, Mapping[str, object]] | None:
    """Lock and validate the due control facts attached to this exact batch."""

    expected_keys = tuple(
        sorted(due_evidence_key(str(row.get("id", ""))) for row in graph.external_submissions)
    )
    submission_ids = tuple(sorted(str(row.get("id", "")) for row in graph.external_submissions))
    if not expected_keys:
        return {}
    table = ResultEvidenceRow.__table__
    rows = session.execute(
        select(table)
        .where(
            or_(
                and_(
                    table.c.submission_id.in_(submission_ids),
                    table.c.evidence_kind == _DUE_EVIDENCE_KIND,
                ),
                table.c.evidence_key.in_(expected_keys),
            )
        )
        .order_by(table.c.submission_id, table.c.id)
        .with_for_update()
    ).mappings().all()

    facts: dict[str, Mapping[str, object]] = {}
    items = graph.items_by_unit
    units = graph.units_by_id
    steps = graph.steps_by_unit
    external_by_step = graph.external_by_step
    for row in rows:
        submission_id = row.get("submission_id")
        external = next(
            (
                candidate
                for candidate in graph.external_submissions
                if candidate.get("id") == submission_id
            ),
            None,
        )
        if external is None:
            return None
        step_id = str(external.get("step_id", ""))
        unit_id = next(
            (candidate_id for candidate_id, step in steps.items() if step.get("id") == step_id),
            "",
        )
        unit = units.get(unit_id)
        step = steps.get(unit_id)
        item = items.get(unit_id)
        if unit is None or step is None or item is None:
            return None
        if (
            row.get("evidence_kind") != _DUE_EVIDENCE_KIND
            or row.get("evidence_key") != due_evidence_key(str(external.get("id", "")))
            or not _matches_due_row(row, graph, unit, step, external, item)
            or unit_id in facts
        ):
            return None
        facts[unit_id] = dict(row)

    for unit_id, fact in facts.items():
        unit = units.get(unit_id)
        step = steps.get(unit_id)
        external = external_by_step.get(str(step.get("id"))) if step is not None else None
        if unit is None or step is None or external is None:
            return None
        if (
            unit.get("result_status") != "unknown"
            or step.get("execution_status") != "reconciling"
            or external.get("submission_status") != "unknown"
        ):
            return None
    return facts


def build_due_fact_values(
    graph: LockedFrameOptimizeBatch,
    unit_id: str,
    evidence_id: str,
    received_at: datetime,
) -> dict[str, object] | None:
    """Build the one stable-key control fact for an eligible original intent."""

    unit = graph.units_by_id.get(unit_id)
    step = graph.steps_by_unit.get(unit_id)
    item = graph.items_by_unit.get(unit_id)
    external = graph.external_by_step.get(str(step.get("id"))) if step is not None else None
    now = _utc(received_at)
    first = _utc(unit.get("first_intent_at")) if unit is not None else None
    deadline = _utc(unit.get("deadline_at")) if unit is not None else None
    if (
        unit is None
        or step is None
        or item is None
        or external is None
        or not isinstance(evidence_id, str)
        or not evidence_id
        or len(evidence_id) > 36
        or now is None
        or first is None
        or deadline is None
        or now < deadline
        or deadline != first + timedelta(seconds=7200)
        or not intent_row_matches(external, graph, unit, step)
        or external.get("submission_status") not in {"submitting", "unknown"}
        or step.get("execution_status") not in {"submitting", "submitted", "reconciling"}
        or unit.get("result_status") != "pending"
        or unit.get("billing_status") != "unbilled"
        or unit.get("debited_amount") != 0
        or unit.get("refunded_amount") != 0
        or unit.get("success_confirmed_at") is not None
    ):
        return None

    received_text = _time_text(now)
    payload = {
        "owner_id": graph.binding.owner_id,
        "task_id": graph.binding.task_id,
        "unit_id": unit_id,
        "step_id": str(step.get("id")),
        "submission_id": str(external.get("id")),
        "submission_key": str(external.get("submission_key")),
        "request_digest": graph.binding.request_digest,
        "first_intent_at": _time_text(first),
        "deadline_at": _time_text(deadline),
        "received_at": received_text,
    }
    payload_text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload_digest = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
    return {
        "id": evidence_id,
        "step_id": payload["step_id"],
        "submission_id": payload["submission_id"],
        "evidence_key": due_evidence_key(payload["submission_id"]),
        "source": _DUE_EVIDENCE_SOURCE,
        "evidence_kind": _DUE_EVIDENCE_KIND,
        "payload_digest": payload_digest,
        "result_location": payload_text,
        "result_digest": None,
        "received_at": now,
        "validity": "unknown",
        "application_status": "pending",
        "applied_at": None,
    }


def _matches_due_row(row, graph, unit, step, external, item) -> bool:
    location = row.get("result_location")
    received = _utc(row.get("received_at"))
    first = _utc(unit.get("first_intent_at"))
    deadline = _utc(unit.get("deadline_at"))
    if (
        not isinstance(location, str)
        or received is None
        or first is None
        or deadline is None
        or received < deadline
        or deadline != first + timedelta(seconds=7200)
        or not intent_row_matches(external, graph, unit, step)
    ):
        return False
    try:
        payload = json.loads(location)
    except (TypeError, json.JSONDecodeError):
        return False
    expected = {
        "owner_id": graph.binding.owner_id,
        "task_id": graph.binding.task_id,
        "unit_id": str(unit.get("id")),
        "step_id": str(step.get("id")),
        "submission_id": str(external.get("id")),
        "submission_key": str(external.get("submission_key")),
        "request_digest": graph.binding.request_digest,
        "first_intent_at": _time_text(first),
        "deadline_at": _time_text(deadline),
        "received_at": _time_text(received),
    }
    return bool(
        isinstance(payload, dict)
        and set(payload) == _DUE_FIELDS
        and payload == expected
        and row.get("step_id") == step.get("id")
        and row.get("submission_id") == external.get("id")
        and row.get("evidence_key") == due_evidence_key(str(external.get("id")))
        and row.get("source") == _DUE_EVIDENCE_SOURCE
        and row.get("evidence_kind") == _DUE_EVIDENCE_KIND
        and row.get("payload_digest") == hashlib.sha256(location.encode("utf-8")).hexdigest()
        and row.get("result_digest") is None
        and row.get("validity") == "unknown"
        and row.get("application_status") == "pending"
        and row.get("applied_at") is None
        and item.unit_id == unit.get("id")
    )


def _utc(value: object) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _time_text(value: datetime) -> str:
    return _utc(value).isoformat(timespec="microseconds")
