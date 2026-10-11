"""Pure JSON protocol codec for Manxiaobai video generation.

The records preserve raw protocol facts only. They do not represent a committed
application task, validated media result, or financial outcome.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, TypeAlias
from urllib.parse import urlsplit


VIDEO_COLLECTION_PATH = "/v1/videos"
DEFAULT_VIDEO_MODEL = "grok-video-1.0"
_DEFAULT_SECONDS = 6
_DEFAULT_ASPECT_RATIO = "16:9"
_DEFAULT_RESOLUTION = "720p"
_MAX_REFERENCE_IMAGES = 7
_SINGLE_REFERENCE_MODEL_MARKER = "1.5"
_COMPLETED_STATUSES = ("completed", "done", "success", "succeeded", "SUCCESS")
_FAILED_STATUSES = ("failed", "FAILURE")

JSONScalar: TypeAlias = str | int | float | bool | None
FrozenJSONValue: TypeAlias = (
    JSONScalar
    | tuple["FrozenJSONValue", ...]
    | Mapping[str, "FrozenJSONValue"]
)
FrozenJSONObject: TypeAlias = Mapping[str, FrozenJSONValue]


class ManxiaobaiCodecError(ValueError):
    """Base error for malformed protocol inputs and prepared references."""


class PreparedReferenceError(ManxiaobaiCodecError):
    """A reference is not an already prepared HTTP(S) URL string."""


class ManxiaobaiHTTPResponseError(ManxiaobaiCodecError):
    """The legacy protocol returned an HTTP status other than 200."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"Manxiaobai returned HTTP {status_code}")


class ManxiaobaiResponseParseError(ManxiaobaiCodecError):
    """The response body is not a JSON object."""


@dataclass(frozen=True, slots=True)
class ManxiaobaiCreateResponse:
    """Immutable raw create-response facts; source_id is not task authority."""

    source_id: FrozenJSONValue
    raw_fields: FrozenJSONObject

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_id", _freeze_json(self.source_id))
        object.__setattr__(self, "raw_fields", _freeze_json_object(self.raw_fields))


@dataclass(frozen=True, slots=True)
class ManxiaobaiQueryResponse:
    """Immutable raw query facts and source-branch projections."""

    source_state: Literal["pending", "completed", "failed"]
    raw_status: FrozenJSONValue
    raw_code: FrozenJSONValue
    has_video: bool
    video_url: FrozenJSONValue
    bound_content_needed: bool
    failure_signal: FrozenJSONValue
    raw_fields: FrozenJSONObject

    def __post_init__(self) -> None:
        for field_name in (
            "raw_status",
            "raw_code",
            "video_url",
            "failure_signal",
        ):
            object.__setattr__(
                self, field_name, _freeze_json(getattr(self, field_name))
            )
        object.__setattr__(self, "raw_fields", _freeze_json_object(self.raw_fields))


