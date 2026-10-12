"""Same-Session generation result writers for frame text and previews."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.errors import GenerationError
from haoai_backend.generation_contracts.records import GenerationResultReceipt, LocalContinuationWork
from haoai_backend.generation_contracts.types import (
    ApplicationOutcome, CanvasApplyAuthority, ClaimAuthority,
    ContinuationClaimAuthority, TargetSnapshot, TextResult,
    ValidatedGenerationResult,
)
from haoai_backend.generation_core.base_schema import ExecutionStepRow, ResultEvidenceRow
from haoai_backend.generation_core.persistence import GenerationRecordRepository
from haoai_backend.personal_production.notes import stage_media_reconciliation
from haoai_backend.series_access.application import require_series_access
from haoai_backend.series_access.domain import ActorIdentity
from haoai_backend.series_access.errors import SeriesAccessDenied, SeriesNotFound
from haoai_backend.series_access.persistence import SqlAlchemySeriesAccessReader
from haoai_backend.task_observation import (
    current_target_application,
    confirm_worker_fence_after_target_lock,
    expected_target_digest,
    load_owned_completed_result,
    load_validated_worker_result,
    stage_application_record,
    target_matches_receipt,
    target_scope_for_snapshot,
)

from .errors import SeriesDataNotFound
from .tables import chapters, storyboard_assets

WorkerAuthority = ClaimAuthority | ContinuationClaimAuthority
ApplicationAuthority = WorkerAuthority | CanvasApplyAuthority


def stage_frame_result(
    shared_session: Session,
    authority: WorkerAuthority,
    validated_result: ValidatedGenerationResult,
    target: TargetSnapshot,
) -> ApplicationOutcome:
    """Stage validated frame text, source description, notes and proof atomically."""
    if target.kind != "frame_text":
        return ApplicationOutcome("write_failed", validated_result.result_ref, None, None, "frame text target is required")
    if not isinstance(validated_result.safe_output, TextResult):
        return ApplicationOutcome("write_failed", validated_result.result_ref, None, None, "frame text result is invalid")
    receipt, work = load_validated_worker_result(shared_session, authority, validated_result, target)
    content = validated_result.safe_output.text
    if hashlib.sha256(content.encode("utf-8")).hexdigest() != receipt.content_digest:
        return ApplicationOutcome("write_failed", receipt.result_ref, None, None, "validated text digest does not match the receipt")
    return _stage_frame_mutation(shared_session, authority, receipt, target, content, work=work)


def stage_frame_preview(
    shared_session: Session,
    authority: CanvasApplyAuthority,
    receipt: GenerationResultReceipt,
    target: TargetSnapshot,
) -> ApplicationOutcome:
    """Stage a canvas-authorized preview; worker results never auto-apply to canvas."""
    if target.kind != "canvas_frame_preview":
        return ApplicationOutcome("write_failed", receipt.result_ref, None, None, "frame preview target is required")
    canonical_receipt = _load_canvas_receipt(shared_session, authority, receipt, target)
    if canonical_receipt is None:
        return ApplicationOutcome("write_failed", receipt.result_ref, None, None, "completed result authority is invalid")
    if not canonical_receipt.recoverable_locator or len(canonical_receipt.recoverable_locator) > 500:
        return ApplicationOutcome("write_failed", canonical_receipt.result_ref, None, None, "recoverable preview locator is invalid")
    return _stage_frame_mutation(
        shared_session, authority, canonical_receipt, target,
        canonical_receipt.recoverable_locator, work=None, preview=True,
    )


def _stage_frame_mutation(
    session: Session,
    authority: ApplicationAuthority,
    receipt: GenerationResultReceipt,
    target: TargetSnapshot,
    replacement: str,
    *,
    work: LocalContinuationWork | None,
    preview: bool = False,
) -> ApplicationOutcome:
    if not isinstance(session, Session):
        raise TypeError("shared_session must be the caller-owned SQLAlchemy Session")
    actor_id = _authority_owner(authority)
    if actor_id != receipt.owner_id or receipt.task_id != _authority_task(authority):
        return ApplicationOutcome("write_failed", receipt.result_ref, None, None, "result owner and task do not match the authority")
    if not target.series_id or not target.chapter_id or not _has_series_access(session, actor_id, target.series_id):
        return ApplicationOutcome("write_failed", receipt.result_ref, None, None, "current series access is unavailable")
    if preview and not target.stable_storyboard_asset_id:
        return ApplicationOutcome("target_conflict", receipt.result_ref, None, None, "preview application requires a stable storyboard id")
    try:
        old_digest = expected_target_digest(target)
        target_scope = target_scope_for_snapshot(target)
    except ValueError as exc:
        return ApplicationOutcome("write_failed", receipt.result_ref, None, None, str(exc))

    if isinstance(authority, CanvasApplyAuthority):
        if authority.target != target or authority.result_ref != receipt.result_ref or authority.operation != receipt.operation or authority.result_digest != receipt.content_digest:
            return ApplicationOutcome("write_failed", receipt.result_ref, None, None, "canvas authority does not match the canonical receipt")
        if authority.operation not in {"image.single", "image.batch"}:
            return ApplicationOutcome("write_failed", receipt.result_ref, None, None, "operation cannot apply a canvas image")

    chapter = session.execute(select(chapters).where(
        chapters.c.id == target.chapter_id,
        chapters.c.series_id == target.series_id,
    ).with_for_update()).mappings().one_or_none()
    if chapter is None:
        return ApplicationOutcome("target_deleted", receipt.result_ref, None, None, "target chapter was deleted")
    frames = _parse_frames(chapter["content"])
    if frames is None:
        return ApplicationOutcome("target_conflict", receipt.result_ref, None, None, "chapter content is not a valid frame list")
    current_identity_index = _resolve_current_frame_identity(frames, target)
    if current_identity_index is None:
        return ApplicationOutcome("target_conflict", receipt.result_ref, None, None, "current frame identity is missing or ambiguous")
    storyboard_id = target.stable_storyboard_asset_id
    asset = None
    if storyboard_id:
        asset = session.execute(select(storyboard_assets).where(
            storyboard_assets.c.id == storyboard_id,
            storyboard_assets.c.chapter_id == target.chapter_id,
            storyboard_assets.c.series_id == target.series_id,
        ).with_for_update()).mappings().one_or_none()
        if asset is None:
            return ApplicationOutcome("target_deleted", receipt.result_ref, None, None, "storyboard asset was deleted")

    if isinstance(authority, (ClaimAuthority, ContinuationClaimAuthority)):
        confirm_worker_fence_after_target_lock(session, authority, receipt=receipt, target=target)

    replay = current_target_application(session, receipt, target, old_digest)
    if replay is not None:
        return replay

    frame_index = _resolve_frame_index(frames, target)
    if frame_index is None:
        return ApplicationOutcome("target_conflict", receipt.result_ref, None, None, "frame identity is missing or ambiguous")
    current_frame = frames[frame_index]
    if _digest(current_frame) != old_digest:
        return ApplicationOutcome("stale", receipt.result_ref, None, None, "frame changed after generation admission")
    if target.expected_text_digest is not None:
        current_text = current_frame.get("text")
        if not isinstance(current_text, str) or _digest(current_text) != target.expected_text_digest:
            return ApplicationOutcome("stale", receipt.result_ref, None, None, "frame text changed after generation admission")

    if preview:
        current_frame["preview"] = replacement
    else:
        current_frame["text"] = replacement
    encoded = json.dumps(frames, ensure_ascii=False)
    updated = session.execute(update(chapters).where(
        chapters.c.id == chapter["id"], chapters.c.series_id == chapter["series_id"],
        chapters.c.content.is_(None) if chapter["content"] is None else chapters.c.content == chapter["content"],
    ).values(content=encoded, updated_at=_database_now(session)))
    if updated.rowcount != 1:
        return ApplicationOutcome("stale", receipt.result_ref, None, None, "chapter content compare-and-set lost")

    if storyboard_id and asset is not None:
        if not preview:
            changed = session.execute(update(storyboard_assets).where(
                storyboard_assets.c.id == storyboard_id,
                storyboard_assets.c.chapter_id == target.chapter_id,
                storyboard_assets.c.series_id == target.series_id,
                storyboard_assets.c.description.is_(None) if asset["description"] is None else storyboard_assets.c.description == asset["description"],
            ).values(description=replacement, updated_at=_database_now(session)))
            if changed.rowcount != 1:
                raise GenerationError("target_conflict_deleted_stale", "storyboard description changed during application")

    reconcile = stage_media_reconciliation(
        session,
        target.chapter_id,
        chapter_content_override=encoded,
        chapter_content_before=chapter["content"],
        focus_storyboard_asset_id=storyboard_id,
    )
    after_digest = _digest(frames[frame_index])
    record = stage_application_record(
        session, receipt, target_scope, old_digest, after_digest,
        media_revision_before=reconcile.media_revision_before,
        media_revision_after=reconcile.media_revision_after,
        notes_revision_before=reconcile.notes_revision_before,
        notes_revision_after=reconcile.notes_revision_after,
    )
    if work is not None:
        _mark_worker_result_applied(session, receipt)
        _advance_work(session, authority, work, record.application_ref)
    return ApplicationOutcome("applied", receipt.result_ref, record.application_ref, after_digest)


def _load_canvas_receipt(session: Session, authority: CanvasApplyAuthority,
                         supplied: GenerationResultReceipt, target: TargetSnapshot) -> GenerationResultReceipt | None:
    if (
        authority.target != target or authority.result_ref != supplied.result_ref
        or authority.operation not in {"image.single", "image.batch"}
    ):
        return None
    from haoai_backend.task_observation import load_owned_completed_result
    actor = authority.actor
    loaded = load_owned_completed_result(session, actor, authority.task_id, authority.result_ref)
    if isinstance(loaded, str):
        return None
    if (
        loaded != supplied or supplied.operation != authority.operation
        or supplied.content_digest != authority.result_digest
        or not target_matches_receipt(session, supplied, target)
    ):
        return None
    return supplied


def _mark_worker_result_applied(session: Session, receipt: GenerationResultReceipt) -> None:
    evidence = session.execute(select(ResultEvidenceRow).where(ResultEvidenceRow.id == receipt.evidence_id).with_for_update()).scalar_one_or_none()
    step = session.execute(select(ExecutionStepRow).where(ExecutionStepRow.id == receipt.step_id).with_for_update()).scalar_one_or_none()
    if evidence is None or step is None or evidence.application_status != "pending" or step.execution_status != "result_observed":
        raise GenerationError("evidence_conflict_or_invalid", "generation evidence has already been applied or changed")
    evidence.application_status = "applied"
    evidence.applied_at = _database_now(session).replace(tzinfo=None)
    step.execution_status = "applied"
    step.updated_at = _database_now(session).replace(tzinfo=None)


def _advance_work(session: Session, authority: ApplicationAuthority, work: LocalContinuationWork, application_ref: str) -> None:
    if isinstance(authority, CanvasApplyAuthority):
        return
    try:
        index = work.phase_order.index(work.phase)
        next_phase = work.phase_order[index + 1]
    except (ValueError, IndexError) as exc:
        raise GenerationError("continuation_phase_or_fence_conflict", "delivery phase has no next continuation phase") from exc
    updated = replace(
        work,
        phase=next_phase,
        phase_version=work.phase_version + 1,
        application_ref=application_ref,
        last_execution_generation=authority.execution_generation,
    )
    changed = GenerationRecordRepository(session).advance_continuation_work(
        updated, expected_phase_version=work.phase_version
    )
    if changed.status != "updated":
        raise GenerationError("continuation_phase_or_fence_conflict", "continuation phase compare-and-set lost")


def _resolve_current_frame_identity(frames: list[Any], target: TargetSnapshot) -> int | None:
    """Find the live stable frame identity without comparing its old content digest."""
    stable_id = target.stable_storyboard_asset_id
    if stable_id:
        matching = [
            index
            for index, frame in enumerate(frames)
            if isinstance(frame, dict) and _frame_storyboard_id(frame) == stable_id
        ]
        return matching[0] if len(matching) == 1 else None
    index = target.compat_frame_index
    if index is None or not 0 <= index < len(frames) or not isinstance(frames[index], dict):
        return None
    return index


def _resolve_frame_index(frames: list[Any], target: TargetSnapshot) -> int | None:
    stable_id = target.stable_storyboard_asset_id
    if stable_id:
        matching = [index for index, frame in enumerate(frames)
                    if isinstance(frame, dict) and _frame_storyboard_id(frame) == stable_id]
        return matching[0] if len(matching) == 1 else None
    if target.compat_frame_index is None or not target.full_chapter_digest:
        return None
    if _digest(frames) != target.full_chapter_digest:
        return None
    index = target.compat_frame_index
    if 0 <= index < len(frames) and isinstance(frames[index], dict):
        return index
    return None


def _frame_storyboard_id(frame: dict[str, Any]) -> str | None:
    refs = frame.get("storyboard")
    if isinstance(refs, list) and refs and isinstance(refs[0], str):
        return refs[0]
    return None


def _parse_frames(content: object) -> list[Any] | None:
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return None
    return content if isinstance(content, list) else None


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _authority_owner(authority: ApplicationAuthority) -> str:
    return authority.scope.user_id if isinstance(authority, ClaimAuthority) else authority.owner_scope.user_id if isinstance(authority, ContinuationClaimAuthority) else authority.actor.user_id


def _authority_task(authority: ApplicationAuthority) -> str:
    return authority.scope.task_id if isinstance(authority, ClaimAuthority) else authority.owner_scope.task_id if isinstance(authority, ContinuationClaimAuthority) else authority.task_id


def _has_series_access(session: Session, user_id: str, series_id: str) -> bool:
    try:
        require_series_access(SqlAlchemySeriesAccessReader(session), ActorIdentity(user_id), series_id)
    except (SeriesAccessDenied, SeriesNotFound):
        return False
    return True


def _database_now(session: Session) -> datetime:
    value = session.execute(select(func.current_timestamp())).scalar_one()
    return value.replace(tzinfo=None) if value.tzinfo is not None else value


__all__ = ("stage_frame_preview", "stage_frame_result")
