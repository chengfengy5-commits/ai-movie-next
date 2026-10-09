"""Stable storyboard identity and URL digest projection tests."""

from __future__ import annotations

import pytest

from haoai_backend.personal_production.notes.media import (
    digest_media_identity,
    normalize_media_identity,
    project_chapter_storyboard_media,
)


@pytest.mark.parametrize(
    ("source", "identity", "digest"),
    [
        (
            "https://CDN.Example/%2f/frame.png?versionId=v%2F1&tenant=Studio&%73ecurity-token=temporary&AWSAccessKeyId=key&X-Amz-Signature=sig&token=business%2Fvalue#preview",
            "https://CDN.Example/%2f/frame.png?versionId=v%2F1&tenant=Studio&token=business%2Fvalue",
            "436a4a63d2d60928c2d342bd28e2953fd068d2afa6e6cc5ee2183d1a38f59687",
        ),
        (
            "HTTPS://CDN.Example/a.png?X-Amz-Signature=x#f",
            "https://CDN.Example/a.png",
            "ee5d9e5783b08dfdc96901f89082e52c4ea76d5c7aa9ebc466eb3de114a6f246",
        ),
        (
            " HTTPS://CDN.Example/a.png?Signature=x#f",
            "https://CDN.Example/a.png",
            "ee5d9e5783b08dfdc96901f89082e52c4ea76d5c7aa9ebc466eb3de114a6f246",
        ),
        (
            "https://[broken/a.png?X-Amz-Signature=x#f",
            "https://[broken/a.png?X-Amz-Signature=x#f",
            "e4905e5231edcb756ce7ac90700dc6774f099ff7c04eb631a8afd9d554710939",
        ),
        (
            "https://[broken]/a.png?Signature=x#f",
            "https://[broken]/a.png?Signature=x#f",
            "5a23815502310760c33ec4e237ecfc2af9b8cb9b0428cea3341ec921a2c92c4a",
        ),
        (
            "https://CDN.Example/角色/镜头.png?tenant=动画&Signature=old#预览",
            "https://CDN.Example/角色/镜头.png?tenant=动画",
            "9aa35765601a2ab92dd9575b2e8504d80c2fb72675ecaecbbb6e7628024acbc5",
        ),
        (
            "https://例子.test/片段.mp4?业务=甲+乙&X-Amz-Date=20260929T120000Z#视频",
            "https://例子.test/片段.mp4?业务=甲+乙",
            "526a3de0935265bbd60d52bd29da99a4aab5006928640e64b51d232cd5b61667",
        ),
        (
            "https://cdn.test/a.png?Signature=x&versionId=v%2F1&tenant=animation",
            "https://cdn.test/a.png?versionId=v%2F1&tenant=animation",
            "240c3b0adbb4de8cfc13371fde139327ea34e553a90f16d803e210f8b365c7c3",
        ),
    ],
)
def test_fixed_legacy_media_digest_vectors(source: str, identity: str, digest: str) -> None:
    actual_identity = normalize_media_identity(source)
    assert actual_identity == identity
    assert digest_media_identity(actual_identity) == digest


def test_empty_unreadable_mixed_duplicate_and_same_chapter_identity_projection() -> None:
    assert project_chapter_storyboard_media("chapter-a", [], []).state == "empty"
    assert project_chapter_storyboard_media("chapter-a", None, []).state == "unreadable"
    assert project_chapter_storyboard_media("chapter-a", "not-json", []).state == "unreadable"

    projection = project_chapter_storyboard_media(
        "chapter-a",
        [
            {"storyboard": ["shared", "ignored"], "preview": "preview-a"},
            {"storyboard": ["shared"], "preview": "preview-b"},
            {"storyboard": ["missing", "shared"]},
            {"storyboard": ["other"]},
            {"storyboard": ["   "]},
            {"storyboard": []},
        ],
        [
            {"id": "shared", "chapter_id": "chapter-a", "image_url": "image-a"},
            {"id": "other", "chapter_id": "foreign-chapter", "image_url": "image-b"},
        ],
    )

    assert projection.state == "ready"
    assert [frame.frame_index for frame in projection.frames] == list(range(6))
    assert [frame.storyboard_asset_id for frame in projection.frames] == [None, None, None, None, None, None]
    assert projection.frames[0].invalid_reason == "duplicate_storyboard_id"
    assert projection.frames[2].invalid_reason == "asset_not_in_chapter"
    assert projection.frames[3].invalid_reason == "asset_not_in_chapter"
    assert projection.frames[4].invalid_reason == "invalid_storyboard_id"
    assert projection.frames[5].invalid_reason == "missing_storyboard_id"


def test_only_the_first_raw_storyboard_reference_is_identity() -> None:
    projection = project_chapter_storyboard_media(
        "chapter-a",
        [{"storyboard": ["first-id", "second-id"]}],
        [
            {"id": "first-id", "chapter_id": "chapter-a", "image_url": None},
            {"id": "second-id", "chapter_id": "chapter-a", "image_url": "ignored"},
        ],
    )
    assert projection.frames[0].storyboard_asset_id == "first-id"
    assert projection.frames[0].is_valid is True
    assert projection.frames[0].asset_image_identity is None


def test_one_valid_frame_remains_editable_beside_duplicate_invalid_frames() -> None:
    projection = project_chapter_storyboard_media(
        "chapter-a",
        [
            {"storyboard": ["valid-a", "ignored-secondary"]},
            {"storyboard": ["duplicate-b"]},
            {"storyboard": ["duplicate-b"]},
        ],
        [
            {"id": "valid-a", "chapter_id": "chapter-a", "image_url": "image-a"},
            {"id": "duplicate-b", "chapter_id": "chapter-a", "image_url": "image-b"},
        ],
    )

    assert [frame.storyboard_asset_id for frame in projection.frames] == [
        "valid-a",
        None,
        None,
    ]
    assert [frame.is_valid for frame in projection.frames] == [True, False, False]
    assert projection.frames[1].invalid_reason == "duplicate_storyboard_id"
    assert projection.frames[2].invalid_reason == "duplicate_storyboard_id"


def test_duplicate_assets_in_the_same_chapter_are_ambiguous() -> None:
    projection = project_chapter_storyboard_media(
        "chapter-a",
        [{"storyboard": ["asset-a"]}],
        [
            {"id": "asset-a", "chapter_id": "chapter-a", "image_url": "first"},
            {"id": "asset-a", "chapter_id": "chapter-a", "image_url": "second"},
        ],
    )

    assert projection.frames[0].is_valid is False
    assert projection.frames[0].storyboard_asset_id is None
    assert projection.frames[0].invalid_reason == "ambiguous_storyboard_asset"


def test_unknown_query_order_and_control_characters_keep_legacy_behavior() -> None:
    value = "https://cdn.test/a.png?tenant=A+%2F&B=2&Signature=sig#view"
    assert normalize_media_identity(value) == "https://cdn.test/a.png?tenant=A+%2F&B=2"
    controlled = "https://cdn.test/a.png?x=1\n&Signature=s"
    assert normalize_media_identity(controlled) == controlled
