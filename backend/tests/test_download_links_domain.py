from __future__ import annotations

import pytest
from pydantic import ValidationError

from haoai_backend.download_links.domain import (
    DISABLED_DOWNLOAD_DETAIL,
    MAX_DOWNLOAD_LINK_ITEMS,
    DownloadLinkRequestItem,
    resolved_item,
)
from haoai_backend.download_links.schemas import DownloadLinkItemRequest, DownloadLinksRequest


def test_result_uses_literal_url_comparison_and_original_filename() -> None:
    original = DownloadLinkRequestItem(url="  ftp://host/file?x=1  ", filename="原始 名称")

    unchanged = resolved_item(original, original.url)
    changed = resolved_item(original, "https://signed.example/file")

    assert unchanged.url == original.url
    assert unchanged.filename == "原始 名称"
    assert unchanged.signed is False
    assert changed.url == "https://signed.example/file"
    assert changed.filename == "原始 名称"
    assert changed.signed is True


def test_request_models_keep_plain_strings_defaults_and_ignore_extra_fields() -> None:
    omitted_filename = DownloadLinkItemRequest.model_validate(
        {"url": "", "unrecognized": "ignored"}
    )
    raw_values = DownloadLinksRequest.model_validate(
        {"items": [{"url": "   ", "filename": "x", "extra": 1}]}
    )

    assert omitted_filename.url == ""
    assert omitted_filename.filename == ""
    assert raw_values.items[0].url == "   "
    assert raw_values.items[0].filename == "x"


def test_request_models_reject_null_required_strings_and_missing_items() -> None:
    with pytest.raises(ValidationError):
        DownloadLinkItemRequest.model_validate({"url": None})
    with pytest.raises(ValidationError):
        DownloadLinkItemRequest.model_validate({"url": "x", "filename": None})
    with pytest.raises(ValidationError):
        DownloadLinksRequest.model_validate({})


def test_domain_constants_match_the_legacy_boundary() -> None:
    assert MAX_DOWNLOAD_LINK_ITEMS == 500
    assert DISABLED_DOWNLOAD_DETAIL == (
        "下载代理已停用（服务器不承载下载流量）：请使用 POST "
        "/api/sign-download-urls 获取签名直链，由浏览器直连 OSS 下载"
    )

