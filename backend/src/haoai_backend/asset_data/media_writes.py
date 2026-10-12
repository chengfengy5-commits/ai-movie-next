"""Explicit same-Session adapter for personal media reconciliation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy.orm import Session

from haoai_backend.personal_production.notes import stage_media_reconciliation
from haoai_backend.personal_production.notes.errors import NotesNotFound
from haoai_backend.shared.identity import TrustedActor

from .errors import AssetDataNotFound


UNSET_CONTENT = object()


def reconcile_source_media_state(
    session: Session,
    chapter_id: str,
    actor: TrustedActor,
    *,
    content_override: object = UNSET_CONTENT,
    new_assets: Sequence[Mapping[str, Any]] = (),
    asset_image_overrides: Mapping[str, object] | None = None,
    deleted_asset_ids: set[str] | None = None,
):
    """Reconcile using the caller's Session; transaction ownership stays outside."""
    del actor  # Authorization is performed by the enclosing asset-data use case.
    kwargs: dict[str, Any] = {}
    if content_override is not UNSET_CONTENT:
        kwargs["chapter_content_override"] = content_override
    if new_assets:
        kwargs["new_assets"] = tuple(dict(asset) for asset in new_assets)
    if asset_image_overrides:
        kwargs["asset_image_overrides"] = dict(asset_image_overrides)
    if deleted_asset_ids:
        kwargs["deleted_asset_ids"] = set(deleted_asset_ids)
    try:
        return stage_media_reconciliation(session, chapter_id, **kwargs).snapshot
    except NotesNotFound as exc:
        raise AssetDataNotFound(exc.detail) from exc
