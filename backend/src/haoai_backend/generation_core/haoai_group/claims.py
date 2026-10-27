"""Fenced SQL leases for the HaoAI three-step video attempt Group."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Event, RLock

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.types import ClaimAuthority, OwnerTaskScope
from haoai_backend.generation_core.base_schema import AITaskRow
from haoai_backend.generation_core.haoai_group.persistence import (
    LockedHaoaiGroupGraph,
    lock_haoai_group_graph,
)

SessionFactory = Callable[[], Session]
DbClock = Callable[[], datetime]
IdFactory = Callable[[], str]
_TASK_TYPE = "video-single"
_OPERATION = "video.single"
_CLAIMABLE_STATUSES = ("queued", "processing", "reconciling")
_DEFAULT_LEASE_SECONDS = 60
_CANDIDATE_PAGE_SIZE = 128


@dataclass(frozen=True, slots=True)
class _Candidate:
    owner_id: str
    task_id: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class _ClaimExpectation:
    owner_id: str
    task_id: str
    instance: str
    token: str
    generation: int
    lease_until: datetime
    request_data: str


@dataclass(frozen=True, slots=True)
class _RenewExpectation:
    prior_lease_until: datetime
    lease_until: datetime


@dataclass(frozen=True, slots=True)
class _ReleaseExpectation:
    status: str
    updated_at: datetime
    user_cancelled_at: datetime | None
    cancellation_reason: str | None
    result: str | None
    request_data: str


@dataclass(slots=True)
class SqlAlchemyHaoaiGroupClaims:
    """Claim only authenticated HaoAI Group graphs under the shared Task lease."""

    session_factory: SessionFactory
    db_clock: DbClock
    id_factory: IdFactory = field(default_factory=lambda: secrets.token_urlsafe)
    lease_seconds: int = _DEFAULT_LEASE_SECONDS
    _signals: dict[str, Event] = field(default_factory=dict, init=False, repr=False)
    _signals_lock: RLock = field(default_factory=RLock, init=False, repr=False)

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

        signal = Event()
        try:
            claim = self._claim_and_commit(instance, token, signal)
        except Exception:
            claim = None
        if claim is not None:
            with self._signals_lock:
                self._signals[token] = signal
        return claim

    def renew_or_revoke(self, claim: ClaimAuthority) -> str:
        signal = self._signal_for(claim)
        if signal is None or signal.is_set():
            return "ownership_lost"
        expected: list[_RenewExpectation] = []
        try:
            renewed_until = self._renew_and_commit(claim, expected)
        except Exception:
            renewed_until = (
                self._confirm_renewal(claim, expected[0]) if expected else None
            )
        if renewed_until is None:
            signal.set()
            return "ownership_lost"
        return "renewed"

    def release_claim(self, claim: ClaimAuthority) -> bool:
        signal = self._signal_for(claim)
        if signal is None:
            return False
        expected: list[_ReleaseExpectation] = []
        try:
            released = self._release_and_commit(claim, expected)
        except Exception:
            released = (
                self._confirm_release(claim, expected[0]) if expected else False
            )
        signal.set()
        with self._signals_lock:
            self._signals.pop(claim.token, None)
        return released

    def _claim_and_commit(self, instance: str, token: str, signal: Event):
        cursor: tuple[datetime, str] | None = None
        while True:
            candidates = self._candidate_ids(cursor)
            if not candidates:
                return None
            for candidate in candidates:
                expected: list[_ClaimExpectation] = []
                try:
                    claim = self._claim_candidate(
                        candidate, instance, token, signal, expected
                    )
                except Exception:
                    claim = (
                        self._confirm_claim(expected[0], signal) if expected else None
                    )
                    if claim is not None:
                        return claim
                    return None
                if claim is not None:
                    return claim
                cursor = (candidate.created_at, candidate.task_id)

    def _candidate_ids(
        self,
        cursor: tuple[datetime, str] | None,
    ) -> list[_Candidate]:
        table = AITaskRow.__table__
        conditions = [
            table.c.type == _TASK_TYPE,
            table.c.status.in_(_CLAIMABLE_STATUSES),
            table.c.user_cancelled_at.is_(None),
        ]
        if cursor is not None:
            created_at, task_id = cursor
            conditions.append(
                or_(
                    table.c.created_at > _database_datetime(created_at),
                    and_(
                        table.c.created_at == _database_datetime(created_at),
                        table.c.id > task_id,
                    ),
                )
            )
        with self.session_factory() as session:
            rows = session.execute(
                select(table.c.user_id, table.c.id, table.c.created_at)
                .where(*conditions)
                .order_by(table.c.created_at, table.c.id)
                .limit(_CANDIDATE_PAGE_SIZE)
            ).all()
        return [
            _Candidate(row.user_id, row.id, _aware_utc(row.created_at))
            for row in rows
        ]

    def _claim_candidate(
        self,
        candidate: _Candidate,
        instance: str,
        token: str,
        signal: Event,
        expected: list[_ClaimExpectation],
    ) -> ClaimAuthority | None:
        with self.session_factory() as session:
            with session.begin():
                graph = lock_haoai_group_graph(
                    session, candidate.owner_id, candidate.task_id
                )
                now = self._server_now()
                if (
                    graph is None
                    or not _group_claim_is_available(graph, now)
                ):
                    return None
                task_table = AITaskRow.__table__
                if session.execute(
                    select(task_table.c.id)
                    .where(task_table.c.claim_token == token)
                    .limit(1)
                ).scalar_one_or_none() is not None:
                    return None
                generation = graph.task.get("execution_generation")
                if type(generation) is not int or generation < 0:
                    return None
                generation += 1
                lease_until = now + timedelta(seconds=self.lease_seconds)
                result = session.execute(
                    update(task_table)
                    .where(*_claim_stage_predicates(graph))
                    .values(
                        status="processing",
                        claimed_by=instance,
                        claim_token=token,
                        execution_generation=generation,
                        lease_until=_database_datetime(lease_until),
                        updated_at=_database_datetime(now),
                    )
                )
                if result.rowcount != 1:
                    return None
                expected.append(
                    _ClaimExpectation(
                        owner_id=graph.owner_id,
                        task_id=graph.task_id,
                        instance=instance,
                        token=token,
                        generation=generation,
                        lease_until=lease_until,
                        request_data=str(graph.task["request_data"]),
                    )
                )
                session.flush()
                return ClaimAuthority(
                    scope=OwnerTaskScope(graph.owner_id, graph.task_id, _OPERATION),
                    worker_instance=instance,
                    token=token,
                    execution_generation=generation,
                    lease_until=lease_until,
                    local_lost_signal=signal,
                )

    def _renew_and_commit(
        self,
        claim: ClaimAuthority,
        expected: list[_RenewExpectation],
    ) -> datetime | None:
        with self.session_factory() as session:
            with session.begin():
                graph = _lock_claimed_graph(session, claim)
                now = self._server_now()
                if graph is None or not _claim_live(graph.task, claim, now):
                    return None
                old_lease = _aware_utc(graph.task["lease_until"])
                lease_until = now + timedelta(seconds=self.lease_seconds)
                if lease_until <= old_lease:
                    return None
                task_table = AITaskRow.__table__
                result = session.execute(
                    update(task_table)
                    .where(*_renewal_predicates(graph.task, claim, now))
                    .values(
                        lease_until=_database_datetime(lease_until),
                        updated_at=_database_datetime(now),
                    )
                )
                if result.rowcount != 1:
                    return None
                expected.append(_RenewExpectation(old_lease, lease_until))
                return lease_until

    def _release_and_commit(
        self,
        claim: ClaimAuthority,
        expected: list[_ReleaseExpectation],
    ) -> bool:
        with self.session_factory() as session:
            with session.begin():
                if not isinstance(claim, ClaimAuthority) or claim.scope.operation != _OPERATION:
                    return False
                graph = lock_haoai_group_graph(
                    session, claim.scope.user_id, claim.scope.task_id
                )
                if graph is None or not _claim_matches(graph.task, claim):
                    return False
                status = (
                    "queued"
                    if graph.task.get("status") == "processing"
                    and graph.task.get("user_cancelled_at") is None
                    else graph.task.get("status")
                )
                if not isinstance(status, str):
                    return False
                now = self._server_now()
                write = _ReleaseExpectation(
                    status=status,
                    updated_at=now,
                    user_cancelled_at=graph.task.get("user_cancelled_at"),
                    cancellation_reason=graph.task.get("cancellation_reason"),
                    result=graph.task.get("result"),
                    request_data=str(graph.task["request_data"]),
                )
                result = session.execute(
                    update(AITaskRow.__table__)
                    .where(*_release_predicates(graph.task, claim))
                    .values(
                        status=write.status,
                        claimed_by=None,
                        claim_token=None,
                        lease_until=None,
                        updated_at=_database_datetime(now),
                    )
                )
                if result.rowcount != 1:
                    return False
                expected.append(write)
                return True

    def _confirm_claim(
        self,
        expected: _ClaimExpectation,
        signal: Event,
    ) -> ClaimAuthority | None:
        try:
            with self.session_factory() as session:
                with session.begin():
                    graph = lock_haoai_group_graph(
                        session, expected.owner_id, expected.task_id
                    )
                    now = self._server_now()
                    lease = graph.task.get("lease_until") if graph is not None else None
                    if (
                        graph is None
                        or graph.task.get("status") != "processing"
                        or graph.task.get("claimed_by") != expected.instance
                        or graph.task.get("claim_token") != expected.token
                        or graph.task.get("execution_generation") != expected.generation
                        or graph.task.get("lease_until") is None
                        or _aware_utc(graph.task["lease_until"]) != expected.lease_until
                        or not isinstance(lease, datetime)
                        or _aware_utc(lease) <= now
                        or graph.task.get("request_data") != expected.request_data
                        or not _group_claim_is_active(graph, now)
                    ):
                        return None
                    return ClaimAuthority(
                        scope=OwnerTaskScope(
                            expected.owner_id, expected.task_id, _OPERATION
                        ),
                        worker_instance=expected.instance,
                        token=expected.token,
                        execution_generation=expected.generation,
                        lease_until=expected.lease_until,
                        local_lost_signal=signal,
                    )
        except Exception:
            return None

    def _confirm_renewal(
        self,
        claim: ClaimAuthority,
        expected: _RenewExpectation,
    ) -> datetime | None:
        try:
            with self.session_factory() as session:
                with session.begin():
                    graph = _lock_claimed_graph(session, claim)
                    now = self._server_now()
                    if (
                        graph is None
                        or not _claim_live(graph.task, claim, now)
                        or _aware_utc(graph.task["lease_until"]) != expected.lease_until
                        or expected.lease_until <= expected.prior_lease_until
                    ):
                        return None
                    return expected.lease_until
        except Exception:
            return None

    def _confirm_release(
        self,
        claim: ClaimAuthority,
        expected: _ReleaseExpectation,
    ) -> bool:
        try:
            with self.session_factory() as session:
                with session.begin():
                    graph = lock_haoai_group_graph(
                        session, claim.scope.user_id, claim.scope.task_id
                    )
                    if graph is None:
                        return False
                    task = graph.task
                    return bool(
                        task.get("status") == expected.status
                        and task.get("claimed_by") is None
                        and task.get("claim_token") is None
                        and task.get("lease_until") is None
                        and task.get("execution_generation") == claim.execution_generation
                        and task.get("user_cancelled_at") == expected.user_cancelled_at
                        and task.get("cancellation_reason") == expected.cancellation_reason
                        and task.get("result") == expected.result
                        and task.get("request_data") == expected.request_data
                        and _same_time(task.get("updated_at"), expected.updated_at)
                    )
        except Exception:
            return False

    def _server_now(self) -> datetime:
        value = self.db_clock()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() != timezone.utc.utcoffset(value)
        ):
            raise ValueError("database clock must return an aware UTC datetime")
        return value.astimezone(timezone.utc)

    def _signal_for(self, claim: ClaimAuthority) -> Event | None:
        if not isinstance(claim, ClaimAuthority) or claim.scope.operation != _OPERATION:
            return None
        with self._signals_lock:
            signal = self._signals.get(claim.token)
        return signal if signal is claim.local_lost_signal else None


def _lock_claimed_graph(
    session: Session,
    claim: ClaimAuthority,
) -> LockedHaoaiGroupGraph | None:
    if (
        not isinstance(claim, ClaimAuthority)
        or claim.scope.operation != _OPERATION
        or claim.local_lost_signal.is_set()
    ):
        return None
    return lock_haoai_group_graph(
        session, claim.scope.user_id, claim.scope.task_id
    )


def _group_claim_is_available(
    graph: LockedHaoaiGroupGraph,
    now: datetime,
) -> bool:
    return _group_claim_is_active(graph, now) and _lease_is_available(graph.task, now)


def _group_claim_is_active(
    graph: LockedHaoaiGroupGraph,
    now: datetime,
) -> bool:
    task = graph.task
    unit = graph.unit
    group = graph.group
    active_step = graph.steps[graph.binding.attempt_index]
    return all(
        (
            task.get("type") == _TASK_TYPE,
            task.get("status") in _CLAIMABLE_STATUSES,
            task.get("user_cancelled_at") is None,
            task.get("billing_status") == unit.get("billing_status"),
            task.get("credit_cost") == unit.get("quoted_amount"),
            unit.get("user_cancelled_at") is None,
            unit.get("success_confirmed_at") is None,
            unit.get("billing_status") in {"charged", "no_charge"},
            unit.get("result_status") in {"pending", "unknown"},
            group.get("status") in {"prepared", "active", "pending_verification"},
            group.get("sticky_policy_stop_event_ref") is None,
            graph.binding.attempt_index == group.get("active_attempt"),
            active_step.get("execution_status")
            in {"prepared", "submitting", "submitted", "reconciling"},
            now.tzinfo is not None,
        )
    )


def _lease_is_available(task: dict[str, object], now: datetime) -> bool:
    if task.get("user_cancelled_at") is not None:
        return False
    lease = task.get("lease_until")
    if task.get("status") in {"queued", "processing", "reconciling"}:
        return lease is None or _aware_utc(lease) <= now
    return False


def _claim_live(task: dict[str, object], claim: ClaimAuthority, now: datetime) -> bool:
    lease = task.get("lease_until")
    return bool(
        _claim_matches(task, claim)
        and task.get("status") in {"processing", "reconciling"}
        and task.get("user_cancelled_at") is None
        and isinstance(lease, datetime)
        and _aware_utc(lease) > now
        and not claim.local_lost_signal.is_set()
    )


def _claim_matches(task: dict[str, object], claim: ClaimAuthority) -> bool:
    return all(
        (
            task.get("id") == claim.scope.task_id,
            task.get("user_id") == claim.scope.user_id,
            task.get("type") == _TASK_TYPE,
            claim.scope.operation == _OPERATION,
            task.get("claimed_by") == claim.worker_instance,
            task.get("claim_token") == claim.token,
            task.get("execution_generation") == claim.execution_generation,
        )
    )


def _claim_stage_predicates(graph: LockedHaoaiGroupGraph):
    table = AITaskRow.__table__
    task = graph.task
    return (
        table.c.id == graph.task_id,
        table.c.user_id == graph.owner_id,
        table.c.type == _TASK_TYPE,
        table.c.status == task.get("status"),
        table.c.execution_generation == task.get("execution_generation"),
        table.c.user_cancelled_at.is_(None),
        _nullable_predicate(table.c.claimed_by, task.get("claimed_by")),
        _nullable_predicate(table.c.claim_token, task.get("claim_token")),
        _nullable_predicate(table.c.lease_until, task.get("lease_until")),
        table.c.request_data == task.get("request_data"),
    )


def _renewal_predicates(
    task: dict[str, object], claim: ClaimAuthority, now: datetime
):
    table = AITaskRow.__table__
    return (
        table.c.id == claim.scope.task_id,
        table.c.user_id == claim.scope.user_id,
        table.c.type == _TASK_TYPE,
        table.c.status.in_(("processing", "reconciling")),
        table.c.user_cancelled_at.is_(None),
        table.c.claimed_by == claim.worker_instance,
        table.c.claim_token == claim.token,
        table.c.execution_generation == claim.execution_generation,
        table.c.lease_until == _database_datetime(_aware_utc(task["lease_until"])),
        table.c.lease_until > _database_datetime(now),
        table.c.request_data == task.get("request_data"),
    )


def _release_predicates(task: dict[str, object], claim: ClaimAuthority):
    table = AITaskRow.__table__
    return (
        table.c.id == claim.scope.task_id,
        table.c.user_id == claim.scope.user_id,
        table.c.type == _TASK_TYPE,
        table.c.status == task.get("status"),
        _nullable_predicate(table.c.user_cancelled_at, task.get("user_cancelled_at")),
        table.c.claimed_by == claim.worker_instance,
        table.c.claim_token == claim.token,
        table.c.execution_generation == claim.execution_generation,
        _nullable_predicate(table.c.lease_until, task.get("lease_until")),
        table.c.request_data == task.get("request_data"),
    )


def _nullable_predicate(column, value: object):
    return column.is_(None) if value is None else column == value


def _same_time(left: object, right: datetime) -> bool:
    return isinstance(left, datetime) and _aware_utc(left) == right


def _aware_utc(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("persisted claim timestamp is invalid")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _database_datetime(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)


__all__ = ("SqlAlchemyHaoaiGroupClaims",)
