from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from haoai_backend.chat_data.domain import ChatMessageRecord
from haoai_backend.chat_data.presentation import chat_message_payload
from haoai_backend.chat_data.schemas import (
    AssetChatMessageCreate,
    ChatMessageCreate,
    ChatMessageResponse,
    ChatMessageUpdate,
)


def test_chat_create_keeps_default_none_and_legacy_integer_conversion() -> None:
    omitted = ChatMessageCreate(
        chapter_id="chapter-a",
        role="assistant",
        content="hello",
    )
    converted = ChatMessageCreate(
        chapter_id="chapter-a",
        frame_index="2",
        role="assistant",
        content="hello",
        ignored="value",
    )

    assert omitted.frame_index is None
    assert converted.frame_index == 2
    assert converted.model_dump()["chat_mode"] == "chat"
    assert "ignored" not in converted.model_dump()


def test_explicit_null_frame_index_is_rejected_but_negative_integer_is_kept() -> None:
    with pytest.raises(ValidationError):
        ChatMessageCreate(
            chapter_id="chapter-a",
            frame_index=None,
            role="assistant",
            content="hello",
        )

    negative = ChatMessageCreate(
        chapter_id="chapter-a",
        frame_index=-3,
        role="",
        content="",
        chat_mode="future-mode",
    )
    assert negative.frame_index == -3
    assert negative.role == ""
    assert negative.content == ""
    assert negative.chat_mode == "future-mode"


def test_asset_create_ignores_extra_frame_and_unknown_values() -> None:
    request = AssetChatMessageCreate(
        chapter_id="chapter-a",
        asset_type="",
        asset_id="asset-a",
        role="user",
        content="",
        frame_index=-10,
        future_field="ignored",
    )
    assert request.model_dump() == {
        "chapter_id": "chapter-a",
        "asset_type": "",
        "asset_id": "asset-a",
        "chat_mode": "chat",
        "role": "user",
        "content": "",
        "model_name": None,
    }


def test_chat_message_response_preserves_nullable_fields_and_created_at_only() -> None:
    created_at = datetime(2026, 10, 9, 12, 0, 0)
    record = ChatMessageRecord(
        id="message-a",
        chapter_id="chapter-a",
        frame_index=None,
        asset_type="",
        asset_id=None,
        chat_mode="image",
        role="assistant",
        content="hello",
        model_name=None,
        created_at=created_at,
    )

    payload = chat_message_payload(record)
    response = ChatMessageResponse.model_validate(payload)

    assert response.model_dump() == payload
    assert response.frame_index is None
    assert response.asset_type == ""
    assert response.asset_id is None
    assert response.model_name is None
    assert "updated_at" not in response.model_dump()


def test_update_request_keeps_empty_content() -> None:
    assert ChatMessageUpdate(content="").content == ""
