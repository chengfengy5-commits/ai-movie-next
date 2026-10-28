"""Commit verified HaoAI Group video results as canonical owner receipts."""

from __future__ import annotations

import json
import hashlib
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Event
from typing import Callable, Literal
from urllib.parse import quote, urlsplit

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from haoai_backend.chat_data.tables import chat_messages
from haoai_backend.generation_contracts.records import GenerationResultReceipt
from haoai_backend.generation_contracts.types import (
    ClaimAuthority,
    CommittedIntent,
    OwnerTaskScope,
    ProviderObservation,
    StoredMedia,
)
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
    ResultEvidenceRow,
)
from haoai_backend.generation_core.persistence import GenerationRecordRepository
from haoai_backend.generation_core.schema import (
    ExecutionAttemptGroupRow,
    GenerationResultReceiptRow,
    LocalContinuationWorkRow,
    PolicyStopEventRow,
    PollReservationRow,
)
from haoai_backend.generation_core.haoai_group.evidence import (
    verified_group_observation_fact,
)
from haoai_backend.generation_core.haoai_group.claims import (
    _has_signed_failed_observation,
)
from haoai_backend.generation_core.haoai_group.intents import (
    _known_id_shape_is_valid,
    _safe_provider_id,
    haoai_group_submission_key,
)
from haoai_backend.generation_core.haoai_group.persistence import (
    LockedHaoaiGroupGraph,
    lock_haoai_group_graph,
)
from haoai_backend.generation_core.haoai_group.settlement import (
    read_verified_group_fee,
    settle_pending_policy_stop_event,
    verify_policy_stop_event,
)
from haoai_backend.generation_core.haoai_group.polls import (
    HaoaiGroupPollGrant,
    HaoaiGroupPollOutcome,
    HaoaiGroupPollReplayGrant,
    observation_body_digest,
)
from haoai_backend.generation_credentials.domain import HmacProofAuthority
from haoai_backend.generation_runtime.registry import HAOAI_ATTEMPT_GROUP
from haoai_backend.generation_credentials.haoai_group_execution_snapshot import (
    VerifiedHaoaiGroupExecutionInput,
)
from haoai_backend.media_storage.owned_write_values import (
    OwnedObjectPlan,
    OwnedObjectVerification,
)
from haoai_backend.media_uploads.domain import MAX_VIDEO_BYTES

SessionFactory = Callable[[], Session]
DbClock = Callable[[], datetime]
IdFactory = Callable[[], str]

_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_TASK_TYPE = "video-single"
_OPERATION = "video.single"
_RESULT_KEY_PREFIX = "haoai-group-result-v1:"
_LATE_RESULT_KEY_PREFIX = "haoai-group-late-result-v1:"
_RESULT_KIND = "provider_video"
_LATE_RESULT_KIND = "haoai_group_late_video_v1"
_VALIDATION_VERSION = "haoai-group-owned-video-v1"
_LATE_VALIDATION_VERSION = "haoai-group-late-owned-video-v1"
_DUE_EVIDENCE_KIND = "haoai_group_due_v1"
_DUE_EVIDENCE_KEY = "haoai-group-due-v1:"
_DUE_PURPOSE = "haoai-group-original-deadline-v1"
_RECONCILING_RESULT = json.dumps(
    {"message": "视频任务策略处理中", "status": "reconciling"},
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
)
_PROVIDER_FAILED_RESULT = json.dumps(
    {
        "code": "provider_failed",
        "message": "视频服务报告本次任务失败",
        "provider_status": "failed",
        "status": "failed",
    },
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
)
_DUE_RESULT = json.dumps(
    {
        "code": "result_unconfirmed",
        "message": "视频生成结果未能在期限内确认",
        "status": "failed",
    },
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
)
_VIDEO_MEDIA_TYPES = frozenset(
    {
        "video/mp4",
        "video/webm",
        "video/quicktime",
        "video/x-m4v",
        "video/x-msvideo",
        "video/x-matroska",
        "video/mpeg",
    }
)


@dataclass(frozen=True, slots=True)
class HaoaiGroupResultOutcome:
    disposition: Literal["completed", "pending", "ownership_lost"]
    receipt: GenerationResultReceipt | None = None


