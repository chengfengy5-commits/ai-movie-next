"""Fake-friendly Manxiaobai video transport for the frozen generation ports.

This adapter submits one JSON create request or queries one known provider ID.
It records constrained provider observations only; it does not fetch media or
assert that a provider state is a validated application result.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import re
import threading
from collections.abc import AsyncIterator, Callable, Coroutine, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, Literal, TypeVar
from urllib.parse import quote, quote_plus, unquote, urlsplit

from haoai_backend.generation_contracts import (
    AsyncHttpClient,
    BoundCredential,
    CommittedIntent,
    CommittedPollReservation,
    CredentialBinding,
    ExternalOperationRef,
    EndpointPolicy,
    Failure,
    FrozenExecutionSnapshot,
    FrozenGrantSnapshot,
    GenerationError,
    GenerationErrorCode,
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
from haoai_backend.generation_providers.video.manxiaobai_codec import (
    DEFAULT_VIDEO_MODEL,
    VIDEO_COLLECTION_PATH,
    ManxiaobaiCodecError,
    ManxiaobaiResponseParseError,
    build_manxiaobai_video_payload,
    parse_manxiaobai_create_response,
    parse_manxiaobai_query_response,
)

PROVIDER_ID = "manxiaobai"
CREATE_TIMEOUT_SECONDS = 120
QUERY_TIMEOUT_SECONDS = 30
MAX_RESPONSE_BYTES = 1024 * 1024
RESPONSE_CHUNK_BYTES = 64 * 1024
MAX_PROVIDER_ID_BYTES = 1024
MAX_MEDIA_LOCATOR_LENGTH = 8192

CredentialResolver = Callable[
    [CredentialBinding, Literal["create", "query", "content"]],
    BoundCredential | Literal["unavailable"],
]
HttpClientFactory = Callable[[], AsyncHttpClient]
Clock = Callable[[], datetime]
_ResultT = TypeVar("_ResultT")


class _ResponseTooLarge(Exception):
    pass


class _HttpRequestFailed(Exception):
    pass


class _ClientUnavailable(Exception):
    pass


class ManxiaobaiVideoProvider:
    """Synchronous public provider port backed by injected async resources.

    All trusted identity and configuration values are paired by the caller that
    constructs this adapter. Construction itself never resolves credentials or
    creates a client.
    """

    def __init__(
        self,
        *,
        owner_scope: OwnerTaskScope,
        snapshot_ref: str,
        frozen_snapshot: FrozenExecutionSnapshot,
        committed_intent: CommittedIntent,
        original_model_reference: ModelReference,
        trusted_model_owner_id: str,
        credential_binding_ref: str,
        original_credential_binding: CredentialBinding,
        trusted_binding_owner_or_system: str,
        trusted_binding_principal_id: str,
        wire_model: str | None,
        credential_resolver: CredentialResolver,
        http_client_factory: HttpClientFactory,
        clock: Clock | None = None,
    ) -> None:
        self._owner_scope = owner_scope
        self._snapshot_ref = snapshot_ref
        self._frozen_snapshot = frozen_snapshot
        self._committed_intent = committed_intent
        self._original_model_reference = original_model_reference
        # The trusted producer supplies this model-owner to binding pairing;
        # owner_or_system is compared separately without parsing its encoding.
        self._trusted_model_owner_id = trusted_model_owner_id
        self._credential_binding_ref = credential_binding_ref
        self._original_credential_binding = original_credential_binding
        self._trusted_binding_owner_or_system = trusted_binding_owner_or_system
        self._trusted_binding_principal_id = trusted_binding_principal_id
        self._wire_model = wire_model
        self._credential_resolver = credential_resolver
        self._http_client_factory = http_client_factory
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._create_lock = threading.Lock()
        self._create_started = False
        self._create_result: ExternalOperationRef | None = None
        self._create_fingerprint: str | None = None

    def submit_async_media(
        self,
        snapshot: FrozenExecutionSnapshot,
        refs: Sequence[ReferenceInput],
        intent: CommittedIntent,
    ) -> ExternalOperationRef:
        self._validate_snapshot_and_intent(snapshot, intent)
        reference_urls = self._prepared_reference_urls(refs)
        payload = self._build_payload(snapshot, reference_urls)
        payload_bytes = self._encode_payload(payload)
        fingerprint = hashlib.sha256(payload_bytes).hexdigest()
        self._require_sync_call_context()

        with self._create_lock:
            if self._create_result is not None:
                if fingerprint != self._create_fingerprint:
                    raise self._error(
                        "evidence_conflict_or_invalid",
                        "the original create intent was reused with different input",
                    )
                return self._create_result
            if self._create_started:
                return self._unknown_create(intent)
            try:
                result = self._run_sync(
                    lambda: self._submit_once(intent, payload_bytes)
                )
            except GenerationError:
                raise
            except asyncio.CancelledError:
                # Cancellation propagates; the started marker still prevents a repost.
                raise
            self._create_result = result
            self._create_fingerprint = fingerprint
            return result

    def query_existing(
        self,
        snapshot: FrozenExecutionSnapshot,
        external_ref: ExternalOperationRef,
        reservation: CommittedPollReservation | None = None,
    ) -> ProviderObservation:
        self._validate_snapshot_and_intent(snapshot, self._committed_intent)
        known_ref = self._validated_known_ref(external_ref)
        if reservation is not None:
            self._validate_reservation(reservation, known_ref)
        self._require_sync_call_context()
        return self._run_sync(lambda: self._query_once(known_ref))

    def resolve_original_credential(
        self,
        binding: CredentialBinding,
        purpose: Literal["create", "query", "content"],
    ) -> BoundCredential | Literal["unavailable"]:
        self._validate_trust_anchors()
        if purpose not in ("create", "query"):
            raise self._unsupported()
        if binding != self._original_credential_binding:
            raise self._error(
                "evidence_conflict_or_invalid",
                "credential binding does not match the original provider binding",
            )
        return self._resolve_credential(binding, purpose)

    def stream_text(
        self,
        snapshot: FrozenExecutionSnapshot,
        authority: WebStreamAuthority,
    ) -> AsyncIterator[TextEvent]:
        raise self._unsupported()

    def optimize_frame(
        self,
        snapshot: FrozenExecutionSnapshot,
        context: str,
        intent: CommittedIntent,
    ) -> TextResult | ProviderFailure:
        raise self._unsupported()

    def generate_sync_image(
        self,
        snapshot: FrozenExecutionSnapshot,
        refs: Sequence[ReferenceInput],
        intent: CommittedIntent,
    ) -> MediaResult | UnknownCreate | Failure:
        raise self._unsupported()

    def fetch_provider_content(
        self,
        snapshot: FrozenExecutionSnapshot,
        external_ref: ExternalOperationRef,
    ) -> ReferenceInput:
        raise self._unsupported()

    async def _submit_once(
        self, intent: CommittedIntent, request_body: bytes
    ) -> ExternalOperationRef:
        credential = self._resolve_credential(
            self._original_credential_binding, "create"
        )
        if credential == "unavailable":
            raise self._error(
                "configuration_unavailable", "original provider credential is unavailable"
            )
        self._validate_bound_credential(credential)

        origin = self._validated_origin()
        try:
            status_code, body = await self._request_bytes(
                method="POST",
                url=f"{origin}{VIDEO_COLLECTION_PATH}",
                headers={
                    "Authorization": f"Bearer {credential.secret}",
                    "Content-Type": "application/json",
                },
                content=request_body,
                timeout_seconds=CREATE_TIMEOUT_SECONDS,
                mark_create_started=True,
            )
        except asyncio.CancelledError:
            raise
        except _ClientUnavailable:
            raise self._error(
                "configuration_unavailable", "provider HTTP client is unavailable"
            ) from None
        except (_HttpRequestFailed, _ResponseTooLarge):
            return self._unknown_create(intent)

        if status_code != 200:
            if 400 <= status_code < 500:
                return ExternalOperationRef(
                    intent_id=intent.external_submission_id,
                    attempt_id=intent.attempt_id,
                    provider=PROVIDER_ID,
                    external_id=None,
                    create_outcome="definitive_failure",
                )
            return self._unknown_create(intent)

        try:
            parsed = parse_manxiaobai_create_response(body)
        except ManxiaobaiCodecError:
            return self._unknown_create(intent)

        external_id = parsed.source_id
        if not self._valid_provider_id(external_id):
            return self._unknown_create(intent)
        if _contains_secret_echo(external_id, credential.secret):
            return self._unknown_create(intent)
        return ExternalOperationRef(
            intent_id=intent.external_submission_id,
            attempt_id=intent.attempt_id,
            provider=PROVIDER_ID,
            external_id=external_id,
            create_outcome="submitted",
            last_safe_provider_state="submitted",
        )

    async def _query_once(
        self,
        external_ref: ExternalOperationRef,
    ) -> ProviderObservation:
        credential = self._resolve_credential(
            self._original_credential_binding, "query"
        )
        if credential == "unavailable":
            raise self._error(
                "configuration_unavailable", "original provider credential is unavailable"
            )
        self._validate_bound_credential(credential)
        if _contains_secret_echo(external_ref.external_id or "", credential.secret):
            raise self._error(
                "evidence_conflict_or_invalid",
                "provider operation ID contains credential material",
            )

        origin = self._validated_origin()
        path_id = _encode_path_segment(external_ref.external_id or "")
        try:
            status_code, body = await self._request_bytes(
                method="GET",
                url=f"{origin}{VIDEO_COLLECTION_PATH}/{path_id}",
                headers={"Authorization": f"Bearer {credential.secret}"},
                content=None,
                timeout_seconds=QUERY_TIMEOUT_SECONDS,
                mark_create_started=False,
            )
        except asyncio.CancelledError:
            raise
        except (_ClientUnavailable, _HttpRequestFailed, _ResponseTooLarge):
            raise self._error(
                "configuration_unavailable", "provider query is unavailable"
            ) from None

        if status_code != 200:
            raise self._error(
                "configuration_unavailable",
                f"provider query returned HTTP {status_code}",
            )
        raw_digest = hashlib.sha256(body).hexdigest()

        try:
            parsed = parse_manxiaobai_query_response(body)
        except ManxiaobaiResponseParseError:
            raise self._error(
                "evidence_conflict_or_invalid",
                "provider query response was malformed",
            ) from None
        except ManxiaobaiCodecError:
            raise self._error(
                "evidence_conflict_or_invalid",
                "provider query response was invalid",
            ) from None

        locator: str | None = None
        locator_rejected = False
        if parsed.video_url:
            if isinstance(parsed.video_url, str) and _safe_video_locator(
                parsed.video_url, credential.secret
            ):
                locator = parsed.video_url
            else:
                locator_rejected = True

        safe_payload: dict[str, object] = {
            "source_state": parsed.source_state,
            "has_video": parsed.has_video,
            "bound_content_needed": parsed.bound_content_needed,
        }
        if type(parsed.raw_code) is int and parsed.raw_code in (0, 1, 200):
            safe_payload["source_code"] = parsed.raw_code
        if locator_rejected:
            safe_payload["locator_rejected"] = True

        safe_error = None
        if parsed.source_state == "failed":
            safe_error = "provider reported failure"
        elif locator_rejected:
            safe_error = "provider locator was rejected"

        observed_ref = ExternalOperationRef(
            intent_id=external_ref.intent_id,
            attempt_id=external_ref.attempt_id,
            provider=PROVIDER_ID,
            external_id=external_ref.external_id,
            create_outcome="submitted",
            last_safe_provider_state=parsed.source_state,
        )
        return self._observation(
            external_ref=observed_ref,
            status=parsed.source_state,
            payload_digest=raw_digest,
            payload=safe_payload,
            media_locator=locator,
            safe_error=safe_error,
        )

    async def _request_bytes(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        content: bytes | None,
        timeout_seconds: float,
        mark_create_started: bool,
    ) -> tuple[int, bytes]:
        try:
            client = self._http_client_factory()
        except Exception:
            raise _ClientUnavailable from None

        response: object | None = None
        try:
            if mark_create_started:
                self._create_started = True
            try:
                response = await client.request(
                    method,
                    url,
                    headers=headers,
                    content=content,
                    timeout_seconds=timeout_seconds,
                    follow_redirects=False,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                raise _HttpRequestFailed from None

            status_code = getattr(response, "status_code", None)
            if type(status_code) is not int or not 100 <= status_code <= 599:
                raise _HttpRequestFailed
            if status_code != 200:
                result = (status_code, b"")
            else:
                result = (status_code, await _read_bounded_response(response))
        except BaseException as error:
            try:
                await _close_resources(response, client)
            except asyncio.CancelledError:
                if not isinstance(error, asyncio.CancelledError):
                    raise
            except BaseException:
                pass
            if isinstance(
                error,
                (asyncio.CancelledError, _ResponseTooLarge, _HttpRequestFailed),
            ):
                raise
            if isinstance(error, Exception):
                raise _HttpRequestFailed from None
            raise

        await _close_resources(response, client)
        return result

    def _observation(
        self,
        *,
        external_ref: ExternalOperationRef,
        status: str,
        payload_digest: str,
        payload: Mapping[str, object],
        media_locator: str | None = None,
        safe_error: str | None = None,
    ) -> ProviderObservation:
        safe_payload = dict(payload)
        safe_payload["response_digest"] = payload_digest
        dedupe_material = "\0".join(
            (
                PROVIDER_ID,
                external_ref.intent_id,
                external_ref.attempt_id,
                external_ref.external_id or "",
                status,
                payload_digest,
            )
        )
        dedupe_key = hashlib.sha256(dedupe_material.encode("utf-8")).hexdigest()
        try:
            received_at = self._clock()
        except Exception:
            raise self._error("configuration_unavailable", "provider clock is unavailable") from None
        if (
            not isinstance(received_at, datetime)
            or received_at.tzinfo is None
            or received_at.utcoffset() is None
        ):
            raise self._error("configuration_unavailable", "provider clock is invalid")
        return ProviderObservation(
            owner_scope=self._owner_scope,
            intent_id=self._committed_intent.external_submission_id,
            attempt_id=self._committed_intent.attempt_id,
            external_ref=external_ref,
            dedupe_key=dedupe_key,
            payload_digest=payload_digest,
            received_at=received_at.astimezone(timezone.utc),
            status=status,
            media_locator=media_locator,
            safe_error=safe_error,
            payload=safe_payload,
        )

    @staticmethod
    def _encode_payload(payload: Mapping[str, object]) -> bytes:
        try:
            return json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError):
            raise GenerationError(
                "invalid_generation_request", "video request payload is invalid"
            ) from None

    def _build_payload(
        self,
        snapshot: FrozenExecutionSnapshot,
        reference_urls: Sequence[str],
    ) -> dict[str, object]:
        params = snapshot.model_params
        duration = params.get("duration")
        size = params.get("size")
        resolution = params.get("resolution")
        if duration is not None and not isinstance(duration, (str, int, float, bool)):
            raise self._error("invalid_generation_request", "video duration is invalid")
        if size is not None and not isinstance(size, str):
            raise self._error("invalid_generation_request", "video aspect ratio is invalid")
        if resolution is not None and not isinstance(resolution, str):
            raise self._error("invalid_generation_request", "video resolution is invalid")
        wire_model = self._wire_model
        if wire_model is None or wire_model == "":
            wire_model = DEFAULT_VIDEO_MODEL
        if not isinstance(wire_model, str) or not wire_model.strip():
            raise self._error("configuration_unavailable", "video wire model is invalid")
        try:
            return build_manxiaobai_video_payload(
                snapshot.effective_prompt,
                wire_model=wire_model,
                duration=duration,
                size=size,
                resolution=resolution,
                reference_images=reference_urls,
            )
        except ManxiaobaiCodecError:
            raise self._error(
                "invalid_generation_request", "prepared video references are invalid"
            ) from None

    def _prepared_reference_urls(
        self, refs: Sequence[ReferenceInput]
    ) -> tuple[str, ...]:
        if isinstance(refs, (str, bytes, bytearray)) or not isinstance(refs, Sequence):
            raise self._error("invalid_generation_request", "video references are invalid")
        result: list[str] = []
        for ref in refs:
            if (
                not isinstance(ref, ReferenceInput)
                or ref.kind not in ("managed_url", "public_http")
                or ref.content is not None
                or ref.provider_operation_ref is not None
                or not isinstance(ref.locator, str)
            ):
                raise self._error(
                    "invalid_generation_request",
                    "only prepared managed or public reference URLs are supported",
                )
            result.append(ref.locator)
        return tuple(result)

    def _validate_snapshot_and_intent(
        self, snapshot: FrozenExecutionSnapshot, intent: CommittedIntent
    ) -> None:
        self._validate_trust_anchors()
        if snapshot != self._frozen_snapshot:
            raise self._error(
                "evidence_conflict_or_invalid",
                "execution snapshot does not match its trusted value",
            )
        if intent != self._committed_intent:
            raise self._error(
                "evidence_conflict_or_invalid", "committed intent does not match its trusted value"
            )
        if (
            intent.snapshot_ref != self._snapshot_ref
            or intent.model_id != self._original_model_reference.model_id
            or intent.provider != PROVIDER_ID
        ):
            raise self._error(
                "evidence_conflict_or_invalid", "committed intent bindings are inconsistent"
            )

    def _validate_trust_anchors(self) -> None:
        snapshot = self._frozen_snapshot
        intent = self._committed_intent
        binding = self._original_credential_binding
        if (
            not isinstance(self._owner_scope, OwnerTaskScope)
            or not isinstance(snapshot, FrozenExecutionSnapshot)
            or not isinstance(intent, CommittedIntent)
            or not isinstance(binding, CredentialBinding)
            or not isinstance(self._original_model_reference, ModelReference)
            or not isinstance(snapshot.grant_snapshot, FrozenGrantSnapshot)
            or not isinstance(snapshot.endpoint_policy, EndpointPolicy)
        ):
            raise self._error("configuration_unavailable", "trusted provider bindings are invalid")
        if (
            not self._snapshot_ref
            or intent.snapshot_ref != self._snapshot_ref
            or snapshot.model_category != "video"
            or snapshot.model_ref != self._original_model_reference
            or snapshot.model_ref.owner_id != self._trusted_model_owner_id
            or snapshot.model_ref.provider != PROVIDER_ID
            or intent.model_id != self._original_model_reference.model_id
            or intent.provider != PROVIDER_ID
            or snapshot.credential_binding_ref != self._credential_binding_ref
            or binding.provider != PROVIDER_ID
            or binding.owner_or_system != self._trusted_binding_owner_or_system
            or binding.original_principal_id != self._trusted_binding_principal_id
            or not self._credential_binding_ref
            or self._owner_scope.user_id != snapshot.grant_snapshot.actor_id
            or intent.external_submission_id == ""
            or intent.attempt_id == ""
        ):
            raise self._error(
                "configuration_unavailable",
                "trusted provider bindings are inconsistent",
            )
        self._validated_origin()

    def _validated_known_ref(
        self, external_ref: ExternalOperationRef
    ) -> ExternalOperationRef:
        intent = self._committed_intent
        if (
            not isinstance(external_ref, ExternalOperationRef)
            or external_ref.intent_id != intent.external_submission_id
            or external_ref.attempt_id != intent.attempt_id
            or external_ref.provider != PROVIDER_ID
            or external_ref.create_outcome != "submitted"
            or not self._valid_provider_id(external_ref.external_id)
        ):
            raise self._error(
                "evidence_conflict_or_invalid", "provider query requires the original submitted ID"
            )
        return ExternalOperationRef(
            intent_id=intent.external_submission_id,
            attempt_id=intent.attempt_id,
            provider=PROVIDER_ID,
            external_id=external_ref.external_id,
            create_outcome="submitted",
            last_safe_provider_state=_safe_previous_state(
                external_ref.last_safe_provider_state
            ),
        )

    def _validate_reservation(
        self,
        reservation: CommittedPollReservation,
        external_ref: ExternalOperationRef,
    ) -> None:
        if not isinstance(reservation, CommittedPollReservation):
            raise self._error("evidence_conflict_or_invalid", "poll reservation is invalid")
        poll = reservation.reservation
        intent = self._committed_intent
        if (
            poll.state != "reserved"
            or poll.step_id != intent.step_id
            or poll.submission_id != intent.external_submission_id
            or poll.attempt_id != intent.attempt_id
            or poll.external_id != external_ref.external_id
        ):
            raise self._error(
                "evidence_conflict_or_invalid",
                "poll reservation does not match the original intent",
            )

    def _resolve_credential(
        self,
        binding: CredentialBinding,
        purpose: Literal["create", "query", "content"],
    ) -> BoundCredential | Literal["unavailable"]:
        try:
            credential = self._credential_resolver(binding, purpose)
        except Exception:
            raise self._error(
                "configuration_unavailable", "original provider credential is unavailable"
            ) from None
        if credential == "unavailable":
            return "unavailable"
        self._validate_bound_credential(credential)
        return credential

    def _validate_bound_credential(self, credential: BoundCredential) -> None:
        if (
            not isinstance(credential, BoundCredential)
            or credential.provider != PROVIDER_ID
            or credential.principal_id != self._original_credential_binding.original_principal_id
            or not isinstance(credential.secret, str)
            or not credential.secret
        ):
            raise self._error(
                "configuration_unavailable",
                "resolved credential does not match the original binding",
            )

    def _validated_origin(self) -> str:
        snapshot_origin = self._frozen_snapshot.endpoint_policy.provider_origin
        binding_origin = self._original_credential_binding.endpoint_origin
        normalized_snapshot = _https_origin(snapshot_origin)
        normalized_binding = _https_origin(binding_origin)
        if (
        normalized_snapshot is None
        or normalized_binding is None
        or normalized_snapshot != normalized_binding
    ):
            raise self._error(
                "configuration_unavailable",
                "provider origin does not match the original credential binding",
            )
        return normalized_binding

    def _valid_provider_id(self, value: object) -> bool:
        if not isinstance(value, str) or not value or value != value.strip():
            return False
        try:
            if len(value.encode("utf-8")) > MAX_PROVIDER_ID_BYTES:
                return False
        except UnicodeEncodeError:
            return False
        return not any(ord(char) < 0x20 or ord(char) == 0x7F for char in value)

    def _unknown_create(self, intent: CommittedIntent) -> ExternalOperationRef:
        return ExternalOperationRef(
            intent_id=intent.external_submission_id,
            attempt_id=intent.attempt_id,
            provider=PROVIDER_ID,
            external_id=None,
            create_outcome="unknown",
        )

    def _require_sync_call_context(self) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        raise self._error(
            "invalid_generation_request",
            "synchronous provider methods cannot run inside an active event loop",
        )

    def _run_sync(
        self, coroutine_factory: Callable[[], Coroutine[Any, Any, _ResultT]]
    ) -> _ResultT:
        return asyncio.run(coroutine_factory())

    def _unsupported(self) -> GenerationError:
        return self._error(
            "invalid_generation_request", "this provider operation is unsupported"
        )

    @staticmethod
    def _error(code: GenerationErrorCode, detail: str) -> GenerationError:
        return GenerationError(code, detail)


async def _read_bounded_response(response: object) -> bytes:
    headers = getattr(response, "headers", {})
    if isinstance(headers, Mapping):
        for key, value in headers.items():
            if isinstance(key, str) and key.lower() == "content-length":
                try:
                    if int(value) > MAX_RESPONSE_BYTES:
                        raise _ResponseTooLarge
                except (TypeError, ValueError):
                    pass

    iterator_result = getattr(response, "iter_bytes")(chunk_size=RESPONSE_CHUNK_BYTES)
    if inspect.isawaitable(iterator_result):
        iterator_result = await iterator_result
    if not hasattr(iterator_result, "__aiter__"):
        raise _HttpRequestFailed
    iterator = iterator_result.__aiter__()
    pieces: list[bytes] = []
    total = 0
    try:
        async for chunk in iterator:
            if not isinstance(chunk, bytes):
                raise _HttpRequestFailed
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise _ResponseTooLarge
            pieces.append(chunk)
        body = b"".join(pieces)
    except asyncio.CancelledError as original_cancel:
        try:
            await _close_resource(iterator)
        except asyncio.CancelledError:
            pass
        raise original_cancel
    except BaseException:
        await _close_resource(iterator)
        raise
    else:
        await _close_resource(iterator)
        return body


async def _close_resource(resource: object | None) -> None:
    if resource is None:
        return
    try:
        close = getattr(resource, "aclose", None)
        if close is None:
            close = getattr(resource, "close", None)
        if not callable(close):
            return
        result = close()
        if inspect.isawaitable(result):
            await result
    except asyncio.CancelledError:
        raise
    except Exception:
        return


async def _close_resources(*resources: object | None) -> None:
    first_error: BaseException | None = None
    for resource in resources:
        try:
            await _close_resource(resource)
        except BaseException as error:
            if first_error is None:
                first_error = error
    if first_error is not None:
        raise first_error


def _https_origin(value: object) -> str | None:
    if not isinstance(value, str) or not value or value != value.strip():
        return None
    if any(ord(char) <= 0x20 or ord(char) == 0x7F for char in value):
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.lower() != "https"
        or not parsed.netloc
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or "@" in parsed.netloc
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        return None
    hostname = parsed.hostname.lower()
    host_part = f"[{hostname}]" if ":" in hostname else hostname
    if port is None or port == 443:
        return f"https://{host_part}"
    return f"https://{host_part}:{port}"


def _encode_path_segment(value: str) -> str:
    encoded = quote(value, safe="")
    if encoded in (".", ".."):
        return encoded.replace(".", "%2E")
    return encoded


def _safe_previous_state(value: object) -> str | None:
    if value in ("submitted", "pending", "completed", "failed"):
        return value
    return None


def _safe_video_locator(value: str, secret: str) -> bool:
    if (
        not value
        or len(value) > MAX_MEDIA_LOCATOR_LENGTH
        or value != value.strip()
        or any(ord(char) <= 0x20 or ord(char) == 0x7F for char in value)
        or "#" in value
        or _contains_secret_echo(value, secret)
    ):
        return False
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme.lower() not in ("http", "https")
        or not parsed.netloc
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or "@" in parsed.netloc
    ):
        return False
    decoded = value
    for _ in range(3):
        if re.search(
            r"(?i)(?:[\"'](?:authorization|error)[\"']\s*:|"
            r"authorization\s*:\s*(?:bearer|basic|token)\b|"
            r"bearer(?:\s+|[:=])|"
            r"request failed|internal server error|unauthorized|forbidden)",
            decoded,
        ):
            return False
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    return True


def _contains_secret_echo(value: str, secret: str) -> bool:
    if not secret:
        return False
    variants = {secret}
    current = secret
    for _ in range(2):
        current = quote(current, safe="")
        variants.add(current)
        variants.add(quote_plus(current, safe=""))
    candidate = value
    for _ in range(3):
        if any(variant and variant in candidate for variant in variants):
            return True
        decoded = unquote(candidate)
        if decoded == candidate:
            break
        candidate = decoded
    return False
