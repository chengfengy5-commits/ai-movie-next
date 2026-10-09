from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from haoai_backend.asset_data.domain import ASSET_KINDS, ASSET_NOT_FOUND_DETAILS
from haoai_backend.asset_data.presentation import parse_chapter_content_for_response
from haoai_backend.asset_data.schemas import (
    CharacterCreate,
    CharacterResponse,
    CharacterUpdate,
    PropResponse,
    PropUpdate,
    SceneResponse,
    SceneUpdate,
    StoryboardAssetCreate,
    StoryboardAssetUpdate,
)


def test_asset_kind_contract_and_legacy_not_found_details() -> None:
    assert ASSET_KINDS == ("character", "scene", "prop")
    assert ASSET_NOT_FOUND_DETAILS == {
        "character": "角色不存在",
        "scene": "场景不存在",
        "prop": "道具不存在",
    }


def test_create_ignores_unmodelled_alias_fields_but_update_tracks_presence() -> None:
    created = CharacterCreate.model_validate(
        {"name": "周晴", "aliases": ["旧名"], "canonical_key": "ignored"}
    )
    assert created.name == "周晴"
    assert "aliases" not in created.model_dump()
    assert "canonical_key" not in created.model_dump()

    omitted = CharacterUpdate.model_validate({"name": "周晴"})
    explicit_null = CharacterUpdate.model_validate({"audio_url": None})
    explicit_empty = CharacterUpdate.model_validate({"audio_url": ""})
    assert omitted.model_fields_set == {"name"}
    assert "audio_url" not in omitted.model_fields_set
    assert explicit_null.model_fields_set == {"audio_url"}
    assert explicit_null.audio_url is None
    assert explicit_empty.audio_url == ""


def test_regular_nullable_fields_preserve_null_and_empty_remains_an_assignment() -> None:
    scene = SceneUpdate.model_validate({"title": None, "description": "", "aliases": []})
    prop = PropUpdate.model_validate({"name": "", "image_url": None})

    assert scene.model_fields_set == {"title", "description", "aliases"}
    assert scene.title is None
    assert scene.description == ""
    assert scene.aliases == []
    assert prop.model_fields_set == {"name", "image_url"}
    assert prop.name == ""
    assert prop.image_url is None


def test_response_aliases_parse_strings_but_validate_list_members() -> None:
    base = {
        "id": "character-1",
        "series_id": "series-1",
        "name": "周晴",
        "gender": None,
        "age": None,
        "role": None,
        "appearance": None,
        "description": None,
        "image_url": None,
        "audio_url": None,
        "voice_ref": None,
        "created_at": "2026-10-08T00:00:00",
        "updated_at": "2026-10-08T00:00:00",
    }
    assert CharacterResponse.model_validate(
        {**base, "aliases": '["晴晴"]'}
    ).aliases == ["晴晴"]
    assert CharacterResponse.model_validate({**base, "aliases": "not-json"}).aliases == []
    assert CharacterResponse.model_validate({**base, "aliases": "null"}).aliases == []
    assert CharacterResponse.model_validate({**base, "aliases": None}).aliases is None
    with pytest.raises(ValidationError):
        CharacterResponse.model_validate({**base, "aliases": ["晴晴", 3]})


@pytest.mark.parametrize(
    ("model", "name_field", "required_nullable_fields"),
    [
        (
            CharacterResponse,
            "name",
            ("gender", "age", "role", "appearance", "description", "image_url", "audio_url", "voice_ref"),
        ),
        (SceneResponse, "title", ("description", "image_url")),
        (PropResponse, "name", ("description", "image_url")),
    ],
)
def test_response_nullable_fields_are_required_keys_but_allow_null(
    model,
    name_field: str,
    required_nullable_fields: tuple[str, ...],
) -> None:
    payload = {
        "id": "asset-1",
        "series_id": "series-1",
        name_field: "名称",
        "created_at": "2026-10-08T00:00:00",
        "updated_at": "2026-10-08T00:00:00",
        **{field: None for field in required_nullable_fields},
    }

    parsed = model.model_validate(payload)
    assert all(getattr(parsed, field) is None for field in required_nullable_fields)
    for field in required_nullable_fields:
        missing = dict(payload)
        del missing[field]
        with pytest.raises(ValidationError):
            model.model_validate(missing)


def test_storyboard_dto_does_not_add_business_validation_or_update_fields() -> None:
    create = StoryboardAssetCreate.model_validate(
        {"chapter_id": "chapter-1", "frame_index": -3, "name": ""}
    )
    update = StoryboardAssetUpdate.model_validate(
        {"name": "新名称", "chapter_id": "other", "frame_index": 4}
    )

    assert create.frame_index == -3
    assert create.name == ""
    assert update.model_dump(exclude_unset=True) == {"name": "新名称"}


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("", []),
        (None, []),
        ([{"text": "not stored JSON"}], []),
        ("not-json", []),
        ("null", None),
        ("7", 7),
        ('[{"text":"一"}]', [{"text": "一"}]),
    ],
)
def test_legacy_chapter_response_content_decodes_only_nonempty_strings(
    content: object,
    expected: object,
) -> None:
    assert parse_chapter_content_for_response(content) == expected
