"""Shared SQL validation for durable frame.optimize result and fee progress."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
    ResultEvidenceRow,
)
from haoai_backend.generation_core.frame_optimize_execution import LockedFrameOptimizeBatch
from haoai_backend.generation_core.frame_optimize_application_facts import (
    AppliedFrameOptimizeFact,
    application_fact,
    parse_partial_results,
    reconstruct_expected_chapter,
)
from haoai_backend.generation_core.schema import (
    ApplicationRecordRow,
    GenerationResultReceiptRow,
    LocalContinuationWorkRow,
    PolicyStopEventRow,
    UnitFeeDecisionRow,
)

_OPERATION = "frame.optimize"
_POLICY_VERSION = "frame_optimize_pre_apply_v1"
_PHASE_ORDER = ["fee_pending", "delivery_pending", "finalize_pending"]
_INLINE_PREFIX = "frame-optimize-inline-text-v1:"
_VALIDATION_VERSION = "frame-optimize-text-v1"


@dataclass(frozen=True, slots=True)
class FrameOptimizeProgress:
    """Authenticated records for every Unit in one locked immutable graph."""

    receipts_by_unit: Mapping[str, Mapping[str, object]]
    evidence_by_unit: Mapping[str, Mapping[str, object]]
    works_by_unit: Mapping[str, Mapping[str, object]]
    fees_by_unit: Mapping[str, Mapping[str, object]]
    ledgers_by_unit: Mapping[str, Mapping[str, object]]
    applications_by_unit: Mapping[str, Mapping[str, object]]
    applied_by_unit: Mapping[str, AppliedFrameOptimizeFact]
    unstarted_insufficient_by_unit: Mapping[str, Mapping[str, object]]
    partial_results: tuple[tuple[int, str], ...]
    stopped_insufficient: bool
    policy_stop_event_refs: tuple[str, ...]
    terminal_error: str | None


def lock_frame_optimize_progress(
    session: Session, graph: LockedFrameOptimizeBatch
) -> FrameOptimizeProgress | None:
    """Lock and validate all persisted result, Work and financial progress."""

    unit_ids = tuple(sorted(graph.units_by_id))
    if not unit_ids:
        return None
    task_id = graph.task.get("id")
    owner_id = graph.task.get("user_id")
    if not isinstance(task_id, str) or not isinstance(owner_id, str):
        return None

    receipt_rows = session.execute(
        select(GenerationResultReceiptRow.__table__)
        .where(GenerationResultReceiptRow.task_id == task_id)
        .order_by(GenerationResultReceiptRow.unit_id, GenerationResultReceiptRow.result_ref)
        .with_for_update()
    ).mappings().all()
    evidence_ids = tuple(sorted({str(row["evidence_id"]) for row in receipt_rows}))
    evidence_rows = (
        session.execute(
            select(ResultEvidenceRow.__table__)
            .where(ResultEvidenceRow.id.in_(evidence_ids))
            .order_by(ResultEvidenceRow.id)
            .with_for_update()
        ).mappings().all()
        if evidence_ids
        else []
    )
    work_rows = session.execute(
        select(LocalContinuationWorkRow.__table__)
        .where(LocalContinuationWorkRow.task_id == task_id)
        .order_by(LocalContinuationWorkRow.unit_id, LocalContinuationWorkRow.work_id)
        .with_for_update()
    ).mappings().all()
    fee_rows = session.execute(
        select(UnitFeeDecisionRow.__table__)
        .where(UnitFeeDecisionRow.unit_id.in_(unit_ids))
        .order_by(UnitFeeDecisionRow.unit_id)
        .with_for_update()
    ).mappings().all()
    ledger_rows = session.execute(
        select(CreditLogRow.__table__)
        .where(CreditLogRow.task_id == task_id)
        .order_by(CreditLogRow.billing_unit_id, CreditLogRow.id)
        .with_for_update()
    ).mappings().all()
    application_rows = session.execute(
        select(ApplicationRecordRow.__table__)
        .where(ApplicationRecordRow.task_id == task_id)
        .order_by(ApplicationRecordRow.unit_id, ApplicationRecordRow.application_ref)
        .with_for_update()
    ).mappings().all()
    policy_stop_rows = session.execute(
        select(PolicyStopEventRow.__table__)
        .where(PolicyStopEventRow.task_id == task_id)
        .order_by(PolicyStopEventRow.event_id)
        .with_for_update()
    ).mappings().all()

    receipts = _unique_by_unit(receipt_rows, "unit_id")
    works = _unique_by_unit(work_rows, "unit_id")
    fees = _unique_by_unit(fee_rows, "unit_id")
    evidence_by_id = _unique_by_id(evidence_rows, "id")
    ledgers = _unique_by_unit(ledger_rows, "billing_unit_id")
    applications = _unique_by_unit(application_rows, "unit_id")
    if any(value is None for value in (receipts, works, fees, evidence_by_id, ledgers, applications)):
        return None
    assert receipts is not None and works is not None and fees is not None
    assert evidence_by_id is not None and ledgers is not None and applications is not None

    if (
        set(receipts) - set(unit_ids)
        or set(works) - set(unit_ids)
        or set(fees) - set(unit_ids)
        or set(ledgers) - set(unit_ids)
        or set(applications) - set(unit_ids)
        or len(evidence_by_id) != len(receipt_rows)
    ):
        return None

    evidence_by_unit: dict[str, Mapping[str, object]] = {}
    applied_by_unit: dict[str, AppliedFrameOptimizeFact] = {}
    unstarted_insufficient: dict[str, Mapping[str, object]] = {}
    insufficient_anchors: set[str] = set()
    units = graph.units_by_id
    steps = graph.steps_by_unit
    external = graph.external_by_step
    item_by_unit = graph.items_by_unit
    total_debited = 0
    saw_insufficient = False
    all_decisions = len(fees) == len(units)
    for unit_id in unit_ids:
        unit = units[unit_id]
        step = steps.get(unit_id)
        item = item_by_unit.get(unit_id)
        receipt = receipts.get(unit_id)
        work = works.get(unit_id)
        fee = fees.get(unit_id)
        ledger = ledgers.get(unit_id)
        application = applications.get(unit_id)
        amount = unit.get("quoted_amount")
        if (
            step is None
            or item is None
            or type(amount) is not int
            or amount < 0
            or item.unit_id != unit_id
        ):
            return None

        if receipt is None:
            if (
                work is not None
                or ledger is not None
                or application is not None
                or unit.get("result_status") != "pending"
                or unit.get("billing_status") != "unbilled"
                or unit.get("debited_amount") != 0
                or unit.get("refunded_amount") != 0
                or unit.get("success_confirmed_at") is not None
            ):
                return None
            if fee is not None:
                if not _unstarted_insufficient_matches(
                    fee,
                    unit,
                    step,
                    graph.external_by_step.get(str(step.get("id"))),
                ):
                    return None
                unstarted_insufficient[unit_id] = fee
            continue

        evidence = evidence_by_id.get(str(receipt.get("evidence_id")))
        if evidence is None or work is None:
            return None
        evidence_by_unit[unit_id] = evidence
        fulfilled = _utc(receipt.get("generation_fulfilled_at"))
        first_intent = _utc(unit.get("first_intent_at"))
        deadline = _utc(unit.get("deadline_at"))
        timely = fulfilled is not None and deadline is not None and fulfilled < deadline
        policy_stop = receipt.get("policy_stop_disposition")
        application_allowed = timely and policy_stop is None
        decoded = decode_optimize_text(receipt.get("recoverable_locator"), receipt.get("content_digest"))
        external_row = external.get(str(step.get("id")))
        if (
            fulfilled is None
            or first_intent is None
            or deadline != first_intent + timedelta(seconds=7200)
            or decoded is None
            or receipt.get("owner_id") != owner_id
            or receipt.get("task_id") != task_id
            or receipt.get("unit_id") != unit_id
            or receipt.get("operation") != _OPERATION
            or receipt.get("step_id") != step.get("id")
            or external_row is None
            or receipt.get("submission_id") != external_row.get("id")
            or receipt.get("selected_attempt_id") != item.item_id
            or receipt.get("validation_version") != _VALIDATION_VERSION
            or receipt.get("late_disposition") != (None if timely else "stopped_late_generation")
            or receipt.get("automatic_application_allowed") not in {True, False}
            or receipt.get("automatic_application_allowed") is not application_allowed
            or policy_stop not in {None, "stopped_insufficient"}
            or not _same_time(receipt.get("validated_at"), fulfilled)
            or not _same_time(unit.get("success_confirmed_at"), fulfilled)
            or unit.get("result_status") != "succeeded"
            or external_row.get("submission_status") != "completed"
            or external_row.get("submitted_at") is None
            or work.get("owner_id") != owner_id
            or work.get("task_id") != task_id
            or work.get("unit_id") != unit_id
            or work.get("operation") != _OPERATION
            or work.get("policy_version") != _POLICY_VERSION
            or work.get("result_ref") != receipt.get("result_ref")
            or work.get("content_digest") != receipt.get("content_digest")
            or not _same_time(work.get("generation_fulfilled_at"), fulfilled)
            or not _same_time(work.get("original_first_intent_at"), first_intent)
            or not _same_time(work.get("original_deadline_at"), deadline)
            or work.get("phase_order") != _PHASE_ORDER
            or type(work.get("phase_version")) is not int
        ):
            return None

        phase = work.get("phase")
        resolution = fee.get("resolution") if fee else None
        if phase in {"finalize_pending", "done"}:
            expected_phase_version = 2 if phase == "finalize_pending" else 3
            if (
                work.get("phase_version") != expected_phase_version
                or work.get("fee_decision_ref") != unit_id
                or fee is None
                or application is None
                or work.get("application_ref") != application.get("application_ref")
                or work.get("terminal_reason") is not None
                or step.get("execution_status") != "applied"
                or evidence.get("application_status") != "applied"
                or evidence.get("applied_at") is None
                or not _fee_matches(fee, owner_id, task_id, unit, receipt, ledger, amount)
            ):
                return None
            fact = application_fact(
                application,
                item,
                owner_id=owner_id,
                task_id=task_id,
                result_ref=str(receipt.get("result_ref")),
                result_digest=str(receipt.get("content_digest")),
                optimized_text=decoded,
            )
            if fact is None:
                return None
            applied_by_unit[unit_id] = fact
        elif phase == "stopped_delivery_failed":
            if (
                work.get("phase_version") != 2
                or work.get("fee_decision_ref") != unit_id
                or fee is None
                or application is not None
                or work.get("application_ref") is not None
                or work.get("terminal_reason") not in {
                    "frame_optimize_target_conflict",
                    "frame_optimize_target_deleted",
                }
                or step.get("execution_status") != "result_observed"
                or evidence.get("application_status") != "pending"
                or evidence.get("applied_at") is not None
                or not _fee_matches(fee, owner_id, task_id, unit, receipt, ledger, amount)
            ):
                return None
        elif application is not None or work.get("application_ref") is not None:
            return None
        if phase not in {"finalize_pending", "done", "stopped_delivery_failed"} and (
            step.get("execution_status") != "result_observed"
            or evidence.get("application_status") != "pending"
            or evidence.get("applied_at") is not None
        ):
            return None
        if not _evidence_matches(
            evidence,
            receipt,
            external_row,
            step,
            decoded,
            applied=phase in {"finalize_pending", "done"},
        ):
            return None
        if phase == "fee_pending":
            if (
                work.get("phase_version") != 0
                or fee is not None
                or ledger is not None
                or work.get("fee_decision_ref") is not None
                or unit.get("billing_status") != "unbilled"
                or unit.get("debited_amount") != 0
                or unit.get("refunded_amount") != 0
            ):
                return None
            all_decisions = False
        elif phase == "delivery_pending":
            if (
                work.get("phase_version") != 1
                or work.get("fee_decision_ref") != unit_id
                or fee is None
            ):
                return None
            if not _fee_matches(fee, owner_id, task_id, unit, receipt, ledger, amount):
                return None
            if resolution == "charged":
                total_debited += amount
            elif resolution == "no_charge":
                pass
            else:
                return None
        elif phase in {"finalize_pending", "done", "stopped_delivery_failed"}:
            if not _fee_matches(fee, owner_id, task_id, unit, receipt, ledger, amount):
                return None
            if resolution == "charged":
                total_debited += amount
            elif resolution != "no_charge":
                return None
        elif phase in {"stopped_insufficient", "stopped_late_generation"}:
            expected_resolution = phase
            if (
                work.get("phase_version") != 1
                or work.get("fee_decision_ref") != unit_id
                or fee is None
                or resolution != expected_resolution
                or not _fee_matches(fee, owner_id, task_id, unit, receipt, ledger, amount)
                or ledger is not None
                or unit.get("billing_status") != "unbilled"
                or unit.get("debited_amount") != 0
                or unit.get("refunded_amount") != 0
            ):
                return None
            if phase == "stopped_insufficient":
                saw_insufficient = True
                if amount > 0:
                    insufficient_anchors.add(unit_id)
        else:
            return None

    if unstarted_insufficient and not insufficient_anchors:
        return None
    items_by_index = {item.frame_index: item for item in graph.verified.items}
    partial_results, terminal_error = _parse_task_results(
        graph.task, items_by_index, works, unstarted_insufficient
    )
    if partial_results is None:
        return None
    expected_results = {
        fact.item.frame_index: fact.optimized_text for fact in applied_by_unit.values()
    }
    if (
        len(expected_results) != len(applied_by_unit)
        or len(partial_results) != len(expected_results)
        or {index: text for index, text in partial_results} != expected_results
        or set(applications) != set(applied_by_unit)
        or reconstruct_expected_chapter(graph.verified, expected_results) is None
    ):
        return None
    if graph.task.get("credit_cost") != total_debited:
        return None
    expected_billing = (
        "charged"
        if total_debited > 0
        else "no_charge"
        if all_decisions and all(fee.get("resolution") == "no_charge" for fee in fees.values())
        else "unbilled"
    )
    if graph.task.get("billing_status") != expected_billing:
        return None
    if saw_insufficient != (graph.task.get("recovery_status") == "stopped_insufficient"):
        return None
    if not saw_insufficient and graph.task.get("recovery_status") != "ready":
        return None

    all_closed = _all_generation_slots_closed(unit_ids, works, unstarted_insufficient)
    task_status = graph.task.get("status")
    if task_status == "completed" and (
        not all_closed or not applied_by_unit or terminal_error is not None
    ):
        return None
    if task_status == "failed" and (
        not all_closed or applied_by_unit or terminal_error is None
    ):
        return None
    if task_status in {"completed", "failed"} and (
        graph.task.get("progress") != 100
        or graph.task.get("claimed_by") is not None
        or graph.task.get("claim_token") is not None
        or graph.task.get("lease_until") is not None
    ):
        return None
    if task_status not in {"failed", "completed"} and terminal_error is not None:
        return None

    return FrameOptimizeProgress(
        receipts_by_unit=receipts,
        evidence_by_unit=evidence_by_unit,
        works_by_unit=works,
        fees_by_unit=fees,
        ledgers_by_unit=ledgers,
        applications_by_unit=applications,
        applied_by_unit=applied_by_unit,
        unstarted_insufficient_by_unit=unstarted_insufficient,
        partial_results=partial_results,
        stopped_insufficient=saw_insufficient,
        policy_stop_event_refs=tuple(str(row["event_id"]) for row in policy_stop_rows),
        terminal_error=terminal_error,
    )


def _unstarted_insufficient_matches(fee, unit, step, external) -> bool:
    return bool(
        fee.get("unit_id") == unit.get("id")
        and fee.get("charge_phase") == "pre_apply"
        and fee.get("resolution") == "stopped_insufficient"
        and fee.get("ledger_ref") is None
        and fee.get("generation_result_ref") is None
        and fee.get("application_ref") is None
        and _utc(fee.get("resolved_at")) is not None
        and type(unit.get("quoted_amount")) is int
        and unit.get("quoted_amount") >= 0
        and unit.get("result_status") == "pending"
        and unit.get("billing_status") == "unbilled"
        and unit.get("debited_amount") == 0
        and unit.get("refunded_amount") == 0
        and unit.get("first_intent_at") is None
        and unit.get("deadline_at") is None
        and unit.get("success_confirmed_at") is None
        and unit.get("user_cancelled_at") is None
        and step.get("execution_status") == "prepared"
        and external is None
    )


def _parse_task_results(task, items_by_index, works, unstarted_insufficient):
    raw = task.get("result")
    parsed = parse_partial_results(raw, items_by_index)
    if parsed is not None:
        return parsed, None
    if task.get("status") != "failed" or not isinstance(raw, str):
        return None, None
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None, None
    if not isinstance(value, dict) or set(value) != {"partial", "error"}:
        return None, None
    if value.get("partial") != [] or not isinstance(value.get("error"), str):
        return None, None
    expected = _failure_reason(works, unstarted_insufficient)
    if expected is None or value.get("error") != expected:
        return None, None
    return (), expected


def _failure_reason(works, unstarted_insufficient):
    if unstarted_insufficient or any(
        work.get("phase") == "stopped_insufficient" for work in works.values()
    ):
        return "insufficient_credits"
    reasons = {
        str(work.get("terminal_reason"))
        for work in works.values()
        if work.get("phase") in {"stopped_delivery_failed", "stopped_late_generation"}
        and isinstance(work.get("terminal_reason"), str)
    }
    if len(reasons) == 1:
        return next(iter(reasons))
    if reasons:
        return "frame_optimize_results_unapplied"
    return None


def _all_generation_slots_closed(unit_ids, works, unstarted_insufficient):
    terminal_phases = {
        "done",
        "stopped_insufficient",
        "stopped_delivery_failed",
        "stopped_late_generation",
    }
    closed = {
        unit_id for unit_id, work in works.items() if work.get("phase") in terminal_phases
    } | set(unstarted_insufficient)
    return closed == set(unit_ids) and set(works) <= set(unit_ids)


def decode_optimize_text(locator: object, expected_digest: object) -> str | None:
    if (
        not isinstance(locator, str)
        or not locator.startswith(_INLINE_PREFIX)
        or not isinstance(expected_digest, str)
        or len(expected_digest) != 64
    ):
        return None
    try:
        raw = base64.b64decode(
            locator[len(_INLINE_PREFIX):].encode("ascii"), altchars=b"-_", validate=True
        )
        value = raw.decode("utf-8")
    except (UnicodeError, ValueError):
        return None
    if hashlib.sha256(raw).hexdigest() != expected_digest or not value.strip() or "\x00" in value:
        return None
    return value


def encode_optimize_text(value: str) -> tuple[str, str] | None:
    try:
        raw = value.encode("utf-8")
    except (AttributeError, UnicodeError):
        return None
    if not value.strip() or "\x00" in value or len(raw) > 2 * 1024 * 1024:
        return None
    digest = hashlib.sha256(raw).hexdigest()
    return digest, _INLINE_PREFIX + base64.urlsafe_b64encode(raw).decode("ascii")


def _evidence_matches(evidence, receipt, submission, step, output: str, *, applied: bool) -> bool:
    digest = str(receipt.get("content_digest"))
    return all(
        (
            evidence.get("step_id") == step.get("id"),
            evidence.get("submission_id") == submission.get("id"),
            evidence.get("evidence_key") == f"frame-optimize-result-v1:{submission.get('id')}",
            evidence.get("source") == "provider",
            evidence.get("evidence_kind") == "provider_text",
            evidence.get("payload_digest") == digest,
            evidence.get("result_digest") == digest,
            evidence.get("validity") == "valid",
            evidence.get("application_status") == ("applied" if applied else "pending"),
            decode_optimize_text(evidence.get("result_location"), digest) == output,
        )
    )


def _fee_matches(fee, owner_id, task_id, unit, receipt, ledger, amount: int) -> bool:
    resolution = fee.get("resolution")
    common = all(
        (
            fee.get("unit_id") == unit.get("id"),
            fee.get("charge_phase") == "pre_apply",
            fee.get("generation_result_ref") == receipt.get("result_ref"),
            fee.get("application_ref") is None,
            fee.get("resolved_at") is not None,
        )
    )
    if not common:
        return False
    if resolution == "charged":
        return bool(
            amount > 0
            and fee.get("ledger_ref") is not None
            and ledger is not None
            and ledger.get("id") == fee.get("ledger_ref")
            and ledger.get("user_id") == owner_id
            and ledger.get("task_id") == task_id
            and ledger.get("billing_unit_id") == unit.get("id")
            and ledger.get("business_key") == f"debit:{unit.get('id')}"
            and ledger.get("type") == "usage"
            and ledger.get("amount") == -amount
            and unit.get("billing_status") == "charged"
            and unit.get("debited_amount") == amount
            and unit.get("refunded_amount") == 0
        )
    if resolution == "no_charge":
        return bool(
            amount == 0
            and fee.get("ledger_ref") is None
            and ledger is None
            and unit.get("billing_status") == "no_charge"
            and unit.get("debited_amount") == 0
            and unit.get("refunded_amount") == 0
        )
    if resolution in {"stopped_insufficient", "stopped_late_generation"}:
        return bool(
            fee.get("ledger_ref") is None
            and ledger is None
            and unit.get("billing_status") == "unbilled"
            and unit.get("debited_amount") == 0
            and unit.get("refunded_amount") == 0
        )
    return False


def _unique_by_unit(rows, key):
    result: dict[str, Mapping[str, object]] = {}
    for row in rows:
        value = dict(row)
        unit_id = value.get(key)
        if not isinstance(unit_id, str) or unit_id in result:
            return None
        result[unit_id] = value
    return result


def _unique_by_id(rows, key):
    result: dict[str, Mapping[str, object]] = {}
    for row in rows:
        value = dict(row)
        identity = value.get(key)
        if not isinstance(identity, str) or identity in result:
            return None
        result[identity] = value
    return result


def _utc(value):
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _same_time(left, right) -> bool:
    return _utc(left) is not None and _utc(left) == _utc(right)


__all__ = [
    "FrameOptimizeProgress",
    "decode_optimize_text",
    "encode_optimize_text",
    "lock_frame_optimize_progress",
]
