"""Response projections kept separate from persistence and HTTP."""

from __future__ import annotations

from typing import Any

from .domain import ChatMessageRecord


def chat_message_payload(record: ChatMessageRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "chapter_id": record.chapter_id,
        "frame_index": record.frame_index,
        "asset_type": record.asset_type,
        "asset_id": record.asset_id,
        "chat_mode": record.chat_mode,
        "role": record.role,
        "content": record.content,
        "model_name": record.model_name,
        "created_at": record.created_at,
    }
