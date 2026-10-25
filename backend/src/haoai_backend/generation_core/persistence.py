"""Caller-session repository for the eight canonical generation records.

The repository stages and flushes only. The caller owns commit, rollback, and
Session lifetime; returned records are values, not proof of a committed write.
"""

from __future__ import annotations

from dataclasses import dataclass, fields as dataclass_fields
from datetime import datetime, timezone
from typing import Literal, TypeAlias, cast

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.records import (
    ApplicationRecord,
    ExecutionAttemptGroup,
    GenerationResultReceipt,
    LocalContinuationWork,
    PolicyStopEvent,
    PollReservation,
    StreamFeeRecord,
    UnitFeeDecision,
)
from haoai_backend.generation_contracts.types import WorkPhase

from .base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
    ResultEvidenceRow,
    UserRow,
)
from .schema import (
    ApplicationRecordRow,
    ExecutionAttemptGroupRow,
    GenerationResultReceiptRow,
    LocalContinuationWorkRow,
    PolicyStopEventRow,
    PollReservationRow,
    StreamFeeRecordRow,
    UnitFeeDecisionRow,
)
from .canvas_result_targets import prepare_canvas_result_target_projection


GenerationRecord: TypeAlias = (
    UnitFeeDecision
    | GenerationResultReceipt
    | ApplicationRecord
    | ExecutionAttemptGroup
    | LocalContinuationWork
    | StreamFeeRecord
    | PollReservation
    | PolicyStopEvent
)
WriteStatus = Literal["created", "same", "updated", "not_found", "conflict", "invalid_binding"]


@dataclass(frozen=True, slots=True)
class RecordWriteResult:
    status: WriteStatus
    record: GenerationRecord | None = None
    detail: str | None = None


