"""Pure codecs for the legacy OpenAI-compatible image JSON protocol.

This module builds request bytes and parses raw response values. It performs no
network, media validation, persistence, or execution-policy decisions.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal


_DEFAULT_BASE_URL = "https://api.openai.com/v1"
_DEFAULT_MODEL = "dall-e-3"
_DEFAULT_SIZE = "1024x1024"
_GENERATION_PATH = "/images/generations"
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
