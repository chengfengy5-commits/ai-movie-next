"""Typed values for the legacy fused-image generation slice."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class FusedServerImageModel:
    id: str
    name: str
    cost_per_call: int
    provider: str
    wire_model: str | None
    api_key: str = field(repr=False)
    base_url: str | None = None
    params: str | None = field(default=None, repr=False)
    is_system: bool = True


@dataclass(frozen=True, slots=True)
class FusedPromptText:
    """Authorized prompt text without unrelated catalog display metadata."""

    system_prompt: str
    user_prompt: str

    def __post_init__(self) -> None:
        if not isinstance(self.system_prompt, str) or not isinstance(self.user_prompt, str):
            raise TypeError("fused prompt templates must be strings")


@dataclass(frozen=True, slots=True)
class FusedFrameTarget:
    chapter_id: str
    frame_index: int
    identity: str


@dataclass(frozen=True, slots=True)
class PreparedFusedGeneration:
    task_id: str
    message_id: str | None
    user_id: str
    series_id: str
    chapter_id: str | None
    member_type: str
    members: tuple[Mapping[str, Any], ...]
    name: str
    size: str | None
    prompt: str
    reference_images: tuple[str, ...]
    model_id: str
    model_name: str
    credit_cost: int
    server_model: FusedServerImageModel = field(repr=False)
    target_frame: FusedFrameTarget | None


@dataclass(frozen=True, slots=True)
class FusedGenerationInput:
    task_id: str
    series_id: str
    member_type: str
    members: tuple[Mapping[str, Any], ...]
    name: str
    size: str | None
    prompt: str
    reference_images: tuple[str, ...]
    server_model: FusedServerImageModel = field(repr=False)


@dataclass(frozen=True, slots=True)
class FusedGenerationOutput:
    image_url: str


@dataclass(frozen=True, slots=True)
class FusedCompletion:
    status: str
    image_url: str | None
    error_message: str | None
    fused_asset_id: str
    completed_at: Any


@dataclass(frozen=True, slots=True)
class FusedTaskClaim:
    cancelled: bool
