"""Lazy one-shot executor for one authenticated item in an image.batch graph."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Protocol
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.ports import GenerationRuntimeConfig
from haoai_backend.generation_contracts.records import GenerationResultReceipt
from haoai_backend.generation_contracts.types import (
    ClaimAuthority,
    CommittedIntent,
    Failure,
    MediaResult,
    PreparedReference,
    ReferenceInput,
    StoredMedia,
    UnknownCreate,
)
from haoai_backend.generation_core.base_schema import AITaskRow, UserRow
from haoai_backend.generation_core.image_batch_claims import SqlAlchemyImageBatchClaims
from haoai_backend.generation_core.image_batch_intents import (
    SqlAlchemyImageBatchIntents,
)
from haoai_backend.generation_credentials.application import (
    RegistryCapabilities,
    build_credential_registry,
)
from haoai_backend.generation_credentials.batch_execution_snapshot import (
    VerifiedImageBatchExecutionInput,
)
from haoai_backend.generation_credentials.domain import CredentialCrypto, HmacProofAuthority
from haoai_backend.generation_providers.image.openai_provider import (
    build_original_openai_image_provider,
)
from haoai_backend.media_uploads.domain import (
    InvalidMediaUpload,
    upload_spec,
    validate_upload_content,
)

_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_MAX_REFERENCE_IMAGES = 8
_MAX_REFERENCE_TOTAL_BYTES = _MAX_REFERENCE_IMAGES * _MAX_IMAGE_BYTES
_IMAGE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
}
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_DATA_URI = re.compile(r"data:(image/[a-z0-9.+-]+);base64,([A-Za-z0-9+/]*={0,2})\Z")


class BatchResultPort(Protocol):
    def complete(
        self,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified: VerifiedImageBatchExecutionInput,
        stored: StoredMedia,
    ) -> GenerationResultReceipt | Literal["pending", "ownership_lost"]: ...

    def fail(
        self,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified: VerifiedImageBatchExecutionInput,
        *,
        code: str,
        safe_detail: str,
        provider_request_started: bool = True,
    ) -> None | Literal["pending", "ownership_lost"]: ...

    def mark_unknown(
        self,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified: VerifiedImageBatchExecutionInput,
    ) -> Literal["pending", "ownership_lost"]: ...

    def record_unknown_observation(
        self,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified: VerifiedImageBatchExecutionInput,
        *,
        payload_digest: str,
        result_location: str | None,
        evidence_kind: str,
    ) -> Literal["recorded", "pending"]: ...


@dataclass(frozen=True, slots=True)
class _ImageObservation:
    payload_digest: str
    result_location: str | None
    evidence_kind: str


@dataclass(frozen=True, slots=True)
class _StorageOutcome:
    stored: StoredMedia | None
    observation: _ImageObservation | None


class _ExecutionAuthorityLost(RuntimeError):
    pass


class _AuthorityGuard:
    def __init__(
        self,
        configuration: GenerationRuntimeConfig,
        claim: ClaimAuthority,
        intent_deadline: datetime,
    ) -> None:
        self._configuration = configuration
        self._claim = claim
        self._intent_deadline = _aware_utc(intent_deadline)
        self._provider_request_started = False

    @property
    def provider_request_started(self) -> bool:
        return self._provider_request_started

    def mark_provider_request_started(self) -> None:
        self._provider_request_started = True

    def is_live(self) -> bool:
        claim = self._claim
        if claim.local_lost_signal.is_set():
            return False
        try:
            with self._configuration.session_factory() as session:
                with session.begin():
                    user_id = session.execute(
                        select(UserRow.id)
                        .where(UserRow.id == claim.scope.user_id)
                        .with_for_update(skip_locked=True)
                    ).scalar_one_or_none()
                    task = session.execute(
                        select(AITaskRow.__table__)
                        .where(
                            AITaskRow.id == claim.scope.task_id,
                            AITaskRow.user_id == claim.scope.user_id,
                            AITaskRow.type == "batch-image",
                        )
                        .with_for_update(skip_locked=True)
                    ).mappings().one_or_none()
                    now = self._configuration.db_clock()
                    if user_id is None or task is None or not _aware_datetime(now):
                        return False
                    lease = task.get("lease_until")
                    live = (
                        not claim.local_lost_signal.is_set()
                        and task.get("status") in {"processing", "reconciling"}
                        and task.get("user_cancelled_at") is None
                        and task.get("claimed_by") == claim.worker_instance
                        and task.get("claim_token") == claim.token
                        and task.get("execution_generation") == claim.execution_generation
                        and isinstance(lease, datetime)
                        and _aware_utc(lease) > _aware_utc(now)
                        and _aware_utc(now) < self._intent_deadline
                    )
                    if not live:
                        return False

                    task_table = AITaskRow.__table__
                    confirmed = session.execute(
                        update(task_table)
                        .where(
                            task_table.c.id == claim.scope.task_id,
                            task_table.c.user_id == claim.scope.user_id,
                            task_table.c.type == "batch-image",
                            task_table.c.status == task.get("status"),
                            task_table.c.status.in_(("processing", "reconciling")),
                            task_table.c.user_cancelled_at.is_(None),
                            task_table.c.claimed_by == claim.worker_instance,
                            task_table.c.claim_token == claim.token,
                            task_table.c.execution_generation
                            == claim.execution_generation,
                            task_table.c.lease_until == _database_datetime(lease),
                            task_table.c.lease_until > _database_datetime(now),
                        )
                        .values(updated_at=task_table.c.updated_at)
                    )
                    return (
                        confirmed.rowcount == 1
                        and not claim.local_lost_signal.is_set()
                    )
        except Exception:
            return False

    def require_live(self) -> None:
        if not self.is_live():
            raise _ExecutionAuthorityLost


class _GatedHttpClient:
    def __init__(self, client: object, guard: _AuthorityGuard) -> None:
        self._client = client
        self._guard = guard

    async def request(self, *args: object, **kwargs: object):
        self._guard.require_live()
        self._guard.mark_provider_request_started()
        return await self._client.request(*args, **kwargs)


class _GatedHttpContext:
    def __init__(self, factory, guard: _AuthorityGuard) -> None:
        self._factory = factory
        self._guard = guard
        self._context = None

    async def __aenter__(self):
        self._guard.require_live()
        context = self._factory()
        self._guard.require_live()
        client = await context.__aenter__()
        try:
            self._guard.require_live()
        except BaseException:
            await context.__aexit__(*sys.exc_info())
            raise
        self._context = context
        return _GatedHttpClient(client, self._guard)

    async def __aexit__(self, exc_type, exc, traceback):
        if self._context is None:
            return False
        return await self._context.__aexit__(exc_type, exc, traceback)


class _GatedPublicFetcher:
    def __init__(self, fetcher: object, guard: _AuthorityGuard) -> None:
        self._fetcher = fetcher
        self._guard = guard

    async def fetch_public(self, *args: object, **kwargs: object):
        self._guard.require_live()
        result = await self._fetcher.fetch_public(*args, **kwargs)
        self._guard.require_live()
        return result


class ImageBatchExecutor:
    """Execute only the one image.batch child named by a current SQL claim."""

    def __init__(
        self,
        configuration: GenerationRuntimeConfig,
        registry_capabilities: RegistryCapabilities,
    ) -> None:
        self._configuration = configuration
        self._registry_capabilities = registry_capabilities
        self._execution_port: SqlAlchemyImageBatchClaims | None = None

    def bind_execution_port(self, execution_port: object) -> None:
        if not isinstance(execution_port, SqlAlchemyImageBatchClaims):
            raise TypeError("image.batch executor requires its registered claim port")
        self._execution_port = execution_port

    def execute_claim(self, claim: ClaimAuthority):
        if (
            not isinstance(claim, ClaimAuthority)
            or claim.scope.operation != "image.batch"
            or claim.local_lost_signal.is_set()
            or self._execution_port is None
        ):
            return "ownership_lost"
        crypto = self._registry_capabilities.crypto
        proof_authority = self._registry_capabilities.proof_authority
        if not isinstance(crypto, CredentialCrypto) or not isinstance(
            proof_authority, HmacProofAuthority
        ):
            return "pending"

        registry = build_credential_registry(self._registry_capabilities)
        intents = SqlAlchemyImageBatchIntents(
            self._configuration.session_factory,
            self._configuration.db_clock,
            self._configuration.id_factory,
            registry,
            crypto,
            proof_authority,
        )
        try:
            results = _build_batch_results(self._configuration)
        except Exception:
            return "pending"

        prepared = intents.prepare(claim)
        if prepared.disposition == "ownership_lost":
            return "ownership_lost"
        if (
            prepared.disposition != "committed"
            or prepared.intent is None
            or prepared.verified_input is None
        ):
            # An existing durable intent is a recovery fact, never permission to re-POST.
            return "pending"

        verified = prepared.verified_input
        intent = prepared.intent
        guard = _AuthorityGuard(self._configuration, claim, intent.deadline_at)
        try:
            provider_references = self._prepare_reference_images(
                verified.references,
                guard,
            )
        except _ExecutionAuthorityLost:
            return "ownership_lost"
        except Exception:
            if not guard.is_live():
                return "ownership_lost"
            return results.fail(
                claim,
                intent,
                verified,
                code="provider_transport_unavailable",
                safe_detail="参考图未能安全读取",
                provider_request_started=False,
            )
        if isinstance(provider_references, Failure):
            if not guard.is_live():
                return "ownership_lost"
            return results.fail(
                claim,
                intent,
                verified,
                code=provider_references.code,
                safe_detail=provider_references.safe_detail,
                provider_request_started=False,
            )

        try:
            guard.require_live()
            provider = build_original_openai_image_provider(
                snapshot=verified.snapshot,
                execution_snapshot_ref=intent.snapshot_ref,
                credential_binding=verified.credential_binding,
                intent=intent,
                wire_model=verified.snapshot.model_name,
                http_client_factory=lambda: _GatedHttpContext(
                    self._configuration.http_client_factory, guard
                ),
                credential_resolver=lambda binding, purpose: self._resolve_credential(
                    binding, purpose, guard
                ),
            )
            guard.require_live()
        except Exception:
            if not guard.is_live():
                return "ownership_lost"
            return results.mark_unknown(claim, intent, verified)

        try:
            provider_result = provider.generate_sync_image(
                verified.snapshot,
                provider_references,
                intent,
            )
        except Exception:
            if not guard.is_live():
                return "ownership_lost"
            return results.mark_unknown(claim, intent, verified)

        if isinstance(provider_result, Failure):
            if not guard.is_live():
                return "ownership_lost"
            return results.fail(
                claim,
                intent,
                verified,
                code=provider_result.code,
                safe_detail=provider_result.safe_detail,
                provider_request_started=guard.provider_request_started,
            )
        if isinstance(provider_result, UnknownCreate) or not isinstance(
            provider_result, MediaResult
        ):
            return (
                results.mark_unknown(claim, intent, verified)
                if guard.is_live()
                else "ownership_lost"
            )

        if not guard.is_live():
            self._record_provider_observation(
                results, claim, intent, verified, provider_result
            )
            return "ownership_lost"
        storage = self._store_provider_media(provider_result, guard)
        if storage.stored is None:
            if storage.observation is not None:
                results.record_unknown_observation(
                    claim,
                    intent,
                    verified,
                    payload_digest=storage.observation.payload_digest,
                    result_location=storage.observation.result_location,
                    evidence_kind=storage.observation.evidence_kind,
                )
            if not guard.is_live():
                return "ownership_lost"
            return results.mark_unknown(claim, intent, verified)
        if not guard.is_live():
            stored_observation = _observation_for_stored(storage.stored)
            if stored_observation is not None:
                results.record_unknown_observation(
                    claim,
                    intent,
                    verified,
                    payload_digest=stored_observation.payload_digest,
                    result_location=stored_observation.result_location,
                    evidence_kind=stored_observation.evidence_kind,
                )
            return "ownership_lost"
        outcome = results.complete(claim, intent, verified, storage.stored)
        if outcome in {"ownership_lost", "pending"}:
            stored_observation = _observation_for_stored(storage.stored)
            if stored_observation is not None:
                results.record_unknown_observation(
                    claim,
                    intent,
                    verified,
                    payload_digest=stored_observation.payload_digest,
                    result_location=stored_observation.result_location,
                    evidence_kind=stored_observation.evidence_kind,
                )
        return outcome

    def _prepare_reference_images(
        self,
        references: tuple[ReferenceInput, ...],
        guard: _AuthorityGuard,
    ) -> tuple[ReferenceInput, ...] | Failure:
        if len(references) > _MAX_REFERENCE_IMAGES:
            return Failure("request_body_too_large", "参考图数量超过图片编辑请求上限")
        prepared: list[ReferenceInput] = []
        total_bytes = 0
        for reference in references:
            guard.require_live()
            if type(reference) is not ReferenceInput or not isinstance(
                reference.locator, str
            ):
                return Failure("invalid_frozen_snapshot", "冻结参考图输入格式无效")
            if reference.kind == "data_uri":
                parsed = _decode_image_data_uri(reference.locator)
                guard.require_live()
                if parsed is None:
                    return Failure("invalid_frozen_snapshot", "参考图无法通过图片校验")
                content_and_type = self._validate_image(*parsed)
            elif reference.kind in {"public_http", "managed_url"}:
                if not _valid_public_reference_locator(reference.locator):
                    return Failure("invalid_frozen_snapshot", "参考图地址无法通过安全校验")
                guard.require_live()
                try:
                    fetcher = self._configuration.public_media_fetcher_factory()
                finally:
                    guard.require_live()
                try:
                    fetched = asyncio.run(
                        _GatedPublicFetcher(fetcher, guard).fetch_public(
                            reference,
                            max_bytes=_MAX_IMAGE_BYTES,
                            timeout_seconds=(
                                self._configuration.timeout_settings.public_media_fetch_seconds
                            ),
                        )
                    )
                except _ExecutionAuthorityLost:
                    raise
                except Exception:
                    return Failure(
                        "provider_transport_unavailable", "参考图未能安全读取"
                    )
                guard.require_live()
                if (
                    not isinstance(fetched, PreparedReference)
                    or fetched.kind != "bytes"
                    or fetched.locator is not None
                    or type(fetched.content) is not bytes
                    or not isinstance(fetched.media_type, str)
                ):
                    return Failure(
                        "provider_transport_unavailable", "参考图未能安全读取"
                    )
                content_and_type = self._validate_image(
                    fetched.content, fetched.media_type
                )
            else:
                return Failure("invalid_frozen_snapshot", "冻结参考图类型不受支持")

            guard.require_live()
            if content_and_type is None:
                return Failure("invalid_frozen_snapshot", "参考图无法通过图片校验")
            content, media_type = content_and_type
            total_bytes += len(content)
            if total_bytes > _MAX_REFERENCE_TOTAL_BYTES:
                return Failure(
                    "request_body_too_large", "参考图数据超过图片编辑请求上限"
                )
            prepared.append(
                ReferenceInput(kind="bytes", content=content, media_type=media_type)
            )
        return tuple(prepared)

    def _resolve_credential(self, binding, purpose, guard: _AuthorityGuard):
        guard.require_live()
        credential = self._configuration.credential_resolver(binding, purpose)
        guard.require_live()
        return credential

    def _store_provider_media(
        self, result: MediaResult, guard: _AuthorityGuard
    ) -> _StorageOutcome:
        if result.media_kind != "image" or not isinstance(result.locator, str):
            return _StorageOutcome(None, None)
        content_and_type = self._read_provider_image(result.locator, guard)
        if content_and_type is None:
            return _StorageOutcome(None, _observation_from_provider_result(result))
        content, media_type = content_and_type
        expected_digest = hashlib.sha256(content).hexdigest()
        observation = _ImageObservation(expected_digest, None, "provider_image_bytes")
        if result.content_digest is not None and result.content_digest != expected_digest:
            return _StorageOutcome(None, observation)
        if not guard.is_live():
            return _StorageOutcome(None, observation)
        try:
            guard.require_live()
            store = self._configuration.media_store_factory()
            guard.require_live()
            guard.require_live()
            stored = store.store_bytes_or_provider_content(
                ReferenceInput(kind="bytes", content=content, media_type=media_type),
                {"max_bytes": _MAX_IMAGE_BYTES},
            )
        except Exception:
            return _StorageOutcome(
                None,
                observation if not guard.is_live() else None,
            )
        if not isinstance(stored, StoredMedia):
            return _StorageOutcome(None, observation)
        if not guard.is_live():
            return _StorageOutcome(
                None,
                _ImageObservation(
                    expected_digest,
                    _safe_managed_locator(stored.locator),
                    "provider_image_bytes",
                ),
            )
        if (
            not stored.locator
            or stored.locator.startswith("data:")
            or stored.content_digest != expected_digest
            or stored.media_type != media_type
            or stored.size_bytes != len(content)
            or _DIGEST.fullmatch(stored.content_digest) is None
        ):
            return _StorageOutcome(None, observation)
        return _StorageOutcome(stored, observation)

    def _read_provider_image(
        self, locator: str, guard: _AuthorityGuard
    ) -> tuple[bytes, str] | None:
        if locator.startswith("data:"):
            parsed = _decode_image_data_uri(locator)
            return self._validate_image(*parsed) if parsed is not None else None
        try:
            parsed_url = urlsplit(locator)
            if (
                parsed_url.scheme not in {"http", "https"}
                or not parsed_url.netloc
                or parsed_url.username is not None
                or parsed_url.password is not None
            ):
                return None
            guard.require_live()
            fetcher = self._configuration.public_media_fetcher_factory()
            guard.require_live()
            fetched = asyncio.run(
                _GatedPublicFetcher(fetcher, guard).fetch_public(
                    ReferenceInput(kind="public_http", locator=locator),
                    max_bytes=_MAX_IMAGE_BYTES,
                    timeout_seconds=(
                        self._configuration.timeout_settings.public_media_fetch_seconds
                    ),
                )
            )
        except Exception:
            return None
        if (
            not isinstance(fetched, PreparedReference)
            or fetched.kind != "bytes"
            or type(fetched.content) is not bytes
            or not isinstance(fetched.media_type, str)
        ):
            return None
        return self._validate_image(fetched.content, fetched.media_type)

    def _record_provider_observation(
        self,
        results: BatchResultPort,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified: VerifiedImageBatchExecutionInput,
        result: MediaResult,
    ) -> None:
        observation = _observation_from_provider_result(result)
        if observation is None:
            return
        results.record_unknown_observation(
            claim,
            intent,
            verified,
            payload_digest=observation.payload_digest,
            result_location=observation.result_location,
            evidence_kind=observation.evidence_kind,
        )

    @staticmethod
    def _validate_image(content: bytes, media_type: str) -> tuple[bytes, str] | None:
        extension = _IMAGE_EXTENSIONS.get(media_type)
        if (
            type(content) is not bytes
            or not content
            or len(content) > _MAX_IMAGE_BYTES
            or extension is None
        ):
            return None
        try:
            spec = upload_spec("image", f"generated-image{extension}")
            if spec.content_type != media_type:
                return None
            validate_upload_content(spec, content)
        except (InvalidMediaUpload, ValueError):
            return None
        return content, media_type


def _build_batch_results(configuration: GenerationRuntimeConfig) -> BatchResultPort:
    from haoai_backend.generation_core.image_batch_results import SqlAlchemyImageBatchResults

    return SqlAlchemyImageBatchResults(
        configuration.session_factory,
        configuration.db_clock,
        configuration.id_factory,
    )


def _decode_image_data_uri(value: str) -> tuple[bytes, str] | None:
    match = _DATA_URI.fullmatch(value)
    if match is None:
        return None
    media_type, encoded = match.groups()
    if media_type not in _IMAGE_EXTENSIONS or not encoded:
        return None
    if len(encoded) > 4 * ((_MAX_IMAGE_BYTES + 2) // 3):
        return None
    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        return None
    if not content or len(content) > _MAX_IMAGE_BYTES:
        return None
    return content, media_type


def _valid_public_reference_locator(value: str) -> bool:
    if any(ord(character) <= 0x20 or ord(character) == 0x7F for character in value):
        return False
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
    )


def _observation_from_provider_result(result: MediaResult) -> _ImageObservation | None:
    if result.media_kind != "image" or not isinstance(result.locator, str):
        return None
    if result.locator.startswith("data:"):
        decoded = _decode_image_data_uri(result.locator)
        if decoded is None:
            return None
        content, _media_type = decoded
        digest = hashlib.sha256(content).hexdigest()
        if result.content_digest is not None and result.content_digest != digest:
            return None
        return _ImageObservation(digest, None, "provider_image_bytes")
    safe_locator = _safe_managed_locator(result.locator)
    if safe_locator is None:
        return None
    digest = result.content_digest
    if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
        digest = hashlib.sha256(result.locator.encode("utf-8")).hexdigest()
    return _ImageObservation(digest, safe_locator, "provider_image_locator")


def _observation_for_stored(stored: StoredMedia) -> _ImageObservation | None:
    safe_locator = _safe_managed_locator(stored.locator)
    if safe_locator is None or _DIGEST.fullmatch(stored.content_digest) is None:
        return None
    return _ImageObservation(stored.content_digest, safe_locator, "provider_image_bytes")


def _safe_managed_locator(locator: str) -> str | None:
    if not isinstance(locator, str) or len(locator) > 4096:
        return None
    try:
        parsed = urlsplit(locator)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            return None
        safe = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        return safe if len(safe) <= 2048 else None
    except ValueError:
        return None


def _aware_datetime(value: object) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _aware_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _database_datetime(value: datetime) -> datetime:
    return _aware_utc(value).replace(tzinfo=None)


__all__ = ("BatchResultPort", "ImageBatchExecutor")
