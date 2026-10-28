"""Strict wire schemas for stable chapter generation-history operations."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr


TargetKind = Literal["frame-preview", "storyboard-image"]
ChatMode = Literal["image", "video"]
Digest = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
ReadOnlyReason = Literal[
    "unreadable_chapter",
    "missing_storyboard_id",
    "duplicate_storyboard_id",
    "asset_not_in_chapter",
    "ambiguous_storyboard_asset",
    "storyboard_index_mismatch",
    "invalid_media_value",
]


class HistoryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, protected_namespaces=())


class ExpectedLockIdentity(HistoryModel):
    user_id: Annotated[StrictStr, Field(min_length=1)]
    acquired_at: Annotated[StrictStr, Field(min_length=1)]


class GenerationHistoryAdoptRequest(HistoryModel):
    protocol_version: Literal[1]
    expected_series_id: Annotated[StrictStr, Field(min_length=1)]
    target_kind: TargetKind
    storyboard_asset_id: Annotated[StrictStr, Field(min_length=1)]
    expected_frame_index: StrictInt = Field(ge=0)
    chat_mode: ChatMode
    message_id: Annotated[StrictStr, Field(min_length=1)]
    expected_message_digest: Digest
    expected_mapping_digest: Digest
    expected_target_digest: Digest
    expected_media_url: StrictStr | None
    expected_lock_identity: ExpectedLockIdentity | None


class TargetProof(HistoryModel):
    mapping_digest: Digest
    target_digest: Digest
    media_url: StrictStr | None


class GenerationHistoryMessage(HistoryModel):
    id: StrictStr
    chapter_id: StrictStr
    frame_index: StrictInt | None
    asset_type: StrictStr | None
    asset_id: StrictStr | None
    chat_mode: StrictStr
    role: StrictStr
    content: StrictStr
    model_name: StrictStr | None
    created_at: StrictStr
    message_digest: Digest


class GenerationHistorySnapshot(HistoryModel):
    protocol_version: Literal[1]
    series_id: StrictStr
    chapter_id: StrictStr
    target_kind: TargetKind
    storyboard_asset_id: StrictStr | None
    frame_index: StrictInt = Field(ge=0)
    chat_mode: ChatMode
    writable: bool
    read_only_reason: ReadOnlyReason | None
    target_proof: TargetProof | None
    messages: list[GenerationHistoryMessage]


class GenerationHistoryAdoptResponse(HistoryModel):
    protocol_version: Literal[1]
    outcome: Literal["applied", "already_current"]
    series_id: StrictStr
    chapter_id: StrictStr
    target_kind: TargetKind
    storyboard_asset_id: StrictStr
    frame_index: StrictInt = Field(ge=0)
    target_proof: TargetProof
