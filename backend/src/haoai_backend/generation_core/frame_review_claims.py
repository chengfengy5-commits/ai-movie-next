"""SQL-backed claim fencing for the registered frame.review graph."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Event, RLock

from sqlalchemy import and_, exists, or_, select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.types import ClaimAuthority, OwnerTaskScope
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
    TaskSubmissionRow,
    UserRow,
)

_TASK_TYPE = "ai-review"
_OPERATION = "frame.review"
_GRAPH_VERSION = "frame-review-v1"
_TASK_STATUSES = ("queued", "processing", "reconciling")
_STEP_SNAPSHOT_KEYS = frozenset(
    {
        "server_attempt_id",
        "prepared_payload_id",
        "protected_ref",
        "snapshot_digest",
        "manifest_snapshot_digest",
    }
)
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_DEFAULT_LEASE_SECONDS = 60

SessionFactory = Callable[[], Session]
DbClock = Callable[[], datetime]
IdFactory = Callable[[], str]


@dataclass(frozen=True, slots=True)
class _ClaimExpectation:
    owner_id: str
    task_id: str
    instance: str
    token: str
    generation: int
    lease_until: datetime
    request_data: str

    def authority(self, signal: Event) -> ClaimAuthority:
        return ClaimAuthority(
            scope=OwnerTaskScope(
                user_id=self.owner_id,
                task_id=self.task_id,
                operation=_OPERATION,
            ),
            worker_instance=self.instance,
            token=self.token,
            execution_generation=self.generation,
            lease_until=self.lease_until,
            local_lost_signal=signal,
        )


@dataclass(frozen=True, slots=True)
class _ReleaseExpectation:
    status: str
    updated_at: datetime
    user_cancelled_at: datetime | None


@dataclass(frozen=True, slots=True)
class _RenewExpectation:
    prior_lease_until: datetime
    candidate_lease_until: datetime


@dataclass(slots=True)
class SqlAlchemyFrameReviewClaims:
    """Claim only complete frame.review graphs and fence every lease update."""

    session_factory: SessionFactory
    db_clock: DbClock
    id_factory: IdFactory
    lease_seconds: int = _DEFAULT_LEASE_SECONDS
    _signals: dict[str, Event] = field(default_factory=dict, init=False, repr=False)
    _signals_lock: RLock = field(default_factory=RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        if not callable(self.session_factory) or not callable(self.db_clock):
            raise TypeError("claim session and database clock must be explicit factories")
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
        staged: list[_ClaimExpectation] = []
        try:
            claim = self._claim_and_commit(instance, token, signal, staged)
        except Exception:
            claim = self._confirm_claim(staged[0], signal) if staged else None
        if claim is not None:
            with self._signals_lock:
                self._signals[token] = signal
        return claim

    def renew_or_revoke(self, claim: ClaimAuthority) -> str:
        signal = self._signal_for(claim)
        if signal is None or signal.is_set():
            return "ownership_lost"
        expected_renewal: list[_RenewExpectation] = []
        try:
            renewed_until = self._renew_and_commit(claim, expected_renewal)
        except Exception:
            renewed_until = self._confirm_renewal(
                claim, expected_renewal[0] if expected_renewal else None
            )
        if renewed_until is None:
            signal.set()
            return "ownership_lost"
        return "renewed"

    def release_claim(self, claim: ClaimAuthority) -> bool:
        signal = self._signal_for(claim)
        if signal is None:
            return False
        expected_release: list[_ReleaseExpectation] = []
        try:
            released_status = self._release_and_commit(claim, expected_release)
        except Exception:
            released_status = self._confirm_release(
                claim, expected_release[0] if expected_release else None
            )
        signal.set()
        with self._signals_lock:
            self._signals.pop(claim.token, None)
        return released_status is not None

    def _claim_and_commit(
        self,
        instance: str,
        token: str,
        signal: Event,
        staged: list[_ClaimExpectation],
    ) -> ClaimAuthority | None:
        cursor: tuple[datetime, str] | None = None
        while True:
            candidates = self._candidate_ids(cursor)
            if not candidates:
                return None
            for owner_id, task_id, created_at in candidates:
                claim = self._claim_candidate(
                    owner_id, task_id, instance, token, signal, staged
                )
                if claim is not None:
                    return claim
                cursor = (created_at, task_id)

    def _candidate_ids(
        self, cursor: tuple[datetime, str] | None
    ) -> list[tuple[str, str, datetime]]:
        table = AITaskRow.__table__
        unit = BillingUnitRow.__table__
        step = ExecutionStepRow.__table__
        external = ExternalSubmissionRow.__table__
        conditions = [
            table.c.type == _TASK_TYPE,
            table.c.status.in_(_TASK_STATUSES),
            table.c.user_cancelled_at.is_(None),
            unit.c.result_status == "pending",
            unit.c.billing_status == "unbilled",
            unit.c.first_intent_at.is_(None),
            unit.c.deadline_at.is_(None),
            unit.c.debited_amount == 0,
            unit.c.refunded_amount == 0,
            step.c.execution_status == "prepared",
            ~exists(
                select(external.c.id).where(external.c.step_id == step.c.id)
            ),
        ]
        if cursor is not None:
            created_at, task_id = cursor
            stored_created_at = _database_datetime(created_at)
            conditions.append(
                or_(
                    table.c.created_at > stored_created_at,
                    and_(table.c.created_at == stored_created_at, table.c.id > task_id),
                )
            )
        with self.session_factory() as session:
            rows = session.execute(
                select(table.c.user_id, table.c.id, table.c.created_at)
                .join(unit, unit.c.task_id == table.c.id)
                .join(step, step.c.billing_unit_id == unit.c.id)
                .where(*conditions)
                .distinct()
                .order_by(table.c.created_at, table.c.id)
                .limit(128)
            ).all()
        return [
            (row.user_id, row.id, _aware_utc(row.created_at)) for row in rows
        ]

    def _claim_candidate(
        self,
        owner_id: str,
        task_id: str,
        instance: str,
        token: str,
        signal: Event,
        staged: list[_ClaimExpectation],
    ) -> ClaimAuthority | None:
        with self.session_factory() as session:
            with session.begin():
                user = session.execute(
                    select(UserRow.id)
                    .where(UserRow.id == owner_id)
                    .with_for_update(skip_locked=True)
                ).scalar_one_or_none()
                if user is None:
                    return None
                task = session.execute(
                    select(AITaskRow.__table__)
                    .where(
                        AITaskRow.id == task_id,
                        AITaskRow.user_id == owner_id,
                        AITaskRow.type == _TASK_TYPE,
                    )
                    .with_for_update(skip_locked=True)
                ).mappings().one_or_none()
                if task is None:
                    return None
                graph = self._lock_registered_graph(session, dict(task))
                now = self._server_now()
                if graph is None or not _lease_is_available(task, now):
                    return None
                unit, step, submission = graph
                if (
                    not _generation_graph_is_claimable(session, unit, step)
                    or not _registered_graph_matches(task, unit, step, submission)
                ):
                    return None
                if session.execute(
                    select(AITaskRow.__table__.c.id)
                    .where(AITaskRow.claim_token == token)
                    .limit(1)
                ).scalar_one_or_none() is not None:
                    return None

                generation = task["execution_generation"] + 1
                lease_until = now + timedelta(seconds=self.lease_seconds)
                task_table = AITaskRow.__table__
                result = session.execute(
                    update(task_table)
                    .where(
                        task_table.c.id == task_id,
                        task_table.c.user_id == owner_id,
                        task_table.c.type == _TASK_TYPE,
                        task_table.c.status == task["status"],
                        task_table.c.status.in_(_TASK_STATUSES),
                        task_table.c.execution_generation == task["execution_generation"],
                        task_table.c.request_data == task["request_data"],
                        task_table.c.user_cancelled_at.is_(None),
                        _lease_cas(task_table, task, now),
                    )
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
                expectation = _ClaimExpectation(
                    owner_id=owner_id,
                    task_id=task_id,
                    instance=instance,
                    token=token,
                    generation=generation,
                    lease_until=lease_until,
                    request_data=task["request_data"],
                )
                staged.append(expectation)
                session.flush()
                return expectation.authority(signal)

    def _lock_registered_graph(self, session: Session, task: dict[str, object]):
        units = session.execute(
            select(BillingUnitRow.__table__)
            .where(BillingUnitRow.task_id == task["id"])
            .order_by(BillingUnitRow.id)
            .with_for_update()
        ).mappings().all()
        if len(units) != 1:
            return None
        unit = dict(units[0])
        steps = session.execute(
            select(ExecutionStepRow.__table__)
            .where(ExecutionStepRow.billing_unit_id == unit["id"])
            .order_by(ExecutionStepRow.step_order, ExecutionStepRow.id)
            .with_for_update()
        ).mappings().all()
        if len(steps) != 1:
            return None
        step = dict(steps[0])
        try:
            request_data = json.loads(task["request_data"])
            submission_id = request_data.get("submission_id")
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(submission_id, str) or not submission_id:
            return None
        submission = session.execute(
            select(TaskSubmissionRow.__table__)
            .where(TaskSubmissionRow.id == submission_id)
            .with_for_update(skip_locked=True)
        ).mappings().one_or_none()
        return None if submission is None else (unit, step, dict(submission))

    def _server_now(self) -> datetime:
        value = self.db_clock()
        if not _aware_datetime(value):
            raise ValueError("database clock must return an aware UTC datetime")
        return _aware_utc(value)

    def _renew_and_commit(
        self, claim: ClaimAuthority, committed_candidate: list[_RenewExpectation]
    ) -> datetime | None:
        with self.session_factory() as session:
            with session.begin():
                task = self._lock_claim_task(session, claim)
                now = self._server_now()
                if task is None or not _claim_is_live(task, claim, now):
                    return None
                lease_until = now + timedelta(seconds=self.lease_seconds)
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
                        table.c.lease_until == _database_datetime(_aware_utc(task["lease_until"])),
                        table.c.lease_until > _database_datetime(now),
                    )
                    .values(
                        lease_until=_database_datetime(lease_until),
                        updated_at=_database_datetime(now),
                    )
                )
                if result.rowcount != 1:
                    return None
                committed_candidate.append(
                    _RenewExpectation(
                        prior_lease_until=_aware_utc(task["lease_until"]),
                        candidate_lease_until=lease_until,
                    )
                )
                return lease_until

    def _release_and_commit(
        self,
        claim: ClaimAuthority,
        committed_candidate: list[_ReleaseExpectation],
    ) -> str | None:
        with self.session_factory() as session:
            with session.begin():
                task = self._lock_claim_task(session, claim)
                if task is None or not _claim_matches(task, claim):
                    return None
                status = (
                    "queued"
                    if task["status"] == "processing"
                    and task["user_cancelled_at"] is None
                    else task["status"]
                )
                now = self._server_now()
                table = AITaskRow.__table__
                result = session.execute(
                    update(table)
                    .where(
                        table.c.id == claim.scope.task_id,
                        table.c.user_id == claim.scope.user_id,
                        table.c.type == _TASK_TYPE,
                        table.c.status == task["status"],
                        table.c.claimed_by == claim.worker_instance,
                        table.c.claim_token == claim.token,
                        table.c.execution_generation == claim.execution_generation,
                        table.c.user_cancelled_at == task["user_cancelled_at"],
                    )
                    .values(
                        status=status,
                        claimed_by=None,
                        claim_token=None,
                        lease_until=None,
                        updated_at=_database_datetime(now),
                    )
                )
                if result.rowcount != 1:
                    return None
                committed_candidate.append(
                    _ReleaseExpectation(
                        status=status,
                        updated_at=now,
                        user_cancelled_at=task["user_cancelled_at"],
                    )
                )
                return status

    def _lock_claim_task(self, session: Session, claim: ClaimAuthority):
        if not isinstance(claim, ClaimAuthority):
            return None
        user = session.execute(
            select(UserRow.id)
            .where(UserRow.id == claim.scope.user_id)
            .with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        if user is None:
            return None
        row = session.execute(
            select(AITaskRow.__table__)
            .where(
                AITaskRow.id == claim.scope.task_id,
                AITaskRow.user_id == claim.scope.user_id,
                AITaskRow.type == _TASK_TYPE,
            )
            .with_for_update(skip_locked=True)
        ).mappings().one_or_none()
        return None if row is None else dict(row)

    def _confirm_claim(
        self, expected: _ClaimExpectation, signal: Event
    ) -> ClaimAuthority | None:
        try:
            with self.session_factory() as session:
                row = session.execute(
                    select(AITaskRow.__table__).where(
                        AITaskRow.id == expected.task_id,
                        AITaskRow.user_id == expected.owner_id,
                        AITaskRow.type == _TASK_TYPE,
                        AITaskRow.status == "processing",
                        AITaskRow.claimed_by == expected.instance,
                        AITaskRow.claim_token == expected.token,
                        AITaskRow.execution_generation == expected.generation,
                        AITaskRow.lease_until == _database_datetime(expected.lease_until),
                        AITaskRow.request_data == expected.request_data,
                        AITaskRow.user_cancelled_at.is_(None),
                    )
                ).mappings().one_or_none()
                if row is None or not _lease_is_active(row, self._server_now()):
                    return None
                return expected.authority(signal)
        except Exception:
            return None

    def _confirm_renewal(
        self, claim: ClaimAuthority, expected: _RenewExpectation | None
    ) -> datetime | None:
        # If the injected/database clock has not advanced, a renewal can stage
        # the lease value already held by the currently locked row. Likewise,
        # a later renewal may stage the value of an earlier committed renewal.
        # Neither value can prove this transaction committed after an
        # acknowledgement error, so compare against the per-write prior lease.
        if (
            expected is None
            or expected.candidate_lease_until <= expected.prior_lease_until
            or claim.local_lost_signal.is_set()
        ):
            return None
        try:
            with self.session_factory() as session:
                row = session.execute(
                    select(AITaskRow.__table__).where(
                        AITaskRow.id == claim.scope.task_id,
                        AITaskRow.user_id == claim.scope.user_id,
                        AITaskRow.type == _TASK_TYPE,
                        AITaskRow.status == "processing",
                        AITaskRow.claimed_by == claim.worker_instance,
                        AITaskRow.claim_token == claim.token,
                        AITaskRow.execution_generation == claim.execution_generation,
                        AITaskRow.lease_until
                        == _database_datetime(expected.candidate_lease_until),
                        AITaskRow.user_cancelled_at.is_(None),
                    )
                ).mappings().one_or_none()
                now = self._server_now()
                if (
                    row is None
                    or not _claim_is_live(dict(row), claim, now)
                    or not _lease_is_active(row, now)
                ):
                    return None
                return expected.candidate_lease_until
        except Exception:
            return None

    def _confirm_release(
        self, claim: ClaimAuthority, expected: _ReleaseExpectation | None
    ) -> str | None:
        if expected is None:
            return None
        try:
            with self.session_factory() as session:
                row = session.execute(
                    select(AITaskRow.__table__).where(
                        AITaskRow.id == claim.scope.task_id,
                        AITaskRow.user_id == claim.scope.user_id,
                        AITaskRow.type == _TASK_TYPE,
                        AITaskRow.status == expected.status,
                        AITaskRow.claimed_by.is_(None),
                        AITaskRow.claim_token.is_(None),
                        AITaskRow.lease_until.is_(None),
                        AITaskRow.execution_generation == claim.execution_generation,
                        AITaskRow.user_cancelled_at == expected.user_cancelled_at,
                        AITaskRow.updated_at == _database_datetime(expected.updated_at),
                    )
                ).mappings().one_or_none()
                return expected.status if row is not None else None
        except Exception:
            return None

    def _signal_for(self, claim: ClaimAuthority) -> Event | None:
        if not isinstance(claim, ClaimAuthority) or claim.scope.operation != _OPERATION:
            return None
        with self._signals_lock:
            signal = self._signals.get(claim.token)
        return signal if signal is claim.local_lost_signal else None


def _generation_graph_is_claimable(session: Session, unit, step) -> bool:
    if not all(
        (
            unit.get("result_status") == "pending",
            unit.get("billing_status") == "unbilled",
            unit.get("first_intent_at") is None,
            unit.get("deadline_at") is None,
            unit.get("debited_amount") == 0,
            unit.get("refunded_amount") == 0,
            step.get("execution_status") == "prepared",
        )
    ):
        return False
    external = ExternalSubmissionRow.__table__
    return (
        session.execute(
            select(external.c.id)
            .where(external.c.step_id == step.get("id"))
            .limit(1)
            .with_for_update()
        ).scalar_one_or_none()
        is None
    )


def _registered_graph_matches(task, unit, step, submission) -> bool:
    try:
        request = json.loads(task["request_data"])
        snapshot = step["input_snapshot"]
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        return False
    if not isinstance(request, dict) or not isinstance(snapshot, dict):
        return False
    digest = request.get("request_digest")
    return all(
        (
            task.get("type") == _TASK_TYPE,
            task.get("credit_cost") == 0,
            request.get("via") == "registered_generation_graph",
            request.get("mode") == "frame_review",
            request.get("operation") == _OPERATION,
            request.get("graph_version") == _GRAPH_VERSION,
            request.get("step_graph_version") == _GRAPH_VERSION,
            request.get("submission_id") == submission.get("id"),
            request.get("billing_unit_id") == unit.get("id"),
            request.get("server_attempt_id") == snapshot.get("server_attempt_id"),
            request.get("prepared_payload_id") == snapshot.get("prepared_payload_id"),
            request.get("protected_ref") == snapshot.get("protected_ref"),
            request.get("snapshot_digest") == snapshot.get("snapshot_digest"),
            request.get("manifest_snapshot_digest") == snapshot.get("manifest_snapshot_digest"),
            unit.get("task_id") == task.get("id"),
            unit.get("operation") == _OPERATION,
            unit.get("step_graph_version") == _GRAPH_VERSION,
            unit.get("item_key") == snapshot.get("server_attempt_id"),
            unit.get("quoted_amount") >= 0,
            unit.get("result_status") == "pending",
            unit.get("billing_status") == "unbilled",
            unit.get("first_intent_at") is None,
            unit.get("deadline_at") is None,
            unit.get("debited_amount") == 0,
            unit.get("refunded_amount") == 0,
            unit.get("input_digest") == digest,
            unit.get("result_status") in {"pending", "unknown", "succeeded"},
            step.get("billing_unit_id") == unit.get("id"),
            step.get("step_key") == "generate",
            step.get("required") is True,
            step.get("step_order") == 0,
            step.get("execution_status") == "prepared",
            step.get("dependencies") in ([], ()),
            step.get("input_digest") == digest,
            set(snapshot) == _STEP_SNAPSHOT_KEYS,
            _is_digest(digest),
            _is_digest(snapshot.get("snapshot_digest")),
            _is_digest(snapshot.get("manifest_snapshot_digest")),
            isinstance(snapshot.get("protected_ref"), str),
            isinstance(snapshot.get("prepared_payload_id"), str),
            submission.get("user_id") == task.get("user_id"),
            submission.get("operation") == _OPERATION,
            submission.get("request_digest") == digest,
            submission.get("task_ids") == [task.get("id")],
            submission.get("status") == "accepted",
            submission.get("idempotency_key") == f"frame-review:{submission.get('id')}",
        )
    )


def _lease_is_available(task, now: datetime) -> bool:
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


def _claim_matches(task, claim: ClaimAuthority) -> bool:
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


def _claim_is_live(task, claim: ClaimAuthority, now: datetime) -> bool:
    lease = task.get("lease_until")
    return (
        _claim_matches(task, claim)
        and task.get("status") == "processing"
        and task.get("user_cancelled_at") is None
        and lease is not None
        and _aware_utc(lease) > now
    )


def _lease_is_active(task, now: datetime) -> bool:
    lease = task.get("lease_until")
    return lease is not None and _aware_utc(lease) > now


def _lease_cas(table, task, now):
    lease = task.get("lease_until")
    if task.get("status") == "queued":
        if lease is None:
            return table.c.lease_until.is_(None)
        return table.c.lease_until == _database_datetime(_aware_utc(lease))
    return (
        table.c.lease_until == _database_datetime(_aware_utc(lease))
        and table.c.lease_until <= _database_datetime(now)
    )


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


def _aware_datetime(value: object) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() == timezone.utc.utcoffset(value)
    )


def _aware_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _database_datetime(value: datetime) -> datetime:
    return _aware_utc(value).replace(tzinfo=None)
