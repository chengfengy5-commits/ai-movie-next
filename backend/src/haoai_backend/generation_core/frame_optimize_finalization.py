"""Consume optimize finalization Work and project a closed parent Task."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import and_, select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.records import LocalContinuationWork
from haoai_backend.generation_contracts.types import (
    ClaimAuthority,
    ContinuationClaimAuthority,
    ControlSettlementAuthority,
    OwnerTaskScope,
)
from haoai_backend.generation_core.base_schema import AITaskRow, UserRow
from haoai_backend.generation_core.frame_optimize_continuation_claims import (
    SqlAlchemyFrameOptimizeContinuationClaims,
    _generation_claim_live,
    _has_unit_cancel_marker,
)
from haoai_backend.generation_core.frame_optimize_execution import (
    LockedFrameOptimizeBatch,
    lock_frame_optimize_batch,
    valid_intent_projection,
)
from haoai_backend.generation_core.frame_optimize_progress import (
    FrameOptimizeProgress,
    lock_frame_optimize_progress,
)
from haoai_backend.generation_core.persistence import GenerationRecordRepository
from haoai_backend.generation_core.schema import LocalContinuationWorkRow
from haoai_backend.generation_credentials.domain import CredentialCrypto, HmacProofAuthority

SessionFactory = Callable[[], Session]
DbClock = Callable[[], datetime]

_TASK_TYPE = "batch-optimize"
_OPERATION = "frame.optimize"


@dataclass(frozen=True, slots=True)
class FrameOptimizeFinalizationOutcome:
    disposition: Literal[
        "pending", "ownership_lost", "work_finalized", "completed", "failed", "already_finalized"
    ]
    task_id: str | None = None
    unit_id: str | None = None


@dataclass(frozen=True, slots=True)
class _FinalizationWrite:
    owner_id: str
    task_id: str
    unit_id: str
    work_id: str
    result_ref: str
    execution_generation: int
    partial_results: tuple[tuple[int, str], ...]
    task_status: str | None
    task_result: str | None


@dataclass(frozen=True, slots=True)
class _ControlWrite:
    owner_id: str
    task_id: str
    error: str
    result: str
    execution_generation: int
    status: str
    updated_at: datetime


@dataclass(slots=True)
class SqlAlchemyFrameOptimizeFinalization:
    session_factory: SessionFactory
    db_clock: DbClock
    crypto: CredentialCrypto
    proof_authority: HmacProofAuthority
    continuation_claims: SqlAlchemyFrameOptimizeContinuationClaims

    def __post_init__(self) -> None:
        if not callable(self.session_factory) or not callable(self.db_clock):
            raise TypeError("finalization session and database clock must be explicit")
        if type(self.crypto) is not CredentialCrypto or type(self.proof_authority) is not HmacProofAuthority:
            raise TypeError("registered frame.optimize verification capabilities are required")
        if not isinstance(self.continuation_claims, SqlAlchemyFrameOptimizeContinuationClaims):
            raise TypeError("frame.optimize continuation claims are required")

    def finalize_current(
        self, generation_claim: ClaimAuthority, work_id: str
    ) -> FrameOptimizeFinalizationOutcome:
        if not isinstance(generation_claim, ClaimAuthority):
            return _outcome("ownership_lost")
        staged: list[_FinalizationWrite] = []
        outcome = _outcome("ownership_lost", generation_claim.scope.task_id)
        try:
            with self.session_factory() as session:
                with session.begin():
                    claim = self.continuation_claims.bind_current_finalization(
                        session, generation_claim, work_id
                    )
                    if claim is not None:
                        outcome = self._consume_in_session(session, claim, staged)
                    else:
                        outcome = self._policy_event_pending_outcome(
                            session, generation_claim, work_id
                        ) or outcome
        except Exception:
            if staged:
                return self._confirm_write(staged[0]) or _outcome(
                    "pending", staged[0].task_id, staged[0].unit_id
                )
            return outcome
        if staged:
            confirmed = self._confirm_write(staged[0])
            if confirmed is not None and confirmed.disposition == "completed":
                generation_claim.local_lost_signal.set()
            return confirmed or outcome
        return self._confirm_existing(
            generation_claim.scope.user_id, generation_claim.scope.task_id, work_id
        ) or outcome

    def _policy_event_pending_outcome(
        self, session: Session, generation_claim: ClaimAuthority, work_id: str
    ) -> FrameOptimizeFinalizationOutcome | None:
        graph = lock_frame_optimize_batch(
            session,
            generation_claim.scope.user_id,
            generation_claim.scope.task_id,
            crypto=self.crypto,
            proof_authority=self.proof_authority,
        )
        now = self._now()
        progress = lock_frame_optimize_progress(session, graph) if graph is not None else None
        if (
            graph is None
            or progress is None
            or now is None
            or not _generation_claim_live(graph.task, generation_claim, now)
            or _has_unit_cancel_marker(graph)
            or not progress.policy_stop_event_refs
        ):
            return None
        unit_id = next(
            (
                unit_id
                for unit_id, work in progress.works_by_unit.items()
                if work.get("work_id") == work_id
                and work.get("phase") == "finalize_pending"
                and work.get("phase_version") == 2
            ),
            None,
        )
        return _outcome("pending", generation_claim.scope.task_id, unit_id) if unit_id else None

    def consume_finalize(
        self, claim: ContinuationClaimAuthority
    ) -> FrameOptimizeFinalizationOutcome:
        if not isinstance(claim, ContinuationClaimAuthority):
            return _outcome("ownership_lost")
        staged: list[_FinalizationWrite] = []
        outcome = _outcome("ownership_lost", claim.owner_scope.task_id, claim.unit_id)
        try:
            with self.session_factory() as session:
                with session.begin():
                    outcome = self._consume_in_session(session, claim, staged)
        except Exception:
            if staged:
                return self._confirm_write(staged[0]) or _outcome(
                    "pending", staged[0].task_id, staged[0].unit_id
                )
            return outcome
        if staged:
            return self._confirm_write(staged[0]) or outcome
        return self._confirm_existing(
            claim.owner_scope.user_id, claim.owner_scope.task_id, claim.work_id
        ) or outcome

    def finalize_stopped_task(
        self,
        task_type: str,
        instance: str,
        task_id: str,
        *,
        generation_claim: ClaimAuthority | None = None,
    ) -> FrameOptimizeFinalizationOutcome:
        """Project a fully closed zero-application Task without fabricating Work."""
        if (
            task_type != _TASK_TYPE
            or not isinstance(instance, str)
            or not instance
            or len(instance) > 64
            or not isinstance(task_id, str)
            or not task_id
        ):
            return _outcome("pending", task_id if isinstance(task_id, str) else None)
        staged: list[_ControlWrite] = []
        outcome = _outcome("pending", task_id)
        try:
            with self.session_factory() as session:
                with session.begin():
                    outcome = self._project_stopped_task(
                        session, task_id, instance, generation_claim, staged
                    )
        except Exception:
            if staged:
                return self._confirm_control_write(staged[0]) or _outcome("pending", task_id)
            return outcome
        if staged:
            confirmed = self._confirm_control_write(staged[0])
            if (
                confirmed is not None
                and confirmed.disposition == "failed"
                and generation_claim is not None
            ):
                generation_claim.local_lost_signal.set()
            return confirmed or outcome
        return self._confirm_control_existing(task_id) or outcome

    def _consume_in_session(
        self,
        session: Session,
        claim: ContinuationClaimAuthority,
        staged: list[_FinalizationWrite],
    ) -> FrameOptimizeFinalizationOutcome:
        now = self._now()
        locked = self.continuation_claims.lock_current_finalization_claim(session, claim, now)
        if locked is None or now is None:
            return _outcome("ownership_lost", claim.owner_scope.task_id, claim.unit_id)
        graph = locked.graph
        progress = locked.progress
        work = locked.work
        if (
            progress.policy_stop_event_refs
            or _has_unit_cancel_marker(graph)
            or graph.task.get("status") != "processing"
            or work.get("phase") != "finalize_pending"
            or work.get("phase_version") != 2
            or work.get("work_id") != claim.work_id
            or work.get("result_ref") != claim.result_ref
        ):
            return _outcome("pending", claim.owner_scope.task_id, claim.unit_id)
        if not self.continuation_claims.confirm_live_delivery_fence(session, claim):
            return _outcome("ownership_lost", claim.owner_scope.task_id, claim.unit_id)

        updated = replace(
            _work_record(work),
            phase="done",
            phase_version=3,
            last_execution_generation=claim.execution_generation,
        )
        if GenerationRecordRepository(session).advance_continuation_work(
            updated, expected_phase_version=2
        ).status != "updated":
            return _outcome("pending", claim.owner_scope.task_id, claim.unit_id)
        session.flush()

        fresh_progress = lock_frame_optimize_progress(session, graph)
        if fresh_progress is None or fresh_progress.policy_stop_event_refs:
            raise _FinalizationWriteConflict
        if _has_unit_cancel_marker(graph):
            raise _FinalizationWriteConflict
        closed = _all_slots_closed(graph, fresh_progress)
        terminal_status: str | None = None
        if closed:
            if not fresh_progress.applied_by_unit:
                raise _FinalizationWriteConflict
            terminal_status = "completed"
            if not self.continuation_claims.confirm_live_delivery_fence(session, claim):
                raise _FinalizationWriteConflict
            final_now = self._now()
            if final_now is None or _utc(claim.lease_until) <= final_now:
                raise _FinalizationWriteConflict
            table = AITaskRow.__table__
            old_result = graph.task.get("result")
            result_match = table.c.result.is_(None) if old_result is None else table.c.result == old_result
            changed = session.execute(
                update(table)
                .where(
                    table.c.id == claim.owner_scope.task_id,
                    table.c.user_id == claim.owner_scope.user_id,
                    table.c.type == _TASK_TYPE,
                    table.c.status == "processing",
                    table.c.claimed_by == claim.worker_instance,
                    table.c.claim_token == claim.token,
                    table.c.execution_generation == claim.execution_generation,
                    table.c.lease_until == _db_time(claim.lease_until),
                    table.c.lease_until > _db_time(final_now),
                    table.c.user_cancelled_at.is_(None),
                    table.c.recovery_status == graph.task.get("recovery_status"),
                    table.c.billing_status == graph.task.get("billing_status"),
                    table.c.credit_cost == graph.task.get("credit_cost"),
                    result_match,
                )
                .values(
                    status=terminal_status,
                    claimed_by=None,
                    claim_token=None,
                    lease_until=None,
                    progress=100,
                    progress_message=f"分镜优化完成（已应用 {len(fresh_progress.applied_by_unit)} 项）",
                    updated_at=_db_time(final_now),
                )
            )
            if changed.rowcount != 1:
                raise _FinalizationWriteConflict
        else:
            if not self.continuation_claims.confirm_live_delivery_fence(session, claim):
                raise _FinalizationWriteConflict
            final_now = self._now()
            if final_now is None or _utc(claim.lease_until) <= final_now:
                raise _FinalizationWriteConflict

        staged.append(
            _FinalizationWrite(
                owner_id=claim.owner_scope.user_id,
                task_id=claim.owner_scope.task_id,
                unit_id=claim.unit_id,
                work_id=claim.work_id,
                result_ref=claim.result_ref,
                execution_generation=claim.execution_generation,
                partial_results=fresh_progress.partial_results,
                task_status=terminal_status,
                task_result=graph.task.get("result"),
            )
        )
        return _outcome(
            terminal_status or "work_finalized", claim.owner_scope.task_id, claim.unit_id
        )

    def _project_stopped_task(
        self,
        session: Session,
        task_id: str,
        instance: str,
        generation_claim: ClaimAuthority | None,
        staged: list[_ControlWrite],
    ) -> FrameOptimizeFinalizationOutcome:
        task_table = AITaskRow.__table__
        owner_id = session.execute(
            select(task_table.c.user_id).where(
                task_table.c.id == task_id, task_table.c.type == _TASK_TYPE
            )
        ).scalar_one_or_none()
        if not isinstance(owner_id, str) or not owner_id:
            return _outcome("pending", task_id)
        if session.execute(
            select(UserRow.id).where(UserRow.id == owner_id).with_for_update()
        ).scalar_one_or_none() is None:
            return _outcome("pending", task_id)
        task_row = session.execute(
            select(task_table)
            .where(task_table.c.id == task_id, task_table.c.user_id == owner_id, task_table.c.type == _TASK_TYPE)
            .with_for_update()
        ).mappings().one_or_none()
        graph = lock_frame_optimize_batch(
            session,
            owner_id,
            task_id,
            crypto=self.crypto,
            proof_authority=self.proof_authority,
        )
        now = self._now()
        progress = lock_frame_optimize_progress(session, graph) if graph is not None else None
        if task_row is None or graph is None or progress is None or now is None:
            return _outcome("pending", task_id)
        task = graph.task
        if task.get("status") in {"completed", "failed"}:
            return _outcome("already_finalized", task_id)
        if (
            task.get("status") not in {"queued", "processing", "reconciling"}
            or task.get("user_cancelled_at") is not None
            or _has_unit_cancel_marker(graph)
            or progress.policy_stop_event_refs
            or not valid_intent_projection(graph)
            or not _all_slots_closed(graph, progress)
            or progress.applied_by_unit
            or progress.partial_results
        ):
            return _outcome("pending", task_id)

        lease = _utc(task.get("lease_until"))
        live_lease = lease is not None and lease > now
        if live_lease:
            if not _generation_claim_matches(generation_claim, task, owner_id, task_id, instance, now):
                return _outcome("ownership_lost", task_id)
        elif generation_claim is not None and not _generation_claim_matches_identity(
            generation_claim, task, owner_id, task_id, instance
        ):
            return _outcome("ownership_lost", task_id)
        if task.get("status") in {"queued", "reconciling"} and (
            task.get("claimed_by") is not None
            or task.get("claim_token") is not None
            or lease is not None
        ):
            return _outcome("pending", task_id)

        error = _failure_reason(progress)
        if error is None or task.get("result") is not None:
            return _outcome("pending", task_id)
        result = json.dumps(
            {"partial": [], "error": error},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        authority = ControlSettlementAuthority(
            owner_scope=OwnerTaskScope(owner_id, task_id, _OPERATION),
            policy_stop_event_refs=progress.policy_stop_event_refs,
            revoked_execution_generation=task.get("execution_generation"),
            quiescent=True,
            purpose="terminal_projection",
        )
        if (
            not authority.quiescent
            or authority.purpose != "terminal_projection"
            or authority.owner_scope.user_id != owner_id
            or authority.owner_scope.task_id != task_id
        ):
            return _outcome("pending", task_id)

        commit_now = self._now()
        if commit_now is None:
            return _outcome("pending", task_id)
        if live_lease:
            if not _generation_claim_matches(
                generation_claim, task, owner_id, task_id, instance, commit_now
            ):
                return _outcome("ownership_lost", task_id)
        elif lease is not None and lease > commit_now:
            return _outcome("ownership_lost", task_id)
        conditions = [
            task_table.c.id == task_id,
            task_table.c.user_id == owner_id,
            task_table.c.type == _TASK_TYPE,
            task_table.c.status == task.get("status"),
            task_table.c.execution_generation == task.get("execution_generation"),
            task_table.c.user_cancelled_at.is_(None),
            task_table.c.recovery_status == task.get("recovery_status"),
            task_table.c.billing_status == task.get("billing_status"),
            task_table.c.credit_cost == task.get("credit_cost"),
            task_table.c.result.is_(None),
        ]
        if lease is None:
            conditions.append(task_table.c.lease_until.is_(None))
        elif live_lease:
            conditions.append(
                and_(
                    task_table.c.lease_until == _db_time(lease),
                    task_table.c.lease_until > _db_time(commit_now),
                )
            )
        else:
            conditions.append(
                and_(
                    task_table.c.lease_until == _db_time(lease),
                    task_table.c.lease_until <= _db_time(commit_now),
                )
            )
        changed = session.execute(
            update(task_table)
            .where(*conditions)
            .values(
                status="failed",
                claimed_by=None,
                claim_token=None,
                lease_until=None,
                result=result,
                progress=100,
                progress_message="分镜优化未能应用任何结果",
                updated_at=_db_time(commit_now),
            )
        )
        if changed.rowcount != 1:
            raise _FinalizationWriteConflict
        staged.append(
            _ControlWrite(
                owner_id=owner_id,
                task_id=task_id,
                error=error,
                result=result,
                execution_generation=int(task.get("execution_generation", 0)),
                status=str(task.get("status")),
                updated_at=commit_now,
            )
        )
        return _outcome("failed", task_id)

    def _confirm_write(self, write: _FinalizationWrite) -> FrameOptimizeFinalizationOutcome | None:
        try:
            with self.session_factory() as session:
                graph = lock_frame_optimize_batch(
                    session,
                    write.owner_id,
                    write.task_id,
                    crypto=self.crypto,
                    proof_authority=self.proof_authority,
                )
                progress = lock_frame_optimize_progress(session, graph) if graph else None
                if graph is None or progress is None:
                    return None
                work = progress.works_by_unit.get(write.unit_id)
                if (
                    work is None
                    or work.get("work_id") != write.work_id
                    or work.get("result_ref") != write.result_ref
                    or work.get("phase") != "done"
                    or work.get("phase_version") != 3
                    or work.get("last_execution_generation") != write.execution_generation
                    or progress.partial_results != write.partial_results
                    or graph.task.get("result") != write.task_result
                ):
                    return None
                status = graph.task.get("status")
                if status == "completed":
                    if (
                        graph.task.get("progress") != 100
                        or graph.task.get("claim_token") is not None
                        or graph.task.get("claimed_by") is not None
                        or graph.task.get("lease_until") is not None
                        or not _all_slots_closed(graph, progress)
                        or not progress.applied_by_unit
                    ):
                        return None
                    return _outcome("completed", write.task_id, write.unit_id)
                if status != "processing":
                    return None
                return _outcome("work_finalized", write.task_id, write.unit_id)
        except Exception:
            return None

    def _confirm_existing(self, owner_id: str, task_id: str, work_id: str):
        try:
            with self.session_factory() as session:
                graph = lock_frame_optimize_batch(
                    session,
                    owner_id,
                    task_id,
                    crypto=self.crypto,
                    proof_authority=self.proof_authority,
                )
                progress = lock_frame_optimize_progress(session, graph) if graph else None
                if graph is None or progress is None:
                    return None
                match = next(
                    (
                        (unit_id, work)
                        for unit_id, work in progress.works_by_unit.items()
                        if work.get("work_id") == work_id
                        and work.get("phase") == "done"
                        and work.get("phase_version") == 3
                    ),
                    None,
                )
                if match is None:
                    return None
                unit_id, _work = match
                if graph.task.get("status") == "completed":
                    return _outcome("already_finalized", task_id, unit_id)
                if graph.task.get("status") == "processing":
                    return _outcome("already_finalized", task_id, unit_id)
                return None
        except Exception:
            return None

    def _confirm_control_write(self, write: _ControlWrite):
        result = self._confirm_control_existing(write.task_id)
        if result is None or result.disposition not in {"failed", "already_finalized"}:
            return None
        try:
            with self.session_factory() as session:
                graph = lock_frame_optimize_batch(
                    session,
                    write.owner_id,
                    write.task_id,
                    crypto=self.crypto,
                    proof_authority=self.proof_authority,
                )
                progress = lock_frame_optimize_progress(session, graph) if graph else None
                if graph is None or progress is None:
                    return None
                task = graph.task
                if not all(
                    (
                        task.get("status") == "failed",
                        task.get("result") == write.result,
                        task.get("progress") == 100,
                        task.get("progress_message") == "分镜优化未能应用任何结果",
                        task.get("claim_token") is None,
                        task.get("claimed_by") is None,
                        task.get("lease_until") is None,
                        task.get("execution_generation") == write.execution_generation,
                        progress.terminal_error == write.error,
                        not progress.applied_by_unit,
                        _all_slots_closed(graph, progress),
                    )
                ):
                    return None
                return _outcome("failed", write.task_id)
        except Exception:
            return None

    def _confirm_control_existing(self, task_id: str):
        try:
            with self.session_factory() as session:
                task = session.execute(
                    select(AITaskRow.__table__).where(
                        AITaskRow.__table__.c.id == task_id,
                        AITaskRow.__table__.c.type == _TASK_TYPE,
                    )
                ).mappings().one_or_none()
                if task is None or task.get("status") != "failed":
                    return None
                graph = lock_frame_optimize_batch(
                    session,
                    str(task["user_id"]),
                    task_id,
                    crypto=self.crypto,
                    proof_authority=self.proof_authority,
                )
                progress = lock_frame_optimize_progress(session, graph) if graph else None
                if (
                    progress is None
                    or progress.terminal_error is None
                    or progress.applied_by_unit
                    or not _all_slots_closed(graph, progress)
                    or task.get("progress") != 100
                    or task.get("claim_token") is not None
                    or task.get("claimed_by") is not None
                    or task.get("lease_until") is not None
                ):
                    return None
                return _outcome("already_finalized", task_id)
        except Exception:
            return None

    def _now(self) -> datetime | None:
        try:
            value = self.db_clock()
        except Exception:
            return None
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            return None
        return value.astimezone(timezone.utc)


class _FinalizationWriteConflict(RuntimeError):
    pass


def _all_slots_closed(graph: LockedFrameOptimizeBatch, progress: FrameOptimizeProgress) -> bool:
    terminal = {
        "done",
        "stopped_insufficient",
        "stopped_delivery_failed",
        "stopped_late_generation",
    }
    closed = {
        unit_id for unit_id, work in progress.works_by_unit.items()
        if work.get("phase") in terminal
    } | set(progress.unstarted_insufficient_by_unit)
    return closed == set(graph.units_by_id) and set(progress.works_by_unit) <= set(graph.units_by_id)


def _failure_reason(progress: FrameOptimizeProgress) -> str | None:
    if progress.stopped_insufficient:
        return "insufficient_credits"
    reasons = {
        str(work.get("terminal_reason"))
        for work in progress.works_by_unit.values()
        if work.get("phase") in {"stopped_delivery_failed", "stopped_late_generation"}
        and isinstance(work.get("terminal_reason"), str)
    }
    if len(reasons) == 1:
        return next(iter(reasons))
    if reasons:
        return "frame_optimize_results_unapplied"
    return None


def _generation_claim_matches(claim, task, owner_id, task_id, instance, now) -> bool:
    return bool(
        _generation_claim_matches_identity(claim, task, owner_id, task_id, instance)
        and claim.local_lost_signal is not None
        and not claim.local_lost_signal.is_set()
        and _utc(task.get("lease_until")) is not None
        and _utc(task.get("lease_until")) > now
        and _same_time(task.get("lease_until"), claim.lease_until)
    )


def _generation_claim_matches_identity(claim, task, owner_id, task_id, instance) -> bool:
    return bool(
        isinstance(claim, ClaimAuthority)
        and claim.scope.operation == _OPERATION
        and claim.scope.user_id == owner_id
        and claim.scope.task_id == task_id
        and claim.worker_instance == instance
        and task.get("status") == "processing"
        and task.get("user_id") == owner_id
        and task.get("id") == task_id
        and task.get("claimed_by") == instance
        and task.get("claim_token") == claim.token
        and task.get("execution_generation") == claim.execution_generation
    )


def _work_record(work) -> LocalContinuationWork:
    return LocalContinuationWork(
        work_id=work["work_id"],
        owner_id=work["owner_id"],
        task_id=work["task_id"],
        unit_id=work["unit_id"],
        operation=work["operation"],
        policy_version=work["policy_version"],
        result_ref=work["result_ref"],
        content_digest=work["content_digest"],
        generation_fulfilled_at=_utc(work["generation_fulfilled_at"]),
        original_first_intent_at=_utc(work["original_first_intent_at"]),
        original_deadline_at=_utc(work["original_deadline_at"]),
        phase_order=tuple(work["phase_order"]),
        phase=work["phase"],
        phase_version=work["phase_version"],
        fee_decision_ref=work.get("fee_decision_ref"),
        application_ref=work.get("application_ref"),
        terminal_reason=work.get("terminal_reason"),
        last_execution_generation=work.get("last_execution_generation"),
    )


def _outcome(disposition, task_id=None, unit_id=None):
    return FrameOptimizeFinalizationOutcome(disposition, task_id, unit_id)


def _utc(value):
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _db_time(value):
    return _utc(value).replace(tzinfo=None)


def _same_time(left, right):
    return _utc(left) is not None and _utc(left) == _utc(right)


__all__ = [
    "FrameOptimizeFinalizationOutcome",
    "SqlAlchemyFrameOptimizeFinalization",
]