@dataclass(slots=True)
class SqlAlchemyHaoaiGroupResults:
    """Select only a verified owned object for the active immutable Group step."""

    session_factory: SessionFactory
    db_clock: DbClock
    proof_authority: HmacProofAuthority | None
    id_factory: IdFactory = field(default_factory=lambda: _new_uuid_id)

    def settle_known_failure(self, scope: OwnerTaskScope) -> str:
        """Settle one accepted, signed provider failure without a deadline wait."""

        if (
            type(scope) is not OwnerTaskScope
            or scope.operation != _OPERATION
            or type(self.proof_authority) is not HmacProofAuthority
        ):
            return "not_selected"

        from haoai_backend.generation_core.haoai_group.cancellation import (
            _lock_group_events,
            _lock_group_receipts,
            _lock_group_work,
            _lock_wallet,
            _refund_unstarted_group,
            _set_read_committed,
        )

        try:
            with self.session_factory() as session:
                with session.begin():
                    _set_read_committed(session)
                    graph = lock_haoai_group_graph(
                        session, scope.user_id, scope.task_id
                    )
                    if graph is None:
                        return "not_selected"
                    if _known_failure_terminal_matches(
                        session, graph, self.proof_authority
                    ):
                        return "settled"

                    attempt_index = graph.group.get("active_attempt")
                    if (
                        graph.group.get("status") != "active"
                        or type(attempt_index) is not int
                        or not 0 <= attempt_index < len(graph.steps)
                    ):
                        return "not_selected"
                    step = graph.steps[attempt_index]
                    if step.get("execution_status") != "result_observed":
                        return "not_selected"
                    if not _has_signed_failed_observation(
                        session, graph, step, self.proof_authority
                    ):
                        return "not_selected"

                    now = self._now()
                    submissions = tuple(
                        row
                        for row in graph.external_submissions
                        if row.get("step_id") == step.get("id")
                    )
                    if (
                        graph.task.get("status")
                        not in {"queued", "processing", "reconciling"}
                        or graph.task.get("user_cancelled_at") is not None
                        or graph.task.get("cancellation_reason") is not None
                        or graph.task.get("result")
                        not in {None, _RECONCILING_RESULT}
                        or graph.unit.get("user_cancelled_at") is not None
                        or graph.unit.get("success_confirmed_at") is not None
                        or graph.unit.get("result_status") not in {"pending", "unknown"}
                        or graph.group.get("sticky_policy_stop_event_ref") is not None
                        or graph.group.get("selected_evidence_id") is not None
                        or graph.group.get("outstanding_reservation_ref") is not None
                        or len(submissions) != 1
                        or not _valid_due_original_submission(
                            graph, step, submissions[0], now
                        )
                    ):
                        return "pending"

                    events = _lock_group_events(session, graph)
                    receipts = _lock_group_receipts(session, graph)
                    works = _lock_group_work(session, graph)
                    if events is None or events or receipts is None or receipts or works is None or works:
                        return "pending"

                    proof = read_verified_group_fee(
                        session, graph, graph.binding.quoted_amount
                    )
                    if proof is None:
                        return "pending"
                    if proof.state == "charged":
                        wallet = _lock_wallet(session, graph.owner_id)
                        if wallet is None or _refund_unstarted_group(
                            session,
                            graph,
                            proof,
                            wallet,
                            now,
                            self.id_factory,
                            description="HaoAI 视频服务确认任务失败退款",
                        ) != "settled":
                            return "pending"
                        billing_status = "refunded"
                        refunded_amount = proof.amount
                    elif proof.state == "no_charge":
                        billing_status = "no_charge"
                        refunded_amount = 0
                    else:
                        return "pending"

                    now = self._now()
                    unit_table = BillingUnitRow.__table__
                    unit_change = session.execute(
                        update(unit_table)
                        .where(
                            unit_table.c.id == graph.binding.unit_id,
                            unit_table.c.task_id == graph.task_id,
                            unit_table.c.operation == _OPERATION,
                            unit_table.c.step_graph_version
                            == graph.binding.graph_version,
                            unit_table.c.input_digest == graph.binding.request_digest,
                            unit_table.c.result_status.in_(("pending", "unknown")),
                            unit_table.c.billing_status == billing_status,
                            unit_table.c.debited_amount == proof.amount,
                            unit_table.c.refunded_amount == refunded_amount,
                            unit_table.c.user_cancelled_at.is_(None),
                            unit_table.c.success_confirmed_at.is_(None),
                            unit_table.c.deadline_at
                            == _database_datetime(graph.unit["deadline_at"]),
                        )
                        .values(
                            result_status="failed",
                            updated_at=_database_datetime(now),
                        )
                    )
                    step_change = session.execute(
                        update(ExecutionStepRow.__table__)
                        .where(
                            ExecutionStepRow.__table__.c.id == step.get("id"),
                            ExecutionStepRow.__table__.c.billing_unit_id
                            == graph.binding.unit_id,
                            ExecutionStepRow.__table__.c.execution_status
                            == "result_observed",
                            ExecutionStepRow.__table__.c.required.is_(False),
                        )
                        .values(
                            execution_status="failed",
                            updated_at=_database_datetime(now),
                        )
                    )
                    submission_change = session.execute(
                        update(ExternalSubmissionRow.__table__)
                        .where(
                            ExternalSubmissionRow.__table__.c.id
                            == submissions[0].get("id"),
                            ExternalSubmissionRow.__table__.c.step_id
                            == step.get("id"),
                            ExternalSubmissionRow.__table__.c.submission_key
                            == haoai_group_submission_key(
                                graph.binding.unit_id, str(step.get("id"))
                            ),
                            ExternalSubmissionRow.__table__.c.request_digest
                            == graph.binding.request_digest,
                            ExternalSubmissionRow.__table__.c.submission_status
                            == "submitted",
                            ExternalSubmissionRow.__table__.c.external_task_id
                            == submissions[0].get("external_task_id"),
                        )
                        .values(
                            submission_status="failed",
                            updated_at=_database_datetime(now),
                        )
                    )
                    group_change = session.execute(
                        update(ExecutionAttemptGroupRow.__table__)
                        .where(
                            ExecutionAttemptGroupRow.__table__.c.unit_id
                            == graph.binding.unit_id,
                            ExecutionAttemptGroupRow.__table__.c.group_key
                            == graph.binding.group_key,
                            ExecutionAttemptGroupRow.__table__.c.active_attempt
                            == attempt_index,
                            ExecutionAttemptGroupRow.__table__.c.status == "active",
                            ExecutionAttemptGroupRow.__table__.c.selected_evidence_id.is_(None),
                            ExecutionAttemptGroupRow.__table__.c.outstanding_reservation_ref.is_(None),
                            ExecutionAttemptGroupRow.__table__.c.sticky_policy_stop_event_ref.is_(None),
                        )
                        .values(
                            status="failed",
                            terminal_reason="provider_failed",
                            next_poll_at=None,
                        )
                    )
                    task_table = AITaskRow.__table__
                    task_result = graph.task.get("result")
                    result_predicate = (
                        task_table.c.result.is_(None)
                        if task_result is None
                        else task_table.c.result == task_result
                    )
                    task_change = session.execute(
                        update(task_table)
                        .where(
                            task_table.c.id == graph.task_id,
                            task_table.c.user_id == graph.owner_id,
                            task_table.c.type == _TASK_TYPE,
                            task_table.c.status == graph.task.get("status"),
                            task_table.c.status.in_(
                                ("queued", "processing", "reconciling")
                            ),
                            task_table.c.execution_generation
                            == graph.task.get("execution_generation"),
                            task_table.c.billing_status == billing_status,
                            task_table.c.credit_cost == proof.amount,
                            task_table.c.user_cancelled_at.is_(None),
                            task_table.c.cancellation_reason.is_(None),
                            result_predicate,
                        )
                        .values(
                            status="failed",
                            result=_PROVIDER_FAILED_RESULT,
                            recovery_status=graph.task["recovery_status"],
                            progress=100,
                            progress_message="视频服务报告本次任务失败",
                            claimed_by=None,
                            claim_token=None,
                            lease_until=None,
                            execution_generation=graph.task[
                                "execution_generation"
                            ]
                            + 1,
                            external_provider="haoai",
                            updated_at=_database_datetime(now),
                        )
                    )
                    if any(
                        change.rowcount != 1
                        for change in (
                            unit_change,
                            step_change,
                            submission_change,
                            group_change,
                            task_change,
                        )
                    ):
                        raise RuntimeError(
                            "HaoAI known provider failure projection CAS lost"
                        )
                    session.flush()
                    terminal_graph = lock_haoai_group_graph(
                        session, scope.user_id, scope.task_id
                    )
                    if terminal_graph is None or not _known_failure_terminal_matches(
                        session, terminal_graph, self.proof_authority
                    ):
                        raise RuntimeError(
                            "HaoAI known provider failure terminal proof changed"
                        )
            return "settled"
        except Exception:
            return self._confirm_known_failure(scope)

    def _confirm_known_failure(self, scope: OwnerTaskScope) -> str:
        try:
            with self.session_factory() as session:
                with session.begin():
                    from haoai_backend.generation_core.haoai_group.cancellation import (
                        _set_read_committed,
                    )

                    _set_read_committed(session)
                    graph = lock_haoai_group_graph(
                        session, scope.user_id, scope.task_id
                    )
                    return (
                        "settled"
                        if graph is not None
                        and _known_failure_terminal_matches(
                            session, graph, self.proof_authority
                        )
                        else "pending"
                    )
        except Exception:
            return "pending"

    def complete(
        self,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified_input: VerifiedHaoaiGroupExecutionInput,
        grant: HaoaiGroupPollGrant,
        poll_outcome: HaoaiGroupPollOutcome,
        plan: OwnedObjectPlan,
        verification: OwnedObjectVerification,
    ) -> HaoaiGroupResultOutcome:
        """Commit result evidence, receipt, selected Group, Unit, Task, and message."""

        if not _input_shapes_match(
            claim, intent, verified_input, grant, poll_outcome, plan, verification
        ):
            return HaoaiGroupResultOutcome("pending")
        observation = poll_outcome.observation
        stored = verification.stored_media
        assert observation is not None and stored is not None
        evidence_key = _RESULT_KEY_PREFIX + intent.external_submission_id
        generated_receipt: GenerationResultReceipt | None = None
        replay_receipt: GenerationResultReceipt | None = None
        ownership_lost = False

        try:
            with self.session_factory() as session:
                with session.begin():
                    graph = lock_haoai_group_graph(
                        session, claim.scope.user_id, claim.scope.task_id
                    )
                    if graph is None or not _binding_matches(
                        graph, claim, intent, verified_input, grant
                    ):
                        return HaoaiGroupResultOutcome("pending")
                    from .media import verify_persisted_media_write

                    if not verify_persisted_media_write(
                        session,
                        claim,
                        intent,
                        verified_input,
                        grant,
                        plan,
                        verification,
                        self.proof_authority,
                    ):
                        return HaoaiGroupResultOutcome("pending")

                    existing = session.execute(
                        select(ResultEvidenceRow.__table__).where(
                            ResultEvidenceRow.__table__.c.evidence_key == evidence_key
                        )
                    ).mappings().one_or_none()
                    if existing is not None:
                        replay_receipt = _existing_receipt(
                            session,
                            graph,
                            claim,
                            intent,
                            verified_input,
                            grant,
                            poll_outcome,
                            existing,
                            evidence_key,
                            plan,
                            verification,
                            stored,
                            self.proof_authority,
                        )
                        if replay_receipt is None:
                            return HaoaiGroupResultOutcome("pending")
                    else:
                        now = self._now()
                        if not _current_group_owner(graph, claim, intent, now):
                            ownership_lost = not _claim_live(graph.task, claim, now)
                            return HaoaiGroupResultOutcome(
                                "ownership_lost" if ownership_lost else "pending"
                            )
                        if not _accepted_completed_observation(
                            session,
                            graph,
                            claim,
                            intent,
                            verified_input,
                            grant,
                            poll_outcome,
                            self.proof_authority,
                        ):
                            return HaoaiGroupResultOutcome("pending")
                        if _has_group_work(session, graph):
                            return HaoaiGroupResultOutcome("pending")
                        if _has_policy_stop_event(session, graph):
                            return HaoaiGroupResultOutcome("pending")

                        result_ref = self._new_id()
                        evidence_id = self._new_id()
                        if result_ref is None or evidence_id is None:
                            return HaoaiGroupResultOutcome("pending")
                        generated_receipt = GenerationResultReceipt(
                            result_ref=result_ref,
                            owner_id=claim.scope.user_id,
                            task_id=claim.scope.task_id,
                            unit_id=verified_input.unit_id,
                            operation=_OPERATION,
                            step_id=intent.step_id,
                            submission_id=intent.external_submission_id,
                            evidence_id=evidence_id,
                            selected_attempt_id=intent.attempt_id,
                            recoverable_locator=stored.locator,
                            content_digest=stored.content_digest,
                            validation_version=_VALIDATION_VERSION,
                            validated_at=now,
                            generation_fulfilled_at=now,
                            late_disposition=None,
                            policy_stop_disposition=None,
                            automatic_application_allowed=False,
                        )
                        if not self._write_terminal(
                            session,
                            graph,
                            claim,
                            intent,
                            verified_input,
                            grant,
                            evidence_key,
                            evidence_id,
                            generated_receipt,
                            stored,
                            now,
                        ):
                            raise RuntimeError("HaoAI Group result transition lost")
        except Exception:
            # A lost commit acknowledgement is resolved only from the original result key.
            replay_receipt = self._confirm_result(
                claim,
                intent,
                verified_input,
                grant,
                poll_outcome,
                evidence_key,
                plan,
                verification,
                stored,
                self.proof_authority,
            )
            if replay_receipt is None:
                return HaoaiGroupResultOutcome("pending")

        receipt = replay_receipt or generated_receipt
        if receipt is None:
            receipt = self._confirm_result(
                claim,
                intent,
                verified_input,
                grant,
                poll_outcome,
                evidence_key,
                plan,
                verification,
                stored,
                self.proof_authority,
            )
        if receipt is None:
            return HaoaiGroupResultOutcome("pending")
        return HaoaiGroupResultOutcome("completed", receipt)

    def complete_controlled_late(
        self,
        candidate: object,
        plan: OwnedObjectPlan,
        verification: OwnedObjectVerification,
        *,
        registry: object,
        crypto: object,
    ) -> HaoaiGroupResultOutcome:
        """Persist a read-only late receipt without changing terminal business state."""

        from .late import (
            HaoaiGroupLateCandidate,
            read_verified_late_candidate,
            same_late_candidate,
        )
        from .media import verify_persisted_late_media_write

        if (
            type(candidate) is not HaoaiGroupLateCandidate
            or type(plan) is not OwnedObjectPlan
            or type(verification) is not OwnedObjectVerification
            or verification.status != "verified"
            or verification.stored_media is None
            or type(self.proof_authority) is not HmacProofAuthority
        ):
            return HaoaiGroupResultOutcome("pending")

        scope = candidate.controlled_scope.owner_scope
        stored = verification.stored_media
        evidence_key = _LATE_RESULT_KEY_PREFIX + candidate.intent.external_submission_id
        completed: GenerationResultReceipt | None = None
        try:
            with self.session_factory() as session:
                with session.begin():
                    from haoai_backend.generation_core.haoai_group.cancellation import (
                        _set_read_committed,
                    )

                    _set_read_committed(session)
                    current = read_verified_late_candidate(
                        session,
                        scope,
                        registry=registry,
                        crypto=crypto,
                        proof_authority=self.proof_authority,
                    )
                    if not same_late_candidate(candidate, current):
                        return HaoaiGroupResultOutcome("pending")
                    graph = lock_haoai_group_graph(session, scope.user_id, scope.task_id)
                    if graph is None or not verify_persisted_late_media_write(
                        session,
                        candidate,
                        plan,
                        verification,
                        self.proof_authority,
                    ):
                        return HaoaiGroupResultOutcome("pending")

                    evidence = session.execute(
                        select(ResultEvidenceRow.__table__).where(
                            ResultEvidenceRow.__table__.c.evidence_key == evidence_key
                        )
                    ).mappings().one_or_none()
                    receipts = session.execute(
                        select(GenerationResultReceiptRow.__table__).where(
                            GenerationResultReceiptRow.__table__.c.unit_id
                            == candidate.verified_input.unit_id
                        )
                    ).mappings().all()
                    if evidence is not None:
                        if len(receipts) != 1:
                            return HaoaiGroupResultOutcome("pending")
                        completed = _late_receipt_from_rows(
                            session,
                            graph,
                            current,
                            evidence,
                            receipts[0],
                            stored,
                        )
                        if completed is None:
                            return HaoaiGroupResultOutcome("pending")
                        if not _task_result_has_late_projection(
                            graph.task.get("result"), completed, current, stored
                        ):
                            return HaoaiGroupResultOutcome("pending")
                    else:
                        if receipts:
                            return HaoaiGroupResultOutcome("pending")
                        now = self._now()
                        result_ref = self._new_id()
                        evidence_id = self._new_id()
                        observation = current.poll_outcome.observation
                        if (
                            now is None
                            or result_ref is None
                            or evidence_id is None
                            or observation is None
                            or not _late_stored_media_matches(
                                plan, verification, stored, current
                            )
                        ):
                            return HaoaiGroupResultOutcome("pending")

                        receipt = GenerationResultReceipt(
                            result_ref=result_ref,
                            owner_id=scope.user_id,
                            task_id=scope.task_id,
                            unit_id=current.verified_input.unit_id,
                            operation=_OPERATION,
                            step_id=current.intent.step_id,
                            submission_id=current.intent.external_submission_id,
                            evidence_id=evidence_id,
                            selected_attempt_id=current.intent.attempt_id,
                            recoverable_locator=stored.locator,
                            content_digest=stored.content_digest,
                            validation_version=_LATE_VALIDATION_VERSION,
                            validated_at=now,
                            generation_fulfilled_at=observation.received_at,
                            late_disposition="stopped_late_generation",
                            policy_stop_disposition=current.financial_disposition,
                            automatic_application_allowed=False,
                        )
                        projection = _late_generation_projection(
                            receipt, current, stored
                        )
                        result_value = _append_late_generation(
                            graph.task.get("result"), projection
                        )
                        if result_value is None:
                            return HaoaiGroupResultOutcome("pending")
                        session.execute(
                            insert(ResultEvidenceRow.__table__).values(
                                id=evidence_id,
                                step_id=current.intent.step_id,
                                submission_id=current.intent.external_submission_id,
                                evidence_key=evidence_key,
                                source="recovery",
                                evidence_kind=_LATE_RESULT_KIND,
                                payload_digest=stored.content_digest,
                                result_location=stored.locator,
                                result_digest=stored.content_digest,
                                received_at=_database_datetime(now),
                                validity="valid",
                                application_status="pending",
                                applied_at=None,
                            )
                        )
                        write = GenerationRecordRepository(session).stage_record(receipt)
                        if write.status != "created":
                            raise RuntimeError("HaoAI Group late receipt insert lost")
                        task_table = AITaskRow.__table__
                        task_change = session.execute(
                            update(task_table)
                            .where(
                                task_table.c.id == scope.task_id,
                                task_table.c.user_id == scope.user_id,
                                task_table.c.type == _TASK_TYPE,
                                task_table.c.status == "failed",
                                task_table.c.result == graph.task.get("result"),
                                task_table.c.execution_generation
                                == graph.task.get("execution_generation"),
                            )
                            .values(
                                result=result_value,
                                updated_at=_database_datetime(now),
                            )
                        )
                        if task_change.rowcount != 1:
                            raise RuntimeError("HaoAI Group late Task projection CAS lost")
                        completed = receipt
        except Exception:
            completed = self._confirm_controlled_late(
                candidate,
                plan,
                verification,
                evidence_key,
                registry=registry,
                crypto=crypto,
            )
            if completed is None:
                return HaoaiGroupResultOutcome("pending")
        return HaoaiGroupResultOutcome("completed", completed)

    def confirm_existing_controlled_late(
        self,
        candidate: object,
        plan: OwnedObjectPlan,
        *,
        registry: object,
        crypto: object,
    ) -> HaoaiGroupResultOutcome | None:
        """Replay a complete late projection from its durable, authenticated facts."""

        from .late import (
            HaoaiGroupLateCandidate,
            read_verified_late_candidate,
            same_late_candidate,
        )
        from .media import verify_persisted_late_media_control

        if (
            type(candidate) is not HaoaiGroupLateCandidate
            or type(plan) is not OwnedObjectPlan
            or type(self.proof_authority) is not HmacProofAuthority
        ):
            return HaoaiGroupResultOutcome("pending")

        scope = candidate.controlled_scope.owner_scope
        evidence_key = _LATE_RESULT_KEY_PREFIX + candidate.intent.external_submission_id
        try:
            with self.session_factory() as session:
                with session.begin():
                    from haoai_backend.generation_core.haoai_group.cancellation import (
                        _set_read_committed,
                    )

                    _set_read_committed(session)
                    current = read_verified_late_candidate(
                        session,
                        scope,
                        registry=registry,
                        crypto=crypto,
                        proof_authority=self.proof_authority,
                    )
                    if not same_late_candidate(candidate, current):
                        return HaoaiGroupResultOutcome("pending")
                    graph = lock_haoai_group_graph(
                        session, scope.user_id, scope.task_id
                    )
                    if graph is None or not verify_persisted_late_media_control(
                        session,
                        current,
                        plan,
                        self.proof_authority,
                    ):
                        return HaoaiGroupResultOutcome("pending")

                    evidence = session.execute(
                        select(ResultEvidenceRow.__table__)
                        .where(
                            ResultEvidenceRow.__table__.c.evidence_key == evidence_key
                        )
                        .with_for_update()
                    ).mappings().one_or_none()
                    receipts = session.execute(
                        select(GenerationResultReceiptRow.__table__)
                        .where(
                            GenerationResultReceiptRow.__table__.c.unit_id
                            == current.verified_input.unit_id
                        )
                        .with_for_update()
                    ).mappings().all()
                    if evidence is None and not receipts:
                        return None
                    if evidence is None or len(receipts) != 1:
                        return HaoaiGroupResultOutcome("pending")

                    stored = _stored_media_from_late_plan(plan)
                    if stored is None:
                        return HaoaiGroupResultOutcome("pending")
                    receipt = _late_receipt_from_rows(
                        session,
                        graph,
                        current,
                        evidence,
                        receipts[0],
                        stored,
                    )
                    if receipt is None or not _task_result_has_late_projection(
                        graph.task.get("result"), receipt, current, stored
                    ):
                        return HaoaiGroupResultOutcome("pending")
                    return HaoaiGroupResultOutcome("completed", receipt)
        except Exception:
            return HaoaiGroupResultOutcome("pending")

    def _confirm_controlled_late(
        self,
        candidate: object,
        plan: OwnedObjectPlan,
        verification: OwnedObjectVerification,
        evidence_key: str,
        *,
        registry: object,
        crypto: object,
    ) -> GenerationResultReceipt | None:
        from .late import (
            HaoaiGroupLateCandidate,
            read_verified_late_candidate,
            same_late_candidate,
        )
        from .media import verify_persisted_late_media_write

        if type(candidate) is not HaoaiGroupLateCandidate:
            return None
        try:
            with self.session_factory() as session:
                with session.begin():
                    from haoai_backend.generation_core.haoai_group.cancellation import (
                        _set_read_committed,
                    )

                    _set_read_committed(session)
                    scope = candidate.controlled_scope.owner_scope
                    current = read_verified_late_candidate(
                        session,
                        scope,
                        registry=registry,
                        crypto=crypto,
                        proof_authority=self.proof_authority,
                    )
                    if not same_late_candidate(candidate, current):
                        return None
                    graph = lock_haoai_group_graph(session, scope.user_id, scope.task_id)
                    if graph is None or not verify_persisted_late_media_write(
                        session,
                        candidate,
                        plan,
                        verification,
                        self.proof_authority,
                    ):
                        return None
                    evidence = session.execute(
                        select(ResultEvidenceRow.__table__).where(
                            ResultEvidenceRow.__table__.c.evidence_key == evidence_key
                        )
                    ).mappings().one_or_none()
                    receipt_row = session.execute(
                        select(GenerationResultReceiptRow.__table__).where(
                            GenerationResultReceiptRow.__table__.c.evidence_id
                            == (evidence.get("id") if evidence is not None else None),
                            GenerationResultReceiptRow.__table__.c.owner_id
                            == scope.user_id,
                            GenerationResultReceiptRow.__table__.c.task_id
                            == scope.task_id,
                            GenerationResultReceiptRow.__table__.c.unit_id
                            == current.verified_input.unit_id,
                        )
                    ).mappings().one_or_none()
                    if evidence is None or receipt_row is None:
                        return None
                    receipt = _late_receipt_from_rows(
                        session,
                        graph,
                        current,
                        evidence,
                        receipt_row,
                        verification.stored_media,
                    )
                    if receipt is None or not _task_result_has_late_projection(
                        graph.task.get("result"), receipt, current, verification.stored_media
                    ):
                        return None
                    return receipt
        except Exception:
            return None

    def settle_due(
        self,
        scope: OwnerTaskScope,
        *,
        stop_signal: Event | None = None,
    ) -> Literal["settled", "retained", "pending"]:
        """Settle only an authenticated, fixed-deadline Group with unknown truth."""

        from haoai_backend.generation_core.haoai_group.cancellation import (
            _lock_group_events,
            _lock_group_receipts,
            _lock_group_work,
            _lock_wallet,
            _refund_unstarted_group,
            _timely_terminal_receipt,
        )

        if (
            type(scope) is not OwnerTaskScope
            or scope.operation != _OPERATION
            or not scope.user_id
            or not scope.task_id
            or (stop_signal is not None and stop_signal.is_set())
            or type(self.proof_authority) is not HmacProofAuthority
        ):
            return "pending"
        financial_writes_staged = False
        try:
            now = self._now()
            with self.session_factory() as session:
                with session.begin():
                    from haoai_backend.generation_core.haoai_group.cancellation import (
                        _set_read_committed,
                    )

                    _set_read_committed(session)
                    graph = lock_haoai_group_graph(
                        session, scope.user_id, scope.task_id
                    )
                    if graph is None:
                        return "pending"
                    events = _lock_group_events(session, graph)
                    receipts = _lock_group_receipts(session, graph)
                    works = _lock_group_work(session, graph)
                    if events is None or receipts is None or works is None or works:
                        return "pending"

                    if graph.task.get("user_cancelled_at") is not None or graph.unit.get(
                        "user_cancelled_at"
                    ) is not None:
                        return "pending"
                    first = _as_utc(graph.unit.get("first_intent_at"))
                    deadline = _as_utc(graph.unit.get("deadline_at"))
                    if (
                        first is None
                        or deadline is None
                        or deadline != first + timedelta(
                            seconds=HAOAI_ATTEMPT_GROUP.original_deadline_seconds
                        )
                        or now < deadline
                        or graph.group.get("outstanding_reservation_ref") is not None
                    ):
                        return "pending"
                    if receipts:
                        return (
                            "retained"
                            if len(receipts) == 1
                            and _timely_terminal_receipt(graph, receipts[0])
                            else "pending"
                        )

                    evidence_key = _DUE_EVIDENCE_KEY + graph.binding.unit_id
                    evidence_table = ResultEvidenceRow.__table__
                    existing = session.execute(
                        select(evidence_table)
                        .where(evidence_table.c.evidence_key == evidence_key)
                        .with_for_update()
                    ).mappings().one_or_none()
                    if existing is not None:
                        return (
                            "settled"
                            if _existing_due_matches(
                                session,
                                graph,
                                events,
                                existing,
                                self.proof_authority,
                            )
                            else "pending"
                        )

                    if (
                        graph.task.get("status")
                        not in {"queued", "processing", "reconciling"}
                        or graph.group.get("status")
                        not in {"active", "pending_verification", "policy_stopped"}
                        or graph.unit.get("result_status") not in {"pending", "unknown"}
                        or graph.unit.get("billing_status") not in {"charged", "no_charge"}
                        or graph.unit.get("success_confirmed_at") is not None
                        or graph.group.get("selected_evidence_id") is not None
                    ):
                        return "pending"

                    active_step = graph.steps[graph.binding.attempt_index]
                    active_submissions = tuple(
                        row
                        for row in graph.external_submissions
                        if row.get("step_id") == active_step.get("id")
                    )
                    if len(active_submissions) != 1:
                        return "pending"
                    submission = active_submissions[0]
                    if not _valid_due_original_submission(
                        graph, active_step, submission, now
                    ):
                        return "pending"

                    wallet = (
                        _lock_wallet(session, graph.owner_id)
                        if graph.binding.quoted_amount > 0
                        else None
                    )
                    if graph.binding.quoted_amount > 0 and wallet is None:
                        return "pending"

                    if events:
                        if len(events) != 1:
                            return "pending"
                        event = events[0]
                        disposition = settle_pending_policy_stop_event(
                            session,
                            graph,
                            event,
                            proof_authority=self.proof_authority,
                            verified_quote=graph.binding.quoted_amount,
                            now=now,
                            id_factory=self.id_factory,
                        )
                        if disposition not in {"refunded", "no_debit"}:
                            return "pending"
                        financial_writes_staged = (
                            event.get("financial_disposition") == "pending"
                        )
                        if disposition == "refunded":
                            graph.task["billing_status"] = "refunded"
                            graph.unit["billing_status"] = "refunded"
                            graph.unit["refunded_amount"] = graph.binding.quoted_amount
                        events = _lock_group_events(session, graph)
                        if events is None or len(events) != 1 or not verify_policy_stop_event(
                            session,
                            graph,
                            events[0],
                            proof_authority=self.proof_authority,
                            verified_quote=graph.binding.quoted_amount,
                        ):
                            if financial_writes_staged:
                                raise RuntimeError(
                                    "HaoAI Group policy settlement proof changed"
                                )
                            return "pending"
                    else:
                        proof = read_verified_group_fee(
                            session, graph, graph.binding.quoted_amount
                        )
                        if proof is None:
                            return "pending"
                        if proof.state == "charged":
                            if wallet is None or _refund_unstarted_group(
                                session,
                                graph,
                                proof,
                                wallet,
                                now,
                                self.id_factory,
                                description="HaoAI 视频任务在原期限内未能确认结果",
                            ) != "settled":
                                return "pending"
                            financial_writes_staged = True
                            graph.task["billing_status"] = "refunded"
                            graph.unit["billing_status"] = "refunded"
                            graph.unit["refunded_amount"] = graph.binding.quoted_amount
                        elif proof.state not in {"no_charge", "refunded"}:
                            return "pending"

                    proof = read_verified_group_fee(
                        session, graph, graph.binding.quoted_amount
                    )
                    if proof is None or proof.state not in {"no_charge", "refunded"}:
                        if financial_writes_staged:
                            raise RuntimeError(
                                "HaoAI Group due fee settlement proof changed"
                            )
                        return "pending"
                    terminal_events = _lock_group_events(session, graph)
                    if terminal_events is None:
                        if financial_writes_staged:
                            raise RuntimeError("HaoAI Group due event set changed")
                        return "pending"
                    if terminal_events and (
                        len(terminal_events) != 1
                        or not verify_policy_stop_event(
                            session,
                            graph,
                            terminal_events[0],
                            proof_authority=self.proof_authority,
                            verified_quote=graph.binding.quoted_amount,
                        )
                        or terminal_events[0].get("financial_disposition")
                        not in {"refunded", "no_debit"}
                    ):
                        if financial_writes_staged:
                            raise RuntimeError(
                                "HaoAI Group due event proof changed"
                            )
                        return "pending"

                    due_id = self._new_id()
                    fact = _group_due_fact(
                        graph,
                        submission,
                        now,
                        proof.original_debit_ref,
                        proof.refund_ref,
                        str(terminal_events[0]["event_id"])
                        if terminal_events
                        else None,
                    )
                    if due_id is None or fact is None:
                        if financial_writes_staged:
                            raise RuntimeError(
                                "HaoAI Group due evidence cannot follow fee settlement"
                            )
                        return "pending"
                    canonical = _canonical_json(fact)
                    envelope = json.dumps(
                        {
                            "fact": fact,
                            "proof": {
                                "key_id": self.proof_authority.key_id,
                                "signature": self.proof_authority.sign(
                                    _DUE_PURPOSE, canonical
                                ),
                            },
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    )
                    session.execute(
                        insert(evidence_table).values(
                            id=due_id,
                            step_id=active_step["id"],
                            submission_id=submission["id"],
                            evidence_key=evidence_key,
                            source="recovery",
                            evidence_kind=_DUE_EVIDENCE_KIND,
                            payload_digest=hashlib.sha256(canonical).hexdigest(),
                            result_location=envelope,
                            result_digest=None,
                            received_at=_database_datetime(now),
                            validity="unknown",
                            application_status="pending",
                            applied_at=None,
                        )
                    )

                    unit_table = BillingUnitRow.__table__
                    unit_change = session.execute(
                        update(unit_table)
                        .where(
                            unit_table.c.id == graph.binding.unit_id,
                            unit_table.c.task_id == graph.task_id,
                            unit_table.c.result_status.in_(("pending", "unknown")),
                            unit_table.c.success_confirmed_at.is_(None),
                            unit_table.c.user_cancelled_at.is_(None),
                            unit_table.c.deadline_at == _database_datetime(deadline),
                        )
                        .values(result_status="unknown", updated_at=_database_datetime(now))
                    )
                    task_table = AITaskRow.__table__
                    task_result = graph.task.get("result")
                    if task_result is None or task_result == _RECONCILING_RESULT:
                        task_result = _DUE_RESULT
                    task_change = session.execute(
                        update(task_table)
                        .where(
                            task_table.c.id == graph.task_id,
                            task_table.c.user_id == graph.owner_id,
                            task_table.c.type == _TASK_TYPE,
                            task_table.c.status == graph.task.get("status"),
                            task_table.c.status.in_(("queued", "processing", "reconciling")),
                            task_table.c.result == graph.task.get("result"),
                            task_table.c.user_cancelled_at.is_(None),
                            task_table.c.execution_generation
                            == graph.task.get("execution_generation"),
                        )
                        .values(
                            status="failed",
                            result=task_result,
                            progress=100,
                            progress_message="视频生成结果未能在期限内确认，原响应仍按未知状态保留",
                            claimed_by=None,
                            claim_token=None,
                            lease_until=None,
                            execution_generation=graph.task["execution_generation"] + 1,
                            updated_at=_database_datetime(now),
                        )
                    )
                    if unit_change.rowcount != 1 or task_change.rowcount != 1:
                        raise RuntimeError("HaoAI Group deadline settlement CAS lost")

                    if not terminal_events:
                        group_table = ExecutionAttemptGroupRow.__table__
                        group_change = session.execute(
                            update(group_table)
                            .where(
                                group_table.c.unit_id == graph.binding.unit_id,
                                group_table.c.group_key == graph.binding.group_key,
                                group_table.c.status == graph.group.get("status"),
                                group_table.c.terminal_reason
                                == graph.group.get("terminal_reason"),
                                group_table.c.selected_evidence_id.is_(None),
                                group_table.c.outstanding_reservation_ref.is_(None),
                                group_table.c.sticky_policy_stop_event_ref.is_(None),
                            )
                            .values(
                                status="failed",
                                terminal_reason="original_deadline_expired",
                                next_poll_at=None,
                            )
                        )
                        if group_change.rowcount != 1:
                            raise RuntimeError("HaoAI Group deadline terminal CAS lost")
                    elif (
                        graph.group.get("status") != "policy_stopped"
                        or graph.group.get("sticky_policy_stop_event_ref")
                        != terminal_events[0].get("event_id")
                    ):
                        raise RuntimeError("HaoAI Group policy terminal proof changed")

            return "settled"
        except Exception:
            return self._confirm_due(scope)

    def _confirm_due(self, scope: OwnerTaskScope) -> Literal["settled", "pending"]:
        from haoai_backend.generation_core.haoai_group.cancellation import (
            _lock_group_events,
            _set_read_committed,
        )

        try:
            with self.session_factory() as session:
                with session.begin():
                    _set_read_committed(session)
                    graph = lock_haoai_group_graph(
                        session, scope.user_id, scope.task_id
                    )
                    if graph is None:
                        return "pending"
                    events = _lock_group_events(session, graph)
                    if events is None:
                        return "pending"
                    evidence = session.execute(
                        select(ResultEvidenceRow.__table__)
                        .where(
                            ResultEvidenceRow.__table__.c.evidence_key
                            == _DUE_EVIDENCE_KEY + graph.binding.unit_id
                        )
                        .with_for_update()
                    ).mappings().one_or_none()
                    return (
                        "settled"
                        if evidence is not None
                        and _existing_due_matches(
                            session,
                            graph,
                            events,
                            evidence,
                            self.proof_authority,
                        )
                        else "pending"
                    )
        except Exception:
            return "pending"

    def authorize_media_materialization(
        self,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified_input: VerifiedHaoaiGroupExecutionInput,
        grant: HaoaiGroupPollGrant,
        poll_outcome: HaoaiGroupPollOutcome,
    ) -> Literal["ready", "pending", "ownership_lost"]:
        """Confirm current ownership before reading provider content or writing media."""

        if not _completed_input_shapes_match(
            claim, intent, verified_input, grant, poll_outcome
        ):
            return "pending"
        try:
            with self.session_factory() as session:
                with session.begin():
                    graph = lock_haoai_group_graph(
                        session, claim.scope.user_id, claim.scope.task_id
                    )
                    if graph is None or not _binding_matches(
                        graph, claim, intent, verified_input, grant
                    ):
                        return "pending"
                    now = self._now()
                    if not _claim_live(graph.task, claim, now):
                        return "ownership_lost"
                    if not _current_group_owner(graph, claim, intent, now):
                        return "pending"
                    if not _accepted_completed_observation(
                        session,
                        graph,
                        claim,
                        intent,
                        verified_input,
                        grant,
                        poll_outcome,
                        self.proof_authority,
                    ):
                        return "pending"
                    if _has_group_work(session, graph) or _has_policy_stop_event(
                        session, graph
                    ):
                        return "pending"
                    return "ready"
        except Exception:
            return "pending"

    def _write_terminal(
        self,
        session: Session,
        graph: LockedHaoaiGroupGraph,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified_input: VerifiedHaoaiGroupExecutionInput,
        grant: HaoaiGroupPollGrant,
        evidence_key: str,
        evidence_id: str,
        receipt: GenerationResultReceipt,
        stored: StoredMedia,
        now: datetime,
    ) -> bool:
        result_value = _success_result_value(stored, receipt.result_ref)
        session.execute(
            insert(ResultEvidenceRow.__table__).values(
                id=evidence_id,
                step_id=intent.step_id,
                submission_id=intent.external_submission_id,
                evidence_key=evidence_key,
                source="provider",
                evidence_kind=_RESULT_KIND,
                payload_digest=stored.content_digest,
                result_location=stored.locator,
                result_digest=stored.content_digest,
                received_at=_database_datetime(now),
                validity="valid",
                application_status="pending",
                applied_at=None,
            )
        )
        task_table = AITaskRow.__table__
        task = graph.task
        result_predicate = (
            task_table.c.result.is_(None)
            if task.get("result") is None
            else task_table.c.result == task.get("result")
        )
        task_change = session.execute(
            update(task_table)
            .where(
                task_table.c.id == claim.scope.task_id,
                task_table.c.user_id == claim.scope.user_id,
                task_table.c.type == _TASK_TYPE,
                task_table.c.status == task.get("status"),
                task_table.c.status.in_(("processing", "reconciling")),
                task_table.c.claimed_by == claim.worker_instance,
                task_table.c.claim_token == claim.token,
                task_table.c.execution_generation == claim.execution_generation,
                task_table.c.lease_until == _database_datetime(task["lease_until"]),
                task_table.c.lease_until > _database_datetime(now),
                task_table.c.user_cancelled_at.is_(None),
                result_predicate,
            )
            .values(
                status="completed",
                result=result_value,
                progress=100,
                progress_message="视频已生成",
                external_provider="haoai",
                updated_at=_database_datetime(now),
            )
        )
        if task_change.rowcount != 1:
            return False

        unit_table = BillingUnitRow.__table__
        unit = graph.unit
        unit_change = session.execute(
            update(unit_table)
            .where(
                unit_table.c.id == verified_input.unit_id,
                unit_table.c.task_id == claim.scope.task_id,
                unit_table.c.operation == _OPERATION,
                unit_table.c.step_graph_version == graph.binding.graph_version,
                unit_table.c.input_digest == intent.request_digest,
                unit_table.c.result_status.in_(("pending", "unknown")),
                unit_table.c.billing_status.in_(("charged", "no_charge")),
                unit_table.c.user_cancelled_at.is_(None),
                unit_table.c.success_confirmed_at.is_(None),
                unit_table.c.first_intent_at
                == _database_datetime(unit["first_intent_at"]),
                unit_table.c.deadline_at == _database_datetime(intent.deadline_at),
                unit_table.c.debited_amount == unit.get("debited_amount"),
                unit_table.c.refunded_amount == 0,
            )
            .values(
                result_status="succeeded",
                success_confirmed_at=_database_datetime(now),
                updated_at=_database_datetime(now),
            )
        )
        if unit_change.rowcount != 1:
            return False

        group_table = ExecutionAttemptGroupRow.__table__
        group = graph.group
        group_change = session.execute(
            update(group_table)
            .where(
                group_table.c.unit_id == verified_input.unit_id,
                group_table.c.group_key == graph.binding.group_key,
                group_table.c.active_attempt == verified_input.attempt_index,
                group_table.c.status == group.get("status"),
                group_table.c.selected_evidence_id.is_(None),
                group_table.c.outstanding_reservation_ref.is_(None),
                group_table.c.sticky_policy_stop_event_ref.is_(None),
            )
            .values(
                selected_evidence_id=evidence_id,
                status="succeeded",
                terminal_reason=None,
                next_poll_at=None,
            )
        )
        if group_change.rowcount != 1:
            return False

        step_change = session.execute(
            update(ExecutionStepRow.__table__)
            .where(
                ExecutionStepRow.__table__.c.id == intent.step_id,
                ExecutionStepRow.__table__.c.billing_unit_id
                == verified_input.unit_id,
                ExecutionStepRow.__table__.c.execution_status == "result_observed",
                ExecutionStepRow.__table__.c.required.is_(False),
            )
            .values(execution_status="applied", updated_at=_database_datetime(now))
        )
        if step_change.rowcount != 1:
            return False

        submission_table = ExternalSubmissionRow.__table__
        submission = _active_submission(graph, intent.step_id)
        if submission is None:
            return False
        submission_change = session.execute(
            update(submission_table)
            .where(
                submission_table.c.id == intent.external_submission_id,
                submission_table.c.step_id == intent.step_id,
                submission_table.c.submission_status == "submitted",
                submission_table.c.external_task_id == submission.get("external_task_id"),
                submission_table.c.intent_at == _database_datetime(intent.committed_at),
                submission_table.c.deadline_at == _database_datetime(intent.deadline_at),
            )
            .values(submission_status="completed", updated_at=_database_datetime(now))
        )
        if submission_change.rowcount != 1:
            return False

        write = GenerationRecordRepository(session).stage_record(receipt)
        if write.status != "created":
            return False
        message_id = task.get("message_id")
        if message_id and not _project_assistant_message(session, str(message_id), stored.locator):
            return False
        return True

    def _confirm_result(
        self,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified_input: VerifiedHaoaiGroupExecutionInput,
        grant: HaoaiGroupPollGrant,
        poll_outcome: HaoaiGroupPollOutcome,
        evidence_key: str,
        plan: OwnedObjectPlan,
        verification: OwnedObjectVerification,
        stored: StoredMedia,
        authority: HmacProofAuthority | None,
    ) -> GenerationResultReceipt | None:
        try:
            with self.session_factory() as session:
                graph = lock_haoai_group_graph(
                    session, claim.scope.user_id, claim.scope.task_id
                )
                if graph is None:
                    return None
                from .media import verify_persisted_media_write

                if not verify_persisted_media_write(
                    session,
                    claim,
                    intent,
                    verified_input,
                    grant,
                    plan,
                    verification,
                    authority,
                ):
                    return None
                if not _accepted_completed_observation(
                    session,
                    graph,
                    claim,
                    intent,
                    verified_input,
                    grant,
                    poll_outcome,
                    authority,
                ):
                    return None
                evidence = session.execute(
                    select(ResultEvidenceRow.__table__).where(
                        ResultEvidenceRow.__table__.c.evidence_key == evidence_key,
                        ResultEvidenceRow.__table__.c.step_id == intent.step_id,
                        ResultEvidenceRow.__table__.c.submission_id
                        == intent.external_submission_id,
                    )
                ).mappings().one_or_none()
                if evidence is None or not _result_evidence_matches(
                    evidence, stored, evidence_key
                ):
                    return None
                receipt_row = session.execute(
                    select(GenerationResultReceiptRow.result_ref).where(
                        GenerationResultReceiptRow.evidence_id == evidence["id"],
                        GenerationResultReceiptRow.owner_id == claim.scope.user_id,
                        GenerationResultReceiptRow.task_id == claim.scope.task_id,
                        GenerationResultReceiptRow.unit_id == verified_input.unit_id,
                        GenerationResultReceiptRow.step_id == intent.step_id,
                        GenerationResultReceiptRow.submission_id
                        == intent.external_submission_id,
                    )
                ).scalar_one_or_none()
                if not isinstance(receipt_row, str):
                    return None
                receipt = GenerationRecordRepository(session).find_result_receipt(
                    claim.scope.user_id, claim.scope.task_id, receipt_row
                )
                if not isinstance(receipt, GenerationResultReceipt) or not _receipt_matches(
                    receipt, stored, graph, intent, verified_input, evidence["id"]
                ):
                    return None
                result_value = _success_result_value(stored, receipt.result_ref)
                if not _terminal_graph_matches(
                    graph,
                    claim,
                    intent,
                    verified_input,
                    evidence["id"],
                    receipt,
                    result_value,
                    stored,
                ):
                    return None
                if _has_policy_stop_event(session, graph):
                    return None
                if not _plan_matches(
                    plan,
                    claim,
                    intent,
                    poll_outcome.observation,
                    verification,
                    stored,
                ):
                    return None
                message_id = graph.task.get("message_id")
                if message_id:
                    message_content = session.execute(
                        select(chat_messages.c.content).where(
                            chat_messages.c.id == message_id,
                            chat_messages.c.role == "assistant",
                        )
                    ).scalar_one_or_none()
                    if message_content != stored.locator:
                        return None
                return receipt
        except Exception:
            return None

    def _new_id(self) -> str | None:
        try:
            value = self.id_factory()
        except Exception:
            return None
        return value if _valid_id(value) else None

    def _now(self) -> datetime:
        now = self.db_clock()
        if not _aware_datetime(now):
            raise ValueError("HaoAI Group result clock must be timezone-aware")
        return _as_utc(now)


def _input_shapes_match(
    claim: object,
    intent: object,
    verified_input: object,
    grant: object,
    outcome: object,
    plan: object,
    verification: object,
) -> bool:
    if not _completed_input_shapes_match(
        claim, intent, verified_input, grant, outcome
    ):
        return False
    if (
        type(plan) is not OwnedObjectPlan
        or type(verification) is not OwnedObjectVerification
        or verification.status != "verified"
        or verification.stored_media is None
    ):
        return False
    observation = outcome.observation
    stored = verification.stored_media
    return bool(
        _valid_stored_media(stored)
        and _input_plan_matches(plan, claim, intent, observation, verification)
    )


def _completed_input_shapes_match(
    claim: object,
    intent: object,
    verified_input: object,
    grant: object,
    outcome: object,
) -> bool:
    if (
        type(claim) is not ClaimAuthority
        or type(intent) is not CommittedIntent
        or type(verified_input) is not VerifiedHaoaiGroupExecutionInput
        or type(grant) not in {HaoaiGroupPollGrant, HaoaiGroupPollReplayGrant}
        or type(outcome) is not HaoaiGroupPollOutcome
        or outcome.disposition != "accepted"
        or outcome.observation is None
        or not outcome.observation_id
        or grant.claim != claim
        or grant.intent != intent
        or grant.verified_input != verified_input
    ):
        return False
    observation = outcome.observation
    return bool(
        claim.scope.operation == _OPERATION
        and claim.scope.user_id == verified_input.snapshot.grant_snapshot.actor_id
        and claim.scope.task_id == verified_input.task_id
        and intent.step_id == verified_input.step_id
        and intent.attempt_id == intent.step_id
        and intent.external_submission_id == grant.reservation.reservation.submission_id
        and observation.owner_scope == claim.scope
        and observation.intent_id == intent.external_submission_id
        and observation.attempt_id == intent.attempt_id
        and observation.external_ref == grant.external_ref
        and observation.status in {"completed", "done"}
        and isinstance(observation.payload, Mapping)
        and observation.payload.get("source_fact") == "source_completed"
        and observation.payload.get("source_status") in {"completed", "done"}
        and observation.payload.get("content_status") != "unsafe_locator"
    )


def _binding_matches(
    graph: LockedHaoaiGroupGraph,
    claim: ClaimAuthority,
    intent: CommittedIntent,
    verified_input: VerifiedHaoaiGroupExecutionInput,
    grant: HaoaiGroupPollGrant,
) -> bool:
    binding = graph.binding
    parent = graph.parent_submission
    active_submission = _active_submission(graph, intent.step_id)
    return bool(
        binding.owner_id == claim.scope.user_id
        and binding.task_id == claim.scope.task_id
        and binding.unit_id == verified_input.unit_id
        and binding.step_id == intent.step_id
        and binding.attempt_index == verified_input.attempt_index
        and binding.submission_id == parent.get("id")
        and binding.raw_key == parent.get("idempotency_key")
        and binding.operation == _OPERATION
        and binding.request_digest == intent.request_digest
        and grant.reservation.reservation.group_key == binding.group_key
        and grant.reservation.reservation.step_id == intent.step_id
        and grant.reservation.reservation.attempt_id == intent.attempt_id
        and grant.reservation.reservation.submission_id == intent.external_submission_id
        and grant.reservation.reservation.external_id == grant.external_ref.external_id
        and grant.reservation.reservation.payload_digest == intent.request_digest
        and active_submission is not None
        and active_submission.get("id") == intent.external_submission_id
        and active_submission.get("external_task_id") == grant.external_ref.external_id
        and _intent_matches_group(graph, intent, verified_input)
    )


def _intent_matches_group(
    graph: LockedHaoaiGroupGraph,
    intent: CommittedIntent,
    verified_input: VerifiedHaoaiGroupExecutionInput,
) -> bool:
    unit = graph.unit
    steps = graph.steps
    if (
        len(steps) != 3
        or verified_input.attempt_index not in (0, 1, 2)
        or steps[verified_input.attempt_index].get("id") != intent.step_id
        or unit.get("id") != verified_input.unit_id
        or not isinstance(unit.get("first_intent_at"), datetime)
        or not isinstance(unit.get("deadline_at"), datetime)
    ):
        return False
    first = _as_utc(unit["first_intent_at"])
    deadline = _as_utc(unit["deadline_at"])
    return bool(
        intent.provider == "haoai"
        and intent.model_id == graph.binding.task_model_id
        and intent.request_digest == graph.binding.request_digest
        and (
            intent.committed_at == first
            if verified_input.attempt_index == 0
            else intent.committed_at > first
        )
        and intent.deadline_at == deadline
        and intent.first_intent_at == first
        and verified_input.step_id == intent.step_id
        and verified_input.server_attempt_id == graph.binding.server_attempt_id
        and verified_input.task_id == graph.task_id
        and verified_input.unit_id == graph.unit.get("id")
        and verified_input.snapshot.model_ref.provider == "haoai"
        and verified_input.snapshot.model_ref.model_id == intent.model_id
    )


def _current_group_owner(
    graph: LockedHaoaiGroupGraph,
    claim: ClaimAuthority,
    intent: CommittedIntent,
    now: datetime,
) -> bool:
    group = graph.group
    unit = graph.unit
    return bool(
        _claim_live(graph.task, claim, now)
        and _as_utc(intent.deadline_at) > now
        and unit.get("result_status") in {"pending", "unknown"}
        and unit.get("billing_status") in {"charged", "no_charge"}
        and unit.get("refunded_amount") == 0
        and unit.get("user_cancelled_at") is None
        and _as_utc(unit["first_intent_at"]) == _as_utc(intent.first_intent_at)
        and _as_utc(unit["deadline_at"]) == _as_utc(intent.deadline_at)
        and group.get("status") in {"active", "pending_verification"}
        and group.get("active_attempt") == graph.binding.attempt_index
        and group.get("selected_evidence_id") is None
        and group.get("sticky_policy_stop_event_ref") is None
        and group.get("outstanding_reservation_ref") is None
    )


def _accepted_completed_observation(
    session: Session,
    graph: LockedHaoaiGroupGraph,
    claim: ClaimAuthority,
    intent: CommittedIntent,
    verified_input: VerifiedHaoaiGroupExecutionInput,
    grant: HaoaiGroupPollGrant,
    outcome: HaoaiGroupPollOutcome,
    authority: HmacProofAuthority | None,
) -> bool:
    reservation = grant.reservation.reservation
    if outcome.observation_id is None or outcome.observation is None:
        return False
    row = session.execute(
        select(ResultEvidenceRow.__table__).where(
            ResultEvidenceRow.__table__.c.id == outcome.observation_id,
            ResultEvidenceRow.__table__.c.evidence_key
            == f"haoai-poll-v1:{reservation.reservation_id}",
            ResultEvidenceRow.__table__.c.step_id == intent.step_id,
            ResultEvidenceRow.__table__.c.submission_id
            == intent.external_submission_id,
        )
    ).mappings().one_or_none()
    if row is None:
        return False
    fact = verified_group_observation_fact(row, authority)
    observation = outcome.observation
    observed_received_at = _as_utc(observation.received_at)
    return bool(
        fact is not None
        and outcome.disposition == "accepted"
        and row.get("source") == "worker"
        and row.get("validity") == "unknown"
        and row.get("application_status") == "pending"
        and row.get("applied_at") is None
        and row.get("result_digest") == observation_body_digest(
            str(row.get("result_location", ""))
        )
        and row.get("result_digest") == observation.payload_digest
        and fact.get("schema") == "haoai_poll_observation_v1"
        and fact.get("owner_id") == claim.scope.user_id
        and fact.get("task_id") == claim.scope.task_id
        and fact.get("unit_id") == verified_input.unit_id
        and fact.get("group_key") == reservation.group_key
        and fact.get("step_id") == intent.step_id
        and fact.get("submission_id") == intent.external_submission_id
        and fact.get("attempt_id") == intent.attempt_id
        and fact.get("external_id") == grant.external_ref.external_id
        and fact.get("request_digest") == intent.request_digest
        and fact.get("reservation_id") == reservation.reservation_id
        and fact.get("sequence") == reservation.sequence
        and fact.get("source_fact") == "source_completed"
        and fact.get("source_status") in {"completed", "done"}
        and fact.get("body_sha256") == observation.payload_digest
        and fact.get("source_fact") == observation.payload.get("source_fact")
        and fact.get("source_status") == observation.payload.get("source_status")
        and fact.get("content_status") == observation.payload.get("content_status")
        and fact.get("received_at") == observed_received_at.isoformat()
        and row.get("received_at") == _database_datetime(observed_received_at)
        and observed_received_at < _as_utc(intent.deadline_at)
        and _reservation_was_accepted(
            session, reservation, verified_input.unit_id, outcome.observation_id
        )
        and _active_submission(graph, intent.step_id) is not None
        and _active_submission(graph, intent.step_id).get("external_task_id")
        == grant.external_ref.external_id
    )


def _reservation_was_accepted(
    session: Session, reservation, unit_id: str, evidence_id: str
) -> bool:
    row = session.execute(
        select(PollReservationRow.__table__).where(
            PollReservationRow.__table__.c.reservation_id == reservation.reservation_id,
            PollReservationRow.__table__.c.unit_id == unit_id,
            PollReservationRow.__table__.c.group_key == reservation.group_key,
            PollReservationRow.__table__.c.sequence == reservation.sequence,
        )
    ).mappings().one_or_none()
    return bool(
        row is not None
        and row.get("state") == "accepted"
        and row.get("observation_ref") == evidence_id
        and row.get("step_id") == reservation.step_id
        and row.get("submission_id") == reservation.submission_id
        and row.get("external_id") == reservation.external_id
    )


def _has_group_work(session: Session, graph: LockedHaoaiGroupGraph) -> bool:
    return (
        session.execute(
            select(LocalContinuationWorkRow.work_id).where(
                LocalContinuationWorkRow.owner_id == graph.owner_id,
                LocalContinuationWorkRow.task_id == graph.task_id,
                LocalContinuationWorkRow.unit_id == graph.unit.get("id"),
            )
        ).first()
        is not None
    )


def _has_policy_stop_event(session: Session, graph: LockedHaoaiGroupGraph) -> bool:
    return (
        session.execute(
            select(PolicyStopEventRow.event_id).where(
                PolicyStopEventRow.unit_id == graph.unit.get("id"),
                PolicyStopEventRow.group_key == graph.binding.group_key,
            )
        ).first()
        is not None
    )


def _existing_receipt(
    session: Session,
    graph: LockedHaoaiGroupGraph,
    claim: ClaimAuthority,
    intent: CommittedIntent,
    verified_input: VerifiedHaoaiGroupExecutionInput,
    grant: HaoaiGroupPollGrant,
    poll_outcome: HaoaiGroupPollOutcome,
    evidence: Mapping[str, object],
    evidence_key: str,
    plan: OwnedObjectPlan,
    verification: OwnedObjectVerification,
    stored: StoredMedia,
    authority: HmacProofAuthority | None,
) -> GenerationResultReceipt | None:
    if (
        not _result_evidence_matches(evidence, stored, evidence_key)
        or not _accepted_completed_observation(
            session,
            graph,
            claim,
            intent,
            verified_input,
            grant,
            poll_outcome,
            authority,
        )
        or _has_policy_stop_event(session, graph)
    ):
        return None
    result_ref = session.execute(
        select(GenerationResultReceiptRow.result_ref).where(
            GenerationResultReceiptRow.evidence_id == evidence.get("id"),
            GenerationResultReceiptRow.owner_id == claim.scope.user_id,
            GenerationResultReceiptRow.task_id == claim.scope.task_id,
            GenerationResultReceiptRow.unit_id == verified_input.unit_id,
            GenerationResultReceiptRow.step_id == intent.step_id,
            GenerationResultReceiptRow.submission_id == intent.external_submission_id,
        )
    ).scalar_one_or_none()
    if not isinstance(result_ref, str):
        return None
    receipt = GenerationRecordRepository(session).find_result_receipt(
        claim.scope.user_id, claim.scope.task_id, result_ref
    )
    if (
        not isinstance(receipt, GenerationResultReceipt)
        or not _receipt_matches(
            receipt, stored, graph, intent, verified_input, evidence.get("id")
        )
        or not _terminal_graph_matches(
            graph,
            claim,
            intent,
            verified_input,
            evidence.get("id"),
            receipt,
            _success_result_value(stored, receipt.result_ref)
            if isinstance(receipt, GenerationResultReceipt)
            else "",
            stored,
        )
        or poll_outcome.observation is None
        or not _plan_matches(
            plan,
            claim,
            intent,
            poll_outcome.observation,
            verification,
            stored,
        )
    ):
        return None
    return receipt


def _result_evidence_matches(
    row: Mapping[str, object], stored: StoredMedia, evidence_key: str
) -> bool:
    return bool(
        row.get("evidence_key") == evidence_key
        and row.get("evidence_kind") == _RESULT_KIND
        and row.get("validity") == "valid"
        and row.get("application_status") == "pending"
        and row.get("result_location") == stored.locator
        and row.get("payload_digest") == stored.content_digest
        and row.get("result_digest") == stored.content_digest
    )


def _receipt_matches(
    receipt: GenerationResultReceipt,
    stored: StoredMedia,
    graph: LockedHaoaiGroupGraph,
    intent: CommittedIntent,
    verified_input: VerifiedHaoaiGroupExecutionInput,
    evidence_id: object,
) -> bool:
    return bool(
        receipt.owner_id == graph.owner_id
        and receipt.task_id == graph.task_id
        and receipt.unit_id == verified_input.unit_id
        and receipt.operation == _OPERATION
        and receipt.step_id == intent.step_id
        and receipt.submission_id == intent.external_submission_id
        and receipt.evidence_id == evidence_id
        and receipt.selected_attempt_id == intent.attempt_id
        and receipt.recoverable_locator == stored.locator
        and receipt.content_digest == stored.content_digest
        and receipt.validation_version == _VALIDATION_VERSION
        and receipt.late_disposition is None
        and receipt.policy_stop_disposition is None
        and receipt.automatic_application_allowed is False
    )


def _terminal_graph_matches(
    graph: LockedHaoaiGroupGraph,
    claim: ClaimAuthority,
    intent: CommittedIntent,
    verified_input: VerifiedHaoaiGroupExecutionInput,
    evidence_id: object,
    receipt: GenerationResultReceipt,
    result_value: str,
    stored: StoredMedia,
) -> bool:
    submission = _active_submission(graph, intent.step_id)
    return bool(
        graph.task.get("status") == "completed"
        and graph.task.get("result") == result_value
        and graph.unit.get("result_status") == "succeeded"
        and graph.unit.get("success_confirmed_at") == _database_datetime(
            receipt.validated_at
        )
        and graph.group.get("status") == "succeeded"
        and graph.group.get("active_attempt") == verified_input.attempt_index
        and graph.group.get("selected_evidence_id") == evidence_id
        and graph.group.get("sticky_policy_stop_event_ref") is None
        and graph.group.get("outstanding_reservation_ref") is None
        and graph.steps[verified_input.attempt_index].get("execution_status") == "applied"
        and submission is not None
        and submission.get("submission_status") == "completed"
        and submission.get("external_task_id")
        == _active_submission(graph, intent.step_id).get("external_task_id")
        and _receipt_matches(
            receipt, stored, graph, intent, verified_input, evidence_id
        )
        and claim.scope.user_id == graph.owner_id
        and claim.scope.task_id == graph.task_id
    )


def _plan_matches(
    plan: object,
    claim: ClaimAuthority,
    intent: CommittedIntent,
    observation: ProviderObservation,
    verification: OwnedObjectVerification,
    stored: StoredMedia,
) -> bool:
    if (
        type(plan) is not OwnedObjectPlan
        or type(verification) is not OwnedObjectVerification
        or verification.status != "verified"
        or verification.stored_media != stored
    ):
        return False
    expected_locator = f"{plan.public_base.rstrip('/')}/{quote(plan.object_key, safe='/')}"
    return bool(
        plan.owner_ref == claim.scope.user_id
        and plan.submission_ref == intent.external_submission_id
        and plan.slot == "primary"
        and _DIGEST.fullmatch(plan.provider_result_digest)
        and _DIGEST.fullmatch(plan.material_sha256)
        and plan.material_sha256 == stored.content_digest
        and plan.material_sha256 == verification.observed_sha256
        and plan.media_type == stored.media_type
        and plan.size_bytes == stored.size_bytes
        and plan.size_bytes == verification.observed_size_bytes
        and plan.size_bytes <= MAX_VIDEO_BYTES
        and stored.locator == expected_locator
    )


def _input_plan_matches(
    plan: OwnedObjectPlan,
    claim: ClaimAuthority,
    intent: CommittedIntent,
    observation: ProviderObservation,
    verification: OwnedObjectVerification,
) -> bool:
    return bool(
        verification.stored_media is not None
        and _plan_matches(
            plan,
            claim,
            intent,
            observation,
            verification,
            verification.stored_media,
        )
    )


def _claim_live(task: Mapping[str, object], claim: ClaimAuthority, now: datetime) -> bool:
    lease_until = task.get("lease_until")
    return bool(
        not claim.local_lost_signal.is_set()
        and task.get("status") in {"processing", "reconciling"}
        and task.get("user_cancelled_at") is None
        and task.get("claimed_by") == claim.worker_instance
        and task.get("claim_token") == claim.token
        and task.get("execution_generation") == claim.execution_generation
        and isinstance(lease_until, datetime)
        and _as_utc(lease_until) > now
    )


def _active_submission(
    graph: LockedHaoaiGroupGraph, step_id: str
) -> Mapping[str, object] | None:
    rows = [row for row in graph.external_submissions if row.get("step_id") == step_id]
    return rows[0] if len(rows) == 1 else None


def _valid_stored_media(value: object) -> bool:
    if type(value) is not StoredMedia:
        return False
    locator = value.locator
    if (
        not isinstance(locator, str)
        or not locator
        or locator != locator.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in locator)
        or "\\" in locator
    ):
        return False
    try:
        parsed = urlsplit(locator)
        _ = parsed.port
    except (TypeError, ValueError, UnicodeError):
        return False
    return bool(
        parsed.scheme in {"https", "http"}
        and parsed.hostname
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
        and isinstance(value.content_digest, str)
        and _DIGEST.fullmatch(value.content_digest)
        and isinstance(value.media_type, str)
        and value.media_type in _VIDEO_MEDIA_TYPES
        and type(value.size_bytes) is int
        and 0 < value.size_bytes <= MAX_VIDEO_BYTES
    )


