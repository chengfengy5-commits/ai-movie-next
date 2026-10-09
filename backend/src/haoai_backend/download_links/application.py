"""Framework-independent ordered batch URL resolution."""

from __future__ import annotations

from collections.abc import Sequence

from .domain import (
    MAX_DOWNLOAD_LINK_ITEMS,
    DownloadLinkRequestItem,
    DownloadLinkResult,
    resolved_item,
)
from .errors import DownloadLinksTooManyItems
from .ports import DownloadURLResolver


async def resolve_download_links(
    resolver: DownloadURLResolver,
    items: Sequence[DownloadLinkRequestItem],
) -> list[DownloadLinkResult]:
    """Resolve each item once in order, returning no partial result on failure."""
    if len(items) > MAX_DOWNLOAD_LINK_ITEMS:
        raise DownloadLinksTooManyItems()

    results: list[DownloadLinkResult] = []
    for item in items:
        resolved_url = await resolver.resolve(item.url, item.filename)
        results.append(resolved_item(item, resolved_url))
    return results

