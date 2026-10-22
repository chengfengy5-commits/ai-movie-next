"""SQL-backed task leases for complete registered frame.optimize batches."""

from __future__ import annotations

import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.types import ClaimAuthority, OwnerTaskScope
from haoai_backend.generation_core.base_schema import AITaskRow, UserRow
from haoai_backend.generation_core.frame_optimize_execution import (
    has_any_intent,
    initial_generation_candidate,
    lock_frame_optimize_batch,
    valid_intent_projection,
)
from haoai_backend.generation_core.frame_optimize_progress import lock_frame_optimize_progress
from haoai_backend.generation_credentials.domain import CredentialCrypto, HmacProofAuthority

_OPERATION = "frame.optimize"
_TASK_TYPE = "batch-optimize"
_ACTIVE_STATUSES = ("queued", "processing", "reconciling")
_DEFAULT_LEASE_SECONDS = 60

SessionFactory = Callable[[], Session]
DbClock = Callable[[], datetime]
IdFactory = Callable[[], str]


@dataclass(frozen=True, slots=True)
class _ClaimWrite:
    owner_id: str
    task_id: str
    worker_instance: str
    token: str
    generation: int
    lease_until: datetime
    request_data: str

    def authority(self, signal: threading.Event) -> ClaimAuthority:
        return ClaimAuthority(
            scope=OwnerTaskScope(self.owner_id, self.task_id, _OPERATION),
            worker_instance=self.worker_instance,
            token=self.token,
            execution_generation=self.generation,
            lease_until=self.lease_until,
            local_lost_signal=signal,
        )


@dataclass(frozen=True, slots=True)
class _RenewWrite:
    prior_lease_until: datetime
    lease_until: datetime


@dataclass(frozen=True, slots=True)
class _ReleaseWrite:
    status: str
    updated_at: datetime
    user_cancelled_at: datetime | None


