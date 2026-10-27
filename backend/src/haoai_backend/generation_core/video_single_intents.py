"""Durable intent admission for the ordinary one-step video.single graph."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.types import (
    ClaimAuthority,
    CommittedIntent,
    ExternalOperationRef,
)
from haoai_backend.generation_credentials.application import CredentialRegistry
from haoai_backend.generation_credentials.domain import CredentialCrypto, HmacProofAuthority
from haoai_backend.generation_credentials.video_execution_snapshot import (
    PersistedVideoExecutionBinding,
    VerifiedVideoExecutionInput,
    read_original_video_single_execution_input,
)

from .base_schema import (
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
_ORIGINAL_DEADLINE_SECONDS = 7200
_CLAIMABLE_STATUS = frozenset({"processing", "reconciling"})
_RECONCILING_RESULT = json.dumps(
    {"message": "视频生成结果需要恢复确认", "status": "reconciling"},
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
)

SessionFactory = Callable[[], Session]
DbClock = Callable[[], datetime]
IdFactory = Callable[[], str]
VideoSnapshotReader = Callable[..., VerifiedVideoExecutionInput | None]


@dataclass(frozen=True, slots=True)
class VideoSingleIntentPreparation:
    disposition: Literal[
        "committed", "resume_query", "existing", "unresolved", "ownership_lost"
    ]
    verified_input: VerifiedVideoExecutionInput | None = None
    intent: CommittedIntent | None = None
    external_ref: ExternalOperationRef | None = None


@dataclass(frozen=True, slots=True)
class _LockedVideoGraph:
    task: dict[str, object]
    unit: dict[str, object]
    step: dict[str, object]
    parent_submission: dict[str, object]
    request_data: dict[str, object]
    existing_submissions: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class _StagedIntent:
    binding: PersistedVideoExecutionBinding
    verified_input: VerifiedVideoExecutionInput
    intent: CommittedIntent
    submission_key: str
    request_snapshot: dict[str, object]
    graph_digest: str


@dataclass(frozen=True, slots=True)
class _StagedQueryResume:
    binding: PersistedVideoExecutionBinding
    verified_input: VerifiedVideoExecutionInput
    intent: CommittedIntent
    external_ref: ExternalOperationRef
    submission: dict[str, object]
    graph_digest: str


@dataclass(slots=True)
class SqlAlchemyVideoSingleIntents:
    """Commit one video intent and confirm it in an independent session."""

    session_factory: SessionFactory
    db_clock: DbClock
    id_factory: IdFactory
    registry: CredentialRegistry
    crypto: CredentialCrypto
    proof_authority: HmacProofAuthority
    snapshot_reader: VideoSnapshotReader = read_original_video_single_execution_input

    def prepare(
        self,
        claim: ClaimAuthority,
    ) -> VideoSingleIntentPreparation:
        if not isinstance(claim, ClaimAuthority):
            return VideoSingleIntentPreparation("ownership_lost")
        if claim.scope.operation != _OPERATION or claim.local_lost_signal.is_set():
            return VideoSingleIntentPreparation("ownership_lost")
        resume = self._prepare_existing_query(claim)
        if resume is not None:
            return resume
        staged = self._stage_and_commit(claim)
        if staged is None:
            return VideoSingleIntentPreparation("unresolved")
        confirmed = self._confirm_intent(claim, staged)
        if confirmed is None:
            return VideoSingleIntentPreparation("unresolved")
        if confirmed == "existing":
            return VideoSingleIntentPreparation("existing")
        return VideoSingleIntentPreparation(
            "committed", staged.verified_input, confirmed
        )

    def _prepare_existing_query(
        self, claim: ClaimAuthority
    ) -> VideoSingleIntentPreparation | None:
        staged: _StagedQueryResume | None = None
        try:
            with self.session_factory() as session:
                with session.begin():
                    graph = self._lock_graph(session, claim)
                    now = self._server_now()
                    if graph is None or not _claim_is_current(graph.task, claim, now):
                        return VideoSingleIntentPreparation("ownership_lost")
                    if not graph.existing_submissions:
                        return None
                    staged = self._stage_query_resume(session, graph, now)
        except Exception:
            return VideoSingleIntentPreparation("unresolved")
        if staged is None:
            return VideoSingleIntentPreparation("unresolved")
        if not self._confirm_query_resume(claim, staged):
            return VideoSingleIntentPreparation("unresolved")
        return VideoSingleIntentPreparation(
            "resume_query",
            staged.verified_input,
            staged.intent,
            staged.external_ref,
        )

    def _stage_query_resume(
        self,
        session: Session,
        graph: _LockedVideoGraph,
        now: datetime,
    ) -> _StagedQueryResume | None:
        if not _query_resume_graph_is_valid(graph, now):
            return None
        binding = _execution_binding(graph)
        if binding is None:
            return None
        try:
            if self.snapshot_reader is read_original_video_single_execution_input:
                verified = self.snapshot_reader(
                    session,
                    binding,
                    registry=self.registry,
                    crypto=self.crypto,
                    proof_authority=self.proof_authority,
                    credential_purpose="query",
                )
            else:
                verified = self.snapshot_reader(
                    session, binding, credential_purpose="query"
                )
        except Exception:
            return None
        if (
            verified is None
            or verified.task_id != binding.task_id
            or verified.unit_id != binding.unit_id
            or verified.step_id != binding.step_id
            or verified.server_attempt_id != binding.server_attempt_id
        ):
            return None
        row = graph.existing_submissions[0]
        intent = _intent_from_existing_submission(row, binding, verified)
        if intent is None:
            return None
        try:
            request_snapshot = _safe_intent_snapshot(binding, graph, verified)
        except (KeyError, TypeError, ValueError):
            return None
        if row.get("request_snapshot") != request_snapshot:
            return None
        external_ref = ExternalOperationRef(
            intent_id=str(row["id"]),
            attempt_id=binding.server_attempt_id,
            provider=intent.provider,
            external_id=str(row["external_task_id"]),
            create_outcome="submitted",
        )
        graph_digest = _original_graph_digest(graph)
        if graph_digest is None:
            return None
        return _StagedQueryResume(
            binding=binding,
            verified_input=verified,
            intent=intent,
            external_ref=external_ref,
            submission=dict(row),
            graph_digest=graph_digest,
        )

    def _confirm_query_resume(
        self,
        claim: ClaimAuthority,
        staged: _StagedQueryResume,
    ) -> bool:
        try:
            with self.session_factory() as session:
                with session.begin():
                    graph = self._lock_graph(session, claim)
                    now = self._server_now()
                    if (
                        graph is None
                        or not _claim_is_current(graph.task, claim, now)
                        or not _query_resume_graph_is_valid(graph, now)
                        or len(graph.existing_submissions) != 1
                        or _execution_binding(graph) != staged.binding
                        or _original_graph_digest(graph) != staged.graph_digest
                        or graph.existing_submissions[0] != staged.submission
                    ):
                        return False
                    intent = _intent_from_existing_submission(
                        graph.existing_submissions[0],
                        staged.binding,
                        staged.verified_input,
                    )
                    return bool(
                        intent == staged.intent
                        and staged.external_ref.intent_id == staged.intent.external_submission_id
                        and staged.external_ref.attempt_id == staged.intent.attempt_id
                        and staged.external_ref.provider == staged.intent.provider
                        and staged.external_ref.external_id
                        == graph.existing_submissions[0].get("external_task_id")
                        and staged.external_ref.create_outcome == "submitted"
                        and graph.existing_submissions[0].get("request_snapshot")
                        == _safe_intent_snapshot(
                            staged.binding, graph, staged.verified_input
                        )
                    )
        except Exception:
            return False

    def _stage_and_commit(
        self, claim: ClaimAuthority
    ) -> _StagedIntent | None:
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
                    if self.snapshot_reader is read_original_video_single_execution_input:
                        verified = self.snapshot_reader(
                            session,
                            binding,
                            registry=self.registry,
                            crypto=self.crypto,
                            proof_authority=self.proof_authority,
                        )
                    else:
                        verified = self.snapshot_reader(session, binding)
                    if (
                        verified is None
                        or verified.task_id != binding.task_id
                        or verified.unit_id != binding.unit_id
                        or verified.step_id != binding.step_id
                        or verified.server_attempt_id != binding.server_attempt_id
                    ):
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
                    # The no-op CAS also serializes SQLite cancellation writers.
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
                            == _database_datetime(
                                _aware_utc(graph.task["lease_until"])
                            ),
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
                    snapshot_ref = _execution_snapshot_ref(binding)
                    deadline = now + timedelta(seconds=_ORIGINAL_DEADLINE_SECONDS)
                    expected = CommittedIntent(
                        step_id=binding.step_id,
                        attempt_id=binding.server_attempt_id,
                        external_submission_id=submission_id,
                        provider=verified.snapshot.model_ref.provider,
                        model_id=verified.snapshot.model_ref.model_id,
                        request_digest=binding.request_digest,
                        snapshot_ref=snapshot_ref,
                        committed_at=now,
                        first_intent_at=now,
                        deadline_at=deadline,
                    )
                    request_snapshot = _safe_intent_snapshot(binding, graph, verified)
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
                            ExecutionStepRow.id == binding.step_id,
                            ExecutionStepRow.execution_status == "prepared",
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
                            unit_table.c.item_key == binding.server_attempt_id,
                            unit_table.c.input_digest == binding.request_digest,
                            unit_table.c.user_cancelled_at.is_(None),
                            unit_table.c.billing_status.in_(("charged", "no_charge")),
                            unit_table.c.result_status == "pending",
                            unit_table.c.success_confirmed_at.is_(None),
                            unit_table.c.first_intent_at.is_(None),
                            unit_table.c.deadline_at.is_(None),
                            unit_table.c.debited_amount
                            == graph.unit.get("debited_amount"),
                            unit_table.c.refunded_amount
                            == graph.unit.get("refunded_amount"),
                        )
                        .values(
                            first_intent_at=_database_datetime(now),
                            deadline_at=_database_datetime(deadline),
                            updated_at=_database_datetime(now),
                        )
                    )
                    if step_update.rowcount != 1 or unit_update.rowcount != 1:
                        raise RuntimeError("video intent graph changed during staging")
        except Exception:
            # A commit error is not evidence that the intent is absent.
            return staged
        return staged

    def _lock_graph(self, session: Session, claim: ClaimAuthority) -> _LockedVideoGraph | None:
        owner_id = claim.scope.user_id
        task_id = claim.scope.task_id
        user = session.execute(
            select(UserRow.__table__)
            .where(UserRow.id == owner_id)
            .with_for_update(skip_locked=True)
        ).mappings().one_or_none()
        if user is None:
            return None
        task_row = session.execute(
            select(AITaskRow.__table__)
            .where(AITaskRow.id == task_id, AITaskRow.user_id == owner_id)
            .with_for_update(skip_locked=True)
        ).mappings().one_or_none()
        if task_row is None:
            return None
        units = session.execute(
            select(BillingUnitRow.__table__)
            .where(BillingUnitRow.task_id == task_id)
            .order_by(BillingUnitRow.id)
            .with_for_update()
        ).mappings().all()
        if len(units) != 1:
            return None
        unit = dict(units[0])
        steps = session.execute(
            select(ExecutionStepRow.__table__)
            .where(ExecutionStepRow.billing_unit_id == unit["id"])
            .order_by(ExecutionStepRow.step_order, ExecutionStepRow.id)
            .with_for_update()
        ).mappings().all()
        if len(steps) != 1:
            return None
        step = dict(steps[0])
        task = dict(task_row)
        try:
            request_data = json.loads(task["request_data"])
            snapshot = step.get("input_snapshot")
            submission_id = request_data.get("submission_id")
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(request_data, dict) or not isinstance(snapshot, dict):
            return None
        parent = session.execute(
            select(TaskSubmissionRow.__table__)
            .where(TaskSubmissionRow.id == submission_id)
            .with_for_update(skip_locked=True)
        ).mappings().one_or_none()
        if parent is None:
            return None
        existing = session.execute(
            select(ExternalSubmissionRow.__table__)
            .where(ExternalSubmissionRow.step_id == step["id"])
            .order_by(ExternalSubmissionRow.id)
            .with_for_update()
        ).mappings().all()
        return _LockedVideoGraph(
            task=task,
            unit=unit,
            step=step,
            parent_submission=dict(parent),
            request_data=request_data,
            existing_submissions=tuple(dict(row) for row in existing),
        )

    def _confirm_intent(
        self,
        claim: ClaimAuthority,
        staged: _StagedIntent,
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
                if not _intent_row_matches(
                    graph.existing_submissions[0], claim, staged
                ):
                    return "existing"
                return staged.intent
        except Exception:
            return None

    def _mark_reconciling(
        self,
        session: Session,
        graph: _LockedVideoGraph,
        claim: ClaimAuthority,
    ) -> bool:
        now = self._server_now()
        task_table = AITaskRow.__table__
        lease = graph.task.get("lease_until")
        if (
            not isinstance(lease, datetime)
            or not _claim_is_current(graph.task, claim, now)
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
                task_table.c.lease_until == _database_datetime(_aware_utc(lease)),
                task_table.c.lease_until > _database_datetime(now),
                task_table.c.request_data == graph.task.get("request_data"),
            )
            .values(
                status="reconciling",
                recovery_status="reconciling",
                progress_message="视频任务需要恢复确认",
                updated_at=_database_datetime(now),
            )
        )
        if changed.rowcount != 1:
            return False
        return True

    def _server_now(self) -> datetime:
        value = self.db_clock()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() != timezone.utc.utcoffset(value)
        ):
            raise ValueError("database clock must return an aware UTC datetime")
        return value.astimezone(timezone.utc)


def _execution_binding(
    graph: _LockedVideoGraph,
) -> PersistedVideoExecutionBinding | None:
    task = graph.task
    unit = graph.unit
    step = graph.step
    parent = graph.parent_submission
    request_data = graph.request_data
    snapshot = step.get("input_snapshot")
    if not isinstance(snapshot, dict):
        return None
    try:
        binding = PersistedVideoExecutionBinding(
            owner_id=str(task["user_id"]),
            task_id=str(task["id"]),
            unit_id=str(unit["id"]),
            step_id=str(step["id"]),
            submission_id=str(parent["id"]),
            raw_key=str(parent["idempotency_key"]),
            operation=str(parent["operation"]),
            graph_version=str(unit["step_graph_version"]),
            task_type=str(task["type"]),
            task_model_id=str(request_data["model_id"]),
            task_model_source=str(request_data["model_source"]),
            task_model_owner_id=(
                str(request_data["model_owner_id"])
                if request_data.get("model_owner_id") is not None
                else None
            ),
            task_model_name=str(task["model_name"]),
            request_digest=str(request_data["request_digest"]),
            rule_digest=str(request_data["rule_digest"]),
            snapshot_digest=str(request_data["snapshot_digest"]),
            server_attempt_id=str(snapshot["server_attempt_id"]),
            prepared_payload_id=str(snapshot["prepared_payload_id"]),
            protected_ref=str(snapshot["protected_ref"]),
            ordered_manifest_digest=str(snapshot["ordered_manifest_digest"]),
            quoted_amount=int(unit["quoted_amount"]),
            task_message_id=(str(task["message_id"]) if task.get("message_id") else None),
        )
    except (KeyError, TypeError, ValueError):
        return None
    if (
        binding.task_type != _TASK_TYPE
        or binding.operation != _OPERATION
        or binding.graph_version != _GRAPH_VERSION
        or unit.get("task_id") != binding.task_id
        or step.get("billing_unit_id") != binding.unit_id
        or parent.get("task_ids") != [binding.task_id]
        or unit.get("operation") != _OPERATION
        or unit.get("item_key") != binding.server_attempt_id
        or unit.get("input_digest") != binding.request_digest
        or step.get("step_key") != "generate"
        or step.get("required") is not True
        or step.get("step_order") != 0
        or step.get("dependencies") not in ([], ())
        or step.get("input_digest") != binding.request_digest
        or request_data.get("operation") != _OPERATION
        or request_data.get("submission_id") != binding.submission_id
        or request_data.get("parent_attempt_id") != binding.submission_id
        or request_data.get("server_attempt_id") != binding.server_attempt_id
        or request_data.get("prepared_payload_id") != binding.prepared_payload_id
        or request_data.get("protected_ref") != binding.protected_ref
        or request_data.get("billing_unit_id") != binding.unit_id
        or request_data.get("request_digest") != binding.request_digest
        or request_data.get("step_graph_version") != _GRAPH_VERSION
        or parent.get("user_id") != binding.owner_id
        or parent.get("operation") != _OPERATION
        or parent.get("request_digest") != binding.request_digest
        or not parent.get("idempotency_key")
    ):
        return None
    return binding


def _query_resume_graph_is_valid(
    graph: _LockedVideoGraph, now: datetime
) -> bool:
    if len(graph.existing_submissions) != 1:
        return False
    task = graph.task
    unit = graph.unit
    step = graph.step
    row = graph.existing_submissions[0]
    status = row.get("submission_status")
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

    external_id = row.get("external_task_id")
    intent_at = row.get("intent_at")
    submitted_at = row.get("submitted_at")
    deadline_at = row.get("deadline_at")
    first_intent_at = unit.get("first_intent_at")
    unit_deadline_at = unit.get("deadline_at")
    if (
        not _safe_provider_id(external_id)
        or not all(
            isinstance(value, datetime)
            for value in (
                intent_at,
                submitted_at,
                deadline_at,
                first_intent_at,
                unit_deadline_at,
            )
        )
        or not _known_id_submission_is_valid(row)
    ):
        return False
    try:
        intent = _aware_utc(intent_at)
        submitted = _aware_utc(submitted_at)
        deadline = _aware_utc(deadline_at)
        first_intent = _aware_utc(first_intent_at)
        unit_deadline = _aware_utc(unit_deadline_at)
    except (TypeError, ValueError):
        return False
    return all(
        (
            task.get("status") in _CLAIMABLE_STATUS,
            task.get("user_cancelled_at") is None,
            unit.get("user_cancelled_at") is None,
            unit.get("success_confirmed_at") is None,
            unit.get("billing_status") in {"charged", "no_charge"},
            task.get("billing_status") == unit.get("billing_status"),
            unit.get("refunded_amount") == 0,
            state_matches,
            row.get("step_id") == step.get("id"),
            row.get("submission_key")
            == _intent_submission_key(str(unit.get("id")), str(step.get("id"))),
            row.get("provider") in {"xai", "manxiaobai", "aistarslab"},
            _nonempty_string(row.get("model_ref")),
            row.get("request_digest") == unit.get("input_digest"),
            intent == first_intent,
            deadline == unit_deadline,
            deadline == first_intent + timedelta(seconds=_ORIGINAL_DEADLINE_SECONDS),
            first_intent <= submitted < deadline,
            now < deadline,
        )
    )


def _intent_from_existing_submission(
    row: dict[str, object],
    binding: PersistedVideoExecutionBinding,
    verified: VerifiedVideoExecutionInput,
) -> CommittedIntent | None:
    intent_at = row.get("intent_at")
    deadline_at = row.get("deadline_at")
    submitted_at = row.get("submitted_at")
    external_id = row.get("external_task_id")
    try:
        if (
            not _safe_provider_id(external_id)
            or not isinstance(intent_at, datetime)
            or not isinstance(deadline_at, datetime)
            or not isinstance(submitted_at, datetime)
            or not _known_id_submission_is_valid(row)
        ):
            return None
        committed_at = _aware_utc(intent_at)
        deadline = _aware_utc(deadline_at)
        submitted = _aware_utc(submitted_at)
    except (TypeError, ValueError):
        return None
    if (
        deadline != committed_at + timedelta(seconds=_ORIGINAL_DEADLINE_SECONDS)
        or not committed_at <= submitted < deadline
        or row.get("id") is None
        or row.get("step_id") != binding.step_id
        or row.get("submission_key")
        != _intent_submission_key(binding.unit_id, binding.step_id)
        or row.get("provider") != verified.snapshot.model_ref.provider
        or row.get("model_ref") != verified.snapshot.model_ref.model_id
        or row.get("request_digest") != binding.request_digest
        or verified.snapshot.model_ref.provider
        not in {"xai", "manxiaobai", "aistarslab"}
    ):
        return None
    return CommittedIntent(
        step_id=binding.step_id,
        attempt_id=binding.server_attempt_id,
        external_submission_id=str(row["id"]),
        provider=verified.snapshot.model_ref.provider,
        model_id=verified.snapshot.model_ref.model_id,
        request_digest=binding.request_digest,
        snapshot_ref=_execution_snapshot_ref(binding),
        committed_at=committed_at,
        first_intent_at=committed_at,
        deadline_at=deadline,
    )


def _safe_provider_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and value == value.strip()
        and len(value.encode("utf-8")) <= 255
        and not any(ord(character) < 0x20 for character in value)
    )


def _known_id_submission_is_valid(row: dict[str, object]) -> bool:
    return bool(
        row.get("submission_status") in {"submitted", "unknown"}
        and _safe_provider_id(row.get("external_task_id"))
        and isinstance(row.get("submitted_at"), datetime)
    )


def _nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _unit_can_create_intent(unit: dict[str, object]) -> bool:
    return (
        unit.get("user_cancelled_at") is None
        and unit.get("billing_status") in {"charged", "no_charge"}
        and unit.get("result_status") == "pending"
        and unit.get("success_confirmed_at") is None
        and unit.get("first_intent_at") is None
        and unit.get("deadline_at") is None
    )


def _unit_can_confirm_intent(unit: dict[str, object]) -> bool:
    return (
        unit.get("user_cancelled_at") is None
        and unit.get("billing_status") in {"charged", "no_charge"}
        and unit.get("result_status") == "pending"
        and unit.get("success_confirmed_at") is None
    )


def _claim_is_current(task: dict[str, object], claim: ClaimAuthority, now: datetime) -> bool:
    lease = task.get("lease_until")
    return bool(
        task.get("id") == claim.scope.task_id
        and task.get("user_id") == claim.scope.user_id
        and task.get("type") == _TASK_TYPE
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
    row: dict[str, object],
    claim: ClaimAuthority,
    staged: _StagedIntent,
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
    binding: PersistedVideoExecutionBinding,
    graph: _LockedVideoGraph,
    verified: VerifiedVideoExecutionInput,
) -> dict[str, object]:
    target = graph.request_data.get("target_snapshot")
    if not isinstance(target, dict):
        raise ValueError("the original target snapshot is unavailable")
    return {
        "owner_id": binding.owner_id,
        "task_id": binding.task_id,
        "unit_id": binding.unit_id,
        "step_id": binding.step_id,
        "operation": _OPERATION,
        "graph_version": _GRAPH_VERSION,
        "attempt_id": binding.server_attempt_id,
        "prepared_payload_id": binding.prepared_payload_id,
        "protected_ref": binding.protected_ref,
        "snapshot_digest": binding.snapshot_digest,
        "snapshot_ref": _execution_snapshot_ref(binding),
        "credential_binding_ref": verified.snapshot.credential_binding_ref,
        "target_snapshot": target,
        "provider": verified.snapshot.model_ref.provider,
        "model_id": verified.snapshot.model_ref.model_id,
    }


def _execution_snapshot_ref(binding: PersistedVideoExecutionBinding) -> str:
    return (
        f"video-single-v1:{binding.prepared_payload_id}:"
        f"{binding.snapshot_digest}:{binding.ordered_manifest_digest}"
    )


def _intent_submission_key(unit_id: str, step_id: str) -> str:
    value = f"video-single-create-v1:{unit_id}:{step_id}"
    if len(value) > 255:
        raise ValueError("execution step id exceeds the stable intent key limit")
    return value


def _original_graph_digest(graph: _LockedVideoGraph) -> str | None:
    task = graph.task
    unit = graph.unit
    step = graph.step
    parent = graph.parent_submission
    facts = {
        "task": {
            key: task.get(key)
            for key in (
                "id", "user_id", "type", "message_id", "credit_cost",
                "model_name", "request_data",
            )
        },
        "unit": {
            key: unit.get(key)
            for key in (
                "id", "task_id", "item_key", "operation", "step_graph_version",
                "input_digest", "quoted_amount",
            )
        },
        "step": {
            key: step.get(key)
            for key in (
                "id", "billing_unit_id", "step_key", "dependencies", "input_digest",
                "input_snapshot", "required", "step_order",
            )
        },
        "parent": {
            key: parent.get(key)
            for key in (
                "id", "user_id", "operation", "idempotency_key",
                "request_digest", "task_ids",
            )
        },
    }
    try:
        encoded = json.dumps(
            facts,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(encoded).hexdigest()

def _aware_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _time_matches(value: object, expected: datetime) -> bool:
    return isinstance(value, datetime) and _aware_utc(value) == expected


def _database_datetime(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)


__all__ = ("VideoSingleIntentPreparation", "SqlAlchemyVideoSingleIntents")
