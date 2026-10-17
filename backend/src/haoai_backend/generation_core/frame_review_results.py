"""Atomic durable result and local-continuation records for frame.review."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.records import (
    GenerationResultReceipt,
    LocalContinuationWork,
)
from haoai_backend.generation_contracts.types import ClaimAuthority, CommittedIntent, TextResult
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
    ResultEvidenceRow,
    TaskSubmissionRow,
    UserRow,
)
from haoai_backend.generation_credentials.frame_review_snapshot_reader import (
    PersistedFrameReviewExecutionBinding,
    VerifiedFrameReviewExecutionInput,
    _FrameReviewResultAckProof,
    _read_frame_review_execution_input_after_result_ack,
    read_original_frame_review_execution_input,
)
from haoai_backend.generation_core.persistence import GenerationRecordRepository
from haoai_backend.generation_core.schema import (
    GenerationResultReceiptRow,
    LocalContinuationWorkRow,
)
from haoai_backend.generation_credentials.domain import CredentialCrypto, HmacProofAuthority
from haoai_backend.generation_runtime.registry import LOCAL_CONTINUATION_POLICY

_TASK_TYPE = "ai-review"
_OPERATION = "frame.review"
_GRAPH_VERSION = "frame-review-v1"
_INLINE_TEXT_PREFIX = "frame-review-inline-text-v1:"
_MAX_TEXT_BYTES = 2 * 1024 * 1024
_CONTINUATION_PHASE_ORDER = (
    "delivery_pending",
    "fee_pending",
    "finalize_pending",
)
_ORIGINAL_DEADLINE_SECONDS = 7200
_TERMINAL_WORK_PHASES = frozenset(
    {
        "done",
        "stopped_cancelled",
        "stopped_insufficient",
        "stopped_delivery_failed",
        "stopped_late_generation",
    }
)

SessionFactory = Callable[[], Session]
DbClock = Callable[[], datetime]
IdFactory = Callable[[], str]
SnapshotReader = Callable[..., VerifiedFrameReviewExecutionInput | None]
PersistResult = Literal["pending", "ownership_lost"] | GenerationResultReceipt


@dataclass(frozen=True, slots=True)
class _LockedReviewGraph:
    task: dict[str, object]
    unit: dict[str, object]
    step: dict[str, object]
    parent: dict[str, object]
    submission: dict[str, object]
    request_data: dict[str, object]


@dataclass(frozen=True, slots=True)
class _StagedResult:
    receipt: GenerationResultReceipt
    work: LocalContinuationWork
    evidence_key: str
    encoded_text: str
    submission_snapshot: dict[str, object]


@dataclass(slots=True)
class SqlAlchemyFrameReviewResults:
    """Validate a provider result and commit evidence, receipt, Unit and Work together."""

    session_factory: SessionFactory
    db_clock: DbClock
    id_factory: IdFactory
    crypto: CredentialCrypto
    proof_authority: HmacProofAuthority
    snapshot_reader: SnapshotReader = read_original_frame_review_execution_input

    def persist_success(
        self,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified: VerifiedFrameReviewExecutionInput,
        result: TextResult,
    ) -> PersistResult:
        validated = _validate_inputs(claim, intent, verified, result)
        if validated is None:
            return "pending"
        digest, encoded = validated
        staged: _StagedResult | None = None
        outcome: Literal["pending", "ownership_lost"] = "pending"
        try:
            with self.session_factory() as session:
                with session.begin():
                    graph = _lock_graph(session, claim, intent, verified)
                    now = self._read_clock()
                    if graph is None or now is None:
                        return "pending"
                    if not _claim_is_current(graph.task, claim, now):
                        return "ownership_lost"
                    if graph.task.get("credit_cost") != 0:
                        return "pending"

                    if _has_existing_result(session, graph, intent):
                        reopened = self._read_snapshot(session, _binding(graph))
                        if reopened is None or reopened != verified:
                            return "pending"
                        return _read_existing_result(
                            session, graph, claim, intent, verified, digest, encoded
                        ) or "pending"
                    if _has_partial_result(session, graph, intent):
                        return "pending"
                    if not _intent_matches(graph, claim, intent, verified, now):
                        return "pending"

                    reopened = self._read_snapshot(session, _binding(graph))
                    if reopened is None or reopened != verified:
                        return "pending"
                    now = self._read_clock()
                    if (
                        now is None
                        or not _claim_is_current(graph.task, claim, now)
                        or not _confirm_claim_fence(session, graph.task, claim, now)
                    ):
                        return "ownership_lost"

                    result_ref, evidence_id, work_id = self._new_ids()
                    if result_ref is None or evidence_id is None or work_id is None:
                        return "pending"
                    deadline = _as_utc(intent.deadline_at)
                    timely = now < deadline
                    late_disposition = None if timely else "stopped_late_generation"
                    receipt = GenerationResultReceipt(
                        result_ref=result_ref,
                        owner_id=claim.scope.user_id,
                        task_id=claim.scope.task_id,
                        unit_id=verified.unit_id,
                        operation=_OPERATION,
                        step_id=intent.step_id,
                        submission_id=intent.external_submission_id,
                        evidence_id=evidence_id,
                        selected_attempt_id=verified.server_attempt_id,
                        recoverable_locator=encoded,
                        content_digest=digest,
                        validation_version="frame-review-text-v1",
                        validated_at=now,
                        generation_fulfilled_at=now,
                        late_disposition=late_disposition,
                        policy_stop_disposition=None,
                        automatic_application_allowed=timely,
                    )
                    work = LocalContinuationWork(
                        work_id=work_id,
                        owner_id=claim.scope.user_id,
                        task_id=claim.scope.task_id,
                        unit_id=verified.unit_id,
                        operation=_OPERATION,
                        policy_version=LOCAL_CONTINUATION_POLICY,
                        result_ref=result_ref,
                        content_digest=digest,
                        generation_fulfilled_at=now,
                        original_first_intent_at=_as_utc(intent.first_intent_at),
                        original_deadline_at=deadline,
                        phase_order=_CONTINUATION_PHASE_ORDER,
                        phase="delivery_pending",
                        phase_version=0,
                        fee_decision_ref=None,
                        application_ref=None,
                        terminal_reason=(
                            None
                            if timely
                            else "original_generation_deadline_elapsed"
                        ),
                        last_execution_generation=claim.execution_generation,
                    )
                    submission_snapshot = _intent_snapshot(graph, intent, verified)
                    staged = _StagedResult(
                        receipt=receipt,
                        work=work,
                        evidence_key=_evidence_key(intent),
                        encoded_text=encoded,
                        submission_snapshot=submission_snapshot,
                    )

                    session.execute(
                        insert(ResultEvidenceRow.__table__).values(
                            id=evidence_id,
                            step_id=intent.step_id,
                            submission_id=intent.external_submission_id,
                            evidence_key=staged.evidence_key,
                            source="provider",
                            evidence_kind="provider_text",
                            payload_digest=digest,
                            result_location=encoded,
                            result_digest=digest,
                            received_at=_db_time(now),
                            validity="valid",
                            application_status="pending",
                            applied_at=None,
                        )
                    )
                    session.flush()
                    if not self._fulfill_unit(session, graph, claim, intent, now):
                        outcome = "ownership_lost"
                        raise _WriteConflict
                    if not self._observe_step(session, graph, intent, now):
                        outcome = "pending"
                        raise _WriteConflict
                    if not self._complete_submission(
                        session, graph, intent, submission_snapshot, now
                    ):
                        outcome = "pending"
                        raise _WriteConflict

                    records = GenerationRecordRepository(session)
                    if records.stage_record(receipt).status != "created":
                        outcome = "pending"
                        raise _WriteConflict
                    if records.stage_record(work).status != "created":
                        outcome = "pending"
                        raise _WriteConflict
                    if not timely:
                        late_work = replace(
                            work,
                            phase="stopped_late_generation",
                            phase_version=1,
                            terminal_reason="original_generation_deadline_elapsed",
                        )
                        if (
                            records.advance_continuation_work(
                                late_work,
                                expected_phase_version=0,
                            ).status
                            != "updated"
                        ):
                            outcome = "pending"
                            raise _WriteConflict
        except _WriteConflict:
            return outcome
        except Exception:
            # Resolve a lost commit acknowledgement without submitting again.
            pass
        if staged is None:
            return outcome
        return self._confirm_persisted(
            claim, intent, verified, digest, encoded, staged
        )

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

    def _new_ids(self) -> tuple[str | None, str | None, str | None]:
        values: list[str | None] = []
        for _ in range(3):
            try:
                value = self.id_factory()
            except Exception:
                return None, None, None
            if not isinstance(value, str) or not value or len(value) > 36:
                return None, None, None
            values.append(value)
        if len(set(values)) != 3:
            return None, None, None
        return values[0], values[1], values[2]

    def _read_clock(self) -> datetime | None:
        try:
            value = self.db_clock()
        except Exception:
            return None
        if not _is_aware_utc(value):
            return None
        return _as_utc(value)

    def _fulfill_unit(self, session, graph, claim, intent, now) -> bool:
        table = BillingUnitRow.__table__
        result = session.execute(
            update(table)
            .where(
                table.c.id == graph.unit["id"],
                table.c.task_id == claim.scope.task_id,
                table.c.operation == _OPERATION,
                table.c.step_graph_version == _GRAPH_VERSION,
                table.c.input_digest == intent.request_digest,
                table.c.result_status == "pending",
                table.c.billing_status == "unbilled",
                table.c.debited_amount == 0,
                table.c.refunded_amount == 0,
                table.c.user_cancelled_at.is_(None),
                table.c.success_confirmed_at.is_(None),
                table.c.first_intent_at == _db_time(intent.first_intent_at),
                table.c.deadline_at == _db_time(intent.deadline_at),
            )
            .values(
                result_status="succeeded",
                success_confirmed_at=_db_time(now),
                updated_at=_db_time(now),
            )
        )
        return result.rowcount == 1

    def _observe_step(self, session, graph, intent, now) -> bool:
        table = ExecutionStepRow.__table__
        result = session.execute(
            update(table)
            .where(
                table.c.id == intent.step_id,
                table.c.billing_unit_id == graph.unit["id"],
                table.c.step_key == "generate",
                table.c.required.is_(True),
                table.c.step_order == 0,
                table.c.input_digest == intent.request_digest,
                table.c.execution_status == "submitting",
            )
            .values(execution_status="result_observed", updated_at=_db_time(now))
        )
        return result.rowcount == 1

    def _complete_submission(
        self, session, graph, intent, expected_snapshot, now
    ) -> bool:
        table = ExternalSubmissionRow.__table__
        if graph.submission.get("request_snapshot") != expected_snapshot:
            return False
        result = session.execute(
            update(table)
            .where(
                table.c.id == intent.external_submission_id,
                table.c.step_id == intent.step_id,
                table.c.provider == intent.provider,
                table.c.model_ref == intent.model_id,
                table.c.request_digest == intent.request_digest,
                table.c.submission_status == "submitting",
                table.c.submitted_at.is_(None),
                table.c.external_task_id.is_(None),
                table.c.intent_at == _db_time(intent.first_intent_at),
                table.c.deadline_at == _db_time(intent.deadline_at),
            )
            .values(
                submission_status="completed",
                submitted_at=_db_time(now),
                updated_at=_db_time(now),
            )
        )
        return result.rowcount == 1

    def _confirm_persisted(
        self, claim, intent, verified, digest, encoded, staged
    ) -> PersistResult:
        try:
            with self.session_factory() as session:
                graph = _lock_graph(session, claim, intent, verified)
                if graph is None:
                    return "pending"
                persisted = _read_existing_result(
                    session,
                    graph,
                    claim,
                    intent,
                    verified,
                    digest,
                    encoded,
                    staged,
                )
                if not isinstance(persisted, GenerationResultReceipt):
                    return "pending"
                result_proof = _FrameReviewResultAckProof(
                    owner_id=persisted.owner_id,
                    task_id=persisted.task_id,
                    unit_id=persisted.unit_id,
                    step_id=persisted.step_id,
                    parent_submission_id=verified.submission_id,
                    external_submission_id=persisted.submission_id,
                    result_ref=persisted.result_ref,
                    evidence_id=persisted.evidence_id,
                    work_id=staged.work.work_id,
                    content_digest=persisted.content_digest,
                )
                reopened = _read_frame_review_execution_input_after_result_ack(
                    session,
                    _binding(graph),
                    result_proof,
                    crypto=self.crypto,
                    proof_authority=self.proof_authority,
                )
                if reopened is None or reopened != verified:
                    return "pending"
                return persisted
        except Exception:
            return "pending"


class _WriteConflict(Exception):
    pass


def _validate_inputs(claim, intent, verified, result):
    if (
        not isinstance(claim, ClaimAuthority)
        or claim.scope.operation != _OPERATION
        or claim.local_lost_signal.is_set()
        or type(intent) is not CommittedIntent
        or type(verified) is not VerifiedFrameReviewExecutionInput
        or type(result) is not TextResult
        or verified.task_id != claim.scope.task_id
        or verified.step_id != intent.step_id
        or verified.server_attempt_id != intent.attempt_id
        or not isinstance(result.text, str)
    ):
        return None
    try:
        encoded_text = result.text.encode("utf-8")
    except UnicodeError:
        return None
    if (
        not result.text.strip()
        or "\x00" in result.text
        or len(encoded_text) > _MAX_TEXT_BYTES
    ):
        return None
    digest = hashlib.sha256(encoded_text).hexdigest()
    return digest, _encode_inline_text(encoded_text)


def _lock_graph(session, claim, intent, verified):
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
    except (TypeError, ValueError, AttributeError, json.JSONDecodeError):
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
    submission_row = session.execute(
        select(ExternalSubmissionRow.__table__)
        .where(
            ExternalSubmissionRow.id == intent.external_submission_id,
            ExternalSubmissionRow.step_id == step["id"],
        )
        .with_for_update(skip_locked=True)
    ).mappings().one_or_none()
    if submission_row is None:
        return None
    graph = _LockedReviewGraph(
        task=task,
        unit=unit,
        step=step,
        parent=dict(parent_row),
        submission=dict(submission_row),
        request_data=dict(request_data),
    )
    if not _graph_identity_matches(graph, claim, intent, verified):
        return None
    return graph


def _graph_identity_matches(graph, claim, intent, verified) -> bool:
    task, unit, step, parent, submission, request = (
        graph.task,
        graph.unit,
        graph.step,
        graph.parent,
        graph.submission,
        graph.request_data,
    )
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
        and task.get("user_id") == claim.scope.user_id
        and task.get("id") == claim.scope.task_id
        and request.get("operation") == _OPERATION
        and request.get("graph_version") == _GRAPH_VERSION
        and request.get("step_graph_version") == _GRAPH_VERSION
        and request.get("submission_id") == parent.get("id")
        and verified.submission_id == parent.get("id")
        and request.get("billing_unit_id") == unit.get("id")
        and request.get("request_digest") == intent.request_digest
        and request.get("request_digest") == unit.get("input_digest")
        and request.get("request_digest") == step.get("input_digest")
        and request.get("server_attempt_id") == verified.server_attempt_id
        and request.get("prepared_payload_id") == verified.prepared_payload_id
        and request.get("snapshot_digest") == snapshot.get("snapshot_digest")
        and request.get("manifest_snapshot_digest") == snapshot.get("manifest_snapshot_digest")
        and unit.get("id") == verified.unit_id
        and unit.get("task_id") == task.get("id")
        and unit.get("operation") == _OPERATION
        and unit.get("step_graph_version") == _GRAPH_VERSION
        and unit.get("item_key") == verified.server_attempt_id
        and step.get("id") == verified.step_id
        and step.get("billing_unit_id") == unit.get("id")
        and step.get("step_key") == "generate"
        and step.get("required") is True
        and step.get("step_order") == 0
        and step.get("dependencies") in ([], ())
        and parent.get("user_id") == claim.scope.user_id
        and parent.get("operation") == _OPERATION
        and parent.get("request_digest") == intent.request_digest
        and parent.get("task_ids") == [task.get("id")]
        and submission.get("id") == intent.external_submission_id
        and submission.get("step_id") == intent.step_id
        and submission.get("provider") == intent.provider
        and submission.get("model_ref") == intent.model_id
        and submission.get("request_digest") == intent.request_digest
    )


def _binding(graph: _LockedReviewGraph) -> PersistedFrameReviewExecutionBinding:
    task, unit, step, parent, request, snapshot = (
        graph.task,
        graph.unit,
        graph.step,
        graph.parent,
        graph.request_data,
        graph.step["input_snapshot"],
    )
    return PersistedFrameReviewExecutionBinding(
        owner_id=task["user_id"],
        task_id=task["id"],
        unit_id=unit["id"],
        step_id=step["id"],
        submission_id=parent["id"],
        raw_key=parent["idempotency_key"],
        task_message_id=task.get("message_id"),
        task_model_name=task["model_name"],
        task_model_id=request["model_id"],
        task_model_source=request["model_source"],
        task_model_owner_id=request["model_owner_id"],
        request_digest=request["request_digest"],
        rule_digest=request["rule_digest"],
        snapshot_digest=request["snapshot_digest"],
        manifest_snapshot_digest=request["manifest_snapshot_digest"],
        server_attempt_id=snapshot["server_attempt_id"],
        prepared_payload_id=snapshot["prepared_payload_id"],
        protected_ref=snapshot["protected_ref"],
        quoted_amount=unit["quoted_amount"],
    )


def _expected_intent_snapshot(
    graph: _LockedReviewGraph,
    intent: CommittedIntent,
    verified: VerifiedFrameReviewExecutionInput,
) -> dict[str, object] | None:
    task, unit, step, request = (
        graph.task,
        graph.unit,
        graph.step,
        graph.request_data,
    )
    stored_step_snapshot = step.get("input_snapshot")
    if not isinstance(stored_step_snapshot, dict):
        return None
    expected_snapshot_ref = (
        f"{_GRAPH_VERSION}:{verified.prepared_payload_id}:"
        f"{request.get('snapshot_digest')}:{request.get('manifest_snapshot_digest')}"
    )
    if intent.snapshot_ref != expected_snapshot_ref:
        return None
    return {
        "owner_id": task.get("user_id"),
        "task_id": task.get("id"),
        "unit_id": unit.get("id"),
        "step_id": step.get("id"),
        "submission_id": graph.parent.get("id"),
        "operation": _OPERATION,
        "graph_version": _GRAPH_VERSION,
        "attempt_id": stored_step_snapshot.get("server_attempt_id"),
        "prepared_payload_id": stored_step_snapshot.get("prepared_payload_id"),
        "protected_ref": stored_step_snapshot.get("protected_ref"),
        "request_digest": request.get("request_digest"),
        "rule_digest": request.get("rule_digest"),
        "snapshot_digest": request.get("snapshot_digest"),
        "manifest_snapshot_digest": request.get("manifest_snapshot_digest"),
        "snapshot_ref": intent.snapshot_ref,
        "credential_binding_ref": verified.snapshot.credential_binding_ref,
        "provider": verified.snapshot.model_ref.provider,
        "model_id": verified.snapshot.model_ref.model_id,
    }


def _original_intent_matches(
    graph: _LockedReviewGraph,
    claim: ClaimAuthority,
    intent: CommittedIntent,
    verified: VerifiedFrameReviewExecutionInput,
) -> bool:
    unit, step, submission = graph.unit, graph.step, graph.submission
    request_snapshot = submission.get("request_snapshot")
    try:
        original_deadline = _as_utc(intent.first_intent_at) + timedelta(
            seconds=_ORIGINAL_DEADLINE_SECONDS
        )
        expected_snapshot = _expected_intent_snapshot(graph, intent, verified)
    except (AttributeError, TypeError, ValueError):
        return False
    return bool(
        intent.step_id == step.get("id")
        and intent.attempt_id == verified.server_attempt_id
        and intent.external_submission_id == submission.get("id")
        and verified.task_id == claim.scope.task_id
        and verified.unit_id == unit.get("id")
        and verified.step_id == step.get("id")
        and verified.submission_id == graph.parent.get("id")
        and intent.provider == verified.snapshot.model_ref.provider
        and intent.model_id == verified.snapshot.model_ref.model_id
        and intent.request_digest == graph.request_data.get("request_digest")
        and intent.committed_at == intent.first_intent_at
        and _as_utc(intent.deadline_at) == original_deadline
        and intent.provider_idempotency_key is None
        and graph.task.get("id") == claim.scope.task_id
        and graph.task.get("user_id") == claim.scope.user_id
        and graph.task.get("type") == _TASK_TYPE
        and unit.get("task_id") == claim.scope.task_id
        and unit.get("operation") == _OPERATION
        and unit.get("step_graph_version") == _GRAPH_VERSION
        and step.get("billing_unit_id") == unit.get("id")
        and step.get("step_key") == "generate"
        and submission.get("step_id") == step.get("id")
        and submission.get("submission_key")
        == f"frame-review-generate-v1:{unit.get('id')}:{step.get('id')}"
        and submission.get("provider") == intent.provider
        and submission.get("model_ref") == intent.model_id
        and submission.get("request_digest") == intent.request_digest
        and isinstance(request_snapshot, dict)
        and request_snapshot == expected_snapshot
        and _same_time(unit.get("first_intent_at"), intent.first_intent_at)
        and _same_time(unit.get("deadline_at"), intent.deadline_at)
        and step.get("execution_status")
        in {"submitting", "result_observed", "applied"}
        and _same_time(submission.get("intent_at"), intent.first_intent_at)
        and _same_time(submission.get("deadline_at"), intent.deadline_at)
        and submission.get("submission_status") in {"submitting", "completed"}
        and submission.get("external_task_id") is None
        and (
            submission.get("submission_status") == "completed"
            or (
                submission.get("submitted_at") is None
                and submission.get("external_task_id") is None
            )
        )
    )


def _intent_matches(
    graph: _LockedReviewGraph,
    claim: ClaimAuthority,
    intent: CommittedIntent,
    verified: VerifiedFrameReviewExecutionInput,
    now: datetime,
) -> bool:
    unit, step, submission = graph.unit, graph.step, graph.submission
    return bool(
        _claim_is_current(graph.task, claim, now)
        and graph.task.get("status") in {"processing", "reconciling"}
        and _original_intent_matches(graph, claim, intent, verified)
        and unit.get("result_status") == "pending"
        and unit.get("billing_status") == "unbilled"
        and unit.get("debited_amount") == 0
        and unit.get("refunded_amount") == 0
        and unit.get("user_cancelled_at") is None
        and step.get("execution_status") == "submitting"
        and submission.get("submission_status") == "submitting"
        and submission.get("submitted_at") is None
        and submission.get("external_task_id") is None
    )


def _has_existing_result(
    session: Session, graph: _LockedReviewGraph, intent: CommittedIntent
) -> bool:
    return (
        session.execute(
            select(GenerationResultReceiptRow.result_ref).where(
                GenerationResultReceiptRow.submission_id == intent.external_submission_id
            )
        ).first()
        is not None
    )


def _read_existing_result(
    session,
    graph,
    claim,
    intent,
    verified,
    digest,
    encoded,
    expected_staged: _StagedResult | None = None,
) -> PersistResult | None:
    rows = session.execute(
        select(GenerationResultReceiptRow.__table__).where(
            GenerationResultReceiptRow.submission_id == intent.external_submission_id,
        )
    ).mappings().all()
    if not rows:
        return None
    if len(rows) != 1:
        return "pending"
    receipt_row = rows[0]
    if not _original_intent_matches(graph, claim, intent, verified):
        return "pending"
    evidence = session.execute(
        select(ResultEvidenceRow.__table__).where(
            ResultEvidenceRow.id == receipt_row["evidence_id"],
        )
    ).mappings().one_or_none()
    works = session.execute(
        select(LocalContinuationWorkRow.__table__).where(
            LocalContinuationWorkRow.task_id == graph.task["id"],
            LocalContinuationWorkRow.unit_id == graph.unit["id"],
        )
    ).mappings().all()
    if len(works) != 1:
        return "pending"
    work = works[0]
    receipt = _receipt_from_row(receipt_row)
    fulfilled_at = receipt_row.get("generation_fulfilled_at")
    timely = (
        isinstance(fulfilled_at, datetime)
        and _as_utc(fulfilled_at) < _as_utc(intent.deadline_at)
    )
    if (
        evidence is None
        or not isinstance(fulfilled_at, datetime)
        or not _receipt_matches_result(
            graph, claim, intent, verified, receipt_row, digest, encoded, fulfilled_at, timely
        )
        or not _evidence_matches_result(
            intent, evidence, digest, encoded, fulfilled_at
        )
        or not _work_matches_result(
            graph, claim, intent, verified, receipt_row, work, digest, fulfilled_at, timely
        )
        or (
            expected_staged is not None
            and not _staged_result_matches(
                expected_staged, receipt, evidence, work
            )
        )
        or _decode_payload_text(evidence.get("result_location"), digest) is None
    ):
        return "pending"
    return receipt


def _receipt_matches_result(
    graph: _LockedReviewGraph,
    claim: ClaimAuthority,
    intent: CommittedIntent,
    verified: VerifiedFrameReviewExecutionInput,
    row: Mapping[str, object],
    digest: str,
    encoded: str,
    fulfilled_at: datetime,
    timely: bool,
) -> bool:
    return all(
        (
            graph.task.get("id") == row.get("task_id"),
            graph.task.get("user_id") == row.get("owner_id"),
            graph.unit.get("id") == row.get("unit_id"),
            row.get("owner_id") == claim.scope.user_id,
            row.get("task_id") == claim.scope.task_id,
            row.get("unit_id") == verified.unit_id,
            row.get("operation") == _OPERATION,
            row.get("step_id") == intent.step_id,
            row.get("submission_id") == intent.external_submission_id,
            row.get("selected_attempt_id") == verified.server_attempt_id,
            row.get("content_digest") == digest,
            row.get("recoverable_locator") == encoded,
            row.get("validation_version") == "frame-review-text-v1",
            row.get("policy_stop_disposition") is None,
            row.get("automatic_application_allowed") is timely,
            row.get("late_disposition")
            == (None if timely else "stopped_late_generation"),
            _same_time(row.get("validated_at"), fulfilled_at),
            _same_time(row.get("generation_fulfilled_at"), fulfilled_at),
            _same_time(graph.unit.get("success_confirmed_at"), fulfilled_at),
            _same_time(graph.submission.get("submitted_at"), fulfilled_at),
        )
    )


def _evidence_matches_result(
    intent: CommittedIntent,
    evidence: Mapping[str, object],
    digest: str,
    encoded: str,
    fulfilled_at: datetime,
) -> bool:
    return all(
        (
            evidence.get("step_id") == intent.step_id,
            evidence.get("submission_id") == intent.external_submission_id,
            evidence.get("evidence_key") == _evidence_key(intent),
            evidence.get("source") == "provider",
            evidence.get("evidence_kind") == "provider_text",
            evidence.get("payload_digest") == digest,
            evidence.get("result_location") == encoded,
            evidence.get("result_digest") == digest,
            evidence.get("validity") == "valid",
            _same_time(evidence.get("received_at"), fulfilled_at),
        )
    )


def _work_matches_result(
    graph: _LockedReviewGraph,
    claim: ClaimAuthority,
    intent: CommittedIntent,
    verified: VerifiedFrameReviewExecutionInput,
    receipt: Mapping[str, object],
    work: Mapping[str, object],
    digest: str,
    fulfilled_at: datetime,
    timely: bool,
) -> bool:
    return all(
        (
            graph.submission.get("submission_status") == "completed",
            graph.step.get("execution_status") in {"result_observed", "applied"},
            graph.unit.get("result_status") == "succeeded",
            graph.unit.get("billing_status") in {"unbilled", "charged", "no_charge"},
            type(graph.unit.get("debited_amount")) is int,
            type(graph.unit.get("refunded_amount")) is int,
            graph.unit.get("debited_amount") >= 0,
            graph.unit.get("refunded_amount") >= 0,
            work.get("owner_id") == claim.scope.user_id,
            work.get("task_id") == claim.scope.task_id,
            work.get("unit_id") == verified.unit_id,
            work.get("operation") == _OPERATION,
            work.get("policy_version") == LOCAL_CONTINUATION_POLICY,
            work.get("result_ref") == receipt.get("result_ref"),
            work.get("content_digest") == digest,
            _same_time(work.get("generation_fulfilled_at"), fulfilled_at),
            _same_time(work.get("original_first_intent_at"), intent.first_intent_at),
            _same_time(work.get("original_deadline_at"), intent.deadline_at),
            work.get("phase_order") == list(_CONTINUATION_PHASE_ORDER),
            work.get("phase") in set(_CONTINUATION_PHASE_ORDER) | _TERMINAL_WORK_PHASES,
            type(work.get("phase_version")) is int,
            work.get("phase_version") >= 0,
            timely or work.get("phase") == "stopped_late_generation",
        )
    )


def _staged_result_matches(
    staged: _StagedResult,
    receipt: GenerationResultReceipt,
    evidence: Mapping[str, object],
    work: Mapping[str, object],
) -> bool:
    return all(
        (
            receipt == staged.receipt,
            evidence.get("id") == staged.receipt.evidence_id,
            evidence.get("evidence_key") == staged.evidence_key,
            evidence.get("result_location") == staged.encoded_text,
            work.get("work_id") == staged.work.work_id,
        )
    )


def _has_partial_result(session, graph, intent) -> bool:
    evidence = session.execute(
        select(ResultEvidenceRow.id).where(
            ResultEvidenceRow.evidence_key == _evidence_key(intent)
        )
    ).scalar_one_or_none()
    work = session.execute(
        select(LocalContinuationWorkRow.work_id).where(
            LocalContinuationWorkRow.task_id == graph.task["id"],
            LocalContinuationWorkRow.unit_id == graph.unit["id"],
            LocalContinuationWorkRow.policy_version == LOCAL_CONTINUATION_POLICY,
        )
    ).scalar_one_or_none()
    return evidence is not None or work is not None


def _receipt_from_row(row) -> GenerationResultReceipt:
    return GenerationResultReceipt(
        result_ref=row["result_ref"],
        owner_id=row["owner_id"],
        task_id=row["task_id"],
        unit_id=row["unit_id"],
        operation=row["operation"],
        step_id=row["step_id"],
        submission_id=row["submission_id"],
        evidence_id=row["evidence_id"],
        selected_attempt_id=row["selected_attempt_id"],
        recoverable_locator=row["recoverable_locator"],
        content_digest=row["content_digest"],
        validation_version=row["validation_version"],
        validated_at=_as_utc(row["validated_at"]),
        generation_fulfilled_at=_as_utc(row["generation_fulfilled_at"]),
        late_disposition=row["late_disposition"],
        policy_stop_disposition=row["policy_stop_disposition"],
        automatic_application_allowed=row["automatic_application_allowed"],
    )


def _evidence_key(intent: CommittedIntent) -> str:
    return f"frame-review-result-v1:{intent.external_submission_id}"


def _encode_inline_text(content: bytes) -> str:
    encoded = base64.urlsafe_b64encode(content).decode("ascii")
    return _INLINE_TEXT_PREFIX + encoded


def decode_inline_text_result(locator: object, expected_digest: object) -> str | None:
    if (
        not isinstance(locator, str)
        or not locator.startswith(_INLINE_TEXT_PREFIX)
        or not isinstance(expected_digest, str)
        or len(expected_digest) != 64
    ):
        return None
    encoded = locator[len(_INLINE_TEXT_PREFIX) :]
    try:
        raw = base64.b64decode(encoded.encode("ascii"), altchars=b"-_", validate=True)
        text = raw.decode("utf-8")
    except (UnicodeError, ValueError):
        return None
    return text if hashlib.sha256(raw).hexdigest() == expected_digest else None


def _intent_snapshot(
    graph: _LockedReviewGraph,
    intent: CommittedIntent,
    verified: VerifiedFrameReviewExecutionInput,
) -> dict[str, object]:
    snapshot = _expected_intent_snapshot(graph, intent, verified)
    return snapshot if snapshot is not None else {}


def _confirm_claim_fence(session, task, claim, now) -> bool:
    table = AITaskRow.__table__
    lease = task.get("lease_until")
    if not isinstance(lease, datetime):
        return False
    changed = session.execute(
        update(table)
        .where(
            table.c.id == claim.scope.task_id,
            table.c.user_id == claim.scope.user_id,
            table.c.type == _TASK_TYPE,
            table.c.status == task.get("status"),
            table.c.status.in_(("processing", "reconciling")),
            table.c.claimed_by == claim.worker_instance,
            table.c.claim_token == claim.token,
            table.c.execution_generation == claim.execution_generation,
            table.c.user_cancelled_at.is_(None),
            table.c.lease_until == _db_time(_as_utc(lease)),
            table.c.lease_until > _db_time(now),
            table.c.request_data == task.get("request_data"),
        )
        .values(updated_at=table.c.updated_at)
    )
    return changed.rowcount == 1


def _decode_payload_text(locator, digest):
    return decode_inline_text_result(locator, digest)


def _claim_is_current(task, claim, now) -> bool:
    lease = task.get("lease_until")
    return bool(
        task.get("id") == claim.scope.task_id
        and task.get("user_id") == claim.scope.user_id
        and task.get("type") == _TASK_TYPE
        and task.get("status") in {"processing", "reconciling"}
        and task.get("claimed_by") == claim.worker_instance
        and task.get("claim_token") == claim.token
        and task.get("execution_generation") == claim.execution_generation
        and task.get("user_cancelled_at") is None
        and isinstance(lease, datetime)
        and _as_utc(lease) > _as_utc(now)
        and not claim.local_lost_signal.is_set()
    )


def _same_time(value, expected) -> bool:
    return isinstance(value, datetime) and _as_utc(value) == _as_utc(expected)


def _is_aware_utc(value) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() == timezone.utc.utcoffset(value)
    )


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _db_time(value: datetime) -> datetime:
    return _as_utc(value).replace(tzinfo=None)