@dataclass(slots=True)
class SqlAlchemyFrameOptimizeClaims:
    """Claim only an intact initial batch and fence every lease transition."""

    session_factory: SessionFactory
    db_clock: DbClock
    crypto: CredentialCrypto
    proof_authority: HmacProofAuthority
    id_factory: IdFactory = field(default=lambda: secrets.token_urlsafe(32))
    lease_seconds: int = _DEFAULT_LEASE_SECONDS
    _signals: dict[str, threading.Event] = field(default_factory=dict, init=False, repr=False)
    _signals_lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        if not callable(self.session_factory) or not callable(self.db_clock):
            raise TypeError("claim session and database clock must be explicit")
        if not callable(self.id_factory):
            raise TypeError("claim token factory must be callable")
        if type(self.lease_seconds) is not int or self.lease_seconds <= 0:
            raise ValueError("claim lease must be a positive number of seconds")

    def claim_next(self, task_type: str, instance: str) -> ClaimAuthority | None:
        if (
            task_type != _TASK_TYPE
            or not isinstance(instance, str)
            or not instance
            or len(instance) > 64
        ):
            return None
        try:
            token = self.id_factory()
        except Exception:
            return None
        if not isinstance(token, str) or not token or len(token) > 64:
            return None

        signal = threading.Event()
        staged: list[_ClaimWrite] = []
        try:
            authority = self._claim_and_commit(instance, token, signal, staged)
        except Exception:
            authority = self._confirm_claim(staged[0], signal) if staged else None
        if authority is not None:
            with self._signals_lock:
                self._signals[token] = signal
        return authority

    def renew_or_revoke(self, claim: ClaimAuthority) -> str:
        signal = self._signal_for(claim)
        if signal is None or signal.is_set():
            return "ownership_lost"
        staged: list[_RenewWrite] = []
        try:
            renewed = self._renew_and_commit(claim, staged)
        except Exception:
            renewed = self._confirm_renewal(claim, staged[0] if staged else None)
        if renewed is None:
            signal.set()
            return "ownership_lost"
        return "renewed"

    def release_claim(self, claim: ClaimAuthority) -> bool:
        signal = self._signal_for(claim)
        if signal is None:
            return False
        staged: list[_ReleaseWrite] = []
        try:
            released = self._release_and_commit(claim, staged)
        except Exception:
            released = self._confirm_release(claim, staged[0] if staged else None)
        signal.set()
        with self._signals_lock:
            self._signals.pop(claim.token, None)
        return released is not None

    def _claim_and_commit(
        self,
        instance: str,
        token: str,
        signal: threading.Event,
        staged: list[_ClaimWrite],
    ) -> ClaimAuthority | None:
        cursor: tuple[datetime, str] | None = None
        while True:
            candidates = self._candidate_ids(cursor)
            if not candidates:
                return None
            for owner_id, task_id, created_at in candidates:
                authority = self._claim_candidate(
                    owner_id, task_id, instance, token, signal, staged
                )
                if authority is not None:
                    return authority
                cursor = (created_at, task_id)

    def _candidate_ids(self, cursor: tuple[datetime, str] | None):
        table = AITaskRow.__table__
        conditions = [
            table.c.type == _TASK_TYPE,
            table.c.status.in_(_ACTIVE_STATUSES),
            table.c.user_cancelled_at.is_(None),
        ]
        if cursor is not None:
            created_at, task_id = cursor
            conditions.append(
                or_(
                    table.c.created_at > _db_datetime(created_at),
                    and_(table.c.created_at == _db_datetime(created_at), table.c.id > task_id),
                )
            )
        with self.session_factory() as session:
            rows = session.execute(
                select(table.c.user_id, table.c.id, table.c.created_at)
                .where(*conditions)
                .order_by(table.c.created_at, table.c.id)
                .limit(128)
            ).all()
        return [(str(row.user_id), str(row.id), _aware_utc(row.created_at)) for row in rows]

    def _claim_candidate(
        self,
        owner_id: str,
        task_id: str,
        instance: str,
        token: str,
        signal: threading.Event,
        staged: list[_ClaimWrite],
    ) -> ClaimAuthority | None:
        with self.session_factory() as session:
            with session.begin():
                graph = lock_frame_optimize_batch(
                    session,
                    owner_id,
                    task_id,
                    crypto=self.crypto,
                    proof_authority=self.proof_authority,
                )
                now = self._server_now()
                progress = (
                    lock_frame_optimize_progress(session, graph)
                    if graph is not None
                    else None
                )
                if (
                    graph is None
                    or progress is None
                    or not _lease_available(graph.task, now)
                    or not initial_generation_candidate(graph)
                    or not valid_intent_projection(graph)
                ):
                    return None
                task_table = AITaskRow.__table__
                if session.execute(
                    select(task_table.c.id).where(task_table.c.claim_token == token).limit(1)
                ).scalar_one_or_none() is not None:
                    return None
                generation = int(graph.task["execution_generation"]) + 1
                lease_until = now + timedelta(seconds=self.lease_seconds)
                condition = _prior_lease_condition(task_table, graph.task)
                result = session.execute(
                    update(task_table)
                    .where(
                        task_table.c.id == task_id,
                        task_table.c.user_id == owner_id,
                        task_table.c.type == _TASK_TYPE,
                        task_table.c.status == graph.task["status"],
                        task_table.c.execution_generation == graph.task["execution_generation"],
                        task_table.c.request_data == graph.task["request_data"],
                        task_table.c.user_cancelled_at.is_(None),
                        condition,
                    )
                    .values(
                        status="processing",
                        claimed_by=instance,
                        claim_token=token,
                        execution_generation=generation,
                        lease_until=_db_datetime(lease_until),
                        updated_at=_db_datetime(now),
                    )
                )
                if result.rowcount != 1:
                    return None
                write = _ClaimWrite(
                    owner_id,
                    task_id,
                    instance,
                    token,
                    generation,
                    lease_until,
                    str(graph.task["request_data"]),
                )
                staged.append(write)
                session.flush()
                return write.authority(signal)

    def _renew_and_commit(self, claim: ClaimAuthority, staged: list[_RenewWrite]):
        with self.session_factory() as session:
            with session.begin():
                graph = _lock_current_graph(session, claim, self)
                now = self._server_now()
                if graph is None or not _claim_live(graph.task, claim, now):
                    return None
                lease = now + timedelta(seconds=self.lease_seconds)
                old = _aware_utc(graph.task["lease_until"])
                if lease <= old:
                    return None
                table = AITaskRow.__table__
                result = session.execute(
                    update(table)
                    .where(
                        table.c.id == claim.scope.task_id,
                        table.c.user_id == claim.scope.user_id,
                        table.c.type == _TASK_TYPE,
                        table.c.status == "processing",
                        table.c.claimed_by == claim.worker_instance,
                        table.c.claim_token == claim.token,
                        table.c.execution_generation == claim.execution_generation,
                        table.c.user_cancelled_at.is_(None),
                        table.c.lease_until == _db_datetime(old),
                        table.c.lease_until > _db_datetime(now),
                    )
                    .values(lease_until=_db_datetime(lease), updated_at=_db_datetime(now))
                )
                if result.rowcount != 1:
                    return None
                staged.append(_RenewWrite(old, lease))
                return lease

    def _release_and_commit(self, claim: ClaimAuthority, staged: list[_ReleaseWrite]):
        with self.session_factory() as session:
            with session.begin():
                graph = _lock_current_graph(session, claim, self)
                now = self._server_now()
                if graph is None or not _claim_live(graph.task, claim, now):
                    return None
                if (
                    graph.task.get("status") != "processing"
                    or graph.task.get("user_cancelled_at") is not None
                ):
                    return None
                has_intent = has_any_intent(graph)
                if not valid_intent_projection(graph):
                    return None
                progress = lock_frame_optimize_progress(session, graph)
                if progress is None:
                    return None
                has_ready_finalization = any(
                    work.get("phase") == "finalize_pending"
                    and work.get("phase_version") == 2
                    for work in progress.works_by_unit.values()
                )
                status = (
                    "queued"
                    if not has_intent or has_ready_finalization
                    else "reconciling"
                )
                table = AITaskRow.__table__
                result = session.execute(
                    update(table)
                    .where(
                        table.c.id == claim.scope.task_id,
                        table.c.user_id == claim.scope.user_id,
                        table.c.type == _TASK_TYPE,
                        table.c.status == "processing",
                        table.c.claimed_by == claim.worker_instance,
                        table.c.claim_token == claim.token,
                        table.c.execution_generation == claim.execution_generation,
                        table.c.user_cancelled_at.is_(None),
                        table.c.lease_until == _db_datetime(_aware_utc(graph.task["lease_until"])),
                        table.c.lease_until > _db_datetime(now),
                    )
                    .values(
                        status=status,
                        claimed_by=None,
                        claim_token=None,
                        lease_until=None,
                        updated_at=_db_datetime(now),
                    )
                )
                if result.rowcount != 1:
                    return None
                staged.append(_ReleaseWrite(status, now, None))
                return status

    def _confirm_claim(self, write: _ClaimWrite, signal: threading.Event):
        try:
            with self.session_factory() as session:
                with session.begin():
                    graph = lock_frame_optimize_batch(
                        session,
                        write.owner_id,
                        write.task_id,
                        crypto=self.crypto,
                        proof_authority=self.proof_authority,
                    )
                    now = self._server_now()
                    if (
                        graph is None
                        or graph.task.get("status") != "processing"
                        or graph.task.get("claimed_by") != write.worker_instance
                        or graph.task.get("claim_token") != write.token
                        or graph.task.get("execution_generation") != write.generation
                        or graph.task.get("lease_until") is None
                        or not _same_time(graph.task.get("lease_until"), write.lease_until)
                        or graph.task.get("request_data") != write.request_data
                        or not _claim_live(graph.task, write.authority(signal), now)
                        or lock_frame_optimize_progress(session, graph) is None
                        or not initial_generation_candidate(graph)
                        or not valid_intent_projection(graph)
                    ):
                        return None
                    return write.authority(signal)
        except Exception:
            return None

    def _confirm_renewal(self, claim: ClaimAuthority, write: _RenewWrite | None):
        if write is None or write.lease_until <= write.prior_lease_until:
            return None
        try:
            with self.session_factory() as session:
                with session.begin():
                    graph = _lock_current_graph(session, claim, self)
                    now = self._server_now()
                    if (
                        graph is None
                        or not _claim_live(graph.task, claim, now)
                        or not _same_time(graph.task.get("lease_until"), write.lease_until)
                    ):
                        return None
                    return write.lease_until
        except Exception:
            return None

    def _confirm_release(self, claim: ClaimAuthority, write: _ReleaseWrite | None):
        if write is None:
            return None
        try:
            with self.session_factory() as session:
                with session.begin():
                    graph = _lock_current_graph(session, claim, self)
                    if graph is None:
                        return None
                    task = graph.task
                    progress = lock_frame_optimize_progress(session, graph)
                    if progress is None:
                        return None
                    has_ready_finalization = any(
                        work.get("phase") == "finalize_pending"
                        and work.get("phase_version") == 2
                        for work in progress.works_by_unit.values()
                    )
                    release_state_matches = (
                        has_any_intent(graph)
                        if write.status == "reconciling"
                        else not has_any_intent(graph) or has_ready_finalization
                    )
                    return write.status if all(
                        (
                            task.get("status") == write.status,
                            task.get("claimed_by") is None,
                            task.get("claim_token") is None,
                            task.get("lease_until") is None,
                            task.get("execution_generation") == claim.execution_generation,
                            task.get("user_cancelled_at") is None,
                            _same_time(task.get("updated_at"), write.updated_at),
                            release_state_matches,
                        )
                    ) else None
        except Exception:
            return None

    def _signal_for(self, claim: ClaimAuthority) -> threading.Event | None:
        if not isinstance(claim, ClaimAuthority) or claim.scope.operation != _OPERATION:
            return None
        with self._signals_lock:
            signal = self._signals.get(claim.token)
        return signal if signal is claim.local_lost_signal else None

    def _server_now(self) -> datetime:
        value = self.db_clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("database clock must return an aware datetime")
        return _aware_utc(value)


