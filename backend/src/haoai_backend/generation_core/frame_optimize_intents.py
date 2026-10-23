"""Single-use durable Provider intents for individual optimize graph Units."""

from __future__ import annotations

import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.types import ClaimAuthority, CommittedIntent
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
)
from haoai_backend.generation_core.frame_optimize_execution import (
    LockedFrameOptimizeBatch,
    intent_row_matches,
    intent_snapshot,
    lock_frame_optimize_batch,
    valid_intent_projection,
)
from haoai_backend.generation_core.frame_optimize_progress import lock_frame_optimize_progress
from haoai_backend.generation_credentials.domain import CredentialCrypto, HmacProofAuthority
from haoai_backend.generation_credentials.frame_optimize_snapshot_reader import (
    VerifiedFrameOptimizeExecutionBatch,
    VerifiedFrameOptimizeExecutionItem,
)

_OPERATION = "frame.optimize"
_TASK_TYPE = "batch-optimize"
_GRAPH_VERSION = "frame-optimize-v1"
_DEADLINE_SECONDS = 7200

SessionFactory = Callable[[], Session]
DbClock = Callable[[], datetime]
IdFactory = Callable[[], str]


@dataclass(frozen=True, slots=True)
class FrameOptimizeIntentPreparation:
    disposition: Literal["committed", "existing", "unresolved", "ownership_lost"]
    verified_batch: VerifiedFrameOptimizeExecutionBatch | None = None
    verified_item: VerifiedFrameOptimizeExecutionItem | None = None
    intent: CommittedIntent | None = None


@dataclass(frozen=True, slots=True)
class _IntentWrite:
    intent: CommittedIntent
    request_snapshot: dict[str, object]


