"""One-shot text transport for an authenticated frame.optimize item."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from haoai_backend.generation_contracts import (
    AsyncHttpClient,
    BoundCredential,
    CommittedIntent,
    CredentialBinding,
    FrozenExecutionSnapshot,
    ProviderFailure,
    TextResult,
)
from haoai_backend.generation_credentials.frame_optimize_snapshot_reader import (
    VerifiedFrameOptimizeExecutionItem,
)

_REQUEST_TIMEOUT_SECONDS = 300.0
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_RESPONSE_CHUNK_BYTES = 64 * 1024

HttpClientFactory = Callable[[], AbstractAsyncContextManager[AsyncHttpClient]]
CredentialResolver = Callable[
    [CredentialBinding, Literal["create", "query", "content"]],
    BoundCredential | None,
]


@dataclass(slots=True)
class FrameOptimizeTextProvider:
    """Send one fresh committed intent using only its authenticated snapshot."""

    snapshot: FrozenExecutionSnapshot
    item: VerifiedFrameOptimizeExecutionItem
    intent: CommittedIntent
    http_client_factory: HttpClientFactory
    credential_resolver: CredentialResolver
    authority_guard: object

    def optimize_frame(self) -> TextResult | ProviderFailure:
        if _running_loop_exists():
            return ProviderFailure(
                "wrong_execution_context", False, "同步文本生成执行上下文无效"
            )
        if self.item.snapshot != self.snapshot or self.intent.step_id != self.item.step_id:
            return ProviderFailure(
                "frozen_execution_mismatch", False, "请求与原冻结执行输入不匹配"
            )
        try:
            return asyncio.run(self._request())
        except asyncio.CancelledError:
            return ProviderFailure(
                "provider_outcome_unknown", False, "文本请求已启动，生成结果未知"
            )
        except Exception:
            return ProviderFailure(
                "provider_transport_unavailable", False, "文本 provider 传输不可用"
            )

    async def _request(self) -> TextResult | ProviderFailure:
        guard = self.authority_guard
        guard.require_live_before_deadline()
        try:
            credential = self.credential_resolver(self.item.credential_binding, "create")
        except Exception:
            credential = None
        guard.require_live_before_deadline()
        if (
            not isinstance(credential, BoundCredential)
            or credential.provider != self.item.credential_binding.provider
            or credential.principal_id != self.item.credential_binding.original_principal_id
            or not isinstance(credential.secret, str)
            or not credential.secret.strip()
        ):
            return ProviderFailure(
                "configuration_unavailable", False, "原文本 provider 凭证不可用"
            )

        try:
            body = _build_request_body(self.item)
            encoded = json.dumps(
                body, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            ).encode("utf-8")
            url = _completion_url(self.snapshot, self.item.credential_binding)
        except (TypeError, ValueError, KeyError):
            return ProviderFailure(
                "invalid_frozen_snapshot", False, "文本冻结参数无法编码"
            )

        status_code: int | None = None
        try:
            guard.require_live_before_deadline()
            async with self.http_client_factory() as client:
                guard.require_live_before_deadline()
                guard.mark_request_started()
                response = await client.request(
                    "POST",
                    url,
                    headers={
                        "Authorization": f"Bearer {credential.secret}",
                        "Content-Type": "application/json",
                    },
                    content=encoded,
                    timeout_seconds=_REQUEST_TIMEOUT_SECONDS,
                    follow_redirects=False,
                )
                status_code = getattr(response, "status_code", None)
                guard.record_http_status(status_code)
                payload_bytes = await _read_bounded_response(response)
        except asyncio.CancelledError:
            raise
        except Exception:
            return ProviderFailure(
                "provider_outcome_unknown",
                False,
                "文本请求已发送，生成结果未知",
            )

        if status_code != 200:
            if isinstance(status_code, int) and 400 <= status_code < 500:
                return ProviderFailure(
                    "provider_rejected", False, f"文本 provider 拒绝请求（HTTP {status_code}）"
                )
            return ProviderFailure(
                "provider_outcome_unknown", False, "文本请求已发送，生成结果未知"
            )
        try:
            payload = json.loads(payload_bytes)
            text = _completion_text(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError, KeyError):
            return ProviderFailure(
                "provider_outcome_unknown", False, "文本请求已发送，但响应无法确认"
            )
        if not text.strip():
            return ProviderFailure(
                "provider_empty_result", False, "文本 provider 未返回可用正文"
            )
        return TextResult(text=text.strip(), finish_reason=_finish_reason(payload))


def _build_request_body(item: VerifiedFrameOptimizeExecutionItem) -> dict[str, object]:
    snapshot = item.snapshot
    request = item.request
    frames = request.get("frames")
    if not isinstance(frames, (tuple, list)) or item.order >= len(frames):
        raise ValueError("authenticated optimize request has no matching frame")
    frame = frames[item.order]
    if not isinstance(frame, Mapping) or frame.get("index") != item.frame_index:
        raise ValueError("authenticated optimize frame order changed")
    asset_context = frame.get("asset_context", "")
    prompt_user = item.prompt.get("user_prompt", "")
    if not isinstance(asset_context, str) or not isinstance(prompt_user, str):
        raise ValueError("authenticated optimize prompt fields are invalid")

    try:
        frozen_context = json.loads(snapshot.effective_context)
        request_context = frozen_context["request"]
        previous_frames = request_context["previous_frames"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("authenticated optimize context is invalid") from error
    if not isinstance(previous_frames, list) or any(
        not isinstance(value, str) for value in previous_frames
    ):
        raise ValueError("authenticated optimize previous context is invalid")
    previous_context = "\n\n".join(previous_frames)
    system_prompt = snapshot.effective_prompt
    if previous_context:
        system_prompt += "\n\n【背景剧情参考（仅供理解上下文与连贯性，不要写进输出）】\n" + previous_context
    user_content = (prompt_user + "\n\n" if prompt_user else "") + item.requested_text
    if asset_context:
        user_content = asset_context + "\n\n" + user_content

    parameters = _plain(snapshot.model_params)
    if not isinstance(parameters, dict):
        raise ValueError("frozen model parameters are invalid")
    body = dict(parameters)
    body.setdefault("temperature", 0.7)
    enable_thinking = request.get("enable_thinking") is True
    body.update(
        {
            "model": snapshot.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "stream": False,
            "thinking": {"type": "enabled" if enable_thinking else "disabled"},
        }
    )
    if enable_thinking:
        body["reasoning_effort"] = "high"
    else:
        body.pop("reasoning_effort", None)
    return body


def _completion_url(snapshot: FrozenExecutionSnapshot, binding: CredentialBinding) -> str:
    api_base_path = dict(snapshot.adapter_config_versions).get("api_base_path")
    parsed = urlsplit(binding.endpoint_origin)
    if (
        not isinstance(api_base_path, str)
        or not api_base_path.startswith("/")
        or parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or binding.endpoint_origin != snapshot.endpoint_policy.provider_origin
    ):
        raise ValueError("original Provider endpoint is invalid")
    path = api_base_path.rstrip("/") + "/chat/completions"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


async def _read_bounded_response(response: object) -> bytes:
    iterator_factory = getattr(response, "iter_bytes", None)
    if not callable(iterator_factory):
        raise ValueError("Provider response is not streamable")
    iterator = iterator_factory(chunk_size=_RESPONSE_CHUNK_BYTES)
    if not hasattr(iterator, "__aiter__"):
        raise ValueError("Provider response iterator is invalid")
    chunks: list[bytes] = []
    total = 0
    try:
        async for chunk in iterator:
            if not isinstance(chunk, bytes):
                raise ValueError("Provider response chunk is invalid")
            total += len(chunk)
            if total > _MAX_RESPONSE_BYTES:
                raise ValueError("Provider response exceeded the size limit")
            chunks.append(chunk)
    finally:
        close = getattr(iterator, "aclose", None)
        if callable(close):
            await close()
    return b"".join(chunks)


def _completion_text(payload: object) -> str:
    if not isinstance(payload, dict):
        raise ValueError("Provider response must be an object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ValueError("Provider response has no completion choice")
    message = choices[0].get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [part["text"] for part in content if isinstance(part, dict) and isinstance(part.get("text"), str)]
        return "".join(parts)
    raise ValueError("Provider response has no text content")


def _finish_reason(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    value = choices[0].get("finish_reason")
    return value if isinstance(value, str) else None


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _running_loop_exists() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


__all__ = ("FrameOptimizeTextProvider",)
