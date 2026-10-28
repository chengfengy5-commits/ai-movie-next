"""Fenced SQL leases for the HaoAI three-step video attempt Group."""

from __future__ import annotations

import secrets
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Event, RLock

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.types import ClaimAuthority, OwnerTaskScope
from haoai_backend.generation_core.base_schema import AITaskRow, ResultEvidenceRow
from haoai_backend.generation_core.haoai_group.evidence import (
    verified_group_observation_fact,
)
from haoai_backend.generation_core.haoai_group.persistence import (
    LockedHaoaiGroupGraph,
    lock_haoai_group_graph,
)
from haoai_backend.generation_core.schema import PollReservationRow
from haoai_backend.generation_credentials.domain import HmacProofAuthority
from haoai_backend.generation_runtime.registry import HAOAI_ATTEMPT_GROUP

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
    proof_authority: HmacProofAuthority | None = None
    _signals: dict[str, Event] = field(default_factory=dict, init=False, repr=False)
    _signals_lock: RLock = field(default_factory=RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        if not callable(self.session_factory) or not callable(self.db_clock):
            raise TypeError("claim session and database clock must be explicit")
        if not callable(self.id_factory):
            raise TypeError("claim token factory must be callable")
        if type(self.lease_seconds) is not int or self.lease_seconds <= 0:
            raise ValueError("claim lease must be a positive number of seconds")
        if self.proof_authority is not None and type(self.proof_authority) is not HmacProofAuthority:
            raise TypeError("Group recovery proof authority must be authenticated")

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
                    or not _group_claim_is_available(
                        session, graph, now, self.proof_authority
                    )
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
                        or not _group_claim_is_active(
                            session, graph, now, self.proof_authority
                        )
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
    session: Session,
    graph: LockedHaoaiGroupGraph,
    now: datetime,
    proof_authority: HmacProofAuthority | None,
) -> bool:
    return _group_claim_is_active(
        session, graph, now, proof_authority
    ) and _lease_is_available(graph.task, now)


def _group_claim_is_active(
    session: Session,
    graph: LockedHaoaiGroupGraph,
    now: datetime,
    proof_authority: HmacProofAuthority | None,
) -> bool:
    task = graph.task
    unit = graph.unit
    group = graph.group
    active_step = graph.steps[graph.binding.attempt_index]
    execution_status = active_step.get("execution_status")
    step_claimable = execution_status in {"prepared", "submitting", "submitted", "reconciling"}
    if execution_status == "result_observed":
        step_claimable = _has_signed_completed_observation(
            session, graph, active_step, proof_authority
        )
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
            step_claimable,
            now.tzinfo is not None,
        )
    )


def _has_signed_completed_observation(
    session: Session,
    graph: LockedHaoaiGroupGraph,
    step: dict[str, object],
    proof_authority: HmacProofAuthority | None,
) -> bool:
    return (
        _signed_observation_status(session, graph, step, proof_authority)
        == "completed"
    )


def _has_signed_failed_observation(
    session: Session,
    graph: LockedHaoaiGroupGraph,
    step: dict[str, object],
    proof_authority: HmacProofAuthority | None,
) -> bool:
    return (
        _signed_observation_status(session, graph, step, proof_authority)
        == "failed"
    )


