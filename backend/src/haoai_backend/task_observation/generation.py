"""Same-Session readers and composition for generation result applications."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Event
from typing import Literal, TypeAlias
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.errors import GenerationError
from haoai_backend.generation_contracts.ports import CallerTransaction
from haoai_backend.generation_contracts.records import ApplicationRecord, GenerationResultReceipt, LocalContinuationWork
from haoai_backend.generation_contracts.types import (
    ApplicationOutcome, CanvasApplyAuthority, ClaimAuthority,
    ContinuationClaimAuthority, GenerationActor, GenerationApplicationCommand,
    TargetSnapshot, TextResult, ValidatedGenerationResult, WebStreamAuthority,
)
from haoai_backend.generation_core.base_schema import (
    AITaskRow, BillingUnitRow, ExecutionStepRow, ExternalSubmissionRow,
    ResultEvidenceRow, UserRow,
)
from haoai_backend.generation_core.persistence import GenerationRecordRepository
from haoai_backend.generation_core.schema import (
    ApplicationRecordRow, GenerationResultReceiptRow, LocalContinuationWorkRow,
)
from haoai_backend.series_access.application import require_series_access
from haoai_backend.series_access.domain import ActorIdentity
from haoai_backend.series_access.errors import SeriesAccessDenied, SeriesNotFound
from haoai_backend.series_access.persistence import SqlAlchemySeriesAccessReader

WorkerAuthority: TypeAlias = ClaimAuthority | ContinuationClaimAuthority
ResultLookup: TypeAlias = GenerationResultReceipt | Literal["not_found", "unready"]
_STAGED_APPLICATIONS_KEY = "haoai_generation_staged_application_refs"


def target_scope_for_snapshot(target: TargetSnapshot) -> str:
    if not target.series_id:
        raise ValueError("generation application target requires a series id")
    if target.kind in {"frame_text", "canvas_frame_preview", "frame_message"}:
        if not target.chapter_id:
            raise ValueError("frame application target requires a chapter id")
        identity = (
            f"storyboard|{target.stable_storyboard_asset_id}"
            if target.stable_storyboard_asset_id
            else f"index|{target.compat_frame_index}"
        )
        if identity.endswith("|None"):
            raise ValueError("frame application target requires a stable id or index")
        scope = f"{target.kind}|{target.series_id}|chapter|{target.chapter_id}|{identity}"
    elif target.kind == "canvas_asset_image" and target.asset_type and target.asset_id:
        scope = f"{target.kind}|{target.series_id}|asset|{target.asset_type}|{target.asset_id}"
    else:
        raise ValueError("unsupported generation application target")
    if len(scope) > 255:
        raise ValueError("generation application target scope exceeds its storage limit")
    return scope


def expected_target_digest(target: TargetSnapshot) -> str:
    digest = (
        target.full_frame_digest
        if target.kind in {"frame_text", "canvas_frame_preview", "frame_message"}
        else target.same_series_target_digest
        if target.kind == "canvas_asset_image"
        else None
    )
    if not digest:
        raise ValueError("generation application target is missing its full snapshot digest")
    return digest


def load_owned_completed_result(
    shared_tx: CallerTransaction, actor: GenerationActor, task_id: str, result_ref: str
) -> ResultLookup:
    """Load a completed receipt only after checking every original evidence link."""
    session = _require_session(shared_tx)
    if actor.user_id != actor.trusted_account_identity.user_id:
        return "not_found"
    session.execute(select(UserRow.id).where(UserRow.id == actor.user_id).with_for_update()).scalar_one_or_none()
    task = session.execute(
        select(AITaskRow).where(AITaskRow.id == task_id).with_for_update().execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if task is None or task.user_id != actor.user_id:
        return "not_found"
    if task.status != "completed":
        return "unready"
    probe = session.execute(
        select(GenerationResultReceiptRow).where(
            GenerationResultReceiptRow.result_ref == result_ref,
            GenerationResultReceiptRow.owner_id == actor.user_id,
            GenerationResultReceiptRow.task_id == task_id,
        )
    ).scalar_one_or_none()
    if probe is None:
        return "not_found"
    session.execute(select(BillingUnitRow.id).where(
        BillingUnitRow.id == probe.unit_id, BillingUnitRow.task_id == task_id,
    ).with_for_update()).scalar_one_or_none()
    row = session.execute(
        select(GenerationResultReceiptRow).where(
            GenerationResultReceiptRow.result_ref == result_ref,
            GenerationResultReceiptRow.owner_id == actor.user_id,
            GenerationResultReceiptRow.task_id == task_id,
            GenerationResultReceiptRow.unit_id == probe.unit_id,
        ).with_for_update()
    ).scalar_one_or_none()
    if row is None:
        return "unready"
    return _load_bound_receipt(session, task, row, require_succeeded=True) or "unready"


def load_validated_worker_result(
    shared_tx: CallerTransaction,
    authority: WorkerAuthority,
    validated_result: ValidatedGenerationResult,
    target: TargetSnapshot,
) -> tuple[GenerationResultReceipt, LocalContinuationWork]:
    """Validate current worker fence, persisted receipt, target and delivery phase."""
    session = _require_session(shared_tx)
    scope, token, worker, generation, work_id, version = _authority_facts(authority)
    if not scope.user_id or not scope.task_id or not scope.operation:
        raise GenerationError("ownership_lost", "generation worker authority is incomplete")
    session.execute(select(UserRow.id).where(UserRow.id == scope.user_id).with_for_update()).scalar_one_or_none()
    task = session.execute(
        select(AITaskRow).where(AITaskRow.id == scope.task_id).with_for_update().execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if task is None or task.user_id != scope.user_id or task.status != "processing":
        raise GenerationError("ownership_lost", "generation task is not owned and processing")
    if task.type != _operation_task_type(scope.operation):
        raise GenerationError("ownership_lost", "generation operation does not match the task")
    if (
        task.claimed_by != worker or task.claim_token != token
        or task.execution_generation != generation or task.lease_until is None
        or not _same_instant(task.lease_until, authority.lease_until)
        or _as_utc(task.lease_until) <= _database_now(session)
        or task.user_cancelled_at is not None
    ):
        raise GenerationError("ownership_lost", "generation worker fence is no longer current")
    if isinstance(authority, ClaimAuthority) and authority.local_lost_signal.is_set():
        raise GenerationError("ownership_lost", "generation worker has lost its local lease")
    if not target.series_id or not _has_series_access(session, scope.user_id, target.series_id):
        raise GenerationError("target_conflict_deleted_stale", "current target access is unavailable")

    result_probe = session.execute(select(GenerationResultReceiptRow).where(
        GenerationResultReceiptRow.result_ref == validated_result.result_ref,
        GenerationResultReceiptRow.owner_id == scope.user_id,
        GenerationResultReceiptRow.task_id == task.id,
        GenerationResultReceiptRow.operation == scope.operation,
    )).scalar_one_or_none()
    if result_probe is None:
        raise GenerationError("evidence_conflict_or_invalid", "validated result has no canonical owner receipt")
    unit = session.execute(select(BillingUnitRow).where(
        BillingUnitRow.id == result_probe.unit_id, BillingUnitRow.task_id == task.id,
    ).with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
    if unit is None or unit.operation != scope.operation or unit.user_cancelled_at is not None:
        raise GenerationError("ownership_lost", "generation billing unit scope is unavailable")
    result_row = session.execute(select(GenerationResultReceiptRow).where(
        GenerationResultReceiptRow.result_ref == validated_result.result_ref,
        GenerationResultReceiptRow.owner_id == scope.user_id,
        GenerationResultReceiptRow.task_id == task.id,
        GenerationResultReceiptRow.unit_id == unit.id,
        GenerationResultReceiptRow.operation == scope.operation,
    ).with_for_update()).scalar_one_or_none()
    if result_row is None:
        raise GenerationError("evidence_conflict_or_invalid", "canonical result changed while binding its unit")
    step_for_target = session.get(ExecutionStepRow, result_row.step_id)
    submission_for_target = session.get(ExternalSubmissionRow, result_row.submission_id)
    if step_for_target is None or submission_for_target is None or not _stored_target_matches(
        step_for_target, submission_for_target, task.request_data, target
    ):
        raise GenerationError("evidence_conflict_or_invalid", "target differs from the frozen admission snapshot")
    receipt = _load_bound_receipt(session, task, result_row, require_succeeded=False)
    if receipt is None or not _validated_output_matches(validated_result, receipt):
        raise GenerationError("evidence_conflict_or_invalid", "validated output differs from canonical evidence")
    if not receipt.automatic_application_allowed or receipt.late_disposition or receipt.policy_stop_disposition:
        raise GenerationError("target_conflict_deleted_stale", "result policy does not permit automatic application")

    work_query = select(LocalContinuationWorkRow).where(
        LocalContinuationWorkRow.owner_id == scope.user_id,
        LocalContinuationWorkRow.task_id == task.id,
        LocalContinuationWorkRow.unit_id == unit.id,
        LocalContinuationWorkRow.result_ref == receipt.result_ref,
        LocalContinuationWorkRow.policy_version == "local_continuation_v1",
        LocalContinuationWorkRow.operation == receipt.operation,
    )
    if work_id is not None:
        work_query = work_query.where(LocalContinuationWorkRow.work_id == work_id)
    work_row = session.execute(
        work_query.with_for_update().execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if (
        work_row is None or work_row.result_ref != receipt.result_ref
        or work_row.content_digest != receipt.content_digest
        or not _same_instant(work_row.generation_fulfilled_at, receipt.generation_fulfilled_at)
        or not _work_authorizes_target(work_row, target)
    ):
        raise GenerationError("continuation_phase_or_fence_conflict", "result delivery phase is not current")

    work = GenerationRecordRepository(session).find_continuation_work(scope.user_id, task.id, work_row.work_id)
    if work is None:
        raise GenerationError("continuation_phase_or_fence_conflict", "continuation work is not owner-bound")

    if work.phase == "delivery_pending":
        if version is not None and work.phase_version != version:
            raise GenerationError("continuation_phase_or_fence_conflict", "continuation phase version is no longer current")
    else:
        replay = current_target_application(
            session, receipt, target, expected_target_digest(target)
        )
        delivery_index = work.phase_order.index("delivery_pending") if "delivery_pending" in work.phase_order else -1
        following_phases = work.phase_order[delivery_index + 1:] if delivery_index >= 0 else ()
        replay_is_current = (
            replay is not None
            and replay.kind in {"applied", "already_applied"}
            and work.application_ref == replay.application_ref
            and work.phase in following_phases
        )
        if isinstance(authority, ContinuationClaimAuthority):
            next_phase = following_phases[0] if following_phases else None
            replay_is_current = (
                replay_is_current
                and work.phase == next_phase
                and work.phase_version == authority.phase_version + 1
            )
        if not replay_is_current:
            raise GenerationError("continuation_phase_or_fence_conflict", "continuation phase is no longer authorized")
    return receipt, work


def confirm_worker_fence_after_target_lock(
    shared_tx: CallerTransaction,
    authority: WorkerAuthority,
    *,
    receipt: GenerationResultReceipt | None = None,
    target: TargetSnapshot | None = None,
    _local_stop_signal: Event | None = None,
) -> None:
    """Recheck the live worker fence and exact delivery state after target locks."""
    session = _require_session(shared_tx)
    scope, token, worker, generation, work_id, version = _authority_facts(authority)
    task = session.execute(
        select(AITaskRow).where(AITaskRow.id == scope.task_id).with_for_update().execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if (
        task is None or task.user_id != scope.user_id or task.status != "processing"
        or task.claimed_by != worker or task.claim_token != token
        or task.execution_generation != generation or task.lease_until is None
        or not _same_instant(task.lease_until, authority.lease_until)
        or task.type != _operation_task_type(scope.operation)
        or _as_utc(task.lease_until) <= _fresh_database_time(session)
        or task.user_cancelled_at is not None
    ):
        raise GenerationError("ownership_lost", "worker fence expired while waiting for the target lock")
    if isinstance(authority, ClaimAuthority) and authority.local_lost_signal.is_set():
        raise GenerationError("ownership_lost", "worker lost its local lease while waiting for the target lock")
    if _local_stop_signal is not None and _local_stop_signal.is_set():
        raise GenerationError("ownership_lost", "continuation worker was stopped while waiting for target locks")
    if work_id is None:
        return
    if not isinstance(authority, ContinuationClaimAuthority):
        raise GenerationError("continuation_phase_or_fence_conflict", "continuation authority is invalid")

    unit = session.execute(
        select(BillingUnitRow).where(
            BillingUnitRow.id == authority.unit_id,
            BillingUnitRow.task_id == scope.task_id,
        ).with_for_update().execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if unit is None or unit.operation != scope.operation or unit.user_cancelled_at is not None:
        raise GenerationError("ownership_lost", "billing unit fence changed while waiting for the target lock")

    work_row = session.execute(
        select(LocalContinuationWorkRow).where(
            LocalContinuationWorkRow.work_id == work_id,
            LocalContinuationWorkRow.owner_id == scope.user_id,
            LocalContinuationWorkRow.task_id == scope.task_id,
            LocalContinuationWorkRow.unit_id == authority.unit_id,
            LocalContinuationWorkRow.result_ref == authority.result_ref,
            LocalContinuationWorkRow.operation == scope.operation,
            LocalContinuationWorkRow.policy_version == "local_continuation_v1",
        ).with_for_update().execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if work_row is None:
        raise GenerationError("continuation_phase_or_fence_conflict", "continuation work scope changed while waiting for the target lock")

    if receipt is None or target is None:
        if work_row.phase == "delivery_pending" and work_row.phase_version == version:
            _confirm_fresh_worker_fence(session, authority, _local_stop_signal)
            return
        raise GenerationError("continuation_phase_or_fence_conflict", "committed continuation replay requires its original receipt and target")
    if (
        receipt.owner_id != scope.user_id or receipt.task_id != scope.task_id
        or receipt.unit_id != authority.unit_id or receipt.operation != scope.operation
        or receipt.result_ref != authority.result_ref
        or work_row.content_digest != receipt.content_digest
        or not _same_instant(work_row.generation_fulfilled_at, receipt.generation_fulfilled_at)
        or not _work_authorizes_target(work_row, target)
    ):
        raise GenerationError("continuation_phase_or_fence_conflict", "continuation work is not bound to the original result and target")

    expected_phase_order = {
        "frame.review": ("delivery_pending", "fee_pending", "finalize_pending"),
        "frame.optimize": ("fee_pending", "delivery_pending", "finalize_pending"),
    }.get(scope.operation)
    if expected_phase_order is None or tuple(work_row.phase_order or ()) != expected_phase_order:
        raise GenerationError("continuation_phase_or_fence_conflict", "continuation phase order is not the frozen operation order")

    try:
        old_digest = expected_target_digest(target)
    except ValueError as error:
        raise GenerationError("continuation_phase_or_fence_conflict", "continuation target digest is invalid") from error
    replay = current_target_application(session, receipt, target, old_digest)

    if work_row.phase == "delivery_pending":
        if (
            work_row.phase_version != version or work_row.application_ref is not None
            or replay is not None
        ):
            raise GenerationError("continuation_phase_or_fence_conflict", "first delivery is no longer at its original pending phase")
        return

    delivery_index = expected_phase_order.index("delivery_pending")
    next_phase = expected_phase_order[delivery_index + 1]
    if (
        work_row.phase != next_phase or work_row.phase_version != version + 1
        or work_row.last_execution_generation != generation
        or work_row.application_ref is None or replay is None
        or replay.kind not in {"applied", "already_applied"}
        or replay.application_ref != work_row.application_ref
    ):
        raise GenerationError("continuation_phase_or_fence_conflict", "continuation replay is not bound to its exact next phase and application")
    _confirm_fresh_worker_fence(session, authority, _local_stop_signal)


def _confirm_fresh_worker_fence(
    session: Session,
    authority: WorkerAuthority,
    local_stop_signal: Event | None,
) -> None:
    """Recheck the task fence after continuation-specific locks and reads."""
    scope, token, worker, generation, _, _ = _authority_facts(authority)
    task = session.execute(
        select(AITaskRow)
        .where(AITaskRow.id == scope.task_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if (
        task is None
        or task.user_id != scope.user_id
        or task.status != "processing"
        or task.claimed_by != worker
        or task.claim_token != token
        or task.execution_generation != generation
        or task.lease_until is None
        or not _same_instant(task.lease_until, authority.lease_until)
        or _as_utc(task.lease_until) <= _fresh_database_time(session)
        or task.user_cancelled_at is not None
    ):
        raise GenerationError("ownership_lost", "worker fence expired before continuation delivery completed")
    if isinstance(authority, ClaimAuthority) and authority.local_lost_signal.is_set():
        raise GenerationError("ownership_lost", "worker lost its local lease before delivery completed")
    if local_stop_signal is not None and local_stop_signal.is_set():
        raise GenerationError("ownership_lost", "continuation worker was stopped before delivery completed")


def find_application_record(
    shared_tx: CallerTransaction, owner_id: str, task_id: str, result_ref: str, target_scope: str
) -> ApplicationRecord | None:
    return GenerationRecordRepository(_require_session(shared_tx)).find_application_record(
        owner_id, task_id, result_ref, target_scope
    )


def stage_application_record(
    shared_tx: CallerTransaction,
    receipt: GenerationResultReceipt,
    target_scope: str,
    before_target_digest: str,
    after_target_digest: str,
    *,
    media_revision_before: int | None = None,
    media_revision_after: int | None = None,
    notes_revision_before: int | None = None,
    notes_revision_after: int | None = None,
) -> ApplicationRecord:
    session = _require_session(shared_tx)
    record = ApplicationRecord(
        application_ref=application_ref_for(receipt.result_ref, target_scope),
        result_ref=receipt.result_ref, owner_id=receipt.owner_id, task_id=receipt.task_id,
        unit_id=receipt.unit_id, target_scope=target_scope, result_digest=receipt.content_digest,
        delivery_status="applied", before_target_digest=before_target_digest,
        after_target_digest=after_target_digest, media_revision_before=media_revision_before,
        media_revision_after=media_revision_after, notes_revision_before=notes_revision_before,
        notes_revision_after=notes_revision_after, committed_at=_database_now(session),
    )
    staged = GenerationRecordRepository(session).stage_record(record)
    if staged.status != "created":
        raise GenerationError("target_conflict_deleted_stale", "application record key is already bound")
    staged_by_transaction = session.info.setdefault(_STAGED_APPLICATIONS_KEY, {})
    transaction = session.get_transaction()
    if transaction is None:
        raise GenerationError("target_conflict_deleted_stale", "application insert has no caller transaction")
    staged_by_transaction[record.application_ref] = transaction
    return record


def application_ref_for(result_ref: str, target_scope: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"haoai-generation-application:{result_ref}:{target_scope}"))


def application_key_for(result_ref: str, target_scope: str) -> str:
    return hashlib.sha256(f"{result_ref}\x1f{target_scope}".encode("utf-8")).hexdigest()


def current_target_application(
    shared_tx: CallerTransaction, receipt: GenerationResultReceipt, target: TargetSnapshot,
    expected_digest: str, application_key: str | None = None,
) -> ApplicationOutcome | None:
    session = _require_session(shared_tx)
    scope = target_scope_for_snapshot(target)
    if application_key is not None and application_key != application_key_for(receipt.result_ref, scope):
        return ApplicationOutcome("target_conflict", receipt.result_ref, None, None, "application key does not match result and target")
    row = session.execute(select(ApplicationRecordRow).where(
        ApplicationRecordRow.result_ref == receipt.result_ref,
        ApplicationRecordRow.target_scope == scope,
    ).with_for_update()).scalar_one_or_none()
    if row is None:
        return None
    exact = (
        row.application_ref == application_ref_for(receipt.result_ref, scope)
        and row.owner_id == receipt.owner_id and row.task_id == receipt.task_id
        and row.unit_id == receipt.unit_id and row.result_digest == receipt.content_digest
        and row.delivery_status == "applied" and row.before_target_digest == expected_digest
        and bool(row.after_target_digest) and row.committed_at is not None
    )
    if not exact:
        return ApplicationOutcome("target_conflict", receipt.result_ref, row.application_ref, None, "result already has a different target application")
    staged_by_transaction = session.info.get(_STAGED_APPLICATIONS_KEY, {})
    kind = (
        "applied"
        if session.get_transaction() is staged_by_transaction.get(row.application_ref)
        else "already_applied"
    )
    return ApplicationOutcome(kind, receipt.result_ref, row.application_ref, row.after_target_digest)


def load_owned_completed_for_canvas(
    shared_tx: CallerTransaction, authority: CanvasApplyAuthority, command: GenerationApplicationCommand
) -> tuple[GenerationResultReceipt | None, ApplicationOutcome | None]:
    session = _require_session(shared_tx)
    actor = authority.actor
    if (
        actor.user_id != actor.trusted_account_identity.user_id
        or command.task_id != authority.task_id or command.result_ref != authority.result_ref
        or command.expected_target != authority.target
        or authority.operation not in {"image.single", "image.batch"}
        or command.expected_target.kind not in {"canvas_frame_preview", "canvas_asset_image"}
        or not command.expected_target.series_id
        or not _has_series_access(session, actor.user_id, command.expected_target.series_id)
    ):
        return None, ApplicationOutcome("write_failed", authority.result_ref, None, None, "canvas result authority or current access is invalid")
    result = load_owned_completed_result(session, actor, authority.task_id, authority.result_ref)
    if result == "not_found":
        return None, ApplicationOutcome("write_failed", authority.result_ref, None, None, "owned completed result was not found")
    if result == "unready":
        return None, ApplicationOutcome("write_failed", authority.result_ref, None, None, "generation result is not ready for application")
    if (
        result.operation != authority.operation
        or result.content_digest != authority.result_digest
        or not target_matches_receipt(session, result, command.expected_target)
    ):
        return None, ApplicationOutcome("write_failed", result.result_ref, None, None, "result operation, digest or target differs from the frozen admission")
    return result, None


def target_matches_receipt(
    shared_tx: CallerTransaction, receipt: GenerationResultReceipt, target: TargetSnapshot
) -> bool:
    session = _require_session(shared_tx)
    row = session.execute(select(GenerationResultReceiptRow).where(
        GenerationResultReceiptRow.result_ref == receipt.result_ref,
        GenerationResultReceiptRow.owner_id == receipt.owner_id,
        GenerationResultReceiptRow.task_id == receipt.task_id,
        GenerationResultReceiptRow.unit_id == receipt.unit_id,
    )).scalar_one_or_none()
    if row is None:
        return False
    step = session.get(ExecutionStepRow, row.step_id)
    submission = session.get(ExternalSubmissionRow, row.submission_id)
    task = session.execute(
        select(AITaskRow).where(AITaskRow.id == row.task_id).with_for_update().execution_options(populate_existing=True)
    ).scalar_one_or_none()
    return bool(
        step is not None and submission is not None and task is not None
        and _stored_target_matches(step, submission, task.request_data, target)
    )


class GenerationResultApplicationFacade:
    """P5 adapter bound to the caller's single request Session."""

    def __init__(self, session: Session) -> None:
        if not isinstance(session, Session):
            raise TypeError("session must be the caller-owned SQLAlchemy Session")
        self._session = session

    def apply_worker_result(self, tx: CallerTransaction, authority: WorkerAuthority,
                            validated_result: ValidatedGenerationResult, target: TargetSnapshot) -> ApplicationOutcome:
        session = self._bound_session(tx)
        if target.kind == "frame_text":
            from haoai_backend.series_data import stage_frame_result
            return stage_frame_result(session, authority, validated_result, target)
        return ApplicationOutcome("write_failed", validated_result.result_ref, None, None, "worker result cannot apply to this target")

    def apply_web_text(self, tx: CallerTransaction, authority: WebStreamAuthority,
                       text_result: TextResult) -> ApplicationOutcome:
        from haoai_backend.chat_data import apply_web_text
        return apply_web_text(self._bound_session(tx), authority, text_result)

    def apply_canvas_media(self, shared_tx: CallerTransaction, authority: CanvasApplyAuthority,
                           command: GenerationApplicationCommand) -> ApplicationOutcome:
        session = self._bound_session(shared_tx)
        receipt, failure = load_owned_completed_for_canvas(session, authority, command)
        if failure is not None:
            return failure
        assert receipt is not None
        try:
            scope = target_scope_for_snapshot(command.expected_target)
            expected_target_digest(command.expected_target)
        except ValueError as exc:
            return ApplicationOutcome("write_failed", receipt.result_ref, None, None, str(exc))
        if command.application_key != application_key_for(receipt.result_ref, scope):
            return ApplicationOutcome("target_conflict", receipt.result_ref, None, None, "application key does not match result and target")
        if command.expected_target.kind == "canvas_frame_preview":
            from haoai_backend.series_data import stage_frame_preview
            return stage_frame_preview(session, authority, receipt, command.expected_target)
        from haoai_backend.asset_data import stage_asset_image
        return stage_asset_image(session, authority, receipt, command.expected_target)

    def lookup_applied_result(self, scope: OwnerTaskScope, result_ref: str) -> ApplicationRecord | None:
        session = self._session
        trusted = scope.trusted_account_identity
        if trusted is None or trusted.user_id != scope.user_id:
            return None
        actor = GenerationActor(scope.user_id, trusted)
        receipt = load_owned_completed_result(session, actor, scope.task_id, result_ref)
        if isinstance(receipt, str) or receipt.operation != scope.operation:
            return None
        rows = session.execute(
            select(ApplicationRecordRow).where(
                ApplicationRecordRow.owner_id == scope.user_id,
                ApplicationRecordRow.task_id == scope.task_id,
                ApplicationRecordRow.unit_id == receipt.unit_id,
                ApplicationRecordRow.result_ref == result_ref,
                ApplicationRecordRow.result_digest == receipt.content_digest,
                ApplicationRecordRow.delivery_status == "applied",
            ).with_for_update()
        ).scalars().all()
        if len(rows) != 1:
            return None
        row = rows[0]
        series_id = _series_id_from_scope(row.target_scope)
        if (
            series_id is None or not _has_series_access(session, scope.user_id, series_id)
            or row.application_ref != application_ref_for(result_ref, row.target_scope)
            or row.committed_at is None or not row.after_target_digest
        ):
            return None
        return find_application_record(session, scope.user_id, scope.task_id, result_ref, row.target_scope)


    def load_owned_completed_result(self, shared_tx: CallerTransaction, actor: GenerationActor,
                                     task_id: str, result_ref: str) -> ResultLookup:
        return load_owned_completed_result(self._bound_session(shared_tx), actor, task_id, result_ref)

    def _bound_session(self, tx: CallerTransaction) -> Session:
        if tx is not self._session:
            raise TypeError("generation result facade must use its bound caller Session")
        return self._session


