"""Pure JSON codec for the legacy xAI video provider protocol."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import TypeAlias, cast


JSONScalar: TypeAlias = type(None) | bool | int | float | str
RawJSONValue: TypeAlias = (
    JSONScalar
    | list["RawJSONValue"]
    | dict[str, "RawJSONValue"]
    | tuple["RawJSONValue", ...]
    | Mapping[str, "RawJSONValue"]
)
FrozenJSONValue: TypeAlias = (
    JSONScalar
    | tuple["FrozenJSONValue", ...]
    | Mapping[str, "FrozenJSONValue"]
)


def _freeze_json(value: object) -> FrozenJSONValue:
    """Copy a JSON value into recursively immutable response data."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value

    if isinstance(value, Mapping):
        frozen_items: dict[str, FrozenJSONValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("raw response objects must have string keys")
            frozen_items[key] = _freeze_json(item)
        return MappingProxyType(frozen_items)

    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)

    raise TypeError("raw response fields must contain JSON values")


@dataclass(frozen=True, slots=True)
class XaiCreateResponse:
    """Raw create response fields used by the legacy xAI adapter."""

    request_id: FrozenJSONValue

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _freeze_json(self.request_id))


@dataclass(frozen=True, slots=True)
class XaiQueryResponse:
    """Parsed provider fields; status remains the upstream's raw value."""

    status: FrozenJSONValue
    video_url: FrozenJSONValue
    error_message: FrozenJSONValue

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _freeze_json(self.status))
        object.__setattr__(self, "video_url", _freeze_json(self.video_url))
        object.__setattr__(self, "error_message", _freeze_json(self.error_message))


class XaiHTTPResponseError(ValueError):
    """An xAI create or query response with a non-200 HTTP status."""

    def __init__(self, status_code: int, body: str | bytes) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"xAI returned HTTP {status_code}")


class XaiResponseParseError(ValueError):
    """A malformed or non-object xAI JSON response."""

    def __init__(self, response_name: str, reason: str) -> None:
        self.response_name = response_name
        self.reason = reason
        super().__init__(f"xAI {response_name} response: {reason}")


def build_video_payload(
    *,
    model: str | None,
    prompt: str,
    size: str | None = None,
    duration: int | None = None,
    resolution: str | None = None,
    reference_images: Sequence[str] | None = None,
    max_reference_images: int = 9,
) -> dict[str, object]:
    """Build the source-compatible xAI JSON payload.

    The legacy model-parameter helper defaults max_reference_images to 9;
    the multi-reference wire format separately slices to 7 images.
    """
    payload: dict[str, object] = {
        "model": model or "grok-imagine-video",
        "prompt": prompt,
    }
    payload["aspect_ratio"] = size or "16:9"

    if resolution:
        payload["resolution"] = resolution

    references = (reference_images or ())[:max_reference_images]
    reference_count = len(references)

    if reference_count == 1:
        payload["image"] = {"url": references[0]}
        if duration:
            payload["duration"] = min(duration, 15)
    elif reference_count > 1:
        payload["reference_images"] = [
            {"url": image_url} for image_url in references[:7]
        ]
        payload["duration"] = min(duration, 10) if duration else 10
    elif duration:
        payload["duration"] = min(duration, 15)

    return payload


def _parse_json_response(
    *,
    response_name: str,
    status_code: int,
    body: str | bytes,
) -> dict[str, RawJSONValue]:
    if status_code != 200:
        raise XaiHTTPResponseError(status_code, body)

    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise XaiResponseParseError(response_name, "invalid JSON") from exc

    if not isinstance(payload, dict):
        raise XaiResponseParseError(response_name, "expected a JSON object")

    return cast(dict[str, RawJSONValue], payload)


def parse_create_response(
    status_code: int,
    body: str | bytes,
) -> XaiCreateResponse:
    """Parse the legacy create response's request_id without inventing a fallback."""
    payload = _parse_json_response(
        response_name="create",
        status_code=status_code,
        body=body,
    )
    return XaiCreateResponse(
        request_id=cast(FrozenJSONValue, payload.get("request_id"))
    )


def parse_query_response(
    status_code: int,
    body: str | bytes,
) -> XaiQueryResponse:
    """Parse raw provider status and media/error fields without task policy."""
    payload = _parse_json_response(
        response_name="query",
        status_code=status_code,
        body=body,
    )
    status = payload.get("status", "")
    video_url: FrozenJSONValue = ""
    error_message: FrozenJSONValue = None

    if status == "done":
        video = payload.get("video", "")
        if isinstance(video, str):
            video_url = video
        elif isinstance(video, dict):
            video_url = cast(FrozenJSONValue, video.get("url", ""))
    elif status == "failed":
        error = payload.get("error", {})
        error_message = (
            cast(FrozenJSONValue, error.get("message", "未知错误"))
            if isinstance(error, dict)
            else str(error)
        )

    return XaiQueryResponse(
        status=cast(FrozenJSONValue, status),
        video_url=video_url,
        error_message=error_message,
    )
