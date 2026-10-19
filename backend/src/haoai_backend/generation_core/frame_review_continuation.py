"""Transactional frame.review delivery, post-apply fee, and final projection."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from threading import Event

from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.records import (
    GenerationResultReceipt,
    LocalContinuationWork,
    UnitFeeDecision,
)
from haoai_backend.generation_contracts.types import (
    ContinuationClaimAuthority,
    TextResult,
    ValidatedGenerationResult,
)
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
    TaskSubmissionRow,
    UserCreditRow,
)
from haoai_backend.generation_core.frame_review_claims import (
    SqlAlchemyFrameReviewClaims,
)
from haoai_backend.generation_core.frame_review_continuation_claims import (
    SqlAlchemyFrameReviewContinuationClaims,
)
from haoai_backend.generation_core.frame_review_results import (
    decode_inline_text_result,
)
from haoai_backend.generation_core.persistence import GenerationRecordRepository
from haoai_backend.generation_core.schema import (
    ApplicationRecordRow,
    GenerationResultReceiptRow,
    LocalContinuationWorkRow,
    UnitFeeDecisionRow,
)
from haoai_backend.generation_contracts.errors import GenerationError
from haoai_backend.generation_contracts.types import (
    TargetSnapshot,
)
from haoai_backend.generation_credentials.domain import (
    CredentialCrypto,
    HmacProofAuthority,
)
from haoai_backend.generation_credentials.frame_review_snapshot_reader import (
    PersistedFrameReviewExecutionBinding,
    VerifiedFrameReviewExecutionInput,
    read_original_frame_review_execution_input,
)
from haoai_backend.series_data.generation import stage_frame_result

SessionFactory = Callable[[], Session]
DbClock = Callable[[], datetime]
IdFactory = Callable[[], str]


class SqlAlchemyFrameReviewContinuationConsumer:
    """Consume one authenticated continuation phase in one caller-owned transaction."""

    def __init__(
        self,
        session_factory: SessionFactory,
        db_clock: DbClock,
        id_factory: IdFactory,
        crypto: CredentialCrypto,
        proof_authority: HmacProofAuthority,
        claims: SqlAlchemyFrameReviewContinuationClaims,
    ) -> None:
        self._session_factory = session_factory
        self._db_clock = db_clock
        self._id_factory = id_factory
        self._crypto = crypto
        self._proof_authority = proof_authority
        self._claims = claims

    def consume(
        self,
        claim: ContinuationClaimAuthority,
        *,
        local_stop_signal: Event | None = None,
    ) -> str:
        if (
            not isinstance(claim, ContinuationClaimAuthority)
            or claim.owner_scope.operation != "frame.review"
        ):
            return "ownership_lost"
        if _signal_is_set(local_stop_signal):
            return "ownership_lost"
        try:
            with self._session_factory() as session:
                with session.begin():
                    now = self._now()
                    graph = self._claims.lock_current_claim(session, claim, now)
                    if graph is None:
                        return "ownership_lost"
                    claim = replace(
                        claim,
                        lease_until=_as_utc(graph["task"]["lease_until"]),
                    )
                    if _signal_is_set(local_stop_signal):
                        return "ownership_lost"
                    phase = graph["work"]["phase"]
                    if phase == "delivery_pending":
                        return self._deliver(
                            session, graph, claim, now, local_stop_signal
                        )
                    if phase == "fee_pending":
                        return self._resolve_fee(
                            session, graph, claim, now, local_stop_signal
                        )
                    if phase == "finalize_pending":
                        return self._finalize(
                            session, graph, claim, now, local_stop_signal
                        )
                    return "pending"
        except Exception:
            # The phase transaction either committed all its durable facts or none.
            return "pending"

    def _deliver(self, session, graph, claim, now, local_stop_signal) -> str:
        receipt = _receipt(graph["receipt"])
        work = _work(graph["work"])
        if not receipt.automatic_application_allowed or receipt.late_disposition:
            return self._stop(
                session,
                work,
                claim,
                "stopped_late_generation",
                "original_generation_deadline_elapsed",
                now,
                local_stop_signal,
            )
        verified = self._reopen(session, graph)
        text = decode_inline_text_result(receipt.recoverable_locator, receipt.content_digest)
        if verified is None or text is None:
            return "pending"
        validated = ValidatedGenerationResult(
            result_ref=receipt.result_ref,
            observation_ref=receipt.evidence_id,
            validation_version=receipt.validation_version,
            recoverable_locator=receipt.recoverable_locator,
            content_digest=receipt.content_digest,
            validated_at=receipt.validated_at,
            generation_fulfilled_at=receipt.generation_fulfilled_at,
            timely_success=True,
            selected_attempt_id=receipt.selected_attempt_id,
            safe_output=TextResult(text),
            automatic_application_allowed=True,
        )
        outcome = stage_frame_result(
            session,
            claim,
            validated,
            verified.snapshot.target_snapshot,
            _local_stop_signal=local_stop_signal,
        )
        if outcome.kind == "applied":
            return "delivered"
        if outcome.kind == "already_applied" and outcome.application_ref:
            updated = _advance_work(
                work,
                phase="fee_pending",
                execution_generation=claim.execution_generation,
                application_ref=outcome.application_ref,
            )
            changed = GenerationRecordRepository(session).advance_continuation_work(
                updated, expected_phase_version=work.phase_version
            )
            if changed.status != "updated":
                raise _ContinuationWriteConflict
            _assert_continuation_projection(
                session,
                claim,
                expected_phase="fee_pending",
                expected_phase_version=work.phase_version + 1,
                expected_status="processing",
                local_stop_signal=local_stop_signal,
            )
            return "delivered"
        return self._stop(
            session,
            work,
            claim,
            "stopped_delivery_failed",
            "frame_review_target_delivery_failed",
            now,
            local_stop_signal,
        )

    def _resolve_fee(self, session, graph, claim, now, local_stop_signal) -> str:
        task, unit, work, receipt, application = (
            graph[key] for key in ("task", "unit", "work", "receipt", "application")
        )
        if (
            application.get("delivery_status") != "applied"
            or application.get("result_ref") != receipt.get("result_ref")
            or work.get("application_ref") != application.get("application_ref")
            or work.get("phase") != "fee_pending"
        ):
            return "pending"

        existing = session.execute(
            select(UnitFeeDecisionRow.__table__)
            .where(UnitFeeDecisionRow.__table__.c.unit_id == unit["id"])
            .with_for_update()
        ).mappings().one_or_none()
        if existing is not None:
            fee = dict(existing)
            if not _fee_matches_applied_result(fee, receipt, application):
                return "pending"
            resolution = fee["resolution"]
            if resolution not in {"charged", "no_charge", "waived_insufficient", "waived_charge_failed"}:
                return "pending"
            return self._advance_to_finalize(
                session, work, claim, fee, now, local_stop_signal
            )

        amount = unit.get("quoted_amount")
        if type(amount) is not int or amount < 0:
            return "pending"
        ledger_id: str | None = None
        resolution: str
        if amount == 0:
            if (
                unit.get("billing_status") != "unbilled"
                or unit.get("debited_amount") != 0
                or unit.get("refunded_amount") != 0
            ):
                return "pending"
            changed = session.execute(
                update(BillingUnitRow.__table__)
                .where(
                    BillingUnitRow.__table__.c.id == unit["id"],
                    BillingUnitRow.__table__.c.task_id == claim.owner_scope.task_id,
                    BillingUnitRow.__table__.c.billing_status == "unbilled",
                    BillingUnitRow.__table__.c.debited_amount == 0,
                    BillingUnitRow.__table__.c.refunded_amount == 0,
                )
                .values(billing_status="no_charge", updated_at=_db_time(now))
            )
            if changed.rowcount != 1:
                raise _ContinuationWriteConflict
            resolution = "no_charge"
            task_billing_status = "no_charge"
        else:
            existing_ledger = session.execute(
                select(CreditLogRow.__table__)
                .where(CreditLogRow.__table__.c.business_key == f"debit:{unit['id']}")
                .with_for_update()
            ).mappings().one_or_none()
            if existing_ledger is not None:
                ledger = dict(existing_ledger)
                if not _ledger_matches_unit(ledger, task, unit, amount):
                    return "pending"
                ledger_id = ledger["id"]
                resolution = "charged"
                task_billing_status = "charged"
            else:
                wallet = session.execute(
                    select(UserCreditRow.__table__)
                    .where(UserCreditRow.__table__.c.user_id == claim.owner_scope.user_id)
                    .with_for_update()
                ).mappings().one_or_none()
                if wallet is None or type(wallet["credits"]) is not int or wallet["credits"] < amount:
                    resolution = "waived_insufficient"
                    task_billing_status = "unbilled"
                else:
                    new_balance = session.execute(
                        update(UserCreditRow.__table__)
                        .where(
                            UserCreditRow.__table__.c.id == wallet["id"],
                            UserCreditRow.__table__.c.user_id == claim.owner_scope.user_id,
                            UserCreditRow.__table__.c.credits == wallet["credits"],
                            UserCreditRow.__table__.c.credits >= amount,
                        )
                        .values(credits=UserCreditRow.__table__.c.credits - amount, updated_at=_db_time(now))
                        .returning(UserCreditRow.__table__.c.credits)
                    ).scalar_one_or_none()
                    if new_balance is None:
                        raise _ContinuationWriteConflict
                    ledger_id = _new_id(self._id_factory)
                    session.execute(
                        insert(CreditLogRow.__table__).values(
                            id=ledger_id,
                            user_id=claim.owner_scope.user_id,
                            amount=-amount,
                            balance_after=new_balance,
                            type="usage",
                            description="AI task ai-review post-apply debit",
                            task_id=claim.owner_scope.task_id,
                            business_key=f"debit:{unit['id']}",
                            billing_unit_id=unit["id"],
                            related_debit_id=None,
                            created_at=_db_time(now),
                        )
                    )
                    changed = session.execute(
                        update(BillingUnitRow.__table__)
                        .where(
                            BillingUnitRow.__table__.c.id == unit["id"],
                            BillingUnitRow.__table__.c.task_id == claim.owner_scope.task_id,
                            BillingUnitRow.__table__.c.billing_status == "unbilled",
                            BillingUnitRow.__table__.c.debited_amount == 0,
                            BillingUnitRow.__table__.c.refunded_amount == 0,
                            BillingUnitRow.__table__.c.quoted_amount == amount,
                        )
                        .values(billing_status="charged", debited_amount=amount, updated_at=_db_time(now))
                    )
                    if changed.rowcount != 1:
                        raise _ContinuationWriteConflict
                    resolution = "charged"
                    task_billing_status = "charged"

        task_changed = session.execute(
            update(AITaskRow.__table__)
            .where(
                AITaskRow.__table__.c.id == task["id"],
                AITaskRow.__table__.c.user_id == task["user_id"],
                AITaskRow.__table__.c.status == "processing",
                AITaskRow.__table__.c.claimed_by == claim.worker_instance,
                AITaskRow.__table__.c.claim_token == claim.token,
                AITaskRow.__table__.c.execution_generation == claim.execution_generation,
                AITaskRow.__table__.c.lease_until == _db_time(claim.lease_until),
                AITaskRow.__table__.c.user_cancelled_at.is_(None),
            )
            .values(billing_status=task_billing_status, updated_at=_db_time(now))
        )
        if task_changed.rowcount != 1:
            raise _ContinuationWriteConflict
        fee_record = UnitFeeDecision(
            unit_id=unit["id"],
            charge_phase="post_apply",
            resolution=resolution,
            ledger_ref=ledger_id,
            generation_result_ref=receipt["result_ref"],
            application_ref=application["application_ref"],
            resolved_at=now,
        )
        staged = GenerationRecordRepository(session).stage_record(fee_record)
        if staged.status != "created":
            raise _ContinuationWriteConflict
        fee = {
            "resolution": resolution,
            "charge_phase": "post_apply",
            "ledger_ref": ledger_id,
            "generation_result_ref": receipt["result_ref"],
            "application_ref": application["application_ref"],
            "resolved_at": _db_time(now),
        }
        return self._advance_to_finalize(
            session, work, claim, fee, now, local_stop_signal
        )

    def _advance_to_finalize(
        self, session, work_row, claim, fee, now, local_stop_signal
    ) -> str:
        if (
            fee.get("charge_phase") != "post_apply"
            or fee.get("generation_result_ref") != claim.result_ref
            or fee.get("application_ref") != work_row.get("application_ref")
            or fee.get("resolved_at") is None
        ):
            return "pending"
        work = _work(work_row)
        updated = _advance_work(
            work,
            phase="finalize_pending",
            fee_decision_ref=work.unit_id,
            execution_generation=claim.execution_generation,
        )
        changed = GenerationRecordRepository(session).advance_continuation_work(
            updated, expected_phase_version=work.phase_version
        )
        if changed.status != "updated":
            raise _ContinuationWriteConflict
        _assert_continuation_projection(
            session,
            claim,
            expected_phase="finalize_pending",
            expected_phase_version=work.phase_version + 1,
            expected_status="processing",
            local_stop_signal=local_stop_signal,
        )
        return "fee_resolved"

    def _finalize(self, session, graph, claim, now, local_stop_signal) -> str:
        task, unit, work_row, receipt_row, fee, application = (
            graph[key]
            for key in ("task", "unit", "work", "receipt", "fee", "application")
        )
        if not _fee_matches_applied_result(fee, receipt_row, application):
            return "pending"
        receipt = _receipt(receipt_row)
        text = decode_inline_text_result(receipt.recoverable_locator, receipt.content_digest)
        verified = self._reopen(session, graph)
        if text is None or verified is None:
            return "pending"
        target = verified.snapshot.target_snapshot
        if not target.chapter_id or target.compat_frame_index is None:
            return "pending"
        result = json.dumps(
            {
                "chapter_id": target.chapter_id,
                "frame_index": target.compat_frame_index,
                "prompt_id": verified.request.get("prompt_id"),
                "optimized_text": text,
                "original_text": verified.original_text,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        task_table = AITaskRow.__table__
        changed = session.execute(
            update(task_table)
            .where(
                task_table.c.id == claim.owner_scope.task_id,
                task_table.c.user_id == claim.owner_scope.user_id,
                task_table.c.type == "ai-review",
                task_table.c.status == "processing",
                task_table.c.claimed_by == claim.worker_instance,
                task_table.c.claim_token == claim.token,
                task_table.c.execution_generation == claim.execution_generation,
                task_table.c.user_cancelled_at.is_(None),
                task_table.c.lease_until == _db_time(claim.lease_until),
                task_table.c.lease_until > _db_time(now),
            )
            .values(
                result=result,
                status="completed",
                progress=100,
                progress_message="分镜评审完成",
                credit_cost=unit["debited_amount"],
                billing_status=unit["billing_status"],
                updated_at=_db_time(now),
            )
        )
        if changed.rowcount != 1:
            return "ownership_lost"
        work = _work(work_row)
        updated = _advance_work(
            work,
            phase="done",
            execution_generation=claim.execution_generation,
        )
        staged = GenerationRecordRepository(session).advance_continuation_work(
            updated, expected_phase_version=work.phase_version
        )
        if staged.status != "updated":
            raise _ContinuationWriteConflict
        _assert_continuation_projection(
            session,
            claim,
            expected_phase="done",
            expected_phase_version=work.phase_version + 1,
            expected_status="completed",
            local_stop_signal=local_stop_signal,
        )
        return "finalized"

    def _stop(
        self,
        session,
        work: LocalContinuationWork,
        claim,
        phase,
        reason,
        now,
        local_stop_signal=None,
    ) -> str:
        updated = _advance_work(
            work,
            phase=phase,
            terminal_reason=reason,
            execution_generation=claim.execution_generation,
        )
        staged = GenerationRecordRepository(session).advance_continuation_work(
            updated, expected_phase_version=work.phase_version
        )
        if staged.status != "updated":
            raise _ContinuationWriteConflict
        changed = session.execute(
            update(AITaskRow.__table__)
            .where(
                AITaskRow.__table__.c.id == claim.owner_scope.task_id,
                AITaskRow.__table__.c.user_id == claim.owner_scope.user_id,
                AITaskRow.__table__.c.type == "ai-review",
                AITaskRow.__table__.c.status == "processing",
                AITaskRow.__table__.c.claimed_by == claim.worker_instance,
                AITaskRow.__table__.c.claim_token == claim.token,
                AITaskRow.__table__.c.execution_generation == claim.execution_generation,
                AITaskRow.__table__.c.lease_until == _db_time(claim.lease_until),
                AITaskRow.__table__.c.user_cancelled_at.is_(None),
            )
            .values(status="failed", progress=100, progress_message=reason, updated_at=_db_time(now))
        )
        if changed.rowcount != 1:
            raise _ContinuationWriteConflict
        _assert_continuation_projection(
            session,
            claim,
            expected_phase=phase,
            expected_phase_version=work.phase_version + 1,
            expected_status="failed",
            local_stop_signal=local_stop_signal,
        )
        return "finalized"

    def _reopen(self, session, graph) -> VerifiedFrameReviewExecutionInput | None:
        task = graph["task"]
        unit = graph["unit"]
        step = graph["step"]
        receipt = graph["receipt"]
        external = graph["external"]
        try:
            request_data = json.loads(task["request_data"])
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if (
            not isinstance(request_data, dict)
            or not isinstance(request_data.get("submission_id"), str)
            or receipt.get("step_id") != step.get("id")
            or receipt.get("submission_id") != external.get("id")
            or external.get("step_id") != step.get("id")
        ):
            return None
        parent_submission_id = request_data["submission_id"]
        parent_row = session.execute(
            select(TaskSubmissionRow.__table__)
            .where(
                TaskSubmissionRow.__table__.c.id == parent_submission_id,
                TaskSubmissionRow.__table__.c.user_id == task["user_id"],
                TaskSubmissionRow.__table__.c.operation == "frame.review",
            )
            .with_for_update()
        ).mappings().one_or_none()
        try:
            snapshot = step["input_snapshot"]
            if parent_row is None or not isinstance(request_data, dict) or not isinstance(snapshot, dict):
                return None
            binding = PersistedFrameReviewExecutionBinding(
                owner_id=task["user_id"],
                task_id=task["id"],
                unit_id=unit["id"],
                step_id=step["id"],
                submission_id=parent_submission_id,
                raw_key=parent_row["idempotency_key"],
                task_message_id=task.get("message_id"),
                task_model_name=task["model_name"],
                task_model_id=request_data["model_id"],
                task_model_source=request_data["model_source"],
                task_model_owner_id=request_data["model_owner_id"],
                request_digest=request_data["request_digest"],
                rule_digest=request_data["rule_digest"],
                snapshot_digest=request_data["snapshot_digest"],
                manifest_snapshot_digest=request_data["manifest_snapshot_digest"],
                server_attempt_id=snapshot["server_attempt_id"],
                prepared_payload_id=snapshot["prepared_payload_id"],
                protected_ref=snapshot["protected_ref"],
                quoted_amount=unit["quoted_amount"],
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        return read_original_frame_review_execution_input(
            session,
            binding,
            crypto=self._crypto,
            proof_authority=self._proof_authority,
        )

    def _now(self) -> datetime:
        value = self._db_clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("continuation clock must return an aware timestamp")
        return value.astimezone(timezone.utc)


def _advance_work(
    work: LocalContinuationWork,
    *,
    phase: str,
    execution_generation: int,
    fee_decision_ref: str | None = None,
    application_ref: str | None = None,
    terminal_reason: str | None = None,
) -> LocalContinuationWork:
    return replace(
        work,
        phase=phase,
        phase_version=work.phase_version + 1,
        fee_decision_ref=(work.fee_decision_ref if fee_decision_ref is None else fee_decision_ref),
        application_ref=(work.application_ref if application_ref is None else application_ref),
        terminal_reason=terminal_reason,
        last_execution_generation=execution_generation,
    )


def _receipt(row) -> GenerationResultReceipt:
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
        validated_at=_as_utc(row["validated_at"]),
        generation_fulfilled_at=_as_utc(row["generation_fulfilled_at"]),
        late_disposition=row["late_disposition"],
        policy_stop_disposition=row["policy_stop_disposition"],
        automatic_application_allowed=row["automatic_application_allowed"],
    )


def _work(row) -> LocalContinuationWork:
    return LocalContinuationWork(
        work_id=row["work_id"],
        owner_id=row["owner_id"],
        task_id=row["task_id"],
        unit_id=row["unit_id"],
        operation=row["operation"],
        policy_version=row["policy_version"],
        result_ref=row["result_ref"],
        content_digest=row["content_digest"],
        generation_fulfilled_at=_as_utc(row["generation_fulfilled_at"]),
        original_first_intent_at=_as_utc(row["original_first_intent_at"]),
        original_deadline_at=_as_utc(row["original_deadline_at"]),
        phase_order=tuple(row["phase_order"]),
        phase=row["phase"],
        phase_version=row["phase_version"],
        fee_decision_ref=row["fee_decision_ref"],
        application_ref=row["application_ref"],
        terminal_reason=row["terminal_reason"],
        last_execution_generation=row["last_execution_generation"],
    )


def _fee_matches_applied_result(fee, receipt, application) -> bool:
    return bool(
        fee.get("charge_phase") == "post_apply"
        and fee.get("generation_result_ref") == receipt.get("result_ref")
        and fee.get("application_ref") == application.get("application_ref")
        and application.get("result_ref") == receipt.get("result_ref")
        and application.get("delivery_status") == "applied"
        and fee.get("resolved_at") is not None
        and fee.get("resolution") in {"charged", "no_charge", "waived_insufficient", "waived_charge_failed"}
    )


def _ledger_matches_unit(ledger, task, unit, amount: int) -> bool:
    return bool(
        ledger.get("user_id") == task.get("user_id")
        and ledger.get("task_id") == task.get("id")
        and ledger.get("billing_unit_id") == unit.get("id")
        and ledger.get("type") == "usage"
        and ledger.get("amount") == -amount
        and ledger.get("business_key") == f"debit:{unit.get('id')}"
        and unit.get("billing_status") == "charged"
        and unit.get("debited_amount") == amount
        and unit.get("refunded_amount") == 0
    )


def _new_id(id_factory: IdFactory) -> str:
    value = id_factory()
    if not isinstance(value, str) or not value or len(value) > 36 or value != value.strip():
        raise ValueError("server ID factory returned an invalid ledger ID")
    return value


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _same_instant(left: object, right: object) -> bool:
    if not isinstance(left, datetime) or not isinstance(right, datetime):
        return False
    return _as_utc(left) == _as_utc(right)


def _db_time(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _fresh_database_time(session: Session) -> datetime:
    statement = (
        select(func.clock_timestamp())
        if session.get_bind().dialect.name == "postgresql"
        else select(func.current_timestamp())
    )
    value = session.execute(statement).scalar_one()
    if not isinstance(value, datetime):
        raise ValueError("database clock returned an invalid timestamp")
    return _as_utc(value)


def _signal_is_set(signal: Event | None) -> bool:
    return signal is not None and signal.is_set()


def _assert_continuation_projection(
    session: Session,
    claim: ContinuationClaimAuthority,
    *,
    expected_phase: str,
    expected_phase_version: int,
    expected_status: str,
    local_stop_signal: Event | None,
) -> None:
    task_table = AITaskRow.__table__
    task_row = session.execute(
        select(task_table)
        .where(
            task_table.c.id == claim.owner_scope.task_id,
            task_table.c.user_id == claim.owner_scope.user_id,
            task_table.c.type == "ai-review",
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).mappings().one_or_none()
    work_table = LocalContinuationWorkRow.__table__
    work_row = session.execute(
        select(work_table)
        .where(
            work_table.c.work_id == claim.work_id,
            work_table.c.owner_id == claim.owner_scope.user_id,
            work_table.c.task_id == claim.owner_scope.task_id,
            work_table.c.unit_id == claim.unit_id,
            work_table.c.result_ref == claim.result_ref,
            work_table.c.operation == "frame.review",
            work_table.c.policy_version == "local_continuation_v1",
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).mappings().one_or_none()
    now = _fresh_database_time(session)
    if (
        _signal_is_set(local_stop_signal)
        or task_row is None
        or work_row is None
        or task_row["status"] != expected_status
        or task_row["claimed_by"] != claim.worker_instance
        or task_row["claim_token"] != claim.token
        or task_row["execution_generation"] != claim.execution_generation
        or task_row["lease_until"] is None
        or not _same_instant(task_row["lease_until"], claim.lease_until)
        or _as_utc(task_row["lease_until"]) <= now
        or task_row["user_cancelled_at"] is not None
        or work_row["phase"] != expected_phase
        or work_row["phase_version"] != expected_phase_version
        or work_row["last_execution_generation"] != claim.execution_generation
        or tuple(work_row["phase_order"] or ())
        != ("delivery_pending", "fee_pending", "finalize_pending")
    ):
        raise _ContinuationWriteConflict


class _ContinuationWriteConflict(RuntimeError):
    """Rollback a multi-row continuation projection when its final CAS loses."""


__all__ = ("SqlAlchemyFrameReviewContinuationConsumer",)
