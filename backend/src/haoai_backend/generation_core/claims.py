"""SQL-backed lease and fencing authority for the registered image.single graph."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Event, RLock

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.types import ClaimAuthority, OwnerTaskScope

from .base_schema import (
    AITaskRow,
    BillingUnitRow,
    ExecutionStepRow,
    TaskSubmissionRow,
    UserRow,
)

_TASK_TYPE = "image-single"
_OPERATION = "image.single"
_GRAPH_VERSION = "image-single-v1"
_STEP_SNAPSHOT_KEYS = frozenset(
    {
        "server_attempt_id",
        "prepared_payload_id",
        "protected_ref",
        "snapshot_digest",
        "ordered_manifest_digest",
    }
)
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_CLAIMABLE_STATUSES = ("queued", "processing", "reconciling")
_DEFAULT_LEASE_SECONDS = 60
_CANDIDATE_PAGE_SIZE = 128

SessionFactory = Callable[[], Session]
DbClock = Callable[[], datetime]
IdFactory = Callable[[], str]


@dataclass(frozen=True, slots=True)
class _Candidate:
    owner_id: str
    task_id: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class _ClaimExpectation:
    owner_id: str
    task_id: str
    task_type: str
    operation: str
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
                operation=self.operation,
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
    user_cancelled_at: datetime | None
    cancellation_reason: str | None
    result: str | None


@dataclass(slots=True)
class SqlAlchemyImageSingleClaims:
    """Acquire, renew, and fence one durable image.single worker lease.

    Only the registered image.single task family is claimable through this
    adapter. Every write is committed before a claim is returned to a worker.
    """

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
        """Claim only a complete registered image.single graph."""

        if task_type != _TASK_TYPE or not isinstance(instance, str) or not instance:
            return None
        try:
            token = self.id_factory()
        except Exception:
            return None
        if not isinstance(token, str) or not token:
            return None
        signal = Event()
        try:
            claim = self._claim_and_commit(task_type, instance, token, signal)
        except Exception:
            claim = None
        if claim is not None:
            with self._signals_lock:
                self._signals[token] = signal
        return claim

    def renew_or_revoke(
        self, claim: ClaimAuthority
    ) -> str:
        """Renew only the exact live token and generation, otherwise signal loss."""

        signal = self._signal_for(claim)
        if signal is None or signal.is_set():
            return "ownership_lost"
        expected: list[datetime] = []
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
        """Release only the exact claim while preserving the task recovery state."""

        signal = self._signal_for(claim)
        if signal is None:
            return False
        expected: list[_ReleaseExpectation] = []
        try:
            released = self._release_and_commit(claim, expected)
        except Exception:
            released = (
                self._confirm_released(claim, expected[0]) if expected else False
            )
        signal.set()
        with self._signals_lock:
            self._signals.pop(claim.token, None)
        return released

    def _claim_and_commit(
        self,
        task_type: str,
        instance: str,
        token: str,
        signal: Event,
    ) -> ClaimAuthority | None:
        cursor: tuple[datetime, str] | None = None
        while True:
            candidates = self._candidate_ids(task_type, cursor)
            if not candidates:
                return None
            for candidate in candidates:
                staged: list[_ClaimExpectation] = []
                try:
                    claim = self._claim_candidate(
                        candidate, task_type, instance, token, signal, staged
                    )
                except Exception:
                    if staged:
                        claim = self._confirm_claim(staged[0], signal)
                        if claim is not None:
                            return claim
                    return None
                if claim is not None:
                    return claim
                cursor = (candidate.created_at, candidate.task_id)
        return None

    def _candidate_ids(
        self, task_type: str, cursor: tuple[datetime, str] | None
    ) -> list[_Candidate]:
        table = AITaskRow.__table__
        conditions = [
            table.c.type == task_type,
            table.c.status.in_(_CLAIMABLE_STATUSES),
            table.c.user_cancelled_at.is_(None),
        ]
        if cursor is not None:
            created_at, task_id = cursor
            stored_created_at = _database_datetime(created_at)
            conditions.append(
                or_(
                    table.c.created_at > stored_created_at,
                    and_(
                        table.c.created_at == stored_created_at,
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
        task_type: str,
        instance: str,
        token: str,
        signal: Event,
        expected: list[_ClaimExpectation],
    ) -> ClaimAuthority | None:
        with self.session_factory() as session:
            with session.begin():
                locked = self._lock_registered_graph(
                    session, candidate.owner_id, candidate.task_id
                )
                if locked is None:
                    return None
                task, unit, step, submission = locked
                now = self._server_now()
                if not _lease_is_available(task, now) or not _registered_graph_matches(
                    task, unit, step, submission
                ):
                    return None
                existing_token = session.execute(
                    select(AITaskRow.__table__.c.id)
                    .where(AITaskRow.claim_token == token)
                    .limit(1)
                ).scalar_one_or_none()
                if existing_token is not None:
                    return None
                generation = task["execution_generation"] + 1
                lease_until = now + timedelta(seconds=self.lease_seconds)
                stored_lease = _database_datetime(lease_until)
                result = session.execute(
                    update(AITaskRow.__table__)
                    .where(
                        *_claim_stage_predicates(
                            candidate.owner_id,
                            candidate.task_id,
                            task_type,
                            task,
                        )
                    )
                    .values(
                        status="processing",
                        claimed_by=instance,
                        claim_token=token,
                        execution_generation=generation,
                        lease_until=stored_lease,
                        updated_at=_database_datetime(now),
                    )
                )
                if result.rowcount != 1:
                    return None
                expectation = _ClaimExpectation(
                    owner_id=candidate.owner_id,
                    task_id=candidate.task_id,
                    task_type=task_type,
                    operation=_OPERATION,
                    instance=instance,
                    token=token,
                    generation=generation,
                    lease_until=lease_until,
                    request_data=task["request_data"],
                )
                expected.append(expectation)
                session.flush()
                return expectation.authority(signal)

    def _lock_registered_graph(
        self, session: Session, owner_id: str, task_id: str
    ) -> tuple[dict[str, object], dict[str, object], dict[str, object], dict[str, object]] | None:
        user = session.execute(
            select(UserRow.__table__)
            .where(UserRow.id == owner_id)
            .with_for_update(skip_locked=True)
        ).mappings().one_or_none()
        if user is None:
            return None
        task = session.execute(
            select(AITaskRow.__table__)
            .where(AITaskRow.id == task_id, AITaskRow.user_id == owner_id)
            .with_for_update(skip_locked=True)
        ).mappings().one_or_none()
        if task is None:
            return None
        units = session.execute(
            select(BillingUnitRow.__table__)
            .where(BillingUnitRow.task_id == task_id)
            .order_by(BillingUnitRow.id)
            .with_for_update()
        ).mappings().all()
        if len(units) != 1:
            return None
        unit = units[0]
        steps = session.execute(
            select(ExecutionStepRow.__table__)
            .where(ExecutionStepRow.billing_unit_id == unit["id"])
            .order_by(ExecutionStepRow.step_order, ExecutionStepRow.id)
            .with_for_update()
        ).mappings().all()
        if len(steps) != 1:
            return None
        step = steps[0]
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
        if submission is None:
            return None
        return dict(task), dict(unit), dict(step), dict(submission)

    def _server_now(self) -> datetime:
        value = self.db_clock()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() != timezone.utc.utcoffset(value)
        ):
            raise ValueError("database clock must return an aware UTC datetime")
        return value.astimezone(timezone.utc)

    def _renew_and_commit(
        self, claim: ClaimAuthority, expected: list[datetime]
    ) -> datetime | None:
        with self.session_factory() as session:
            with session.begin():
                task = self._lock_claim_task(session, claim)
                if task is None:
                    return None
                now = self._server_now()
                if not _claim_is_renewable(task, claim, now):
                    return None
                lease_until = now + timedelta(seconds=self.lease_seconds)
                if lease_until <= _aware_utc(task["lease_until"]):
                    return None
                result = session.execute(
                    update(AITaskRow.__table__)
                    .where(*_renewal_predicates(task, claim, now))
                    .values(lease_until=_database_datetime(lease_until), updated_at=_database_datetime(now))
                )
                if result.rowcount != 1:
                    return None
                expected.append(lease_until)
                return lease_until

    def _release_and_commit(
        self, claim: ClaimAuthority, expected: list[_ReleaseExpectation]
    ) -> bool:
        with self.session_factory() as session:
            with session.begin():
                task = self._lock_claim_task(session, claim)
                if task is None:
                    return False
                now = self._server_now()
                if not _claim_matches(task, claim):
                    return False
                status = (
                    "queued"
                    if task["status"] == "processing"
                    and task["user_cancelled_at"] is None
                    else task["status"]
                )
                expectation = _ReleaseExpectation(
                    status=status,
                    user_cancelled_at=task["user_cancelled_at"],
                    cancellation_reason=task["cancellation_reason"],
                    result=task["result"],
                )
                result = session.execute(
                    update(AITaskRow.__table__)
                    .where(*_release_predicates(task, claim))
                    .values(
                        status=status,
                        claimed_by=None,
                        claim_token=None,
                        lease_until=None,
                        updated_at=_database_datetime(now),
                    )
                )
                if result.rowcount != 1:
                    return False
                expected.append(expectation)
                return True

    def _lock_claim_task(self, session: Session, claim: ClaimAuthority):
        user = session.execute(
            select(UserRow.id)
            .where(UserRow.id == claim.scope.user_id)
            .with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        if user is None:
            return None
        return session.execute(
            select(AITaskRow.__table__)
            .where(AITaskRow.id == claim.scope.task_id)
            .with_for_update(skip_locked=True)
        ).mappings().one_or_none()

    def _confirm_claim(
        self, expected: _ClaimExpectation, signal: Event
    ) -> ClaimAuthority | None:
        try:
            with self.session_factory() as session:
                row = session.execute(
                    select(AITaskRow.__table__).where(
                        AITaskRow.id == expected.task_id,
                        AITaskRow.user_id == expected.owner_id,
                        AITaskRow.type == expected.task_type,
                        AITaskRow.claimed_by == expected.instance,
                        AITaskRow.claim_token == expected.token,
                        AITaskRow.execution_generation == expected.generation,
                        AITaskRow.lease_until == _database_datetime(expected.lease_until),
                        AITaskRow.request_data == expected.request_data,
                    )
                ).mappings().one_or_none()
                now = self._server_now()
                if (
                    row is None
                    or row["status"] != "processing"
                    or row["user_cancelled_at"] is not None
                    or not _task_operation_matches(row, expected.operation)
                    or not _lease_is_active(row, now)
                ):
                    return None
                return expected.authority(signal)
        except Exception:
            return None

    def _confirm_renewal(
        self, claim: ClaimAuthority, expected_lease_until: datetime
    ) -> datetime | None:
        try:
            with self.session_factory() as session:
                row = session.execute(
                    select(AITaskRow.__table__).where(AITaskRow.id == claim.scope.task_id)
                ).mappings().one_or_none()
                now = self._server_now()
                if (
                    row is None
                    or not _claim_is_renewable(row, claim, now)
                    or _aware_utc(row["lease_until"]) != expected_lease_until
                ):
                    return None
                return expected_lease_until
        except Exception:
            return None

    def _confirm_released(
        self, claim: ClaimAuthority, expected: _ReleaseExpectation
    ) -> bool:
        try:
            with self.session_factory() as session:
                row = session.execute(
                    select(AITaskRow.__table__).where(AITaskRow.id == claim.scope.task_id)
                ).mappings().one_or_none()
                return bool(
                    row is not None
                    and row["user_id"] == claim.scope.user_id
                    and row["type"] == _TASK_TYPE
                    and claim.scope.operation == _OPERATION
                    and _task_operation_matches(row, _OPERATION)
                    and row["status"] == expected.status
                    and row["claimed_by"] is None
                    and row["claim_token"] is None
                    and row["lease_until"] is None
                    and row["execution_generation"] == claim.execution_generation
                    and row["user_cancelled_at"] == expected.user_cancelled_at
                    and row["cancellation_reason"] == expected.cancellation_reason
                    and row["result"] == expected.result
                )
        except Exception:
            return False

    def _signal_for(self, claim: ClaimAuthority) -> Event | None:
        if not isinstance(claim, ClaimAuthority):
            return None
        with self._signals_lock:
            signal = self._signals.get(claim.token)
        if signal is not None and signal is not claim.local_lost_signal:
            return None
        return signal


def _registered_graph_matches(
    task: dict[str, object],
    unit: dict[str, object],
    step: dict[str, object],
    submission: dict[str, object],
) -> bool:
    try:
        request_data = json.loads(task["request_data"])
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    snapshot = step.get("input_snapshot")
    if not isinstance(request_data, dict) or not isinstance(snapshot, dict):
        return False
    manifest_digest = snapshot.get("ordered_manifest_digest")
    return all(
        (
            task.get("type") == _TASK_TYPE,
            task.get("user_id") == submission.get("user_id"),
            task.get("credit_cost") == unit.get("quoted_amount"),
            unit.get("task_id") == task.get("id"),
            unit.get("operation") == _OPERATION,
            unit.get("step_graph_version") == _GRAPH_VERSION,
            unit.get("item_key") == snapshot.get("server_attempt_id"),
            isinstance(unit.get("input_digest"), str)
            and _DIGEST.fullmatch(unit["input_digest"]) is not None,
            unit.get("input_digest") == submission.get("request_digest"),
            step.get("billing_unit_id") == unit.get("id"),
            step.get("step_key") == "generate",
            step.get("required") is True,
            step.get("step_order") == 0,
            step.get("dependencies") in ([], ()),
            step.get("input_digest") == unit.get("input_digest"),
            set(snapshot) == _STEP_SNAPSHOT_KEYS,
            snapshot.get("snapshot_digest") == request_data.get("snapshot_digest"),
            isinstance(manifest_digest, str) and _DIGEST.fullmatch(manifest_digest),
            request_data.get("operation") == _OPERATION,
            request_data.get("submission_id") == submission.get("id"),
            request_data.get("parent_attempt_id") == submission.get("id"),
            request_data.get("server_attempt_id") == snapshot.get("server_attempt_id"),
            request_data.get("prepared_payload_id") == snapshot.get("prepared_payload_id"),
            request_data.get("protected_ref") == snapshot.get("protected_ref"),
            request_data.get("billing_unit_id") == unit.get("id"),
            request_data.get("item_id") == unit.get("item_key"),
            request_data.get("unit_key") == unit.get("item_key"),
            request_data.get("step_graph_version") == _GRAPH_VERSION,
            request_data.get("request_digest") == unit.get("input_digest"),
            request_data.get("request_digest") == submission.get("request_digest"),
            request_data.get("rule_digest") is not None,
            request_data.get("target_ref") == f"{snapshot.get('protected_ref')}#target",
            request_data.get("model_ref") == f"{snapshot.get('protected_ref')}#model",
            request_data.get("grant_ref") == f"{snapshot.get('protected_ref')}#grant",
            submission.get("operation") == _OPERATION,
            submission.get("task_ids") == [task.get("id")],
            isinstance(submission.get("idempotency_key"), str)
            and bool(submission["idempotency_key"]),
        )
    )


def _lease_is_available(task: dict[str, object], now: datetime) -> bool:
    if task.get("user_cancelled_at") is not None:
        return False
    if task.get("status") == "queued":
        lease_until = task.get("lease_until")
        return lease_until is None or _aware_utc(lease_until) <= now
    if task.get("status") in {"processing", "reconciling"}:
        lease_until = task.get("lease_until")
        return lease_until is not None and _aware_utc(lease_until) <= now
    return False


def _lease_is_active(task: dict[str, object], now: datetime) -> bool:
    lease_until = task.get("lease_until")
    return lease_until is not None and _aware_utc(lease_until) > now


def _claim_matches(task: dict[str, object], claim: ClaimAuthority) -> bool:
    return all(
        (
            task.get("id") == claim.scope.task_id,
            task.get("user_id") == claim.scope.user_id,
            task.get("type") == _TASK_TYPE,
            claim.scope.operation == _OPERATION,
            _task_operation_matches(task, _OPERATION),
            task.get("claimed_by") == claim.worker_instance,
            task.get("claim_token") == claim.token,
            task.get("execution_generation") == claim.execution_generation,
        )
    )


def _claim_is_renewable(
    task: dict[str, object], claim: ClaimAuthority, now: datetime
) -> bool:
    return (
        _claim_matches(task, claim)
        and task.get("status") == "processing"
        and task.get("user_cancelled_at") is None
        and _lease_is_active(task, now)
    )


def _task_operation_matches(task: dict[str, object], operation: str) -> bool:
    try:
        request_data = json.loads(task.get("request_data"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    return isinstance(request_data, dict) and request_data.get("operation") == operation


def _nullable_predicate(column, value: object):
    return column.is_(None) if value is None else column == value


def _claim_stage_predicates(
    owner_id: str,
    task_id: str,
    task_type: str,
    task: dict[str, object],
) -> tuple[object, ...]:
    table = AITaskRow.__table__
    return (
        table.c.id == task_id,
        table.c.user_id == owner_id,
        table.c.type == task_type,
        table.c.status == task["status"],
        table.c.execution_generation == task["execution_generation"],
        table.c.user_cancelled_at.is_(None),
        _nullable_predicate(table.c.claimed_by, task.get("claimed_by")),
        _nullable_predicate(table.c.claim_token, task.get("claim_token")),
        _nullable_predicate(table.c.lease_until, task.get("lease_until")),
        table.c.request_data == task["request_data"],
    )


def _renewal_predicates(
    task: dict[str, object], claim: ClaimAuthority, now: datetime
) -> tuple[object, ...]:
    table = AITaskRow.__table__
    return (
        table.c.id == claim.scope.task_id,
        table.c.user_id == claim.scope.user_id,
        table.c.type == _TASK_TYPE,
        table.c.status == "processing",
        table.c.user_cancelled_at.is_(None),
        table.c.claimed_by == claim.worker_instance,
        table.c.claim_token == claim.token,
        table.c.execution_generation == claim.execution_generation,
        table.c.lease_until == _database_datetime(_aware_utc(task["lease_until"])),
        table.c.lease_until > _database_datetime(now),
        table.c.request_data == task["request_data"],
    )


def _release_predicates(
    task: dict[str, object], claim: ClaimAuthority
) -> tuple[object, ...]:
    table = AITaskRow.__table__
    return (
        table.c.id == claim.scope.task_id,
        table.c.user_id == claim.scope.user_id,
        table.c.type == _TASK_TYPE,
        table.c.status == task["status"],
        _nullable_predicate(table.c.user_cancelled_at, task.get("user_cancelled_at")),
        table.c.claimed_by == claim.worker_instance,
        table.c.claim_token == claim.token,
        table.c.execution_generation == claim.execution_generation,
        _nullable_predicate(table.c.lease_until, task.get("lease_until")),
    )


def _aware_utc(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("persisted claim lease is invalid")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _database_datetime(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)


__all__ = ("SqlAlchemyImageSingleClaims",)
