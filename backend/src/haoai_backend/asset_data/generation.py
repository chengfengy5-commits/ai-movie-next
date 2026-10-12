"""Same-Session canvas image application for owned series assets."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Mapping

from sqlalchemy import and_, select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.records import GenerationResultReceipt
from haoai_backend.generation_contracts.types import ApplicationOutcome, CanvasApplyAuthority, TargetSnapshot
from haoai_backend.series_access.application import require_series_access
from haoai_backend.series_access.domain import ActorIdentity
from haoai_backend.series_access.errors import SeriesAccessDenied, SeriesNotFound
from haoai_backend.series_access.persistence import SqlAlchemySeriesAccessReader
from haoai_backend.task_observation import (
    current_target_application,
    load_owned_completed_result,
    stage_application_record,
    target_matches_receipt,
    target_scope_for_snapshot,
)

from .tables import ASSET_TABLES

_ASSET_DIGEST_FIELDS = {
    "character": ("id", "series_id", "name", "aliases", "canonical_key", "gender", "age", "role", "appearance", "description", "image_url", "audio_url", "voice_ref"),
    "scene": ("id", "series_id", "title", "description", "image_url", "aliases", "canonical_key"),
    "prop": ("id", "series_id", "name", "description", "image_url", "aliases", "canonical_key"),
}


def digest_asset_target(asset_type: str, row: Mapping[str, object]) -> str:
    """Digest the complete mutable target projection used by canvas CAS."""
    fields = _ASSET_DIGEST_FIELDS.get(asset_type)
    if fields is None:
        raise ValueError("only character, scene, and prop images are canvas asset targets")
    value = {name: row.get(name) for name in fields}
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


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


def _database_now(session: Session) -> datetime:
    from sqlalchemy import func

    value = session.execute(select(func.current_timestamp())).scalar_one()
    return value.replace(tzinfo=None) if value.tzinfo is not None else value


__all__ = ("digest_asset_target", "stage_asset_image")
