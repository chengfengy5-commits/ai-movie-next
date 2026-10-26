"""Bounded discovery of original image.batch intents that reached deadline."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Event, Lock
from typing import Literal

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.types import ApplicationOutcome, OwnerTaskScope
from haoai_backend.generation_core.base_schema import ResultEvidenceRow
from haoai_backend.generation_core.schema import GenerationResultReceiptRow

from .base_schema import AITaskRow, BillingUnitRow

SessionFactory = Callable[[], Session]
DbClock = Callable[[], datetime]
_MAX_PAGE_SIZE = 32
_ACTIVE_TASK_STATUSES = ("queued", "processing", "reconciling")


@dataclass(frozen=True, slots=True)
class _Candidate:
    user_id: str
    task_id: str
    created_at: datetime
    result_ref: str | None = None


@dataclass(slots=True)
class SqlAlchemyImageBatchRecovery:
    """Find due batch items and delegate all terminal decisions to results."""

    session_factory: SessionFactory
    db_clock: DbClock
    results: object
    page_size: int = _MAX_PAGE_SIZE
    _cursor: tuple[datetime, str] | None = None
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        if (
            not callable(self.session_factory)
            or not callable(self.db_clock)
            or not callable(getattr(self.results, "settle_due", None))
            or type(self.page_size) is not int
            or self.page_size <= 0
            or self.page_size > _MAX_PAGE_SIZE
        ):
            raise ValueError("image.batch recovery dependencies must be explicit")

    def scan_due(
        self, *, stop_signal: Event | None = None
    ) -> tuple[Literal["settled", "retained", "pending"], ...]:
        if _is_stopping(stop_signal):
            return ()
        try:
            now = self.db_clock()
        except Exception:
            return ("pending",)
        if not _valid_utc(now):
            return ("pending",)

        with self._lock:
            try:
                candidates = self._next_page(now.astimezone(timezone.utc))
            except Exception:
                return ("pending",)
            outcomes: list[Literal["settled", "retained", "pending"]] = []
            for candidate in candidates:
                if _is_stopping(stop_signal):
                    break
                scope = OwnerTaskScope(
                    user_id=candidate.user_id,
                    task_id=candidate.task_id,
                    operation="image.batch",
                )
                if candidate.result_ref is None:
                    outcomes.append(
                        self.results.settle_due(scope, stop_signal=stop_signal)
                    )
                    continue
                apply_completed = getattr(self.results, "apply_completed", None)
                if not callable(apply_completed):
                    outcomes.append("pending")
                    continue
                applied = apply_completed(
                    scope, candidate.result_ref, stop_signal=stop_signal
                )
                outcomes.append(
                    "retained"
                    if isinstance(applied, ApplicationOutcome)
                    and applied.kind
                    in {"applied", "already_applied", "target_conflict", "target_deleted"}
                    else "pending"
                )
            return tuple(outcomes)

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
        receipt = GenerationResultReceiptRow.__table__
        evidence = ResultEvidenceRow.__table__
        due_unit = exists(
            select(unit.c.id).where(
                unit.c.task_id == task.c.id,
                unit.c.operation == "image.batch",
                unit.c.result_status.in_(("pending", "unknown")),
                unit.c.deadline_at.is_not(None),
                unit.c.deadline_at <= _database_datetime(now),
            )
        )
        pending_result_ref = (
            select(receipt.c.result_ref)
            .select_from(
                receipt.join(
                    evidence,
                    and_(
                        evidence.c.id == receipt.c.evidence_id,
                        evidence.c.step_id == receipt.c.step_id,
                        evidence.c.submission_id == receipt.c.submission_id,
                    ),
                )
            )
            .where(
                receipt.c.owner_id == task.c.user_id,
                receipt.c.task_id == task.c.id,
                receipt.c.operation == "image.batch",
                receipt.c.automatic_application_allowed.is_(True),
                receipt.c.late_disposition.is_(None),
                receipt.c.policy_stop_disposition.is_(None),
                evidence.c.validity == "valid",
                evidence.c.application_status == "pending",
            )
            .limit(1)
            .scalar_subquery()
        )
        successful_unit = exists(
            select(unit.c.id).where(
                unit.c.task_id == task.c.id,
                unit.c.operation == "image.batch",
                unit.c.result_status == "succeeded",
                unit.c.refunded_amount == 0,
            )
        )
        completed_application = and_(
            task.c.status == "completed",
            successful_unit,
            pending_result_ref.is_not(None),
        )
        conditions = [
            task.c.type == "batch-image",
            task.c.user_cancelled_at.is_(None),
            or_(
                and_(task.c.status.in_(_ACTIVE_TASK_STATUSES), due_unit),
                completed_application,
            ),
        ]
        if cursor is not None:
            created_at, task_id = cursor
            stored_created_at = _database_datetime(created_at)
            conditions.append(
                or_(
                    task.c.created_at > stored_created_at,
                    and_(
                        task.c.created_at == stored_created_at,
                        task.c.id > task_id,
                    ),
                )
            )
        with self.session_factory() as session:
            rows = session.execute(
                select(
                    task.c.user_id,
                    task.c.id,
                    task.c.created_at,
                    pending_result_ref.label("result_ref"),
                )
                .where(*conditions)
                .order_by(task.c.created_at, task.c.id)
                .limit(limit)
            ).all()
        return [
            _Candidate(
                row.user_id,
                row.id,
                _aware_utc(row.created_at),
                row.result_ref,
            )
            for row in rows
        ]


def _is_stopping(stop_signal: Event | None) -> bool:
    return stop_signal is not None and stop_signal.is_set()


def _valid_utc(value: object) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() == timezone.utc.utcoffset(value)
    )


def _aware_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _database_datetime(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)


__all__ = ("SqlAlchemyImageBatchRecovery",)
