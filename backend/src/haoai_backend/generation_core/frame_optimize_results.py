"""Persist authenticated frame.optimize text results before per-Unit fees."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.records import (
    GenerationResultReceipt,
    LocalContinuationWork,
    UnitFeeDecision,
)
from haoai_backend.generation_contracts.types import ClaimAuthority, CommittedIntent, TextResult
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
    ResultEvidenceRow,
)
from haoai_backend.generation_core.frame_optimize_execution import (
    LockedFrameOptimizeBatch,
    intent_row_matches,
    lock_frame_optimize_batch,
)
from haoai_backend.generation_core.frame_optimize_progress import (
    _PHASE_ORDER,
    _POLICY_VERSION,
    decode_optimize_text,
    encode_optimize_text,
    lock_frame_optimize_progress,
)
from haoai_backend.generation_core.persistence import GenerationRecordRepository
from haoai_backend.generation_core.schema import (
    GenerationResultReceiptRow,
    LocalContinuationWorkRow,
    UnitFeeDecisionRow,
)
from haoai_backend.generation_credentials.domain import CredentialCrypto, HmacProofAuthority
from haoai_backend.generation_credentials.frame_optimize_snapshot_reader import (
    VerifiedFrameOptimizeExecutionBatch,
    VerifiedFrameOptimizeExecutionItem,
)

_OPERATION = "frame.optimize"
_TASK_TYPE = "batch-optimize"
_EVIDENCE_PREFIX = "frame-optimize-result-v1:"
_VALIDATION_VERSION = "frame-optimize-text-v1"
_DEADLINE_SECONDS = 7200

SessionFactory = Callable[[], Session]
DbClock = Callable[[], datetime]
IdFactory = Callable[[], str]


@dataclass(frozen=True, slots=True)
class _StagedResult:
    receipt: GenerationResultReceipt
    work: LocalContinuationWork
    evidence_id: str
    encoded_text: str
    evidence_key: str


@dataclass(slots=True)
class SqlAlchemyFrameOptimizeResults:
    """Atomically persist one validated result, receipt, and fee-phase Work."""

    session_factory: SessionFactory
    db_clock: DbClock
    crypto: CredentialCrypto
    proof_authority: HmacProofAuthority
    id_factory: IdFactory = field(default=lambda: secrets.token_hex(18))

    def __post_init__(self) -> None:
        if not callable(self.session_factory) or not callable(self.db_clock):
            raise TypeError("result session and database clock must be explicit")
        if not callable(self.id_factory):
            raise TypeError("result id factory must be callable")

    def persist_success(
        self,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified_batch: VerifiedFrameOptimizeExecutionBatch,
        verified_item: VerifiedFrameOptimizeExecutionItem,
        result: TextResult,
    ) -> GenerationResultReceipt | Literal["pending", "ownership_lost"]:
        encoded = _validate_call(claim, intent, verified_batch, verified_item, result)
        if encoded is None:
            return "pending"
        digest, locator = encoded
        staged: _StagedResult | None = None
        outcome: Literal["pending", "ownership_lost"] = "pending"
        try:
            with self.session_factory() as session:
                with session.begin():
                    graph = self._lock_graph(session, claim)
                    now = self._now()
                    if graph is None or now is None:
                        return "pending"
                    progress = lock_frame_optimize_progress(session, graph)
                    if progress is None:
                        return "pending"
                    existing = progress.receipts_by_unit.get(verified_item.unit_id)
                    if existing is not None:
                        return _matching_receipt(existing, intent, verified_item, digest, locator)
                    if not _claim_is_live(graph.task, claim, now):
                        return "ownership_lost"
                    unit = graph.units_by_id.get(verified_item.unit_id)
                    step = graph.steps_by_unit.get(verified_item.unit_id)
                    external = graph.external_by_step.get(intent.step_id)
                    if (
                        unit is None
                        or step is None
                        or external is None
                        or not _intent_matches(graph, claim, intent, verified_item, unit, step, external)
                        or unit.get("result_status") != "pending"
                        or unit.get("billing_status") != "unbilled"
                        or unit.get("debited_amount") != 0
                        or unit.get("refunded_amount") != 0
                        or step.get("execution_status") not in {"submitting", "submitted", "reconciling"}
                        or external.get("submission_status") not in {"submitting", "submitted", "unknown"}
                        or unit.get("user_cancelled_at") is not None
                    ):
                        return "pending"

                    fulfilled_at = self._now()
                    if (
                        fulfilled_at is None
                        or not _claim_is_live(graph.task, claim, fulfilled_at)
                        or not _confirm_claim_fence(session, graph.task, claim, fulfilled_at)
                    ):
                        return "ownership_lost"
                    if graph.verified != verified_batch:
                        return "pending"
                    reopened_item = graph.items_by_unit.get(verified_item.unit_id)
                    if reopened_item != verified_item:
                        return "pending"

                    deadline = _utc(intent.deadline_at)
                    timely = deadline is not None and fulfilled_at < deadline
                    stopped = progress.stopped_insufficient
                    late = not timely
                    final_resolution = (
                        "stopped_late_generation"
                        if late
                        else "stopped_insufficient"
                        if stopped
                        else None
                    )
                    result_ref, evidence_id, work_id = self._new_ids()
                    if result_ref is None or evidence_id is None or work_id is None:
                        return "pending"

                    receipt = GenerationResultReceipt(
                        result_ref=result_ref,
                        owner_id=claim.scope.user_id,
                        task_id=claim.scope.task_id,
                        unit_id=verified_item.unit_id,
                        operation=_OPERATION,
                        step_id=intent.step_id,
                        submission_id=intent.external_submission_id,
                        evidence_id=evidence_id,
                        selected_attempt_id=verified_item.item_id,
                        recoverable_locator=locator,
                        content_digest=digest,
                        validation_version=_VALIDATION_VERSION,
                        validated_at=fulfilled_at,
                        generation_fulfilled_at=fulfilled_at,
                        late_disposition="stopped_late_generation" if late else None,
                        policy_stop_disposition="stopped_insufficient" if stopped and not late else None,
                        automatic_application_allowed=timely and not stopped,
                    )
                    work = LocalContinuationWork(
                        work_id=work_id,
                        owner_id=claim.scope.user_id,
                        task_id=claim.scope.task_id,
                        unit_id=verified_item.unit_id,
                        operation=_OPERATION,
                        policy_version=_POLICY_VERSION,
                        result_ref=result_ref,
                        content_digest=digest,
                        generation_fulfilled_at=fulfilled_at,
                        original_first_intent_at=_utc(intent.first_intent_at),
                        original_deadline_at=deadline,
                        phase_order=tuple(_PHASE_ORDER),
                        phase="fee_pending",
                        phase_version=0,
                        fee_decision_ref=None,
                        application_ref=None,
                        terminal_reason=None,
                        last_execution_generation=claim.execution_generation,
                    )
                    key = _EVIDENCE_PREFIX + intent.external_submission_id
                    staged = _StagedResult(receipt, work, evidence_id, locator, key)
                    session.execute(
                        insert(ResultEvidenceRow.__table__).values(
                            id=evidence_id,
                            step_id=intent.step_id,
                            submission_id=intent.external_submission_id,
                            evidence_key=key,
                            source="provider",
                            evidence_kind="provider_text",
                            payload_digest=digest,
                            result_location=locator,
                            result_digest=digest,
                            received_at=_db_time(fulfilled_at),
                            validity="valid",
                            application_status="pending",
                            applied_at=None,
                        )
                    )
                    if not self._fulfill_unit(session, unit, intent, fulfilled_at):
                        outcome = "ownership_lost"
                        raise _ResultWriteConflict
                    if not self._observe_step(session, step, intent, fulfilled_at):
                        raise _ResultWriteConflict
                    if not self._complete_submission(session, external, intent, fulfilled_at):
                        raise _ResultWriteConflict
                    records = GenerationRecordRepository(session)
                    if records.stage_record(receipt).status != "created":
                        raise _ResultWriteConflict
                    if records.stage_record(work).status != "created":
                        raise _ResultWriteConflict
                    if final_resolution is not None:
                        decision = UnitFeeDecision(
                            unit_id=verified_item.unit_id,
                            charge_phase="pre_apply",
                            resolution=final_resolution,
                            ledger_ref=None,
                            generation_result_ref=result_ref,
                            application_ref=None,
                            resolved_at=fulfilled_at,
                        )
                        if records.stage_record(decision).status != "created":
                            raise _ResultWriteConflict
                        terminal_work = replace(
                            work,
                            phase=final_resolution,
                            phase_version=1,
                            fee_decision_ref=verified_item.unit_id,
                            terminal_reason=(
                                "original_generation_deadline_elapsed"
                                if late
                                else "insufficient_credits"
                            ),
                        )
                        if records.advance_continuation_work(
                            terminal_work, expected_phase_version=0
                        ).status != "updated":
                            raise _ResultWriteConflict
        except _ResultWriteConflict:
            return outcome
        except Exception:
            pass
        if staged is None:
            return outcome
        return self._confirm_persisted(claim, intent, verified_item, digest, locator, staged)

    def _lock_graph(self, session: Session, claim: ClaimAuthority) -> LockedFrameOptimizeBatch | None:
        return lock_frame_optimize_batch(
            session,
            claim.scope.user_id,
            claim.scope.task_id,
            crypto=self.crypto,
            proof_authority=self.proof_authority,
        )

    def _fulfill_unit(self, session, unit, intent, now) -> bool:
        table = BillingUnitRow.__table__
        changed = session.execute(
            update(table)
            .where(
                table.c.id == unit["id"],
                table.c.task_id == unit["task_id"],
                table.c.operation == _OPERATION,
                table.c.step_graph_version == "frame-optimize-v1",
                table.c.input_digest == intent.request_digest,
                table.c.result_status == "pending",
                table.c.billing_status == "unbilled",
                table.c.debited_amount == 0,
                table.c.refunded_amount == 0,
                table.c.user_cancelled_at.is_(None),
                table.c.success_confirmed_at.is_(None),
                table.c.first_intent_at == _db_time(intent.first_intent_at),
                table.c.deadline_at == _db_time(intent.deadline_at),
            )
            .values(
                result_status="succeeded",
                success_confirmed_at=_db_time(now),
                updated_at=_db_time(now),
            )
        )
        return changed.rowcount == 1

    def _observe_step(self, session, step, intent, now) -> bool:
        changed = session.execute(
            update(ExecutionStepRow.__table__)
            .where(
                ExecutionStepRow.__table__.c.id == intent.step_id,
                ExecutionStepRow.__table__.c.billing_unit_id == step["billing_unit_id"],
                ExecutionStepRow.__table__.c.execution_status == step["execution_status"],
                ExecutionStepRow.__table__.c.input_digest == intent.request_digest,
            )
            .values(execution_status="result_observed", updated_at=_db_time(now))
        )
        return changed.rowcount == 1

    def _complete_submission(self, session, external, intent, now) -> bool:
        table = ExternalSubmissionRow.__table__
        changed = session.execute(
            update(table)
            .where(
                table.c.id == intent.external_submission_id,
                table.c.step_id == intent.step_id,
                table.c.submission_status == external["submission_status"],
                table.c.intent_at == _db_time(intent.first_intent_at),
                table.c.deadline_at == _db_time(intent.deadline_at),
            )
            .values(
                submission_status="completed",
                submitted_at=external.get("submitted_at") or _db_time(now),
                updated_at=_db_time(now),
            )
        )
        return changed.rowcount == 1

    def _confirm_persisted(self, claim, intent, verified_item, digest, locator, staged):
        try:
            with self.session_factory() as session:
                with session.begin():
                    graph = self._lock_graph(session, claim)
                    if graph is None:
                        return "pending"
                    progress = lock_frame_optimize_progress(session, graph)
                    if progress is None:
                        return "pending"
                    row = progress.receipts_by_unit.get(verified_item.unit_id)
                    if row is None:
                        return "pending"
                    receipt = _matching_receipt(row, intent, verified_item, digest, locator)
                    if not isinstance(receipt, GenerationResultReceipt):
                        return "pending"
                    evidence = progress.evidence_by_unit.get(verified_item.unit_id)
                    work = progress.works_by_unit.get(verified_item.unit_id)
                    if (
                        receipt != staged.receipt
                        or evidence is None
                        or evidence.get("id") != staged.evidence_id
                        or evidence.get("evidence_key") != staged.evidence_key
                        or evidence.get("result_location") != staged.encoded_text
                        or work is None
                        or work.get("work_id") != staged.work.work_id
                    ):
                        return "pending"
                    reopened_item = graph.items_by_unit.get(verified_item.unit_id)
                    if reopened_item != verified_item:
                        return "pending"
                    return receipt
        except Exception:
            return "pending"

    def _new_ids(self) -> tuple[str | None, str | None, str | None]:
        values: list[str] = []
        for _ in range(3):
            try:
                value = self.id_factory()
            except Exception:
                return None, None, None
            if not isinstance(value, str) or not value or len(value) > 36:
                return None, None, None
            values.append(value)
        return tuple(values) if len(set(values)) == 3 else (None, None, None)

    def _now(self) -> datetime | None:
        try:
            value = self.db_clock()
        except Exception:
            return None
        return _utc(value)


def _validate_call(claim, intent, batch, item, result):
    if (
        not isinstance(claim, ClaimAuthority)
        or claim.scope.operation != _OPERATION
        or claim.local_lost_signal.is_set()
        or type(intent) is not CommittedIntent
        or type(batch) is not VerifiedFrameOptimizeExecutionBatch
        or type(item) is not VerifiedFrameOptimizeExecutionItem
        or type(result) is not TextResult
        or batch.task_id != claim.scope.task_id
        or batch.request_digest != intent.request_digest
        or item not in batch.items
    ):
        return None
    encoded = encode_optimize_text(result.text)
    return encoded


def _intent_matches(graph, claim, intent, item, unit, step, external) -> bool:
    try:
        first = _utc(intent.first_intent_at)
        deadline = _utc(intent.deadline_at)
    except (TypeError, ValueError):
        return False
    return bool(
        first is not None
        and deadline == first + timedelta(seconds=_DEADLINE_SECONDS)
        and intent.committed_at == intent.first_intent_at
        and intent.provider_idempotency_key is None
        and intent.step_id == step.get("id") == item.step_id
        and intent.attempt_id == item.item_id
        and intent.external_submission_id == external.get("id")
        and intent.request_digest == graph.binding.request_digest
        and intent.provider == item.snapshot.model_ref.provider
        and intent.model_id == item.snapshot.model_ref.model_id
        and external.get("intent_at") is not None
        and _same_time(external.get("intent_at"), first)
        and _same_time(external.get("deadline_at"), deadline)
        and intent_row_matches(external, graph, unit, step)
        and item.unit_id == unit.get("id")
        and not claim.local_lost_signal.is_set()
    )


def _claim_is_live(task, claim, now: datetime) -> bool:
    lease = _utc(task.get("lease_until"))
    return bool(
        task.get("id") == claim.scope.task_id
        and task.get("user_id") == claim.scope.user_id
        and task.get("type") == _TASK_TYPE
        and task.get("status") == "processing"
        and task.get("claimed_by") == claim.worker_instance
        and task.get("claim_token") == claim.token
        and task.get("execution_generation") == claim.execution_generation
        and task.get("user_cancelled_at") is None
        and lease is not None
        and lease > now
        and not claim.local_lost_signal.is_set()
    )


def _confirm_claim_fence(session, task, claim, now) -> bool:
    return _claim_is_live(task, claim, now)


def _matching_receipt(row, intent, item, digest, locator):
    if (
        row.get("submission_id") != intent.external_submission_id
        or row.get("step_id") != intent.step_id
        or row.get("unit_id") != item.unit_id
        or row.get("selected_attempt_id") != item.item_id
        or row.get("content_digest") != digest
        or row.get("recoverable_locator") != locator
        or row.get("validation_version") != _VALIDATION_VERSION
    ):
        return "pending"
    return _receipt_from_row(row)


def _receipt_from_row(row):
    return GenerationResultReceipt(
        result_ref=row["result_ref"],
        owner_id=row["owner_id"],
        task_id=row["task_id"],
        unit_id=row["unit_id"],
        operation=row["operation"],
        step_id=row["step_id"],
        submission_id=row["submission_id"],
        evidence_id=row["evidence_id"],
        selected_attempt_id=row["selected_attempt_id"],
        recoverable_locator=row["recoverable_locator"],
        content_digest=row["content_digest"],
        validation_version=row["validation_version"],
        validated_at=_utc(row["validated_at"]),
        generation_fulfilled_at=_utc(row["generation_fulfilled_at"]),
        late_disposition=row["late_disposition"],
        policy_stop_disposition=row["policy_stop_disposition"],
        automatic_application_allowed=row["automatic_application_allowed"],
    )


class _ResultWriteConflict(RuntimeError):
    pass


def _utc(value):
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _db_time(value: datetime) -> datetime:
    return _utc(value).replace(tzinfo=None)


def _same_time(left, right) -> bool:
    return _utc(left) is not None and _utc(left) == _utc(right)


__all__ = ["SqlAlchemyFrameOptimizeResults"]
