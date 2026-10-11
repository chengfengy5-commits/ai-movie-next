"""HaoAI's bounded synchronous adapter over the frozen video provider port.

This module owns one committed execution binding at a time. It projects the
legacy JSON protocol into safe contract values and never performs media
transfer, retry, polling, billing, persistence, or Task finalization.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import inspect
import json
import re
import threading
from collections import deque
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import quote, unquote, unquote_plus, urlsplit

from haoai_backend.generation_contracts import (
    AsyncHttpClient,
    BoundCredential,
    CommittedIntent,
    CommittedPollReservation,
    CredentialBinding,
    ExternalOperationRef,
    Failure,
    FrozenExecutionSnapshot,
    MediaResult,
    OwnerTaskScope,
    ProviderFailure,
    ProviderObservation,
    ReferenceInput,
    TextEvent,
    TextResult,
    UnknownCreate,
    WebStreamAuthority,
)
from haoai_backend.generation_providers.video.haoai_codec import (
    DEFAULT_HAOAI_VIDEO_MODEL,
    DEFAULT_MAX_REFERENCE_IMAGES,
    build_haoai_video_create_payload,
    haoai_video_create_url,
    haoai_video_query_url,
    parse_haoai_create_response,
    parse_haoai_query_response,
)

PROVIDER_NAME = "haoai"
VIDEO_CATEGORY = "video"
MAX_RESPONSE_BYTES = 1024 * 1024
RESPONSE_CHUNK_BYTES = 64 * 1024
MAX_CANDIDATE_CHARACTERS = 8192
MAX_SHADOW_ROUNDS = 8
MAX_SHADOW_NODES = 64
MAX_TOTAL_SHADOW_CHARACTERS = 65536
_UNSUPPORTED_DETAIL = "HaoAI video adapter operation is unsupported"
_BINDING_ERROR = "HaoAI execution binding is invalid"
_CREDENTIAL_ERROR = "HaoAI original credential is unavailable"
_IDENTITY_ERROR = "HaoAI original provider identity is unusable"
_CREATE_UNKNOWN_STATE = "create_outcome_unknown"
_CREATE_REJECTED_STATE = "provider_rejected_request"
_UNSAFE_ID_STATE = "provider_identity_unusable"
_UNSAFE_LOCATOR_ERROR = "provider media locator is unsafe"
_QUERY_ERROR = "provider query is unavailable"
_CONTENT_NEEDED = "bound_content_needed"
_ALLOWED_REFERENCE_KINDS = frozenset({"managed_url", "public_http", "data_uri"})
_ALLOWED_SAFE_STATES = frozenset(
    {"queued", "in_progress", "completed", "done", "failed"}
)
_HEX_ESCAPE_RE = re.compile(r"%(?![0-9a-fA-F]{2})")
_BASE64_TEXT_RE = re.compile(r"[A-Za-z0-9+/_-]+={0,2}\Z")


CredentialResolver = Callable[
    [CredentialBinding, Literal["create", "query", "content"]],
    BoundCredential | None,
]
HttpClientFactory = Callable[[], AbstractAsyncContextManager[AsyncHttpClient]]
UtcClock = Callable[[], datetime]


@dataclass(frozen=True, slots=True, repr=False)
class HaoaiExecutionBinding:
    """Trusted per-execution values paired by the caller after P1 commit.

    The value shape carries no database commit proof. The P2 caller remains
    responsible for supplying the original committed values.
    """

    owner_scope: OwnerTaskScope
    snapshot: FrozenExecutionSnapshot
    intent: CommittedIntent
    credential_binding: CredentialBinding


class _ResponseTooLarge(Exception):
    pass


class _InvalidResponseBody(Exception):
    pass


class _ResponseReadFailure(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__("provider response could not be read")
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class _HttpResult:
    status_code: int
    body: bytes


class HaoaiVideoProvider:
    """P3 HaoAI JSON create/query adapter with lazy, injected dependencies."""

    def __init__(
        self,
        binding: HaoaiExecutionBinding,
        *,
        http_client_factory: HttpClientFactory,
        credential_resolver: CredentialResolver,
        utc_clock: UtcClock,
    ) -> None:
        self._binding = binding
        self._http_client_factory = http_client_factory
        self._credential_resolver = credential_resolver
        self._utc_clock = utc_clock
        self._create_guard = threading.Lock()
        self._create_started = False
        self._create_result: ExternalOperationRef | None = None

    def stream_text(
        self, snapshot: FrozenExecutionSnapshot, authority: WebStreamAuthority
    ) -> AsyncIterator[TextEvent]:
        del snapshot, authority
        return self._unsupported_text_stream()

    async def _unsupported_text_stream(self) -> AsyncIterator[TextEvent]:
        yield TextEvent(kind="text_error", safe_error=_UNSUPPORTED_DETAIL)

    def optimize_frame(
        self,
        snapshot: FrozenExecutionSnapshot,
        context: str,
        intent: CommittedIntent,
    ) -> TextResult | ProviderFailure:
        del snapshot, context, intent
        return ProviderFailure(
            code="unsupported",
            retryable=False,
            safe_detail=_UNSUPPORTED_DETAIL,
        )

    def generate_sync_image(
        self,
        snapshot: FrozenExecutionSnapshot,
        refs: Sequence[ReferenceInput],
        intent: CommittedIntent,
    ) -> MediaResult | UnknownCreate | Failure:
        del snapshot, refs, intent
        return Failure(code="unsupported", safe_detail=_UNSUPPORTED_DETAIL)

    def fetch_provider_content(
        self,
        snapshot: FrozenExecutionSnapshot,
        external_ref: ExternalOperationRef,
    ) -> ReferenceInput:
        del snapshot, external_ref
        raise NotImplementedError(_UNSUPPORTED_DETAIL)

    def resolve_original_credential(
        self,
        binding: CredentialBinding,
        purpose: Literal["create", "query", "content"],
    ) -> BoundCredential | Literal["unavailable"]:
        self._validate_binding(self._binding.snapshot, self._binding.intent)
        if binding != self._binding.credential_binding:
            raise ValueError(_BINDING_ERROR)
        if purpose not in ("create", "query", "content"):
            raise ValueError(_BINDING_ERROR)
        if purpose == "content":
            return "unavailable"

        try:
            credential = self._credential_resolver(binding, purpose)
        except Exception:
            return "unavailable"
        if not isinstance(credential, BoundCredential):
            return "unavailable"
        if (
            credential.provider != PROVIDER_NAME
            or credential.principal_id != binding.original_principal_id
            or not isinstance(credential.secret, str)
            or not credential.secret
            or _has_control_characters(credential.secret)
            or not _is_utf8_text(credential.secret)
        ):
            return "unavailable"
        return credential

    def submit_async_media(
        self,
        snapshot: FrozenExecutionSnapshot,
        refs: Sequence[ReferenceInput],
        intent: CommittedIntent,
    ) -> ExternalOperationRef:
        _require_no_running_event_loop()
        self._validate_binding(snapshot, intent)

        with self._create_guard:
            if self._create_started:
                return self._create_result or self._unknown_create(intent)

        try:
            reference_values = _prepared_reference_strings(snapshot, refs)
            payload = _build_payload(snapshot, reference_values)
            request_body = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            endpoint = _create_endpoint(snapshot, self._binding.credential_binding)
        except (TypeError, ValueError, OverflowError):
            with self._create_guard:
                if self._create_started:
                    return self._create_result or self._unknown_create(intent)
            return self._external_ref(
                external_id=None,
                outcome="definitive_failure",
                state="invalid_request",
                intent=intent,
            )

        with self._create_guard:
            if self._create_started:
                return self._create_result or self._unknown_create(intent)
            self._create_started = True
            self._create_result = self._unknown_create(intent)

        credential = self.resolve_original_credential(
            self._binding.credential_binding, "create"
        )
        if credential == "unavailable":
            return self._remember_create(
                self._external_ref(
                    external_id=None,
                    outcome="definitive_failure",
                    state="credential_unavailable",
                    intent=intent,
                )
            )

        headers = _request_headers(credential)
        try:
            result = asyncio.run(
                self._exchange(
                    "POST",
                    endpoint,
                    headers=headers,
                    content=request_body,
                    timeout_seconds=120.0,
                )
            )
        except asyncio.CancelledError:
            raise
        except _ResponseReadFailure as exc:
            if 400 <= exc.status_code <= 499:
                return self._remember_create(self._rejected_create(intent))
            return self._remember_create(self._unknown_create(intent))
        except Exception:
            return self._remember_create(self._unknown_create(intent))

        if result.status_code != 200:
            if 400 <= result.status_code <= 499:
                return self._remember_create(self._rejected_create(intent))
            return self._remember_create(self._unknown_create(intent))

        try:
            projected = parse_haoai_create_response(result.status_code, result.body)
        except Exception:
            return self._remember_create(self._unknown_create(intent))

        chosen_id = projected.external_id
        if not isinstance(chosen_id, str) or not chosen_id or not chosen_id.strip():
            return self._remember_create(self._unknown_create(intent))
        if not _safe_response_candidate(chosen_id, credential.secret, kind="id"):
            return self._remember_create(
                self._external_ref(
                    external_id=None,
                    outcome="unknown",
                    state=_UNSAFE_ID_STATE,
                    intent=intent,
                )
            )

        return self._remember_create(
            self._external_ref(
                external_id=chosen_id,
                outcome="submitted",
                state=_safe_source_state(projected.raw_status),
                intent=intent,
            )
        )

    def query_existing(
        self,
        snapshot: FrozenExecutionSnapshot,
        external_ref: ExternalOperationRef,
        reservation: CommittedPollReservation | None = None,
    ) -> ProviderObservation:
        _require_no_running_event_loop()
        self._validate_binding(snapshot, self._binding.intent)
        self._validate_external_ref(external_ref, self._binding.intent)
        self._validate_poll_reservation(
            reservation, external_ref, self._binding.intent
        )

        try:
            endpoint = _query_endpoint(
                snapshot,
                self._binding.credential_binding,
                external_ref.external_id,
            )
        except (TypeError, ValueError):
            raise ValueError(_BINDING_ERROR) from None

        credential = self.resolve_original_credential(
            self._binding.credential_binding, "query"
        )
        if credential == "unavailable":
            return self._observation(
                external_ref=external_ref,
                status="unknown",
                received_at=self._now_utc(),
                body=b"",
                safe_error=_CREDENTIAL_ERROR,
                payload={"source_fact": "original_credential_unavailable"},
            )

        if not _safe_response_candidate(
            external_ref.external_id, credential.secret, kind="id"
        ):
            return self._observation(
                external_ref=external_ref,
                status="unknown",
                received_at=self._now_utc(),
                body=b"",
                safe_error=_IDENTITY_ERROR,
                payload={"source_fact": "original_identity_unusable"},
            )

        try:
            result = asyncio.run(
                self._exchange(
                    "GET",
                    endpoint,
                    headers=_request_headers(credential),
                    content=None,
                    timeout_seconds=30.0,
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return self._observation(
                external_ref=external_ref,
                status="unknown",
                received_at=self._now_utc(),
                body=b"",
                safe_error=_QUERY_ERROR,
                payload={"source_fact": "transport_unavailable"},
            )

        received_at = self._now_utc()
        try:
            projected = parse_haoai_query_response(result.status_code, result.body)
        except Exception:
            return self._observation(
                external_ref=external_ref,
                status="unknown",
                received_at=received_at,
                body=result.body,
                safe_error=_QUERY_ERROR,
                payload={"source_fact": "response_unusable"},
            )

        if result.status_code != 200 or projected.body_kind != "object":
            payload: dict[str, object] = {
                "source_fact": (
                    "http_error"
                    if result.status_code != 200
                    else projected.body_kind
                )
            }
            if projected.auth_unavailable_markers:
                payload["auth_unavailable_markers"] = (
                    projected.auth_unavailable_markers
                )
            return self._observation(
                external_ref=external_ref,
                status="unknown",
                received_at=received_at,
                body=result.body,
                safe_error=_QUERY_ERROR,
                payload=payload,
            )

        source_status = _safe_source_state(projected.raw_status)
        if source_status == "completed" or source_status == "done":
            status = "completed"
        elif source_status == "failed":
            status = "failed"
        else:
            status = "unknown"

        payload = {
            "source_fact": (
                "source_completed"
                if status == "completed"
                else "source_failed"
                if status == "failed"
                else "source_unknown"
            )
        }
        if source_status is not None:
            payload["source_status"] = source_status
        if projected.image_download_markers:
            payload["image_download_markers"] = projected.image_download_markers

        media_locator: str | None = None
        safe_error: str | None = None
        if status == "completed":
            selected = projected.media_locator
            if not selected:
                payload["content_status"] = _CONTENT_NEEDED
                payload["content_path_suffix"] = "/content"
            elif isinstance(selected, str) and _safe_response_candidate(
                selected, credential.secret, kind="url"
            ):
                media_locator = selected
                if projected.media_locator_source is not None:
                    payload["locator_source"] = projected.media_locator_source
            else:
                payload["content_status"] = "unsafe_locator"
                safe_error = _UNSAFE_LOCATOR_ERROR
        elif status == "failed":
            safe_error = "provider reported generation failure"

        return self._observation(
            external_ref=external_ref,
            status=status,
            received_at=received_at,
            body=result.body,
            media_locator=media_locator,
            safe_error=safe_error,
            payload=payload,
        )

    async def _exchange(
        self,
        method: Literal["POST", "GET"],
        url: str,
        *,
        headers: Mapping[str, str],
        content: bytes | None,
        timeout_seconds: float,
    ) -> _HttpResult:
        manager = self._http_client_factory()
        entered = False
        response: object | None = None
        iterator: object | None = None
        status_code: int | None = None
        body: bytes | None = None
        primary_error: BaseException | None = None
        cleanup_errors: list[BaseException] = []
        cleanup_cancellation: asyncio.CancelledError | None = None

        try:
            client = await manager.__aenter__()
            entered = True
            response = await client.request(
                method,
                url,
                headers=headers,
                content=content,
                timeout_seconds=timeout_seconds,
                follow_redirects=False,
            )
            raw_status = getattr(response, "status_code", None)
            if type(raw_status) is not int:
                raise _InvalidResponseBody()
            status_code = raw_status
            try:
                iterator = await _response_iterator(response)
                body = await _read_response_body(response, iterator)
            except asyncio.CancelledError:
                raise
            except Exception:
                raise _ResponseReadFailure(status_code) from None
        except BaseException as exc:
            primary_error = exc
        finally:
            for resource in (iterator, response):
                try:
                    await _close_owned_resource(resource)
                except BaseException as exc:
                    cleanup_errors.append(exc)
                    if (
                        cleanup_cancellation is None
                        and isinstance(exc, asyncio.CancelledError)
                    ):
                        cleanup_cancellation = exc
            if entered:
                try:
                    await manager.__aexit__(
                        type(primary_error) if primary_error is not None else None,
                        primary_error,
                        primary_error.__traceback__
                        if primary_error is not None
                        else None,
                    )
                except BaseException as exc:
                    cleanup_errors.append(exc)
                    if (
                        cleanup_cancellation is None
                        and isinstance(exc, asyncio.CancelledError)
                    ):
                        cleanup_cancellation = exc

        if isinstance(primary_error, asyncio.CancelledError):
            raise primary_error
        if cleanup_cancellation is not None:
            raise cleanup_cancellation
        if primary_error is not None:
            raise primary_error
        if cleanup_errors:
            raise cleanup_errors[0]
        if status_code is None or body is None:
            raise _InvalidResponseBody()
        return _HttpResult(status_code=status_code, body=body)

    def _validate_binding(
        self,
        snapshot: FrozenExecutionSnapshot,
        intent: CommittedIntent,
    ) -> None:
        trusted = self._binding
        credential = trusted.credential_binding
        if snapshot != trusted.snapshot or intent != trusted.intent:
            raise ValueError(_BINDING_ERROR)
        if (
            not _is_nonblank_text(trusted.owner_scope.user_id)
            or not _is_nonblank_text(trusted.owner_scope.task_id)
            or not _is_nonblank_text(trusted.owner_scope.operation)
        ):
            raise ValueError(_BINDING_ERROR)
        if (
            snapshot.grant_snapshot.actor_id != trusted.owner_scope.user_id
            or snapshot.model_category != VIDEO_CATEGORY
            or snapshot.model_ref.provider != PROVIDER_NAME
            or intent.provider != PROVIDER_NAME
            or intent.model_id != snapshot.model_ref.model_id
            or snapshot.model_ref.owner_id
            not in (trusted.owner_scope.user_id, "system")
            or credential.owner_or_system != snapshot.model_ref.owner_id
            or credential.provider != PROVIDER_NAME
            or not _is_nonblank_text(credential.source)
            or not _is_nonblank_text(credential.row_id)
            or not _is_nonblank_text(credential.original_principal_id)
            or not _is_nonblank_text(credential.binding_version)
            or not _is_nonblank_text(snapshot.credential_binding_ref)
            or credential.endpoint_origin
            != snapshot.endpoint_policy.provider_origin
            or not _valid_https_base(credential.endpoint_origin)
            or not _is_nonblank_text(intent.step_id)
            or not _is_nonblank_text(intent.attempt_id)
            or not _is_nonblank_text(intent.external_submission_id)
            or not _is_nonblank_text(intent.request_digest)
            or not _is_nonblank_text(intent.snapshot_ref)
        ):
            raise ValueError(_BINDING_ERROR)

    def _validate_external_ref(
        self, external_ref: ExternalOperationRef, intent: CommittedIntent
    ) -> None:
        if (
            external_ref.intent_id != intent.external_submission_id
            or external_ref.attempt_id != intent.attempt_id
            or external_ref.provider != PROVIDER_NAME
            or external_ref.create_outcome != "submitted"
            or not isinstance(external_ref.external_id, str)
            or not external_ref.external_id
            or not external_ref.external_id.strip()
            or len(external_ref.external_id) > MAX_CANDIDATE_CHARACTERS
            or _has_control_characters(external_ref.external_id)
            or _HEX_ESCAPE_RE.search(external_ref.external_id)
        ):
            raise ValueError(_BINDING_ERROR)

    def _validate_poll_reservation(
        self,
        reservation: CommittedPollReservation | None,
        external_ref: ExternalOperationRef,
        intent: CommittedIntent,
    ) -> None:
        if reservation is None:
            return
        poll = reservation.reservation
        if (
            poll.state != "reserved"
            or poll.step_id != intent.step_id
            or poll.attempt_id != intent.attempt_id
            or poll.submission_id != intent.external_submission_id
            or poll.external_id != external_ref.external_id
            or poll.payload_digest != intent.request_digest
        ):
            raise ValueError(_BINDING_ERROR)

    def _external_ref(
        self,
        *,
        external_id: str | None,
        outcome: Literal["submitted", "unknown", "definitive_failure"],
        state: str | None,
        intent: CommittedIntent,
    ) -> ExternalOperationRef:
        return ExternalOperationRef(
            intent_id=intent.external_submission_id,
            attempt_id=intent.attempt_id,
            provider=PROVIDER_NAME,
            external_id=external_id,
            create_outcome=outcome,
            last_safe_provider_state=state,
        )

    def _remember_create(
        self, external_ref: ExternalOperationRef
    ) -> ExternalOperationRef:
        with self._create_guard:
            self._create_result = external_ref
        return external_ref

    def _unknown_create(self, intent: CommittedIntent) -> ExternalOperationRef:
        return self._external_ref(
            external_id=None,
            outcome="unknown",
            state=_CREATE_UNKNOWN_STATE,
            intent=intent,
        )

    def _rejected_create(self, intent: CommittedIntent) -> ExternalOperationRef:
        return self._external_ref(
            external_id=None,
            outcome="definitive_failure",
            state=_CREATE_REJECTED_STATE,
            intent=intent,
        )

    def _observation(
        self,
        *,
        external_ref: ExternalOperationRef,
        status: str,
        received_at: datetime,
        body: bytes,
        media_locator: str | None = None,
        safe_error: str | None = None,
        payload: Mapping[str, object],
    ) -> ProviderObservation:
        if received_at.tzinfo is None or received_at.utcoffset() is None:
            raise ValueError("HaoAI clock must return an aware datetime")
        received_utc = received_at.astimezone(timezone.utc)
        body_digest = hashlib.sha256(body).hexdigest()
        dedupe_material = "\x00".join(
            (
                external_ref.intent_id,
                external_ref.attempt_id,
                external_ref.external_id or "",
                status,
                body_digest,
            )
        ).encode("utf-8")
        dedupe_key = hashlib.sha256(dedupe_material).hexdigest()
        return ProviderObservation(
            owner_scope=self._binding.owner_scope,
            intent_id=external_ref.intent_id,
            attempt_id=external_ref.attempt_id,
            external_ref=external_ref,
            dedupe_key=dedupe_key,
            payload_digest=body_digest,
            received_at=received_utc,
            status=status,
            media_locator=media_locator,
            safe_error=safe_error,
            payload=payload,
        )

    def _now_utc(self) -> datetime:
        now = self._utc_clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("HaoAI clock must return an aware datetime")
        return now.astimezone(timezone.utc)


def _require_no_running_event_loop() -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise RuntimeError("synchronous HaoAI provider call inside a running event loop")


def _has_control_characters(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _is_nonblank_text(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and not _has_control_characters(value)
    )


def _is_utf8_text(value: str) -> bool:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _valid_https_base(value: str | None) -> bool:
    if not isinstance(value, str) or not value or _has_control_characters(value):
        return False
    if value != value.strip() or any(character.isspace() for character in value):
        return False
    if "\\" in value or "#" in value or "?" in value:
        return False
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() == "https"
        and bool(parsed.hostname)
        and "@" not in parsed.netloc
        and not parsed.query
        and not parsed.fragment
    )


def _create_endpoint(
    snapshot: FrozenExecutionSnapshot, binding: CredentialBinding
) -> str:
    origin = binding.endpoint_origin
    if (
        origin != snapshot.endpoint_policy.provider_origin
        or not _valid_https_base(origin)
    ):
        raise ValueError(_BINDING_ERROR)
    return haoai_video_create_url(origin)


def _query_endpoint(
    snapshot: FrozenExecutionSnapshot,
    binding: CredentialBinding,
    external_id: str | None,
) -> str:
    origin = binding.endpoint_origin
    if (
        not isinstance(external_id, str)
        or origin != snapshot.endpoint_policy.provider_origin
        or not _valid_https_base(origin)
    ):
        raise ValueError(_BINDING_ERROR)
    return haoai_video_query_url(origin, quote(external_id, safe=""))


def _model_parameter(
    model_params: Mapping[str, object], name: str
) -> object | None:
    return model_params.get(name)


def _max_reference_images(model_params: Mapping[str, object]) -> int:
    value: object = model_params.get(
        "max_reference_images", DEFAULT_MAX_REFERENCE_IMAGES
    )
    if isinstance(value, Mapping):
        value = value.get("default", DEFAULT_MAX_REFERENCE_IMAGES)
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_MAX_REFERENCE_IMAGES


def _build_payload(
    snapshot: FrozenExecutionSnapshot, references: Sequence[str]
) -> dict[str, object]:
    model = snapshot.model_name or DEFAULT_HAOAI_VIDEO_MODEL
    if not isinstance(model, str):
        raise TypeError("frozen HaoAI wire model must be text")
    return build_haoai_video_create_payload(
        snapshot.effective_prompt,
        model=model,
        duration=_model_parameter(snapshot.model_params, "duration"),
        size=_model_parameter(snapshot.model_params, "size"),
        resolution=_model_parameter(snapshot.model_params, "resolution"),
        reference_images=references,
        max_reference_images=_max_reference_images(snapshot.model_params),
    )


def _prepared_reference_strings(
    snapshot: FrozenExecutionSnapshot, refs: Sequence[ReferenceInput]
) -> tuple[str, ...]:
    max_refs = _max_reference_images(snapshot.model_params)
    selected = refs[:max_refs]
    values: list[str] = []
    for reference in selected:
        if not isinstance(reference, ReferenceInput):
            raise TypeError("prepared reference has an unsupported value shape")
        if reference.kind not in _ALLOWED_REFERENCE_KINDS:
            raise TypeError("prepared reference kind is unsupported by HaoAI")
        if not isinstance(reference.locator, str):
            raise TypeError("prepared reference has no native wire locator")
        values.append(reference.locator)
    return tuple(values)


def _request_headers(credential: BoundCredential) -> Mapping[str, str]:
    return {
        "Authorization": f"Bearer {credential.secret}",
        "Content-Type": "application/json",
    }


def _safe_source_state(value: object) -> str | None:
    return value if isinstance(value, str) and value in _ALLOWED_SAFE_STATES else None


async def _response_iterator(response: object) -> object:
    iter_bytes = getattr(response, "iter_bytes", None)
    if not callable(iter_bytes):
        raise _InvalidResponseBody()
    stream = iter_bytes(chunk_size=RESPONSE_CHUNK_BYTES)
    iterator = await stream if inspect.isawaitable(stream) else stream
    return iterator


async def _read_response_body(response: object, iterator: object) -> bytes:
    headers = getattr(response, "headers", {})
    declared_length = _content_length(headers)
    if declared_length is not None and declared_length > MAX_RESPONSE_BYTES:
        raise _ResponseTooLarge()
    if not hasattr(iterator, "__aiter__"):
        raise _InvalidResponseBody()

    body = bytearray()
    async for chunk in iterator:
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise _InvalidResponseBody()
        if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
            raise _ResponseTooLarge()
        body.extend(chunk)
    return bytes(body)


def _content_length(headers: object) -> int | None:
    if not isinstance(headers, Mapping):
        raise _InvalidResponseBody()
    values = [
        value
        for key, value in headers.items()
        if isinstance(key, str) and key.lower() == "content-length"
    ]
    if not values:
        return None
    if len(values) != 1 or not isinstance(values[0], str):
        raise _InvalidResponseBody()
    raw_value = values[0].strip()
    if not raw_value or not raw_value.isdecimal():
        raise _InvalidResponseBody()
    try:
        return int(raw_value)
    except (ValueError, OverflowError):
        raise _InvalidResponseBody() from None


async def _close_owned_resource(resource: object | None) -> None:
    if resource is None:
        return
    close = getattr(resource, "aclose", None)
    if not callable(close):
        close = getattr(resource, "close", None)
    if not callable(close):
        return
    result = close()
    if inspect.isawaitable(result):
        await result


def _safe_response_candidate(
    candidate: str,
    secret: str,
    *,
    kind: Literal["id", "url"],
) -> bool:
    if (
        not isinstance(candidate, str)
        or not candidate
        or len(candidate) > MAX_CANDIDATE_CHARACTERS
        or candidate != candidate.strip()
        or _has_control_characters(candidate)
    ):
        return False
    if kind == "url" and not _valid_media_url(candidate):
        return False
    if not secret or not _is_utf8_text(secret):
        return False
    return not _contains_secret_shadow(candidate, secret)


def _valid_media_url(candidate: str) -> bool:
    if "\\" in candidate or "#" in candidate:
        return False
    try:
        parsed = urlsplit(candidate)
        _ = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() in ("http", "https")
        and bool(parsed.hostname)
        and "@" not in parsed.netloc
        and not _HEX_ESCAPE_RE.search(candidate)
    )


def _contains_secret_shadow(candidate: str, secret: str) -> bool:
    secret_bytes = secret.encode("utf-8")
    pending: deque[tuple[str, int]] = deque([(candidate, 0)])
    queued = {candidate}
    total_characters = len(candidate)

    while pending:
        value, depth = pending.popleft()
        if secret in value:
            return True
        try:
            value_bytes = value.encode("utf-8")
        except UnicodeEncodeError:
            return True
        if secret_bytes and secret_bytes in value_bytes:
            return True

        if _HEX_ESCAPE_RE.search(value):
            return True
        if depth >= MAX_SHADOW_ROUNDS:
            if _has_pending_shadow_transform(value):
                return True
            continue

        def enqueue(shadow: str) -> bool:
            nonlocal total_characters
            if shadow in queued:
                return True
            if (
                len(queued) >= MAX_SHADOW_NODES
                or len(shadow) > MAX_CANDIDATE_CHARACTERS
                or total_characters + len(shadow) > MAX_TOTAL_SHADOW_CHARACTERS
            ):
                return False
            queued.add(shadow)
            total_characters += len(shadow)
            pending.append((shadow, depth + 1))
            return True

        if "%" in value:
            try:
                decoded = unquote(value, errors="strict")
            except (UnicodeDecodeError, ValueError):
                return True
            if decoded != value and not enqueue(decoded):
                return True

        if "\\" in value:
            try:
                decoded_json_string = json.loads(
                    '"' + value.replace('"', '\\"') + '"'
                )
            except (json.JSONDecodeError, ValueError):
                return True
            if not isinstance(decoded_json_string, str):
                return True
            if decoded_json_string != value and not enqueue(decoded_json_string):
                return True
            try:
                whole_json_value = json.loads(value)
            except (json.JSONDecodeError, ValueError):
                whole_json_value = None
            if (
                isinstance(whole_json_value, str)
                and whole_json_value != value
                and not enqueue(whole_json_value)
            ):
                return True

        query_values = _query_shadow_values(value)
        if query_values is None:
            return True
        for query_value in query_values:
            if query_value != value and not enqueue(query_value):
                return True
            if "+" in query_value:
                try:
                    form_value = unquote_plus(query_value, errors="strict")
                except UnicodeDecodeError:
                    return True
                if form_value != query_value and not enqueue(form_value):
                    return True

        for encoded in (value, *query_values):
            decoded_bytes = _decode_recognizable_base64(encoded)
            if decoded_bytes is None:
                continue
            if secret_bytes and secret_bytes in decoded_bytes:
                return True
            try:
                decoded_text = decoded_bytes.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if not enqueue(decoded_text):
                return True

    return False


def _has_pending_shadow_transform(value: str) -> bool:
    if "\\" in value:
        return True
    if "%" in value:
        try:
            if unquote(value, errors="strict") != value:
                return True
        except (UnicodeDecodeError, ValueError):
            return True
    query_values = _query_shadow_values(value)
    if query_values is None:
        return True
    for query_value in query_values:
        if query_value != value:
            return True
        if "+" in query_value:
            try:
                if unquote_plus(query_value, errors="strict") != query_value:
                    return True
            except UnicodeDecodeError:
                return True
    return any(
        _decode_recognizable_base64(encoded) is not None
        for encoded in (value, *query_values)
    )


def _query_shadow_values(value: str) -> tuple[str, ...] | None:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ()
    values: list[str] = []

    def append(candidate: str) -> bool:
        if len(values) >= MAX_SHADOW_NODES:
            return False
        values.append(candidate)
        return True

    if parsed.path:
        if not append(parsed.path):
            return None
        for segment in parsed.path.split("/"):
            if segment and not append(segment):
                return None
    if parsed.query:
        if not append(parsed.query):
            return None
        for component in parsed.query.split("&"):
            if not append(component):
                return None
            if "=" in component:
                key, item = component.split("=", 1)
                if not append(key) or not append(item):
                    return None
    return tuple(values)


def _decode_recognizable_base64(value: str) -> bytes | None:
    if (
        len(value) > MAX_CANDIDATE_CHARACTERS
        or not _BASE64_TEXT_RE.fullmatch(value)
    ):
        return None
    unpadded = value.rstrip("=")
    if len(unpadded) % 4 == 1:
        return None
    padded = unpadded + ("=" * ((4 - len(unpadded) % 4) % 4))
    try:
        return base64.b64decode(padded, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError):
        return None


__all__ = ("HaoaiExecutionBinding", "HaoaiVideoProvider")
