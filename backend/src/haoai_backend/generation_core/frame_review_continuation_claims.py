"""SQL claim fencing for durable frame.review continuation work."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from threading import Event, RLock

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.types import (
    ContinuationClaimAuthority,
    OwnerTaskScope,
)
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
    UserRow,
)
from haoai_backend.generation_core.schema import (
    ApplicationRecordRow,
    GenerationResultReceiptRow,
    LocalContinuationWorkRow,
    UnitFeeDecisionRow,
)

_TASK_TYPE = "ai-review"
_OPERATION = "frame.review"
_POLICY_VERSION = "local_continuation_v1"
_PHASE_ORDER = ("delivery_pending", "fee_pending", "finalize_pending")
_ELIGIBLE_PHASES = (*_PHASE_ORDER,)
_TERMINAL_PHASES = {
    "done",
    "stopped_insufficient",
    "stopped_delivery_failed",
    "stopped_late_generation",
}
_DEFAULT_LEASE_SECONDS = 60

SessionFactory = Callable[[], Session]
DbClock = Callable[[], datetime]
IdFactory = Callable[[], str]


@dataclass(frozen=True, slots=True)
class _ReleaseExpectation:
    status: str
    updated_at: datetime
    user_cancelled_at: object
    work_phase: str
    work_phase_version: int
    work_last_execution_generation: int | None
    work_application_ref: str | None
    work_fee_decision_ref: str | None
    work_terminal_reason: str | None


@dataclass(slots=True)
class SqlAlchemyFrameReviewContinuationClaims:
    """Claim, renew, and release one exact persisted work phase at a time."""

    session_factory: SessionFactory
    db_clock: DbClock
    id_factory: IdFactory
    lease_seconds: int = _DEFAULT_LEASE_SECONDS
    _signals: dict[str, Event] = field(default_factory=dict, init=False, repr=False)
    _signals_lock: RLock = field(default_factory=RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        if not callable(self.session_factory) or not callable(self.db_clock):
            raise TypeError("continuation session and database clock must be explicit")
        if not callable(self.id_factory):
            raise TypeError("continuation token factory must be callable")
        if type(self.lease_seconds) is not int or self.lease_seconds <= 0:
            raise ValueError("continuation lease must be a positive number of seconds")

    def claim_continuation(
        self, task_type: str, instance: str, lease_seconds: int
    ) -> ContinuationClaimAuthority | None:
        if (
            task_type != _TASK_TYPE
            or not isinstance(instance, str)
            or not instance
            or len(instance) > 64
            or type(lease_seconds) is not int
            or lease_seconds <= 0
        ):
            return None
        try:
            token = self.id_factory()
        except Exception:
            return None
        if not isinstance(token, str) or not token or len(token) > 64:
            return None

        signal = Event()
        cursor: tuple[datetime, str, str] | None = None
        while True:
            candidates = self._candidate_ids(cursor)
            if not candidates:
                return None
            for candidate in candidates:
                authority = self._claim_candidate(
                    candidate, instance, token, signal, lease_seconds
                )
                if authority is not None:
                    with self._signals_lock:
                        self._signals[token] = signal
                    return authority
                cursor = (candidate[3], candidate[1], candidate[2])

    def renew_continuation(
        self, claim: ContinuationClaimAuthority
    ) -> ContinuationClaimAuthority | None:
        signal = self._signal_for(claim)
        if signal is None or signal.is_set():
            return None
        try:
            with self.session_factory() as session:
                with session.begin():
                    graph = self._lock_claim_graph(session, claim)
                    now = self._server_now()
                    if graph is None or not _claim_is_live(graph["task"], claim, now):
                        return None
                    lease_until = now + timedelta(seconds=self.lease_seconds)
                    task_table = AITaskRow.__table__
                    changed = session.execute(
                        update(task_table)
                        .where(
                            task_table.c.id == claim.owner_scope.task_id,
                            task_table.c.user_id == claim.owner_scope.user_id,
                            task_table.c.status == "processing",
                            task_table.c.claimed_by == claim.worker_instance,
                            task_table.c.claim_token == claim.token,
                            task_table.c.execution_generation == claim.execution_generation,
                            task_table.c.lease_until == _db_time(claim.lease_until),
                            task_table.c.lease_until > _db_time(now),
                            task_table.c.user_cancelled_at.is_(None),
                        )
                        .values(lease_until=_db_time(lease_until), updated_at=_db_time(now))
                    )
                    if changed.rowcount != 1:
                        return None
                    return replace(claim, lease_until=lease_until)
        except Exception:
            return None

    def release_continuation(self, claim: ContinuationClaimAuthority) -> bool:
        signal = self._signal_for(claim)
        if signal is None:
            return False
        released = False
        expected_release: list[_ReleaseExpectation] = []
        try:
            with self.session_factory() as session:
                with session.begin():
                    graph = self._lock_claim_graph(
                        session,
                        claim,
                        require_phase_version=False,
                        require_lease_until=False,
                    )
                    if graph is None or not _claim_identity_matches(graph["task"], claim):
                        return False
                    task = graph["task"]
                    work = graph["work"]
                    status = task["status"]
                    if not _release_transition_is_authorized(graph, claim):
                        return False
                    phase = work["phase"]
                    if phase not in _TERMINAL_PHASES:
                        status = "queued"
                    elif phase == "done":
                        status = "completed"
                    else:
                        status = "failed"
                    now = self._server_now()
                    updated_at = _db_time(now)
                    table = AITaskRow.__table__
                    changed = session.execute(
                        update(table)
                        .where(
                            table.c.id == claim.owner_scope.task_id,
                            table.c.user_id == claim.owner_scope.user_id,
                            table.c.status == task["status"],
                            table.c.claimed_by == claim.worker_instance,
                            table.c.claim_token == claim.token,
                            table.c.execution_generation == claim.execution_generation,
                            table.c.lease_until == task["lease_until"],
                            table.c.user_cancelled_at == task["user_cancelled_at"],
                        )
                        .values(
                            status=status,
                            claimed_by=None,
                            claim_token=None,
                            lease_until=None,
                            updated_at=updated_at,
                        )
                    )
                    released = changed.rowcount == 1
                    if released:
                        expected_release.append(
                            _ReleaseExpectation(
                                status=status,
                                updated_at=updated_at,
                                user_cancelled_at=task["user_cancelled_at"],
                                work_phase=work["phase"],
                                work_phase_version=work["phase_version"],
                                work_last_execution_generation=work[
                                    "last_execution_generation"
                                ],
                                work_application_ref=work.get("application_ref"),
                                work_fee_decision_ref=work.get("fee_decision_ref"),
                                work_terminal_reason=work.get("terminal_reason"),
                            )
                        )
        except Exception:
            released = self._confirm_release(
                claim, expected_release[0] if expected_release else None
            )
        finally:
            signal.set()
            with self._signals_lock:
                self._signals.pop(claim.token, None)
        return released

    def _confirm_release(
        self,
        claim: ContinuationClaimAuthority,
        expected: _ReleaseExpectation | None,
    ) -> bool:
        """Confirm only this transaction's exact Task and phase projection."""
        if expected is None:
            return False
        task = AITaskRow.__table__
        work = LocalContinuationWorkRow.__table__
        try:
            with self.session_factory() as session:
                task_row = session.execute(
                    select(task).where(
                        task.c.id == claim.owner_scope.task_id,
                        task.c.user_id == claim.owner_scope.user_id,
                        task.c.type == _TASK_TYPE,
                        task.c.status == expected.status,
                        task.c.claimed_by.is_(None),
                        task.c.claim_token.is_(None),
                        task.c.lease_until.is_(None),
                        task.c.execution_generation == claim.execution_generation,
                        task.c.user_cancelled_at == expected.user_cancelled_at,
                        task.c.updated_at == expected.updated_at,
                    )
                ).mappings().one_or_none()
                if task_row is None:
                    return False
                work_row = session.execute(
                    select(work).where(
                        work.c.work_id == claim.work_id,
                        work.c.owner_id == claim.owner_scope.user_id,
                        work.c.task_id == claim.owner_scope.task_id,
                        work.c.unit_id == claim.unit_id,
                        work.c.result_ref == claim.result_ref,
                        work.c.operation == _OPERATION,
                        work.c.policy_version == _POLICY_VERSION,
                        work.c.phase == expected.work_phase,
                        work.c.phase_version == expected.work_phase_version,
                        work.c.last_execution_generation
                        == expected.work_last_execution_generation,
                        work.c.application_ref == expected.work_application_ref,
                        work.c.fee_decision_ref == expected.work_fee_decision_ref,
                        work.c.terminal_reason == expected.work_terminal_reason,
                    )
                ).mappings().one_or_none()
                return work_row is not None
        except Exception:
            return False

    def lock_current_claim(
        self,
        session: Session,
        claim: ContinuationClaimAuthority,
        now: datetime,
    ) -> dict[str, dict[str, object]] | None:
        """Lock and validate the exact live work phase in a caller transaction."""

        graph = self._lock_claim_graph(session, claim, require_lease_until=False)
        if graph is None or not _claim_identity_matches(graph["task"], claim):
            return None
        lease_until = graph["task"].get("lease_until")
        if lease_until is None or _as_utc(lease_until) <= now:
            return None
        return graph

    def _candidate_ids(
        self, cursor: tuple[datetime, str, str] | None
    ) -> list[tuple[str, str, str, datetime]]:
        task = AITaskRow.__table__
        unit = BillingUnitRow.__table__
        work = LocalContinuationWorkRow.__table__
        result = GenerationResultReceiptRow.__table__
        now = self._server_now()
        conditions = [
            task.c.type == _TASK_TYPE,
            task.c.status.in_(("queued", "processing", "reconciling")),
            task.c.user_cancelled_at.is_(None),
            or_(
                task.c.status.in_(("queued", "reconciling")),
                task.c.lease_until.is_(None),
                task.c.lease_until <= _db_time(now),
            ),
            unit.c.operation == _OPERATION,
            unit.c.result_status == "succeeded",
            work.c.policy_version == _POLICY_VERSION,
            work.c.operation == _OPERATION,
            work.c.phase.in_(_ELIGIBLE_PHASES),
            result.c.operation == _OPERATION,
        ]
        if cursor is not None:
            generated_at, task_id, work_id = cursor
            stored_time = _db_time(generated_at)
            conditions.append(
                or_(
                    work.c.generation_fulfilled_at > stored_time,
                    and_(work.c.generation_fulfilled_at == stored_time, task.c.id > task_id),
                    and_(work.c.generation_fulfilled_at == stored_time, task.c.id == task_id, work.c.work_id > work_id),
                )
            )
        with self.session_factory() as session:
            rows = session.execute(
                select(task.c.user_id, task.c.id, work.c.work_id, work.c.generation_fulfilled_at)
                .join(unit, unit.c.task_id == task.c.id)
                .join(work, and_(work.c.task_id == task.c.id, work.c.unit_id == unit.c.id))
                .join(result, result.c.result_ref == work.c.result_ref)
                .where(*conditions)
                .order_by(work.c.generation_fulfilled_at, task.c.id, work.c.work_id)
                .limit(128)
            ).all()
        return [
            (row.user_id, row.id, row.work_id, _aware_utc(row.generation_fulfilled_at))
            for row in rows
        ]

    def _claim_candidate(
        self,
        candidate: tuple[str, str, str, datetime],
        instance: str,
        token: str,
        signal: Event,
        lease_seconds: int,
    ) -> ContinuationClaimAuthority | None:
        owner_id, task_id, work_id, _generated_at = candidate
        try:
            with self.session_factory() as session:
                with session.begin():
                    user = session.execute(
                        select(UserRow.id).where(UserRow.id == owner_id).with_for_update(skip_locked=True)
                    ).scalar_one_or_none()
                    if user is None:
                        return None
                    task_row = session.execute(
                        select(AITaskRow.__table__)
                        .where(AITaskRow.id == task_id, AITaskRow.user_id == owner_id, AITaskRow.type == _TASK_TYPE)
                        .with_for_update(skip_locked=True)
                    ).mappings().one_or_none()
                    if task_row is None:
                        return None
                    task = dict(task_row)
                    graph = self._lock_work_graph(session, task, work_id)
                    now = self._server_now()
                    if graph is None or not _lease_available(task, now):
                        return None
                    if not _graph_phase_is_claimable(graph):
                        return None
                    if session.execute(
                        select(AITaskRow.claim_token).where(AITaskRow.claim_token == token).limit(1)
                    ).scalar_one_or_none() is not None:
                        return None
                    generation = task["execution_generation"] + 1
                    lease_until = now + timedelta(seconds=min(lease_seconds, self.lease_seconds))
                    task_table = AITaskRow.__table__
                    changed = session.execute(
                        update(task_table)
                        .where(
                            task_table.c.id == task_id,
                            task_table.c.user_id == owner_id,
                            task_table.c.type == _TASK_TYPE,
                            task_table.c.status == task["status"],
                            task_table.c.execution_generation == task["execution_generation"],
                            task_table.c.request_data == task["request_data"],
                            task_table.c.user_cancelled_at.is_(None),
                            _lease_available_clause(task_table, task, now),
                        )
                        .values(
                            status="processing",
                            claimed_by=instance,
                            claim_token=token,
                            execution_generation=generation,
                            lease_until=_db_time(lease_until),
                            updated_at=_db_time(now),
                        )
                    )
                    if changed.rowcount != 1:
                        return None
                    return ContinuationClaimAuthority(
                        owner_scope=OwnerTaskScope(owner_id, task_id, _OPERATION),
                        unit_id=graph["unit"]["id"],
                        work_id=work_id,
                        result_ref=graph["work"]["result_ref"],
                        phase_version=graph["work"]["phase_version"],
                        worker_instance=instance,
                        token=token,
                        execution_generation=generation,
                        lease_until=lease_until,
                    )
        except Exception:
            return None

    def _lock_claim_graph(
        self,
        session: Session,
        claim: ContinuationClaimAuthority,
        *,
        require_phase_version: bool = True,
        require_lease_until: bool = True,
    ) -> dict[str, dict[str, object]] | None:
        if not isinstance(claim, ContinuationClaimAuthority) or claim.owner_scope.operation != _OPERATION:
            return None
        user = session.execute(
            select(UserRow.id).where(UserRow.id == claim.owner_scope.user_id).with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        if user is None:
            return None
        task_row = session.execute(
            select(AITaskRow.__table__)
            .where(AITaskRow.id == claim.owner_scope.task_id, AITaskRow.user_id == claim.owner_scope.user_id, AITaskRow.type == _TASK_TYPE)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        ).mappings().one_or_none()
        if task_row is None:
            return None
        task = dict(task_row)
        graph = self._lock_work_graph(session, task, claim.work_id)
        if graph is None:
            return None
        if (
            graph["unit"]["id"] != claim.unit_id
            or graph["work"]["result_ref"] != claim.result_ref
            or (
                require_phase_version
                and graph["work"]["phase_version"] != claim.phase_version
            )
        ):
            return None
        if require_lease_until and not _same_instant(
            task.get("lease_until"), claim.lease_until
        ):
            return None
        graph["task"] = task
        return graph

    def _lock_work_graph(self, session: Session, task: dict[str, object], work_id: str):
        unit_row = session.execute(
            select(BillingUnitRow.__table__)
            .where(BillingUnitRow.__table__.c.task_id == task["id"], BillingUnitRow.__table__.c.operation == _OPERATION)
            .with_for_update()
        ).mappings().one_or_none()
        if unit_row is None:
            return None
        unit = dict(unit_row)
        work_row = session.execute(
            select(LocalContinuationWorkRow.__table__)
            .where(
                LocalContinuationWorkRow.__table__.c.work_id == work_id,
                LocalContinuationWorkRow.__table__.c.owner_id == task["user_id"],
                LocalContinuationWorkRow.__table__.c.task_id == task["id"],
                LocalContinuationWorkRow.__table__.c.unit_id == unit["id"],
                LocalContinuationWorkRow.__table__.c.operation == _OPERATION,
                LocalContinuationWorkRow.__table__.c.policy_version == _POLICY_VERSION,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).mappings().one_or_none()
        if work_row is None:
            return None
        work = dict(work_row)
        receipt_row = session.execute(
            select(GenerationResultReceiptRow.__table__)
            .where(
                GenerationResultReceiptRow.__table__.c.result_ref == work["result_ref"],
                GenerationResultReceiptRow.__table__.c.owner_id == task["user_id"],
                GenerationResultReceiptRow.__table__.c.task_id == task["id"],
                GenerationResultReceiptRow.__table__.c.unit_id == unit["id"],
                GenerationResultReceiptRow.__table__.c.operation == _OPERATION,
            )
            .with_for_update()
        ).mappings().one_or_none()
        if receipt_row is None:
            return None
        receipt = dict(receipt_row)
        step_row = session.execute(
            select(ExecutionStepRow.__table__)
            .where(ExecutionStepRow.__table__.c.id == receipt["step_id"], ExecutionStepRow.__table__.c.billing_unit_id == unit["id"])
            .with_for_update()
        ).mappings().one_or_none()
        external_row = session.execute(
            select(ExternalSubmissionRow.__table__)
            .where(ExternalSubmissionRow.__table__.c.id == receipt["submission_id"], ExternalSubmissionRow.__table__.c.step_id == receipt["step_id"])
            .with_for_update()
        ).mappings().one_or_none()
        if step_row is None or external_row is None:
            return None
        graph = {
            "task": task,
            "unit": unit,
            "work": work,
            "receipt": receipt,
            "step": dict(step_row),
            "external": dict(external_row),
        }
        if work["phase"] in {"fee_pending", "finalize_pending", "done"}:
            app_ref = work.get("application_ref")
            app_row = session.execute(
                select(ApplicationRecordRow.__table__)
                .where(
                    ApplicationRecordRow.__table__.c.application_ref == app_ref,
                    ApplicationRecordRow.__table__.c.result_ref == receipt["result_ref"],
                    ApplicationRecordRow.__table__.c.task_id == task["id"],
                    ApplicationRecordRow.__table__.c.unit_id == unit["id"],
                    ApplicationRecordRow.__table__.c.delivery_status == "applied",
                )
                .with_for_update()
            ).mappings().one_or_none()
            if app_row is None:
                return None
            graph["application"] = dict(app_row)
        if work["phase"] in {"finalize_pending", "done"}:
            fee_row = session.execute(
                select(UnitFeeDecisionRow.__table__)
                .where(
                    UnitFeeDecisionRow.__table__.c.unit_id == unit["id"],
                    UnitFeeDecisionRow.__table__.c.charge_phase == "post_apply",
                    UnitFeeDecisionRow.__table__.c.generation_result_ref == receipt["result_ref"],
                    UnitFeeDecisionRow.__table__.c.application_ref == work.get("application_ref"),
                    UnitFeeDecisionRow.__table__.c.resolution.in_(
                        ("charged", "no_charge", "waived_insufficient", "waived_charge_failed")
                    ),
                    UnitFeeDecisionRow.__table__.c.resolved_at.is_not(None),
                )
                .with_for_update()
            ).mappings().one_or_none()
            if fee_row is None:
                return None
            graph["fee"] = dict(fee_row)
        return graph

    def _signal_for(self, claim: ContinuationClaimAuthority) -> Event | None:
        if not isinstance(claim, ContinuationClaimAuthority) or claim.owner_scope.operation != _OPERATION:
            return None
        with self._signals_lock:
            return self._signals.get(claim.token)

    def _server_now(self) -> datetime:
        value = self.db_clock()
        if not _aware_datetime(value):
            raise ValueError("database clock must return an aware UTC datetime")
        return _aware_utc(value)


def _graph_phase_is_claimable(graph: dict[str, dict[str, object]]) -> bool:
    task, unit, work, receipt, step, external = (
        graph[key] for key in ("task", "unit", "work", "receipt", "step", "external")
    )
    phase = work.get("phase")
    if (
        task.get("type") != _TASK_TYPE
        or task.get("status") not in ("queued", "processing", "reconciling")
        or task.get("user_cancelled_at") is not None
        or unit.get("result_status") != "succeeded"
        or unit.get("task_id") != task.get("id")
        or unit.get("operation") != _OPERATION
        or work.get("phase_order") != list(_PHASE_ORDER)
        or phase not in _ELIGIBLE_PHASES
        or work.get("phase_version") < 0
        or receipt.get("content_digest") != work.get("content_digest")
        or receipt.get("result_ref") != work.get("result_ref")
        or step.get("execution_status") not in ("result_observed", "applied")
        or external.get("submission_status") != "completed"
        or external.get("submitted_at") is None
        or unit.get("first_intent_at") is None
        or unit.get("deadline_at") is None
        or not _same_instant(unit.get("first_intent_at"), work.get("original_first_intent_at"))
        or not _same_instant(unit.get("deadline_at"), work.get("original_deadline_at"))
    ):
        return False
    if phase == "delivery_pending":
        if receipt.get("automatic_application_allowed") is not True:
            return receipt.get("late_disposition") == "stopped_late_generation"
        return step.get("execution_status") == "result_observed" and work.get("application_ref") is None
    if receipt.get("automatic_application_allowed") is not True:
        return False
    if phase == "fee_pending":
        return step.get("execution_status") == "applied" and bool(work.get("application_ref"))
    return (
        step.get("execution_status") == "applied"
        and bool(work.get("application_ref"))
        and bool(graph.get("application"))
        and bool(graph.get("fee"))
    )


def _lease_available(task: dict[str, object], now: datetime) -> bool:
    if task.get("user_cancelled_at") is not None:
        return False
    status = task.get("status")
    lease = task.get("lease_until")
    if status in {"queued", "reconciling"}:
        return task.get("claimed_by") is None and task.get("claim_token") is None and lease is None
    return (
        status == "processing"
        and (
            lease is None
            or _as_utc(lease) <= now
        )
    )


def _lease_available_clause(table, task: dict[str, object], now: datetime):
    if task.get("status") in {"queued", "reconciling"}:
        return and_(table.c.claimed_by.is_(None), table.c.claim_token.is_(None), table.c.lease_until.is_(None))
    lease = task.get("lease_until")
    return or_(table.c.lease_until.is_(None), table.c.lease_until <= _db_time(now)) if lease is None else table.c.lease_until <= _db_time(now)


def _claim_matches(task: dict[str, object], claim: ContinuationClaimAuthority) -> bool:
    return bool(
        _claim_identity_matches(task, claim)
        and task.get("lease_until") is not None
        and _same_instant(task.get("lease_until"), claim.lease_until)
    )


def _claim_identity_matches(
    task: dict[str, object], claim: ContinuationClaimAuthority
) -> bool:
    return bool(
        task.get("type") == _TASK_TYPE
        and task.get("user_id") == claim.owner_scope.user_id
        and task.get("status") in {"processing", "completed", "failed"}
        and task.get("claimed_by") == claim.worker_instance
        and task.get("claim_token") == claim.token
        and task.get("execution_generation") == claim.execution_generation
        and task.get("lease_until") is not None
        and task.get("user_cancelled_at") is None
    )


def _claim_is_live(task: dict[str, object], claim: ContinuationClaimAuthority, now: datetime) -> bool:
    return _claim_matches(task, claim) and _as_utc(task["lease_until"]) > now


def _release_transition_is_authorized(
    graph: dict[str, dict[str, object]], claim: ContinuationClaimAuthority
) -> bool:
    task = graph["task"]
    work = graph["work"]
    phase_order = tuple(work.get("phase_order") or ())
    phase_version = claim.phase_version
    if (
        phase_order != _PHASE_ORDER
        or type(phase_version) is not int
        or not 0 <= phase_version < len(phase_order)
        or work.get("work_id") != claim.work_id
        or work.get("unit_id") != claim.unit_id
        or work.get("result_ref") != claim.result_ref
    ):
        return False

    claimed_phase = phase_order[phase_version]
    if work.get("phase_version") == phase_version:
        return work.get("phase") == claimed_phase and task.get("status") == "processing"

    if (
        work.get("phase_version") != phase_version + 1
        or work.get("last_execution_generation") != claim.execution_generation
    ):
        return False

    next_phase = (
        phase_order[phase_version + 1]
        if phase_version + 1 < len(phase_order)
        else None
    )
    if work.get("phase") == next_phase:
        if task.get("status") != "processing":
            return False
        if next_phase == "fee_pending":
            application = graph.get("application")
            return bool(
                application
                and application.get("delivery_status") == "applied"
                and application.get("application_ref") == work.get("application_ref")
                and application.get("result_ref") == claim.result_ref
            )
        fee = graph.get("fee")
        application = graph.get("application")
        return bool(
            next_phase == "finalize_pending"
            and application
            and fee
            and application.get("delivery_status") == "applied"
            and application.get("application_ref") == work.get("application_ref")
            and application.get("result_ref") == claim.result_ref
            and fee.get("unit_id") == claim.unit_id
            and fee.get("application_ref") == application.get("application_ref")
            and fee.get("generation_result_ref") == claim.result_ref
            and fee.get("resolution")
            in {"charged", "no_charge", "waived_insufficient", "waived_charge_failed"}
        )

    if claimed_phase == "delivery_pending" and work.get("phase") in {
        "stopped_insufficient",
        "stopped_delivery_failed",
        "stopped_late_generation",
    }:
        return bool(task.get("status") == "failed" and work.get("terminal_reason"))

    if claimed_phase == "finalize_pending" and work.get("phase") == "done":
        application = graph.get("application")
        fee = graph.get("fee")
        unit = graph.get("unit")
        return bool(
            task.get("status") == "completed"
            and task.get("result")
            and task.get("progress") == 100
            and unit is not None
            and task.get("credit_cost") == unit.get("debited_amount")
            and task.get("billing_status") == unit.get("billing_status")
            and application
            and fee
            and work.get("application_ref") == application.get("application_ref")
            and work.get("fee_decision_ref") == claim.unit_id
            and fee.get("application_ref") == application.get("application_ref")
            and fee.get("generation_result_ref") == claim.result_ref
        )
    return False


def _aware_datetime(value: object) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _aware_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


def _as_utc(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("database timestamp is invalid")
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _db_time(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _same_instant(left: object, right: object) -> bool:
    try:
        return _as_utc(left) == _as_utc(right)
    except ValueError:
        return False


__all__ = ("SqlAlchemyFrameReviewContinuationClaims",)
