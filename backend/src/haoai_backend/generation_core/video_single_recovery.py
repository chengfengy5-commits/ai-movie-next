"""Bounded deadline settlement for unresolved original video.single intents."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Event, Lock
from typing import Literal

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.types import OwnerTaskScope
from haoai_backend.shared.identity import TrustedActor

from .base_schema import AITaskRow, BillingUnitRow
from .haoai_group.candidate_scope import has_haoai_group_candidate_marker
from .video_single_results import SqlAlchemyVideoSingleResults, _UNRESOLVED_RESULT

SessionFactory = Callable[[], Session]
DbClock = Callable[[], datetime]
_MAX_PAGE_SIZE = 32


@dataclass(frozen=True, slots=True)
class _Candidate:
    user_id: str
    task_id: str
    created_at: datetime


@dataclass(slots=True)
class SqlAlchemyVideoSingleRecovery:
    session_factory: SessionFactory
    db_clock: DbClock
    results: SqlAlchemyVideoSingleResults
    group_cancellation: object | None = None
    group_recovery: object | None = None
    page_size: int = _MAX_PAGE_SIZE
    _cursor: tuple[datetime, str] | None = None
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        if (
            not callable(self.session_factory)
            or not callable(self.db_clock)
            or not callable(getattr(self.results, "settle_due", None))
            or (
                self.group_cancellation is not None
                and not callable(getattr(self.group_cancellation, "cancel_owned", None))
            )
            or (
                self.group_recovery is not None
                and not callable(getattr(self.group_recovery, "scan_due", None))
            )
            or type(self.page_size) is not int
            or not 1 <= self.page_size <= _MAX_PAGE_SIZE
        ):
            raise ValueError("video.single recovery dependencies must be explicit")

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
                candidates = []
                outcomes: list[Literal["settled", "retained", "pending"]] = [
                    "pending"
                ]
            else:
                outcomes = []
            for candidate in candidates:
                if _is_stopping(stop_signal):
                    break
                outcomes.append(
                    self.results.settle_due(
                        OwnerTaskScope(
                            user_id=candidate.user_id,
                            task_id=candidate.task_id,
                            operation="video.single",
                        ),
                        stop_signal=stop_signal,
                    )
                )
        if self.group_recovery is not None and not _is_stopping(stop_signal):
            try:
                group_outcomes = self.group_recovery.scan_due(
                    stop_signal=stop_signal
                )
                if not isinstance(group_outcomes, tuple):
                    group_outcomes = ("pending",)
            except Exception:
                group_outcomes = ("pending",)
            outcomes.extend(
                value
                if value in {"settled", "retained", "pending"}
                else "pending"
                for value in group_outcomes
            )
        return tuple(outcomes)

    def consume_cancel_and_due(
        self,
        scope: OwnerTaskScope,
        *,
        stop_signal: Event | None = None,
    ) -> Literal[
        "settled", "retained", "pending", "terminal_projection", "not_selected"
    ]:
        """Route a video cancellation through Group proof before ordinary due."""

        if (
            type(scope) is not OwnerTaskScope
            or scope.operation != "video.single"
            or _is_stopping(stop_signal)
        ):
            return "pending"
        if self.group_recovery is not None:
            try:
                outcome = self.group_recovery.consume_cancel_and_due(
                    scope, stop_signal=stop_signal
                )
            except Exception:
                return "pending"
            if outcome != "not_selected":
                if outcome in {"settled", "retained", "pending", "terminal_projection"}:
                    return outcome
                return "pending"
        elif self.group_cancellation is not None:
            try:
                outcome = self.group_cancellation.cancel_owned(
                    TrustedActor(user_id=scope.user_id), scope.task_id
                )
            except Exception:
                return "pending"
            if outcome != "not_selected":
                if outcome in {"settled", "retained", "pending", "terminal_projection"}:
                    return outcome
                return "pending"
        if _is_stopping(stop_signal):
            return "pending"
        return self.results.settle_due(scope, stop_signal=stop_signal)

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
        due_unit = exists(
            select(unit.c.id).where(
                unit.c.task_id == task.c.id,
                unit.c.operation == "video.single",
                unit.c.result_status.in_(("pending", "unknown")),
                unit.c.deadline_at.is_not(None),
                unit.c.deadline_at <= _database_datetime(now),
            )
        )
        conditions = [
            task.c.type == "video-single",
            task.c.status.in_(("processing", "reconciling", "queued")),
            task.c.user_cancelled_at.is_(None),
            or_(task.c.result.is_(None), task.c.result == _UNRESOLVED_RESULT),
            due_unit,
            ~has_haoai_group_candidate_marker(task),
        ]
        if cursor is not None:
            created_at, task_id = cursor
            conditions.append(
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
                .where(*conditions)
                .order_by(task.c.created_at, task.c.id)
                .limit(limit)
            ).all()
        return [
            _Candidate(
                user_id=str(row.user_id),
                task_id=str(row.id),
                created_at=_aware_utc(row.created_at),
            )
            for row in rows
        ]


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
        # SQLite may return UTC database timestamps without timezone metadata.
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _database_datetime(value: datetime) -> datetime:
    return _aware_utc(value).replace(tzinfo=None)


__all__ = ("SqlAlchemyVideoSingleRecovery",)
