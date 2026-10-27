"""Pure codec for the fixed legacy AIStarsLab video protocol."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


DEFAULT_CHANNEL = "test"
DEFAULT_MODEL = "test-video"
DEFAULT_ASPECT_RATIO = "16:9"
DEFAULT_QUALITY = "720p"
DEFAULT_DURATION_SECONDS = 5
MAX_REFERENCE_IMAGES = 9
MAX_REFERENCE_AUDIOS = 5
MAX_REFERENCE_VIDEOS = 5


class AistarslabCodecError(ValueError):
    """A request or response does not match the supported wire contract."""


@dataclass(frozen=True, slots=True)
class AistarslabModel:
    channel: str
    model: str


@dataclass(frozen=True, slots=True)
class AistarslabCreateResponse:
    code: int
    task_id: str | None


@dataclass(frozen=True, slots=True)
class AistarslabStatusResponse:
    status: int
    output_url: str | None


def parse_model(value: object) -> AistarslabModel:
    if value is None:
        return AistarslabModel(DEFAULT_CHANNEL, DEFAULT_MODEL)
    if not isinstance(value, str):
        raise AistarslabCodecError("model must be a string")

    model_value = value.strip()
    if not model_value:
        return AistarslabModel(DEFAULT_CHANNEL, DEFAULT_MODEL)
    if ":" not in model_value:
        return AistarslabModel(DEFAULT_CHANNEL, model_value)
    channel, _, model = model_value.partition(":")
    return AistarslabModel(channel.strip(), model.strip())


def build_create_payload(
    *,
    model: object,
    prompt: object,
    aspect_ratio: object = None,
    quality: object = None,
    duration: object = None,
    images: Sequence[str] = (),
    audios: Sequence[str] = (),
    videos: Sequence[str] = (),
) -> dict[str, object]:
    parsed_model = parse_model(model)
    if not isinstance(prompt, str):
        raise AistarslabCodecError("prompt must be a string")

    image_urls = _reference_urls(images, "images", MAX_REFERENCE_IMAGES)
    audio_urls = _reference_urls(audios, "audios", MAX_REFERENCE_AUDIOS)
    video_urls = _reference_urls(videos, "videos", MAX_REFERENCE_VIDEOS)

    if aspect_ratio is None or aspect_ratio == "":
        aspect_ratio = DEFAULT_ASPECT_RATIO
    if quality is None or quality == "":
        quality = DEFAULT_QUALITY
    if not isinstance(aspect_ratio, str) or not isinstance(quality, str):
        raise AistarslabCodecError("video aspect ratio and quality must be strings")

    try:
        duration_seconds = (
            int(duration)
            if duration
            else DEFAULT_DURATION_SECONDS
        )
    except (TypeError, ValueError, OverflowError):
        raise AistarslabCodecError("duration must be convertible to an integer") from None
    if isinstance(duration, bool):
        raise AistarslabCodecError("duration must not be a boolean")

    prompt_with_markers = prompt
    for label, values in (
        ("图片", image_urls),
        ("音频", audio_urls),
        ("视频", video_urls),
    ):
        if values:
            prompt_with_markers += " " + " ".join(
                f"@{label}{index}" for index in range(1, len(values) + 1)
            )

    mode = (
        "frames2video"
        if len(image_urls) == 2
        else "image2video"
        if image_urls
        else "text2video"
    )
    payload: dict[str, object] = {
        "channel": parsed_model.channel,
        "model": parsed_model.model,
        "prompt": prompt_with_markers,
        "aspectRatio": aspect_ratio,
        "quality": quality,
        "duration": duration_seconds,
        "mode": mode,
    }
    if image_urls:
        payload["inputImages"] = list(image_urls)
    if audio_urls:
        payload["inputAudios"] = list(audio_urls)
    if video_urls:
        payload["inputVideos"] = list(video_urls)
    return payload


def encode_create_payload(payload: Mapping[str, object]) -> bytes:
    try:
        return json.dumps(
            dict(payload),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        raise AistarslabCodecError("create payload is not valid JSON") from None


def parse_create_response(status_code: object, body: bytes) -> AistarslabCreateResponse:
    payload = _parse_response(status_code, body, "create")
    code = payload.get("code")
    if type(code) is not int:
        raise AistarslabCodecError("create response code must be an integer")
    if code != 0:
        return AistarslabCreateResponse(code=code, task_id=None)

    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise AistarslabCodecError("create response data must be an object")
    task_id = data.get("taskId")
    if (
        not isinstance(task_id, str)
        or not task_id
        or task_id != task_id.strip()
        or len(task_id.encode("utf-8")) > 1024
        or any(ord(char) <= 0x20 or ord(char) == 0x7F for char in task_id)
    ):
        raise AistarslabCodecError("create response taskId is invalid")
    return AistarslabCreateResponse(code=code, task_id=task_id)


def parse_status_response(status_code: object, body: bytes) -> AistarslabStatusResponse:
    payload = _parse_response(status_code, body, "status")
    code = payload.get("code")
    if type(code) is not int or code != 0:
        raise AistarslabCodecError("status response code is invalid")
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise AistarslabCodecError("status response data must be an object")

    status = data.get("status")
    if type(status) is not int or status not in {1, 2, 3, 4}:
        raise AistarslabCodecError("status response status is invalid")
    output_url: str | None = None
    if status == 3:
        outputs = data.get("outputs")
        if (
            not isinstance(outputs, Sequence)
            or isinstance(outputs, (str, bytes, bytearray))
            or not outputs
            or not isinstance(outputs[0], str)
            or not outputs[0]
            or outputs[0] != outputs[0].strip()
        ):
            raise AistarslabCodecError("completed status response has no valid output")
        output_url = outputs[0]
    return AistarslabStatusResponse(status=status, output_url=output_url)


def _reference_urls(
    values: Sequence[str],
    name: str,
    maximum: int,
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes, bytearray)) or not isinstance(values, Sequence):
        raise AistarslabCodecError(f"{name} must be a sequence of prepared URLs")
    urls = tuple(values)
    if len(urls) > maximum:
        raise AistarslabCodecError(f"{name} exceed the supported limit of {maximum}")
    if any(not isinstance(value, str) or not value for value in urls):
        raise AistarslabCodecError(f"{name} contain an invalid URL")
    return urls


def _parse_response(
    status_code: object,
    body: bytes,
    response_name: str,
) -> Mapping[str, Any]:
    if type(status_code) is not int or status_code != 200:
        raise AistarslabCodecError(f"{response_name} HTTP response is invalid")
    if not isinstance(body, bytes):
        raise AistarslabCodecError(f"{response_name} body must be bytes")
    try:
        payload = json.loads(body, parse_constant=_reject_non_json_constant)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        raise AistarslabCodecError(f"{response_name} body is not valid JSON") from None
    if not isinstance(payload, Mapping):
        raise AistarslabCodecError(f"{response_name} body must be an object")
    return payload


def _reject_non_json_constant(_value: str) -> None:
    raise ValueError("nonstandard JSON constant")


__all__ = (
    "AistarslabCodecError",
    "AistarslabCreateResponse",
    "AistarslabModel",
    "AistarslabStatusResponse",
    "DEFAULT_ASPECT_RATIO",
    "DEFAULT_CHANNEL",
    "DEFAULT_DURATION_SECONDS",
    "DEFAULT_MODEL",
    "DEFAULT_QUALITY",
    "MAX_REFERENCE_AUDIOS",
    "MAX_REFERENCE_IMAGES",
    "MAX_REFERENCE_VIDEOS",
    "build_create_payload",
    "encode_create_payload",
    "parse_create_response",
    "parse_model",
    "parse_status_response",
)