@dataclass(slots=True)
class SqlAlchemyFrameOptimizeIntents:
    """Atomically persist one selected Unit's original intent after full-batch auth."""

    session_factory: SessionFactory
    db_clock: DbClock
    crypto: CredentialCrypto
    proof_authority: HmacProofAuthority
    id_factory: IdFactory = field(default=lambda: secrets.token_hex(18))

    def __post_init__(self) -> None:
        if not callable(self.session_factory) or not callable(self.db_clock):
            raise TypeError("intent session and database clock must be explicit")
        if not callable(self.id_factory):
            raise TypeError("intent id factory must be callable")

    def prepare(self, claim: ClaimAuthority, unit_id: str) -> FrameOptimizeIntentPreparation:
        if (
            not isinstance(claim, ClaimAuthority)
            or claim.scope.operation != _OPERATION
            or claim.local_lost_signal.is_set()
            or not isinstance(unit_id, str)
            or not unit_id
        ):
            return FrameOptimizeIntentPreparation("ownership_lost")
        staged: list[_IntentWrite] = []
        try:
            write, disposition = self._stage_and_commit(claim, unit_id, staged)
        except Exception:
            write = staged[0] if staged else None
            disposition = None
        if disposition is not None:
            return FrameOptimizeIntentPreparation(disposition)
        if write is None:
            return FrameOptimizeIntentPreparation("unresolved")
        return self._confirm_commit(claim, unit_id, write)

    def _stage_and_commit(
        self,
        claim: ClaimAuthority,
        unit_id: str,
        staged: list[_IntentWrite],
    ) -> tuple[_IntentWrite | None, Literal["existing", "unresolved", "ownership_lost"] | None]:
        with self.session_factory() as session:
            with session.begin():
                graph = lock_frame_optimize_batch(
                    session,
                    claim.scope.user_id,
                    claim.scope.task_id,
                    crypto=self.crypto,
                    proof_authority=self.proof_authority,
                )
                now = self._server_now()
                if graph is None or not valid_intent_projection(graph):
                    return None, "unresolved"
                progress = lock_frame_optimize_progress(session, graph)
                if progress is None:
                    return None, "unresolved"
                if progress.stopped_insufficient:
                    return None, "ownership_lost"

                unit = graph.units_by_id.get(unit_id)
                step = graph.steps_by_unit.get(unit_id)
                if unit is None or step is None or unit_id not in graph.items_by_unit:
                    return None, "unresolved"
                existing = graph.external_by_step.get(str(step["id"]))
                if existing is not None:
                    return None, "existing"
                if not _claim_is_current(graph.task, claim, now):
                    return None, "ownership_lost"
                if not _unit_is_unprepared(unit, step):
                    return None, "unresolved"
                item = graph.items_by_unit[unit_id]
                external_id = self.id_factory()
                if not isinstance(external_id, str) or not external_id or len(external_id) > 36:
                    return None, "unresolved"
                key = _submission_key(unit_id, str(step["id"]))
                snapshot_ref = _snapshot_ref(item.prepared_payload_id, graph, item.order)
                request_snapshot = intent_snapshot(
                    graph,
                    unit,
                    step,
                    snapshot_ref=snapshot_ref,
                )
                if request_snapshot is None:
                    return None, "unresolved"
                commit_now = self._server_now()
                if not _claim_is_current(graph.task, claim, commit_now):
                    return None, "ownership_lost"
                deadline = commit_now + timedelta(seconds=_DEADLINE_SECONDS)
                provider = request_snapshot["provider"]
                model_id = request_snapshot["model_id"]
                if not isinstance(provider, str) or not isinstance(model_id, str):
                    return None, "unresolved"
                intent = CommittedIntent(
                    step_id=str(step["id"]),
                    attempt_id=item.item_id,
                    external_submission_id=external_id,
                    provider=provider,
                    model_id=model_id,
                    request_digest=graph.binding.request_digest,
                    snapshot_ref=snapshot_ref,
                    committed_at=commit_now,
                    first_intent_at=commit_now,
                    deadline_at=deadline,
                )
                write = _IntentWrite(intent, request_snapshot)
                staged.append(write)
                session.execute(
                    insert(ExternalSubmissionRow.__table__).values(
                        id=external_id,
                        step_id=intent.step_id,
                        submission_key=key,
                        provider=provider,
                        model_ref=model_id,
                        request_digest=intent.request_digest,
                        request_snapshot=request_snapshot,
                        intent_at=_db_datetime(commit_now),
                        submitted_at=None,
                        external_task_id=None,
                        submission_status="submitting",
                        deadline_at=_db_datetime(deadline),
                        created_at=_db_datetime(commit_now),
                        updated_at=_db_datetime(commit_now),
                    )
                )
                step_result = session.execute(
                    update(ExecutionStepRow.__table__)
                    .where(
                        ExecutionStepRow.id == intent.step_id,
                        ExecutionStepRow.billing_unit_id == unit_id,
                        ExecutionStepRow.step_key == "generate",
                        ExecutionStepRow.execution_status == "prepared",
                        ExecutionStepRow.input_digest == intent.request_digest,
                    )
                    .values(execution_status="submitting", updated_at=_db_datetime(commit_now))
                )
                unit_result = session.execute(
                    update(BillingUnitRow.__table__)
                    .where(
                        BillingUnitRow.id == unit_id,
                        BillingUnitRow.task_id == claim.scope.task_id,
                        BillingUnitRow.operation == _OPERATION,
                        BillingUnitRow.step_graph_version == _GRAPH_VERSION,
                        BillingUnitRow.result_status == "pending",
                        BillingUnitRow.billing_status == "unbilled",
                        BillingUnitRow.debited_amount == 0,
                        BillingUnitRow.refunded_amount == 0,
                        BillingUnitRow.user_cancelled_at.is_(None),
                        BillingUnitRow.first_intent_at.is_(None),
                        BillingUnitRow.deadline_at.is_(None),
                        BillingUnitRow.input_digest == intent.request_digest,
                    )
                    .values(
                        first_intent_at=_db_datetime(commit_now),
                        deadline_at=_db_datetime(deadline),
                        updated_at=_db_datetime(commit_now),
                    )
                )
                if step_result.rowcount != 1 or unit_result.rowcount != 1:
                    raise RuntimeError("optimize intent graph changed during commit")
                return write, None

    def _confirm_commit(
        self,
        claim: ClaimAuthority,
        unit_id: str,
        write: _IntentWrite,
    ) -> FrameOptimizeIntentPreparation:
        try:
            with self.session_factory() as session:
                with session.begin():
                    graph = lock_frame_optimize_batch(
                        session,
                        claim.scope.user_id,
                        claim.scope.task_id,
                        crypto=self.crypto,
                        proof_authority=self.proof_authority,
                    )
                    now = self._server_now()
                    if graph is None or not valid_intent_projection(graph):
                        return FrameOptimizeIntentPreparation("unresolved")
                    progress = lock_frame_optimize_progress(session, graph)
                    if progress is None:
                        return FrameOptimizeIntentPreparation("unresolved")
                    unit = graph.units_by_id.get(unit_id)
                    step = graph.steps_by_unit.get(unit_id)
                    item = graph.items_by_unit.get(unit_id)
                    external = graph.external_by_step.get(write.intent.step_id)
                    if (
                        unit is None
                        or step is None
                        or item is None
                        or external is None
                        or external.get("id") != write.intent.external_submission_id
                        or external.get("request_snapshot") != write.request_snapshot
                        or not intent_row_matches(external, graph, unit, step)
                        or not _same_time(unit.get("first_intent_at"), write.intent.first_intent_at)
                        or not _same_time(unit.get("deadline_at"), write.intent.deadline_at)
                        or not _same_time(external.get("intent_at"), write.intent.committed_at)
                        or not _same_time(external.get("deadline_at"), write.intent.deadline_at)
                    ):
                        return FrameOptimizeIntentPreparation("unresolved")
                    if not _claim_is_current(graph.task, claim, now):
                        return FrameOptimizeIntentPreparation("existing")
                    if progress.stopped_insufficient:
                        return FrameOptimizeIntentPreparation("existing")
                    if not _first_send_is_current(
                        task=graph.task,
                        unit=unit,
                        step=step,
                        external=external,
                        now=now,
                    ):
                        return FrameOptimizeIntentPreparation("existing")
                    return FrameOptimizeIntentPreparation(
                        "committed",
                        verified_batch=graph.verified,
                        verified_item=item,
                        intent=write.intent,
                    )
        except Exception:
            return FrameOptimizeIntentPreparation("unresolved")

    def _server_now(self) -> datetime:
        value = self.db_clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("database clock must return an aware datetime")
        return _aware_utc(value)


