"""Bounded discovery for original image.single intents whose deadline elapsed."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Event
from threading import Lock
from typing import Literal

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.types import OwnerTaskScope

from .base_schema import AITaskRow, BillingUnitRow
from .image_single_results import (
    SqlAlchemyImageSingleResults,
    _UNRESOLVED_RESULT,
)

SessionFactory = Callable[[], Session]
DbClock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class _Candidate:
    user_id: str
    task_id: str
    created_at: datetime


@dataclass(slots=True)
class SqlAlchemyImageSingleRecovery:
    session_factory: SessionFactory
    db_clock: DbClock
    results: SqlAlchemyImageSingleResults
    page_size: int = 32
    _cursor: tuple[datetime, str] | None = None
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        if (
            not callable(self.session_factory)
            or not callable(self.db_clock)
            or not callable(getattr(self.results, "settle_due", None))
            or type(self.page_size) is not int
            or self.page_size <= 0
            or self.page_size > 32
        ):
            raise ValueError("image.single recovery dependencies must be explicit")

    def scan_due(self, *, stop_signal: Event | None = None) -> tuple[Literal["settled", "retained", "pending"], ...]:
        if _is_stopping(stop_signal):
            return ()
        try:
            now = self.db_clock()
        except Exception:
            return ("pending",)
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() != timezone.utc.utcoffset(now)
        ):
            return ("pending",)

        with self._lock:
            try:
                candidates = self._next_page(now.astimezone(timezone.utc))
            except Exception:
                return ("pending",)
            results: list[Literal["settled", "retained", "pending"]] = []
            for candidate in candidates:
                if _is_stopping(stop_signal):
                    break
                scope = OwnerTaskScope(
                    user_id=candidate.user_id,
                    task_id=candidate.task_id,
                    operation="image.single",
                )
                results.append(self.results.settle_due(scope, stop_signal=stop_signal))
            return tuple(results)

    def _next_page(self, now: datetime) -> list[_Candidate]:
        page = self._read_candidates(now, self._cursor, self.page_size)
        selected = list(page)
        if len(selected) < self.page_size and self._cursor is not None:
            seen = {candidate.task_id for candidate in selected}
            wrapped = self._read_candidates(now, None, self.page_size)
            selected.extend(
                candidate
                for candidate in wrapped
                if candidate.task_id not in seen
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
                unit.c.operation == "image.single",
                unit.c.result_status.in_(("pending", "unknown")),
                unit.c.deadline_at.is_not(None),
                unit.c.deadline_at <= _database_datetime(now),
            )
        )
        conditions = [
            task.c.type == "image-single",
            task.c.status.in_(("processing", "reconciling", "queued")),
            task.c.user_cancelled_at.is_(None),
            or_(
                task.c.result.is_(None),
                task.c.result == _UNRESOLVED_RESULT,
            ),
            due_unit,
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


def _is_stopping(stop_signal: Event | None) -> bool:
    return stop_signal is not None and stop_signal.is_set()


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _database_datetime(value: datetime) -> datetime:
    return _aware_utc(value).replace(tzinfo=None)


__all__ = ("SqlAlchemyImageSingleRecovery",)
