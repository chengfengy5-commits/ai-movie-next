"""Pure request/result values and URL comparison rules."""

from __future__ import annotations

from dataclasses import dataclass

MAX_DOWNLOAD_LINK_ITEMS = 500
DISABLED_DOWNLOAD_DETAIL = (
    "下载代理已停用（服务器不承载下载流量）：请使用 POST "
    "/api/sign-download-urls 获取签名直链，由浏览器直连 OSS 下载"
)


@dataclass(frozen=True, slots=True)
class DownloadLinkRequestItem:
    url: str
    filename: str


@dataclass(frozen=True, slots=True)
class DownloadLinkResult:
    url: str
    filename: str
    signed: bool


def resolved_item(
    request_item: DownloadLinkRequestItem,
    resolved_url: str,
) -> DownloadLinkResult:
    """Keep the requested filename and compare the returned URL literally."""
    return DownloadLinkResult(
        url=resolved_url,
        filename=request_item.filename,
        signed=resolved_url != request_item.url,
    )

