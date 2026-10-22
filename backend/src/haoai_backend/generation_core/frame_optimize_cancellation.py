"""Settle owner cancellation for a registered frame.optimize batch."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from threading import Event
from typing import Literal

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.records import LocalContinuationWork, UnitFeeDecision
from haoai_backend.generation_contracts.types import ControlSettlementAuthority, OwnerTaskScope
from haoai_backend.generation_credentials.domain import CredentialCrypto, HmacProofAuthority
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    UserRow,
)
from haoai_backend.generation_core.frame_optimize_execution import (
    LockedFrameOptimizeBatch,
    lock_frame_optimize_batch,
    valid_intent_projection,
)
from haoai_backend.generation_core.frame_optimize_progress import (
    FrameOptimizeProgress,
    lock_frame_optimize_progress,
)
from haoai_backend.generation_core.persistence import GenerationRecordRepository
from haoai_backend.generation_core.schema import (
    ExecutionAttemptGroupRow,
    PolicyStopEventRow,
)
from haoai_backend.shared.identity import TrustedActor

SessionFactory = Callable[[], Session]
DbClock = Callable[[], datetime]
IdFactory = Callable[[], str]
CancelOutcome = Literal["settled", "retained", "not_selected", "pending"]

_OPERATION = "frame.optimize"
_TASK_TYPE = "batch-optimize"
_MARKER_PREFIX = "frame_optimize_cancel:"
_MARKER_PATTERN = re.compile(r"^frame_optimize_cancel:[A-Za-z0-9_-]{1,40}$")
_CANCELLED_ERROR = "用户已停止批量优化"


@dataclass(frozen=True, slots=True)
class _CancellationWrite:
    owner_id: str
    task_id: str
    marker: str
    cancelled_at: datetime
    result: str
    credit_cost: int
    billing_status: str
    execution_generation: int


@dataclass(slots=True)
class SqlAlchemyFrameOptimizeCancellation:
    """Authenticate and atomically stop every Unit in an optimize batch."""

    session_factory: SessionFactory
    db_clock: DbClock
    id_factory: IdFactory
    crypto: CredentialCrypto
    proof_authority: HmacProofAuthority

    def __post_init__(self) -> None:
        if (
            not callable(self.session_factory)
            or not callable(self.db_clock)
            or not callable(self.id_factory)
            or type(self.crypto) is not CredentialCrypto
            or type(self.proof_authority) is not HmacProofAuthority
        ):
            raise ValueError("frame.optimize cancellation dependencies must be explicit")

    def cancel_owned(
        self,
        actor: TrustedActor,
        task_id: str,
        *,
        stop_signal: Event | None = None,
    ) -> CancelOutcome:
        if type(actor) is not TrustedActor or not _valid_identity(actor.user_id, task_id):
            return "not_selected"
        marker = self._new_marker()
        if marker is None:
            return "pending"
        return self._run(actor.user_id, task_id, marker, mode="owner", stop_signal=stop_signal)

    def consume_persisted(
        self,
        scope: OwnerTaskScope,
        *,
        stop_signal: Event | None = None,
    ) -> CancelOutcome:
        if (
            type(scope) is not OwnerTaskScope
            or scope.operation != _OPERATION
            or not _valid_identity(scope.user_id, scope.task_id)
        ):
            return "pending"
        marker = self._new_marker()
        if marker is None:
            return "pending"
        return self._run(
            scope.user_id, scope.task_id, marker, mode="persisted", stop_signal=stop_signal
        )

    def _run(
        self,
        owner_id: str,
        task_id: str,
        marker: str,
        *,
        mode: Literal["owner", "persisted", "confirm"],
        stop_signal: Event | None,
    ) -> CancelOutcome:
        try:
            with self.session_factory() as session:
                with session.begin():
                    outcome = self._settle(
                        session,
                        owner_id,
                        task_id,
                        marker,
                        mode=mode,
                        stop_signal=stop_signal,
                    )
            return outcome
        except Exception:
            if mode != "confirm" and self._confirm_commit(owner_id, task_id, marker):
                return "settled"
            return "pending"

    def _confirm_commit(self, owner_id: str, task_id: str, marker: str) -> bool:
        try:
            with self.session_factory() as session:
                with session.begin():
                    return self._settle(
                        session,
                        owner_id,
                        task_id,
                        marker,
                        mode="confirm",
                        stop_signal=None,
                    ) == "retained"
        except Exception:
            return False

    def _settle(
        self,
        session: Session,
        owner_id: str,
        task_id: str,
        requested_marker: str,
        *,
        mode: Literal["owner", "persisted", "confirm"],
        stop_signal: Event | None,
    ) -> CancelOutcome:
        if _stopping(stop_signal):
            return "pending"
        user = session.execute(
            select(UserRow.id).where(UserRow.id == owner_id).with_for_update()
        ).scalar_one_or_none()
        if user is None:
            return "not_selected"
        task_row = session.execute(
            select(AITaskRow.__table__)
            .where(AITaskRow.id == task_id, AITaskRow.user_id == owner_id)
            .with_for_update()
        ).mappings().one_or_none()
        if task_row is None or task_row.get("type") != _TASK_TYPE:
            return "not_selected"
        task = dict(task_row)
        graph = lock_frame_optimize_batch(
            session,
            owner_id,
            task_id,
            crypto=self.crypto,
            proof_authority=self.proof_authority,
        )
        if graph is None:
            return "pending"

        # This graph version has no authenticated retry groups or policy events.
        # Lock every possible linked row before reading financial progress.
        unit_ids = tuple(sorted(graph.units_by_id))
        groups = session.execute(
            select(ExecutionAttemptGroupRow.__table__)
            .where(ExecutionAttemptGroupRow.__table__.c.unit_id.in_(unit_ids))
            .order_by(
                ExecutionAttemptGroupRow.__table__.c.unit_id,
                ExecutionAttemptGroupRow.__table__.c.group_key,
            )
            .with_for_update()
        ).mappings().all()
        events = session.execute(
            select(PolicyStopEventRow.__table__)
            .where(
                or_(
                    PolicyStopEventRow.__table__.c.task_id == task_id,
                    PolicyStopEventRow.__table__.c.unit_id.in_(unit_ids),
                )
            )
            .order_by(PolicyStopEventRow.__table__.c.event_id)
            .with_for_update()
        ).mappings().all()
        if _stopping(stop_signal) or groups or events:
            return "pending"

        progress = lock_frame_optimize_progress(session, graph)
        if (
            progress is None
            or not valid_intent_projection(graph)
            or not _task_scope_matches(task, owner_id, task_id)
        ):
            return "pending"

        existing_marker = task.get("cancellation_reason")
        existing_cancelled_at = task.get("user_cancelled_at")
        if existing_marker is not None or existing_cancelled_at is not None:
            if (
                not _valid_marker(existing_marker)
                or progress.cancellation_marker != existing_marker
                or not _cancelled_projection_matches(task, progress, existing_marker)
            ):
                return "pending"
            if mode == "confirm" and existing_marker != requested_marker:
                return "pending"
            return "retained"

        if mode == "confirm":
            return "pending"
        if mode == "persisted" and task.get("status") != "cancelling":
            return "not_selected"
        if task.get("status") in {"completed", "failed"}:
            return "retained"
        if task.get("status") not in {"queued", "processing", "reconciling", "cancelling"}:
            return "pending"
        if task.get("result") is not None and progress.partial_results is None:
            return "pending"

        now = self._now()
        if now is None or _stopping(stop_signal):
            return "pending"
        fee_records = self._stage_missing_fee_decisions(session, graph, progress, now)
        if fee_records is None:
            return "pending"
        if _stopping(stop_signal):
            raise _CancellationStopped

        billing_status = _billing_status(graph, progress, fee_records)
        if billing_status is None:
            raise _CancellationWriteConflict
        self._mark_units(
            session, graph, requested_marker, now, progress, fee_records, stop_signal
        )
        self._stop_unfinished_work(session, graph, progress, stop_signal)
        if _stopping(stop_signal):
            raise _CancellationStopped

        # The parent projection is deliberately last. Its CAS revokes the old
        # lease without minting a new generation or touching any result source.
        partial = [
            {"index": index, "optimized_text": text}
            for index, text in progress.partial_results
        ]
        result = json.dumps(
            {"partial": partial, "error": _CANCELLED_ERROR},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        authority = ControlSettlementAuthority(
            owner_scope=OwnerTaskScope(owner_id, task_id, _OPERATION),
            policy_stop_event_refs=(),
            revoked_execution_generation=task.get("execution_generation"),
            quiescent=True,
            purpose="cancel",
        )
        if not _authority_matches(authority, task, owner_id, task_id) or _stopping(stop_signal):
            raise _CancellationWriteConflict

        final_now = self._now()
        if final_now is None or _stopping(stop_signal):
            raise _CancellationWriteConflict
        task_table = AITaskRow.__table__
        conditions = [
            task_table.c.id == task_id,
            task_table.c.user_id == owner_id,
            task_table.c.type == _TASK_TYPE,
            task_table.c.status == task.get("status"),
            task_table.c.execution_generation == task.get("execution_generation"),
            task_table.c.claimed_by == task.get("claimed_by"),
            task_table.c.claim_token == task.get("claim_token"),
            _nullable_equals(task_table.c.lease_until, task.get("lease_until")),
            task_table.c.user_cancelled_at.is_(None),
            task_table.c.cancellation_reason.is_(None),
            task_table.c.billing_status == task.get("billing_status"),
            task_table.c.credit_cost == task.get("credit_cost"),
            task_table.c.recovery_status == task.get("recovery_status"),
            _nullable_equals(task_table.c.result, task.get("result")),
        ]
        updated = session.execute(
            update(task_table)
            .where(*conditions)
            .values(
                status="failed",
                claimed_by=None,
                claim_token=None,
                lease_until=None,
                user_cancelled_at=_db_time(now),
                cancellation_reason=requested_marker,
                progress=100,
                progress_message=_CANCELLED_ERROR,
                result=result,
                billing_status=billing_status,
                credit_cost=task.get("credit_cost"),
                updated_at=_db_time(final_now),
            )
        )
        if updated.rowcount != 1:
            raise _CancellationWriteConflict
        session.flush()

        write = _CancellationWrite(
            owner_id=owner_id,
            task_id=task_id,
            marker=requested_marker,
            cancelled_at=now,
            result=result,
            credit_cost=int(task.get("credit_cost", 0)),
            billing_status=billing_status,
            execution_generation=int(task.get("execution_generation", 0)),
        )
        fresh_graph = lock_frame_optimize_batch(
            session,
            owner_id,
            task_id,
            crypto=self.crypto,
            proof_authority=self.proof_authority,
        )
        fresh_progress = (
            lock_frame_optimize_progress(session, fresh_graph) if fresh_graph is not None else None
        )
        if fresh_progress is None or not _write_matches(write, fresh_graph, fresh_progress):
            raise _CancellationWriteConflict
        return "settled"

    def _stage_missing_fee_decisions(
        self,
        session: Session,
        graph: LockedFrameOptimizeBatch,
        progress: FrameOptimizeProgress,
        now: datetime,
    ) -> dict[str, UnitFeeDecision] | None:
        planned: dict[str, UnitFeeDecision] = {}
        for unit_id in sorted(graph.units_by_id):
            unit = graph.units_by_id[unit_id]
            fee = progress.fees_by_unit.get(unit_id)
            if fee is not None:
                continue
            amount = unit.get("quoted_amount")
            receipt = progress.receipts_by_unit.get(unit_id)
            if type(amount) is not int or amount < 0:
                return None
            if receipt is None and unit_id in progress.unstarted_insufficient_by_unit:
                continue
            if receipt is not None:
                work = progress.works_by_unit.get(unit_id)
                if (
                    work is None
                    or work.get("phase") != "fee_pending"
                    or work.get("phase_version") != 0
                ):
                    return None
            resolution = "no_charge" if amount == 0 else "waived_cancelled"
            planned[unit_id] = UnitFeeDecision(
                unit_id=unit_id,
                charge_phase="pre_apply",
                resolution=resolution,
                ledger_ref=None,
                generation_result_ref=(receipt.get("result_ref") if receipt else None),
                application_ref=None,
                resolved_at=now,
            )

        records = GenerationRecordRepository(session)
        for unit_id in sorted(planned):
            record = planned[unit_id]
            if records.stage_record(record).status != "created":
                raise _CancellationWriteConflict
        session.flush()
        return planned

    def _mark_units(
        self,
        session: Session,
        graph: LockedFrameOptimizeBatch,
        marker: str,
        now: datetime,
        progress: FrameOptimizeProgress,
        staged_fees: dict[str, UnitFeeDecision],
        stop_signal: Event | None,
    ) -> None:
        table = BillingUnitRow.__table__
        for unit_id in sorted(graph.units_by_id):
            if _stopping(stop_signal):
                raise _CancellationStopped
            unit = graph.units_by_id[unit_id]
            values: dict[str, object] = {
                "user_cancelled_at": _db_time(now),
                "cancellation_reason": marker,
                "updated_at": _db_time(now),
            }
            if (
                unit.get("quoted_amount") == 0
                and unit.get("billing_status") == "unbilled"
                and (
                    unit_id in staged_fees
                    or progress.fees_by_unit.get(unit_id, {}).get("resolution") == "no_charge"
                )
            ):
                values["billing_status"] = "no_charge"
            changed = session.execute(
                update(table)
                .where(
                    table.c.id == unit_id,
                    table.c.task_id == graph.task.get("id"),
                    table.c.billing_status == unit.get("billing_status"),
                    table.c.result_status == unit.get("result_status"),
                    table.c.debited_amount == unit.get("debited_amount"),
                    table.c.refunded_amount == unit.get("refunded_amount"),
                    _nullable_equals(table.c.first_intent_at, unit.get("first_intent_at")),
                    _nullable_equals(table.c.deadline_at, unit.get("deadline_at")),
                    table.c.success_confirmed_at.is_(None)
                    if unit.get("success_confirmed_at") is None
                    else table.c.success_confirmed_at == unit.get("success_confirmed_at"),
                    table.c.user_cancelled_at.is_(None),
                    table.c.cancellation_reason.is_(None),
                )
                .values(**values)
            )
            if changed.rowcount != 1:
                raise _CancellationWriteConflict

    def _stop_unfinished_work(
        self,
        session: Session,
        graph: LockedFrameOptimizeBatch,
        progress: FrameOptimizeProgress,
        stop_signal: Event | None,
    ) -> None:
        repository = GenerationRecordRepository(session)
        for unit_id in sorted(progress.works_by_unit):
            if _stopping(stop_signal):
                raise _CancellationStopped
            row = progress.works_by_unit[unit_id]
            phase = row.get("phase")
            if phase in {
                "done",
                "stopped_cancelled",
                "stopped_insufficient",
                "stopped_delivery_failed",
                "stopped_late_generation",
            }:
                continue
            if phase not in {"fee_pending", "delivery_pending", "finalize_pending"}:
                raise _CancellationWriteConflict
            work = _work_record(row)
            application = progress.applications_by_unit.get(unit_id)
            fee = progress.fees_by_unit.get(unit_id)
            fee_ref = unit_id if fee is not None or unit_id in graph.units_by_id else None
            updated = replace(
                work,
                phase="stopped_cancelled",
                phase_version=work.phase_version + 1,
                fee_decision_ref=fee_ref,
                application_ref=(
                    application.get("application_ref")
                    if application is not None
                    else work.application_ref
                ),
                terminal_reason="user_cancelled",
            )
            result = repository.advance_continuation_work(
                updated, expected_phase_version=work.phase_version
            )
            if result.status != "updated":
                raise _CancellationWriteConflict

    def _new_marker(self) -> str | None:
        try:
            value = self.id_factory()
        except Exception:
            return None
        marker = f"{_MARKER_PREFIX}{value}" if isinstance(value, str) else ""
        return marker if _valid_marker(marker) else None

    def _now(self) -> datetime | None:
        try:
            value = self.db_clock()
        except Exception:
            return None
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            return None
        return value.astimezone(timezone.utc)


def _billing_status(graph, progress, staged_fees) -> str | None:
    resolutions = {
        unit_id: fee.get("resolution")
        for unit_id, fee in progress.fees_by_unit.items()
    }
    resolutions.update({unit_id: record.resolution for unit_id, record in staged_fees.items()})
    if set(resolutions) != set(graph.units_by_id):
        return None
    total = graph.task.get("credit_cost")
    if type(total) is not int or total < 0:
        return None
    if total > 0:
        return "charged"
    if all(value == "no_charge" for value in resolutions.values()):
        return "no_charge"
    return "unbilled"


def _cancelled_projection_matches(task, progress, marker: str) -> bool:
    return bool(
        _valid_marker(marker)
        and progress.cancellation_marker == marker
        and task.get("status") == "failed"
        and task.get("progress") == 100
        and task.get("progress_message") == _CANCELLED_ERROR
        and task.get("claim_token") is None
        and task.get("claimed_by") is None
        and task.get("lease_until") is None
        and progress.terminal_error == _CANCELLED_ERROR
        and _all_slots_closed(progress)
        and task.get("result") == _cancel_result(progress.partial_results)
    )


def _write_matches(write, graph, progress) -> bool:
    if graph is None or progress is None:
        return False
    task = graph.task
    return bool(
        task.get("user_id") == write.owner_id
        and task.get("id") == write.task_id
        and task.get("type") == _TASK_TYPE
        and task.get("status") == "failed"
        and task.get("progress") == 100
        and task.get("progress_message") == _CANCELLED_ERROR
        and task.get("result") == write.result
        and task.get("billing_status") == write.billing_status
        and task.get("credit_cost") == write.credit_cost
        and task.get("execution_generation") == write.execution_generation
        and task.get("user_cancelled_at") is not None
        and _same_time(task.get("user_cancelled_at"), write.cancelled_at)
        and task.get("cancellation_reason") == write.marker
        and task.get("claim_token") is None
        and task.get("claimed_by") is None
        and task.get("lease_until") is None
        and _cancelled_projection_matches(task, progress, write.marker)
    )


def _cancel_result(partial_results) -> str:
    return json.dumps(
        {
            "partial": [
                {"index": index, "optimized_text": text}
                for index, text in partial_results
            ],
            "error": _CANCELLED_ERROR,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _all_slots_closed(progress) -> bool:
    closed = {
        unit_id
        for unit_id, work in progress.works_by_unit.items()
        if work.get("phase")
        in {
            "done",
            "stopped_cancelled",
            "stopped_insufficient",
            "stopped_delivery_failed",
            "stopped_late_generation",
        }
    }
    closed.update(progress.unstarted_insufficient_by_unit)
    closed.update(progress.unstarted_cancelled_by_unit)
    # The caller compares this set against the authenticated batch separately.
    expected_count = (
        len(progress.works_by_unit)
        + len(progress.unstarted_insufficient_by_unit)
        + len(progress.unstarted_cancelled_by_unit)
    )
    return len(closed) == expected_count


def _authority_matches(authority, task, owner_id, task_id) -> bool:
    return bool(
        type(authority) is ControlSettlementAuthority
        and authority.owner_scope == OwnerTaskScope(owner_id, task_id, _OPERATION)
        and authority.policy_stop_event_refs == ()
        and authority.revoked_execution_generation == task.get("execution_generation")
        and authority.quiescent is True
        and authority.purpose == "cancel"
    )


def _task_scope_matches(task, owner_id, task_id) -> bool:
    return bool(
        task.get("user_id") == owner_id
        and task.get("id") == task_id
        and task.get("type") == _TASK_TYPE
        and task.get("request_data") is not None
    )


def _valid_identity(owner_id, task_id) -> bool:
    return bool(
        isinstance(owner_id, str)
        and owner_id
        and isinstance(task_id, str)
        and task_id
    )


def _valid_marker(value) -> bool:
    return isinstance(value, str) and _MARKER_PATTERN.fullmatch(value) is not None


def _stopping(signal) -> bool:
    try:
        return signal is not None and signal.is_set()
    except Exception:
        return True


def _work_record(row) -> LocalContinuationWork:
    return LocalContinuationWork(
        work_id=row["work_id"],
        owner_id=row["owner_id"],
        task_id=row["task_id"],
        unit_id=row["unit_id"],
        operation=row["operation"],
        policy_version=row["policy_version"],
        result_ref=row["result_ref"],
        content_digest=row["content_digest"],
        generation_fulfilled_at=_utc(row["generation_fulfilled_at"]),
        original_first_intent_at=_utc(row["original_first_intent_at"]),
        original_deadline_at=_utc(row["original_deadline_at"]),
        phase_order=tuple(row["phase_order"]),
        phase=row["phase"],
        phase_version=row["phase_version"],
        fee_decision_ref=row.get("fee_decision_ref"),
        application_ref=row.get("application_ref"),
        terminal_reason=row.get("terminal_reason"),
        last_execution_generation=row.get("last_execution_generation"),
    )


def _nullable_equals(column, value):
    return column.is_(None) if value is None else column == value


def _utc(value):
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _same_time(left, right):
    return _utc(left) is not None and _utc(left) == _utc(right)


def _db_time(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None)


class _CancellationWriteConflict(RuntimeError):
    """Abort all staged cancellation state after any compare-and-set loss."""


class _CancellationStopped(RuntimeError):
    """Abort cancellation when shutdown asks the caller to stop."""


__all__ = ["CancelOutcome", "SqlAlchemyFrameOptimizeCancellation"]
