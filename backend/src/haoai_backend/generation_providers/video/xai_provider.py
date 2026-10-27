"""Fake-testable xAI video transport adapter for frozen generation contracts."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import math
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timezone
from typing import Literal, TypeAlias, cast
from urllib.parse import quote, unquote_plus, urlsplit

from ...generation_contracts import (
    AsyncHttpClient,
    AsyncHttpResponse,
    BoundCredential,
    CommittedIntent,
    CommittedPollReservation,
    CredentialBinding,
    ExternalOperationRef,
    Failure,
    FrozenExecutionSnapshot,
    FrozenJSON,
    GenerationError,
    MediaResult,
    ModelReference,
    OwnerTaskScope,
    ProviderFailure,
    ProviderObservation,
    ReferenceInput,
    TextEvent,
    TextResult,
    UnknownCreate,
    WebStreamAuthority,
)
from .xai_codec import (
    XaiHTTPResponseError,
    XaiResponseParseError,
    build_video_payload,
    parse_create_response,
    parse_query_response,
)


CredentialPurpose: TypeAlias = Literal["create", "query", "content"]
CredentialResolver: TypeAlias = Callable[
    [CredentialBinding, CredentialPurpose],
    BoundCredential | Literal["unavailable"],
]
HttpClientFactory: TypeAlias = Callable[
    [], AbstractAsyncContextManager[AsyncHttpClient]
]
Clock: TypeAlias = Callable[[], datetime]

_RESPONSE_BODY_LIMIT = 1_048_576
_RESPONSE_CHUNK_SIZE = 64 * 1024
_CREATE_TIMEOUT_SECONDS = 120.0
_QUERY_TIMEOUT_SECONDS = 30.0
_MEDIA_LOCATOR_LIMIT = 4096
_SAFE_QUERY_PROVIDER_STATES = frozenset(
    {"pending", "queued", "processing", "completed", "done", "failed"}
)


class _BodyTooLarge(ValueError):
    """A provider response exceeded the adapter's fixed memory bound."""


