"""OpenAI-compatible provider runtime for storyboard asset requests."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any, Protocol
from urllib.parse import SplitResult, urlsplit, urlunsplit

from haoai_backend.generation_providers.text.openai_codec import OpenAITextStreamCodec
from haoai_backend.storyboard_generation.openai_runtime import (
    AiohttpStoryboardTextTransport,
    StoryboardTextTransport,
)

from .ports import StoryboardAssetRuntimeCall

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-3.5-turbo"
DEFAULT_MAX_TOKENS = 8192
REQUEST_TIMEOUT_SECONDS = 600
TRUNCATION_WARNING = (
    "AI 输出达到长度上限被截断，本次结果可能不完整，请重试；若仍不完整，"
    "建议减少单个章节的分镜数量或分批提取"
)


class AddressResolver(Protocol):
    async def __call__(self, host: str, port: int) -> Sequence[str]: ...


class OpenAICompatibleStoryboardAssetRuntime:
    """Stream storyboard asset output through an injected HTTP transport."""

    def __init__(
        self,
        transport: StoryboardTextTransport | None = None,
        *,
        address_resolver: AddressResolver | None = None,
    ) -> None:
        self._transport = (
            transport
            if transport is not None
            else AiohttpStoryboardTextTransport(timeout_seconds=REQUEST_TIMEOUT_SECONDS)
        )
        self._address_resolver = address_resolver or _resolve_addresses

    async def stream(
        self, call: StoryboardAssetRuntimeCall
    ) -> AsyncIterator[str | Mapping[str, Any]]:
        provider = call.model.provider.strip().lower()
        if provider == "anthropic":
            yield {"type": "error", "message": "Anthropic流式输出暂不支持，请使用非流式模式"}
            return
        if not call.model.api_key or not call.model.api_key.strip():
            yield {"type": "error", "message": "模型执行配置不完整，请检查服务配置"}
            return

        try:
            url, parsed = _completion_url(call.model.base_url)
            await _validate_endpoint(parsed, self._address_resolver)
        except asyncio.CancelledError:
            raise
        except Exception:
            yield {"type": "error", "message": "模型服务地址配置无效"}
            return

        headers = {
            "Authorization": f"Bearer {call.model.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        body = _request_body(call, provider)
        try:
            async with self._transport.open_stream(
                url,
                headers=headers,
                json_body=body,
            ) as response:
                codec = OpenAITextStreamCodec(
                    status_code=response.status,
                    first_choice_only=True,
                )
                async for chunk in response.iter_bytes():
                    for event in codec.feed(chunk):
                        if event.kind == "content" and event.text:
                            yield event.text
                        elif event.kind in {"upstream_error", "error"}:
                            yield {"type": "error", "message": "AI 服务返回了无效响应"}
                            return
                for event in codec.finalize():
                    if event.kind == "content" and event.text:
                        yield event.text
                    elif event.kind in {"upstream_error", "error"}:
                        yield {"type": "error", "message": "AI 服务未返回有效正文"}
                        return
                if codec.finish_reason == "length":
                    yield {"type": "warning", "message": TRUNCATION_WARNING}
        except asyncio.CancelledError:
            raise
        except Exception:
            yield {"type": "error", "message": "AI 服务暂时不可用，请检查模型配置"}


def _request_body(call: StoryboardAssetRuntimeCall, provider: str) -> dict[str, Any]:
    user_text = call.user_prompt + "\n\n" + call.raw_content
    model = (call.model.model or "").strip() or DEFAULT_MODEL
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": call.system_prompt},
            {"role": "user", "content": user_text},
        ],
        "temperature": call.temperature,
        "max_tokens": _max_tokens(call.model.params),
        "stream": True,
    }
    if provider in {"openai", "deepseek"} and call.enable_thinking:
        body["thinking"] = {"type": "enabled"}
        body["reasoning_effort"] = "high"
    else:
        body["thinking"] = {"type": "disabled"}
    return body


def _max_tokens(params: object) -> int:
    value: Any = params
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return DEFAULT_MAX_TOKENS
    if not isinstance(value, Mapping):
        return DEFAULT_MAX_TOKENS

    value = value.get("max_tokens", DEFAULT_MAX_TOKENS)
    if isinstance(value, Mapping):
        value = value.get("default") or value.get("value", DEFAULT_MAX_TOKENS)
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_MAX_TOKENS
    return parsed if parsed > 0 else DEFAULT_MAX_TOKENS


def _completion_url(base_url: str | None) -> tuple[str, SplitResult]:
    value = (base_url or DEFAULT_BASE_URL).strip().rstrip("/")
    parsed = urlsplit(value)
    try:
        parsed.port
    except ValueError:
        raise ValueError("invalid model URL") from None
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.netloc
        or any(char.isspace() for char in value)
    ):
        raise ValueError("invalid model URL")
    url = urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path.rstrip("/") + "/chat/completions",
            "",
            "",
        )
    )
    return url, parsed


async def _validate_endpoint(parsed: SplitResult, resolver: AddressResolver) -> None:
    if parsed.scheme == "https":
        return
    host = parsed.hostname
    if host is None:
        raise ValueError("invalid model URL")
    if _is_internal_host(host):
        return
    port = parsed.port or 80
    addresses = await resolver(host, port)
    if not addresses or not any(_is_internal_address(address) for address in addresses):
        raise ValueError("public model services require HTTPS")


async def _resolve_addresses(host: str, port: int) -> Sequence[str]:
    loop = asyncio.get_running_loop()
    results = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return tuple(str(result[4][0]) for result in results)


def _is_internal_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    return _is_internal_address(host)


def _is_internal_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_unspecified
    )


__all__ = (
    "AddressResolver",
    "OpenAICompatibleStoryboardAssetRuntime",
    "TRUNCATION_WARNING",
)