def _success_result_value(stored: StoredMedia, result_ref: str) -> str:
    return json.dumps(
        {
            "kind": "video",
            "url": stored.locator,
            "result_ref": result_ref,
            "content_digest": stored.content_digest,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _project_assistant_message(session: Session, message_id: str, locator: str) -> bool:
    message = session.execute(
        select(chat_messages)
        .where(chat_messages.c.id == message_id)
        .with_for_update()
    ).mappings().one_or_none()
    if (
        message is None
        or message.get("role") != "assistant"
        or message.get("content") != "正在生成视频..."
    ):
        return False
    changed = session.execute(
        update(chat_messages)
        .where(
            chat_messages.c.id == message_id,
            chat_messages.c.role == "assistant",
            chat_messages.c.content == "正在生成视频...",
        )
        .values(content=locator)
    )
    return changed.rowcount == 1


def _valid_due_original_submission(
    graph: LockedHaoaiGroupGraph,
    step: Mapping[str, object],
    submission: Mapping[str, object],
    now: datetime,
) -> bool:
    """Require the active row to preserve the committed original intent."""

    status = submission.get("submission_status")
    if status not in {"submitting", "submitted", "unknown"}:
        return False
    step_statuses = frozenset(
        {"submitting"}
        if status == "submitting"
        else {"submitted", "reconciling", "result_observed"}
    )
    if not _known_id_shape_is_valid(
        graph,
        dict(submission),
        dict(step),
        submission_statuses=frozenset({str(status)}),
        step_statuses=step_statuses,
    ):
        return False

    first_intent = _as_utc(graph.unit.get("first_intent_at"))
    deadline = _as_utc(graph.unit.get("deadline_at"))
    intent_at = _as_utc(submission.get("intent_at"))
    row_deadline = _as_utc(submission.get("deadline_at"))
    current_time = _as_utc(now)
    if (
        first_intent is None
        or deadline is None
        or intent_at is None
        or row_deadline != deadline
        or current_time is None
        or not first_intent <= intent_at < deadline
        or intent_at > current_time
    ):
        return False

    submitted_at = _as_utc(submission.get("submitted_at"))
    external_id = submission.get("external_task_id")
    if status == "submitting":
        return submitted_at is None and external_id is None
    if (
        submitted_at is None
        or not intent_at <= submitted_at < deadline
        or submitted_at > current_time
    ):
        return False
    if status == "unknown":
        return external_id is None
    return _safe_provider_id(external_id)


def _known_failure_terminal_matches(
    session: Session,
    graph: LockedHaoaiGroupGraph,
    authority: HmacProofAuthority | None,
) -> bool:
    from haoai_backend.generation_core.haoai_group.cancellation import (
        _lock_group_events,
        _lock_group_receipts,
        _lock_group_work,
    )
    from haoai_backend.generation_core.haoai_group.claims import (
        _has_signed_failed_observation,
    )

    attempt_index = graph.group.get("active_attempt")
    if type(attempt_index) is not int or not 0 <= attempt_index < len(graph.steps):
        return False
    step = graph.steps[attempt_index]
    submissions = tuple(
        row
        for row in graph.external_submissions
        if row.get("step_id") == step.get("id")
    )
    if len(submissions) != 1:
        return False
    submission = submissions[0]
    intent_at = _as_utc(submission.get("intent_at"))
    submitted_at = _as_utc(submission.get("submitted_at"))
    row_deadline = _as_utc(submission.get("deadline_at"))
    first_intent = _as_utc(graph.unit.get("first_intent_at"))
    deadline = _as_utc(graph.unit.get("deadline_at"))
    try:
        request_shape_matches = _known_id_shape_is_valid(
            graph,
            dict(submission),
            step,
            submission_statuses=frozenset({"failed"}),
            step_statuses=frozenset({"failed"}),
        )
    except Exception:
        return False
    if (
        not request_shape_matches
        or submission.get("submission_status") != "failed"
        or not _safe_provider_id(submission.get("external_task_id"))
        or intent_at is None
        or submitted_at is None
        or row_deadline is None
        or first_intent is None
        or deadline is None
        or row_deadline != deadline
        or deadline != first_intent + timedelta(
            seconds=HAOAI_ATTEMPT_GROUP.original_deadline_seconds
        )
        or not first_intent <= intent_at <= submitted_at < deadline
        or not _has_signed_failed_observation(
            session, graph, step, authority
        )
        or graph.task.get("status") != "failed"
        or graph.task.get("result") != _PROVIDER_FAILED_RESULT
        or graph.task.get("claimed_by") is not None
        or graph.task.get("claim_token") is not None
        or graph.task.get("lease_until") is not None
        or graph.task.get("user_cancelled_at") is not None
        or graph.task.get("cancellation_reason") is not None
        or graph.unit.get("result_status") != "failed"
        or graph.unit.get("user_cancelled_at") is not None
        or graph.unit.get("success_confirmed_at") is not None
        or graph.group.get("status") != "failed"
        or graph.group.get("terminal_reason") != "provider_failed"
        or graph.group.get("selected_evidence_id") is not None
        or graph.group.get("sticky_policy_stop_event_ref") is not None
        or graph.group.get("outstanding_reservation_ref") is not None
    ):
        return False

    events = _lock_group_events(session, graph)
    receipts = _lock_group_receipts(session, graph)
    works = _lock_group_work(session, graph)
    if events is None or events or receipts is None or receipts or works is None or works:
        return False
    fee = read_verified_group_fee(session, graph, graph.binding.quoted_amount)
    if fee is None:
        return False
    expected_billing = "no_charge" if fee.amount == 0 else "refunded"
    return bool(
        fee.state == expected_billing
        and graph.task.get("billing_status") == expected_billing
        and graph.task.get("credit_cost") == fee.amount
        and graph.unit.get("billing_status") == expected_billing
        and graph.unit.get("debited_amount") == fee.amount
        and graph.unit.get("refunded_amount")
        == (fee.amount if fee.amount > 0 else 0)
    )


def _group_due_fact(
    graph: LockedHaoaiGroupGraph,
    submission: Mapping[str, object],
    now: datetime,
    original_debit_ref: str | None,
    refund_ref: str | None,
    policy_event_id: str | None,
) -> dict[str, object] | None:
    first = graph.unit.get("first_intent_at")
    deadline = graph.unit.get("deadline_at")
    first_utc = _as_utc(first) if isinstance(first, datetime) else None
    deadline_utc = _as_utc(deadline) if isinstance(deadline, datetime) else None
    if (
        first_utc is None
        or deadline_utc is None
        or deadline_utc != first_utc + timedelta(
            seconds=HAOAI_ATTEMPT_GROUP.original_deadline_seconds
        )
        or not isinstance(submission.get("id"), str)
        or not isinstance(submission.get("step_id"), str)
    ):
        return None
    external_id = submission.get("external_task_id")
    if external_id is not None and not isinstance(external_id, str):
        return None
    return {
        "schema": _DUE_EVIDENCE_KIND,
        "policy_version": HAOAI_ATTEMPT_GROUP.policy_version,
        "registry_version": HAOAI_ATTEMPT_GROUP.registry_version,
        "owner_id": graph.owner_id,
        "task_id": graph.task_id,
        "unit_id": graph.binding.unit_id,
        "group_key": graph.binding.group_key,
        "step_id": submission["step_id"],
        "submission_id": submission["id"],
        "external_id": external_id,
        "request_digest": graph.binding.request_digest,
        "first_intent_at": first_utc.isoformat(),
        "deadline_at": deadline_utc.isoformat(),
        "settled_at": _as_utc(now).isoformat(),
        "provider_truth": "unknown",
        "quoted_amount": graph.binding.quoted_amount,
        "billing_status": graph.unit.get("billing_status"),
        "original_debit_ref": original_debit_ref,
        "refund_ref": refund_ref,
        "policy_event_id": policy_event_id,
    }


def _existing_due_matches(
    session: Session,
    graph: LockedHaoaiGroupGraph,
    events: list[Mapping[str, object]],
    evidence: Mapping[str, object],
    authority: HmacProofAuthority | None,
) -> bool:
    if (
        type(authority) is not HmacProofAuthority
        or evidence.get("source") != "recovery"
        or evidence.get("evidence_kind") != _DUE_EVIDENCE_KIND
        or evidence.get("evidence_key") != _DUE_EVIDENCE_KEY + graph.binding.unit_id
        or evidence.get("validity") != "unknown"
        or evidence.get("application_status") != "pending"
        or evidence.get("applied_at") is not None
        or evidence.get("result_digest") is not None
        or not isinstance(evidence.get("result_location"), str)
    ):
        return False
    try:
        envelope = json.loads(str(evidence["result_location"]))
        if not isinstance(envelope, dict):
            return False
        fact = envelope.get("fact")
        proof = envelope.get("proof")
        canonical = _canonical_json(fact)
        if (
            not isinstance(fact, dict)
            or not isinstance(proof, dict)
            or hashlib.sha256(canonical).hexdigest()
            != evidence.get("payload_digest")
            or proof.get("key_id") != authority.key_id
            or not authority.verify(
                _DUE_PURPOSE, canonical, proof.get("signature")
            )
        ):
            return False
        first = graph.unit.get("first_intent_at")
        deadline = graph.unit.get("deadline_at")
        first_utc = _as_utc(first) if isinstance(first, datetime) else None
        deadline_utc = _as_utc(deadline) if isinstance(deadline, datetime) else None
        if (
            first_utc is None
            or deadline_utc is None
            or deadline_utc != first_utc + timedelta(
                seconds=HAOAI_ATTEMPT_GROUP.original_deadline_seconds
            )
            or evidence.get("step_id") != fact.get("step_id")
            or evidence.get("submission_id") != fact.get("submission_id")
            or fact.get("schema") != _DUE_EVIDENCE_KIND
            or fact.get("policy_version") != HAOAI_ATTEMPT_GROUP.policy_version
            or fact.get("registry_version") != HAOAI_ATTEMPT_GROUP.registry_version
            or fact.get("owner_id") != graph.owner_id
            or fact.get("task_id") != graph.task_id
            or fact.get("unit_id") != graph.binding.unit_id
            or fact.get("group_key") != graph.binding.group_key
            or fact.get("request_digest") != graph.binding.request_digest
            or fact.get("first_intent_at") != first_utc.isoformat()
            or fact.get("deadline_at") != deadline_utc.isoformat()
            or fact.get("provider_truth") != "unknown"
            or fact.get("quoted_amount") != graph.binding.quoted_amount
            or graph.task.get("status") != "failed"
            or graph.unit.get("result_status") != "unknown"
            or graph.unit.get("user_cancelled_at") is not None
            or graph.group.get("outstanding_reservation_ref") is not None
        ):
            return False
        fee = read_verified_group_fee(
            session, graph, graph.binding.quoted_amount
        )
        if (
            fee is None
            or fee.state not in {"no_charge", "refunded"}
            or fact.get("billing_status") != graph.unit.get("billing_status")
            or fact.get("original_debit_ref") != fee.original_debit_ref
            or fact.get("refund_ref") != fee.refund_ref
        ):
            return False
        policy_event_id = fact.get("policy_event_id")
        if policy_event_id is None:
            return not events and (
                graph.group.get("status") == "failed"
                and graph.group.get("terminal_reason")
                == "original_deadline_expired"
            )
        return bool(
            len(events) == 1
            and events[0].get("event_id") == policy_event_id
            and graph.group.get("status") == "policy_stopped"
            and graph.group.get("sticky_policy_stop_event_ref") == policy_event_id
            and events[0].get("financial_disposition") in {"refunded", "no_debit"}
            and verify_policy_stop_event(
                session,
                graph,
                events[0],
                proof_authority=authority,
                verified_quote=graph.binding.quoted_amount,
            )
        )
    except (KeyError, TypeError, ValueError, UnicodeError):
        return False


def _late_stored_media_matches(
    plan: OwnedObjectPlan,
    verification: OwnedObjectVerification,
    stored: StoredMedia,
    candidate: object,
) -> bool:
    from .late import HaoaiGroupLateCandidate

    if type(candidate) is not HaoaiGroupLateCandidate:
        return False
    observation = candidate.poll_outcome.observation
    expected_locator = f"{plan.public_base.rstrip('/')}/{quote(plan.object_key, safe='/')}"
    return bool(
        type(plan) is OwnedObjectPlan
        and type(verification) is OwnedObjectVerification
        and verification.status == "verified"
        and verification.stored_media == stored
        and observation is not None
        and plan.owner_ref == candidate.controlled_scope.owner_scope.user_id
        and plan.submission_ref == candidate.intent.external_submission_id
        and plan.slot == "primary"
        and plan.provider_result_digest == observation.payload_digest
        and plan.material_sha256 == stored.content_digest
        and plan.media_type == stored.media_type
        and plan.size_bytes == stored.size_bytes
        and plan.size_bytes <= MAX_VIDEO_BYTES
        and stored.locator == expected_locator
        and verification.observed_sha256 == plan.material_sha256
        and verification.observed_size_bytes == plan.size_bytes
    )


def _stored_media_from_late_plan(plan: OwnedObjectPlan) -> StoredMedia | None:
    if type(plan) is not OwnedObjectPlan:
        return None
    try:
        locator = f"{plan.public_base.rstrip('/')}/{quote(plan.object_key, safe='/')}"
        return StoredMedia(
            locator=locator,
            content_digest=plan.material_sha256,
            media_type=plan.media_type,
            size_bytes=plan.size_bytes,
        )
    except (AttributeError, TypeError, ValueError, UnicodeError):
        return None


def _late_generation_projection(
    receipt: GenerationResultReceipt,
    candidate: object,
    stored: StoredMedia,
) -> dict[str, object]:
    from .late import HaoaiGroupLateCandidate

    if type(candidate) is not HaoaiGroupLateCandidate:
        raise TypeError("late receipt requires its original verified Group candidate")
    return {
        "version": "haoai-group-late-generation-v1",
        "result_ref": receipt.result_ref,
        "disposition": "stopped_late_generation",
        "financial_disposition": candidate.financial_disposition,
        "recoverable_locator": stored.locator,
        "content_digest": stored.content_digest,
        "media_type": stored.media_type,
        "size_bytes": stored.size_bytes,
        "validated_at": _as_utc(receipt.validated_at).isoformat(),
        "automatic_application_allowed": False,
    }


def _append_late_generation(
    original_result: object,
    projection: Mapping[str, object],
) -> str | None:
    parsed = _strict_result_object(original_result)
    if parsed is None:
        document: dict[str, object] = {
            "original_result": original_result,
            "late_generation": dict(projection),
        }
    else:
        existing = parsed.get("late_generation")
        if existing is not None:
            if existing != dict(projection):
                return None
            return original_result if isinstance(original_result, str) else None
        document = {**parsed, "late_generation": dict(projection)}
    try:
        return json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, UnicodeError):
        return None


def _strict_result_object(value: object) -> dict[str, object] | None:
    if not isinstance(value, str):
        return None

    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate result key")
            result[key] = item
        return result

    try:
        parsed = json.loads(
            value,
            object_pairs_hook=unique,
            parse_constant=_reject_result_constant,
        )
    except (TypeError, ValueError, UnicodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _reject_result_constant(value: str) -> None:
    raise ValueError(f"non-finite result value: {value}")


def _late_receipt_from_rows(
    session: Session,
    graph: LockedHaoaiGroupGraph,
    candidate: object,
    evidence: Mapping[str, object],
    row: Mapping[str, object],
    stored: StoredMedia | None,
) -> GenerationResultReceipt | None:
    from .late import HaoaiGroupLateCandidate

    if (
        type(candidate) is not HaoaiGroupLateCandidate
        or stored is None
        or evidence.get("evidence_key")
        != _LATE_RESULT_KEY_PREFIX + candidate.intent.external_submission_id
        or evidence.get("source") != "recovery"
        or evidence.get("evidence_kind") != _LATE_RESULT_KIND
        or evidence.get("validity") != "valid"
        or evidence.get("application_status") != "pending"
        or evidence.get("applied_at") is not None
        or evidence.get("step_id") != candidate.intent.step_id
        or evidence.get("submission_id") != candidate.intent.external_submission_id
        or evidence.get("payload_digest") != stored.content_digest
        or evidence.get("result_location") != stored.locator
        or evidence.get("result_digest") != stored.content_digest
        or row.get("owner_id") != graph.owner_id
        or row.get("task_id") != graph.task_id
        or row.get("unit_id") != candidate.verified_input.unit_id
        or row.get("operation") != _OPERATION
        or row.get("step_id") != candidate.intent.step_id
        or row.get("submission_id") != candidate.intent.external_submission_id
        or row.get("evidence_id") != evidence.get("id")
        or row.get("selected_attempt_id") != candidate.intent.attempt_id
        or row.get("recoverable_locator") != stored.locator
        or row.get("content_digest") != stored.content_digest
        or row.get("validation_version") != _LATE_VALIDATION_VERSION
        or row.get("late_disposition") != "stopped_late_generation"
        or row.get("policy_stop_disposition") != candidate.financial_disposition
        or row.get("automatic_application_allowed") is not False
    ):
        return None
    validated_at = _as_utc(row.get("validated_at"))
    fulfilled_at = _as_utc(row.get("generation_fulfilled_at"))
    observation = candidate.poll_outcome.observation
    if (
        validated_at is None
        or observation is None
        or fulfilled_at != _as_utc(observation.received_at)
        or not _valid_id(row.get("result_ref"))
    ):
        return None
    return GenerationResultReceipt(
        result_ref=str(row["result_ref"]),
        owner_id=graph.owner_id,
        task_id=graph.task_id,
        unit_id=candidate.verified_input.unit_id,
        operation=_OPERATION,
        step_id=candidate.intent.step_id,
        submission_id=candidate.intent.external_submission_id,
        evidence_id=str(evidence["id"]),
        selected_attempt_id=candidate.intent.attempt_id,
        recoverable_locator=stored.locator,
        content_digest=stored.content_digest,
        validation_version=_LATE_VALIDATION_VERSION,
        validated_at=validated_at,
        generation_fulfilled_at=fulfilled_at,
        late_disposition="stopped_late_generation",
        policy_stop_disposition=candidate.financial_disposition,
        automatic_application_allowed=False,
    )


def _task_result_has_late_projection(
    task_result: object,
    receipt: GenerationResultReceipt,
    candidate: object,
    stored: StoredMedia,
) -> bool:
    result = _strict_result_object(task_result)
    return bool(
        result is not None
        and result.get("late_generation")
        == _late_generation_projection(receipt, candidate, stored)
    )


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _valid_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= 36
        and not any(ord(char) < 32 or ord(char) == 127 for char in value)
    )


def _aware_datetime(value: object) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _database_datetime(value: datetime) -> datetime:
    return _as_utc(value).replace(tzinfo=None)


def _new_uuid_id() -> str:
    return str(uuid.uuid4())


__all__ = ("HaoaiGroupResultOutcome", "SqlAlchemyHaoaiGroupResults")
