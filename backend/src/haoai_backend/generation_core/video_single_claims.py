"""SQL-backed leases for the ordinary, single-step ``video.single`` graph."""

from __future__ import annotations

import json
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Event, RLock

from sqlalchemy import and_, or_, select, update
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

_TASK_TYPE = "video-single"
_OPERATION = "video.single"
_GRAPH_VERSION = "video-single-v1"
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
_RECONCILING_RESULT = json.dumps(
    {"message": "视频生成结果需要恢复确认", "status": "reconciling"},
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
)

SessionFactory = Callable[[], Session]
DbClock = Callable[[], datetime]
IdFactory = Callable[[], str]


@dataclass(frozen=True, slots=True)
class _Candidate:
    owner_id: str
    task_id: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class _LockedGraph:
    owner_id: str
    task_id: str
    submission_id: str
    task: dict[str, object]
    unit: dict[str, object]
    step: dict[str, object]
    submission: dict[str, object]
    external_submissions: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class _ClaimExpectation:
    owner_id: str
    task_id: str
    instance: str
    token: str
    generation: int
    lease_until: datetime
    request_data: str
    submission_id: str

    def authority(self, signal: Event) -> ClaimAuthority:
        return ClaimAuthority(
            scope=OwnerTaskScope(self.owner_id, self.task_id, _OPERATION),
            worker_instance=self.instance,
            token=self.token,
            execution_generation=self.generation,
            lease_until=self.lease_until,
            local_lost_signal=signal,
        )


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
class SqlAlchemyVideoSingleClaims:
    """Acquire and fence claims for the registered ``video.single`` family."""

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

    def _claim_and_commit(
        self, instance: str, token: str, signal: Event
    ) -> ClaimAuthority | None:
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
                        self._confirm_claim(expected[0], signal)
                        if expected
                        else None
                    )
                    if claim is not None:
                        return claim
                    return None
                if claim is not None:
                    return claim
                cursor = (candidate.created_at, candidate.task_id)

    def _candidate_ids(
        self, cursor: tuple[datetime, str] | None
    ) -> list[_Candidate]:
        table = AITaskRow.__table__
        conditions = [
            table.c.type == _TASK_TYPE,
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
        instance: str,
        token: str,
        signal: Event,
        expected: list[_ClaimExpectation],
    ) -> ClaimAuthority | None:
        with self.session_factory() as session:
            with session.begin():
                graph = _lock_registered_graph(
                    session, candidate.owner_id, candidate.task_id
                )
                now = self._server_now()
                if (
                    graph is None
                    or not _registered_graph_matches(graph)
                    or not _claim_is_available(graph, now)
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
                expectation = _ClaimExpectation(
                    owner_id=graph.owner_id,
                    task_id=graph.task_id,
                    instance=instance,
                    token=token,
                    generation=generation,
                    lease_until=lease_until,
                    request_data=str(graph.task["request_data"]),
                    submission_id=graph.submission_id,
                )
                expected.append(expectation)
                session.flush()
                return expectation.authority(signal)

    def _renew_and_commit(
        self, claim: ClaimAuthority, expected: list[_RenewExpectation]
    ) -> datetime | None:
        with self.session_factory() as session:
            with session.begin():
                graph = _lock_claimed_graph(session, claim)
                now = self._server_now()
                if graph is None or not _registered_graph_matches(graph):
                    return None
                task = graph.task
                if not _claim_live(task, claim, now):
                    return None
                old_lease = _aware_utc(task["lease_until"])
                lease_until = now + timedelta(seconds=self.lease_seconds)
                if lease_until <= old_lease:
                    return None
                result = session.execute(
                    update(AITaskRow.__table__)
                    .where(*_renewal_predicates(task, claim, now))
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
        self, claim: ClaimAuthority, expected: list[_ReleaseExpectation]
    ) -> bool:
        with self.session_factory() as session:
            with session.begin():
                if (
                    not isinstance(claim, ClaimAuthority)
                    or claim.scope.operation != _OPERATION
                ):
                    return False
                graph = _lock_registered_graph(
                    session, claim.scope.user_id, claim.scope.task_id
                )
                now = self._server_now()
                if graph is None or not _registered_graph_matches(graph):
                    return False
                task = graph.task
                if not _claim_matches(task, claim):
                    return False
                status = (
                    "queued"
                    if task.get("status") == "processing"
                    and task.get("user_cancelled_at") is None
                    else task.get("status")
                )
                if not isinstance(status, str):
                    return False
                write = _ReleaseExpectation(
                    status=status,
                    updated_at=now,
                    user_cancelled_at=task.get("user_cancelled_at"),
                    cancellation_reason=task.get("cancellation_reason"),
                    result=task.get("result"),
                    request_data=str(task["request_data"]),
                )
                result = session.execute(
                    update(AITaskRow.__table__)
                    .where(*_release_predicates(task, claim))
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
        self, expected: _ClaimExpectation, signal: Event
    ) -> ClaimAuthority | None:
        try:
            with self.session_factory() as session:
                with session.begin():
                    graph = _lock_registered_graph(
                        session, expected.owner_id, expected.task_id
                    )
                    now = self._server_now()
                    if (
                        graph is None
                        or graph.submission_id != expected.submission_id
                        or graph.task.get("status") != "processing"
                        or graph.task.get("claimed_by") != expected.instance
                        or graph.task.get("claim_token") != expected.token
                        or graph.task.get("execution_generation") != expected.generation
                        or graph.task.get("lease_until") is None
                        or _aware_utc(graph.task["lease_until"]) != expected.lease_until
                        or graph.task.get("request_data") != expected.request_data
                        or not _registered_graph_matches(graph)
                        or not _claim_available_for_renewal(
                            graph, now
                        )
                    ):
                        return None
                    return expected.authority(signal)
        except Exception:
            return None

    def _confirm_renewal(
        self, claim: ClaimAuthority, expected: _RenewExpectation
    ) -> datetime | None:
        try:
            with self.session_factory() as session:
                with session.begin():
                    graph = _lock_claimed_graph(session, claim)
                    now = self._server_now()
                    if graph is None or not _registered_graph_matches(graph):
                        return None
                    task = graph.task
                    if (
                        not _claim_live(task, claim, now)
                        or _aware_utc(task["lease_until"]) != expected.lease_until
                        or expected.lease_until <= expected.prior_lease_until
                    ):
                        return None
                    return expected.lease_until
        except Exception:
            return None

    def _confirm_release(
        self, claim: ClaimAuthority, expected: _ReleaseExpectation
    ) -> bool:
        try:
            with self.session_factory() as session:
                with session.begin():
                    graph = _lock_registered_graph(
                        session, claim.scope.user_id, claim.scope.task_id
                    )
                    if graph is None or not _registered_graph_matches(graph):
                        return False
                    task = graph.task
                    return bool(
                        task.get("status") == expected.status
                        and task.get("claimed_by") is None
                        and task.get("claim_token") is None
                        and task.get("lease_until") is None
                        and task.get("execution_generation")
                        == claim.execution_generation
                        and task.get("user_cancelled_at") == expected.user_cancelled_at
                        and task.get("cancellation_reason")
                        == expected.cancellation_reason
                        and task.get("result") == expected.result
                        and task.get("request_data") == expected.request_data
                        and _same_time(task.get("updated_at"), expected.updated_at)
                    )
        except Exception:
            return False

    def _signal_for(self, claim: ClaimAuthority) -> Event | None:
        if not isinstance(claim, ClaimAuthority) or claim.scope.operation != _OPERATION:
            return None
        with self._signals_lock:
            signal = self._signals.get(claim.token)
        return signal if signal is claim.local_lost_signal else None

    def _server_now(self) -> datetime:
        value = self.db_clock()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() != timezone.utc.utcoffset(value)
        ):
            raise ValueError("database clock must return an aware UTC datetime")
        return value.astimezone(timezone.utc)