def _validate_prepared_image_url(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(ord(char) <= 0x20 or ord(char) == 0x7F for char in value)
    ):
        raise PreparedReferenceError("reference must be a prepared HTTP(S) URL")

    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        raise PreparedReferenceError(
            "reference must be a prepared HTTP(S) URL"
        ) from None

    if (
        parsed.scheme not in ("http", "https")
        or not parsed.netloc
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise PreparedReferenceError("reference must be a prepared HTTP(S) URL")
    return value


def build_manxiaobai_video_payload(
    prompt: str,
    *,
    wire_model: str | None = None,
    duration: object = None,
    size: str | None = None,
    resolution: str | None = None,
    reference_images: Sequence[str] | None = None,
) -> dict[str, object]:
    """Build the legacy JSON body without uploading or reading references."""
    model_name = wire_model or DEFAULT_VIDEO_MODEL
    if not isinstance(model_name, str):
        raise ManxiaobaiCodecError("wire_model must be a string")

    if reference_images is None:
        references: Sequence[str] = ()
    elif isinstance(reference_images, (str, bytes, bytearray)) or not isinstance(
        reference_images, Sequence
    ):
        raise PreparedReferenceError("reference_images must be a sequence of URLs")
    else:
        references = reference_images

    reference_limit = (
        1 if _SINGLE_REFERENCE_MODEL_MARKER in model_name else _MAX_REFERENCE_IMAGES
    )
    prepared_references = [
        _validate_prepared_image_url(reference)
        for reference in references[:reference_limit]
    ]

    payload: dict[str, object] = {
        "model": model_name,
        "prompt": prompt,
        "seconds": str(duration or _DEFAULT_SECONDS),
        "aspect_ratio": size or _DEFAULT_ASPECT_RATIO,
        "resolution": resolution or _DEFAULT_RESOLUTION,
    }
    if prepared_references:
        payload["input_reference"] = (
            prepared_references[0]
            if len(prepared_references) == 1
            else prepared_references
        )
    return payload


def _reject_non_json_constant(_value: str) -> None:
    raise ValueError("non-standard JSON constant")


def _freeze_json(value: object) -> FrozenJSONValue:
    if isinstance(value, Mapping):
        frozen_fields: dict[str, FrozenJSONValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ManxiaobaiResponseParseError(
                    "response object keys must be strings"
                )
            frozen_fields[key] = _freeze_json(item)
        return MappingProxyType(frozen_fields)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise ManxiaobaiResponseParseError("response contains a non-JSON value")


def _freeze_json_object(value: object) -> FrozenJSONObject:
    if not isinstance(value, Mapping):
        raise ManxiaobaiResponseParseError("response fields must be an object")
    frozen = _freeze_json(value)
    if not isinstance(frozen, Mapping):
        raise ManxiaobaiResponseParseError("response fields must be an object")
    return frozen


def _parse_response_object(
    body: bytes | str, status_code: int
) -> FrozenJSONObject:
    if status_code != 200:
        raise ManxiaobaiHTTPResponseError(status_code)
    if not isinstance(body, (bytes, str)):
        raise ManxiaobaiResponseParseError("response body must be bytes or text")

    try:
        value = json.loads(body, parse_constant=_reject_non_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        raise ManxiaobaiResponseParseError("response body is not valid JSON") from None

    if not isinstance(value, dict):
        raise ManxiaobaiResponseParseError("response JSON must be an object")
    frozen = _freeze_json(value)
    if not isinstance(frozen, Mapping):
        raise ManxiaobaiResponseParseError("response JSON must be an object")
    return frozen


def parse_manxiaobai_create_response(
    body: bytes | str, *, status_code: int = 200
) -> ManxiaobaiCreateResponse:
    """Read the legacy id-then-task_id field precedence, never request_id."""
    raw_fields = _parse_response_object(body, status_code)
    source_id = raw_fields.get("id") or raw_fields.get("task_id")
    return ManxiaobaiCreateResponse(
        source_id=source_id,
        raw_fields=raw_fields,
    )


def _source_failure_signal(raw_fields: FrozenJSONObject) -> FrozenJSONValue:
    error = raw_fields.get("error", {})
    error_message = error.get("message", "") if isinstance(error, Mapping) else ""
    return error_message or raw_fields.get("fail_reason", "") or raw_fields.get(
        "message", "未知错误"
    )


def _source_video_url(raw_fields: FrozenJSONObject) -> FrozenJSONValue:
    video = raw_fields.get("video", "")
    if isinstance(video, str):
        video_url: FrozenJSONValue = video
    elif isinstance(video, Mapping):
        video_url = video.get("url", "")
    else:
        video_url = ""

    if video_url:
        return video_url

    result_url = raw_fields.get("result_url", "")
    if result_url:
        return result_url

    metadata = raw_fields.get("metadata")
    if isinstance(metadata, Mapping):
        return metadata.get("url", "")
    return ""


def parse_manxiaobai_query_response(
    body: bytes | str, *, status_code: int = 200
) -> ManxiaobaiQueryResponse:
    """Project the legacy poll branch without fetching media or asserting task truth."""
    raw_fields = _parse_response_object(body, status_code)
    top_status = raw_fields.get("status", "")
    nested_data = raw_fields.get("data")
    nested_status = (
        nested_data.get("status", "") if isinstance(nested_data, Mapping) else ""
    )
    raw_status = top_status or nested_status
    raw_code = raw_fields.get("code")
    has_video = bool(raw_fields.get("result_url") or raw_fields.get("video"))

    is_completed = (
        raw_status in _COMPLETED_STATUSES
        or (raw_code in (0, 200) and has_video)
        or has_video
    )
    if is_completed:
        source_state: Literal["pending", "completed", "failed"] = "completed"
    elif raw_status in _FAILED_STATUSES or raw_code == 1:
        source_state = "failed"
    else:
        source_state = "pending"

    video_url = _source_video_url(raw_fields)
    return ManxiaobaiQueryResponse(
        source_state=source_state,
        raw_status=raw_status,
        raw_code=raw_code,
        has_video=has_video,
        video_url=video_url,
        bound_content_needed=(source_state == "completed" and not video_url),
        failure_signal=(
            _source_failure_signal(raw_fields) if source_state == "failed" else None
        ),
        raw_fields=raw_fields,
    )
