"""Pure codecs for the legacy OpenAI-compatible image JSON protocol.

This module builds request bytes and parses raw response values. It performs no
network, media validation, persistence, or execution-policy decisions.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal


_DEFAULT_BASE_URL = "https://api.openai.com/v1"
_DEFAULT_MODEL = "dall-e-3"
_DEFAULT_SIZE = "1024x1024"
_GENERATION_PATH = "/images/generations"
_EDIT_PATH = "/images/edits"
_MAX_REFERENCE_IMAGES = 8
_MAX_REFERENCE_IMAGE_BYTES = 10 * 1024 * 1024
_MAX_MULTIPART_REQUEST_BYTES = (
    _MAX_REFERENCE_IMAGES * _MAX_REFERENCE_IMAGE_BYTES + 256 * 1024
)
_IMAGE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
}
_OPENAI_SIZE_MAP = {
    "1:1": "1024x1024",
    "16:9": "1792x1024",
    "9:16": "1024x1792",
    "3:2": "1792x1024",
    "2:3": "1024x1792",
    "4:3": "1792x1024",
    "3:4": "1024x1792",
    "auto": "1024x1024",
}


@dataclass(frozen=True, slots=True)
class OpenAIImageGenerationRequest:
    """Immutable raw HTTP request components for JSON text-to-image."""

    url: str
    body_json: bytes
    content_type: Literal["application/json"] = "application/json"


@dataclass(frozen=True, slots=True)
class OpenAIImageEditReference:
    """One validated image part ready for the multipart edit request."""

    content: bytes
    media_type: str


@dataclass(frozen=True, slots=True)
class OpenAIImageEditRequest:
    """Immutable raw HTTP request components for reference image editing."""

    url: str
    body: bytes
    content_type: str


@dataclass(frozen=True, slots=True)
class OpenAIImageOutput:
    """Untrusted provider output before media fetching or validation."""

    kind: Literal["url", "base64"]
    value: str


class OpenAIImageCodecError(ValueError):
    """A local wire-shape error; it does not imply a task or provider outcome."""

    def __init__(
        self,
        code: Literal["http_error", "invalid_response", "missing_media"],
        message: str,
        *,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.message = message


def _legacy_quality(provider: str | None, params: object) -> object | None:
    if (provider or "").lower() != "haoai" or not params:
        return None

    try:
        parsed = json.loads(params) if isinstance(params, str) else params
        quality = parsed.get("quality", {})
        value = quality.get("default", "") if isinstance(quality, dict) else quality
        return value if value else None
    except Exception:
        # The legacy adapter ignored malformed model parameters.
        return None


def build_openai_text_to_image_request(
    *,
    base_url: str | None,
    model: str | None,
    prompt: str,
    size: str | None,
    provider: str | None = None,
    params: object = None,
) -> OpenAIImageGenerationRequest:
    """Build the legacy JSON generation request without performing HTTP."""

    base = base_url or _DEFAULT_BASE_URL
    payload: dict[str, object] = {
        "model": model or _DEFAULT_MODEL,
        "prompt": prompt,
        "n": 1,
        "size": _OPENAI_SIZE_MAP.get(size, size or _DEFAULT_SIZE),
    }
    quality = _legacy_quality(provider, params)
    if quality:
        payload["quality"] = quality

    return OpenAIImageGenerationRequest(
        url=base + _GENERATION_PATH,
        body_json=json.dumps(payload).encode("utf-8"),
    )


def build_openai_image_edit_request(
    *,
    base_url: str | None,
    model: str | None,
    prompt: str,
    size: str | None,
    references: tuple[OpenAIImageEditReference, ...],
) -> OpenAIImageEditRequest:
    """Build one bounded legacy multipart image-edit request."""

    if not 1 <= len(references) <= _MAX_REFERENCE_IMAGES:
        raise ValueError("reference image count is outside the supported limit")
    if not isinstance(prompt, str):
        raise ValueError("prompt must be text")

    total_reference_bytes = 0
    for reference in references:
        if (
            type(reference) is not OpenAIImageEditReference
            or type(reference.content) is not bytes
            or not 1 <= len(reference.content) <= _MAX_REFERENCE_IMAGE_BYTES
            or reference.media_type not in _IMAGE_EXTENSIONS
        ):
            raise ValueError("reference image is not prepared for multipart encoding")
        total_reference_bytes += len(reference.content)
    if total_reference_bytes > _MAX_REFERENCE_IMAGES * _MAX_REFERENCE_IMAGE_BYTES:
        raise ValueError("reference image bytes exceed the supported limit")

    base = base_url or _DEFAULT_BASE_URL
    fields: list[tuple[str, str]] = [
        ("model", model or _DEFAULT_MODEL),
        ("prompt", prompt),
        ("n", "1"),
    ]
    if size:
        fields.append(("size", _OPENAI_SIZE_MAP.get(size, size)))

    boundary = _new_boundary(fields, references)
    segments: list[bytes] = []
    for name, value in fields:
        segments.extend(
            (
                f"--{boundary}\r\n".encode("ascii"),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(
                    "ascii"
                ),
                value.encode("utf-8"),
                b"\r\n",
            )
        )
    for index, reference in enumerate(references):
        # The field name and filename are fixed server values, never request text.
        segments.extend(
            (
                f"--{boundary}\r\n".encode("ascii"),
                (
                    'Content-Disposition: form-data; name="image"; '
                    f'filename="ref_{index}.png"\r\n'
                ).encode("ascii"),
                f"Content-Type: {reference.media_type}\r\n\r\n".encode("ascii"),
                reference.content,
                b"\r\n",
            )
        )
    segments.append(f"--{boundary}--\r\n".encode("ascii"))
    if sum(map(len, segments)) > _MAX_MULTIPART_REQUEST_BYTES:
        raise ValueError("multipart request exceeds the local transport limit")

    return OpenAIImageEditRequest(
        url=base + _EDIT_PATH,
        body=b"".join(segments),
        content_type=f"multipart/form-data; boundary={boundary}",
    )


def _new_boundary(
    fields: list[tuple[str, str]],
    references: tuple[OpenAIImageEditReference, ...],
) -> str:
    values = tuple(value.encode("utf-8") for _, value in fields) + tuple(
        reference.content for reference in references
    )
    while True:
        boundary = f"haoai-{secrets.token_hex(24)}"
        marker = boundary.encode("ascii")
        if all(marker not in value for value in values):
            return boundary


def parse_openai_image_response(
    status_code: int,
    body: object,
) -> OpenAIImageOutput:
    """Parse the legacy JSON response's first URL or base64 image value."""

    if status_code != 200:
        raise OpenAIImageCodecError(
            "http_error",
            f"图片生成失败 ({status_code})",
            status_code=status_code,
        )
    if not isinstance(body, Mapping):
        raise OpenAIImageCodecError(
            "invalid_response",
            "OpenAI image response must be an object",
        )

    items = body.get("data", [{}])
    if not isinstance(items, list) or not items:
        raise OpenAIImageCodecError(
            "invalid_response",
            "OpenAI image response data must contain an object",
        )

    first = items[0]
    if not isinstance(first, Mapping):
        raise OpenAIImageCodecError(
            "invalid_response",
            "OpenAI image response item must be an object",
        )

    image_url = first.get("url", "")
    if image_url:
        if not isinstance(image_url, str):
            raise OpenAIImageCodecError(
                "invalid_response",
                "OpenAI image URL must be a string",
            )
        return OpenAIImageOutput(kind="url", value=image_url)

    image_base64 = first.get("b64_json", "")
    if image_base64:
        if not isinstance(image_base64, str):
            raise OpenAIImageCodecError(
                "invalid_response",
                "OpenAI image base64 value must be a string",
            )
        return OpenAIImageOutput(kind="base64", value=image_base64)

    raise OpenAIImageCodecError(
        "missing_media",
        "图片生成返回中没有可用结果",
    )
