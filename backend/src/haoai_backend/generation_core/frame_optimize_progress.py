"""Shared SQL validation for durable frame.optimize result and fee progress."""

from __future__ import annotations

import base64
import hashlib
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
from haoai_backend.generation_core.schema import (
    ApplicationRecordRow,
    GenerationResultReceiptRow,
    LocalContinuationWorkRow,
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
    stopped_insufficient: bool


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
    applications = session.execute(
        select(ApplicationRecordRow.application_ref)
        .where(ApplicationRecordRow.task_id == task_id)
        .limit(1)
    ).first()

    receipts = _unique_by_unit(receipt_rows, "unit_id")
    works = _unique_by_unit(work_rows, "unit_id")
    fees = _unique_by_unit(fee_rows, "unit_id")
    evidence_by_id = _unique_by_id(evidence_rows, "id")
    ledgers = _unique_by_unit(ledger_rows, "billing_unit_id")
    if any(value is None for value in (receipts, works, fees, evidence_by_id, ledgers)):
        return None
    assert receipts is not None and works is not None and fees is not None
    assert evidence_by_id is not None and ledgers is not None

    if (
        set(receipts) - set(unit_ids)
        or set(works) - set(unit_ids)
        or set(fees) - set(unit_ids)
        or set(ledgers) - set(unit_ids)
        or applications is not None
        or len(evidence_by_id) != len(receipt_rows)
    ):
        return None

    evidence_by_unit: dict[str, Mapping[str, object]] = {}
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
                or fee is not None
                or ledger is not None
                or unit.get("result_status") != "pending"
                or unit.get("billing_status") != "unbilled"
                or unit.get("debited_amount") != 0
                or unit.get("refunded_amount") != 0
                or unit.get("success_confirmed_at") is not None
            ):
                return None
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
            or step.get("execution_status") != "result_observed"
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
            or work.get("application_ref") is not None
        ):
            return None
        if not _evidence_matches(evidence, receipt, external_row, step, decoded):
            return None

        phase = work.get("phase")
        resolution = fee.get("resolution") if fee else None
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
        else:
            return None

    if graph.task.get("result") is not None:
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

    return FrameOptimizeProgress(
        receipts_by_unit=receipts,
        evidence_by_unit=evidence_by_unit,
        works_by_unit=works,
        fees_by_unit=fees,
        ledgers_by_unit=ledgers,
        stopped_insufficient=saw_insufficient,
    )


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


def _evidence_matches(evidence, receipt, submission, step, output: str) -> bool:
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
            evidence.get("application_status") == "pending",
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
