"""Lease the saved result phases for a registered frame.optimize batch."""

from __future__ import annotations

import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.types import (
    ClaimAuthority,
    ContinuationClaimAuthority,
    OwnerTaskScope,
)
from haoai_backend.generation_core.base_schema import AITaskRow, BillingUnitRow, UserRow
from haoai_backend.generation_core.frame_optimize_execution import (
    LockedFrameOptimizeBatch,
    lock_frame_optimize_batch,
)
from haoai_backend.generation_core.frame_optimize_progress import (
    FrameOptimizeProgress,
    lock_frame_optimize_progress,
)
from haoai_backend.generation_core.schema import LocalContinuationWorkRow
from haoai_backend.generation_credentials.domain import CredentialCrypto, HmacProofAuthority

_OPERATION = "frame.optimize"
_TASK_TYPE = "batch-optimize"
_ACTIVE = ("queued", "processing", "reconciling")
_DEFAULT_LEASE_SECONDS = 60

SessionFactory = Callable[[], Session]
DbClock = Callable[[], datetime]
IdFactory = Callable[[], str]


@dataclass(frozen=True, slots=True)
class LockedFrameOptimizeContinuation:
    graph: LockedFrameOptimizeBatch
    progress: FrameOptimizeProgress
    unit: dict[str, object]
    receipt: dict[str, object]
    evidence: dict[str, object]
    work: dict[str, object]
    fee: dict[str, object] | None
    ledger: dict[str, object] | None


