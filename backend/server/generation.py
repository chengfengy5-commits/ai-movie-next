"""Build the opt-in frame.optimize host dependencies from one server database."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.ports import GenerationRuntimeConfig
from haoai_backend.generation_credentials.application import (
    build_credential_registry,
)
from haoai_backend.generation_contracts.types import (
    ExternalOperationRef,
    FrozenJSON,
    PreparedReference,
    ProviderContentCapability,
    ReferenceInput,
    StoredMedia,
    TransferFailure,
)
from haoai_backend.generation_runtime.frame_optimize_assembly import (
    FrameOptimizeWorkerConfiguration,
)

from .configuration import ServerConfiguration
from .credentials import load_registry_capabilities
from .http import AiohttpAsyncHttpClientFactory


class GenerationHostConfigurationError(ValueError):
    """A frame.optimize host dependency is explicitly unavailable or invalid."""


def build_frame_optimize_configuration(
    configuration: ServerConfiguration,
    *,
    session_factory: Callable[[], Session],
) -> FrameOptimizeWorkerConfiguration | None:
    """Load Root capabilities and bind runtime ports to the host's single Session factory."""
    credentials_path = configuration.frame_optimize_credentials_file
    if credentials_path is None:
        if configuration.frame_optimize_enabled:
            raise GenerationHostConfigurationError(
                "frame.optimize credentials are required when enabled"
            )
        return None
    if not isinstance(credentials_path, Path) or not credentials_path.is_absolute():
        raise GenerationHostConfigurationError(
            "frame.optimize credentials path must be absolute"
        )

    db_clock = _database_clock(session_factory)
    id_factory = _uuid_id
    capabilities = load_registry_capabilities(
        credentials_path,
        legacy_secret_key=configuration.secret_key.value,
        session_factory=session_factory,
        db_clock=db_clock,
        id_factory=id_factory,
    )
    registry = build_credential_registry(capabilities)
    runtime = GenerationRuntimeConfig(
        session_factory=session_factory,
        db_clock=db_clock,
        id_factory=id_factory,
        http_client_factory=AiohttpAsyncHttpClientFactory(),
        credential_resolver=registry.resolve_original_credential,
        provider_factories=(),
        media_store_factory=_UnavailableGenerationMediaStore,
        local_muse_staging_dir=None,
        public_media_fetcher_factory=_UnavailablePublicMediaFetcher,
        bound_provider_content_fetcher_factory=_UnavailableBoundContentFetcher,
        local_muse_capability_factory=_UnavailableLocalMuseCapability,
        r2_storage_factory=_UnavailableR2StorageCapability,
    )
    return FrameOptimizeWorkerConfiguration(
        runtime=runtime,
        registry_capabilities=capabilities,
        max_user_concurrency=configuration.frame_optimize_max_user_concurrency,
        enabled=configuration.frame_optimize_enabled,
    )


def _database_clock(
    session_factory: Callable[[], Session],
) -> Callable[[], datetime]:
    if not callable(session_factory):
        raise GenerationHostConfigurationError("server Session factory is unavailable")

    def read_database_utc() -> datetime:
        session = None
        try:
            session = session_factory()
            value = session.execute(select(func.current_timestamp())).scalar_one()
        except Exception:
            raise RuntimeError("database UTC clock is unavailable") from None
        finally:
            if session is not None:
                try:
                    session.close()
                except Exception:
                    raise RuntimeError(
                        "database UTC clock session could not be closed"
                    ) from None

        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                raise RuntimeError("database UTC clock returned an invalid value") from None
        if not isinstance(value, datetime):
            raise RuntimeError("database UTC clock returned an invalid value")
        if value.tzinfo is None or value.utcoffset() is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    return read_database_utc


def _uuid_id() -> str:
    return str(uuid.uuid4())


class _UnavailableGenerationMediaStore:
    def normalize_managed_url(
        self, url: str, explicit_origin_map: Mapping[str, str]
    ) -> str:
        raise RuntimeError("generation media storage is unavailable in this host")

    def prepare_reference(
        self,
        input: ReferenceInput,
        capability: ProviderContentCapability | None,
        config: GenerationRuntimeConfig,
    ) -> PreparedReference:
        raise RuntimeError("generation media storage is unavailable in this host")

    def transfer_public_url(
        self, input: ReferenceInput, guard_config: FrozenJSON
    ) -> StoredMedia | TransferFailure:
        raise RuntimeError("generation media storage is unavailable in this host")

    def store_bytes_or_provider_content(
        self, input: ReferenceInput, object_policy: FrozenJSON
    ) -> StoredMedia:
        raise RuntimeError("generation media storage is unavailable in this host")

    def release_prepared_reference(
        self,
        handle: PreparedReference,
    ) -> Literal["released", "already_released", "not_owned", "failed"]:
        raise RuntimeError("generation media storage is unavailable in this host")

    def fetch_bound_content(
        self,
        external_ref: ExternalOperationRef,
        capability: ProviderContentCapability,
    ) -> PreparedReference:
        raise RuntimeError("generation media storage is unavailable in this host")


class _UnavailablePublicMediaFetcher:
    async def fetch_public(
        self,
        reference: ReferenceInput,
        *,
        max_bytes: int,
        timeout_seconds: float,
    ) -> PreparedReference | TransferFailure:
        raise RuntimeError("public media fetching is unavailable in this host")


class _UnavailableBoundContentFetcher:
    async def fetch_bound_content(
        self,
        external_ref: ExternalOperationRef,
        capability: ProviderContentCapability,
    ) -> PreparedReference | TransferFailure:
        raise RuntimeError("bound provider content fetching is unavailable in this host")


class _UnavailableLocalMuseCapability:
    def stage_local_reference(
        self, reference: ReferenceInput, staging_directory: Path
    ) -> PreparedReference | TransferFailure:
        raise RuntimeError("local media staging is unavailable in this host")


class _UnavailableR2StorageCapability:
    def store_prepared(self, reference: PreparedReference) -> StoredMedia | TransferFailure:
        raise RuntimeError("R2 storage is unavailable in this host")

    def release(
        self,
        stored: StoredMedia,
    ) -> Literal["released", "already_released", "not_owned", "failed"]:
        raise RuntimeError("R2 storage is unavailable in this host")


__all__ = (
    "GenerationHostConfigurationError",
    "build_frame_optimize_configuration",
)
