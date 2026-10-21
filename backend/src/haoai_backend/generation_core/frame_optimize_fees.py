"""Resolve one verified frame.optimize result's pre-apply fee atomically."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.records import LocalContinuationWork, UnitFeeDecision
from haoai_backend.generation_contracts.types import ClaimAuthority, ContinuationClaimAuthority
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    UserCreditRow,
)
from haoai_backend.generation_core.frame_optimize_continuation_claims import (
    SqlAlchemyFrameOptimizeContinuationClaims,
)
from haoai_backend.generation_core.frame_optimize_progress import (
    _PHASE_ORDER,
    lock_frame_optimize_progress,
)
from haoai_backend.generation_core.persistence import GenerationRecordRepository
from haoai_backend.generation_core.schema import LocalContinuationWorkRow, UnitFeeDecisionRow

_OPERATION = "frame.optimize"
_TASK_TYPE = "batch-optimize"

SessionFactory = Callable[[], Session]
DbClock = Callable[[], datetime]
IdFactory = Callable[[], str]
FeeDisposition = Literal[
    "charged", "no_charge", "stopped_insufficient", "existing", "pending", "ownership_lost"
]


@dataclass(frozen=True, slots=True)
class FrameOptimizeFeeOutcome:
    disposition: FeeDisposition
    unit_id: str
    result_ref: str | None = None
    ledger_ref: str | None = None
    amount: int = 0


@dataclass(frozen=True, slots=True)
class _FeeWrite:
    owner_id: str
    task_id: str
    unit_id: str
    work_id: str
    result_ref: str
    resolution: str
    ledger_ref: str | None
    amount: int
    balance_after: int | None
    resolved_at: datetime
    work_phase: str
    work_phase_version: int


@dataclass(slots=True)
class SqlAlchemyFrameOptimizeFees:
    """Charge only a result already authenticated by the whole-batch reader."""

    session_factory: SessionFactory
    db_clock: DbClock
    crypto: object
    proof_authority: object
    continuation_claims: SqlAlchemyFrameOptimizeContinuationClaims
    id_factory: IdFactory = lambda: secrets.token_hex(18)

    def __post_init__(self) -> None:
        if not callable(self.session_factory) or not callable(self.db_clock):
            raise TypeError("fee session and database clock must be explicit")
        if not callable(self.id_factory):
            raise TypeError("fee id factory must be callable")
        if not isinstance(self.continuation_claims, SqlAlchemyFrameOptimizeContinuationClaims):
            raise TypeError("optimize continuation claims are required")

    def consume_fee(self, claim: ContinuationClaimAuthority) -> FrameOptimizeFeeOutcome:
        if not isinstance(claim, ContinuationClaimAuthority):
            return FrameOptimizeFeeOutcome("ownership_lost", "")
        staged: list[_FeeWrite] = []
        outcome = "pending"
        try:
            with self.session_factory() as session:
                with session.begin():
                    now = self._now()
                    if now is None:
                        return _outcome("pending", claim.unit_id, claim.result_ref)
                    locked = self.continuation_claims.lock_current_claim(session, claim, now)
                    if locked is None:
                        return _outcome("ownership_lost", claim.unit_id, claim.result_ref)
                    existing = locked.fee
                    if existing is not None:
                        return _outcome("existing", claim.unit_id, claim.result_ref, existing)
                    stage_now = self._now()
                    if stage_now is None:
                        return _outcome("pending", claim.unit_id, claim.result_ref)
                    write = self._stage_fee(session, locked, claim, stage_now)
                    if isinstance(write, str):
                        outcome = write
                    else:
                        staged.append(write)
                        outcome = write.resolution
        except Exception:
            outcome = "pending"
        if staged:
            return self._confirm_fee(staged[0])
        return _outcome(outcome, claim.unit_id, claim.result_ref)

    def resolve_current_fee(
        self, generation_claim: ClaimAuthority, work_id: str
    ) -> FrameOptimizeFeeOutcome:
        if not isinstance(generation_claim, ClaimAuthority):
            return FrameOptimizeFeeOutcome("ownership_lost", "")
        staged: list[_FeeWrite] = []
        outcome = "pending"
        unit_id = ""
        result_ref = None
        try:
            with self.session_factory() as session:
                with session.begin():
                    continuation = self.continuation_claims.bind_current_continuation(
                        session, generation_claim, work_id
                    )
                    if continuation is None:
                        return _outcome("ownership_lost", "")
                    unit_id = continuation.unit_id
                    result_ref = continuation.result_ref
                    now = self._now()
                    if now is None:
                        return _outcome("pending", continuation.unit_id, continuation.result_ref)
                    locked = self.continuation_claims.lock_current_claim(session, continuation, now)
                    if locked is None:
                        return _outcome("ownership_lost", continuation.unit_id, continuation.result_ref)
                    if locked.fee is not None:
                        return _outcome("existing", continuation.unit_id, continuation.result_ref, locked.fee)
                    stage_now = self._now()
                    if stage_now is None:
                        return _outcome("pending", continuation.unit_id, continuation.result_ref)
                    write = self._stage_fee(session, locked, continuation, stage_now)
                    if isinstance(write, str):
                        outcome = write
                    else:
                        staged.append(write)
                        outcome = write.resolution
        except Exception:
            outcome = "pending"
        if staged:
            return self._confirm_fee(staged[0])
        return _outcome(outcome, unit_id, result_ref)

    def _stage_fee(self, session, locked, claim, now):
        work = locked.work
        unit = locked.unit
        receipt = locked.receipt
        if (
            work.get("phase") != "fee_pending"
            or work.get("phase_version") != claim.phase_version
            or work.get("work_id") != claim.work_id
            or work.get("result_ref") != claim.result_ref
            or receipt.get("result_ref") != claim.result_ref
            or not receipt.get("automatic_application_allowed")
            or receipt.get("late_disposition") is not None
        ):
            return "pending"
        amount = unit.get("quoted_amount")
        if type(amount) is not int or amount < 0:
            return "pending"

        ledger_id: str | None = None
        balance_after: int | None = None
        resolution: str
        if amount == 0:
            resolution = "no_charge"
        else:
            wallet = session.execute(
                select(UserCreditRow.__table__)
                .where(UserCreditRow.__table__.c.user_id == claim.owner_scope.user_id)
                .with_for_update()
            ).mappings().one_or_none()
            if wallet is None or type(wallet.get("credits")) is not int or wallet["credits"] < amount:
                resolution = "stopped_insufficient"
            else:
                balance_after = wallet["credits"] - amount
                changed = session.execute(
                    update(UserCreditRow.__table__)
                    .where(
                        UserCreditRow.__table__.c.id == wallet["id"],
                        UserCreditRow.__table__.c.user_id == claim.owner_scope.user_id,
                        UserCreditRow.__table__.c.credits == wallet["credits"],
                        UserCreditRow.__table__.c.credits >= amount,
                    )
                    .values(credits=balance_after, updated_at=_db_time(now))
                )
                if changed.rowcount != 1:
                    raise _FeeWriteConflict
                resolution = "charged"
                ledger_id = self._new_id()
                if ledger_id is None:
                    raise _FeeWriteConflict
                session.execute(
                    insert(CreditLogRow.__table__).values(
                        id=ledger_id,
                        user_id=claim.owner_scope.user_id,
                        amount=-amount,
                        balance_after=balance_after,
                        type="usage",
                        description="AI task frame.optimize pre-apply debit",
                        task_id=claim.owner_scope.task_id,
                        business_key=f"debit:{claim.unit_id}",
                        billing_unit_id=claim.unit_id,
                        related_debit_id=None,
                        created_at=_db_time(now),
                    )
                )

        if resolution == "charged":
            unit_status = "charged"
            debited = amount
        elif resolution == "no_charge":
            unit_status = "no_charge"
            debited = 0
        else:
            unit_status = "unbilled"
            debited = 0
        unit_table = BillingUnitRow.__table__
        unit_changed = session.execute(
            update(unit_table)
            .where(
                unit_table.c.id == claim.unit_id,
                unit_table.c.task_id == claim.owner_scope.task_id,
                unit_table.c.operation == _OPERATION,
                unit_table.c.result_status == "succeeded",
                unit_table.c.billing_status == "unbilled",
                unit_table.c.debited_amount == 0,
                unit_table.c.refunded_amount == 0,
                unit_table.c.user_cancelled_at.is_(None),
                unit_table.c.quoted_amount == amount,
            )
            .values(
                billing_status=unit_status,
                debited_amount=debited,
                updated_at=_db_time(now),
            )
        )
        if unit_changed.rowcount != 1:
            raise _FeeWriteConflict

        progress = locked.progress
        task = locked.graph.task
        prior_debited = sum(int(value.get("debited_amount", 0)) for value in locked.graph.units)
        total_debited = prior_debited + debited
        fee_decision = UnitFeeDecision(
            unit_id=claim.unit_id,
            charge_phase="pre_apply",
            resolution=resolution,
            ledger_ref=ledger_id,
            generation_result_ref=claim.result_ref,
            application_ref=None,
            resolved_at=now,
        )
        records = GenerationRecordRepository(session)
        if records.stage_record(fee_decision).status != "created":
            raise _FeeWriteConflict

        all_decisions = len(progress.fees_by_unit) + 1 == len(locked.graph.units)
        all_zero = all_decisions and all(
            fee.get("resolution") == "no_charge" for fee in progress.fees_by_unit.values()
        ) and resolution == "no_charge"
        task_billing = "charged" if total_debited > 0 else "no_charge" if all_zero else "unbilled"
        stop = resolution == "stopped_insufficient"
        task_table = AITaskRow.__table__
        task_values = {
            "billing_status": task_billing,
            "credit_cost": total_debited,
        }
        if stop:
            task_values["recovery_status"] = "stopped_insufficient"
        commit_now = self._now()
        if commit_now is None:
            raise _FeeWriteConflict
        task_values["updated_at"] = _db_time(commit_now)
        task_changed = session.execute(
            update(task_table)
            .where(
                task_table.c.id == claim.owner_scope.task_id,
                task_table.c.user_id == claim.owner_scope.user_id,
                task_table.c.type == _TASK_TYPE,
                task_table.c.status == "processing",
                task_table.c.claimed_by == claim.worker_instance,
                task_table.c.claim_token == claim.token,
                task_table.c.execution_generation == claim.execution_generation,
                task_table.c.lease_until == _db_time(claim.lease_until),
                task_table.c.lease_until > _db_time(commit_now),
                task_table.c.user_cancelled_at.is_(None),
                task_table.c.recovery_status == task.get("recovery_status"),
                task_table.c.credit_cost == task.get("credit_cost"),
                task_table.c.billing_status == task.get("billing_status"),
                task_table.c.recovery_status == task.get("recovery_status"),
            )
            .values(**task_values)
        )
        if task_changed.rowcount != 1:
            raise _FeeWriteConflict

        next_phase = "stopped_insufficient" if stop else "delivery_pending"
        next_work = replace(
            _work_record(work),
            phase=next_phase,
            phase_version=int(work["phase_version"]) + 1,
            fee_decision_ref=claim.unit_id,
            terminal_reason="insufficient_credits" if stop else None,
            last_execution_generation=claim.execution_generation,
        )
        if records.advance_continuation_work(
            next_work, expected_phase_version=int(work["phase_version"])
        ).status != "updated":
            raise _FeeWriteConflict
        return _FeeWrite(
            owner_id=claim.owner_scope.user_id,
            task_id=claim.owner_scope.task_id,
            unit_id=claim.unit_id,
            work_id=claim.work_id,
            result_ref=claim.result_ref,
            resolution=resolution,
            ledger_ref=ledger_id,
            amount=debited,
            balance_after=balance_after,
            resolved_at=now,
            work_phase=next_phase,
            work_phase_version=next_work.phase_version,
        )

    def _confirm_fee(self, write: _FeeWrite) -> FrameOptimizeFeeOutcome:
        try:
            with self.session_factory() as session:
                with session.begin():
                    from haoai_backend.generation_core.frame_optimize_execution import lock_frame_optimize_batch

                    graph = lock_frame_optimize_batch(
                        session,
                        write.owner_id,
                        write.task_id,
                        crypto=self.crypto,
                        proof_authority=self.proof_authority,
                    )
                    progress = lock_frame_optimize_progress(session, graph) if graph else None
                    if progress is None:
                        return _outcome("pending", write.unit_id, write.result_ref)
                    fee = progress.fees_by_unit.get(write.unit_id)
                    work = progress.works_by_unit.get(write.unit_id)
                    receipt = progress.receipts_by_unit.get(write.unit_id)
                    ledger = progress.ledgers_by_unit.get(write.unit_id)
                    if (
                        fee is None
                        or work is None
                        or receipt is None
                        or fee.get("resolution") != write.resolution
                        or fee.get("ledger_ref") != write.ledger_ref
                        or fee.get("generation_result_ref") != write.result_ref
                        or fee.get("application_ref") is not None
                        or not _same_time(fee.get("resolved_at"), write.resolved_at)
                        or work.get("work_id") != write.work_id
                        or not _fee_work_state_matches(work, write)
                        or work.get("result_ref") != write.result_ref
                        or receipt.get("result_ref") != write.result_ref
                    ):
                        return _outcome("pending", write.unit_id, write.result_ref)
                    if write.resolution == "charged":
                        if (
                            ledger is None
                            or ledger.get("id") != write.ledger_ref
                            or ledger.get("amount") != -write.amount
                            or ledger.get("balance_after") != write.balance_after
                        ):
                            return _outcome("pending", write.unit_id, write.result_ref)
                    elif ledger is not None:
                        return _outcome("pending", write.unit_id, write.result_ref)
                    return _outcome(
                        write.resolution,
                        write.unit_id,
                        write.result_ref,
                        fee,
                        amount=write.amount,
                    )
        except Exception:
            return _outcome("pending", write.unit_id, write.result_ref)

    def _new_id(self):
        try:
            value = self.id_factory()
        except Exception:
            return None
        return value if isinstance(value, str) and value and len(value) <= 36 else None

    def _now(self):
        try:
            value = self.db_clock()
        except Exception:
            return None
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            return None
        return value.astimezone(timezone.utc)


def _work_record(row) -> LocalContinuationWork:
    return LocalContinuationWork(
        work_id=row["work_id"],
        owner_id=row["owner_id"],
        task_id=row["task_id"],
        unit_id=row["unit_id"],
        operation=row["operation"],
        policy_version=row["policy_version"],
        result_ref=row["result_ref"],
        content_digest=row["content_digest"],
        generation_fulfilled_at=_utc(row["generation_fulfilled_at"]),
        original_first_intent_at=_utc(row["original_first_intent_at"]),
        original_deadline_at=_utc(row["original_deadline_at"]),
        phase_order=tuple(row["phase_order"]),
        phase=row["phase"],
        phase_version=row["phase_version"],
        fee_decision_ref=row.get("fee_decision_ref"),
        application_ref=row.get("application_ref"),
        terminal_reason=row.get("terminal_reason"),
        last_execution_generation=row.get("last_execution_generation"),
    )


def _outcome(disposition, unit_id, result_ref=None, fee=None, *, amount=0):
    return FrameOptimizeFeeOutcome(
        disposition=disposition,
        unit_id=unit_id,
        result_ref=result_ref,
        ledger_ref=fee.get("ledger_ref") if fee else None,
        amount=amount,
    )


def _fee_work_state_matches(work, write: _FeeWrite) -> bool:
    actual = (work.get("phase"), work.get("phase_version"))
    initial = (write.work_phase, write.work_phase_version)
    if initial == ("delivery_pending", 1):
        return actual in {
            initial,
            ("finalize_pending", 2),
            ("stopped_delivery_failed", 2),
        }
    return actual == initial


class _FeeWriteConflict(RuntimeError):
    pass


def _utc(value):
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _db_time(value):
    return _utc(value).replace(tzinfo=None)


def _same_time(left, right):
    return _utc(left) is not None and _utc(left) == _utc(right)


__all__ = ["FrameOptimizeFeeOutcome", "SqlAlchemyFrameOptimizeFees"]