class _RequestFailed(Exception):
    """The request budget is unavailable or expired before dispatch."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_query_status(raw_status: object, secret: str) -> str:
    """Expose only known provider states, never provider-controlled free text."""
    if (
        isinstance(raw_status, str)
        and raw_status in _SAFE_QUERY_PROVIDER_STATES
        and (not secret or secret not in raw_status)
    ):
        return raw_status
    return "unknown"


def _safe_query_error(status: str, secret: str) -> str | None:
    if status != "failed":
        return None
    message = "xAI reported a provider failure"
    return None if secret and secret in message else message


def _contains_secret_echo(value: str, secret: str) -> bool:
    if not secret:
        return False

    candidate = value
    for _ in range(4):
        if secret.casefold() in candidate.casefold():
            return True
        decoded = unquote_plus(candidate)
        if decoded == candidate:
            break
        candidate = decoded
    return False


def _safe_media_locator(raw_locator: object, secret: str) -> str | None:
    """Keep bounded HTTP(S) locators while rejecting credential echoes."""
    if (
        not isinstance(raw_locator, str)
        or not raw_locator
        or len(raw_locator) > _MEDIA_LOCATOR_LIMIT
        or raw_locator != raw_locator.strip()
        or any(ord(char) <= 0x20 or ord(char) == 0x7F for char in raw_locator)
    ):
        return None

    try:
        parsed = urlsplit(raw_locator)
        _ = parsed.port
    except ValueError:
        return None

    if (
        parsed.scheme not in ("http", "https")
        or not parsed.netloc
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path in ("", "/")
        or parsed.fragment
    ):
        return None

    if secret and secret.casefold() in raw_locator.casefold():
        return None

    decoded_locator = raw_locator
    for _ in range(3):
        decoded_locator = unquote_plus(decoded_locator)
        if secret and secret.casefold() in decoded_locator.casefold():
            return None

    return raw_locator


def _freeze_error(
    code: Literal[
        "invalid_generation_request",
        "evidence_conflict_or_invalid",
        "configuration_unavailable",
    ],
    detail: str,
) -> GenerationError:
    return GenerationError(code, detail)


class XaiVideoProvider:
    """Implements only xAI asynchronous video create and original-ID query.

    The caller supplies the trusted frozen snapshot, model, owner scope, and
    credential-binding pair. Network and secret dependencies stay lazy.
    """

    def __init__(
        self,
        *,
        owner_scope: OwnerTaskScope,
        original_snapshot: FrozenExecutionSnapshot,
        original_intent: CommittedIntent,
        credential_binding: CredentialBinding,
        credential_binding_ref: str,
        snapshot_ref: str,
        model_ref: ModelReference,
        wire_model: str,
        http_client_factory: HttpClientFactory,
        credential_resolver: CredentialResolver,
        clock: Clock = _utc_now,
    ) -> None:
        if not isinstance(owner_scope, OwnerTaskScope):
            raise TypeError("owner_scope must be a trusted OwnerTaskScope")
        if not isinstance(original_snapshot, FrozenExecutionSnapshot):
            raise TypeError("original_snapshot must be a trusted frozen snapshot")
        if not isinstance(original_intent, CommittedIntent):
            raise TypeError("original_intent must be a trusted CommittedIntent")
        if not isinstance(credential_binding, CredentialBinding):
            raise TypeError("credential_binding must be a trusted CredentialBinding")
        if not isinstance(model_ref, ModelReference):
            raise TypeError("model_ref must be a trusted ModelReference")
        if not callable(http_client_factory) or not callable(credential_resolver):
            raise TypeError("provider dependencies must be callable")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if not credential_binding_ref or not credential_binding_ref.strip():
            raise ValueError("credential_binding_ref must be nonblank")
        if not snapshot_ref or not snapshot_ref.strip():
            raise ValueError("snapshot_ref must be nonblank")
        if not wire_model or not wire_model.strip():
            raise ValueError("wire_model must be nonblank")
        if credential_binding.provider != "xai" or model_ref.provider != "xai":
            raise ValueError("xAI provider configuration must use provider xai")
        if (
            snapshot_ref != original_intent.snapshot_ref
            or original_snapshot.model_ref != model_ref
            or original_snapshot.credential_binding_ref != credential_binding_ref
            or original_snapshot.endpoint_policy.provider_origin
            != credential_binding.endpoint_origin
            or original_snapshot.grant_snapshot.actor_id != owner_scope.user_id
        ):
            raise ValueError("trusted xAI provider inputs do not form one original binding")
        if not owner_scope.user_id or not owner_scope.task_id or not owner_scope.operation:
            raise ValueError("owner_scope must contain its trusted original identifiers")

        self._owner_scope = owner_scope
        self._original_snapshot = original_snapshot
        self._original_intent = original_intent
        self._credential_binding = credential_binding
        self._credential_binding_ref = credential_binding_ref
        self._snapshot_ref = snapshot_ref
        self._model_ref = model_ref
        self._wire_model = wire_model
        self._http_client_factory = http_client_factory
        self._credential_resolver = credential_resolver
        self._clock = clock

    def resolve_original_credential(
        self,
        binding: CredentialBinding,
        purpose: CredentialPurpose,
    ) -> BoundCredential | Literal["unavailable"]:
        """Resolve only the exact original binding and supported request purpose."""
        if binding is not self._credential_binding or purpose not in ("create", "query"):
            return "unavailable"

        try:
            resolved = self._credential_resolver(binding, purpose)
        except Exception:
            return "unavailable"

        if resolved == "unavailable" or not isinstance(resolved, BoundCredential):
            return "unavailable"
        if (
            resolved.provider != binding.provider
            or resolved.principal_id != binding.original_principal_id
            or not resolved.secret
            or not resolved.secret.strip()
        ):
            return "unavailable"
        return resolved

    def submit_async_media(
        self,
        snapshot: FrozenExecutionSnapshot,
        refs: Sequence[ReferenceInput],
        intent: CommittedIntent,
    ) -> ExternalOperationRef:
        self._require_sync_context()
        return asyncio.run(self._submit_async_media(snapshot, refs, intent))

    def query_existing(
        self,
        snapshot: FrozenExecutionSnapshot,
        external_ref: ExternalOperationRef,
        reservation: CommittedPollReservation | None = None,
    ) -> ProviderObservation:
        self._require_sync_context()
        return asyncio.run(
            self._query_existing(snapshot, external_ref, reservation)
        )

    def stream_text(
        self,
        snapshot: FrozenExecutionSnapshot,
        authority: WebStreamAuthority,
    ) -> AsyncIterator[TextEvent]:
        raise NotImplementedError("xAI video adapter does not implement text streaming")

    def optimize_frame(
        self, snapshot: FrozenExecutionSnapshot, context: str, intent: CommittedIntent
    ) -> TextResult | ProviderFailure:
        raise NotImplementedError("xAI video adapter does not implement frame optimization")

    def generate_sync_image(
        self,
        snapshot: FrozenExecutionSnapshot,
        refs: Sequence[ReferenceInput],
        intent: CommittedIntent,
    ) -> MediaResult | UnknownCreate | Failure:
        raise NotImplementedError("xAI video adapter does not implement image generation")

    def fetch_provider_content(
        self, snapshot: FrozenExecutionSnapshot, external_ref: ExternalOperationRef
    ) -> ReferenceInput:
        raise NotImplementedError("xAI video adapter does not fetch provider content")

    @staticmethod
    def _require_sync_context() -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        raise RuntimeError(
            "synchronous provider methods cannot run inside an active event loop"
        )

    def _validate_snapshot(
        self, snapshot: FrozenExecutionSnapshot
    ) -> str:
        if not isinstance(snapshot, FrozenExecutionSnapshot):
            raise _freeze_error(
                "invalid_generation_request", "a frozen execution snapshot is required"
            )
        if snapshot != self._original_snapshot:
            raise _freeze_error(
                "evidence_conflict_or_invalid",
                "snapshot does not match the original trusted execution snapshot",
            )
        if snapshot.model_ref != self._model_ref:
            raise _freeze_error(
                "evidence_conflict_or_invalid",
                "snapshot model binding does not match the configured provider",
            )
        if snapshot.credential_binding_ref != self._credential_binding_ref:
            raise _freeze_error(
                "evidence_conflict_or_invalid",
                "snapshot credential binding does not match the original binding",
            )
        if snapshot.grant_snapshot.actor_id != self._owner_scope.user_id:
            raise _freeze_error(
                "evidence_conflict_or_invalid",
                "snapshot actor does not match the original owner scope",
            )
        if self._credential_binding.provider != self._model_ref.provider:
            raise _freeze_error(
                "evidence_conflict_or_invalid",
                "model and credential provider bindings do not match",
            )
        if snapshot.endpoint_policy.provider_origin != self._credential_binding.endpoint_origin:
            raise _freeze_error(
                "evidence_conflict_or_invalid",
                "snapshot endpoint does not match the original credential binding",
            )

        origin = self._credential_binding.endpoint_origin
        try:
            parsed = urlsplit(origin)
            valid_origin = (
                parsed.scheme == "https"
                and bool(parsed.netloc)
                and parsed.username is None
                and parsed.password is None
                and parsed.path in ("", "/")
                and parsed.query == ""
                and parsed.fragment == ""
                and parsed.hostname is not None
            )
            _ = parsed.port
        except ValueError:
            valid_origin = False
        if not valid_origin:
            raise _freeze_error(
                "configuration_unavailable",
                "the original xAI endpoint origin is unavailable",
            )
        return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")

    def _validate_intent(self, intent: CommittedIntent) -> None:
        if not isinstance(intent, CommittedIntent):
            raise _freeze_error(
                "invalid_generation_request", "a committed intent is required"
            )
        if (
            intent != self._original_intent
            or intent.provider != "xai"
            or intent.model_id != self._model_ref.model_id
            or intent.snapshot_ref != self._snapshot_ref
            or not isinstance(intent.step_id, str)
            or not intent.step_id.strip()
            or not isinstance(intent.attempt_id, str)
            or not intent.attempt_id.strip()
            or not isinstance(intent.external_submission_id, str)
            or not intent.external_submission_id.strip()
        ):
            raise _freeze_error(
                "evidence_conflict_or_invalid",
                "committed intent does not match the original xAI execution",
            )

    @staticmethod
    def _reference_urls(
        refs: Sequence[ReferenceInput] | None,
    ) -> tuple[str, ...]:
        if refs is None:
            return ()
        if isinstance(refs, (str, bytes)) or not isinstance(refs, Sequence):
            raise _freeze_error(
                "invalid_generation_request", "video references must be a sequence"
            )

        urls: list[str] = []
        for reference in refs:
            if not isinstance(reference, ReferenceInput):
                raise _freeze_error(
                    "invalid_generation_request", "video reference has an invalid type"
                )
            if reference.kind not in ("managed_url", "public_http"):
                raise _freeze_error(
                    "invalid_generation_request",
                    "xAI video accepts URL references in this adapter batch",
                )
            locator = reference.locator
            if not isinstance(locator, str) or not locator.strip():
                raise _freeze_error(
                    "invalid_generation_request", "video reference URL is missing"
                )
            try:
                parsed = urlsplit(locator)
                valid_url = (
                    parsed.scheme in ("http", "https")
                    and bool(parsed.netloc)
                    and parsed.username is None
                    and parsed.password is None
                    and parsed.fragment == ""
                    and parsed.hostname is not None
                )
                _ = parsed.port
            except ValueError:
                valid_url = False
            if not valid_url:
                raise _freeze_error(
                    "invalid_generation_request", "video reference URL is invalid"
                )
            urls.append(locator)
        return tuple(urls)

    @staticmethod
    def _parameter_default(
        snapshot: FrozenExecutionSnapshot, key: str, default: object
    ) -> object:
        configured = snapshot.model_params.get(key)
        if isinstance(configured, Mapping):
            return configured.get("default", default)
        return default if configured is None else configured

    def _request_content(
        self,
        snapshot: FrozenExecutionSnapshot,
        refs: Sequence[ReferenceInput] | None,
    ) -> bytes:
        if not isinstance(snapshot.effective_prompt, str):
            raise _freeze_error(
                "invalid_generation_request", "frozen video prompt is invalid"
            )
        reference_urls = self._reference_urls(refs)
        size = self._parameter_default(snapshot, "size", None)
        duration = self._parameter_default(snapshot, "duration", None)
        resolution = self._parameter_default(snapshot, "resolution", None)
        if size is not None and not isinstance(size, str):
            raise _freeze_error(
                "invalid_generation_request", "frozen video size is invalid"
            )
        if resolution is not None and not isinstance(resolution, str):
            raise _freeze_error(
                "invalid_generation_request", "frozen video resolution is invalid"
            )
        if duration is not None and (
            isinstance(duration, bool) or not isinstance(duration, int)
        ):
            raise _freeze_error(
                "invalid_generation_request", "frozen video duration is invalid"
            )

        max_images_value = self._parameter_default(
            snapshot, "max_reference_images", 9
        )
        try:
            max_reference_images = int(max_images_value)
        except (TypeError, ValueError, OverflowError):
            max_reference_images = 9

        payload = build_video_payload(
            model=self._wire_model,
            prompt=snapshot.effective_prompt,
            size=size,
            duration=duration,
            resolution=resolution,
            reference_images=reference_urls,
            max_reference_images=max_reference_images,
        )
        try:
            return json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeEncodeError) as exc:
            raise _freeze_error(
                "invalid_generation_request", "frozen video request settings are invalid"
            ) from exc

    def _credential(
        self, purpose: Literal["create", "query"]
    ) -> BoundCredential:
        credential = self.resolve_original_credential(
            self._credential_binding, purpose
        )
        if credential == "unavailable":
            raise _freeze_error(
                "configuration_unavailable",
                "the original xAI credential binding is unavailable",
            )
        return credential

    @staticmethod
    async def _read_bounded(
        response: AsyncHttpResponse, deadline: float
    ) -> tuple[bytes, object]:
        candidate = response.iter_bytes(chunk_size=_RESPONSE_CHUNK_SIZE)
        iterator = await candidate if inspect.isawaitable(candidate) else candidate
        if not hasattr(iterator, "__aiter__"):
            raise TypeError("provider response iterator is invalid")

        body = bytearray()
        try:
            async for chunk in iterator:
                if not isinstance(chunk, bytes):
                    raise TypeError("provider response chunks must be bytes")
                if len(body) + len(chunk) > _RESPONSE_BODY_LIMIT:
                    raise _BodyTooLarge("provider response exceeded its byte limit")
                body.extend(chunk)
        except BaseException:
            try:
                await XaiVideoProvider._close_iterator_until(iterator, deadline)
            except BaseException:
                pass
            raise

        return bytes(body), iterator

    @staticmethod
    async def _close_iterator_until(iterator: object, deadline: float) -> bool:
        close = getattr(iterator, "aclose", None)
        if not callable(close):
            return True
        try:
            async with asyncio.timeout_at(deadline):
                close_result = close()
                if inspect.isawaitable(close_result):
                    await close_result
        except TimeoutError:
            return False
        return True

    @staticmethod
    async def _close_client_context_until(
        context: AbstractAsyncContextManager[AsyncHttpClient],
        deadline: float,
        error: BaseException | None,
    ) -> bool:
        try:
            async with asyncio.timeout_at(deadline):
                await context.__aexit__(
                    type(error) if error is not None else None,
                    error,
                    error.__traceback__ if error is not None else None,
                )
        except TimeoutError:
            return False
        return True

    @staticmethod
    def _unknown_create(intent: CommittedIntent) -> ExternalOperationRef:
        return ExternalOperationRef(
            intent_id=intent.external_submission_id,
            attempt_id=intent.attempt_id,
            provider=intent.provider,
            external_id=None,
            create_outcome="unknown",
        )

    async def _submit_async_media(
        self,
        snapshot: FrozenExecutionSnapshot,
        refs: Sequence[ReferenceInput] | None,
        intent: CommittedIntent,
    ) -> ExternalOperationRef:
        origin = self._validate_snapshot(snapshot)
        self._validate_intent(intent)
        content = self._request_content(snapshot, refs)
        credential = self._credential("create")
        headers = {
            "Authorization": f"Bearer {credential.secret}",
            "Content-Type": "application/json",
        }

        request_started = False
        response_status: int | None = None
        body: bytes | None = None
        iterator: object | None = None
        operation_error: BaseException | None = None
        client_context: AbstractAsyncContextManager[AsyncHttpClient] | None = None
        client_entered = False
        deadline = 0.0
        try:
            request_timeout = self._remaining_request_timeout(_CREATE_TIMEOUT_SECONDS)
            deadline = asyncio.get_running_loop().time() + request_timeout
            async with asyncio.timeout_at(deadline):
                client_context = self._http_client_factory()
                client = await client_context.__aenter__()
                client_entered = True
                request_started = True
                response = await client.request(
                    "POST",
                    f"{origin}/v1/videos/generations",
                    headers=headers,
                    content=content,
                    timeout_seconds=request_timeout,
                    follow_redirects=False,
                )
                response_status = response.status_code
                if (
                    isinstance(response_status, bool)
                    or not isinstance(response_status, int)
                ):
                    response_status = None
                elif response_status == 200:
                    body, iterator = await self._read_bounded(response, deadline)
        except BaseException as exc:
            operation_error = exc

        cleanup_completed = True
        cleanup_cancel: asyncio.CancelledError | None = None
        cleanup_error: BaseException | None = None
        if iterator is not None:
            try:
                cleanup_completed = await self._close_iterator_until(
                    iterator, deadline
                ) and cleanup_completed
            except asyncio.CancelledError as exc:
                cleanup_cancel = exc
            except BaseException as exc:
                cleanup_error = exc
        if client_entered and client_context is not None:
            try:
                cleanup_completed = await self._close_client_context_until(
                    client_context, deadline, operation_error
                ) and cleanup_completed
            except asyncio.CancelledError as exc:
                cleanup_cancel = cleanup_cancel or exc
            except BaseException as exc:
                cleanup_error = cleanup_error or exc

        if isinstance(operation_error, asyncio.CancelledError):
            raise operation_error
        if cleanup_cancel is not None:
            raise cleanup_cancel
        if operation_error is not None and not isinstance(operation_error, Exception):
            raise operation_error

        if response_status == 200 and body is not None:
            try:
                parsed = parse_create_response(200, body)
            except (XaiHTTPResponseError, XaiResponseParseError, TypeError, ValueError):
                return self._unknown_create(intent)

            request_id = parsed.request_id
            if (
                isinstance(request_id, str)
                and request_id.strip()
                and not _contains_secret_echo(request_id, credential.secret)
            ):
                return ExternalOperationRef(
                    intent_id=intent.external_submission_id,
                    attempt_id=intent.attempt_id,
                    provider=intent.provider,
                    external_id=request_id,
                    create_outcome="submitted",
                )
            return self._unknown_create(intent)

        if operation_error is not None:
            if request_started:
                return self._unknown_create(intent)
            raise _freeze_error(
                "configuration_unavailable",
                "the xAI HTTP client is unavailable",
            ) from operation_error

        if not cleanup_completed or cleanup_error is not None:
            return self._unknown_create(intent)

        if response_status == 408:
            return self._unknown_create(intent)
        if response_status is not None and 400 <= response_status < 500:
            return ExternalOperationRef(
                intent_id=intent.external_submission_id,
                attempt_id=intent.attempt_id,
                provider=intent.provider,
                external_id=None,
                create_outcome="definitive_failure",
                last_safe_provider_state=str(response_status),
            )

        return self._unknown_create(intent)

    def _validate_external_ref(self, external_ref: ExternalOperationRef) -> None:
        if not isinstance(external_ref, ExternalOperationRef):
            raise _freeze_error(
                "invalid_generation_request", "an original external operation reference is required"
            )
        if (
            external_ref.provider != "xai"
            or external_ref.intent_id != self._original_intent.external_submission_id
            or external_ref.attempt_id != self._original_intent.attempt_id
            or external_ref.provider != self._original_intent.provider
            or external_ref.create_outcome != "submitted"
            or not isinstance(external_ref.external_id, str)
            or not external_ref.external_id.strip()
        ):
            raise _freeze_error(
                "evidence_conflict_or_invalid",
                "external operation reference is not an original submitted xAI request",
            )

    def _validate_reservation(
        self,
        external_ref: ExternalOperationRef,
        reservation: CommittedPollReservation | None,
    ) -> None:
        if reservation is None:
            return
        if not isinstance(reservation, CommittedPollReservation):
            raise _freeze_error(
                "invalid_generation_request", "a committed poll reservation is required"
            )
        poll = reservation.reservation
        if (
            poll.step_id != self._original_intent.step_id
            or poll.submission_id != self._original_intent.external_submission_id
            or poll.attempt_id != self._original_intent.attempt_id
            or poll.external_id != external_ref.external_id
        ):
            raise _freeze_error(
                "evidence_conflict_or_invalid",
                "poll reservation does not match the original xAI request",
            )

    def _observation(
        self,
        *,
        snapshot: FrozenExecutionSnapshot,
        external_ref: ExternalOperationRef,
        status: str,
        payload: FrozenJSON,
        body_digest: str,
        media_locator: str | None = None,
        safe_error: str | None = None,
    ) -> ProviderObservation:
        received_at = self._clock()
        if (
            not isinstance(received_at, datetime)
            or received_at.tzinfo is None
            or received_at.utcoffset() is None
        ):
            raise _freeze_error(
                "configuration_unavailable",
                "the provider observation clock is unavailable",
            )
        dedupe_material = "\0".join(
            (
                external_ref.provider,
                external_ref.intent_id,
                external_ref.attempt_id,
                external_ref.external_id or "",
                body_digest,
            )
        ).encode("utf-8")
        return ProviderObservation(
            owner_scope=self._owner_scope,
            intent_id=external_ref.intent_id,
            attempt_id=external_ref.attempt_id,
            external_ref=external_ref,
            dedupe_key=hashlib.sha256(dedupe_material).hexdigest(),
            payload_digest=body_digest,
            received_at=received_at,
            status=status,
            media_locator=media_locator,
            safe_error=safe_error,
            payload=payload,
        )

    async def _query_existing(
        self,
        snapshot: FrozenExecutionSnapshot,
        external_ref: ExternalOperationRef,
        reservation: CommittedPollReservation | None,
    ) -> ProviderObservation:
        origin = self._validate_snapshot(snapshot)
        self._validate_intent(self._original_intent)
        self._validate_external_ref(external_ref)
        self._validate_reservation(external_ref, reservation)
        credential = self._credential("query")
        headers = {"Authorization": f"Bearer {credential.secret}"}
        encoded_id = quote(cast(str, external_ref.external_id), safe="")

        request_started = False
        response_status: int | None = None
        body: bytes | None = None
        iterator: object | None = None
        operation_error: BaseException | None = None
        client_context: AbstractAsyncContextManager[AsyncHttpClient] | None = None
        client_entered = False
        deadline = 0.0
        try:
            request_timeout = self._remaining_request_timeout(_QUERY_TIMEOUT_SECONDS)
            deadline = asyncio.get_running_loop().time() + request_timeout
            async with asyncio.timeout_at(deadline):
                client_context = self._http_client_factory()
                client = await client_context.__aenter__()
                client_entered = True
                request_started = True
                response = await client.request(
                    "GET",
                    f"{origin}/v1/videos/{encoded_id}",
                    headers=headers,
                    timeout_seconds=request_timeout,
                    follow_redirects=False,
                )
                response_status = response.status_code
                if (
                    isinstance(response_status, bool)
                    or not isinstance(response_status, int)
                ):
                    raise _freeze_error(
                        "evidence_conflict_or_invalid",
                        "xAI returned an invalid HTTP status",
                    )
                if response_status == 200:
                    body, iterator = await self._read_bounded(response, deadline)
        except BaseException as exc:
            operation_error = exc

        cleanup_completed = True
        cleanup_cancel: asyncio.CancelledError | None = None
        cleanup_error: BaseException | None = None
        if iterator is not None:
            try:
                cleanup_completed = await self._close_iterator_until(
                    iterator, deadline
                ) and cleanup_completed
            except asyncio.CancelledError as exc:
                cleanup_cancel = exc
            except BaseException as exc:
                cleanup_error = exc
        if client_entered and client_context is not None:
            try:
                cleanup_completed = await self._close_client_context_until(
                    client_context, deadline, operation_error
                ) and cleanup_completed
            except asyncio.CancelledError as exc:
                cleanup_cancel = cleanup_cancel or exc
            except BaseException as exc:
                cleanup_error = cleanup_error or exc

        if isinstance(operation_error, asyncio.CancelledError):
            raise operation_error
        if cleanup_cancel is not None:
            raise cleanup_cancel
        if isinstance(operation_error, _BodyTooLarge):
            raise _freeze_error(
                "evidence_conflict_or_invalid",
                "xAI query response exceeded the bounded body limit",
            ) from operation_error
        if isinstance(operation_error, GenerationError):
            raise operation_error
        if operation_error is not None and not isinstance(operation_error, Exception):
            raise operation_error

        if operation_error is not None:
            if request_started:
                raise _freeze_error(
                    "configuration_unavailable",
                    "the xAI query could not be completed",
                ) from operation_error
            raise _freeze_error(
                "configuration_unavailable",
                "the xAI HTTP client is unavailable",
            ) from operation_error

        if not cleanup_completed or cleanup_error is not None:
            raise _freeze_error(
                "configuration_unavailable",
                "the xAI query could not be completed",
            )

        if response_status != 200:
            payload: FrozenJSON = cast(
                FrozenJSON, {"http_status": response_status}
            )
            digest = hashlib.sha256(
                f"http-status:{response_status}".encode("ascii")
            ).hexdigest()
            return self._observation(
                snapshot=snapshot,
                external_ref=external_ref,
                status=str(response_status),
                payload=payload,
                body_digest=digest,
            )

        if body is None:
            raise _freeze_error(
                "configuration_unavailable",
                "the xAI query could not be completed",
            )

        try:
            parsed = parse_query_response(200, body)
        except (XaiHTTPResponseError, XaiResponseParseError, TypeError, ValueError) as exc:
            raise _freeze_error(
                "evidence_conflict_or_invalid",
                "xAI returned a malformed query response",
            ) from exc

        status = _safe_query_status(parsed.status, credential.secret)
        safe_error = _safe_query_error(status, credential.secret)
        media_locator = _safe_media_locator(parsed.video_url, credential.secret)
        digest = hashlib.sha256(body).hexdigest()
        return self._observation(
            snapshot=snapshot,
            external_ref=external_ref,
            status=status,
            payload=cast(FrozenJSON, {"http_status": 200}),
            body_digest=digest,
            media_locator=media_locator,
            safe_error=safe_error,
        )

    def _remaining_request_timeout(self, request_limit: float) -> float:
        try:
            now = self._clock()
            deadline = self._original_intent.deadline_at
            if (
                not isinstance(now, datetime)
                or now.tzinfo is None
                or now.utcoffset() is None
                or not isinstance(deadline, datetime)
                or deadline.tzinfo is None
                or deadline.utcoffset() is None
            ):
                raise ValueError
            remaining = (
                deadline.astimezone(timezone.utc) - now.astimezone(timezone.utc)
            ).total_seconds()
        except Exception:
            raise _RequestFailed from None
        if not math.isfinite(remaining) or remaining <= 0:
            raise _RequestFailed
        return min(request_limit, remaining)
