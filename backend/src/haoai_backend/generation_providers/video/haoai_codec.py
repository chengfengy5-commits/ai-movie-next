"""Pure raw-protocol helpers for the legacy HaoAI video endpoint.

The response projections preserve selected source fields and marker facts. They
never make retry, Task, billing, persistence, or media-transfer decisions.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import TypeAlias


DEFAULT_HAOAI_VIDEO_BASE_URL = "https://cpa.haoai.icu/v1"
DEFAULT_HAOAI_VIDEO_MODEL = "grok-imagine-video-1.5-preview"
DEFAULT_MAX_REFERENCE_IMAGES = 9

JsonValue: TypeAlias = (
    str
    | int
    | float
    | bool
    | None
    | tuple["JsonValue", ...]
    | Mapping[str, "JsonValue"]
)

_IMAGE_DOWNLOAD_MARKERS = (
    "image_download",
    "failed to download the provided image",
    "downloading the image",
)
_AUTH_UNAVAILABLE_MARKERS = (
    "auth_unavailable",
    "no auth available",
    "no available auth candidates",
    "all candidates are unavailable",
)


def _freeze_json(value: object) -> JsonValue:
    """Copy JSON-shaped values into recursively immutable containers."""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("JSON object keys must be strings")
            frozen[key] = _freeze_json(item)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    raise TypeError(f"expected a JSON value, got {type(value).__name__}")


def _decode_json_object(body: str | bytes | bytearray) -> tuple[str, dict[str, object] | None]:
    try:
        decoded = json.loads(body)
    except (TypeError, ValueError, UnicodeDecodeError):
        return "malformed_json", None
    if not isinstance(decoded, dict):
        return "non_object", None
    return "object", decoded


def normalize_haoai_video_base_url(base_url: str | None) -> str:
    """Apply the source terminal-``/v1`` base normalization only."""
    api_base = (base_url or DEFAULT_HAOAI_VIDEO_BASE_URL).rstrip("/")
    if api_base.endswith("/v1"):
        api_base = api_base[:-3]
    return api_base.rstrip("/")


def haoai_video_create_url(base_url: str | None) -> str:
    return f"{normalize_haoai_video_base_url(base_url)}/openai/v1/videos"


def haoai_video_query_url(base_url: str | None, external_id: str) -> str:
    return f"{normalize_haoai_video_base_url(base_url)}/openai/v1/videos/{external_id}"


def build_haoai_video_create_payload(
    prompt: str,
    *,
    model: str | None = None,
    duration: object = None,
    size: str | None = None,
    resolution: str | None = None,
    reference_images: Sequence[object] | None = None,
    max_reference_images: int = DEFAULT_MAX_REFERENCE_IMAGES,
) -> dict[str, object]:
    """Build the legacy wire shape from already-prepared reference strings."""
    payload: dict[str, object] = {
        "model": model or DEFAULT_HAOAI_VIDEO_MODEL,
        "prompt": prompt,
        "seconds": int(duration) if duration else 4,
        "aspect_ratio": size or "16:9",
    }
    if resolution:
        payload["resolution"] = resolution

    selected: list[str] = []
    if reference_images:
        for reference in reference_images[:max_reference_images]:
            if isinstance(reference, str):
                selected.append(reference)

    if len(selected) == 1:
        payload["input_reference"] = {"image_url": selected[0]}
    elif len(selected) > 1:
        payload["reference_images"] = [{"url": reference} for reference in selected]
        markers = " ".join(f"<IMAGE_{index}>" for index in range(1, len(selected) + 1))
        payload["prompt"] = f"{prompt}\n\n参考图：{markers}"
    return payload


@dataclass(frozen=True)
class HaoaiCreateResponse:
    """A field-limited view of the source create response; not a Task result."""

    http_status: int
    body_kind: str
    request_id_present: bool = False
    raw_request_id: JsonValue = None
    id_present: bool = False
    raw_id: JsonValue = None
    status_present: bool = False
    raw_status: JsonValue = ""
    external_id: JsonValue = None
    external_id_source: str | None = None

    def __post_init__(self) -> None:
        for name in ("raw_request_id", "raw_id", "raw_status", "external_id"):
            object.__setattr__(self, name, _freeze_json(getattr(self, name)))


def parse_haoai_create_response(
    http_status: int, body: str | bytes | bytearray
) -> HaoaiCreateResponse:
    """Project known create fields while preserving malformed/body-shape facts."""
    if http_status != 200:
        return HaoaiCreateResponse(http_status=http_status, body_kind="http_error")

    body_kind, data = _decode_json_object(body)
    if data is None:
        return HaoaiCreateResponse(http_status=http_status, body_kind=body_kind)

    request_id_present = "request_id" in data
    id_present = "id" in data
    request_id = data.get("request_id")
    raw_id = data.get("id")
    if request_id:
        external_id, source = request_id, "request_id"
    elif raw_id:
        external_id, source = raw_id, "id"
    else:
        external_id, source = None, None

    return HaoaiCreateResponse(
        http_status=http_status,
        body_kind="object",
        request_id_present=request_id_present,
        raw_request_id=request_id,
        id_present=id_present,
        raw_id=raw_id,
        status_present="status" in data,
        raw_status=data.get("status", ""),
        external_id=external_id,
        external_id_source=source,
    )


def _response_text(body: str | bytes | bytearray) -> str:
    if isinstance(body, str):
        return body
    if isinstance(body, (bytes, bytearray)):
        return bytes(body).decode("utf-8", errors="replace")
    raise TypeError("response body must be text or bytes")


def _matching_markers(text: str, markers: tuple[str, ...]) -> tuple[str, ...]:
    lowered = text.lower()
    return tuple(marker for marker in markers if marker in lowered)


def _select_media_locator(data: dict[str, object]) -> tuple[object, str | None]:
    video_url = data.get("video_url") or ""
    if video_url:
        return video_url, "video_url"

    video = data.get("video", "")
    if isinstance(video, dict):
        video_url = video.get("url", "")
        if video_url:
            return video_url, "video.url"
    else:
        video_url = str(video) if video else ""
        if video_url:
            return video_url, "video_string"

    video_url = data.get("url", "")
    if video_url:
        return video_url, "url"
    return video_url, None


@dataclass(frozen=True)
class HaoaiQueryResponse:
    """Selected raw query fields and protocol marker facts, without policy."""

    http_status: int
    body_kind: str
    status_present: bool = False
    raw_status: JsonValue = ""
    error_present: bool = False
    raw_error: JsonValue = None
    error_message: JsonValue = "未知错误"
    progress_present: bool = False
    raw_progress: JsonValue = 0
    video_url_present: bool = False
    raw_video_url: JsonValue = ""
    video_present: bool = False
    raw_video: JsonValue = ""
    url_present: bool = False
    raw_url: JsonValue = ""
    media_locator: JsonValue = ""
    media_locator_source: str | None = None
    image_download_markers: tuple[str, ...] = ()
    auth_unavailable_markers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "raw_status",
            "raw_error",
            "error_message",
            "raw_progress",
            "raw_video_url",
            "raw_video",
            "raw_url",
            "media_locator",
        ):
            object.__setattr__(self, name, _freeze_json(getattr(self, name)))
        object.__setattr__(self, "image_download_markers", tuple(self.image_download_markers))
        object.__setattr__(self, "auth_unavailable_markers", tuple(self.auth_unavailable_markers))

    @property
    def matches_completion_status(self) -> bool:
        return self.raw_status in ("completed", "done")

    @property
    def matches_failed_status(self) -> bool:
        return self.raw_status == "failed"

    @property
    def completed_without_locator(self) -> bool:
        return self.matches_completion_status and not bool(self.media_locator)


def parse_haoai_query_response(
    http_status: int, body: str | bytes | bytearray
) -> HaoaiQueryResponse:
    """Project source query fields; marker matches never authorize a retry."""
    if http_status != 200:
        markers = _matching_markers(
            _response_text(body)[:500], _AUTH_UNAVAILABLE_MARKERS
        )
        return HaoaiQueryResponse(
            http_status=http_status,
            body_kind="http_error",
            auth_unavailable_markers=markers,
        )

    body_kind, data = _decode_json_object(body)
    if data is None:
        return HaoaiQueryResponse(http_status=http_status, body_kind=body_kind)

    status_present = "status" in data
    raw_status = data.get("status", "")
    error_present = "error" in data
    raw_error = data.get("error")
    if isinstance(raw_error, dict):
        error_message = raw_error.get("message", "未知错误")
    elif error_present:
        error_message = raw_error
    else:
        error_message = "未知错误"

    media_locator, media_locator_source = _select_media_locator(data)
    image_markers: tuple[str, ...] = ()
    if raw_status == "failed":
        image_markers = _matching_markers(str(error_message), _IMAGE_DOWNLOAD_MARKERS)

    return HaoaiQueryResponse(
        http_status=http_status,
        body_kind="object",
        status_present=status_present,
        raw_status=raw_status,
        error_present=error_present,
        raw_error=raw_error,
        error_message=error_message,
        progress_present="progress" in data,
        raw_progress=data.get("progress", 0),
        video_url_present="video_url" in data,
        raw_video_url=data.get("video_url", ""),
        video_present="video" in data,
        raw_video=data.get("video", ""),
        url_present="url" in data,
        raw_url=data.get("url", ""),
        media_locator=media_locator,
        media_locator_source=media_locator_source,
        image_download_markers=image_markers,
    )
