"""Single-use original Provider intent for the registered frame.review graph."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.types import ClaimAuthority, CommittedIntent
from haoai_backend.generation_credentials.domain import CredentialCrypto, HmacProofAuthority
from haoai_backend.generation_credentials.frame_review_snapshot_reader import (
    PersistedFrameReviewExecutionBinding,
    VerifiedFrameReviewExecutionInput,
    read_original_frame_review_execution_input,
)
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
    TaskSubmissionRow,
    UserRow,
)

_TASK_TYPE = "ai-review"
_OPERATION = "frame.review"
_GRAPH_VERSION = "frame-review-v1"
_ORIGINAL_DEADLINE_SECONDS = 7200
_CLAIMABLE_STATUSES = ("processing", "reconciling")

SessionFactory = Callable[[], Session]
DbClock = Callable[[], datetime]
IdFactory = Callable[[], str]
SnapshotReader = Callable[..., VerifiedFrameReviewExecutionInput | None]


@dataclass(frozen=True, slots=True)
class FrameReviewIntentPreparation:
    disposition: Literal["committed", "existing", "unresolved", "ownership_lost"]
    verified_input: VerifiedFrameReviewExecutionInput | None = None
    intent: CommittedIntent | None = None


@dataclass(frozen=True, slots=True)
class _IntentCommit:
    intent: CommittedIntent
    verified_input: VerifiedFrameReviewExecutionInput
    snapshot: dict[str, object]


@dataclass(slots=True)
class SqlAlchemyFrameReviewIntents:
    """Persist and confirm one review intent before allowing Provider I/O."""

    session_factory: SessionFactory
    db_clock: DbClock
    id_factory: IdFactory
    crypto: CredentialCrypto
    proof_authority: HmacProofAuthority
    snapshot_reader: SnapshotReader = read_original_frame_review_execution_input

    def prepare(self, claim: ClaimAuthority) -> FrameReviewIntentPreparation:
        if (
            not isinstance(claim, ClaimAuthority)
            or claim.scope.operation != _OPERATION
            or claim.local_lost_signal.is_set()
        ):
            return FrameReviewIntentPreparation("ownership_lost")
        try:
            staged, disposition = self._stage_and_commit(claim)
        except Exception:
            return FrameReviewIntentPreparation("unresolved")
        if disposition is not None:
            return FrameReviewIntentPreparation(disposition)
        if staged is None:
            return FrameReviewIntentPreparation("unresolved")
        if not self._confirm_intent(claim, staged):
            return FrameReviewIntentPreparation("unresolved")
        return FrameReviewIntentPreparation(
            "committed", staged.verified_input, staged.intent
        )

    def _stage_and_commit(
        self, claim: ClaimAuthority
    ) -> tuple[
        _IntentCommit | None,
        Literal["existing", "unresolved", "ownership_lost"] | None,
    ]:
        staged: list[_IntentCommit] = []
        try:
            return self._stage_and_commit_transaction(claim, staged)
        except Exception:
            if staged and self._confirm_intent(claim, staged[0]):
                return staged[0], None
            return None, "unresolved"

    def _stage_and_commit_transaction(
        self,
        claim: ClaimAuthority,
        committed_candidate: list[_IntentCommit],
    ) -> tuple[
        _IntentCommit | None,
        Literal["existing", "unresolved", "ownership_lost"] | None,
    ]:
        staged: _IntentCommit | None = None
        with self.session_factory() as session:
            with session.begin():
                graph = self._lock_graph(session, claim)
                if graph is None:
                    return None, "unresolved"
                task, unit, step, parent, request_data, submissions = graph
                now = self._server_now()
                if not _claim_is_current(task, claim, now):
                    return None, "ownership_lost"
                if submissions or unit.get("first_intent_at") is not None:
                    return None, "existing"
                if (
                    step.get("execution_status") != "prepared"
                    or unit.get("deadline_at") is not None
                    or unit.get("result_status") != "pending"
                    or unit.get("billing_status") != "unbilled"
                    or unit.get("debited_amount") != 0
                    or unit.get("refunded_amount") != 0
                ):
                    return None, "unresolved"
                binding = _execution_binding(task, unit, step, parent, request_data)
                if binding is None:
                    return None, "unresolved"
                verified = self._read_snapshot(session, binding)
                if (
                    verified is None
                    or verified.task_id != binding.task_id
                    or verified.unit_id != binding.unit_id
                    or verified.step_id != binding.step_id
                    or verified.submission_id != binding.submission_id
                    or verified.server_attempt_id != binding.server_attempt_id
                ):
                    return None, "unresolved"

                now = self._server_now()
                if not _claim_is_current(task, claim, now):
                    return None, "ownership_lost"
                if not _confirm_claim_fence(session, task, claim, now):
                    return None, "ownership_lost"

                try:
                    submission_id = self.id_factory()
                except Exception:
                    return None, "unresolved"
                if not isinstance(submission_id, str) or not submission_id or len(submission_id) > 36:
                    return None, "unresolved"
                snapshot_ref = _execution_snapshot_ref(binding)
                deadline = now + timedelta(seconds=_ORIGINAL_DEADLINE_SECONDS)
                intent = CommittedIntent(
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
                request_snapshot = _safe_intent_snapshot(binding, verified)
                staged = _IntentCommit(intent, verified, request_snapshot)
                committed_candidate.append(staged)
                session.execute(
                    insert(ExternalSubmissionRow.__table__).values(
                        id=submission_id,
                        step_id=binding.step_id,
                        submission_key=_submission_key(binding.unit_id, binding.step_id),
                        provider=intent.provider,
                        model_ref=intent.model_id,
                        request_digest=intent.request_digest,
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
                step_result = session.execute(
                    update(ExecutionStepRow.__table__)
                    .where(
                        ExecutionStepRow.id == binding.step_id,
                        ExecutionStepRow.billing_unit_id == binding.unit_id,
                        ExecutionStepRow.execution_status == "prepared",
                        ExecutionStepRow.input_digest == binding.request_digest,
                    )
                    .values(
                        execution_status="submitting",
                        updated_at=_database_datetime(now),
                    )
                )
                unit_result = session.execute(
                    update(BillingUnitRow.__table__)
                    .where(
                        BillingUnitRow.id == binding.unit_id,
                        BillingUnitRow.task_id == binding.task_id,
                        BillingUnitRow.operation == _OPERATION,
                        BillingUnitRow.step_graph_version == _GRAPH_VERSION,
                        BillingUnitRow.result_status == "pending",
                        BillingUnitRow.billing_status == "unbilled",
                        BillingUnitRow.debited_amount == 0,
                        BillingUnitRow.refunded_amount == 0,
                        BillingUnitRow.user_cancelled_at.is_(None),
                        BillingUnitRow.first_intent_at.is_(None),
                        BillingUnitRow.deadline_at.is_(None),
                        BillingUnitRow.input_digest == binding.request_digest,
                    )
                    .values(
                        first_intent_at=_database_datetime(now),
                        deadline_at=_database_datetime(deadline),
                        updated_at=_database_datetime(now),
                    )
                )
                if step_result.rowcount != 1 or unit_result.rowcount != 1:
                    raise RuntimeError("review intent graph changed during commit")
        return staged, None

    def _lock_graph(self, session: Session, claim: ClaimAuthority):
        user = session.execute(
            select(UserRow.id)
            .where(UserRow.id == claim.scope.user_id)
            .with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        if user is None:
            return None
        task_row = session.execute(
            select(AITaskRow.__table__)
            .where(
                AITaskRow.id == claim.scope.task_id,
                AITaskRow.user_id == claim.scope.user_id,
                AITaskRow.type == _TASK_TYPE,
            )
            .with_for_update(skip_locked=True)
        ).mappings().one_or_none()
        if task_row is None:
            return None
        task = dict(task_row)
        units = session.execute(
            select(BillingUnitRow.__table__)
            .where(BillingUnitRow.task_id == claim.scope.task_id)
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
        try:
            request_data = json.loads(task["request_data"])
            parent_id = request_data.get("submission_id")
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(request_data, dict) or not isinstance(parent_id, str):
            return None
        parent_row = session.execute(
            select(TaskSubmissionRow.__table__)
            .where(TaskSubmissionRow.id == parent_id)
            .with_for_update(skip_locked=True)
        ).mappings().one_or_none()
        if parent_row is None:
            return None
        existing = session.execute(
            select(ExternalSubmissionRow.__table__)
            .where(ExternalSubmissionRow.step_id == step["id"])
            .order_by(ExternalSubmissionRow.id)
            .with_for_update()
        ).mappings().all()
        parent = dict(parent_row)
        if not _graph_matches(task, unit, step, parent, request_data):
            return None
        return task, unit, step, parent, request_data, tuple(dict(row) for row in existing)

    def _read_snapshot(
        self, session: Session, binding: PersistedFrameReviewExecutionBinding
    ) -> VerifiedFrameReviewExecutionInput | None:
        if self.snapshot_reader is read_original_frame_review_execution_input:
            return self.snapshot_reader(
                session,
                binding,
                crypto=self.crypto,
                proof_authority=self.proof_authority,
            )
        return self.snapshot_reader(session, binding)

    def _confirm_intent(self, claim: ClaimAuthority, staged: _IntentCommit) -> bool:
        try:
            with self.session_factory() as session:
                intent = staged.intent
                unit = session.execute(
                    select(BillingUnitRow.__table__).where(
                        BillingUnitRow.id == staged.verified_input.unit_id,
                        BillingUnitRow.task_id == claim.scope.task_id,
                    )
                ).mappings().one_or_none()
                step = session.execute(
                    select(ExecutionStepRow.__table__).where(
                        ExecutionStepRow.id == intent.step_id,
                        ExecutionStepRow.billing_unit_id == staged.verified_input.unit_id,
                    )
                ).mappings().one_or_none()
                submission = session.execute(
                    select(ExternalSubmissionRow.__table__).where(
                        ExternalSubmissionRow.id == intent.external_submission_id,
                        ExternalSubmissionRow.step_id == intent.step_id,
                    )
                ).mappings().one_or_none()
                task = session.execute(
                    select(AITaskRow.__table__).where(
                        AITaskRow.id == claim.scope.task_id,
                        AITaskRow.user_id == claim.scope.user_id,
                        AITaskRow.type == _TASK_TYPE,
                    )
                ).mappings().one_or_none()
                now = self._server_now()
            return bool(
                unit is not None
                and step is not None
                and submission is not None
                and task is not None
                and step["execution_status"] == "submitting"
                and _time_matches(unit.get("first_intent_at"), intent.first_intent_at)
                and _time_matches(unit.get("deadline_at"), intent.deadline_at)
                and _time_matches(submission.get("intent_at"), intent.committed_at)
                and _time_matches(submission.get("deadline_at"), intent.deadline_at)
                and submission.get("submission_status") == "submitting"
                and submission.get("request_digest") == intent.request_digest
                and submission.get("provider") == intent.provider
                and submission.get("model_ref") == intent.model_id
                and submission.get("request_snapshot") == staged.snapshot
                and task.get("status") in _CLAIMABLE_STATUSES
                and task.get("claimed_by") == claim.worker_instance
                and task.get("claim_token") == claim.token
                and task.get("execution_generation") == claim.execution_generation
                and task.get("user_cancelled_at") is None
                and task.get("lease_until") is not None
                and _aware_utc(task["lease_until"]) > now
                and not claim.local_lost_signal.is_set()
            )
        except Exception:
            return False

    def _server_now(self) -> datetime:
        value = self.db_clock()
        if not _aware_utc_input(value):
            raise ValueError("database clock must return an aware UTC datetime")
        return _aware_utc(value)


def _graph_matches(task, unit, step, parent, request_data) -> bool:
    snapshot = step.get("input_snapshot")
    return bool(
        isinstance(snapshot, dict)
        and set(snapshot)
        == {
            "server_attempt_id",
            "prepared_payload_id",
            "protected_ref",
            "snapshot_digest",
            "manifest_snapshot_digest",
        }
        and task.get("type") == _TASK_TYPE
        and task.get("credit_cost") == 0
        and request_data.get("operation") == _OPERATION
        and request_data.get("graph_version") == _GRAPH_VERSION
        and request_data.get("step_graph_version") == _GRAPH_VERSION
        and request_data.get("via") == "registered_generation_graph"
        and request_data.get("mode") == "frame_review"
        and request_data.get("submission_id") == parent.get("id")
        and request_data.get("billing_unit_id") == unit.get("id")
        and request_data.get("request_digest") == unit.get("input_digest")
        and request_data.get("request_digest") == step.get("input_digest")
        and request_data.get("snapshot_digest") == snapshot.get("snapshot_digest")
        and request_data.get("manifest_snapshot_digest")
        == snapshot.get("manifest_snapshot_digest")
        and request_data.get("server_attempt_id") == snapshot.get("server_attempt_id")
        and request_data.get("prepared_payload_id") == snapshot.get("prepared_payload_id")
        and request_data.get("protected_ref") == snapshot.get("protected_ref")
        and unit.get("task_id") == task.get("id")
        and unit.get("item_key") == snapshot.get("server_attempt_id")
        and unit.get("operation") == _OPERATION
        and unit.get("step_graph_version") == _GRAPH_VERSION
        and unit.get("billing_status") == "unbilled"
        and unit.get("quoted_amount") >= 0
        and unit.get("debited_amount") == 0
        and unit.get("refunded_amount") == 0
        and step.get("billing_unit_id") == unit.get("id")
        and step.get("step_key") == "generate"
        and step.get("required") is True
        and step.get("step_order") == 0
        and step.get("dependencies") in ([], ())
        and parent.get("user_id") == task.get("user_id")
        and parent.get("operation") == _OPERATION
        and parent.get("request_digest") == request_data.get("request_digest")
        and parent.get("task_ids") == [task.get("id")]
        and parent.get("idempotency_key") == f"frame-review:{parent.get('id')}"
    )


def _execution_binding(task, unit, step, parent, request_data):
    snapshot = step.get("input_snapshot")
    if not isinstance(snapshot, dict):
        return None
    try:
        binding = PersistedFrameReviewExecutionBinding(
            owner_id=task["user_id"],
            task_id=task["id"],
            unit_id=unit["id"],
            step_id=step["id"],
            submission_id=parent["id"],
            raw_key=parent["idempotency_key"],
            task_message_id=task.get("message_id"),
            task_model_name=task["model_name"],
            task_model_id=request_data["model_id"],
            task_model_source=request_data["model_source"],
            task_model_owner_id=request_data["model_owner_id"],
            request_digest=request_data["request_digest"],
            rule_digest=request_data["rule_digest"],
            snapshot_digest=request_data["snapshot_digest"],
            manifest_snapshot_digest=request_data["manifest_snapshot_digest"],
            server_attempt_id=snapshot["server_attempt_id"],
            prepared_payload_id=snapshot["prepared_payload_id"],
            protected_ref=snapshot["protected_ref"],
            quoted_amount=unit["quoted_amount"],
        )
    except (KeyError, TypeError, ValueError):
        return None
    if (
        not all(isinstance(value, str) and value for value in (
            binding.owner_id, binding.task_id, binding.unit_id, binding.step_id,
            binding.submission_id, binding.raw_key, binding.task_model_name,
            binding.task_model_id, binding.task_model_source, binding.task_model_owner_id,
            binding.server_attempt_id, binding.prepared_payload_id, binding.protected_ref,
        ))
        or binding.task_model_source not in {"system", "user"}
        or binding.task_model_owner_id != ("system" if binding.task_model_source == "system" else binding.owner_id)
        or binding.raw_key != f"frame-review:{binding.submission_id}"
        or type(binding.quoted_amount) is not int
        or binding.quoted_amount < 0
    ):
        return None
    return binding


def _safe_intent_snapshot(binding, verified) -> dict[str, object]:
    return {
        "owner_id": binding.owner_id,
        "task_id": binding.task_id,
        "unit_id": binding.unit_id,
        "step_id": binding.step_id,
        "submission_id": binding.submission_id,
        "operation": _OPERATION,
        "graph_version": _GRAPH_VERSION,
        "attempt_id": binding.server_attempt_id,
        "prepared_payload_id": binding.prepared_payload_id,
        "protected_ref": binding.protected_ref,
        "request_digest": binding.request_digest,
        "rule_digest": binding.rule_digest,
        "snapshot_digest": binding.snapshot_digest,
        "manifest_snapshot_digest": binding.manifest_snapshot_digest,
        "snapshot_ref": _execution_snapshot_ref(binding),
        "credential_binding_ref": verified.snapshot.credential_binding_ref,
        "provider": verified.snapshot.model_ref.provider,
        "model_id": verified.snapshot.model_ref.model_id,
    }


def _execution_snapshot_ref(binding) -> str:
    return (
        f"frame-review-v1:{binding.prepared_payload_id}:"
        f"{binding.snapshot_digest}:{binding.manifest_snapshot_digest}"
    )


def _submission_key(unit_id: str, step_id: str) -> str:
    value = f"frame-review-generate-v1:{unit_id}:{step_id}"
    if len(value) > 255:
        raise ValueError("review step id exceeds the stable intent key limit")
    return value


def _claim_is_current(task, claim: ClaimAuthority, now: datetime) -> bool:
    lease = task.get("lease_until")
    return bool(
        task.get("id") == claim.scope.task_id
        and task.get("user_id") == claim.scope.user_id
        and task.get("type") == _TASK_TYPE
        and task.get("status") in _CLAIMABLE_STATUSES
        and task.get("claimed_by") == claim.worker_instance
        and task.get("claim_token") == claim.token
        and task.get("execution_generation") == claim.execution_generation
        and task.get("user_cancelled_at") is None
        and isinstance(lease, datetime)
        and _aware_utc(lease) > now
        and not claim.local_lost_signal.is_set()
    )


def _confirm_claim_fence(session: Session, task, claim: ClaimAuthority, now: datetime) -> bool:
    table = AITaskRow.__table__
    lease = task.get("lease_until")
    result = session.execute(
        update(table)
        .where(
            table.c.id == claim.scope.task_id,
            table.c.user_id == claim.scope.user_id,
            table.c.type == _TASK_TYPE,
            table.c.status == task.get("status"),
            table.c.status.in_(_CLAIMABLE_STATUSES),
            table.c.claimed_by == claim.worker_instance,
            table.c.claim_token == claim.token,
            table.c.execution_generation == claim.execution_generation,
            table.c.user_cancelled_at.is_(None),
            table.c.lease_until == _database_datetime(_aware_utc(lease)),
            table.c.lease_until > _database_datetime(now),
            table.c.request_data == task.get("request_data"),
        )
        .values(updated_at=table.c.updated_at)
    )
    return result.rowcount == 1


def _time_matches(value: object, expected: datetime) -> bool:
    return isinstance(value, datetime) and _aware_utc(value) == expected


def _aware_utc_input(value: object) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() == timezone.utc.utcoffset(value)
    )


def _aware_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _database_datetime(value: datetime) -> datetime:
    return _aware_utc(value).replace(tzinfo=None)
