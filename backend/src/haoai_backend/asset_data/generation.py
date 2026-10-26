"""Same-Session canvas image application for owned series assets."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal, Mapping

from sqlalchemy import and_, select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.canvas_asset_targets import (
    CANVAS_ASSET_TARGET_FIELDS,
    canvas_asset_target_digest,
)
from haoai_backend.generation_contracts.records import ApplicationRecord, GenerationResultReceipt
from haoai_backend.generation_contracts.types import (
    ApplicationOutcome,
    CanvasApplyAuthority,
    GenerationActor,
    TargetSnapshot,
)
from haoai_backend.series_access.application import require_series_access
from haoai_backend.series_access.domain import ActorIdentity
from haoai_backend.series_access.errors import SeriesAccessDenied, SeriesNotFound
from haoai_backend.series_access.persistence import SqlAlchemySeriesAccessReader
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.task_observation import (
    current_target_application,
    load_owned_completed_result,
    stage_application_record,
    target_matches_receipt,
    target_scope_for_snapshot,
)

from haoai_backend.personal_production.notes import stage_media_reconciliation
from haoai_backend.personal_production.notes.errors import NotesNotFound
from haoai_backend.generation_core.persistence import GenerationRecordRepository
from haoai_backend.task_observation import application_ref_for

from .tables import ASSET_TABLES, chapters, storyboard_assets

_ASSET_DIGEST_FIELDS = CANVAS_ASSET_TARGET_FIELDS


def digest_asset_target(asset_type: str, row: Mapping[str, object]) -> str:
    """Digest the complete mutable target projection used by canvas CAS."""
    return canvas_asset_target_digest(asset_type, row)


def stage_asset_image(
    shared_session: Session,
    authority: CanvasApplyAuthority,
    receipt: GenerationResultReceipt,
    target: TargetSnapshot,
) -> ApplicationOutcome:
    """Stage the explicit canvas asset replacement; the caller commits once."""
    if not isinstance(shared_session, Session):
        raise TypeError("shared_session must be the caller-owned SQLAlchemy Session")
    if target.kind != "canvas_asset_image":
        return ApplicationOutcome("write_failed", receipt.result_ref, None, None, "canvas asset target is required")
    actor = authority.actor
    if (
        actor.user_id != actor.trusted_account_identity.user_id
        or authority.target != target
        or authority.task_id != receipt.task_id
        or authority.result_ref != receipt.result_ref
        or authority.operation != receipt.operation
        or authority.result_digest != receipt.content_digest
        or authority.operation not in {"image.single", "image.batch"}
        or target.asset_type not in ASSET_TABLES
        or not target.asset_id
        or not target.series_id
    ):
        return ApplicationOutcome("write_failed", receipt.result_ref, None, None, "canvas asset authority is invalid")

    loaded = load_owned_completed_result(shared_session, actor, authority.task_id, authority.result_ref)
    if isinstance(loaded, str) or loaded != receipt or not target_matches_receipt(shared_session, receipt, target):
        return ApplicationOutcome("write_failed", receipt.result_ref, None, None, "canonical result does not match the owner, operation, or frozen target")
    try:
        require_series_access(
            SqlAlchemySeriesAccessReader(shared_session), ActorIdentity(actor.user_id), target.series_id
        )
        target_scope = target_scope_for_snapshot(target)
        if not target.same_series_target_digest:
            raise ValueError("asset target is missing its full same-series digest")
    except (SeriesAccessDenied, SeriesNotFound):
        return ApplicationOutcome("write_failed", receipt.result_ref, None, None, "current series access is unavailable")
    except ValueError as exc:
        return ApplicationOutcome("write_failed", receipt.result_ref, None, None, str(exc))

    table = ASSET_TABLES[target.asset_type]
    current = shared_session.execute(select(table).where(
        table.c.id == target.asset_id,
        table.c.series_id == target.series_id,
    ).with_for_update()).mappings().one_or_none()
    if current is None:
        return ApplicationOutcome("target_deleted", receipt.result_ref, None, None, "asset target was deleted")
    current_row = dict(current)

    replay = current_target_application(
        shared_session, receipt, target, target.same_series_target_digest
    )
    if replay is not None:
        return replay
    if current_row.get("image_url") != target.expected_asset_image_url:
        return ApplicationOutcome("stale", receipt.result_ref, None, None, "asset image changed after generation admission")
    before_digest = digest_asset_target(target.asset_type, current_row)
    if before_digest != target.same_series_target_digest:
        return ApplicationOutcome("stale", receipt.result_ref, None, None, "asset target changed after generation admission")
    if not receipt.recoverable_locator or len(receipt.recoverable_locator) > 500:
        return ApplicationOutcome("write_failed", receipt.result_ref, None, None, "recoverable image locator is invalid")

    predicates = [table.c.id == target.asset_id, table.c.series_id == target.series_id]
    predicates.extend(
        column.is_(None) if current_row.get(name) is None else column == current_row[name]
        for name, column in ((name, table.c[name]) for name in _ASSET_DIGEST_FIELDS[target.asset_type])
    )
    changed = shared_session.execute(
        update(table).where(and_(*predicates)).values(
            image_url=receipt.recoverable_locator,
            updated_at=_database_now(shared_session),
        )
    )
    if changed.rowcount != 1:
        return ApplicationOutcome("stale", receipt.result_ref, None, None, "asset target compare-and-set lost")

    next_row = dict(current_row)
    next_row["image_url"] = receipt.recoverable_locator
    after_digest = digest_asset_target(target.asset_type, next_row)
    record = stage_application_record(
        shared_session, receipt, target_scope, before_digest, after_digest
    )
    return ApplicationOutcome("applied", receipt.result_ref, record.application_ref, after_digest)


def stage_batch_asset_image_if_empty(
    shared_session: Session,
    actor: GenerationActor,
    receipt: GenerationResultReceipt,
    target: TargetSnapshot,
) -> ApplicationOutcome:
    """Apply one completed batch image to its original asset only while empty."""
    if not isinstance(shared_session, Session):
        raise TypeError("shared_session must be the caller-owned SQLAlchemy Session")
    if (
        not isinstance(actor, GenerationActor)
        or actor.user_id != actor.trusted_account_identity.user_id
        or receipt.operation != "image.batch"
        or receipt.owner_id != actor.user_id
        or not receipt.automatic_application_allowed
        or receipt.late_disposition is not None
        or receipt.policy_stop_disposition is not None
        or target.kind != "asset_message"
        or target.asset_type not in {*ASSET_TABLES, "storyboard"}
        or not target.series_id
        or not target.chapter_id
        or not target.asset_id
    ):
        return _batch_application_refusal(receipt, "write_failed", "batch image target authority is invalid")

    try:
        target_scope = target_scope_for_snapshot(target)
    except ValueError:
        return _batch_application_refusal(receipt, "write_failed", "batch image target scope is invalid")
    loaded = load_owned_completed_result(shared_session, actor, receipt.task_id, receipt.result_ref)
    if (
        isinstance(loaded, str)
        or loaded != receipt
        or not target_matches_receipt(shared_session, receipt, target)
        or not receipt.recoverable_locator
        or len(receipt.recoverable_locator) > 500
    ):
        return _batch_application_refusal(receipt, "write_failed", "original completed result or target could not be verified")

    target_table = storyboard_assets if target.asset_type == "storyboard" else ASSET_TABLES[target.asset_type]
    if target.asset_type == "storyboard":
        chapter = shared_session.execute(
            select(chapters.c.id, chapters.c.series_id)
            .where(chapters.c.id == target.chapter_id)
            .with_for_update()
        ).mappings().one_or_none()
        if chapter is None:
            return _stage_rejected_batch_application(
                shared_session, receipt, target_scope, "target_deleted", None
            )
        if chapter["series_id"] != target.series_id:
            return _stage_rejected_batch_application(
                shared_session, receipt, target_scope, "target_conflict", None
            )

    target_columns = [
        target_table.c.id,
        target_table.c.series_id,
        target_table.c.image_url,
    ]
    if target.asset_type == "storyboard":
        target_columns.append(target_table.c.chapter_id)
    current = shared_session.execute(
        select(*target_columns)
        .where(target_table.c.id == target.asset_id)
        .with_for_update()
    ).mappings().one_or_none()
    if current is None:
        return _stage_rejected_batch_application(
            shared_session, receipt, target_scope, "target_deleted", None
        )
    current_row = dict(current)
    before_digest = _batch_media_target_digest(target, current_row.get("image_url"))
    if (
        current_row.get("series_id") != target.series_id
        or (
            target.asset_type == "storyboard"
            and current_row.get("chapter_id") != target.chapter_id
        )
    ):
        return _stage_rejected_batch_application(
            shared_session, receipt, target_scope, "target_conflict", before_digest
        )

    try:
        require_series_access(
            SqlAlchemySeriesAccessReader(shared_session),
            ActorIdentity(actor.user_id),
            target.series_id,
        )
    except SeriesAccessDenied:
        return _stage_rejected_batch_application(
            shared_session, receipt, target_scope, "target_conflict", before_digest
        )
    except SeriesNotFound:
        return _stage_rejected_batch_application(
            shared_session, receipt, target_scope, "target_deleted", before_digest
        )

    if current_row.get("image_url") not in (None, ""):
        return _stage_rejected_batch_application(
            shared_session, receipt, target_scope, "target_conflict", before_digest
        )

    media_outcome = None
    if target.asset_type == "storyboard":
        try:
            media_outcome = stage_media_reconciliation(
                shared_session,
                target.chapter_id,
                asset_image_overrides={target.asset_id: receipt.recoverable_locator},
                focus_storyboard_asset_id=target.asset_id,
            )
        except NotesNotFound:
            return _stage_rejected_batch_application(
                shared_session, receipt, target_scope, "target_deleted", before_digest
            )

    changed = shared_session.execute(
        update(target_table)
        .where(
            target_table.c.id == target.asset_id,
            target_table.c.series_id == target.series_id,
            target_table.c.image_url.is_(None) | (target_table.c.image_url == ""),
            *(
                (target_table.c.chapter_id == target.chapter_id,)
                if target.asset_type == "storyboard"
                else ()
            ),
        )
        .values(image_url=receipt.recoverable_locator, updated_at=_database_now(shared_session))
    )
    if changed.rowcount != 1:
        raise RuntimeError("batch asset image compare-and-set lost")

    after_digest = _batch_media_target_digest(target, receipt.recoverable_locator)
    revisions = {} if media_outcome is None else {
        "media_revision_before": media_outcome.media_revision_before,
        "media_revision_after": media_outcome.media_revision_after,
        "notes_revision_before": media_outcome.notes_revision_before,
        "notes_revision_after": media_outcome.notes_revision_after,
    }
    record = stage_application_record(
        shared_session,
        receipt,
        target_scope,
        before_digest,
        after_digest,
        **revisions,
    )
    return ApplicationOutcome("applied", receipt.result_ref, record.application_ref, after_digest)


def _stage_rejected_batch_application(
    session: Session,
    receipt: GenerationResultReceipt,
    target_scope: str,
    status: Literal["target_conflict", "target_deleted"],
    target_digest: str | None,
) -> ApplicationOutcome:
    now = _database_now(session)
    record = ApplicationRecord(
        application_ref=application_ref_for(receipt.result_ref, target_scope),
        result_ref=receipt.result_ref,
        owner_id=receipt.owner_id,
        task_id=receipt.task_id,
        unit_id=receipt.unit_id,
        target_scope=target_scope,
        result_digest=receipt.content_digest,
        delivery_status=status,
        before_target_digest=target_digest,
        after_target_digest=target_digest,
        media_revision_before=None,
        media_revision_after=None,
        notes_revision_before=None,
        notes_revision_after=None,
        committed_at=now,
    )
    staged = GenerationRecordRepository(session).stage_record(record)
    if staged.status != "created":
        raise RuntimeError("batch application result already has a different receipt")
    kind = "target_deleted" if status == "target_deleted" else "target_conflict"
    return ApplicationOutcome(kind, receipt.result_ref, record.application_ref, target_digest)


def _batch_application_refusal(
    receipt: GenerationResultReceipt, kind: Literal["write_failed"], detail: str
) -> ApplicationOutcome:
    return ApplicationOutcome(kind, receipt.result_ref, None, None, detail)


def _batch_media_target_digest(target: TargetSnapshot, image_url: object) -> str:
    value = {
        "kind": "asset_message",
        "series_id": target.series_id,
        "chapter_id": target.chapter_id,
        "asset_type": target.asset_type,
        "asset_id": target.asset_id,
        "image_url": image_url,
    }
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _database_now(session: Session) -> datetime:
    from sqlalchemy import func

    value = session.execute(select(func.current_timestamp())).scalar_one()
    return value.replace(tzinfo=None) if value.tzinfo is not None else value


__all__ = (
    "digest_asset_target",
    "stage_asset_image",
    "stage_batch_asset_image_if_empty",
)