def _lock_current_graph(session: Session, claim: ClaimAuthority, adapter):
    if (
        not isinstance(claim, ClaimAuthority)
        or claim.scope.operation != _OPERATION
        or claim.local_lost_signal.is_set()
    ):
        return None
    # Lease transitions must wait behind a fee/result transaction. The batch
    # loader uses SKIP LOCKED for worker discovery, which would otherwise turn
    # ordinary contention into a false ownership loss and stop sibling work.
    user_id = session.execute(
        select(UserRow.id)
        .where(UserRow.id == claim.scope.user_id)
        .with_for_update()
    ).scalar_one_or_none()
    if user_id is None:
        return None
    graph = lock_frame_optimize_batch(
        session,
        claim.scope.user_id,
        claim.scope.task_id,
        crypto=adapter.crypto,
        proof_authority=adapter.proof_authority,
    )
    if (
        graph is None
        or not valid_intent_projection(graph)
        or lock_frame_optimize_progress(session, graph) is None
    ):
        return None
    return graph


def _claim_matches(task, claim: ClaimAuthority) -> bool:
    return all(
        (
            task.get("id") == claim.scope.task_id,
            task.get("user_id") == claim.scope.user_id,
            task.get("type") == _TASK_TYPE,
            task.get("claimed_by") == claim.worker_instance,
            task.get("claim_token") == claim.token,
            task.get("execution_generation") == claim.execution_generation,
            claim.scope.operation == _OPERATION,
        )
    )


def _claim_live(task, claim: ClaimAuthority, now: datetime) -> bool:
    lease = task.get("lease_until")
    return bool(
        _claim_matches(task, claim)
        and task.get("status") == "processing"
        and task.get("user_cancelled_at") is None
        and isinstance(lease, datetime)
        and _aware_utc(lease) > now
        and not claim.local_lost_signal.is_set()
    )


def _lease_available(task, now: datetime) -> bool:
    if task.get("user_cancelled_at") is not None:
        return False
    lease = task.get("lease_until")
    if task.get("status") == "queued":
        return lease is None or _aware_utc(lease) <= now
    return (
        task.get("status") in {"processing", "reconciling"}
        and lease is not None
        and _aware_utc(lease) <= now
    )


def _prior_lease_condition(table, task):
    lease = task.get("lease_until")
    if lease is None:
        return table.c.lease_until.is_(None)
    return table.c.lease_until == _db_datetime(_aware_utc(lease))


def _same_time(left, right) -> bool:
    return _aware_utc(left) == _aware_utc(right)


def _aware_utc(value) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("expected datetime")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _db_datetime(value: datetime) -> datetime:
    return _aware_utc(value).replace(tzinfo=None)


__all__ = ["SqlAlchemyFrameOptimizeClaims"]
