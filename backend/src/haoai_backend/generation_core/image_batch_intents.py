"""Durable intent admission for one item in the original image.batch graph."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.types import ClaimAuthority, CommittedIntent
from haoai_backend.generation_credentials.application import CredentialRegistry
from haoai_backend.generation_credentials.batch_execution_snapshot import (
    PersistedImageBatchExecutionBinding,
    VerifiedImageBatchExecutionInput,
    read_original_image_batch_execution_input,
)
from haoai_backend.generation_credentials.domain import CredentialCrypto, HmacProofAuthority

from .base_schema import (
    AITaskRow,
    BillingUnitRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
    TaskSubmissionRow,
    UserRow,
)

_TASK_TYPE = "batch-image"
_OPERATION = "image.batch"
_GRAPH_VERSION = "image-batch-v1"
_ORIGINAL_DEADLINE_SECONDS = 7200
_MAX_BATCH_ITEMS = 64
_CLAIMABLE_STATUS = frozenset({"processing"})
_DIGEST_LENGTH = 64

SessionFactory = Callable[[], Session]
DbClock = Callable[[], datetime]
IdFactory = Callable[[], str]
ImageBatchSnapshotReader = Callable[..., VerifiedImageBatchExecutionInput | None]


@dataclass(frozen=True, slots=True)
class ImageBatchIntentPreparation:
    disposition: Literal["committed", "existing", "unresolved", "ownership_lost"]
    verified_input: VerifiedImageBatchExecutionInput | None = None
    intent: CommittedIntent | None = None


@dataclass(frozen=True, slots=True)
class _LockedBatchGraph:
    task: dict[str, object]
    unit: dict[str, object]
    step: dict[str, object]
    parent_submission: dict[str, object]
    ordered_task_ids: tuple[str, ...]
    tasks_by_id: dict[str, dict[str, object]]
    units_by_task: dict[str, dict[str, object]]
    steps_by_task: dict[str, dict[str, object]]
    request_data_by_task: dict[str, dict[str, object]]
    existing_submissions: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class _StagedIntent:
    binding: PersistedImageBatchExecutionBinding
    verified_input: VerifiedImageBatchExecutionInput
    intent: CommittedIntent
    submission_key: str
    request_snapshot: dict[str, object]
    graph_digest: str


@dataclass(slots=True)
class SqlAlchemyImageBatchIntents:
    """Commit one child intent and return it only after a separate read-back."""

    session_factory: SessionFactory
    db_clock: DbClock
    id_factory: IdFactory
    registry: CredentialRegistry
    crypto: CredentialCrypto
    proof_authority: HmacProofAuthority
    snapshot_reader: ImageBatchSnapshotReader = read_original_image_batch_execution_input

    def prepare(self, claim: ClaimAuthority) -> ImageBatchIntentPreparation:
        if not isinstance(claim, ClaimAuthority):
            return ImageBatchIntentPreparation("ownership_lost")
        if claim.scope.operation != _OPERATION or claim.local_lost_signal.is_set():
            return ImageBatchIntentPreparation("ownership_lost")
        staged = self._stage_and_commit(claim)
        if staged is None:
            return ImageBatchIntentPreparation("unresolved")
        confirmed = self._confirm_intent(claim, staged)
        if confirmed is None:
            return ImageBatchIntentPreparation("unresolved")
        if confirmed == "existing":
            return ImageBatchIntentPreparation("existing")
        return ImageBatchIntentPreparation(
            "committed", staged.verified_input, confirmed
        )

    def _stage_and_commit(self, claim: ClaimAuthority) -> _StagedIntent | None:
        staged: _StagedIntent | None = None
        try:
            with self.session_factory() as session:
                with session.begin():
                    graph = self._lock_graph(session, claim)
                    if graph is None:
                        return None
                    now = self._server_now()
                    if not _claim_is_current(graph.task, claim, now):
                        return None
                    if graph.existing_submissions:
                        self._mark_reconciling(session, graph, claim)
                        return None
                    if not _unit_can_create_intent(graph.unit):
                        return None
                    if (
                        graph.step.get("execution_status") != "prepared"
                        or graph.unit.get("first_intent_at") is not None
                        or graph.unit.get("deadline_at") is not None
                    ):
                        self._mark_reconciling(session, graph, claim)
                        return None

                    binding = _execution_binding(graph)
                    if binding is None:
                        self._mark_reconciling(session, graph, claim)
                        return None
                    if self.snapshot_reader is read_original_image_batch_execution_input:
                        verified = self.snapshot_reader(
                            session,
                            binding,
                            registry=self.registry,
                            crypto=self.crypto,
                            proof_authority=self.proof_authority,
                        )
                    else:
                        verified = self.snapshot_reader(session, binding)
                    if not _verified_input_matches(verified, binding, graph):
                        self._mark_reconciling(session, graph, claim)
                        return None
                    graph_digest = _original_graph_digest(graph)
                    if graph_digest is None:
                        self._mark_reconciling(session, graph, claim)
                        return None

                    now = self._server_now()
                    if not _claim_is_current(graph.task, claim, now):
                        return None
                    task_table = AITaskRow.__table__
                    task_fence = session.execute(
                        update(task_table)
                        .where(
                            task_table.c.id == claim.scope.task_id,
                            task_table.c.user_id == claim.scope.user_id,
                            task_table.c.type == _TASK_TYPE,
                            task_table.c.status == graph.task["status"],
                            task_table.c.status.in_(_CLAIMABLE_STATUS),
                            task_table.c.claimed_by == claim.worker_instance,
                            task_table.c.claim_token == claim.token,
                            task_table.c.execution_generation
                            == claim.execution_generation,
                            task_table.c.user_cancelled_at.is_(None),
                            task_table.c.lease_until
                            == _database_datetime(_aware_utc(graph.task["lease_until"])),
                            task_table.c.lease_until > _database_datetime(now),
                            task_table.c.request_data == graph.task["request_data"],
                        )
                        .values(updated_at=task_table.c.updated_at)
                    )
                    if task_fence.rowcount != 1:
                        return None

                    submission_key = _intent_submission_key(
                        binding.unit_id, binding.step_id
                    )
                    submission_id = self.id_factory()
                    if not isinstance(submission_id, str) or not submission_id:
                        self._mark_reconciling(session, graph, claim)
                        return None
                    snapshot_ref = _execution_snapshot_ref(binding, graph, verified)
                    deadline = now + timedelta(seconds=_ORIGINAL_DEADLINE_SECONDS)
                    expected = CommittedIntent(
                        step_id=binding.step_id,
                        attempt_id=verified.server_attempt_id,
                        external_submission_id=submission_id,
                        provider=verified.snapshot.model_ref.provider,
                        model_id=verified.snapshot.model_ref.model_id,
                        request_digest=str(graph.request_data_by_task[binding.task_id]["request_digest"]),
                        snapshot_ref=snapshot_ref,
                        committed_at=now,
                        first_intent_at=now,
                        deadline_at=deadline,
                    )
                    request_snapshot = _safe_intent_snapshot(
                        binding, graph, verified, snapshot_ref
                    )
                    staged = _StagedIntent(
                        binding=binding,
                        verified_input=verified,
                        intent=expected,
                        submission_key=submission_key,
                        request_snapshot=request_snapshot,
                        graph_digest=graph_digest,
                    )
                    session.execute(
                        insert(ExternalSubmissionRow.__table__).values(
                            id=submission_id,
                            step_id=binding.step_id,
                            submission_key=submission_key,
                            provider=expected.provider,
                            model_ref=expected.model_id,
                            request_digest=expected.request_digest,
                            request_snapshot=request_snapshot,
                            intent_at=_database_datetime(now),
                            submitted_at=None,
                            external_task_id=None,
                            submission_status="submitting",
                            deadline_at=_database_datetime(deadline),
                            created_at=_database_datetime(now),
                            updated_at=_database_datetime(now),
                        )
                    )
                    step_update = session.execute(
                        update(ExecutionStepRow.__table__)
                        .where(
                            ExecutionStepRow.__table__.c.id == binding.step_id,
                            ExecutionStepRow.__table__.c.execution_status == "prepared",
                        )
                        .values(
                            execution_status="submitting",
                            updated_at=_database_datetime(now),
                        )
                    )
                    unit_table = BillingUnitRow.__table__
                    unit_update = session.execute(
                        update(unit_table)
                        .where(
                            unit_table.c.id == binding.unit_id,
                            unit_table.c.task_id == binding.task_id,
                            unit_table.c.operation == _OPERATION,
                            unit_table.c.step_graph_version == _GRAPH_VERSION,
                            unit_table.c.item_key == verified.server_attempt_id,
                            unit_table.c.input_digest == expected.request_digest,
                            unit_table.c.user_cancelled_at.is_(None),
                            unit_table.c.billing_status.in_(("charged", "no_charge")),
                            unit_table.c.result_status == "pending",
                            unit_table.c.success_confirmed_at.is_(None),
                            unit_table.c.first_intent_at.is_(None),
                            unit_table.c.deadline_at.is_(None),
                            unit_table.c.debited_amount == graph.unit.get("debited_amount"),
                            unit_table.c.refunded_amount == graph.unit.get("refunded_amount"),
                        )
                        .values(
                            first_intent_at=_database_datetime(now),
                            deadline_at=_database_datetime(deadline),
                            updated_at=_database_datetime(now),
                        )
                    )
                    if step_update.rowcount != 1 or unit_update.rowcount != 1:
                        raise RuntimeError("image.batch intent graph changed during staging")
        except Exception:
            # A commit exception does not establish whether the intent exists.
            return staged
        return staged

    def _lock_graph(self, session: Session, claim: ClaimAuthority) -> _LockedBatchGraph | None:
        owner_id = claim.scope.user_id
        task_id = claim.scope.task_id
        task_table = AITaskRow.__table__
        parent_table = TaskSubmissionRow.__table__

        task_probe = session.execute(
            select(task_table).where(
                task_table.c.id == task_id,
                task_table.c.user_id == owner_id,
                task_table.c.type == _TASK_TYPE,
            )
        ).mappings().one_or_none()
        if task_probe is None:
            return None
        request_data = _json_object(task_probe.get("request_data"))
        if request_data is None:
            return None
        submission_id = request_data.get("submission_id")
        if not isinstance(submission_id, str) or not submission_id:
            return None
        parent_probe = session.execute(
            select(parent_table).where(parent_table.c.id == submission_id)
        ).mappings().one_or_none()
        if parent_probe is None:
            return None
        ordered_task_ids = _ordered_task_ids(parent_probe.get("task_ids"))
        if ordered_task_ids is None or task_id not in ordered_task_ids:
            return None

        user = session.execute(
            select(UserRow.__table__)
            .where(UserRow.__table__.c.id == owner_id)
            .with_for_update(skip_locked=True)
        ).mappings().one_or_none()
        if user is None:
            return None
        task_rows = session.execute(
            select(task_table)
            .where(task_table.c.id.in_(ordered_task_ids), task_table.c.user_id == owner_id)
            .order_by(task_table.c.id)
            .with_for_update(skip_locked=True)
        ).mappings().all()
        if len(task_rows) != len(ordered_task_ids):
            return None
        tasks_by_id = {row["id"]: dict(row) for row in task_rows}
        if set(tasks_by_id) != set(ordered_task_ids):
            return None
        if tasks_by_id[task_id].get("request_data") != task_probe.get("request_data"):
            return None

        unit_table = BillingUnitRow.__table__
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
            task_key = row["task_id"]
            if task_key in units_by_task:
                return None
            units_by_task[task_key] = dict(row)
        if set(units_by_task) != set(ordered_task_ids):
            return None

        unit_ids = tuple(units_by_task[item_id]["id"] for item_id in ordered_task_ids)
        step_rows = session.execute(
            select(ExecutionStepRow.__table__)
            .where(ExecutionStepRow.__table__.c.billing_unit_id.in_(unit_ids))
            .order_by(
                ExecutionStepRow.__table__.c.billing_unit_id,
                ExecutionStepRow.__table__.c.step_order,
                ExecutionStepRow.__table__.c.id,
            )
            .with_for_update()
        ).mappings().all()
        if len(step_rows) != len(ordered_task_ids):
            return None
        steps_by_unit: dict[str, dict[str, object]] = {}
        for row in step_rows:
            unit_key = row["billing_unit_id"]
            if unit_key in steps_by_unit:
                return None
            steps_by_unit[unit_key] = dict(row)
        if set(steps_by_unit) != set(unit_ids):
            return None
        steps_by_task = {
            item_id: steps_by_unit[units_by_task[item_id]["id"]]
            for item_id in ordered_task_ids
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
            parent.get("id") != submission_id
            or parent.get("user_id") != owner_id
            or parent.get("operation") != _OPERATION
            or parent.get("status") != "accepted"
            or parent.get("task_ids") != list(ordered_task_ids)
            or not _nonempty_text(parent.get("idempotency_key"))
            or not _is_digest(parent.get("request_digest"))
        ):
            return None

        request_data_by_task: dict[str, dict[str, object]] = {}
        for index, item_id in enumerate(ordered_task_ids):
            task = tasks_by_id[item_id]
            unit = units_by_task[item_id]
            step = steps_by_task[item_id]
            request = _json_object(task.get("request_data"))
            input_snapshot = step.get("input_snapshot")
            if (
                request is None
                or not isinstance(input_snapshot, dict)
                or task.get("type") != _TASK_TYPE
                or task.get("user_id") != owner_id
                or not isinstance(task.get("message_id"), str)
                or not task.get("message_id")
                or task.get("credit_cost") != unit.get("quoted_amount")
                or unit.get("task_id") != item_id
                or unit.get("operation") != _OPERATION
                or unit.get("step_graph_version") != _GRAPH_VERSION
                or unit.get("item_key") != f"{submission_id}:{index}"
                or unit.get("input_digest") != parent.get("request_digest")
                or step.get("billing_unit_id") != unit.get("id")
                or step.get("step_key") != "generate"
                or step.get("required") is not True
                or step.get("step_order") != 0
                or step.get("dependencies") not in ([], ())
                or step.get("input_digest") != parent.get("request_digest")
                or request.get("operation") != _OPERATION
                or request.get("submission_id") != submission_id
                or request.get("parent_attempt_id") != submission_id
                or request.get("server_attempt_id") != f"{submission_id}:{index}"
                or request.get("prepared_payload_id") != input_snapshot.get("prepared_payload_id")
                or request.get("protected_ref") != input_snapshot.get("protected_ref")
                or request.get("snapshot_digest") != input_snapshot.get("snapshot_digest")
                or request.get("request_digest") != parent.get("request_digest")
                or request.get("billing_unit_id") != unit.get("id")
                or request.get("step_graph_version") != _GRAPH_VERSION
            ):
                return None
            request_data_by_task[item_id] = request

        selected_unit = units_by_task[task_id]
        selected_step = steps_by_task[task_id]
        existing = session.execute(
            select(ExternalSubmissionRow.__table__)
            .where(ExternalSubmissionRow.__table__.c.step_id == selected_step["id"])
            .order_by(ExternalSubmissionRow.__table__.c.id)
            .with_for_update()
        ).mappings().all()
        return _LockedBatchGraph(
            task=tasks_by_id[task_id],
            unit=selected_unit,
            step=selected_step,
            parent_submission=parent,
            ordered_task_ids=ordered_task_ids,
            tasks_by_id=tasks_by_id,
            units_by_task=units_by_task,
            steps_by_task=steps_by_task,
            request_data_by_task=request_data_by_task,
            existing_submissions=tuple(dict(row) for row in existing),
        )

    def _confirm_intent(
        self, claim: ClaimAuthority, staged: _StagedIntent
    ) -> CommittedIntent | Literal["existing"] | None:
        try:
            with self.session_factory() as session:
                graph = self._lock_graph(session, claim)
                now = self._server_now()
                if (
                    graph is None
                    or len(graph.existing_submissions) != 1
                    or not _claim_is_current(graph.task, claim, now)
                    or graph.step.get("execution_status") != "submitting"
                    or not _time_matches(
                        graph.unit.get("first_intent_at"), staged.intent.first_intent_at
                    )
                    or not _time_matches(
                        graph.unit.get("deadline_at"), staged.intent.deadline_at
                    )
                    or not _unit_can_confirm_intent(graph.unit)
                    or claim.local_lost_signal.is_set()
                ):
                    return None
                binding = _execution_binding(graph)
                if (
                    binding != staged.binding
                    or _original_graph_digest(graph) != staged.graph_digest
                ):
                    return "existing"
                if not _intent_row_matches(graph.existing_submissions[0], claim, staged):
                    return "existing"
                return staged.intent
        except Exception:
            return None

    def _mark_reconciling(
        self, session: Session, graph: _LockedBatchGraph, claim: ClaimAuthority
    ) -> bool:
        now = self._server_now()
        task_table = AITaskRow.__table__
        if not isinstance(graph.task.get("lease_until"), datetime) or not _claim_is_current(
            graph.task, claim, now
        ):
            return False
        changed = session.execute(
            update(task_table)
            .where(
                task_table.c.id == claim.scope.task_id,
                task_table.c.user_id == claim.scope.user_id,
                task_table.c.type == _TASK_TYPE,
                task_table.c.status == graph.task.get("status"),
                task_table.c.status.in_(_CLAIMABLE_STATUS),
                task_table.c.claimed_by == claim.worker_instance,
                task_table.c.claim_token == claim.token,
                task_table.c.execution_generation == claim.execution_generation,
                task_table.c.user_cancelled_at.is_(None),
                task_table.c.lease_until
                == _database_datetime(_aware_utc(graph.task["lease_until"])),
                task_table.c.lease_until > _database_datetime(now),
                task_table.c.request_data == graph.task.get("request_data"),
            )
            .values(
                status="reconciling",
                recovery_status="reconciling",
                progress_message="批量图片任务需要恢复确认",
                updated_at=_database_datetime(now),
            )
        )
        return changed.rowcount == 1

    def _server_now(self) -> datetime:
        value = self.db_clock()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() != timezone.utc.utcoffset(value)
        ):
            raise ValueError("database clock must return an aware UTC datetime")
        return value.astimezone(timezone.utc)


def _execution_binding(graph: _LockedBatchGraph) -> PersistedImageBatchExecutionBinding | None:
    task = graph.task
    unit = graph.unit
    step = graph.step
    parent = graph.parent_submission
    request = graph.request_data_by_task.get(str(task.get("id")))
    input_snapshot = step.get("input_snapshot")
    if request is None or not isinstance(input_snapshot, dict):
        return None
    try:
        binding = PersistedImageBatchExecutionBinding(
            owner_id=str(task["user_id"]),
            task_id=str(task["id"]),
            unit_id=str(unit["id"]),
            step_id=str(step["id"]),
            submission_id=str(parent["id"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
    index = graph.ordered_task_ids.index(binding.task_id)
    if (
        binding.owner_id != parent.get("user_id")
        or binding.submission_id != request.get("submission_id")
        or parent.get("task_ids") != list(graph.ordered_task_ids)
        or unit.get("task_id") != binding.task_id
        or unit.get("operation") != _OPERATION
        or unit.get("step_graph_version") != _GRAPH_VERSION
        or unit.get("item_key") != f"{binding.submission_id}:{index}"
        or unit.get("input_digest") != parent.get("request_digest")
        or step.get("billing_unit_id") != binding.unit_id
        or step.get("input_digest") != parent.get("request_digest")
        or request.get("server_attempt_id") != f"{binding.submission_id}:{index}"
        or request.get("prepared_payload_id") != input_snapshot.get("prepared_payload_id")
        or request.get("protected_ref") != input_snapshot.get("protected_ref")
        or request.get("snapshot_digest") != input_snapshot.get("snapshot_digest")
    ):
        return None
    return binding


def _verified_input_matches(
    verified: VerifiedImageBatchExecutionInput | None,
    binding: PersistedImageBatchExecutionBinding,
    graph: _LockedBatchGraph,
) -> bool:
    if type(verified) is not VerifiedImageBatchExecutionInput:
        return False
    index = graph.ordered_task_ids.index(binding.task_id)
    request = graph.request_data_by_task[binding.task_id]
    return all(
        (
            verified.task_id == binding.task_id,
            verified.unit_id == binding.unit_id,
            verified.step_id == binding.step_id,
            verified.submission_id == binding.submission_id,
            verified.item_index == index,
            verified.ordered_count == len(graph.ordered_task_ids),
            verified.server_attempt_id == request.get("server_attempt_id"),
            verified.prepared_payload_id == request.get("prepared_payload_id"),
        )
    )


def _unit_can_create_intent(unit: dict[str, object]) -> bool:
    return all(
        (
            unit.get("user_cancelled_at") is None,
            unit.get("cancellation_reason") is None,
            unit.get("billing_status") in {"charged", "no_charge"},
            unit.get("result_status") == "pending",
            unit.get("success_confirmed_at") is None,
            unit.get("first_intent_at") is None,
            unit.get("deadline_at") is None,
        )
    )


def _unit_can_confirm_intent(unit: dict[str, object]) -> bool:
    return all(
        (
            unit.get("user_cancelled_at") is None,
            unit.get("cancellation_reason") is None,
            unit.get("billing_status") in {"charged", "no_charge"},
            unit.get("result_status") == "pending",
            unit.get("success_confirmed_at") is None,
        )
    )


def _claim_is_current(task: dict[str, object], claim: ClaimAuthority, now: datetime) -> bool:
    lease = task.get("lease_until")
    return bool(
        task.get("id") == claim.scope.task_id
        and task.get("user_id") == claim.scope.user_id
        and task.get("type") == _TASK_TYPE
        and claim.scope.operation == _OPERATION
        and task.get("status") in _CLAIMABLE_STATUS
        and task.get("claimed_by") == claim.worker_instance
        and task.get("claim_token") == claim.token
        and task.get("execution_generation") == claim.execution_generation
        and task.get("user_cancelled_at") is None
        and isinstance(lease, datetime)
        and _aware_utc(lease) > now
        and not claim.local_lost_signal.is_set()
    )


def _intent_row_matches(
    row: dict[str, object], claim: ClaimAuthority, staged: _StagedIntent
) -> bool:
    expected = staged.intent
    snapshot = row.get("request_snapshot")
    return bool(
        row.get("id") == expected.external_submission_id
        and row.get("step_id") == expected.step_id
        and row.get("submission_key") == staged.submission_key
        and row.get("provider") == expected.provider
        and row.get("model_ref") == expected.model_id
        and row.get("request_digest") == expected.request_digest
        and _time_matches(row.get("intent_at"), expected.committed_at)
        and _time_matches(row.get("deadline_at"), expected.deadline_at)
        and row.get("submission_status") == "submitting"
        and row.get("submitted_at") is None
        and row.get("external_task_id") is None
        and snapshot == staged.request_snapshot
        and staged.request_snapshot.get("task_id") == claim.scope.task_id
        and staged.request_snapshot.get("owner_id") == claim.scope.user_id
        and staged.request_snapshot.get("operation") == _OPERATION
        and staged.request_snapshot.get("attempt_id") == expected.attempt_id
        and staged.request_snapshot.get("snapshot_ref") == expected.snapshot_ref
        and staged.request_snapshot.get("model_id") == expected.model_id
        and staged.request_snapshot.get("provider") == expected.provider
    )


def _safe_intent_snapshot(
    binding: PersistedImageBatchExecutionBinding,
    graph: _LockedBatchGraph,
    verified: VerifiedImageBatchExecutionInput,
    snapshot_ref: str,
) -> dict[str, object]:
    return {
        "owner_id": binding.owner_id,
        "task_id": binding.task_id,
        "unit_id": binding.unit_id,
        "step_id": binding.step_id,
        "submission_id": binding.submission_id,
        "operation": _OPERATION,
        "graph_version": _GRAPH_VERSION,
        "item_index": verified.item_index,
        "ordered_count": verified.ordered_count,
        "attempt_id": verified.server_attempt_id,
        "prepared_payload_id": verified.prepared_payload_id,
        "protected_ref": graph.request_data_by_task[binding.task_id]["protected_ref"],
        "snapshot_digest": graph.request_data_by_task[binding.task_id]["snapshot_digest"],
        "snapshot_ref": snapshot_ref,
        "credential_binding_ref": verified.snapshot.credential_binding_ref,
        "target_snapshot": asdict(verified.snapshot.target_snapshot),
        "provider": verified.snapshot.model_ref.provider,
        "model_id": verified.snapshot.model_ref.model_id,
    }


def _execution_snapshot_ref(
    binding: PersistedImageBatchExecutionBinding,
    graph: _LockedBatchGraph,
    verified: VerifiedImageBatchExecutionInput,
) -> str:
    request = graph.request_data_by_task[binding.task_id]
    input_snapshot = graph.steps_by_task[binding.task_id]["input_snapshot"]
    if not isinstance(input_snapshot, dict):
        raise ValueError("the original batch snapshot is unavailable")
    snapshot_digest = request["snapshot_digest"]
    manifest_digest = input_snapshot["ordered_manifest_digest"]
    return f"{_GRAPH_VERSION}:{verified.prepared_payload_id}:{snapshot_digest}:{manifest_digest}"


def _intent_submission_key(unit_id: str, step_id: str) -> str:
    value = f"image-batch-create-v1:{unit_id}:{step_id}"
    if len(value) > 255:
        raise ValueError("execution step id exceeds the stable intent key limit")
    return value


def _original_graph_digest(graph: _LockedBatchGraph) -> str | None:
    facts = {
        "parent": {
            key: graph.parent_submission.get(key)
            for key in ("id", "user_id", "operation", "idempotency_key", "request_digest", "task_ids")
        },
        "items": [
            {
                "task": {
                    key: graph.tasks_by_id[task_id].get(key)
                    for key in ("id", "user_id", "type", "message_id", "credit_cost", "model_name", "request_data")
                },
                "unit": {
                    key: graph.units_by_task[task_id].get(key)
                    for key in ("id", "task_id", "item_key", "operation", "step_graph_version", "input_digest", "quoted_amount")
                },
                "step": {
                    key: graph.steps_by_task[task_id].get(key)
                    for key in ("id", "billing_unit_id", "step_key", "dependencies", "input_digest", "input_snapshot", "required", "step_order")
                },
            }
            for task_id in graph.ordered_task_ids
        ],
    }
    try:
        encoded = json.dumps(
            facts,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        return None
    return hashlib.sha256(encoded).hexdigest()


def _ordered_task_ids(value: object) -> tuple[str, ...] | None:
    if (
        not isinstance(value, list)
        or not 1 <= len(value) <= _MAX_BATCH_ITEMS
        or any(not _nonempty_text(item) for item in value)
        or len(set(value)) != len(value)
    ):
        return None
    return tuple(value)


def _json_object(value: object) -> dict[str, object] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    try:
        result = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return result if isinstance(result, dict) else None


def _nonempty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _is_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _DIGEST_LENGTH
        and all(char in "0123456789abcdef" for char in value)
    )


def _aware_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _time_matches(value: object, expected: datetime) -> bool:
    return isinstance(value, datetime) and _aware_utc(value) == expected


def _database_datetime(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)


__all__ = ("ImageBatchIntentPreparation", "SqlAlchemyImageBatchIntents")
