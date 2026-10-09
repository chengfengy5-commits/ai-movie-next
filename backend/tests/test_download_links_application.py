from __future__ import annotations

import asyncio

import pytest

from haoai_backend.download_links.application import resolve_download_links
from haoai_backend.download_links.domain import DownloadLinkRequestItem
from haoai_backend.download_links.errors import DownloadLinksTooManyItems


class RecordingResolver:
    def __init__(self, *, fail_at: int | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.active = 0
        self.max_active = 0
        self.fail_at = fail_at

    async def resolve(self, url: str, filename: str) -> str:
        index = len(self.calls)
        self.calls.append((url, filename))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0)
            if self.fail_at == index:
                raise RuntimeError("resolver failed")
            return url + "&resolved=1" if index == 1 else url
        finally:
            self.active -= 1


def test_empty_batch_does_not_call_resolver() -> None:
    resolver = RecordingResolver()
    result = asyncio.run(resolve_download_links(resolver, []))
    assert result == []
    assert resolver.calls == []


def test_items_are_awaited_in_order_with_duplicates_and_literal_signed_result() -> None:
    resolver = RecordingResolver()
    items = [
        DownloadLinkRequestItem(url="same", filename="first"),
        DownloadLinkRequestItem(url="same", filename="second"),
        DownloadLinkRequestItem(url="last", filename="third"),
    ]

    result = asyncio.run(resolve_download_links(resolver, items))

    assert resolver.calls == [("same", "first"), ("same", "second"), ("last", "third")]
    assert resolver.max_active == 1
    assert [(item.url, item.filename, item.signed) for item in result] == [
        ("same", "first", False),
        ("same&resolved=1", "second", True),
        ("last", "third", False),
    ]


def test_exact_batch_limit_is_accepted() -> None:
    resolver = RecordingResolver()
    items = [DownloadLinkRequestItem(url=f"u{index}", filename="") for index in range(500)]

    result = asyncio.run(resolve_download_links(resolver, items))

    assert len(result) == 500
    assert len(resolver.calls) == 500
    assert resolver.max_active == 1


def test_oversized_batch_is_rejected_before_any_resolver_call() -> None:
    resolver = RecordingResolver()
    items = [DownloadLinkRequestItem(url=f"u{index}", filename="") for index in range(501)]

    with pytest.raises(DownloadLinksTooManyItems) as error:
        asyncio.run(resolve_download_links(resolver, items))

    assert error.value.status_code == 400
    assert error.value.detail == "单次最多 500 条"
    assert resolver.calls == []


def test_resolver_failure_stops_at_the_failing_item_without_retry() -> None:
    resolver = RecordingResolver(fail_at=1)
    items = [
        DownloadLinkRequestItem(url="first", filename="1"),
        DownloadLinkRequestItem(url="second", filename="2"),
        DownloadLinkRequestItem(url="third", filename="3"),
    ]

    with pytest.raises(RuntimeError, match="resolver failed"):
        asyncio.run(resolve_download_links(resolver, items))

    assert resolver.calls == [("first", "1"), ("second", "2")]
    assert resolver.max_active == 1