def _load_bound_receipt(session: Session, task: AITaskRow, row: GenerationResultReceiptRow,
                        *, require_succeeded: bool) -> GenerationResultReceipt | None:
    unit = session.execute(select(BillingUnitRow).where(
        BillingUnitRow.id == row.unit_id, BillingUnitRow.task_id == task.id,
    ).with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
    step = session.get(ExecutionStepRow, row.step_id)
    submission = session.get(ExternalSubmissionRow, row.submission_id)
    evidence = session.get(ResultEvidenceRow, row.evidence_id)
    if (
        unit is None or unit.operation != row.operation
        or task.type != _operation_task_type(row.operation)
        or (require_succeeded and unit.result_status != "succeeded")
        or step is None or step.billing_unit_id != unit.id
        or step.execution_status not in {"result_observed", "applied"}
        or submission is None or submission.step_id != step.id
        or evidence is None or evidence.step_id != step.id or evidence.submission_id != submission.id
        or evidence.validity != "valid" or evidence.result_digest != row.content_digest
        or not row.recoverable_locator or not row.content_digest or not row.automatic_application_allowed
        or not _stored_target_matches(step, submission, task.request_data, None)
    ):
        return None
    return GenerationRecordRepository(session).find_result_receipt(task.user_id, task.id, row.result_ref)


def _stored_target_matches(
    step, submission, task_request_data: object, expected_target: TargetSnapshot | None
) -> bool:
    snapshots = [
        snapshot
        for source in (step.input_snapshot, submission.request_snapshot, _decode_mapping(task_request_data))
        if (snapshot := _target_value(source)) is not None
    ]
    if expected_target is None:
        if not snapshots:
            return True
        target_fields = (
            "kind", "chapter_id", "series_id", "stable_storyboard_asset_id",
            "asset_type", "asset_id", "compat_frame_index", "expected_text_digest",
            "media_identity", "content_digest", "notes_revision", "full_frame_digest",
            "full_chapter_digest", "expected_asset_image_url", "same_series_target_digest",
        )
        first_snapshot = tuple(snapshots[0].get(name) for name in target_fields)
        return all(
            tuple(snapshot.get(name) for name in target_fields) == first_snapshot
            for snapshot in snapshots[1:]
        )
    if not snapshots:
        return False

    expected = {
        "kind": expected_target.kind, "chapter_id": expected_target.chapter_id,
        "series_id": expected_target.series_id,
        "stable_storyboard_asset_id": expected_target.stable_storyboard_asset_id,
        "asset_type": expected_target.asset_type, "asset_id": expected_target.asset_id,
        "compat_frame_index": expected_target.compat_frame_index,
        "expected_text_digest": expected_target.expected_text_digest,
        "media_identity": expected_target.media_identity, "content_digest": expected_target.content_digest,
        "notes_revision": expected_target.notes_revision, "full_frame_digest": expected_target.full_frame_digest,
        "full_chapter_digest": expected_target.full_chapter_digest,
        "expected_asset_image_url": expected_target.expected_asset_image_url,
        "same_series_target_digest": expected_target.same_series_target_digest,
    }
    return all(
        all(snapshot.get(key) == item for key, item in expected.items())
        for snapshot in snapshots
    )


def _target_value(source: object) -> dict[str, object] | None:
    if isinstance(source, str):
        source = _decode_mapping(source)
    if not isinstance(source, dict):
        return None
    target = source.get("target_snapshot")
    if isinstance(target, dict):
        return target
    nested = source.get("snapshot")
    return nested.get("target_snapshot") if isinstance(nested, dict) and isinstance(nested.get("target_snapshot"), dict) else None


def _decode_mapping(value: object) -> dict[str, object] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return None
        return decoded if isinstance(decoded, dict) else None
    return None


def _validated_output_matches(value: ValidatedGenerationResult, receipt: GenerationResultReceipt) -> bool:
    if (
        value.result_ref != receipt.result_ref or value.validation_version != receipt.validation_version
        or value.recoverable_locator != receipt.recoverable_locator or value.content_digest != receipt.content_digest
        or not _same_instant(value.validated_at, receipt.validated_at)
        or not _same_instant(value.generation_fulfilled_at, receipt.generation_fulfilled_at)
        or value.selected_attempt_id != receipt.selected_attempt_id
        or value.automatic_application_allowed != receipt.automatic_application_allowed
    ):
        return False
    if isinstance(value.safe_output, TextResult):
        return hashlib.sha256(value.safe_output.text.encode("utf-8")).hexdigest() == receipt.content_digest
    return value.safe_output.content_digest == receipt.content_digest


def _work_authorizes_target(work: LocalContinuationWorkRow, target: TargetSnapshot) -> bool:
    return (
        work.operation in {"frame.review", "frame.optimize"}
        and target.kind == "frame_text"
        and bool(target.chapter_id and target.series_id)
    )


def _authority_facts(authority: WorkerAuthority):
    if isinstance(authority, ClaimAuthority):
        return authority.scope, authority.token, authority.worker_instance, authority.execution_generation, None, None
    return authority.owner_scope, authority.token, authority.worker_instance, authority.execution_generation, authority.work_id, authority.phase_version


def _operation_task_type(operation: str) -> str | None:
    return {"image.single": "image-single", "image.batch": "batch-image", "video.single": "video-single",
            "frame.review": "ai-review", "frame.optimize": "batch-optimize"}.get(operation)


def _require_session(tx: CallerTransaction) -> Session:
    if not isinstance(tx, Session):
        raise TypeError("generation result operations require the caller-owned SQLAlchemy Session")
    return tx


def _database_now(session: Session) -> datetime:
    value = session.execute(select(func.current_timestamp())).scalar_one()
    return _as_utc(value)


def _fresh_database_time(session: Session) -> datetime:
    """Read wall-clock time after target locks instead of transaction start time."""
    if session.get_bind().dialect.name == "postgresql":
        statement = select(func.clock_timestamp())
    else:
        statement = select(func.current_timestamp())
    return _as_utc(session.execute(statement).scalar_one())


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _same_instant(left: datetime, right: datetime) -> bool:
    return _as_utc(left) == _as_utc(right)


def _series_id_from_scope(scope: str) -> str | None:
    parts = scope.split("|")
    return parts[1] if len(parts) >= 3 and parts[1] else None


def _has_series_access(session: Session, user_id: str, series_id: str) -> bool:
    try:
        require_series_access(SqlAlchemySeriesAccessReader(session), ActorIdentity(user_id), series_id)
    except (SeriesAccessDenied, SeriesNotFound):
        return False
    return True


__all__ = (
    "GenerationResultApplicationFacade", "ResultLookup", "WorkerAuthority",
    "application_key_for", "application_ref_for", "current_target_application",
    "confirm_worker_fence_after_target_lock", "expected_target_digest",
    "find_application_record", "load_owned_completed_result", "load_validated_worker_result",
    "stage_application_record", "target_matches_receipt",
    "target_scope_for_snapshot",
)
