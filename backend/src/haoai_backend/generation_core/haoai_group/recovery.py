"""Bounded restart recovery for HaoAI Group events and fixed deadlines."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Event, Lock
from typing import Literal

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.types import OwnerTaskScope
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    ExecutionStepRow,
    ResultEvidenceRow,
)
from haoai_backend.generation_core.haoai_group.candidate_scope import (
    has_haoai_group_candidate_marker,
)
from haoai_backend.generation_core.haoai_group.cancellation import (
    SqlAlchemyHaoaiGroupCancellation,
)
from haoai_backend.generation_core.haoai_group.results import (
    SqlAlchemyHaoaiGroupResults,
)
from haoai_backend.generation_core.haoai_group.settlement import (
    SqlAlchemyHaoaiGroupEventSettler,
)
from haoai_backend.generation_core.schema import (
    ExecutionAttemptGroupRow,
    PolicyStopEventRow,
    PollReservationRow,
)
from haoai_backend.shared.identity import TrustedActor

SessionFactory = Callable[[], Session]
DbClock = Callable[[], datetime]
_MAX_PAGE_SIZE = 32


@dataclass(frozen=True, slots=True)
class _Candidate:
    owner_id: str
    task_id: str
    created_at: datetime


@dataclass(slots=True)
class SqlAlchemyHaoaiGroupRecovery:
    """Settle committed Group events and due tasks without normal provider calls."""

    session_factory: SessionFactory
    db_clock: DbClock
    cancellation: SqlAlchemyHaoaiGroupCancellation
    event_settler: SqlAlchemyHaoaiGroupEventSettler
    results: SqlAlchemyHaoaiGroupResults
    late_recoverer: Callable[[OwnerTaskScope], object] | None = None
    page_size: int = _MAX_PAGE_SIZE
    _cursor: tuple[datetime, str] | None = None
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        if (
            not callable(self.session_factory)
            or not callable(self.db_clock)
            or not callable(getattr(self.cancellation, "cancel_owned", None))
            or not callable(getattr(self.event_settler, "settle_owned", None))
            or not callable(getattr(self.results, "settle_due", None))
            or (
                self.late_recoverer is not None
                and not callable(self.late_recoverer)
            )
            or type(self.page_size) is not int
            or not 1 <= self.page_size <= _MAX_PAGE_SIZE
        ):
            raise ValueError("HaoAI Group recovery dependencies must be explicit")

    def scan_due(
        self,
        *,
        stop_signal: Event | None = None,
    ) -> tuple[Literal["settled", "retained", "pending"], ...]:
        if _is_stopping(stop_signal):
            return ()
        try:
            now = self.db_clock()
        except Exception:
            return ("pending",)
        if not _aware_datetime(now):
            return ("pending",)

        with self._lock:
            try:
                candidates = self._next_page(_aware_utc(now))
            except Exception:
                return ("pending",)
        outcomes: list[Literal["settled", "retained", "pending"]] = []
        late_scopes: list[OwnerTaskScope] = []
        for candidate in candidates:
            if _is_stopping(stop_signal):
                break
            scope = OwnerTaskScope(
                user_id=candidate.owner_id,
                task_id=candidate.task_id,
                operation="video.single",
            )
            outcome = self.consume_due(scope, stop_signal=stop_signal)
            outcomes.append(outcome)
            # A previous process may already have committed the terminal
            # settlement. In that case the idempotent settlement replay can
            # return pending even though the late receipt is still absent.
            # The controlled late reader independently verifies terminal
            # finance before doing any content or media work, so every scoped
            # candidate can be offered to it after the financial transaction
            # has ended.
            late_scopes.append(scope)
        if self.late_recoverer is not None:
            for scope in late_scopes:
                if _is_stopping(stop_signal):
                    break
                try:
                    self.late_recoverer(scope)
                except Exception:
                    # Late projection failure is retried from its original proof
                    # on a later scan; settled financial facts remain unchanged.
                    continue
        return tuple(outcomes)

    def consume_cancel_and_due(
        self,
        scope: OwnerTaskScope,
        *,
        stop_signal: Event | None = None,
    ) -> Literal[
        "settled", "retained", "pending", "terminal_projection", "not_selected"
    ]:
        """Use the same fresh-graph N6 transaction for an explicit P2 cancel."""

        if (
            type(scope) is not OwnerTaskScope
            or scope.operation != "video.single"
            or _is_stopping(stop_signal)
        ):
            return "pending"
        try:
            outcome = self.cancellation.cancel_owned(
                TrustedActor(user_id=scope.user_id), scope.task_id
            )
        except Exception:
            return "pending"
        if outcome in {
            "settled",
            "retained",
            "pending",
            "terminal_projection",
            "not_selected",
        }:
            return outcome
        return "pending"

    def consume_due(
        self,
        scope: OwnerTaskScope,
        *,
        stop_signal: Event | None = None,
    ) -> Literal["settled", "retained", "pending"]:
        """Read existing cancellation/event truth, then settle the original due."""

        if (
            type(scope) is not OwnerTaskScope
            or scope.operation != "video.single"
            or _is_stopping(stop_signal)
        ):
            return "pending"

        cancellation_state = self._has_cancel_marker(scope)
        if cancellation_state is None:
            return "pending"
        if cancellation_state:
            return _terminal_result(
                self.cancellation.cancel_owned(
                    TrustedActor(user_id=scope.user_id), scope.task_id
                )
            )

        settle_failure = getattr(self.results, "settle_known_failure", None)
        if callable(settle_failure):
            try:
                if settle_failure(scope) == "settled":
                    return "settled"
            except Exception:
                return "pending"

        try:
            event_outcome = self.event_settler.settle_owned(
                scope.user_id, scope.task_id
            )
        except Exception:
            return "pending"
        if event_outcome == "pending":
            return "pending"
        try:
            due_outcome = self.results.settle_due(scope, stop_signal=stop_signal)
        except Exception:
            return "pending"
        if due_outcome != "pending":
            return due_outcome
        return "settled" if event_outcome == "settled" else "pending"

    def _has_cancel_marker(self, scope: OwnerTaskScope) -> bool | None:
        task = AITaskRow.__table__
        unit = BillingUnitRow.__table__
        try:
            with self.session_factory() as session:
                row = session.execute(
                    select(
                        task.c.user_cancelled_at,
                        task.c.cancellation_reason,
                        unit.c.user_cancelled_at.label("unit_cancelled_at"),
                        unit.c.cancellation_reason.label("unit_cancellation_reason"),
                    )
                    .join(unit, unit.c.task_id == task.c.id)
                    .where(
                        task.c.id == scope.task_id,
                        task.c.user_id == scope.user_id,
                    )
                ).one_or_none()
            return (
                None
                if row is None
                else row.user_cancelled_at is not None
                or row.unit_cancelled_at is not None
                or row.cancellation_reason is not None
                or row.unit_cancellation_reason is not None
            )
        except Exception:
            return None

    def _next_page(self, now: datetime) -> list[_Candidate]:
        page = self._read_candidates(now, self._cursor, self.page_size)
        selected = list(page)
        if len(selected) < self.page_size and self._cursor is not None:
            seen = {candidate.task_id for candidate in selected}
            wrapped = self._read_candidates(now, None, self.page_size)
            selected.extend(
                candidate for candidate in wrapped if candidate.task_id not in seen
            )
            selected = selected[: self.page_size]
        if selected:
            last = selected[-1]
            self._cursor = (last.created_at, last.task_id)
        else:
            self._cursor = None
        return selected

    def _read_candidates(
        self,
        now: datetime,
        cursor: tuple[datetime, str] | None,
        limit: int,
    ) -> list[_Candidate]:
        task = AITaskRow.__table__
        unit = BillingUnitRow.__table__
        event = PolicyStopEventRow.__table__
        due = exists(
            select(unit.c.id).where(
                unit.c.task_id == task.c.id,
                unit.c.result_status.in_(("pending", "unknown")),
                unit.c.deadline_at.is_not(None),
                unit.c.deadline_at <= _database_datetime(now),
            )
        )
        unit_cancel = exists(
            select(unit.c.id).where(
                unit.c.task_id == task.c.id,
                or_(
                    unit.c.user_cancelled_at.is_not(None),
                    unit.c.cancellation_reason.is_not(None),
                ),
            )
        )
        pending_event = exists(
            select(event.c.event_id)
            .select_from(event.join(unit, event.c.unit_id == unit.c.id))
            .where(
                unit.c.task_id == task.c.id,
                event.c.financial_disposition == "pending",
            )
        )
        group = ExecutionAttemptGroupRow.__table__
        step = ExecutionStepRow.__table__
        poll = PollReservationRow.__table__
        evidence = ResultEvidenceRow.__table__
        accepted_failed_observation = exists(
            select(poll.c.reservation_id)
            .select_from(
                poll.join(
                    evidence,
                    and_(
                        evidence.c.id == poll.c.observation_ref,
                        evidence.c.step_id == poll.c.step_id,
                        evidence.c.submission_id == poll.c.submission_id,
                    ),
                )
                .join(step, step.c.id == poll.c.step_id)
                .join(unit, unit.c.id == poll.c.unit_id)
                .join(
                    group,
                    and_(
                        group.c.unit_id == unit.c.id,
                        group.c.group_key == poll.c.group_key,
                    ),
                )
            )
            .where(
                unit.c.task_id == task.c.id,
                group.c.status == "active",
                group.c.active_attempt == step.c.step_order,
                group.c.selected_evidence_id.is_(None),
                group.c.sticky_policy_stop_event_ref.is_(None),
                group.c.outstanding_reservation_ref.is_(None),
                step.c.execution_status == "result_observed",
                poll.c.state == "accepted",
                evidence.c.source == "worker",
                evidence.c.evidence_kind == "haoai_poll_observation_v1",
                evidence.c.result_location.like('%"source_fact":"source_failed"%'),
            )
        )
        any_unit = exists(
            select(unit.c.id).where(unit.c.task_id == task.c.id)
        )
        missing_graph = and_(
            task.c.status.in_(("queued", "processing", "reconciling")),
            ~any_unit,
        )
        candidates = [
            has_haoai_group_candidate_marker(task),
            or_(
                task.c.user_cancelled_at.is_not(None),
                task.c.cancellation_reason.is_not(None),
                unit_cancel,
                due,
                pending_event,
                accepted_failed_observation,
                missing_graph,
            ),
        ]
        if cursor is not None:
            created_at, task_id = cursor
            candidates.append(
                or_(
                    task.c.created_at > _database_datetime(created_at),
                    and_(
                        task.c.created_at == _database_datetime(created_at),
                        task.c.id > task_id,
                    ),
                )
            )
        with self.session_factory() as session:
            rows = session.execute(
                select(task.c.user_id, task.c.id, task.c.created_at)
                .where(*candidates)
                .distinct()
                .order_by(task.c.created_at, task.c.id)
                .limit(limit)
            ).all()
        return [
            _Candidate(
                owner_id=str(row.user_id),
                task_id=str(row.id),
                created_at=_aware_utc(row.created_at),
            )
            for row in rows
        ]


def _terminal_result(value: str) -> Literal["settled", "retained", "pending"]:
    if value in {"settled", "retained"}:
        return value
    return "pending"


def _is_stopping(signal: Event | None) -> bool:
    return signal is not None and signal.is_set()


def _aware_datetime(value: object) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _database_datetime(value: datetime) -> datetime:
    return _aware_utc(value).replace(tzinfo=None)


__all__ = ("SqlAlchemyHaoaiGroupRecovery",)