def _lock_registered_graph(
    session: Session, owner_id: str, task_id: str
) -> _LockedGraph | None:
    owner = session.execute(
        select(UserRow.id)
        .where(UserRow.id == owner_id)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()
    if owner is None:
        return None
    task_row = session.execute(
        select(AITaskRow.__table__)
        .where(
            AITaskRow.__table__.c.id == task_id,
            AITaskRow.__table__.c.user_id == owner_id,
            AITaskRow.__table__.c.type == _TASK_TYPE,
        )
        .with_for_update(skip_locked=True)
    ).mappings().one_or_none()
    if task_row is None:
        return None

    units = session.execute(
        select(BillingUnitRow.__table__)
        .where(BillingUnitRow.__table__.c.task_id == task_id)
        .order_by(BillingUnitRow.__table__.c.id)
        .with_for_update()
    ).mappings().all()
    if len(units) != 1:
        return None
    unit = dict(units[0])

    steps = session.execute(
        select(ExecutionStepRow.__table__)
        .where(ExecutionStepRow.__table__.c.billing_unit_id == unit["id"])
        .order_by(
            ExecutionStepRow.__table__.c.step_order,
            ExecutionStepRow.__table__.c.id,
        )
        .with_for_update()
    ).mappings().all()
    if len(steps) != 1:
        return None
    step = dict(steps[0])
    task = dict(task_row)
    request_data = _json_object(task.get("request_data"))
    submission_id = (
        request_data.get("submission_id") if request_data is not None else None
    )
    if not _nonempty_string(submission_id):
        return None
    submission_row = session.execute(
        select(TaskSubmissionRow.__table__)
        .where(TaskSubmissionRow.__table__.c.id == submission_id)
        .with_for_update(skip_locked=True)
    ).mappings().one_or_none()
    if submission_row is None:
        return None
    external_submissions = session.execute(
        select(ExternalSubmissionRow.__table__)
        .where(ExternalSubmissionRow.__table__.c.step_id == step["id"])
        .order_by(ExternalSubmissionRow.__table__.c.id)
        .with_for_update()
    ).mappings().all()
    return _LockedGraph(
        owner_id=owner_id,
        task_id=task_id,
        submission_id=submission_id,
        task=task,
        unit=unit,
        step=step,
        submission=dict(submission_row),
        external_submissions=tuple(dict(row) for row in external_submissions),
    )


def _lock_claimed_graph(session: Session, claim: ClaimAuthority) -> _LockedGraph | None:
    if (
        not isinstance(claim, ClaimAuthority)
        or claim.scope.operation != _OPERATION
        or claim.local_lost_signal.is_set()
    ):
        return None
    return _lock_registered_graph(session, claim.scope.user_id, claim.scope.task_id)


def _registered_graph_matches(graph: _LockedGraph) -> bool:
    task = graph.task
    unit = graph.unit
    step = graph.step
    submission = graph.submission
    request_data = _json_object(task.get("request_data"))
    snapshot = step.get("input_snapshot")
    if request_data is None or not isinstance(snapshot, dict):
        return False

    attempt_id = snapshot.get("server_attempt_id")
    payload_id = snapshot.get("prepared_payload_id")
    protected_ref = snapshot.get("protected_ref")
    snapshot_digest = snapshot.get("snapshot_digest")
    manifest_digest = snapshot.get("ordered_manifest_digest")
    submission_id = submission.get("id")
    return all(
        (
            graph.owner_id == task.get("user_id") == submission.get("user_id"),
            graph.task_id == task.get("id"),
            task.get("type") == _TASK_TYPE,
            task.get("credit_cost") == unit.get("quoted_amount"),
            submission_id == graph.submission_id,
            submission.get("operation") == _OPERATION,
            submission.get("status") == "accepted",
            submission.get("task_ids") == [graph.task_id],
            _nonempty_string(submission.get("idempotency_key")),
            _is_digest(submission.get("request_digest")),
            unit.get("task_id") == graph.task_id,
            unit.get("operation") == _OPERATION,
            unit.get("step_graph_version") == _GRAPH_VERSION,
            _nonempty_string(unit.get("item_key")),
            _is_digest(unit.get("input_digest")),
            unit.get("input_digest") == submission.get("request_digest"),
            step.get("billing_unit_id") == unit.get("id"),
            step.get("step_key") == "generate",
            step.get("required") is True,
            step.get("step_order") == 0,
            step.get("dependencies") in ([], ()),
            step.get("input_digest") == unit.get("input_digest"),
            set(snapshot) == _STEP_SNAPSHOT_KEYS,
            _nonempty_string(attempt_id),
            attempt_id == f"{submission_id}:0",
            attempt_id == unit.get("item_key"),
            _nonempty_string(payload_id),
            _nonempty_string(protected_ref),
            _is_digest(snapshot_digest),
            _is_digest(manifest_digest),
            request_data.get("operation") == _OPERATION,
            request_data.get("submission_id") == submission_id,
            request_data.get("parent_attempt_id") == submission_id,
            request_data.get("server_attempt_id") == attempt_id,
            request_data.get("item_id") == attempt_id,
            request_data.get("unit_key") == attempt_id,
            request_data.get("billing_unit_id") == unit.get("id"),
            request_data.get("step_graph_version") == _GRAPH_VERSION,
            request_data.get("request_digest") == unit.get("input_digest"),
            request_data.get("snapshot_digest") == snapshot_digest,
            request_data.get("prepared_payload_id") == payload_id,
            request_data.get("protected_ref") == protected_ref,
            request_data.get("target_ref") == f"{protected_ref}#target",
            request_data.get("model_ref") == f"{protected_ref}#model",
            request_data.get("grant_ref") == f"{protected_ref}#grant",
            _nonempty_string(request_data.get("rule_digest")),
        )
    )


def _lease_is_available(task: dict[str, object], now: datetime) -> bool:
    if task.get("user_cancelled_at") is not None:
        return False
    lease = task.get("lease_until")
    if task.get("status") == "queued":
        return lease is None or _aware_utc(lease) <= now
    if task.get("status") in {"processing", "reconciling"}:
        return lease is not None and _aware_utc(lease) <= now
    return False


def _lease_is_active(task: dict[str, object], now: datetime) -> bool:
    lease = task.get("lease_until")
    return lease is not None and _aware_utc(lease) > now


def _item_is_unstarted(unit: dict[str, object], step: dict[str, object]) -> bool:
    return all(
        (
            unit.get("first_intent_at") is None,
            unit.get("deadline_at") is None,
            unit.get("success_confirmed_at") is None,
            unit.get("user_cancelled_at") is None,
            unit.get("cancellation_reason") is None,
            unit.get("result_status") == "pending",
            step.get("execution_status") == "prepared",
        )
    )


def _claim_available_for_renewal(
    graph: _LockedGraph,
    now: datetime,
) -> bool:
    task = graph.task
    return bool(
        task.get("status") == "processing"
        and task.get("user_cancelled_at") is None
        and _lease_is_active(task, now)
        and (
            (not graph.external_submissions and _item_is_unstarted(graph.unit, graph.step))
            or _query_resume_item_is_valid(graph, now)
        )
    )


def _claim_is_available(graph: _LockedGraph, now: datetime) -> bool:
    return bool(
        not graph.external_submissions
        and _lease_is_available(graph.task, now)
        and _item_is_unstarted(graph.unit, graph.step)
    ) or bool(
        _query_resume_lease_is_available(graph.task, now)
        and _query_resume_item_is_valid(graph, now)
    )


def _query_resume_lease_is_available(
    task: dict[str, object], now: datetime
) -> bool:
    if task.get("user_cancelled_at") is not None:
        return False
    status = task.get("status")
    lease = task.get("lease_until")
    if lease is None:
        return (
            status in {"queued", "reconciling"}
            and task.get("claimed_by") is None
            and task.get("claim_token") is None
        )
    return bool(
        status in {"processing", "reconciling"}
        and isinstance(lease, datetime)
        and _aware_utc(lease) <= now
        and _nonempty_string(task.get("claimed_by"))
        and _nonempty_string(task.get("claim_token"))
    )


def _query_resume_item_is_valid(graph: _LockedGraph, now: datetime) -> bool:
    task = graph.task
    unit = graph.unit
    step = graph.step
    if len(graph.external_submissions) != 1:
        return False
    external = graph.external_submissions[0]
    external_id = external.get("external_task_id")
    submitted_at = external.get("submitted_at")
    intent_at = external.get("intent_at")
    deadline_at = external.get("deadline_at")
    first_intent_at = unit.get("first_intent_at")
    unit_deadline_at = unit.get("deadline_at")
    request_data = _json_object(task.get("request_data"))
    snapshot = step.get("input_snapshot")
    request_snapshot = external.get("request_snapshot")
    if (
        not _safe_provider_id(external_id)
        or not all(
            isinstance(value, datetime)
            for value in (submitted_at, intent_at, deadline_at, first_intent_at, unit_deadline_at)
        )
        or not isinstance(request_data, dict)
        or not isinstance(snapshot, dict)
        or not isinstance(request_snapshot, dict)
        or set(request_snapshot)
        != {
            "owner_id", "task_id", "unit_id", "step_id", "operation",
            "graph_version", "attempt_id", "prepared_payload_id", "protected_ref",
            "snapshot_digest", "snapshot_ref", "credential_binding_ref",
            "target_snapshot", "provider", "model_id",
        }
    ):
        return False

    status = external.get("submission_status")
    if status == "submitted":
        state_matches = (
            task.get("result") is None
            and unit.get("result_status") == "pending"
            and step.get("execution_status") == "submitting"
        )
    elif status == "unknown":
        state_matches = (
            task.get("result") == _RECONCILING_RESULT
            and unit.get("result_status") == "unknown"
            and step.get("execution_status") == "reconciling"
        )
    else:
        return False

    try:
        first_intent = _aware_utc(first_intent_at)
        intent = _aware_utc(intent_at)
        submitted = _aware_utc(submitted_at)
        deadline = _aware_utc(deadline_at)
        unit_deadline = _aware_utc(unit_deadline_at)
    except (TypeError, ValueError):
        return False
    expected_snapshot_ref = (
        f"{_GRAPH_VERSION}:{snapshot.get('prepared_payload_id')}:"
        f"{snapshot.get('snapshot_digest')}:{snapshot.get('ordered_manifest_digest')}"
    )
    return all(
        (
            task.get("status") in {"queued", "processing", "reconciling"},
            task.get("user_cancelled_at") is None,
            unit.get("user_cancelled_at") is None,
            unit.get("success_confirmed_at") is None,
            unit.get("billing_status") in {"charged", "no_charge"},
            task.get("billing_status") == unit.get("billing_status"),
            unit.get("refunded_amount") == 0,
            unit.get("result_status") in {"pending", "unknown"},
            step.get("execution_status") in {"submitting", "reconciling"},
            state_matches,
            _nonempty_string(external.get("id")),
            external.get("step_id") == step.get("id"),
            external.get("submission_key")
            == f"video-single-create-v1:{unit.get('id')}:{step.get('id')}",
            external.get("provider") in {"xai", "manxiaobai", "aistarslab"},
            _nonempty_string(external.get("model_ref")),
            external.get("request_digest") == unit.get("input_digest"),
            external.get("submission_status") in {"submitted", "unknown"},
            _same_time(intent, first_intent),
            _same_time(deadline, unit_deadline),
            deadline == first_intent + timedelta(hours=2),
            first_intent <= submitted < deadline,
            now < deadline,
            request_snapshot.get("owner_id") == graph.owner_id,
            request_snapshot.get("task_id") == graph.task_id,
            request_snapshot.get("unit_id") == unit.get("id"),
            request_snapshot.get("step_id") == step.get("id"),
            request_snapshot.get("operation") == _OPERATION,
            request_snapshot.get("graph_version") == _GRAPH_VERSION,
            request_snapshot.get("attempt_id") == snapshot.get("server_attempt_id"),
            request_snapshot.get("prepared_payload_id")
            == snapshot.get("prepared_payload_id"),
            request_snapshot.get("protected_ref") == snapshot.get("protected_ref"),
            request_snapshot.get("snapshot_digest") == snapshot.get("snapshot_digest"),
            request_snapshot.get("snapshot_ref") == expected_snapshot_ref,
            request_snapshot.get("target_snapshot") == request_data.get("target_snapshot"),
            request_snapshot.get("provider") == external.get("provider"),
            request_snapshot.get("model_id") == external.get("model_ref"),
            _nonempty_string(request_snapshot.get("credential_binding_ref")),
        )
    )


def _claim_live(task: dict[str, object], claim: ClaimAuthority, now: datetime) -> bool:
    return bool(
        _claim_matches(task, claim)
        and task.get("status") == "processing"
        and task.get("user_cancelled_at") is None
        and _lease_is_active(task, now)
        and not claim.local_lost_signal.is_set()
    )


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


def _task_operation_matches(task: dict[str, object], operation: str) -> bool:
    request_data = _json_object(task.get("request_data"))
    return request_data is not None and request_data.get("operation") == operation


def _claim_stage_predicates(graph: _LockedGraph) -> tuple[object, ...]:
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
        table.c.request_data == task.get("request_data"),
    )


def _release_predicates(
    task: dict[str, object], claim: ClaimAuthority
) -> tuple[object, ...]:
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


def _json_object(value: object) -> dict[str, object] | None:
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _safe_provider_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and value == value.strip()
        and len(value.encode("utf-8")) <= 255
        and not any(ord(character) < 0x20 for character in value)
    )


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


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


__all__ = ("SqlAlchemyVideoSingleClaims",)
