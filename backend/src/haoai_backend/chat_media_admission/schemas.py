"""Request DTOs matching the legacy single and batch media admission routes."""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError

from .errors import ChatMediaRequestError


class _StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())


class StandaloneTarget(_StrictRequest):
    kind: Literal["standalone"]


class FrameMessageTarget(_StrictRequest):
    kind: Literal["frame_message"]
    chapter_id: StrictStr = Field(min_length=1, max_length=36)
    storyboard_asset_id: StrictStr = Field(min_length=1, max_length=36)


class AssetMessageTarget(_StrictRequest):
    kind: Literal["asset_message"]
    chapter_id: StrictStr = Field(min_length=1, max_length=36)
    asset_type: Literal["character", "scene", "prop", "storyboard"]
    asset_id: StrictStr = Field(min_length=1, max_length=36)


class FramePreviewDestination(_StrictRequest):
    kind: Literal["frame_preview"]
    chapter_id: StrictStr = Field(min_length=1, max_length=36)
    storyboard_asset_id: StrictStr = Field(min_length=1, max_length=36)


class AssetImageDestination(_StrictRequest):
    kind: Literal["asset_image"]
    chapter_id: StrictStr = Field(min_length=1, max_length=36)
    asset_type: Literal["character", "scene", "prop"]
    asset_id: StrictStr = Field(min_length=1, max_length=36)


CanvasDestination = Annotated[
    Union[FramePreviewDestination, AssetImageDestination],
    Field(discriminator="kind"),
]


class CanvasApplyTarget(_StrictRequest):
    kind: Literal["canvas_apply"]
    destination: CanvasDestination


TaskTarget = Annotated[
    Union[
        StandaloneTarget,
        FrameMessageTarget,
        AssetMessageTarget,
        CanvasApplyTarget,
    ],
    Field(discriminator="kind"),
]


class ChatImageRequest(_StrictRequest):
    model_id: StrictStr = Field(min_length=1, max_length=255)
    prompt: StrictStr = Field(min_length=1)
    target: TaskTarget
    size: StrictStr | None = None
    reference_image: StrictStr | None = None
    reference_images: list[StrictStr] | None = None


class ChatVideoRequest(_StrictRequest):
    model_id: StrictStr = Field(min_length=1, max_length=255)
    prompt: StrictStr = Field(min_length=1)
    target: TaskTarget
    size: StrictStr | None = None
    duration: StrictInt | None = None
    resolution: StrictStr | None = None
    reference_images: list[StrictStr] | None = None
    reference_audios: list[StrictStr] | None = None
    reference_videos: list[StrictStr] | None = None


class BatchAssetItem(BaseModel):
    """Legacy batch items intentionally retain ordinary Pydantic coercion."""

    model_config = ConfigDict(protected_namespaces=())

    type: str
    id: str
    name: str = ""
    description: str = ""
    model_id: str = ""
    prompt_id: str = ""
    size: str | None = None
    reference_images: list[str] | None = None


class BatchImageRequest(BaseModel):
    """Legacy batch request intentionally ignores unknown fields."""

    model_config = ConfigDict(protected_namespaces=())

    chapter_id: str
    assets: list[BatchAssetItem]


def parse_single_request(body: object, *, video: bool) -> dict[str, object]:
    model: type[ChatImageRequest] | type[ChatVideoRequest] = (
        ChatVideoRequest if video else ChatImageRequest
    )
    try:
        parsed = model.model_validate(body)
    except ValidationError as error:
        raise ChatMediaRequestError("请求格式无效") from error
    return parsed.model_dump(mode="json")


def parse_batch_request(body: object) -> dict[str, object]:
    try:
        parsed = BatchImageRequest.model_validate(body)
    except ValidationError as error:
        raise ChatMediaRequestError("请求格式无效") from error
    return parsed.model_dump(mode="json")
