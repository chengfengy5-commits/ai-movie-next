"""Non-streaming OpenAI-compatible text transport for frame.review."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Literal, cast
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

_REQUEST_TIMEOUT_SECONDS = 300.0
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_RESPONSE_CHUNK_BYTES = 64 * 1024
_SUPPORTED_PROVIDERS = frozenset({"openai", "deepseek", "custom"})
_CREDENTIAL_UNAVAILABLE: Literal["unavailable"] = "unavailable"

HttpClientFactory = Callable[[], AbstractAsyncContextManager[AsyncHttpClient]]
CredentialResolver = Callable[
    [CredentialBinding, Literal["create", "query", "content"]],
    BoundCredential | None,
]


@dataclass(frozen=True, slots=True)
class _OriginalTextExecutionBinding:
    snapshot: FrozenExecutionSnapshot
    execution_snapshot_ref: str
    credential_binding: CredentialBinding
    intent: CommittedIntent
    wire_model: str
    completion_url: str


class OpenAITextProvider:
    """Make one bounded synchronous completion request with the original binding."""

    def __init__(
        self,
        *,
        execution: _OriginalTextExecutionBinding,
        http_client_factory: HttpClientFactory,
        credential_resolver: CredentialResolver,
    ) -> None:
        self._execution = execution
        self._http_client_factory = http_client_factory
        self._credential_resolver = credential_resolver

    def resolve_original_credential(
        self,
        binding: CredentialBinding,
        purpose: Literal["create", "query", "content"],
    ) -> BoundCredential | Literal["unavailable"]:
        execution = self._execution
        if binding != execution.credential_binding or purpose != "create":
            return _CREDENTIAL_UNAVAILABLE
        try:
            credential = self._credential_resolver(binding, purpose)
        except Exception:
            return _CREDENTIAL_UNAVAILABLE
        if (
            not isinstance(credential, BoundCredential)
            or credential.provider != binding.provider
            or credential.principal_id != binding.original_principal_id
            or not isinstance(credential.secret, str)
            or not credential.secret.strip()
        ):
            return _CREDENTIAL_UNAVAILABLE
        return credential

    def optimize_frame(
        self,
        snapshot: FrozenExecutionSnapshot,
        context: str,
        intent: CommittedIntent,
    ) -> TextResult | ProviderFailure:
        if _running_loop_exists():
            return ProviderFailure(
                "wrong_execution_context", False, "同步文本生成执行上下文无效"
            )
        if snapshot != self._execution.snapshot or intent != self._execution.intent:
            return ProviderFailure(
                "frozen_execution_mismatch", False, "请求与原冻结执行输入不匹配"
            )
        if context != snapshot.effective_context:
            return ProviderFailure(
                "frozen_context_mismatch", False, "请求上下文与原冻结输入不匹配"
            )
        try:
            return asyncio.run(self._request(execution=self._execution, context=context))
        except asyncio.CancelledError:
            return ProviderFailure(
                "provider_outcome_unknown", False, "文本请求已启动，生成结果未知"
            )
        except Exception:
            return ProviderFailure(
                "provider_transport_unavailable", False, "文本 provider 传输不可用"
            )

    async def _request(
        self,
        *,
        execution: _OriginalTextExecutionBinding,
        context: str,
    ) -> TextResult | ProviderFailure:
        credential = self.resolve_original_credential(
            execution.credential_binding, "create"
        )
        if credential == _CREDENTIAL_UNAVAILABLE:
            return ProviderFailure(
                "configuration_unavailable", False, "原文本 provider 凭证不可用"
            )
        try:
            body = _build_request_body(execution, context)
            encoded = json.dumps(
                body, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            ).encode("utf-8")
        except (TypeError, ValueError, KeyError):
            return ProviderFailure(
                "invalid_frozen_snapshot", False, "文本冻结参数无法编码"
            )

        request_started = False
        try:
            async with self._http_client_factory() as client:
                request_started = True
                response = await client.request(
                    "POST",
                    execution.completion_url,
                    headers={
                        "Authorization": f"Bearer {credential.secret}",
                        "Content-Type": "application/json",
                    },
                    content=encoded,
                    timeout_seconds=_REQUEST_TIMEOUT_SECONDS,
                    follow_redirects=False,
                )
                status_code = response.status_code
                payload_bytes = await _read_bounded_response(response)
        except asyncio.CancelledError:
            raise
        except Exception:
            code = "provider_outcome_unknown" if request_started else "provider_transport_unavailable"
            detail = "文本请求已启动，生成结果未知" if request_started else "文本 provider 传输不可用"
            return ProviderFailure(code, False, detail)

        if status_code != 200:
            if status_code >= 500 or 200 <= status_code < 300:
                return ProviderFailure(
                    "provider_outcome_unknown", False, "文本请求已发送，生成结果未知"
                )
            return ProviderFailure(
                "provider_rejected", False, f"文本 provider 拒绝请求（HTTP {status_code}）"
            )
        try:
            payload = json.loads(payload_bytes)
            text = _completion_text(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError, KeyError):
            return ProviderFailure(
                "provider_outcome_unknown", False, "文本请求已发送，但响应无法确认"
            )
        result = text.strip()
        if not result:
            return ProviderFailure(
                "provider_empty_result", False, "文本 provider 未返回可用正文"
            )
        finish_reason = _finish_reason(payload)
        return TextResult(text=result, finish_reason=finish_reason)


def build_original_openai_text_provider(
    *,
    snapshot: FrozenExecutionSnapshot,
    execution_snapshot_ref: str,
    credential_binding: CredentialBinding,
    intent: CommittedIntent,
    wire_model: str,
    http_client_factory: HttpClientFactory,
    credential_resolver: CredentialResolver,
) -> OpenAITextProvider:
    """Create an inert adapter bound to one frozen review intent."""

    if (
        type(snapshot) is not FrozenExecutionSnapshot
        or type(credential_binding) is not CredentialBinding
        or type(intent) is not CommittedIntent
        or not isinstance(execution_snapshot_ref, str)
        or not execution_snapshot_ref
        or intent.snapshot_ref != execution_snapshot_ref
        or intent.provider != snapshot.model_ref.provider
        or intent.model_id != snapshot.model_ref.model_id
        or not isinstance(wire_model, str)
        or not wire_model
        or snapshot.model_name != wire_model
        or credential_binding.provider != snapshot.model_ref.provider
        or credential_binding.owner_or_system != snapshot.model_ref.owner_id
        or credential_binding.row_id != snapshot.model_ref.model_id
        or not snapshot.credential_binding_ref.endswith(
            f":v{credential_binding.binding_version}"
        )
        or dict(snapshot.adapter_config_versions).get("credential_binding_version")
        != credential_binding.binding_version
        or snapshot.endpoint_policy.provider_origin != credential_binding.endpoint_origin
        or credential_binding.source not in {"system", "user"}
        or not callable(http_client_factory)
        or not callable(credential_resolver)
        or credential_binding.provider.lower() not in _SUPPORTED_PROVIDERS
    ):
        raise ValueError("original text execution binding is incomplete")
    completion_url = _completion_url(snapshot, credential_binding)
    execution = _OriginalTextExecutionBinding(
        snapshot=snapshot,
        execution_snapshot_ref=execution_snapshot_ref,
        credential_binding=credential_binding,
        intent=intent,
        wire_model=wire_model,
        completion_url=completion_url,
    )
    return OpenAITextProvider(
        execution=execution,
        http_client_factory=http_client_factory,
        credential_resolver=credential_resolver,
    )


def _build_request_body(
    execution: _OriginalTextExecutionBinding,
    context: str,
) -> dict[str, object]:
    data = json.loads(context)
    if not isinstance(data, dict):
        raise ValueError("frozen review context must be an object")
    request = data.get("request")
    frame = data.get("original_frame")
    if not isinstance(request, dict) or not isinstance(frame, dict):
        raise ValueError("frozen review context lacks target inputs")
    original_text = data.get("original_text")
    if not isinstance(original_text, str) or not original_text.strip():
        raise ValueError("frozen target text is unavailable")
    prompt_user = data.get("prompt_user")
    asset_context = request.get("asset_context")
    previous_context = request.get("prev_context")
    error_reason = request.get("error_reason")
    enable_thinking = request.get("enable_thinking") is True
    if not all(
        value is None or isinstance(value, str)
        for value in (prompt_user, asset_context, previous_context, error_reason)
    ):
        raise ValueError("frozen review prompt fields are invalid")

    system_prompt = execution.snapshot.effective_prompt
    if previous_context:
        system_prompt += (
            "\n\n【背景剧情参考（仅供理解上下文与连贯性，不要写进输出）】\n"
            + previous_context
        )
    target_block = (
        "【本次要优化的目标分镜正文如下：严格只优化这一段内容，不得包含、不得改动其它片段；"
        "必须把本段剧情完整演绎完毕，不得截断、不得省略、不得中途结束】\n"
        + original_text
    )
    user_content = (
        (prompt_user + "\n\n")
        if isinstance(prompt_user, str) and prompt_user
        else ""
    ) + target_block
    if asset_context:
        user_content = asset_context + "\n\n" + user_content
    if error_reason:
        user_content += "\n\n【失败原因参考（仅供理解问题）】\n" + error_reason

    parameters = _thaw_json(execution.snapshot.model_params)
    if not isinstance(parameters, dict):
        raise ValueError("frozen model parameters must be an object")
    body: dict[str, object] = dict(parameters)
    body.setdefault("temperature", 0.7)
    body.update(
        {
            "model": execution.wire_model,
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


def _completion_url(
    snapshot: FrozenExecutionSnapshot,
    binding: CredentialBinding,
) -> str:
    api_base_path = dict(snapshot.adapter_config_versions).get("api_base_path")
    if not isinstance(api_base_path, str) or not api_base_path.startswith("/"):
        raise ValueError("frozen API base path is unavailable")
    origin = binding.endpoint_origin
    parsed = urlsplit(origin)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("original Provider endpoint is invalid")
    path = api_base_path.rstrip("/")
    if path.endswith("/chat/completions"):
        completion_path = path
    else:
        completion_path = f"{path}/chat/completions"
    return urlunsplit((parsed.scheme, parsed.netloc, completion_path, "", ""))


async def _read_bounded_response(response: object) -> bytes:
    headers = getattr(response, "headers", {})
    content_length = _header_value(headers, "content-length")
    if content_length is not None:
        try:
            size = int(content_length)
        except (TypeError, ValueError) as error:
            raise ValueError("invalid response content length") from error
        if size < 0 or size > _MAX_RESPONSE_BYTES:
            raise ValueError("provider response exceeds the local size limit")

    pending_iterator: object = response.iter_bytes(  # type: ignore[attr-defined]
        chunk_size=_RESPONSE_CHUNK_BYTES
    )
    if hasattr(pending_iterator, "__await__"):
        pending_iterator = await cast(object, pending_iterator)  # type: ignore[misc]
    if not hasattr(pending_iterator, "__aiter__"):
        raise TypeError("HTTP response body must be an async iterator")
    iterator = cast(AsyncIterator[bytes], pending_iterator)
    body = bytearray()
    try:
        async for chunk in iterator:
            if not isinstance(chunk, bytes) or len(body) + len(chunk) > _MAX_RESPONSE_BYTES:
                raise ValueError("provider response exceeds the local size limit")
            body.extend(chunk)
    finally:
        close = getattr(iterator, "aclose", None)
        if callable(close):
            result = close()
            if hasattr(result, "__await__"):
                await result
    return bytes(body)


def _completion_text(payload: object) -> str:
    if not isinstance(payload, Mapping):
        raise ValueError("completion response must be an object")
    error = payload.get("error")
    if error:
        raise ValueError("Provider returned a completion error")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        raise ValueError("completion response has no first choice")
    message = choices[0].get("message")
    if not isinstance(message, Mapping):
        raise ValueError("completion response has no message")
    content = message.get("content")
    if not content:
        content = message.get("reasoning_content")
    if not isinstance(content, str):
        raise ValueError("completion response has no text")
    return content


def _finish_reason(payload: object) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        return None
    value = choices[0].get("finish_reason")
    return value if isinstance(value, str) else None


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    if isinstance(value, list):
        return [_thaw_json(item) for item in value]
    return value


def _header_value(headers: object, wanted: str) -> str | None:
    if not isinstance(headers, Mapping):
        return None
    for name, value in headers.items():
        if isinstance(name, str) and name.lower() == wanted:
            return str(value)
    return None


def _running_loop_exists() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True
