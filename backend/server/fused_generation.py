"""Explicit ServerHost assembly for fused image generation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from starlette.concurrency import run_in_threadpool

from haoai_backend.fused_generation.domain import FusedGenerationStorageUnknown
from haoai_backend.fused_generation.media_storage import (
    SynchronousFusedGenerationImageStorage,
)
from haoai_backend.fused_generation.openai_runtime import FusedOpenAIImageRuntime
from haoai_backend.generation_contracts import ReferenceInput, StoredMedia, TransferFailure
from haoai_backend.generation_credentials.application import (
    CredentialRegistry,
    build_credential_registry,
)
from haoai_backend.generation_credentials.domain import CredentialCrypto, HmacProofAuthority
from haoai_backend.media_uploads.domain import MAX_IMAGE_BYTES
from haoai_backend.media_storage.sync_facade import SynchronousMediaStore
from haoai_backend.media_storage.transfer_types import MAX_TRANSFER_SECONDS

from .configuration import ServerConfiguration, ServerConfigurationError
from .http import AiohttpAsyncHttpClientFactory
from .media_runtime import ServerMediaRuntimeResources
from .credentials import load_registry_capabilities
from .generation import _database_clock, _uuid_id

_UNKNOWN_TRANSFER_FAILURES = frozenset(
    {"cleanup_failure", "storage_outcome_unverified", "timeout", "transfer_failure"}
)


@dataclass(frozen=True, slots=True)
class FusedGenerationHostConfiguration:
    runtime: FusedOpenAIImageRuntime = field(repr=False)
    storage: FusedGenerationURLStorage = field(repr=False)
    inline_image_storage: SynchronousFusedGenerationImageStorage = field(repr=False)
    credential_registry: CredentialRegistry = field(repr=False)
    credential_crypto: CredentialCrypto = field(repr=False)
    proof_authority: HmacProofAuthority = field(repr=False)


class FusedGenerationURLStorage:
    """Use the shared public-media transfer path and preserve ambiguous results."""

    def __init__(self, media_store: SynchronousMediaStore) -> None:
        if not isinstance(media_store, SynchronousMediaStore):
            raise TypeError("an explicit SynchronousMediaStore is required")
        self._media_store = media_store

    async def store_generated_image(
        self,
        provider_url: str,
    ) -> str | None | FusedGenerationStorageUnknown:
        worker = asyncio.create_task(
            run_in_threadpool(self._transfer_public_url, provider_url)
        )
        result, cancellation_requested = await _drain_worker(worker)
        if cancellation_requested:
            return FusedGenerationStorageUnknown(cancellation_requested=True)
        if type(result) is StoredMedia:
            if (
                type(result.locator) is str
                and result.locator
                and result.locator == result.locator.strip()
                and type(result.media_type) is str
                and result.media_type.startswith("image/")
            ):
                return result.locator
            return FusedGenerationStorageUnknown()
        if type(result) is TransferFailure:
            if result.code in _UNKNOWN_TRANSFER_FAILURES:
                return FusedGenerationStorageUnknown()
            return None
        return FusedGenerationStorageUnknown()

    def _transfer_public_url(self, provider_url: str) -> StoredMedia | TransferFailure:
        return self._media_store.transfer_public_url(
            ReferenceInput(kind="public_http", locator=provider_url),
            {
                "max_bytes": MAX_IMAGE_BYTES,
                "timeout_seconds": min(MAX_TRANSFER_SECONDS, 180),
            },
        )


def build_fused_generation_host_configuration(
    configuration: ServerConfiguration,
    *,
    session_factory,
    media_resources: ServerMediaRuntimeResources | None,
) -> FusedGenerationHostConfiguration:
    if (
        not isinstance(configuration, ServerConfiguration)
        or not configuration.fused_generation_enabled
        or not configuration.fused_generation_provider_origins
        or configuration.r2_configuration is None
        or configuration.r2_access_key is None
        or configuration.r2_secret_key is None
        or configuration.fused_generation_credentials_file is None
        or media_resources is None
    ):
        raise ServerConfigurationError(
            "fused generation runtime dependencies are incomplete"
        )

    capabilities = load_registry_capabilities(
        configuration.fused_generation_credentials_file,
        legacy_secret_key=configuration.secret_key.value,
        session_factory=session_factory,
        db_clock=_database_clock(session_factory),
        id_factory=_uuid_id,
    )
    if capabilities.crypto is None or capabilities.proof_authority is None:
        raise ServerConfigurationError("fused credential capabilities are incomplete")
    registry = build_credential_registry(capabilities)
    media_store = media_resources.media_store_factory()
    runtime = FusedOpenAIImageRuntime(
        http_client_factory=AiohttpAsyncHttpClientFactory(),
        public_media_fetcher=media_resources.public_media_fetcher_factory(),
        allowed_provider_origins=configuration.fused_generation_provider_origins,
    )
    return FusedGenerationHostConfiguration(
        runtime=runtime,
        storage=FusedGenerationURLStorage(media_store),
        inline_image_storage=SynchronousFusedGenerationImageStorage(media_store),
        credential_registry=registry,
        credential_crypto=capabilities.crypto,
        proof_authority=capabilities.proof_authority,
    )


async def _drain_worker(worker: asyncio.Task[object]) -> tuple[object | None, bool]:
    cancellation_requested = False
    while not worker.done():
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError:
            if worker.cancelled():
                if cancellation_requested:
                    return None, True
                raise
            cancellation_requested = True
        except Exception:
            if cancellation_requested:
                return None, True
            return None, False

    try:
        return worker.result(), cancellation_requested
    except asyncio.CancelledError:
        if cancellation_requested:
            return None, True
        raise
    except Exception:
        if cancellation_requested:
            return None, True
        return None, False