def _claim_is_current(task, claim: ClaimAuthority, now: datetime) -> bool:
    lease = task.get("lease_until")
    return bool(
        task.get("id") == claim.scope.task_id
        and task.get("user_id") == claim.scope.user_id
        and task.get("type") == _TASK_TYPE
        and task.get("status") == "processing"
        and task.get("claimed_by") == claim.worker_instance
        and task.get("claim_token") == claim.token
        and task.get("execution_generation") == claim.execution_generation
        and task.get("user_cancelled_at") is None
        and isinstance(lease, datetime)
        and _aware_utc(lease) > now
        and not claim.local_lost_signal.is_set()
    )


def _unit_is_unprepared(unit, step) -> bool:
    return all(
        (
            unit.get("operation") == _OPERATION,
            unit.get("step_graph_version") == _GRAPH_VERSION,
            unit.get("result_status") == "pending",
            unit.get("billing_status") == "unbilled",
            unit.get("debited_amount") == 0,
            unit.get("refunded_amount") == 0,
            unit.get("user_cancelled_at") is None,
            unit.get("first_intent_at") is None,
            unit.get("deadline_at") is None,
            step.get("step_key") == "generate",
            step.get("execution_status") == "prepared",
        )
    )


def _first_send_is_current(*, task, unit, step, external, now: datetime) -> bool:
    deadline = unit.get("deadline_at")
    return bool(
        unit.get("billing_status") == "unbilled"
        and unit.get("result_status") == "pending"
        and unit.get("debited_amount") == 0
        and unit.get("refunded_amount") == 0
        and unit.get("user_cancelled_at") is None
        and isinstance(deadline, datetime)
        and _aware_utc(deadline) > now
        and step.get("step_key") == "generate"
        and step.get("execution_status") == "submitting"
        and external.get("submission_status") == "submitting"
        and external.get("submitted_at") is None
        and external.get("external_task_id") is None
    )


def _snapshot_ref(
    payload_id: str,
    graph: LockedFrameOptimizeBatch,
    item_order: int,
) -> str:
    return f"frame-optimize-v1:{payload_id}:{graph.binding.manifest_snapshot_digest}:{item_order}"


def _submission_key(unit_id: str, step_id: str) -> str:
    value = f"frame-optimize-generate-v1:{unit_id}:{step_id}"
    if len(value) > 255:
        raise ValueError("optimize intent key exceeds the stable submission limit")
    return value


def _aware_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _db_datetime(value: datetime) -> datetime:
    return _aware_utc(value).replace(tzinfo=None)


def _same_time(left: object, right: object) -> bool:
    return (
        isinstance(left, datetime)
        and isinstance(right, datetime)
        and _aware_utc(left) == _aware_utc(right)
    )


__all__ = ["FrameOptimizeIntentPreparation", "SqlAlchemyFrameOptimizeIntents"]
