"""SQL-backed per-item leases for the registered image.batch graph."""

from __future__ import annotations

import json
import re
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Event

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.types import ClaimAuthority, OwnerTaskScope
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    ExecutionStepRow,
    TaskSubmissionRow,
    UserRow,
)

_TASK_TYPE = "batch-image"
_OPERATION = "image.batch"
_GRAPH_VERSION = "image-batch-v1"
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
_ACTIVE_STATUSES = ("queued", "processing", "reconciling")
_DEFAULT_LEASE_SECONDS = 60
_CANDIDATE_PAGE_SIZE = 128
_MAX_BATCH_ITEMS = 64

SessionFactory = Callable[[], Session]
DbClock = Callable[[], datetime]
IdFactory = Callable[[], str]


@dataclass(frozen=True, slots=True)
class _Candidate:
    owner_id: str
    task_id: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class _LockedBatch:
    owner_id: str
    submission_id: str
    ordered_task_ids: tuple[str, ...]
    manifest_digest: str
    tasks_by_id: dict[str, dict[str, object]]
    units_by_task: dict[str, dict[str, object]]
    steps_by_task: dict[str, dict[str, object]]


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
    ordered_task_ids: tuple[str, ...]
    manifest_digest: str

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
class SqlAlchemyImageBatchClaims:
    """Acquire one image.batch task lease without authorizing external work."""

    session_factory: SessionFactory
    db_clock: DbClock
    id_factory: IdFactory = field(default=lambda: secrets.token_urlsafe(32))
    lease_seconds: int = _DEFAULT_LEASE_SECONDS
    _signals: dict[str, Event] = field(default_factory=dict, init=False, repr=False)
    _signals_lock: threading.RLock = field(
        default_factory=threading.RLock, init=False, repr=False
    )

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
            lease_until = self._renew_and_commit(claim, expected)
        except Exception:
            lease_until = (
                self._confirm_renewal(claim, expected[0]) if expected else None
            )
        if lease_until is None:
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
                    claim = self._confirm_claim(expected[0], signal) if expected else None
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
            table.c.status.in_(_ACTIVE_STATUSES),
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
                graph = _lock_registered_batch(
                    session, candidate.owner_id, candidate.task_id
                )
                if graph is None:
                    return None
                task = graph.tasks_by_id[candidate.task_id]
                unit = graph.units_by_task[candidate.task_id]
                step = graph.steps_by_task[candidate.task_id]
                now = self._server_now()
                if not _claim_available(task, unit, step, now):
                    return None
                table = AITaskRow.__table__
                if session.execute(
                    select(table.c.id).where(table.c.claim_token == token).limit(1)
                ).scalar_one_or_none() is not None:
                    return None

                generation = int(task["execution_generation"]) + 1
                lease_until = now + timedelta(seconds=self.lease_seconds)
                result = session.execute(
                    update(table)
                    .where(*_claim_predicates(candidate.owner_id, candidate.task_id, task))
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
                write = _ClaimExpectation(
                    owner_id=candidate.owner_id,
                    task_id=candidate.task_id,
                    instance=instance,
                    token=token,
                    generation=generation,
                    lease_until=lease_until,
                    request_data=str(task["request_data"]),
                    submission_id=graph.submission_id,
                    ordered_task_ids=graph.ordered_task_ids,
                    manifest_digest=graph.manifest_digest,
                )
                expected.append(write)
                session.flush()
                return write.authority(signal)

    def _renew_and_commit(
        self, claim: ClaimAuthority, expected: list[_RenewExpectation]
    ) -> datetime | None:
        with self.session_factory() as session:
            with session.begin():
                graph = _lock_claimed_batch(session, claim)
                now = self._server_now()
                if graph is None:
                    return None
                task = graph.tasks_by_id[claim.scope.task_id]
                if not _claim_live(task, claim, now):
                    return None
                old_lease = _aware_utc(task["lease_until"])
                lease_until = now + timedelta(seconds=self.lease_seconds)
                if lease_until <= old_lease:
                    return None
                table = AITaskRow.__table__
                result = session.execute(
                    update(table)
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
                graph = _lock_registered_batch(
                    session, claim.scope.user_id, claim.scope.task_id
                )
                now = self._server_now()
                if graph is None:
                    return False
                task = graph.tasks_by_id[claim.scope.task_id]
                if not _claim_matches(task, claim):
                    return False
                release_status = (
                    "queued"
                    if task.get("status") == "processing"
                    and task.get("user_cancelled_at") is None
                    else task.get("status")
                )
                if not isinstance(release_status, str):
                    return False
                write = _ReleaseExpectation(
                    status=release_status,
                    updated_at=now,
                    user_cancelled_at=task.get("user_cancelled_at"),
                    cancellation_reason=task.get("cancellation_reason"),
                    result=task.get("result"),
                    request_data=str(task["request_data"]),
                )
                table = AITaskRow.__table__
                result = session.execute(
                    update(table)
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
                    graph = _lock_registered_batch(
                        session, expected.owner_id, expected.task_id
                    )
                    now = self._server_now()
                    if graph is None or not _expectation_matches_graph(expected, graph):
                        return None
                    task = graph.tasks_by_id[expected.task_id]
                    if (
                        task.get("status") != "processing"
                        or task.get("claimed_by") != expected.instance
                        or task.get("claim_token") != expected.token
                        or task.get("execution_generation") != expected.generation
                        or task.get("lease_until") is None
                        or _aware_utc(task["lease_until"]) != expected.lease_until
                        or task.get("request_data") != expected.request_data
                        or not _claim_available_for_renewal(
                            task,
                            graph.units_by_task[expected.task_id],
                            graph.steps_by_task[expected.task_id],
                            now,
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
                    graph = _lock_claimed_batch(session, claim)
                    now = self._server_now()
                    if graph is None:
                        return None
                    task = graph.tasks_by_id[claim.scope.task_id]
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
                    graph = _lock_registered_batch(
                        session, claim.scope.user_id, claim.scope.task_id
                    )
                    if graph is None:
                        return False
                    task = graph.tasks_by_id[claim.scope.task_id]
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

    def _signal_for(self, claim: ClaimAuthority) -> Event | None:
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


def _lock_registered_batch(
    session: Session, owner_id: str, selected_task_id: str
) -> _LockedBatch | None:
    task_table = AITaskRow.__table__
    unit_table = BillingUnitRow.__table__
    step_table = ExecutionStepRow.__table__
    parent_table = TaskSubmissionRow.__table__

    # This preliminary read only discovers the parent lock set. Its values are
    # checked again after the parent row is locked at the end of the lock order.
    selected_probe = session.execute(
        select(task_table.c.request_data).where(
            task_table.c.id == selected_task_id,
            task_table.c.user_id == owner_id,
            task_table.c.type == _TASK_TYPE,
        )
    ).scalar_one_or_none()
    request_probe = _json_object(selected_probe)
    if request_probe is None:
        return None
    submission_id = request_probe.get("submission_id")
    if not _nonempty_string(submission_id):
        return None
    parent_probe = session.execute(
        select(parent_table).where(parent_table.c.id == submission_id)
    ).mappings().one_or_none()
    if parent_probe is None:
        return None
    ordered_task_ids = _ordered_task_ids(parent_probe.get("task_ids"))
    if ordered_task_ids is None or selected_task_id not in ordered_task_ids:
        return None

    owner = session.execute(
        select(UserRow.id)
        .where(UserRow.id == owner_id)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()
    if owner is None:
        return None

    task_rows = session.execute(
        select(task_table)
        .where(
            task_table.c.id.in_(ordered_task_ids),
            task_table.c.user_id == owner_id,
        )
        .order_by(task_table.c.id)
        .with_for_update(skip_locked=True)
    ).mappings().all()
    if len(task_rows) != len(ordered_task_ids):
        return None
    tasks_by_id = {row["id"]: dict(row) for row in task_rows}

    unit_rows = session.execute(
        select(unit_table)
        .where(unit_table.c.task_id.in_(ordered_task_ids))
        .order_by(unit_table.c.task_id, unit_table.c.id)
        .with_for_update()
    ).mappings().all()
    if len(unit_rows) != len(ordered_task_ids):
        return None
    units_by_task: dict[str, dict[str, object]] = {}
    for row in unit_rows:
        task_id = row["task_id"]
        if task_id in units_by_task:
            return None
        units_by_task[task_id] = dict(row)
    if set(units_by_task) != set(ordered_task_ids):
        return None

    unit_ids = tuple(unit["id"] for unit in units_by_task.values())
    step_rows = session.execute(
        select(step_table)
        .where(step_table.c.billing_unit_id.in_(unit_ids))
        .order_by(step_table.c.billing_unit_id, step_table.c.step_order, step_table.c.id)
        .with_for_update()
    ).mappings().all()
    if len(step_rows) != len(ordered_task_ids):
        return None
    steps_by_unit: dict[str, dict[str, object]] = {}
    for row in step_rows:
        unit_id = row["billing_unit_id"]
        if unit_id in steps_by_unit:
            return None
        steps_by_unit[unit_id] = dict(row)
    if set(steps_by_unit) != set(unit_ids):
        return None
    steps_by_task = {
        task_id: steps_by_unit[units_by_task[task_id]["id"]]
        for task_id in ordered_task_ids
    }

    parent = session.execute(
        select(parent_table)
        .where(parent_table.c.id == submission_id)
        .with_for_update(skip_locked=True)
    ).mappings().one_or_none()
    if parent is None:
        return None
    parent = dict(parent)
    if (
        parent.get("task_ids") != list(ordered_task_ids)
        or parent.get("user_id") != owner_id
        or parent.get("id") != submission_id
    ):
        return None

    manifest_digest = _registered_batch_matches(
        owner_id,
        submission_id,
        ordered_task_ids,
        parent,
        tasks_by_id,
        units_by_task,
        steps_by_task,
    )
    if manifest_digest is None:
        return None
    return _LockedBatch(
        owner_id,
        submission_id,
        ordered_task_ids,
        manifest_digest,
        tasks_by_id,
        units_by_task,
        steps_by_task,
    )


def _registered_batch_matches(
    owner_id: str,
    submission_id: str,
    ordered_task_ids: tuple[str, ...],
    parent: dict[str, object],
    tasks_by_id: dict[str, dict[str, object]],
    units_by_task: dict[str, dict[str, object]],
    steps_by_task: dict[str, dict[str, object]],
) -> str | None:
    if (
        parent.get("operation") != _OPERATION
        or parent.get("status") != "accepted"
        or not _nonempty_string(parent.get("idempotency_key"))
        or not _is_digest(parent.get("request_digest"))
        or not tasks_by_id
        or len(tasks_by_id) != len(ordered_task_ids)
    ):
        return None

    manifest_digests: set[str] = set()
    item_keys: set[str] = set()
    payload_ids: set[str] = set()
    protected_refs: set[str] = set()
    message_ids: set[str] = set()
    for item_index, task_id in enumerate(ordered_task_ids):
        task = tasks_by_id.get(task_id)
        unit = units_by_task.get(task_id)
        step = steps_by_task.get(task_id)
        if task is None or unit is None or step is None:
            return None
        request_data = _json_object(task.get("request_data"))
        snapshot = step.get("input_snapshot")
        if not isinstance(snapshot, dict) or set(snapshot) != _STEP_SNAPSHOT_KEYS:
            return None
        if (
            task.get("id") != task_id
            or task.get("user_id") != owner_id
            or task.get("type") != _TASK_TYPE
            or task.get("credit_cost") != unit.get("quoted_amount")
            or unit.get("task_id") != task_id
            or unit.get("operation") != _OPERATION
            or unit.get("step_graph_version") != _GRAPH_VERSION
            or unit.get("item_key") != snapshot.get("server_attempt_id")
            or not _is_digest(unit.get("input_digest"))
            or unit.get("input_digest") != parent.get("request_digest")
            or step.get("billing_unit_id") != unit.get("id")
            or step.get("step_key") != "generate"
            or step.get("required") is not True
            or step.get("step_order") != 0
            or step.get("dependencies") not in ([], ())
            or step.get("input_digest") != unit.get("input_digest")
            or request_data is None
        ):
            return None

        attempt_id = snapshot.get("server_attempt_id")
        payload_id = snapshot.get("prepared_payload_id")
        protected_ref = snapshot.get("protected_ref")
        snapshot_digest = snapshot.get("snapshot_digest")
        manifest_digest = snapshot.get("ordered_manifest_digest")
        if (
            not _nonempty_string(attempt_id)
            or not _nonempty_string(payload_id)
            or not _nonempty_string(protected_ref)
            or not _is_digest(snapshot_digest)
            or not _is_digest(manifest_digest)
            or not _nonempty_string(task.get("message_id"))
            or task.get("message_id") in message_ids
            or request_data.get("operation") != _OPERATION
            or request_data.get("submission_id") != submission_id
            or request_data.get("parent_attempt_id") != submission_id
            or attempt_id != f"{submission_id}:{item_index}"
            or request_data.get("server_attempt_id") != attempt_id
            or request_data.get("item_id") != attempt_id
            or request_data.get("unit_key") != attempt_id
            or request_data.get("billing_unit_id") != unit.get("id")
            or request_data.get("step_graph_version") != _GRAPH_VERSION
            or request_data.get("request_digest") != unit.get("input_digest")
            or request_data.get("snapshot_digest") != snapshot_digest
            or request_data.get("prepared_payload_id") != payload_id
            or request_data.get("protected_ref") != protected_ref
            or request_data.get("target_ref") != f"{protected_ref}#target"
            or request_data.get("model_ref") != f"{protected_ref}#model"
            or request_data.get("grant_ref") != f"{protected_ref}#grant"
            or request_data.get("reference_ref") != f"{protected_ref}#references"
            or attempt_id in item_keys
            or payload_id in payload_ids
            or protected_ref in protected_refs
        ):
            return None

        item_keys.add(attempt_id)
        payload_ids.add(payload_id)
        protected_refs.add(protected_ref)
        message_ids.add(task["message_id"])
        manifest_digests.add(manifest_digest)

    if len(manifest_digests) != 1:
        return None
    return next(iter(manifest_digests))


def _lock_claimed_batch(session: Session, claim: ClaimAuthority) -> _LockedBatch | None:
    if (
        not isinstance(claim, ClaimAuthority)
        or claim.scope.operation != _OPERATION
        or claim.local_lost_signal.is_set()
    ):
        return None
    return _lock_registered_batch(session, claim.scope.user_id, claim.scope.task_id)


def _expectation_matches_graph(
    expected: _ClaimExpectation, graph: _LockedBatch
) -> bool:
    return all(
        (
            graph.owner_id == expected.owner_id,
            graph.submission_id == expected.submission_id,
            graph.ordered_task_ids == expected.ordered_task_ids,
            graph.manifest_digest == expected.manifest_digest,
        )
    )


def _claim_available(
    task: dict[str, object],
    unit: dict[str, object],
    step: dict[str, object],
    now: datetime,
) -> bool:
    return _lease_available(task, now) and _item_is_unstarted(unit, step)


def _claim_available_for_renewal(
    task: dict[str, object],
    unit: dict[str, object],
    step: dict[str, object],
    now: datetime,
) -> bool:
    lease = task.get("lease_until")
    return bool(
        task.get("status") == "processing"
        and task.get("user_cancelled_at") is None
        and lease is not None
        and _aware_utc(lease) > now
        and _item_is_unstarted(unit, step)
    )


def _item_is_unstarted(
    unit: dict[str, object], step: dict[str, object]
) -> bool:
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


def _lease_available(task: dict[str, object], now: datetime) -> bool:
    if task.get("user_cancelled_at") is not None:
        return False
    lease = task.get("lease_until")
    if task.get("status") == "queued":
        return lease is None or _aware_utc(lease) <= now
    if task.get("status") in {"processing", "reconciling"}:
        return lease is not None and _aware_utc(lease) <= now
    return False


def _claim_live(
    task: dict[str, object],
    claim: ClaimAuthority,
    now: datetime,
) -> bool:
    lease = task.get("lease_until")
    return bool(
        _claim_matches(task, claim)
        and task.get("status") == "processing"
        and task.get("user_cancelled_at") is None
        and lease is not None
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


def _claim_predicates(
    owner_id: str, task_id: str, task: dict[str, object]
) -> tuple[object, ...]:
    table = AITaskRow.__table__
    return (
        table.c.id == task_id,
        table.c.user_id == owner_id,
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


def _ordered_task_ids(value: object) -> tuple[str, ...] | None:
    if not isinstance(value, list) or not 1 <= len(value) <= _MAX_BATCH_ITEMS:
        return None
    if any(not _nonempty_string(task_id) for task_id in value):
        return None
    if len(set(value)) != len(value):
        return None
    return tuple(value)


def _json_object(value: object) -> dict[str, object] | None:
    if not isinstance(value, str):
        return None
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


def _nullable_predicate(column, value: object):
    return column.is_(None) if value is None else column == value


def _aware_utc(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("persisted claim lease is invalid")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _database_datetime(value: datetime) -> datetime:
    return _aware_utc(value).replace(tzinfo=None)


def _same_time(left: object, right: datetime) -> bool:
    try:
        return _aware_utc(left) == _aware_utc(right)
    except (TypeError, ValueError):
        return False


__all__ = ["SqlAlchemyImageBatchClaims"]
