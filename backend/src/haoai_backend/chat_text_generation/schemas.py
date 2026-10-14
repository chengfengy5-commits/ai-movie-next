"""Request DTOs for the three legacy chat text endpoints."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ChatMessageInput(BaseModel):
    model_config = ConfigDict(extra="ignore", protected_namespaces=())

    role: str
    content: str


class ChatStreamRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", protected_namespaces=())

    model_id: str
    messages: list[ChatMessageInput]
    message_id: str | None = None
    prompt_id: str | None = None
    skip_credit_check: bool = False
    enable_thinking: bool = False
    task_type: str | None = None


class AIReviewRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", protected_namespaces=())

    chapter_id: str
    frame_index: int
    prompt_id: str = ""
    model_id: str = ""
    message_id: str = ""
    error_reason: str = ""
    asset_context: str = ""
    prev_context: str = ""
    enable_thinking: bool = False


class BatchOptimizeFrame(BaseModel):
    model_config = ConfigDict(extra="ignore", protected_namespaces=())

    index: int
    text: str
    asset_context: str = ""


class BatchOptimizeRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", protected_namespaces=())

    chapter_id: str
    model_id: str
    prompt_id: str = ""
    enable_thinking: bool = False
    concurrency: int = 5
    chapter_texts: list[str] | None = None
    frames: list[BatchOptimizeFrame]