@dataclass(slots=True)
class SqlAlchemyFrameOptimizeContinuationClaims:
    """Bind a fee phase to a live generation lease or recover it after release."""

    session_factory: SessionFactory
    db_clock: DbClock
    crypto: CredentialCrypto
    proof_authority: HmacProofAuthority
    id_factory: IdFactory = field(default=lambda: secrets.token_urlsafe(32))
    lease_seconds: int = _DEFAULT_LEASE_SECONDS
    _signals: dict[str, threading.Event | object] = field(default_factory=dict, init=False, repr=False)
    _independent_tokens: set[str] = field(default_factory=set, init=False, repr=False)
    _signals_lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        if not callable(self.session_factory) or not callable(self.db_clock):
            raise TypeError("continuation session and database clock must be explicit")
        if not callable(self.id_factory):
            raise TypeError("continuation token factory must be callable")
        if type(self.lease_seconds) is not int or self.lease_seconds <= 0:
            raise ValueError("continuation lease must be a positive number of seconds")

    def bind_current_continuation(
        self,
        session: Session,
        generation_claim: ClaimAuthority,
        work_id: str,
    ) -> ContinuationClaimAuthority | None:
        """Create a Unit-scoped authority inside the caller's fee transaction."""
        if (
            not isinstance(session, Session)
            or not isinstance(generation_claim, ClaimAuthority)
            or generation_claim.scope.operation != _OPERATION
            or generation_claim.local_lost_signal.is_set()
            or not isinstance(work_id, str)
            or not work_id
        ):
            return None
        graph = self._lock_batch(session, generation_claim.scope.user_id, generation_claim.scope.task_id)
        now = self._now()
        if graph is None or now is None or not _generation_claim_live(graph.task, generation_claim, now):
            return None
        progress = lock_frame_optimize_progress(session, graph)
        if progress is None or progress.stopped_insufficient:
            return None
        match = next(
            (
                (unit_id, work)
                for unit_id, work in progress.works_by_unit.items()
                if work.get("work_id") == work_id and work.get("phase") == "fee_pending"
            ),
            None,
        )
        if match is None:
            return None
        unit_id, work = match
        signal = generation_claim.local_lost_signal
        with self._signals_lock:
            self._signals.setdefault(generation_claim.token, signal)
        return _authority(graph.task, work, unit_id, generation_claim)

    def bind_current_delivery(
        self,
        session: Session,
        generation_claim: ClaimAuthority,
        work_id: str,
    ) -> ContinuationClaimAuthority | None:
        """Bind the live generation lease to one already-paid delivery Unit."""
        if (
            not isinstance(session, Session)
            or not isinstance(generation_claim, ClaimAuthority)
            or generation_claim.scope.operation != _OPERATION
            or generation_claim.local_lost_signal.is_set()
            or not isinstance(work_id, str)
            or not work_id
        ):
            return None
        graph = self._lock_batch(session, generation_claim.scope.user_id, generation_claim.scope.task_id)
        now = self._now()
        if (
            graph is None
            or now is None
            or not _generation_claim_live(graph.task, generation_claim, now)
            or _has_unit_cancel_marker(graph)
        ):
            return None
        progress = lock_frame_optimize_progress(session, graph)
        if progress is None:
            return None
        match = next(
            (
                (unit_id, work)
                for unit_id, work in progress.works_by_unit.items()
                if work.get("work_id") == work_id
                and work.get("phase") == "delivery_pending"
                and work.get("phase_version") == 1
                and work.get("fee_decision_ref") == unit_id
                and progress.fees_by_unit.get(unit_id, {}).get("resolution") in {"charged", "no_charge"}
            ),
            None,
        )
        if match is None:
            return None
        unit_id, work = match
        signal = generation_claim.local_lost_signal
        with self._signals_lock:
            self._signals.setdefault(generation_claim.token, signal)
        return _authority(graph.task, work, unit_id, generation_claim)

    def bind_current_finalization(
        self,
        session: Session,
        generation_claim: ClaimAuthority,
        work_id: str,
    ) -> ContinuationClaimAuthority | None:
        """Narrow a live generation lease to one applied optimize Work."""
        if (
            not isinstance(session, Session)
            or not isinstance(generation_claim, ClaimAuthority)
            or generation_claim.scope.operation != _OPERATION
            or generation_claim.local_lost_signal.is_set()
            or not isinstance(work_id, str)
            or not work_id
        ):
            return None
        graph = self._lock_batch(
            session, generation_claim.scope.user_id, generation_claim.scope.task_id
        )
        now = self._now()
        if (
            graph is None
            or now is None
            or not _generation_claim_live(graph.task, generation_claim, now)
            or _has_unit_cancel_marker(graph)
        ):
            return None
        progress = lock_frame_optimize_progress(session, graph)
        if progress is None or progress.policy_stop_event_refs:
            return None
        match = next(
            (
                (unit_id, work)
                for unit_id, work in progress.works_by_unit.items()
                if work.get("work_id") == work_id
                and work.get("phase") == "finalize_pending"
                and work.get("phase_version") == 2
                and work.get("application_ref") == progress.applications_by_unit.get(unit_id, {}).get(
                    "application_ref"
                )
                and progress.fees_by_unit.get(unit_id, {}).get("resolution")
                in {"charged", "no_charge"}
            ),
            None,
        )
        if match is None:
            return None
        unit_id, work = match
        signal = generation_claim.local_lost_signal
        with self._signals_lock:
            self._signals.setdefault(generation_claim.token, signal)
        return _authority(graph.task, work, unit_id, generation_claim)

    def claim_continuation(
        self, task_type: str, instance: str, lease_seconds: int | None = None
    ) -> ContinuationClaimAuthority | None:
        requested = self.lease_seconds if lease_seconds is None else lease_seconds
        if (
            task_type != _TASK_TYPE
            or not isinstance(instance, str)
            or not instance
            or len(instance) > 64
            or type(requested) is not int
            or requested <= 0
        ):
            return None
        try:
            token = self.id_factory()
        except Exception:
            return None
        if not isinstance(token, str) or not token or len(token) > 64:
            return None
        now = self._now()
        if now is None:
            return None
        for owner_id, task_id, work_id in self._candidate_ids(now):
            staged: list[ContinuationClaimAuthority] = []
            try:
                claim = self._claim_candidate(
                    owner_id, task_id, work_id, instance, token,
                    min(requested, self.lease_seconds), staged,
                )
            except Exception:
                claim = self._confirm_claim(staged[0]) if staged else None
            if claim is not None:
                signal = threading.Event()
                with self._signals_lock:
                    self._signals[token] = signal
                    self._independent_tokens.add(token)
                return claim
        return None

    def renew_continuation(
        self, claim: ContinuationClaimAuthority
    ) -> ContinuationClaimAuthority | None:
        signal = self._signal_for(claim, independent_only=True)
        if signal is None or signal.is_set():
            return None
        staged: list[ContinuationClaimAuthority] = []
        try:
            with self.session_factory() as session:
                with session.begin():
                    graph = self._lock_authority_graph(
                        session,
                        claim,
                        self._now(),
                        allow_advanced_phase=True,
                        allow_stopped=True,
                        allow_done=True,
                    )
                    now = self._now()
                    if graph is None or now is None:
                        return None
                    new_lease = now + timedelta(seconds=self.lease_seconds)
                    table = AITaskRow.__table__
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
                            table.c.lease_until > _db_time(now),
                            table.c.user_cancelled_at.is_(None),
                        )
                        .values(lease_until=_db_time(new_lease), updated_at=_db_time(now))
                    )
                    if changed.rowcount != 1:
                        return None
                    staged.append(replace(claim, lease_until=new_lease))
        except Exception:
            return self._confirm_renewal(staged[0]) if staged else None
        return staged[0] if staged else None

    def release_continuation(self, claim: ContinuationClaimAuthority) -> bool:
        signal = self._signal_for(claim, independent_only=True)
        if signal is None:
            return False
        expected: tuple[datetime, dict[str, object]] | None = None
        try:
            with self.session_factory() as session:
                with session.begin():
                    graph = self._lock_authority_graph(
                        session,
                        claim,
                        self._now(),
                        allow_advanced_phase=True,
                        allow_stopped=True,
                        allow_done=True,
                    )
                    now = self._now()
                    if graph is None or now is None:
                        return False
                    task = graph.graph.task
                    table = AITaskRow.__table__
                    changed = session.execute(
                        update(table)
                        .where(
                            table.c.id == claim.owner_scope.task_id,
                            table.c.user_id == claim.owner_scope.user_id,
                            table.c.status == "processing",
                            table.c.claimed_by == claim.worker_instance,
                            table.c.claim_token == claim.token,
                            table.c.execution_generation == claim.execution_generation,
                            table.c.lease_until == task.get("lease_until"),
                            table.c.lease_until > _db_time(now),
                            table.c.user_cancelled_at.is_(None),
                        )
                        .values(
                            status="reconciling",
                            claimed_by=None,
                            claim_token=None,
                            lease_until=None,
                            updated_at=_db_time(now),
                        )
                    )
                    if changed.rowcount == 1:
                        expected = (now, graph.work)
        except Exception:
            pass
        released = expected is not None and self._confirm_release(claim, expected)
        signal.set()
        self._forget_signal(claim.token, signal)
        return released

    def lock_current_claim(
        self,
        session: Session,
        claim: ContinuationClaimAuthority,
        now: datetime | None = None,
    ) -> LockedFrameOptimizeContinuation | None:
        if not isinstance(session, Session) or not isinstance(claim, ContinuationClaimAuthority):
            return None
        signal = self._signal_for(claim)
        if signal is None or signal.is_set():
            return None
        graph = self._lock_authority_graph(session, claim, now or self._now())
        if graph is None:
            return None
        return graph

    def lock_current_finalization_claim(
        self,
        session: Session,
        claim: ContinuationClaimAuthority,
        now: datetime | None = None,
    ) -> LockedFrameOptimizeContinuation | None:
        if not isinstance(session, Session) or not isinstance(claim, ContinuationClaimAuthority):
            return None
        signal = self._signal_for(claim)
        if signal is None or signal.is_set():
            return None
        locked = self._lock_authority_graph(
            session,
            claim,
            now or self._now(),
            allow_advanced_phase=True,
            allow_stopped=True,
        )
        if (
            locked is None
            or _has_unit_cancel_marker(locked.graph)
            or locked.progress.policy_stop_event_refs
            or locked.work.get("phase") != "finalize_pending"
            or locked.work.get("phase_version") != claim.phase_version
            or claim.phase_version != 2
            or locked.work.get("application_ref") is None
            or locked.fee is None
            or locked.fee.get("resolution") not in {"charged", "no_charge"}
            or locked.work.get("fee_decision_ref") != claim.unit_id
        ):
            return None
        return locked

    def lock_current_delivery_claim(
        self,
        session: Session,
        claim: ContinuationClaimAuthority,
        now: datetime | None = None,
    ) -> LockedFrameOptimizeContinuation | None:
        if not isinstance(session, Session) or not isinstance(claim, ContinuationClaimAuthority):
            return None
        signal = self._signal_for(claim)
        if signal is None or signal.is_set():
            return None
        locked = self._lock_authority_graph(
            session,
            claim,
            now or self._now(),
            allow_advanced_phase=True,
            allow_stopped=True,
        )
        if locked is None:
            return None
        if locked.work.get("phase") == "delivery_pending":
            if (
                _has_unit_cancel_marker(locked.graph)
                or locked.work.get("phase_version") != claim.phase_version
                or locked.work.get("fee_decision_ref") != claim.unit_id
                or locked.fee is None
                or locked.fee.get("resolution") not in {"charged", "no_charge"}
                or not locked.receipt.get("automatic_application_allowed")
            ):
                return None
            return locked
        if locked.work.get("phase") == "finalize_pending":
            if (
                locked.work.get("phase_version") != claim.phase_version + 1
                or locked.work.get("application_ref") is None
            ):
                return None
            return locked
        return None

    def confirm_live_delivery_fence(
        self, session: Session, claim: ContinuationClaimAuthority
    ) -> bool:
        """Recheck the parent lease after source locks and before final CAS writes."""
        if not isinstance(session, Session) or not isinstance(claim, ContinuationClaimAuthority):
            return False
        signal = self._signal_for(claim)
        now = self._now()
        task = session.execute(
            select(AITaskRow.__table__)
            .where(
                AITaskRow.__table__.c.id == claim.owner_scope.task_id,
                AITaskRow.__table__.c.user_id == claim.owner_scope.user_id,
                AITaskRow.__table__.c.type == _TASK_TYPE,
            )
            .with_for_update()
        ).mappings().one_or_none()
        if task is None or now is None or signal is None or signal.is_set():
            return False
        lease = _utc(task.get("lease_until"))
        units = session.execute(
            select(BillingUnitRow.__table__)
            .where(BillingUnitRow.__table__.c.task_id == claim.owner_scope.task_id)
            .order_by(BillingUnitRow.__table__.c.id)
            .with_for_update()
        ).mappings().all()
        return bool(
            task.get("status") == "processing"
            and task.get("claimed_by") == claim.worker_instance
            and task.get("claim_token") == claim.token
            and task.get("execution_generation") == claim.execution_generation
            and task.get("user_cancelled_at") is None
            and bool(units)
            and all(row.get("user_cancelled_at") is None for row in units)
            and lease is not None
            and lease > now
            and _same_time(lease, claim.lease_until)
        )

    def _claim_candidate(self, owner_id, task_id, work_id, instance, token, seconds, staged):
        signal = threading.Event()
        with self.session_factory() as session:
            with session.begin():
                user = session.execute(
                    select(UserRow.id).where(UserRow.id == owner_id).with_for_update()
                ).scalar_one_or_none()
                if user is None:
                    return None
                task_row = session.execute(
                    select(AITaskRow.__table__)
                    .where(
                        AITaskRow.__table__.c.id == task_id,
                        AITaskRow.__table__.c.user_id == owner_id,
                        AITaskRow.__table__.c.type == _TASK_TYPE,
                    )
                    .with_for_update()
                ).mappings().one_or_none()
                if task_row is None:
                    return None
                graph = self._lock_batch(session, owner_id, task_id)
                now = self._now()
                if graph is None or now is None:
                    return None
                progress = lock_frame_optimize_progress(session, graph)
                unit_id = self._unit_for_work(progress, work_id) if progress else None
                work = progress.works_by_unit.get(unit_id) if progress and unit_id else None
                fee = progress.fees_by_unit.get(unit_id) if progress and unit_id else None
                may_drain_paid_delivery = bool(
                    work is not None
                    and work.get("phase") in {"delivery_pending", "finalize_pending"}
                    and fee is not None
                    and fee.get("resolution") in {"charged", "no_charge"}
                )
                delivery_blocked_by_unit_cancel = bool(
                    may_drain_paid_delivery and _has_unit_cancel_marker(graph)
                )
                if (
                    progress is None
                    or unit_id is None
                    or work is None
                    or work.get("work_id") != work_id
                    or work.get("phase") not in {
                        "fee_pending", "delivery_pending", "finalize_pending"
                    }
                    or (
                        work.get("phase") == "finalize_pending"
                        and progress.policy_stop_event_refs
                    )
                    or delivery_blocked_by_unit_cancel
                    or (
                        progress.stopped_insufficient
                        and not may_drain_paid_delivery
                    )
                    or not _lease_available(graph.task, now)
                    or (
                        graph.task.get("recovery_status") == "stopped_insufficient"
                        and not may_drain_paid_delivery
                    )
                ):
                    return None
                if session.execute(
                    select(AITaskRow.__table__.c.id)
                    .where(AITaskRow.__table__.c.claim_token == token)
                    .limit(1)
                ).scalar_one_or_none() is not None:
                    return None
                generation = int(graph.task.get("execution_generation", 0)) + 1
                lease = now + timedelta(seconds=seconds)
                task_table = AITaskRow.__table__
                changed = session.execute(
                    update(task_table)
                    .where(
                        task_table.c.id == task_id,
                        task_table.c.user_id == owner_id,
                        task_table.c.type == _TASK_TYPE,
                        task_table.c.status == graph.task.get("status"),
                        task_table.c.execution_generation == graph.task.get("execution_generation"),
                        task_table.c.request_data == graph.task.get("request_data"),
                        task_table.c.user_cancelled_at.is_(None),
                        _available_clause(task_table, graph.task, now),
                    )
                    .values(
                        status="processing",
                        claimed_by=instance,
                        claim_token=token,
                        execution_generation=generation,
                        lease_until=_db_time(lease),
                        updated_at=_db_time(now),
                    )
                )
                if changed.rowcount != 1:
                    return None
                authority = ContinuationClaimAuthority(
                    owner_scope=OwnerTaskScope(owner_id, task_id, _OPERATION),
                    unit_id=unit_id,
                    work_id=work_id,
                    result_ref=str(work["result_ref"]),
                    phase_version=int(work["phase_version"]),
                    worker_instance=instance,
                    token=token,
                    execution_generation=generation,
                    lease_until=lease,
                )
                staged.append(authority)
                return authority

    def _confirm_claim(self, expected):
        try:
            with self.session_factory() as session:
                with session.begin():
                    locked = self._lock_authority_graph(
                        session,
                        expected,
                        self._now(),
                        allow_finalize=True,
                        allow_stopped=expected.phase_version == 2,
                    )
                    if locked is None:
                        return None
                    if locked.work.get("phase") == "finalize_pending":
                        if locked.progress.policy_stop_event_refs:
                            return None
                        if locked.progress.stopped_insufficient and not (
                            expected.phase_version == 2
                            and locked.work.get("phase_version") == 2
                            and locked.fee is not None
                            and locked.fee.get("resolution") in {"charged", "no_charge"}
                        ):
                            return None
                    return expected
        except Exception:
            return None

    def _lock_authority_graph(
        self,
        session,
        claim,
        now,
        *,
        allow_advanced_phase=False,
        allow_stopped=False,
        allow_finalize=False,
        allow_done=False,
    ) -> LockedFrameOptimizeContinuation | None:
        if now is None or claim.owner_scope.operation != _OPERATION:
            return None
        graph = self._lock_batch(session, claim.owner_scope.user_id, claim.owner_scope.task_id)
        locked_now = self._now()
        if graph is None or locked_now is None:
            return None
        progress = lock_frame_optimize_progress(session, graph)
        if progress is None:
            return None
        task = graph.task
        lease = _utc(task.get("lease_until"))
        if not (
            task.get("status") == "processing"
            and task.get("user_id") == claim.owner_scope.user_id
            and task.get("id") == claim.owner_scope.task_id
            and task.get("claimed_by") == claim.worker_instance
            and task.get("claim_token") == claim.token
            and task.get("execution_generation") == claim.execution_generation
            and task.get("user_cancelled_at") is None
            and lease is not None
            and lease > locked_now
            and _same_time(lease, claim.lease_until)
        ):
            return None
        unit = graph.units_by_id.get(claim.unit_id)
        receipt = progress.receipts_by_unit.get(claim.unit_id)
        evidence = progress.evidence_by_unit.get(claim.unit_id)
        work = progress.works_by_unit.get(claim.unit_id)
        fee = progress.fees_by_unit.get(claim.unit_id)
        ledger = progress.ledgers_by_unit.get(claim.unit_id)
        allowed_advanced_phases = {"fee_pending", "delivery_pending", "finalize_pending"}
        if allow_done and claim.phase_version == 2:
            allowed_advanced_phases.add("done")
        if allow_stopped:
            allowed_advanced_phases.update({"stopped_insufficient", "stopped_delivery_failed"})
        allowed_current_phases = {"fee_pending", "delivery_pending"}
        if allow_finalize:
            allowed_current_phases.add("finalize_pending")
        if (
            unit is None
            or receipt is None
            or evidence is None
            or work is None
            or work.get("work_id") != claim.work_id
            or work.get("result_ref") != claim.result_ref
            or work.get("phase_version") not in (
                (claim.phase_version, claim.phase_version + 1)
                if allow_advanced_phase
                else (claim.phase_version,)
            )
            or (
                not allow_advanced_phase
                and work.get("phase") not in allowed_current_phases
            )
            or (
                allow_advanced_phase
                and work.get("phase") not in allowed_advanced_phases
            )
        ):
            return None
        if progress.stopped_insufficient and not allow_stopped and not (
            work.get("phase") == "delivery_pending"
            and fee is not None
            and fee.get("resolution") in {"charged", "no_charge"}
        ):
            return None
        return LockedFrameOptimizeContinuation(
            graph=graph,
            progress=progress,
            unit=dict(unit),
            receipt=dict(receipt),
            evidence=dict(evidence),
            work=dict(work),
            fee=dict(fee) if fee else None,
            ledger=dict(ledger) if ledger else None,
        )

    def _candidate_ids(self, now):
        task = AITaskRow.__table__
        work = LocalContinuationWorkRow.__table__
        with self.session_factory() as session:
            rows = session.execute(
                select(task.c.user_id, task.c.id, work.c.work_id)
                .join(work, work.c.task_id == task.c.id)
                .where(
                    task.c.type == _TASK_TYPE,
                    task.c.status.in_(_ACTIVE),
                    task.c.user_cancelled_at.is_(None),
                    or_(
                        and_(task.c.status.in_(("queued", "reconciling")), task.c.lease_until.is_(None)),
                        and_(task.c.status == "processing", task.c.lease_until <= _db_time(now)),
                    ),
                    work.c.operation == _OPERATION,
                    work.c.policy_version == "frame_optimize_pre_apply_v1",
                    work.c.phase.in_(
                        ("fee_pending", "delivery_pending", "finalize_pending")
                    ),
                )
                .order_by(task.c.created_at, task.c.id, work.c.work_id)
                .limit(128)
            ).all()
        return [(str(row.user_id), str(row.id), str(row.work_id)) for row in rows]

    def _confirm_release(self, claim, expected):
        now, work = expected
        task = AITaskRow.__table__
        work_table = LocalContinuationWorkRow.__table__
        try:
            with self.session_factory() as session:
                task_row = session.execute(
                    select(task).where(
                        task.c.id == claim.owner_scope.task_id,
                        task.c.user_id == claim.owner_scope.user_id,
                        task.c.type == _TASK_TYPE,
                        task.c.status == "reconciling",
                        task.c.claimed_by.is_(None),
                        task.c.claim_token.is_(None),
                        task.c.lease_until.is_(None),
                        task.c.execution_generation == claim.execution_generation,
                        task.c.user_cancelled_at.is_(None),
                        task.c.updated_at == _db_time(now),
                    )
                ).mappings().one_or_none()
                if task_row is None:
                    return False
                row = session.execute(
                    select(work_table).where(
                        work_table.c.work_id == claim.work_id,
                        work_table.c.owner_id == claim.owner_scope.user_id,
                        work_table.c.task_id == claim.owner_scope.task_id,
                        work_table.c.unit_id == claim.unit_id,
                        work_table.c.result_ref == claim.result_ref,
                        work_table.c.phase == work.get("phase"),
                        work_table.c.phase_version == work.get("phase_version"),
                        work_table.c.fee_decision_ref == work.get("fee_decision_ref"),
                    )
                ).mappings().one_or_none()
                return row is not None
        except Exception:
            return False

    def _confirm_renewal(self, expected: ContinuationClaimAuthority):
        try:
            with self.session_factory() as session:
                with session.begin():
                    locked = self._lock_authority_graph(
                        session,
                        expected,
                        self._now(),
                        allow_advanced_phase=True,
                        allow_stopped=True,
                        allow_done=True,
                    )
                    return expected if locked is not None else None
        except Exception:
            return None

    def _lock_batch(self, session, owner_id, task_id):
        return lock_frame_optimize_batch(
            session,
            owner_id,
            task_id,
            crypto=self.crypto,
            proof_authority=self.proof_authority,
        )

    def _signal_for(self, claim, independent_only=False):
        if not isinstance(claim, ContinuationClaimAuthority):
            return None
        with self._signals_lock:
            if independent_only and claim.token not in self._independent_tokens:
                return None
            signal = self._signals.get(claim.token)
        return signal if signal is not None and not signal.is_set() else None

    def _forget_signal(self, token, signal):
        with self._signals_lock:
            if self._signals.get(token) is signal:
                self._signals.pop(token, None)
            self._independent_tokens.discard(token)

    def _unit_for_work(self, progress, work_id):
        return next(
            (unit_id for unit_id, work in progress.works_by_unit.items() if work.get("work_id") == work_id),
            "",
        )

    def _now(self):
        try:
            value = self.db_clock()
        except Exception:
            return None
        return _utc(value)


