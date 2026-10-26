"""Fake-injectable OpenAI-compatible synchronous image transport."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Literal, cast
from urllib.parse import urlsplit

from haoai_backend.generation_contracts import (
    AsyncHttpClient,
    BoundCredential,
    CommittedIntent,
    CredentialBinding,
    Failure,
    FrozenExecutionSnapshot,
    MediaResult,
    ReferenceInput,
    UnknownCreate,
)

from .openai_codec import (
    OpenAIImageCodecError,
    OpenAIImageEditReference,
    OpenAIImageEditRequest,
    build_openai_image_edit_request,
    build_openai_text_to_image_request,
    parse_openai_image_response,
)


_OPENAI_TIMEOUT_SECONDS = 1800.0
_RESPONSE_CHUNK_BYTES = 64 * 1024
_MAX_REQUEST_BODY_BYTES = 1024 * 1024
_MAX_REFERENCE_IMAGES = 8
_MAX_REFERENCE_IMAGE_BYTES = 10 * 1024 * 1024
_MAX_REFERENCE_TOTAL_BYTES = _MAX_REFERENCE_IMAGES * _MAX_REFERENCE_IMAGE_BYTES
_REFERENCE_MEDIA_TYPES = frozenset(
    {"image/bmp", "image/gif", "image/jpeg", "image/png", "image/webp"}
)
_MAX_RESPONSE_BODY_BYTES = 64 * 1024 * 1024
_SUPPORTED_PROVIDERS = frozenset({"openai", "haoai"})
_CREDENTIAL_UNAVAILABLE: Literal["unavailable"] = "unavailable"

HttpClientFactory = Callable[[], AbstractAsyncContextManager[AsyncHttpClient]]
CredentialResolver = Callable[
    [CredentialBinding, Literal["create", "query", "content"]],
    BoundCredential | None,
]


@dataclass(frozen=True, slots=True)
class _OriginalImageExecutionBinding:
    """Trusted association supplied by the original execution producer.

    This local value pins the exact immutable execution snapshot, its separate
    execution reference, the original credential binding, committed intent,
    and nonsecret provider wire model. This association does not itself attest
    that the intent was durably committed.
    """

    snapshot: FrozenExecutionSnapshot
    execution_snapshot_ref: str
    credential_binding: CredentialBinding
    credential_binding_ref: str
    intent: CommittedIntent
    wire_model: str | None

    def __post_init__(self) -> None:
        if not self.execution_snapshot_ref:
            raise ValueError("execution snapshot reference is required")
        if self.intent.snapshot_ref != self.execution_snapshot_ref:
            raise ValueError("committed intent must bind the execution snapshot")
        if self.snapshot.credential_binding_ref != self.credential_binding_ref:
            raise ValueError("snapshot must bind the original credential reference")
        if (
            self.intent.provider != self.snapshot.model_ref.provider
            or self.intent.model_id != self.snapshot.model_ref.model_id
            or self.credential_binding.provider != self.snapshot.model_ref.provider
        ):
            raise ValueError("original execution model and provider must be paired")
        if (
            self.snapshot.endpoint_policy.provider_origin is not None
            and self.snapshot.endpoint_policy.provider_origin
            != self.credential_binding.endpoint_origin
        ):
            raise ValueError("snapshot and original credential endpoint must match")
        if not _valid_base_url(self.credential_binding.endpoint_origin):
            raise ValueError("original credential endpoint is invalid")
        if self.wire_model is not None and not isinstance(self.wire_model, str):
            raise TypeError("wire model must be a string or None")


class OpenAIImageProvider:
    """Implements the original JSON generation and multipart edit methods.

    Dependencies are injected and remain unopened until a supported request is
    explicitly invoked. Unrelated provider methods remain unsupported.
    """

    def __init__(
        self,
        *,
        execution: _OriginalImageExecutionBinding,
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
        """Resolve only the configured original binding, without fallback."""

        if self._execution.snapshot.model_ref.provider not in _SUPPORTED_PROVIDERS:
            return _CREDENTIAL_UNAVAILABLE
        if binding != self._execution.credential_binding or purpose != "create":
            return _CREDENTIAL_UNAVAILABLE
        try:
            credential = self._credential_resolver(binding, purpose)
        except Exception:
            return _CREDENTIAL_UNAVAILABLE
        if credential is None or not isinstance(credential, BoundCredential):
            return _CREDENTIAL_UNAVAILABLE
        if (
            credential.provider != binding.provider
            or credential.principal_id != binding.original_principal_id
            or not isinstance(credential.secret, str)
            or not credential.secret.strip()
        ):
            return _CREDENTIAL_UNAVAILABLE
        return credential

    def generate_sync_image(
        self,
        snapshot: FrozenExecutionSnapshot,
        refs: Sequence[ReferenceInput],
        intent: CommittedIntent,
    ) -> MediaResult | UnknownCreate | Failure:
        """Submit exactly one frozen create request, without retry or fallback."""

        if _running_loop_exists():
            return Failure(
                "wrong_execution_context",
                "同步图片生成不能在运行中的事件循环内执行",
            )
        invalid = self._validate_frozen_inputs(snapshot, intent)
        if invalid is not None:
            return invalid
        edit_references: tuple[OpenAIImageEditReference, ...] | None = None
        if refs:
            edit_references, invalid = _prepare_edit_references(refs)
            if invalid is not None:
                return invalid
        try:
            return asyncio.run(
                self._generate(self._execution, edit_references=edit_references)
            )
        except asyncio.CancelledError:
            return UnknownCreate(reason="图片生成请求已取消，结果未知")
        except Exception:
            return Failure(
                "provider_transport_unavailable",
                "图片生成传输未能启动",
            )

    def _validate_frozen_inputs(
        self,
        snapshot: FrozenExecutionSnapshot,
        intent: CommittedIntent,
    ) -> Failure | None:
        execution = self._execution
        if snapshot != execution.snapshot:
            return Failure(
                "frozen_execution_mismatch",
                "图片请求与原冻结执行快照不匹配",
            )
        if intent != execution.intent:
            return Failure(
                "committed_intent_mismatch",
                "图片请求与原提交意图不匹配",
            )
        if execution.snapshot.model_ref.provider not in _SUPPORTED_PROVIDERS:
            return Failure(
                "unsupported_provider",
                "当前图片适配器不支持该 provider",
            )
        return None

    async def _generate(
        self,
        execution: _OriginalImageExecutionBinding,
        *,
        edit_references: tuple[OpenAIImageEditReference, ...] | None = None,
    ) -> MediaResult | UnknownCreate | Failure:
        snapshot = execution.snapshot
        binding = execution.credential_binding
        params = _thaw_json(snapshot.model_params)
        size = params.get("size") if isinstance(params, Mapping) else None
        request: OpenAIImageEditRequest | None = None
        if edit_references is not None:
            try:
                request = build_openai_image_edit_request(
                    base_url=binding.endpoint_origin,
                    model=execution.wire_model,
                    prompt=snapshot.effective_prompt,
                    size=size if isinstance(size, str) else None,
                    references=edit_references,
                )
            except ValueError:
                return Failure(
                    "request_body_too_large",
                    "图片编辑请求超过本地传输上限",
                )

        credential = self.resolve_original_credential(binding, "create")
        if credential == _CREDENTIAL_UNAVAILABLE:
            return Failure(
                "configuration_unavailable",
                "原图片 provider 凭证不可用",
            )

        if request is None:
            try:
                text_request = build_openai_text_to_image_request(
                    base_url=binding.endpoint_origin,
                    model=execution.wire_model,
                    prompt=snapshot.effective_prompt,
                    size=size if isinstance(size, str) else None,
                    provider=snapshot.model_ref.provider,
                    params=params,
                )
            except Exception:
                return Failure("invalid_frozen_snapshot", "图片冻结参数无法编码")
            if len(text_request.body_json) > _MAX_REQUEST_BODY_BYTES:
                return Failure("request_body_too_large", "图片请求超过本地传输上限")
            request_url = text_request.url
            request_content_type = text_request.content_type
            request_body = text_request.body_json
        else:
            request_url = request.url
            request_content_type = request.content_type
            request_body = request.body

        request_started = False
        try:
            async with self._http_client_factory() as client:
                request_started = True
                response = await client.request(
                    "POST",
                    request_url,
                    headers={
                        "Authorization": f"Bearer {credential.secret}",
                        "Content-Type": request_content_type,
                    },
                    content=request_body,
                    timeout_seconds=_OPENAI_TIMEOUT_SECONDS,
                    follow_redirects=False,
                )
                status = response.status_code
                if status >= 500 or 200 <= status < 300 and status != 200:
                    return UnknownCreate(reason="图片 provider 已响应，但创建结果未知")
                if status != 200:
                    return Failure(
                        "provider_rejected",
                        f"图片生成失败 ({status})",
                    )

                body_bytes = await _read_bounded_response(response)
                try:
                    body = json.loads(body_bytes)
                    output = parse_openai_image_response(status, body)
                except (UnicodeDecodeError, json.JSONDecodeError, OpenAIImageCodecError):
                    return UnknownCreate(reason="图片 provider 响应无法确认创建结果")
                locator = (
                    output.value
                    if output.kind == "url"
                    else f"data:image/png;base64,{output.value}"
                )
                return MediaResult(
                    media_kind="image",
                    locator=locator,
                    content_digest=None,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            if request_started:
                return UnknownCreate(reason="图片请求已发送，provider 结果未知")
            return Failure(
                "provider_transport_unavailable",
                "图片 provider 传输不可用",
            )


def _prepare_edit_references(
    references: Sequence[ReferenceInput],
) -> tuple[tuple[OpenAIImageEditReference, ...] | None, Failure | None]:
    if (
        not isinstance(references, Sequence)
        or isinstance(references, (str, bytes))
        or len(references) > _MAX_REFERENCE_IMAGES
    ):
        return None, Failure(
            "request_body_too_large",
            "参考图数量超过图片编辑请求上限",
        )
    prepared: list[OpenAIImageEditReference] = []
    total_bytes = 0
    for reference in references:
        if (
            type(reference) is not ReferenceInput
            or reference.kind != "bytes"
            or reference.locator is not None
            or type(reference.content) is not bytes
            or not 1 <= len(reference.content) <= _MAX_REFERENCE_IMAGE_BYTES
            or not isinstance(reference.media_type, str)
            or reference.media_type not in _REFERENCE_MEDIA_TYPES
            or reference.provider_operation_ref is not None
        ):
            return None, Failure(
                "invalid_frozen_snapshot",
                "图片参考图未经过安全准备",
            )
        total_bytes += len(reference.content)
        if total_bytes > _MAX_REFERENCE_TOTAL_BYTES:
            return None, Failure(
                "request_body_too_large",
                "参考图数据超过图片编辑请求上限",
            )
        prepared.append(
            OpenAIImageEditReference(
                content=reference.content,
                media_type=reference.media_type,
            )
        )
    return tuple(prepared), None


def build_original_openai_image_provider(
    *,
    snapshot: FrozenExecutionSnapshot,
    execution_snapshot_ref: str,
    credential_binding: CredentialBinding,
    intent: CommittedIntent,
    wire_model: str,
    http_client_factory: HttpClientFactory,
    credential_resolver: CredentialResolver,
) -> OpenAIImageProvider:
    """Build the adapter from authenticated values without opening resources.

    Callers must independently confirm the durable intent before passing it.
    The returned provider does not treat this value shape as proof of commit.
    """

    if (
        type(snapshot) is not FrozenExecutionSnapshot
        or type(credential_binding) is not CredentialBinding
        or type(intent) is not CommittedIntent
        or not isinstance(wire_model, str)
        or not wire_model
        or snapshot.model_name != wire_model
        or snapshot.endpoint_policy.provider_origin != credential_binding.endpoint_origin
        or credential_binding.source not in {"system", "user"}
        or credential_binding.owner_or_system != snapshot.model_ref.owner_id
        or credential_binding.row_id != snapshot.model_ref.model_id
        or credential_binding.provider != snapshot.model_ref.provider
        or not callable(http_client_factory)
        or not callable(credential_resolver)
    ):
        raise ValueError("original image execution binding is incomplete")
    execution = _OriginalImageExecutionBinding(
        snapshot=snapshot,
        execution_snapshot_ref=execution_snapshot_ref,
        credential_binding=credential_binding,
        credential_binding_ref=snapshot.credential_binding_ref,
        intent=intent,
        wire_model=wire_model,
    )
    return OpenAIImageProvider(
        execution=execution,
        http_client_factory=http_client_factory,
        credential_resolver=credential_resolver,
    )


def _running_loop_exists() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    if isinstance(value, list):
        return [_thaw_json(item) for item in value]
    return value


def _valid_base_url(value: str) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.hostname)
        and "@" not in parsed.netloc
        and not parsed.query
        and not parsed.fragment
    )


async def _read_bounded_response(response: object) -> bytes:
    headers = getattr(response, "headers", {})
    content_length = _header_value(headers, "content-length")
    if content_length is not None:
        try:
            declared_size = int(content_length)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid content length") from exc
        if declared_size < 0 or declared_size > _MAX_RESPONSE_BODY_BYTES:
            raise ValueError("response body exceeds local limit")

    pending_iterator: object = response.iter_bytes(  # type: ignore[attr-defined]
        chunk_size=_RESPONSE_CHUNK_BYTES
    )
    if inspect.isawaitable(pending_iterator):
        pending_iterator = await pending_iterator
    if not hasattr(pending_iterator, "__aiter__"):
        raise TypeError("HTTP response body must be an async iterator")
    iterator = cast(AsyncIterator[bytes], pending_iterator)

    body = bytearray()
    try:
        async for chunk in iterator:
            if not isinstance(chunk, bytes):
                raise TypeError("HTTP response chunk must be bytes")
            if len(body) + len(chunk) > _MAX_RESPONSE_BODY_BYTES:
                raise ValueError("response body exceeds local limit")
            body.extend(chunk)
    finally:
        close = getattr(iterator, "aclose", None)
        if callable(close):
            close_result = close()
            if inspect.isawaitable(close_result):
                await close_result
    return bytes(body)


def _header_value(headers: object, wanted: str) -> str | None:
    if not isinstance(headers, Mapping):
        return None
    for name, value in headers.items():
        if isinstance(name, str) and name.lower() == wanted:
            return str(value)
    return None
