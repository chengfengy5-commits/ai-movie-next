"""Same-session adapter for the established personal-media coordinator."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from haoai_backend.personal_production.notes import stage_media_reconciliation
from haoai_backend.personal_production.notes.errors import NotesNotFound
from haoai_backend.shared.identity import TrustedActor

from .domain import ChapterRecord
from .errors import SeriesDataNotFound


UNSET_CONTENT = object()


def reconcile_source_media_state(
    session: Session,
    chapter: ChapterRecord,
    actor: TrustedActor,
    *,
    content_override: object = UNSET_CONTENT,
    new_assets: tuple[dict[str, Any], ...] = (),
    deleted_asset_ids: set[object] | None = None,
) -> None:
    """Reconcile through the caller's Session without owning its transaction."""
    del actor  # The enclosing series writer has already checked current access.
    kwargs: dict[str, Any] = {}
    if content_override is not UNSET_CONTENT:
        kwargs["chapter_content_override"] = content_override
    if new_assets:
        kwargs["new_assets"] = new_assets
    if deleted_asset_ids:
        kwargs["deleted_asset_ids"] = set(deleted_asset_ids)
    try:
        stage_media_reconciliation(session, chapter.id, **kwargs)
    except NotesNotFound as exc:
        raise SeriesDataNotFound(exc.detail) from exc