class GenerationRecordRepository:
    """Persist generation records in a supplied SQLAlchemy Session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def find_unit_fee_decision(self, unit_id: str) -> UnitFeeDecision | None:
        row = self._session.get(UnitFeeDecisionRow, unit_id)
        return None if row is None else _unit_fee_record(row)

    def find_result_receipt(
        self, owner_id: str, task_id: str, result_ref: str
    ) -> GenerationResultReceipt | None:
        row = self._session.get(GenerationResultReceiptRow, result_ref)
        if row is None or row.owner_id != owner_id or row.task_id != task_id:
            return None
        return _result_record(row)

    def find_application_record(
        self, owner_id: str, task_id: str, result_ref: str, target_scope: str
    ) -> ApplicationRecord | None:
        row = self._session.execute(
            select(ApplicationRecordRow).where(
                ApplicationRecordRow.result_ref == result_ref,
                ApplicationRecordRow.target_scope == target_scope,
            )
        ).scalar_one_or_none()
        if row is None or row.owner_id != owner_id or row.task_id != task_id:
            return None
        return _application_record(row)

    def find_attempt_group(self, unit_id: str, group_key: str) -> ExecutionAttemptGroup | None:
        row = self._session.get(ExecutionAttemptGroupRow, (unit_id, group_key))
        return None if row is None else _attempt_group_record(row)

    def find_continuation_work(
        self, owner_id: str, task_id: str, work_id: str
    ) -> LocalContinuationWork | None:
        row = self._session.get(LocalContinuationWorkRow, work_id)
        if row is None or row.owner_id != owner_id or row.task_id != task_id:
            return None
        return _work_record(row)

    def find_stream_fee_record(
        self, owner_id: str, server_attempt_id: str
    ) -> StreamFeeRecord | None:
        row = self._session.get(StreamFeeRecordRow, server_attempt_id)
        if row is None or row.owner_id != owner_id:
            return None
        return _stream_fee_record(row)

    def find_poll_reservation(
        self, unit_id: str, group_key: str, sequence: int
    ) -> PollReservation | None:
        row = self._session.execute(
            select(PollReservationRow).where(
                PollReservationRow.unit_id == unit_id,
                PollReservationRow.group_key == group_key,
                PollReservationRow.sequence == sequence,
            )
        ).scalar_one_or_none()
        return None if row is None else _poll_record(row)

    def find_policy_stop_event(
        self, owner_id: str, task_id: str, event_id: str
    ) -> PolicyStopEvent | None:
        row = self._session.get(PolicyStopEventRow, event_id)
        if row is None or row.owner_id != owner_id or row.task_id != task_id:
            return None
        return _policy_stop_record(row)

    def stage_record(self, record: GenerationRecord) -> RecordWriteResult:
        """Stage an immutable insert, returning same/conflict on natural-key replay."""

        if isinstance(record, PollReservation):
            return RecordWriteResult(
                "invalid_binding",
                detail="poll reservations require reserve_poll(unit_id=...) and group compare-and-set",
            )
        existing = self._find_same_key(record)
        if existing is not None:
            if not _record_scope_matches(record, existing):
                return RecordWriteResult(
                    "conflict",
                    detail="natural key already belongs to another owner/task scope",
                )
            same = _record_values_equal(existing, record)
            return RecordWriteResult(
                "same" if same else "conflict",
                existing,
                None if same else "natural key already binds a different record",
            )
        if isinstance(record, LocalContinuationWork) and (
            not record.phase_order
            or record.phase != record.phase_order[0]
            or record.phase_version != 0
        ):
            return RecordWriteResult(
                "invalid_binding",
                detail="new continuation work must start at its frozen first phase and version zero",
            )
        invalid = self._validate_bindings(record)
        if invalid is not None:
            return RecordWriteResult("invalid_binding", detail=invalid)
        target_projection = None
        if isinstance(record, GenerationResultReceipt):
            prepared_projection = prepare_canvas_result_target_projection(
                self._session,
                record,
            )
            if prepared_projection.status == "invalid_binding":
                return RecordWriteResult(
                    "invalid_binding",
                    detail=prepared_projection.detail,
                )
            if prepared_projection.status == "project":
                target_projection = prepared_projection.row
        self._session.add(_row_from_record(record))
        self._session.flush()
        if target_projection is not None:
            self._session.add(target_projection)
            self._session.flush()
        return RecordWriteResult("created", record)

    def reserve_poll(
        self,
        reservation: PollReservation,
        *,
        unit_id: str,
        expected_poll_used: int,
        expected_poll_sequence: int,
    ) -> RecordWriteResult:
        """Atomically stage one original poll-budget reservation and group CAS."""

        existing = self._session.execute(
            select(PollReservationRow).where(
                PollReservationRow.unit_id == unit_id,
                PollReservationRow.group_key == reservation.group_key,
                PollReservationRow.sequence == reservation.sequence,
            )
        ).scalar_one_or_none()
        existing_by_id = self._session.get(PollReservationRow, reservation.reservation_id)
        if existing is not None or existing_by_id is not None:
            if (
                existing is not None
                and existing.unit_id == unit_id
                and existing.reservation_id == reservation.reservation_id
                and existing_by_id is not None
                and _record_values_equal(_poll_record(existing), reservation)
            ):
                return RecordWriteResult("same", reservation)
            return RecordWriteResult(
                "conflict",
                None if existing is None else _poll_record(existing),
                "poll reservation id or group sequence already binds another reservation",
            )
        if reservation.state != "reserved":
            return RecordWriteResult("invalid_binding", detail="new poll reservations must be reserved")
        if (
            reservation.committed_poll_count != expected_poll_used + 1
            or reservation.sequence != expected_poll_sequence + 1
        ):
            return RecordWriteResult("conflict", detail="reservation count or sequence is not the next value")
        invalid = self._validate_poll_reservation(reservation, unit_id)
        if invalid is not None:
            return RecordWriteResult("invalid_binding", detail=invalid)
        changed = self._session.execute(
            update(ExecutionAttemptGroupRow)
            .where(
                ExecutionAttemptGroupRow.unit_id == unit_id,
                ExecutionAttemptGroupRow.group_key == reservation.group_key,
                ExecutionAttemptGroupRow.poll_used == expected_poll_used,
                ExecutionAttemptGroupRow.poll_sequence == expected_poll_sequence,
                ExecutionAttemptGroupRow.outstanding_reservation_ref.is_(None),
            )
            .values(
                poll_used=reservation.committed_poll_count,
                poll_sequence=reservation.sequence,
                next_poll_at=reservation.next_poll_at,
                outstanding_reservation_ref=reservation.reservation_id,
            )
        )
        if changed.rowcount != 1:
            return RecordWriteResult("conflict", detail="attempt-group poll compare-and-set lost")
        self._session.add(_poll_row(reservation, unit_id=unit_id))
        self._session.flush()
        return RecordWriteResult("created", reservation)

    def advance_continuation_work(
        self,
        updated: LocalContinuationWork,
        *,
        expected_phase_version: int,
    ) -> RecordWriteResult:
        """Advance immutable work facts with an exact phase-version compare-and-set."""

        row = self._session.get(LocalContinuationWorkRow, updated.work_id)
        if row is None:
            return RecordWriteResult("not_found")
        current = _work_record(row)
        if not _record_scope_matches(updated, current):
            return RecordWriteResult(
                "conflict",
                detail="continuation work belongs to another owner/task/unit scope",
            )
        if _record_values_equal(current, updated):
            return RecordWriteResult("same", current)
        if row.phase_version != expected_phase_version or updated.phase_version != expected_phase_version + 1:
            return RecordWriteResult("conflict", current, "continuation phase version changed")
        immutable_fields = (
            "work_id", "owner_id", "task_id", "unit_id", "operation", "policy_version",
            "result_ref", "content_digest", "generation_fulfilled_at", "original_first_intent_at",
            "original_deadline_at", "phase_order",
        )
        if any(
            not _field_values_equal(getattr(current, name), getattr(updated, name))
            for name in immutable_fields
        ):
            return RecordWriteResult("conflict", current, "immutable continuation binding changed")
        if not _allowed_work_transition(current.phase, updated.phase, current.phase_order):
            return RecordWriteResult("conflict", current, "continuation phase transition is not allowed")
        invalid = self._validate_bindings(updated)
        if invalid is not None:
            return RecordWriteResult("invalid_binding", current, invalid)
        changed = self._session.execute(
            update(LocalContinuationWorkRow)
            .where(
                LocalContinuationWorkRow.work_id == updated.work_id,
                LocalContinuationWorkRow.phase_version == expected_phase_version,
                LocalContinuationWorkRow.phase == current.phase,
            )
            .values(
                phase=updated.phase,
                phase_version=updated.phase_version,
                fee_decision_ref=updated.fee_decision_ref,
                application_ref=updated.application_ref,
                terminal_reason=updated.terminal_reason,
                last_execution_generation=updated.last_execution_generation,
            )
        )
        if changed.rowcount != 1:
            return RecordWriteResult("conflict", current, "continuation phase compare-and-set lost")
        self._session.flush()
        return RecordWriteResult("updated", updated)

    def _find_same_key(self, record: GenerationRecord) -> GenerationRecord | None:
        if isinstance(record, UnitFeeDecision):
            row = self._session.get(UnitFeeDecisionRow, record.unit_id)
            return None if row is None else _unit_fee_record(row)
        if isinstance(record, GenerationResultReceipt):
            row = self._session.get(GenerationResultReceiptRow, record.result_ref)
            return None if row is None else _result_record(row)
        if isinstance(record, ApplicationRecord):
            row = self._session.execute(
                select(ApplicationRecordRow).where(
                    ApplicationRecordRow.result_ref == record.result_ref,
                    ApplicationRecordRow.target_scope == record.target_scope,
                )
            ).scalar_one_or_none()
            return None if row is None else _application_record(row)
        if isinstance(record, ExecutionAttemptGroup):
            row = self._session.get(ExecutionAttemptGroupRow, (record.unit_id, record.group_key))
            return None if row is None else _attempt_group_record(row)
        if isinstance(record, LocalContinuationWork):
            row = self._session.execute(
                select(LocalContinuationWorkRow).where(
                    LocalContinuationWorkRow.task_id == record.task_id,
                    LocalContinuationWorkRow.unit_id == record.unit_id,
                    LocalContinuationWorkRow.policy_version == record.policy_version,
                )
            ).scalar_one_or_none()
            return None if row is None else _work_record(row)
        if isinstance(record, StreamFeeRecord):
            row = self._session.get(StreamFeeRecordRow, record.server_attempt_id)
            return None if row is None else _stream_fee_record(row)
        if isinstance(record, PollReservation):
            row = self._session.get(PollReservationRow, record.reservation_id)
            return None if row is None else _poll_record(row)
        if isinstance(record, PolicyStopEvent):
            row = self._session.execute(
                select(PolicyStopEventRow).where(
                    PolicyStopEventRow.unit_id == record.unit_id,
                    PolicyStopEventRow.group_key == record.group_key,
                    PolicyStopEventRow.policy_version == record.policy_version,
                    PolicyStopEventRow.reason == record.reason,
                )
            ).scalar_one_or_none()
            return None if row is None else _policy_stop_record(row)
        raise TypeError(f"unsupported generation record type: {type(record).__name__}")

    def _validate_bindings(self, record: GenerationRecord) -> str | None:
        if isinstance(record, UnitFeeDecision):
            return self._validate_fee_decision(record)
        if isinstance(record, GenerationResultReceipt):
            return self._validate_result_receipt(record)
        if isinstance(record, ApplicationRecord):
            result = self._session.get(GenerationResultReceiptRow, record.result_ref)
            if result is None or (
                result.owner_id != record.owner_id
                or result.task_id != record.task_id
                or result.unit_id != record.unit_id
                or result.content_digest != record.result_digest
            ):
                return "application does not match its original result receipt"
            task, unit = self._task_unit(record.owner_id, record.task_id, record.unit_id)
            return None if task is not None and unit is not None else "application owner/task/unit binding is invalid"
        if isinstance(record, ExecutionAttemptGroup):
            unit = self._session.get(BillingUnitRow, record.unit_id)
            if unit is None:
                return "attempt group unit does not exist"
            if len(record.attempt_step_ids) != 3 or len(set(record.attempt_step_ids)) != 3:
                return "attempt group must retain its three distinct frozen steps"
            if record.max_retries != 2 or not 0 <= record.active_attempt < 3:
                return "attempt group retry or active-attempt value is incompatible"
            for step_id in record.attempt_step_ids:
                step = self._session.get(ExecutionStepRow, step_id)
                if step is None or step.billing_unit_id != record.unit_id:
                    return "attempt group step is not bound to its original unit"
            if record.selected_evidence_id is not None:
                evidence = self._session.get(ResultEvidenceRow, record.selected_evidence_id)
                if evidence is None or evidence.step_id not in record.attempt_step_ids:
                    return "selected evidence is not bound to an attempt-group step"
            return None
        if isinstance(record, LocalContinuationWork):
            return self._validate_continuation_work(record)
        if isinstance(record, StreamFeeRecord):
            return self._validate_stream_fee_record(record)
        if isinstance(record, PollReservation):
            return "poll reservations require explicit unit-scoped compare-and-set"
        if isinstance(record, PolicyStopEvent):
            return self._validate_policy_stop_event(record)
        raise TypeError(f"unsupported generation record type: {type(record).__name__}")

    def _validate_result_receipt(self, record: GenerationResultReceipt) -> str | None:
        task, unit = self._task_unit(record.owner_id, record.task_id, record.unit_id)
        if task is None or unit is None or unit.operation != record.operation:
            return "result owner/task/unit/operation binding is invalid"
        step = self._session.get(ExecutionStepRow, record.step_id)
        submission = self._session.get(ExternalSubmissionRow, record.submission_id)
        evidence = self._session.get(ResultEvidenceRow, record.evidence_id)
        if step is None or step.billing_unit_id != record.unit_id:
            return "result step is not bound to its billing unit"
        if submission is None or submission.step_id != record.step_id:
            return "result submission is not bound to its original step"
        if evidence is None or evidence.step_id != record.step_id or evidence.submission_id != record.submission_id:
            return "result evidence is not bound to its original step and submission"
        if evidence.validity != "valid" or evidence.result_digest != record.content_digest:
            return "result receipt does not match valid stored evidence"
        return None

    def _validate_fee_decision(self, record: UnitFeeDecision) -> str | None:
        unit = self._session.get(BillingUnitRow, record.unit_id)
        if unit is None:
            return "fee decision unit does not exist"
        if record.resolution == "no_charge" and unit.quoted_amount != 0:
            return "no_charge is valid only for a zero-quote unit"
        if record.resolution == "charged" and record.ledger_ref is None:
            return "charged decision requires the original ledger reference"
        if record.resolution in {"waived_insufficient", "waived_charge_failed"}:
            if record.resolved_at is None:
                return "definitive fee waiver requires a resolution timestamp"
            if record.ledger_ref is not None:
                return "definitive fee waiver cannot reference a debit ledger"
            if (
                unit.billing_status != "unbilled"
                or unit.debited_amount != 0
                or unit.refunded_amount != 0
            ):
                return "definitive fee waiver requires the original unit to remain unbilled"
            existing_ledger = self._session.execute(
                select(CreditLogRow.id)
                .where(CreditLogRow.billing_unit_id == record.unit_id)
                .limit(1)
            ).first()
            if existing_ledger is not None:
                return "definitive fee waiver cannot coexist with a unit ledger entry"
        if record.ledger_ref is not None:
            log = self._session.get(CreditLogRow, record.ledger_ref)
            task = self._session.get(AITaskRow, unit.task_id)
            if (
                log is None or task is None or log.billing_unit_id != record.unit_id
                or log.task_id != unit.task_id or log.user_id != task.user_id
                or log.type != "usage" or log.amount >= 0
            ):
                return "fee ledger reference is not the original owner/task/unit debit"
        if record.generation_result_ref is not None:
            result = self._session.get(GenerationResultReceiptRow, record.generation_result_ref)
            if result is None or result.unit_id != record.unit_id:
                return "fee decision result reference belongs to another unit"
        if record.application_ref is not None:
            application = self._session.get(ApplicationRecordRow, record.application_ref)
            if application is None or application.unit_id != record.unit_id:
                return "fee decision application reference belongs to another unit"
        return None

    def _validate_completed_fee_decision(self, record: UnitFeeDecision) -> str | None:
        """Require the Unit ledger state to agree with a terminal fee decision."""

        unit = self._session.get(BillingUnitRow, record.unit_id)
        if unit is None:
            return "fee decision unit does not exist"
        if record.resolution == "charged":
            if (
                unit.billing_status != "charged"
                or unit.debited_amount <= 0
                or unit.refunded_amount != 0
            ):
                return "charged completion requires a charged, non-refunded Unit"
            log = self._session.get(CreditLogRow, record.ledger_ref)
            if log is None or unit.debited_amount != -log.amount:
                return "Unit debit amount does not match its original usage ledger"
            return None
        if record.resolution == "no_charge":
            if record.ledger_ref is not None:
                return "no_charge completion cannot reference a usage ledger"
            if (
                unit.billing_status != "no_charge"
                or unit.quoted_amount != 0
                or unit.debited_amount != 0
                or unit.refunded_amount != 0
            ):
                return "no_charge completion requires a zero-debit, no-charge Unit"
            ledger = self._session.execute(
                select(CreditLogRow.id)
                .where(CreditLogRow.billing_unit_id == record.unit_id)
                .limit(1)
            ).first()
            if ledger is not None:
                return "no_charge completion cannot coexist with a Unit ledger entry"
        return None

    def _validate_continuation_work(self, record: LocalContinuationWork) -> str | None:
        task, unit = self._task_unit(record.owner_id, record.task_id, record.unit_id)
        result = self._session.get(GenerationResultReceiptRow, record.result_ref)
        if task is None or unit is None or result is None:
            return "continuation owner/task/unit/result binding is incomplete"
        if (
            unit.operation != record.operation or result.owner_id != record.owner_id
            or result.task_id != record.task_id or result.unit_id != record.unit_id
            or result.content_digest != record.content_digest
            or not _same_instant(result.generation_fulfilled_at, record.generation_fulfilled_at)
        ):
            return "continuation work differs from its selected fulfilled result"
        if (
            unit.first_intent_at is None or unit.deadline_at is None
            or not _same_instant(unit.first_intent_at, record.original_first_intent_at)
            or not _same_instant(unit.deadline_at, record.original_deadline_at)
        ):
            return "continuation work changed the original intent or deadline"
        expected_order = {
            "frame.review": ("delivery_pending", "fee_pending", "finalize_pending"),
            "frame.optimize": ("fee_pending", "delivery_pending", "finalize_pending"),
        }.get(record.operation)
        if expected_order is None or tuple(record.phase_order) != expected_order:
            return "continuation phase order is not the frozen operation order"
        allowed_phases = set(expected_order) | {
            "done", "stopped_cancelled", "stopped_insufficient",
            "stopped_delivery_failed", "stopped_late_generation",
        }
        if record.phase not in allowed_phases or record.phase_version < 0:
            return "continuation phase or version is invalid"
        if record.last_execution_generation is not None and record.last_execution_generation != task.execution_generation:
            return "continuation Task generation fence is stale"
        if record.fee_decision_ref is not None:
            fee = self._session.get(UnitFeeDecisionRow, record.fee_decision_ref)
            if fee is None or fee.unit_id != record.unit_id:
                return "continuation fee decision reference belongs to another unit"
        if record.application_ref is not None:
            application = self._session.get(ApplicationRecordRow, record.application_ref)
            if application is None or application.result_ref != record.result_ref:
                return "continuation application reference belongs to another result"
        if record.phase == "done":
            if record.phase_version != len(expected_order):
                return "done requires every frozen continuation phase to have advanced"
            if record.fee_decision_ref is None or record.application_ref is None:
                return "done requires the original settled fee and applied delivery records"
            fee = self._session.get(UnitFeeDecisionRow, record.fee_decision_ref)
            application = self._session.get(ApplicationRecordRow, record.application_ref)
            if fee is None or application is None:
                return "done completion records are absent"
            fee_record = _unit_fee_record(fee)
            allowed_fee_resolutions = {"charged", "no_charge"}
            definitive_review_waivers = {"waived_insufficient", "waived_charge_failed"}
            if record.operation == "frame.review":
                allowed_fee_resolutions |= definitive_review_waivers
            if fee_record.resolution not in allowed_fee_resolutions:
                return "done requires a settled fee decision permitted for this operation"
            if fee_record.resolved_at is None:
                return "done requires a resolved fee decision"
            if (
                fee_record.generation_result_ref not in {None, record.result_ref}
                or fee_record.application_ref not in {None, record.application_ref}
            ):
                return "done fee decision is not bound to the original result and application"
            if fee_record.resolution in definitive_review_waivers and (
                fee_record.charge_phase != "post_apply"
                or fee_record.generation_result_ref != record.result_ref
                or fee_record.application_ref != record.application_ref
            ):
                return "review fee waiver must follow and bind the original applied result"
            fee_error = self._validate_fee_decision(fee_record)
            if fee_error is not None:
                return "done fee decision is invalid: " + fee_error
            completion_error = self._validate_completed_fee_decision(fee_record)
            if completion_error is not None:
                return "done fee decision does not match the original unit: " + completion_error
            application_record = _application_record(application)
            if (
                application_record.owner_id != record.owner_id
                or application_record.task_id != record.task_id
                or application_record.unit_id != record.unit_id
                or application_record.result_ref != record.result_ref
                or application_record.result_digest != record.content_digest
                or application_record.delivery_status != "applied"
                or application_record.committed_at is None
            ):
                return "done requires the original committed application result"
        return None

    def _validate_stream_fee_record(self, record: StreamFeeRecord) -> str | None:
        owner = self._session.get(UserRow, record.owner_id)
        if owner is None or record.operation != "text.stream":
            return "stream fee owner or operation binding is invalid"
        if record.quoted_amount < 0 or record.actual_debited_amount < 0:
            return "stream fee amounts cannot be negative"
        if record.actual_debited_amount == 0:
            if record.original_debit_log_id is not None or record.original_business_key is not None:
                return "zero stream fee cannot point to a debit log"
            if record.disposition == "debit":
                return "debit disposition must contain a positive verified debit"
        else:
            expected_key = f"stream:debit:{record.server_attempt_id}"
            if (
                record.disposition != "debit"
                or record.original_debit_log_id is None
                or record.original_business_key != expected_key
            ):
                return "positive stream debit must retain its original attempt key"
            log = self._session.get(CreditLogRow, record.original_debit_log_id)
            if (
                log is None or log.user_id != record.owner_id or log.type != "usage"
                or log.amount != -record.actual_debited_amount
                or log.business_key != expected_key or log.billing_unit_id is not None
                or log.task_id is not None or log.related_debit_id is not None
            ):
                return "stream debit does not match the original taskless ledger entry"
        if (record.task_id is None) != (record.message_id is None):
            return "optional stream Task and message binding must be present together"
        if record.task_id is not None:
            task = self._session.get(AITaskRow, record.task_id)
            if task is None or task.user_id != record.owner_id or task.message_id != record.message_id:
                return "optional stream Task binding belongs to another owner or message"
        return None

    def _validate_poll_reservation(
        self, record: PollReservation, unit_id: str
    ) -> str | None:
        group = self._session.get(ExecutionAttemptGroupRow, (unit_id, record.group_key))
        if group is None:
            return "poll reservation group does not exist"
        attempt_steps = tuple(group.attempt_step_ids or ())
        if not 0 <= group.active_attempt < len(attempt_steps):
            return "attempt group active index is invalid"
        if record.step_id != attempt_steps[group.active_attempt]:
            return "poll reservation step is not the sole active attempt"
        step = self._session.get(ExecutionStepRow, record.step_id)
        submission = self._session.get(ExternalSubmissionRow, record.submission_id)
        unit = self._session.get(BillingUnitRow, unit_id)
        if (
            step is None or unit is None or step.billing_unit_id != unit_id
            or submission is None or submission.step_id != record.step_id
            or submission.external_task_id != record.external_id
            or submission.request_digest != record.payload_digest
            or record.payload_digest != group.original_payload_digest
        ):
            return "poll reservation does not match the original active submission"
        if record.committed_poll_count != group.poll_used + 1 or record.sequence != group.poll_sequence + 1:
            return "poll reservation does not use the next committed budget and sequence"
        if group.outstanding_reservation_ref is not None:
            return "attempt group already has an outstanding reservation"
        return None

    def _validate_policy_stop_event(self, record: PolicyStopEvent) -> str | None:
        group = self._session.get(ExecutionAttemptGroupRow, (record.unit_id, record.group_key))
        if group is None:
            return "policy event group does not exist"
        task, unit = self._task_unit(record.owner_id, record.task_id, record.unit_id)
        if task is None or unit is None:
            return "policy event owner/task/unit binding is invalid"
        active_steps = tuple(group.attempt_step_ids or ())
        if not 0 <= group.active_attempt < len(active_steps) or record.active_step_id != active_steps[group.active_attempt]:
            return "policy event does not identify the active group attempt"
        step = self._session.get(ExecutionStepRow, record.active_step_id)
        submission = self._session.get(ExternalSubmissionRow, record.active_submission_id)
        if (
            step is None or step.billing_unit_id != record.unit_id
            or submission is None or submission.step_id != record.active_step_id
            or submission.external_task_id != record.external_id
        ):
            return "policy event is not bound to the original active submission"
        reservation_rows: list[PollReservationRow] = []
        for ref in record.reservation_refs:
            reservation = self._session.get(PollReservationRow, ref)
            if reservation is None or reservation.unit_id != record.unit_id or reservation.group_key != record.group_key:
                return "policy event reservation references cross a group boundary"
            reservation_rows.append(reservation)
        sequences = [item.sequence for item in reservation_rows]
        if sequences != sorted(sequences) or len(set(sequences)) != len(sequences):
            return "policy event reservation references are not in increasing sequence order"
        if len(set(record.observation_refs)) != len(record.observation_refs):
            return "policy event observation references must be unique and ordered"
        if record.reason != "auth_unavailable_12":
            return "policy event reason is not the frozen auth12 rule"
        if record.financial_disposition == "no_debit":
            if record.amount != 0 or record.original_debit_ref is not None or record.refund_ref is not None:
                return "no_debit event must retain amount zero and no ledger links"
        elif record.original_debit_ref is not None:
            debit = self._session.get(CreditLogRow, record.original_debit_ref)
            if (
                debit is None or debit.user_id != record.owner_id or debit.task_id != record.task_id
                or debit.billing_unit_id != record.unit_id or debit.type != "usage" or debit.amount >= 0
            ):
                return "policy event original debit is not bound to owner/task/unit"
            if record.amount > -debit.amount:
                return "policy event amount exceeds its original debit"
        elif record.financial_disposition != "no_debit":
            return "policy event financial disposition lacks original debit evidence"
        if record.refund_ref is not None:
            refund = self._session.get(CreditLogRow, record.refund_ref)
            if (
                refund is None or refund.user_id != record.owner_id or refund.task_id != record.task_id
                or refund.billing_unit_id != record.unit_id or refund.type != "refund"
                or refund.related_debit_id != record.original_debit_ref or refund.amount != record.amount
            ):
                return "policy event refund is not the exact original related debit"
        if record.financial_disposition == "refunded" and record.refund_ref is None:
            return "refunded policy event must reference its original refund"
        return None

    def _task_unit(
        self, owner_id: str, task_id: str, unit_id: str
    ) -> tuple[AITaskRow | None, BillingUnitRow | None]:
        task = self._session.get(AITaskRow, task_id)
        unit = self._session.get(BillingUnitRow, unit_id)
        if task is None or unit is None or task.user_id != owner_id or unit.task_id != task_id:
            return None, None
        return task, unit


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _to_database_time(value: datetime | None) -> datetime | None:
    normalized = _as_utc(value)
    return None if normalized is None else normalized.replace(tzinfo=None)


def _record_values_equal(left: GenerationRecord, right: GenerationRecord) -> bool:
    if type(left) is not type(right):
        return False
    return all(
        _field_values_equal(getattr(left, item.name), getattr(right, item.name))
        for item in dataclass_fields(left)
    )


def _record_scope_matches(candidate: GenerationRecord, existing: GenerationRecord) -> bool:
    if type(candidate) is not type(existing):
        return False
    if isinstance(candidate, (GenerationResultReceipt, ApplicationRecord, LocalContinuationWork, PolicyStopEvent)):
        scope_fields = ("owner_id", "task_id", "unit_id")
    elif isinstance(candidate, StreamFeeRecord):
        scope_fields = ("owner_id",)
    else:
        return True
    return all(
        getattr(candidate, field_name) == getattr(existing, field_name)
        for field_name in scope_fields
    )


def _field_values_equal(left: object, right: object) -> bool:
    if isinstance(left, datetime) and isinstance(right, datetime):
        return _same_instant(left, right)
    if isinstance(left, (tuple, list)) and isinstance(right, (tuple, list)):
        return len(left) == len(right) and all(
            _field_values_equal(left_item, right_item)
            for left_item, right_item in zip(left, right)
        )
    return left == right


def _same_instant(left: datetime, right: datetime) -> bool:
    def normalized(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    return normalized(left) == normalized(right)


def _allowed_work_transition(
    current: WorkPhase, target: WorkPhase, phase_order: tuple[WorkPhase, ...]
) -> bool:
    stopped = {
        "stopped_cancelled", "stopped_insufficient",
        "stopped_delivery_failed", "stopped_late_generation",
    }
    if current in stopped or current == "done":
        return False
    if target == "done":
        return bool(phase_order) and current == phase_order[-1]
    if target in stopped:
        return True
    if current not in phase_order:
        return False
    try:
        return phase_order.index(target) == phase_order.index(current) + 1
    except ValueError:
        return False


def _unit_fee_record(row: UnitFeeDecisionRow) -> UnitFeeDecision:
    return UnitFeeDecision(
        row.unit_id, row.charge_phase, row.resolution, row.ledger_ref,
        row.generation_result_ref, row.application_ref, _as_utc(row.resolved_at),
    )


def _result_record(row: GenerationResultReceiptRow) -> GenerationResultReceipt:
    return GenerationResultReceipt(
        row.result_ref, row.owner_id, row.task_id, row.unit_id, row.operation,
        row.step_id, row.submission_id, row.evidence_id, row.selected_attempt_id,
        row.recoverable_locator, row.content_digest, row.validation_version,
        _as_utc(row.validated_at), _as_utc(row.generation_fulfilled_at), row.late_disposition,
        row.policy_stop_disposition, row.automatic_application_allowed,
    )


def _application_record(row: ApplicationRecordRow) -> ApplicationRecord:
    return ApplicationRecord(
        row.application_ref, row.result_ref, row.owner_id, row.task_id, row.unit_id,
        row.target_scope, row.result_digest, row.delivery_status,
        row.before_target_digest, row.after_target_digest,
        row.media_revision_before, row.media_revision_after,
        row.notes_revision_before, row.notes_revision_after, _as_utc(row.committed_at),
    )


def _attempt_group_record(row: ExecutionAttemptGroupRow) -> ExecutionAttemptGroup:
    steps = tuple(row.attempt_step_ids or ())
    if len(steps) != 3:
        raise ValueError("stored attempt group has an invalid frozen step vector")
    return ExecutionAttemptGroup(
        row.unit_id, row.group_key, row.registry_version, cast(tuple[str, str, str], steps),
        row.original_payload_digest, row.max_retries, row.active_attempt,
        row.selected_evidence_id, row.status, row.poll_used,
        row.auth_unavailable_streak, row.terminal_reason, row.poll_sequence,
        _as_utc(row.next_poll_at), row.outstanding_reservation_ref,
        row.sticky_policy_stop_event_ref, row.active_eligibility_version,
    )


def _work_record(row: LocalContinuationWorkRow) -> LocalContinuationWork:
    return LocalContinuationWork(
        row.work_id, row.owner_id, row.task_id, row.unit_id, row.operation,
        row.policy_version, row.result_ref, row.content_digest,
        _as_utc(row.generation_fulfilled_at), _as_utc(row.original_first_intent_at),
        _as_utc(row.original_deadline_at), cast(tuple[WorkPhase, ...], tuple(row.phase_order or ())),
        row.phase, row.phase_version, row.fee_decision_ref, row.application_ref,
        row.terminal_reason, row.last_execution_generation,
    )


def _stream_fee_record(row: StreamFeeRecordRow) -> StreamFeeRecord:
    return StreamFeeRecord(
        row.server_attempt_id, row.owner_id, row.operation,
        row.frozen_model_provenance, row.quoted_amount, row.disposition,
        row.actual_debited_amount, row.original_debit_log_id,
        row.original_business_key, row.settlement_state, row.start_state,
        row.start_token, row.task_id, row.message_id, _as_utc(row.created_at), _as_utc(row.updated_at),
    )


def _poll_record(row: PollReservationRow) -> PollReservation:
    return PollReservation(
        row.reservation_id, row.group_key, row.sequence, row.attempt_id,
        row.step_id, row.submission_id, row.external_id, row.payload_digest,
        row.committed_poll_count, _as_utc(row.reserved_at), _as_utc(row.timeout_at),
        _as_utc(row.next_poll_at), row.token, row.state, row.observation_ref,
    )


def _policy_stop_record(row: PolicyStopEventRow) -> PolicyStopEvent:
    return PolicyStopEvent(
        row.event_id, row.group_key, row.policy_version, row.reason,
        cast(tuple[str, ...], tuple(row.reservation_refs or ())),
        cast(tuple[str, ...], tuple(row.observation_refs or ())),
        row.active_step_id, row.active_submission_id, row.external_id,
        _as_utc(row.stopped_at), row.financial_disposition, row.original_debit_ref,
        row.refund_ref, row.amount, row.owner_id, row.task_id, row.unit_id,
    )


GenerationRow: TypeAlias = (
    UnitFeeDecisionRow
    | GenerationResultReceiptRow
    | ApplicationRecordRow
    | ExecutionAttemptGroupRow
    | LocalContinuationWorkRow
    | StreamFeeRecordRow
    | PollReservationRow
    | PolicyStopEventRow
)


def _row_from_record(record: GenerationRecord) -> GenerationRow:
    if isinstance(record, UnitFeeDecision):
        return UnitFeeDecisionRow(
            unit_id=record.unit_id, charge_phase=record.charge_phase,
            resolution=record.resolution, ledger_ref=record.ledger_ref,
            generation_result_ref=record.generation_result_ref,
            application_ref=record.application_ref, resolved_at=_to_database_time(record.resolved_at),
        )
    if isinstance(record, GenerationResultReceipt):
        return GenerationResultReceiptRow(
            result_ref=record.result_ref, owner_id=record.owner_id, task_id=record.task_id,
            unit_id=record.unit_id, operation=record.operation, step_id=record.step_id,
            submission_id=record.submission_id, evidence_id=record.evidence_id,
            selected_attempt_id=record.selected_attempt_id,
            recoverable_locator=record.recoverable_locator, content_digest=record.content_digest,
            validation_version=record.validation_version, validated_at=_to_database_time(record.validated_at),
            generation_fulfilled_at=_to_database_time(record.generation_fulfilled_at),
            late_disposition=record.late_disposition,
            policy_stop_disposition=record.policy_stop_disposition,
            automatic_application_allowed=record.automatic_application_allowed,
        )
    if isinstance(record, ApplicationRecord):
        return ApplicationRecordRow(
            application_ref=record.application_ref, result_ref=record.result_ref,
            owner_id=record.owner_id, task_id=record.task_id, unit_id=record.unit_id,
            target_scope=record.target_scope, result_digest=record.result_digest,
            delivery_status=record.delivery_status, before_target_digest=record.before_target_digest,
            after_target_digest=record.after_target_digest,
            media_revision_before=record.media_revision_before,
            media_revision_after=record.media_revision_after,
            notes_revision_before=record.notes_revision_before,
            notes_revision_after=record.notes_revision_after, committed_at=_to_database_time(record.committed_at),
        )
    if isinstance(record, ExecutionAttemptGroup):
        return ExecutionAttemptGroupRow(
            unit_id=record.unit_id, group_key=record.group_key,
            registry_version=record.registry_version, attempt_step_ids=list(record.attempt_step_ids),
            original_payload_digest=record.original_payload_digest, max_retries=record.max_retries,
            active_attempt=record.active_attempt, selected_evidence_id=record.selected_evidence_id,
            status=record.status, poll_used=record.poll_used,
            auth_unavailable_streak=record.auth_unavailable_streak,
            terminal_reason=record.terminal_reason, poll_sequence=record.poll_sequence,
            next_poll_at=_to_database_time(record.next_poll_at),
            outstanding_reservation_ref=record.outstanding_reservation_ref,
            sticky_policy_stop_event_ref=record.sticky_policy_stop_event_ref,
            active_eligibility_version=record.active_eligibility_version,
        )
    if isinstance(record, LocalContinuationWork):
        return _work_row(record)
    if isinstance(record, StreamFeeRecord):
        return _stream_fee_row(record)
    if isinstance(record, PollReservation):
        raise ValueError("poll reservations require explicit unit-scoped reservation")
    if isinstance(record, PolicyStopEvent):
        return _policy_stop_row(record)
    raise TypeError(f"unsupported generation record type: {type(record).__name__}")


def _work_row(record: LocalContinuationWork) -> LocalContinuationWorkRow:
    return LocalContinuationWorkRow(
        work_id=record.work_id, owner_id=record.owner_id, task_id=record.task_id,
        unit_id=record.unit_id, operation=record.operation, policy_version=record.policy_version,
        result_ref=record.result_ref, content_digest=record.content_digest,
        generation_fulfilled_at=_to_database_time(record.generation_fulfilled_at),
        original_first_intent_at=_to_database_time(record.original_first_intent_at),
        original_deadline_at=_to_database_time(record.original_deadline_at), phase_order=list(record.phase_order),
        phase=record.phase, phase_version=record.phase_version,
        fee_decision_ref=record.fee_decision_ref, application_ref=record.application_ref,
        terminal_reason=record.terminal_reason,
        last_execution_generation=record.last_execution_generation,
    )


def _stream_fee_row(record: StreamFeeRecord) -> StreamFeeRecordRow:
    return StreamFeeRecordRow(
        server_attempt_id=record.server_attempt_id, owner_id=record.owner_id,
        operation=record.operation, frozen_model_provenance=record.frozen_model_provenance,
        quoted_amount=record.quoted_amount, disposition=record.disposition,
        actual_debited_amount=record.actual_debited_amount,
        original_debit_log_id=record.original_debit_log_id,
        original_business_key=record.original_business_key,
        settlement_state=record.settlement_state, start_state=record.start_state,
        start_token=record.start_token, task_id=record.task_id, message_id=record.message_id,
        created_at=_to_database_time(record.created_at), updated_at=_to_database_time(record.updated_at),
    )


def _poll_row(record: PollReservation, *, unit_id: str) -> PollReservationRow:
    return PollReservationRow(
        reservation_id=record.reservation_id, unit_id=unit_id,
        group_key=record.group_key, sequence=record.sequence, attempt_id=record.attempt_id,
        step_id=record.step_id, submission_id=record.submission_id,
        external_id=record.external_id, payload_digest=record.payload_digest,
        committed_poll_count=record.committed_poll_count, reserved_at=_to_database_time(record.reserved_at),
        timeout_at=_to_database_time(record.timeout_at), next_poll_at=_to_database_time(record.next_poll_at),
        token=record.token, state=record.state, observation_ref=record.observation_ref,
    )


def _policy_stop_row(record: PolicyStopEvent) -> PolicyStopEventRow:
    return PolicyStopEventRow(
        event_id=record.event_id, unit_id=record.unit_id, group_key=record.group_key,
        policy_version=record.policy_version, reason=record.reason,
        reservation_refs=list(record.reservation_refs), observation_refs=list(record.observation_refs),
        active_step_id=record.active_step_id, active_submission_id=record.active_submission_id,
        external_id=record.external_id, stopped_at=_to_database_time(record.stopped_at),
        financial_disposition=record.financial_disposition,
        original_debit_ref=record.original_debit_ref, refund_ref=record.refund_ref,
        amount=record.amount, owner_id=record.owner_id, task_id=record.task_id,
    )


__all__ = ("GenerationRecord", "GenerationRow", "RecordWriteResult", "GenerationRecordRepository")
