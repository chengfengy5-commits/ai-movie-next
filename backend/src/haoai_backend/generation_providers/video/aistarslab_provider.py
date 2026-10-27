"""Bounded, fake-testable adapter for the legacy AIStarsLab video API."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, TypeAlias
from urllib.parse import quote_plus, unquote_plus, urlsplit

from haoai_backend.generation_contracts import (
    AsyncHttpClient,
    AsyncHttpResponse,
    BoundCredential,
    CommittedIntent,
    CommittedPollReservation,
    CredentialBinding,
    ExternalOperationRef,
    Failure,
    FrozenExecutionSnapshot,
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
from haoai_backend.generation_providers.video.aistarslab_codec import (
    AistarslabCodecError,
    AistarslabStatusResponse,
    build_create_payload,
    encode_create_payload,
    parse_create_response,
    parse_status_response,
)


PROVIDER_ID = "aistarslab"
CREATE_PATH = "/generation/create/video"
STATUS_PATH = "/generation/status"
CREATE_TIMEOUT_SECONDS = 120.0
QUERY_TIMEOUT_SECONDS = 30.0
MAX_RESPONSE_BYTES = 1_048_576
RESPONSE_CHUNK_BYTES = 64 * 1024
MAX_PROVIDER_ID_BYTES = 1024
MAX_MEDIA_LOCATOR_LENGTH = 8192

CredentialPurpose: TypeAlias = Literal["create", "query", "content"]
CredentialResolver: TypeAlias = Callable[
    [CredentialBinding, CredentialPurpose],
    BoundCredential | Literal["unavailable"],
]
HttpClientFactory: TypeAlias = Callable[
    [], AbstractAsyncContextManager[AsyncHttpClient]
]
Clock: TypeAlias = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class AistarslabReferenceInputs:
    images: tuple[ReferenceInput, ...] = ()
    audios: tuple[ReferenceInput, ...] = ()
    videos: tuple[ReferenceInput, ...] = ()


class _ClientUnavailable(Exception):
    pass


class _RequestFailed(Exception):
    pass


class _ResponseTooLarge(Exception):
    pass


class AistarslabVideoProvider:
    """Create and query one original AIStarsLab video intent."""

    def __init__(
        self,
        *,
        owner_scope: OwnerTaskScope,
        frozen_snapshot: FrozenExecutionSnapshot,
        committed_intent: CommittedIntent,
        original_model_reference: ModelReference,
        original_credential_binding: CredentialBinding,
        credential_binding_ref: str,
        wire_model: str,
        http_client_factory: HttpClientFactory,
        credential_resolver: CredentialResolver,
        clock: Clock,
    ) -> None:
        if not isinstance(owner_scope, OwnerTaskScope):
            raise TypeError("owner_scope must be a trusted OwnerTaskScope")
        if not isinstance(frozen_snapshot, FrozenExecutionSnapshot):
            raise TypeError("frozen_snapshot must be a trusted frozen snapshot")
        if not isinstance(committed_intent, CommittedIntent):
            raise TypeError("committed_intent must be a trusted CommittedIntent")
        if not isinstance(original_model_reference, ModelReference):
            raise TypeError("original_model_reference must be a trusted ModelReference")
        if not isinstance(original_credential_binding, CredentialBinding):
            raise TypeError("original_credential_binding must be a trusted binding")
        if not isinstance(credential_binding_ref, str) or not credential_binding_ref:
            raise TypeError("credential_binding_ref must be nonblank")
        if not callable(http_client_factory) or not callable(credential_resolver):
            raise TypeError("provider dependencies must be callable")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if not isinstance(wire_model, str):
            raise TypeError("wire_model must be a string")

        self._owner_scope = owner_scope
        self._snapshot = frozen_snapshot
        self._intent = committed_intent
        self._model_ref = original_model_reference
        self._binding = original_credential_binding
        self._credential_binding_ref = credential_binding_ref
        self._wire_model = wire_model
        self._http_client_factory = http_client_factory
        self._credential_resolver = credential_resolver
        self._clock = clock
        self._create_fingerprint: str | None = None
        self._create_result: ExternalOperationRef | None = None
        self._api_base_url = self._validate_trust_anchors()

    def submit_async_media(
        self,
        snapshot: FrozenExecutionSnapshot,
        refs: AistarslabReferenceInputs,
        intent: CommittedIntent,
    ) -> ExternalOperationRef:
        self._require_sync_context()
        self._validate_snapshot_and_intent(snapshot, intent)
        payload = self._create_payload(snapshot, refs)
        request_body = encode_create_payload(payload)
        fingerprint = hashlib.sha256(request_body).hexdigest()
        if self._create_result is not None:
            if fingerprint != self._create_fingerprint:
                raise self._error(
                    "evidence_conflict_or_invalid",
                    "the original create intent was reused with different input",
                )
            return self._create_result

        try:
            result = asyncio.run(self._submit_once(request_body))
        except _ClientUnavailable:
            raise self._error(
                "configuration_unavailable", "AIStarsLab HTTP client is unavailable"
            ) from None
        self._create_fingerprint = fingerprint
        self._create_result = result
        return result

    def query_existing(
        self,
        snapshot: FrozenExecutionSnapshot,
        external_ref: ExternalOperationRef,
        reservation: CommittedPollReservation | None = None,
    ) -> ProviderObservation:
        self._require_sync_context()
        self._validate_snapshot_and_intent(snapshot, self._intent)
        known_ref = self._validated_external_ref(external_ref)
        if reservation is not None and (
            reservation.reservation.submission_id
            != self._intent.external_submission_id
            or reservation.reservation.attempt_id != self._intent.attempt_id
            or reservation.reservation.step_id != self._intent.step_id
            or reservation.reservation.external_id != known_ref.external_id
        ):
            raise self._error(
                "evidence_conflict_or_invalid", "poll reservation is not for this intent"
            )
        credential = self._resolve_credential("query")
        if not _safe_provider_id(known_ref.external_id, credential.secret):
            raise self._error(
                "evidence_conflict_or_invalid", "AIStarsLab task ID is invalid"
            )
        try:
            status_code, body = asyncio.run(self._request_bytes(
                method="GET",
                url=(
                    f"{self._api_base_url}{STATUS_PATH}?taskId="
                    f"{quote_plus(known_ref.external_id or '')}"
                ),
                credential=credential,
                content=None,
                timeout_seconds=QUERY_TIMEOUT_SECONDS,
            ))
        except _ClientUnavailable:
            raise self._error(
                "configuration_unavailable", "AIStarsLab query is unavailable"
            ) from None
        except (_RequestFailed, _ResponseTooLarge):
            raise self._error(
                "configuration_unavailable", "AIStarsLab query is unavailable"
            ) from None

        try:
            parsed = parse_status_response(status_code, body)
        except AistarslabCodecError:
            raise self._error(
                "evidence_conflict_or_invalid", "AIStarsLab status response is invalid"
            ) from None
        return self._observation(known_ref, parsed, body, credential.secret)

    def stream_text(
        self,
        snapshot: FrozenExecutionSnapshot,
        authority: WebStreamAuthority,
    ) -> AsyncIterator[TextEvent]:
        raise NotImplementedError("AIStarsLab video adapter does not stream text")

    def optimize_frame(
        self, snapshot: FrozenExecutionSnapshot, context: str, intent: CommittedIntent
    ) -> TextResult | ProviderFailure:
        raise NotImplementedError("AIStarsLab video adapter does not optimize frames")

    def generate_sync_image(
        self,
        snapshot: FrozenExecutionSnapshot,
        refs: Sequence[ReferenceInput],
        intent: CommittedIntent,
    ) -> MediaResult | UnknownCreate | Failure:
        raise NotImplementedError("AIStarsLab video adapter does not generate images")

    def fetch_provider_content(
        self, snapshot: FrozenExecutionSnapshot, external_ref: ExternalOperationRef
    ) -> ReferenceInput:
        raise NotImplementedError("AIStarsLab output is fetched through the pinned public fetcher")

    async def _submit_once(self, request_body: bytes) -> ExternalOperationRef:
        credential = self._resolve_credential("create")
        try:
            status_code, body = await self._request_bytes(
                method="POST",
                url=f"{self._api_base_url}{CREATE_PATH}",
                credential=credential,
                content=request_body,
                timeout_seconds=CREATE_TIMEOUT_SECONDS,
            )
        except (_RequestFailed, _ResponseTooLarge):
            return self._unknown_create()

        if status_code == 408:
            return self._unknown_create()
        if status_code != 200:
            if 400 <= status_code < 500:
                return self._external_ref(None, "definitive_failure")
            return self._unknown_create()
        try:
            parsed = parse_create_response(status_code, body)
        except AistarslabCodecError:
            return self._unknown_create()
        if parsed.code != 0:
            return self._external_ref(None, "definitive_failure")
        if not _safe_provider_id(parsed.task_id, credential.secret):
            return self._unknown_create()
        return self._external_ref(parsed.task_id, "submitted")

    async def _request_bytes(
        self,
        *,
        method: str,
        url: str,
        credential: BoundCredential,
        content: bytes | None,
        timeout_seconds: float,
    ) -> tuple[int, bytes]:
        headers = {"Authorization": f"Bearer {credential.secret}"}
        if content is not None:
            headers["Content-Type"] = "application/json"

        request_timeout = self._remaining_request_timeout(timeout_seconds)
        request_started = False
        try:
            # Include client setup, dispatch, streamed body reads, and normal cleanup.
            async with asyncio.timeout(request_timeout):
                try:
                    context = self._http_client_factory()
                except Exception:
                    raise _ClientUnavailable from None
                async with context as client:
                    request_started = True
                    try:
                        response = await client.request(
                            method,
                            url,
                            headers=headers,
                            content=content,
                            timeout_seconds=request_timeout,
                            follow_redirects=False,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        raise _RequestFailed from None
                    status_code = getattr(response, "status_code", None)
                    if type(status_code) is not int or not 100 <= status_code <= 599:
                        raise _RequestFailed
                    if status_code != 200:
                        return status_code, b""
                    return status_code, await _read_bounded_response(response)
        except TimeoutError:
            raise _RequestFailed from None
        except asyncio.CancelledError:
            raise
        except (_RequestFailed, _ResponseTooLarge):
            raise
        except Exception:
            if request_started:
                raise _RequestFailed from None
            raise _ClientUnavailable from None

    def _remaining_request_timeout(self, request_limit: float) -> float:
        try:
            now = self._clock()
            deadline = self._intent.deadline_at
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
        if remaining <= 0:
            raise _RequestFailed
        return min(request_limit, remaining)

    def _create_payload(
        self,
        snapshot: FrozenExecutionSnapshot,
        refs: AistarslabReferenceInputs,
    ) -> dict[str, object]:
        if not isinstance(refs, AistarslabReferenceInputs):
            raise self._error(
                "invalid_generation_request", "AIStarsLab references are invalid"
            )
        try:
            return build_create_payload(
                model=self._wire_model,
                prompt=snapshot.effective_prompt,
                aspect_ratio=_parameter_default(snapshot, "size"),
                quality=_parameter_default(snapshot, "resolution"),
                duration=_parameter_default(snapshot, "duration"),
                images=_prepared_reference_urls(refs.images),
                audios=_prepared_reference_urls(refs.audios),
                videos=_prepared_reference_urls(refs.videos),
            )
        except AistarslabCodecError:
            raise self._error(
                "invalid_generation_request", "AIStarsLab request settings are invalid"
            ) from None

    def _resolve_credential(
        self, purpose: Literal["create", "query"]
    ) -> BoundCredential:
        try:
            credential = self._credential_resolver(self._binding, purpose)
        except Exception:
            credential = "unavailable"
        if (
            not isinstance(credential, BoundCredential)
            or credential.provider != PROVIDER_ID
            or credential.principal_id != self._binding.original_principal_id
            or not isinstance(credential.secret, str)
            or not credential.secret
            or not credential.secret.strip()
        ):
            raise self._error(
                "configuration_unavailable", "original AIStarsLab credential is unavailable"
            )
        return credential

    def _validate_snapshot_and_intent(
        self,
        snapshot: FrozenExecutionSnapshot,
        intent: CommittedIntent,
    ) -> None:
        if snapshot != self._snapshot or intent != self._intent:
            raise self._error(
                "evidence_conflict_or_invalid", "AIStarsLab intent is not the original snapshot"
            )
        if (
            snapshot.model_ref != self._model_ref
            or snapshot.model_ref.provider != PROVIDER_ID
            or snapshot.model_category != "video"
            or snapshot.model_name != self._wire_model
            or snapshot.credential_binding_ref != self._credential_binding_ref
            or snapshot.grant_snapshot.actor_id != self._owner_scope.user_id
            or intent.provider != PROVIDER_ID
            or intent.model_id != self._model_ref.model_id
            or intent.snapshot_ref != self._snapshot_ref
            or self._owner_scope.operation != "video.single"
        ):
            raise self._error(
                "evidence_conflict_or_invalid", "AIStarsLab execution binding is inconsistent"
            )

    def _validate_trust_anchors(self) -> str:
        snapshot = self._snapshot
        binding = self._binding
        intent = self._intent
        adapters = dict(snapshot.adapter_config_versions)
        if (
            snapshot.model_ref != self._model_ref
            or snapshot.model_ref.provider != PROVIDER_ID
            or snapshot.model_category != "video"
            or snapshot.model_name != self._wire_model
            or snapshot.credential_binding_ref != self._credential_binding_ref
            or intent.provider != PROVIDER_ID
            or intent.model_id != self._model_ref.model_id
            or binding.provider != PROVIDER_ID
            or binding.row_id != self._model_ref.model_id
            or binding.owner_or_system != self._model_ref.owner_id
            or not binding.original_principal_id
            or binding.endpoint_origin != snapshot.endpoint_policy.provider_origin
            or binding.allowed_content_paths != snapshot.endpoint_policy.allowed_content_paths
            or adapters.get("credential_binding_version") != binding.binding_version
            or not adapters.get("endpoint_policy")
            or not self._owner_scope.user_id
            or not self._owner_scope.task_id
            or self._owner_scope.operation != "video.single"
            or snapshot.grant_snapshot.actor_id != self._owner_scope.user_id
        ):
            raise ValueError("original AIStarsLab execution binding is incomplete")

        api_base_path = adapters.get("api_base_path")
        if not _safe_api_base_path(api_base_path):
            raise ValueError("original AIStarsLab API base path is unavailable")
        origin = _https_origin(snapshot.endpoint_policy.provider_origin)
        binding_origin = _https_origin(binding.endpoint_origin)
        if origin is None or origin != binding_origin:
            raise ValueError("original AIStarsLab provider origin is invalid")
        return f"{origin}{api_base_path.rstrip('/') if api_base_path != '/' else ''}"

    def _validated_external_ref(
        self,
        external_ref: ExternalOperationRef,
    ) -> ExternalOperationRef:
        if (
            not isinstance(external_ref, ExternalOperationRef)
            or external_ref.intent_id != self._intent.external_submission_id
            or external_ref.attempt_id != self._intent.attempt_id
            or external_ref.provider != PROVIDER_ID
            or external_ref.create_outcome != "submitted"
            or not _safe_provider_id(external_ref.external_id)
        ):
            raise self._error(
                "evidence_conflict_or_invalid", "AIStarsLab query requires the original task ID"
            )
        return external_ref

    def _observation(
        self,
        external_ref: ExternalOperationRef,
        parsed: AistarslabStatusResponse,
        body: bytes,
        secret: str,
    ) -> ProviderObservation:
        status = {1: "pending", 2: "pending", 3: "completed", 4: "failed"}[parsed.status]
        media_locator = parsed.output_url
        if media_locator is not None and not _safe_media_locator(
            media_locator, secret
        ):
            raise self._error(
                "evidence_conflict_or_invalid", "AIStarsLab output locator was rejected"
            )
        observed_ref = ExternalOperationRef(
            intent_id=external_ref.intent_id,
            attempt_id=external_ref.attempt_id,
            provider=PROVIDER_ID,
            external_id=external_ref.external_id,
            create_outcome="submitted",
            last_safe_provider_state=status,
        )
        payload_digest = hashlib.sha256(body).hexdigest()
        dedupe_material = "\0".join(
            (
                PROVIDER_ID,
                observed_ref.intent_id,
                observed_ref.attempt_id,
                observed_ref.external_id or "",
                status,
                payload_digest,
            )
        )
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
            intent_id=observed_ref.intent_id,
            attempt_id=observed_ref.attempt_id,
            external_ref=observed_ref,
            dedupe_key=hashlib.sha256(dedupe_material.encode("utf-8")).hexdigest(),
            payload_digest=payload_digest,
            received_at=received_at.astimezone(timezone.utc),
            status=status,
            media_locator=media_locator,
            safe_error="AIStarsLab reported a provider failure" if status == "failed" else None,
            payload={"provider_status": parsed.status},
        )

    def _unknown_create(self) -> ExternalOperationRef:
        return self._external_ref(None, "unknown")

    def _external_ref(
        self,
        external_id: str | None,
        create_outcome: Literal["submitted", "unknown", "definitive_failure"],
    ) -> ExternalOperationRef:
        return ExternalOperationRef(
            intent_id=self._intent.external_submission_id,
            attempt_id=self._intent.attempt_id,
            provider=PROVIDER_ID,
            external_id=external_id,
            create_outcome=create_outcome,
            last_safe_provider_state=("submitted" if create_outcome == "submitted" else None),
        )

    @property
    def _snapshot_ref(self) -> str:
        return self._intent.snapshot_ref

    @staticmethod
    def _require_sync_context() -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        raise RuntimeError("synchronous provider methods cannot run inside an active event loop")

    @staticmethod
    def _error(code: GenerationErrorCode, detail: str) -> GenerationError:
        return GenerationError(code, detail)


async def _read_bounded_response(response: AsyncHttpResponse) -> bytes:
    content_length = response.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_RESPONSE_BYTES:
                raise _ResponseTooLarge
        except ValueError:
            pass
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.iter_bytes(chunk_size=RESPONSE_CHUNK_BYTES):
        if not isinstance(chunk, bytes):
            raise _RequestFailed
        size += len(chunk)
        if size > MAX_RESPONSE_BYTES:
            raise _ResponseTooLarge
        chunks.append(chunk)
    return b"".join(chunks)


def _prepared_reference_urls(refs: Sequence[ReferenceInput]) -> tuple[str, ...]:
    if isinstance(refs, (str, bytes, bytearray)) or not isinstance(refs, Sequence):
        raise AistarslabCodecError("references must be prepared sequences")
    urls: list[str] = []
    for ref in refs:
        if (
            not isinstance(ref, ReferenceInput)
            or ref.kind not in {"managed_url", "public_http"}
            or ref.content is not None
            or ref.provider_operation_ref is not None
            or not _safe_reference_locator(ref.locator)
        ):
            raise AistarslabCodecError("reference is not a prepared URL")
        urls.append(ref.locator)
    return tuple(urls)


def _parameter_default(snapshot: FrozenExecutionSnapshot, name: str) -> object:
    value = snapshot.model_params.get(name)
    if isinstance(value, Mapping):
        value = value.get("default")
    return value


def _safe_reference_locator(value: object) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(ord(char) <= 0x20 or ord(char) == 0x7F for char in value)
    ):
        return False
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.netloc)
        and parsed.hostname is not None
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
    )


def _safe_provider_id(value: object, secret: str = "") -> bool:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value.encode("utf-8")) > MAX_PROVIDER_ID_BYTES
        or any(ord(char) <= 0x20 or ord(char) == 0x7F for char in value)
    ):
        return False
    return not secret or secret.casefold() not in value.casefold()


def _safe_media_locator(value: str, secret: str) -> bool:
    if (
        len(value) > MAX_MEDIA_LOCATOR_LENGTH
        or any(ord(char) <= 0x20 or ord(char) == 0x7F for char in value)
        or (secret and secret.casefold() in value.casefold())
    ):
        return False
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path in {"", "/"}
        or parsed.fragment
    ):
        return False
    decoded = value
    for _ in range(3):
        decoded = unquote_plus(decoded)
        if secret and secret.casefold() in decoded.casefold():
            return False
    return True


def _https_origin(value: object) -> str | None:
    if not isinstance(value, str) or not value or value != value.strip():
        return None
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _safe_api_base_path(value: object) -> bool:
    if (
        not isinstance(value, str)
        or not value.startswith("/")
        or value.startswith("//")
        or "\\" in value
        or any(char in value for char in "?#%*")
        or any(ord(char) < 32 for char in value)
        or "//" in value
    ):
        return False
    return all(segment not in {".", ".."} for segment in value.split("/")[1:])


__all__ = (
    "AistarslabReferenceInputs",
    "AistarslabVideoProvider",
    "CREATE_PATH",
    "PROVIDER_ID",
    "STATUS_PATH",
)