def _signed_observation_status(
    session: Session,
    graph: LockedHaoaiGroupGraph,
    step: dict[str, object],
    proof_authority: HmacProofAuthority | None,
) -> str | None:
    if type(proof_authority) is not HmacProofAuthority:
        return None
    submissions = tuple(
        row
        for row in graph.external_submissions
        if row.get("step_id") == step.get("id")
    )
    submission_status = (
        "failed" if step.get("execution_status") == "failed" else "submitted"
    )
    allowed_step_statuses = (
        {"failed"}
        if submission_status == "failed"
        else {"submitted", "reconciling", "result_observed"}
    )
    if (
        len(submissions) != 1
        or submissions[0].get("submission_status") != submission_status
        or step.get("execution_status") not in allowed_step_statuses
        or not isinstance(submissions[0].get("external_task_id"), str)
    ):
        return None
    reservation = session.execute(
        select(PollReservationRow.__table__)
        .where(
            PollReservationRow.__table__.c.unit_id == graph.binding.unit_id,
            PollReservationRow.__table__.c.group_key == graph.binding.group_key,
            PollReservationRow.__table__.c.step_id == step.get("id"),
            PollReservationRow.__table__.c.state == "accepted",
        )
        .order_by(PollReservationRow.__table__.c.sequence.desc())
        .limit(1)
    ).mappings().one_or_none()
    if reservation is None or not isinstance(reservation.get("observation_ref"), str):
        return None
    evidence = session.execute(
        select(ResultEvidenceRow.__table__).where(
            ResultEvidenceRow.__table__.c.id == reservation.get("observation_ref"),
            ResultEvidenceRow.__table__.c.evidence_key
            == f"haoai-poll-v1:{reservation.get('reservation_id')}",
            ResultEvidenceRow.__table__.c.step_id == step.get("id"),
            ResultEvidenceRow.__table__.c.submission_id == submissions[0].get("id"),
        )
    ).mappings().one_or_none()
    if evidence is None:
        return None
    fact = verified_group_observation_fact(evidence, proof_authority)
    if not isinstance(fact, dict):
        return None
    try:
        received = datetime.fromisoformat(str(fact.get("received_at")))
        if received.tzinfo is None:
            return None
        received_utc = received.astimezone(timezone.utc)
        timeout = _aware_utc(reservation.get("timeout_at"))
        deadline = _aware_utc(graph.unit.get("deadline_at"))
        reserved_at = _aware_utc(reservation.get("reserved_at"))
    except (TypeError, ValueError):
        return None
    fact_source = fact.get("source_fact")
    source_status = fact.get("source_status")
    verified_status = (
        "completed"
        if fact_source == "source_completed" and source_status in {"completed", "done"}
        else "failed"
        if fact_source == "source_failed" and source_status == "failed"
        else None
    )
    valid = bool(
        verified_status is not None
        and fact.get("transport") == "provider_response"
        and reservation.get("attempt_id") == graph.binding.step_id
        and reservation.get("step_id") == graph.binding.step_id
        and reservation.get("submission_id") == submissions[0].get("id")
        and reservation.get("external_id") == submissions[0].get("external_task_id")
        and reservation.get("payload_digest") == graph.binding.request_digest
        and type(reservation.get("sequence")) is int
        and evidence.get("source") == "worker"
        and evidence.get("payload_digest") == hashlib.sha256(
            json.dumps(
                fact,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        and evidence.get("result_digest") == fact.get("body_sha256")
        and fact.get("schema") == "haoai_poll_observation_v1"
        and fact.get("policy_version") == HAOAI_ATTEMPT_GROUP.policy_version
        and fact.get("registry_version") == HAOAI_ATTEMPT_GROUP.registry_version
        and fact.get("owner_id") == graph.owner_id
        and fact.get("task_id") == graph.task_id
        and fact.get("unit_id") == graph.binding.unit_id
        and fact.get("group_key") == graph.binding.group_key
        and fact.get("step_id") == graph.binding.step_id
        and fact.get("submission_id") == submissions[0].get("id")
        and fact.get("attempt_id") == graph.binding.step_id
        and fact.get("external_id") == submissions[0].get("external_task_id")
        and fact.get("request_digest") == graph.binding.request_digest
        and fact.get("reservation_id") == reservation.get("reservation_id")
        and fact.get("sequence") == reservation.get("sequence")
        and fact.get("reservation_token_digest")
        == hashlib.sha256(str(reservation.get("token", "")).encode("utf-8")).hexdigest()
        and evidence.get("received_at") == _database_datetime(received_utc)
        and received_utc < timeout
        and received_utc < deadline
        and reserved_at < timeout
    )
    return verified_status if valid else None


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