def _authority(task, work, unit_id, claim):
    return ContinuationClaimAuthority(
        owner_scope=OwnerTaskScope(claim.scope.user_id, claim.scope.task_id, _OPERATION),
        unit_id=unit_id,
        work_id=str(work["work_id"]),
        result_ref=str(work["result_ref"]),
        phase_version=int(work["phase_version"]),
        worker_instance=claim.worker_instance,
        token=claim.token,
        execution_generation=claim.execution_generation,
        lease_until=_utc(task.get("lease_until")),
    )


def _generation_claim_live(task, claim, now):
    return bool(
        task.get("status") == "processing"
        and task.get("id") == claim.scope.task_id
        and task.get("user_id") == claim.scope.user_id
        and task.get("type") == _TASK_TYPE
        and task.get("claimed_by") == claim.worker_instance
        and task.get("claim_token") == claim.token
        and task.get("execution_generation") == claim.execution_generation
        and task.get("user_cancelled_at") is None
        and _utc(task.get("lease_until")) is not None
        and _utc(task.get("lease_until")) > now
        and not claim.local_lost_signal.is_set()
    )


def _has_unit_cancel_marker(graph: LockedFrameOptimizeBatch) -> bool:
    return any(unit.get("user_cancelled_at") is not None for unit in graph.units)


def _lease_available(task, now):
    if task.get("user_cancelled_at") is not None:
        return False
    status = task.get("status")
    lease = _utc(task.get("lease_until"))
    if status in {"queued", "reconciling"}:
        return task.get("claimed_by") is None and task.get("claim_token") is None and lease is None
    return status == "processing" and lease is not None and lease <= now


def _available_clause(table, task, now):
    if task.get("status") in {"queued", "reconciling"}:
        return and_(table.c.claimed_by.is_(None), table.c.claim_token.is_(None), table.c.lease_until.is_(None))
    return or_(table.c.lease_until.is_(None), table.c.lease_until <= _db_time(now))


def _utc(value):
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _db_time(value: datetime):
    return _utc(value).replace(tzinfo=None)


def _same_time(left, right):
    return _utc(left) is not None and _utc(left) == _utc(right)


__all__ = ["LockedFrameOptimizeContinuation", "SqlAlchemyFrameOptimizeContinuationClaims"]
