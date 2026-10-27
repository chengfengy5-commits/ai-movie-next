"""Explicit, opt-in host dependencies for the image.single worker."""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy.orm import Session

from haoai_backend.chat_media_admission import ChatMediaAdmissionConfiguration
from haoai_backend.generation_catalog.admission import AdmissionPolicy
from haoai_backend.generation_contracts.ports import (
    BoundProviderContentFetcher,
    GenerationRuntimeConfig,
    LocalMuseCapability,
    PublicMediaFetcher,
    R2StorageCapability,
    RuntimeConcurrencySettings,
    RuntimeTimeoutSettings,
)
from haoai_backend.generation_contracts.types import (
    ExternalOperationRef,
    PreparedReference,
    ProviderContentCapability,
    ReferenceInput,
    StoredMedia,
    TransferFailure,
)
from haoai_backend.generation_credentials.application import (
    RegistryCapabilities,
    build_credential_registry,
)
from haoai_backend.generation_credentials.snapshot_producer import SnapshotVersions
from haoai_backend.generation_runtime.assembly import GenerationWorkerConfiguration
from haoai_backend.generation_runtime.image_batch_assembly import (
    ImageBatchWorkerConfiguration,
)
from haoai_backend.generation_runtime.video_single_assembly import (
    VideoSingleWorkerConfiguration,
)
from haoai_backend.media_storage.aiohttp_library import AiohttpMatureHttpLibrary
from haoai_backend.media_storage.owned_media_runtime import (
    OwnedMediaDependencies,
    OwnedMediaRuntime,
    OwnedMediaServices,
)
from haoai_backend.media_storage.pinned_http import PinnedPublicMediaFetcher
from haoai_backend.media_storage.public_fetch_policy import (
    PublicFetchPolicy,
)
from haoai_backend.media_storage.r2_sdk_factory import Boto3S3ClientFactory
from haoai_backend.media_storage.sync_facade import SynchronousMediaStore
from haoai_backend.media_storage.transfer_types import (
    MAX_TRANSFER_BYTES,
    MAX_TRANSFER_SECONDS,
    MediaTypeRule,
    R2Configuration,
    SafeMediaPolicy,
    SecretHandle,
    SecretValue,
    S3ClientFactory,
)

from .configuration import ServerConfiguration
from .credentials import load_registry_capabilities
from .generation import _database_clock, _uuid_id
from .http import AiohttpAsyncHttpClientFactory
from .media_runtime import ServerMediaRuntimeResources


class ImageGenerationHostConfigurationError(RuntimeError):
    """An enabled image host cannot be assembled without its dependencies."""


@dataclass(frozen=True, slots=True)
class ImageGenerationHostConfiguration:
    """Shared admission and media dependencies for opt-in generation workers."""

    admission_configuration: ChatMediaAdmissionConfiguration | None
    generation_worker_configuration: GenerationWorkerConfiguration | None
    media_runtime: OwnedMediaRuntime | None = field(default=None, repr=False)
    image_batch_worker_configuration: ImageBatchWorkerConfiguration | None = None
    video_single_worker_configuration: VideoSingleWorkerConfiguration | None = None

    def __post_init__(self) -> None:
        if self.admission_configuration is None:
            if any(
                value is not None
                for value in (
                    self.generation_worker_configuration,
                    self.image_batch_worker_configuration,
                    self.video_single_worker_configuration,
                    self.media_runtime,
                )
            ):
                raise ValueError("disabled image host cannot own worker resources")
            return
        if (
            self.media_runtime is None
            or (
                self.generation_worker_configuration is None
                and self.image_batch_worker_configuration is None
                and self.video_single_worker_configuration is None
            )
        ):
            raise ValueError(
                "enabled image host dependencies must be assembled together"
            )
        worker_configurations = tuple(
            worker
            for worker in (
                self.generation_worker_configuration,
                self.image_batch_worker_configuration,
                self.video_single_worker_configuration,
            )
            if worker is not None
        )
        if any(
            worker.runtime is not worker_configurations[0].runtime
            or worker.registry_capabilities
            is not worker_configurations[0].registry_capabilities
            for worker in worker_configurations[1:]
        ):
            raise ValueError(
                "generation workers must share one runtime and credential registry"
            )


@dataclass(frozen=True, slots=True, repr=False)
class _SecretHandle:
    value: SecretValue = field(repr=False)

    def reveal(self) -> SecretValue:
        return self.value

    def __repr__(self) -> str:
        return "_SecretHandle(<redacted>)"


def build_server_media_runtime_resources(
    configuration: ServerConfiguration,
    *,
    s3_client_factory: S3ClientFactory | None,
    include_video: bool,
) -> ServerMediaRuntimeResources:
    """Build one cold media owner shared by enabled generation families."""

    if (
        not isinstance(configuration, ServerConfiguration)
        or configuration.r2_configuration is None
        or configuration.r2_access_key is None
        or configuration.r2_secret_key is None
        or not isinstance(s3_client_factory, Boto3S3ClientFactory)
    ):
        raise ImageGenerationHostConfigurationError(
            "generation media requires complete object storage"
        )

    media_policy = _image_media_policy(include_video=include_video)
    http_library = _build_http_library()
    generation_r2 = replace(configuration.r2_configuration, business_type="generation")
    managed_origin_map = _managed_origin_map(configuration.r2_configuration)
    media_runtime = OwnedMediaRuntime(
        OwnedMediaDependencies(
            transfer_tmp_dir=generation_r2.transfer_tmp_dir,
            getaddrinfo=_system_getaddrinfo,
            http_library=http_library,
            media_policy=media_policy,
            r2_configuration=generation_r2,
            access_key=_SecretHandle(configuration.r2_access_key),
            secret_key=_SecretHandle(configuration.r2_secret_key),
            s3_client_factory=s3_client_factory,
        ),
        loop_factory=asyncio.new_event_loop,
        thread_factory=threading.Thread,
        executor_factory=concurrent.futures.ThreadPoolExecutor,
    )

    def media_store_factory() -> SynchronousMediaStore:
        return SynchronousMediaStore(
            media_runtime,
            mode="url_or_inline",
            reference_stager=None,
            staging_directory=None,
            managed_origin_map=managed_origin_map,
        )

    def public_media_fetcher_factory() -> PublicMediaFetcher:
        return _OwnedRuntimePublicMediaFetcher(
            media_runtime,
            PinnedPublicMediaFetcher(
                PublicFetchPolicy(_system_getaddrinfo),
                media_policy,
                http_library=http_library,
            ),
        )

    return ServerMediaRuntimeResources(
        runtime=media_runtime,
        media_store_factory=media_store_factory,
        public_media_fetcher_factory=public_media_fetcher_factory,
    )


def build_image_generation_host_configuration(
    configuration: ServerConfiguration,
    *,
    session_factory: Callable[[], Session],
    s3_client_factory: S3ClientFactory | None,
    shared_media_resources: ServerMediaRuntimeResources | None = None,
) -> ImageGenerationHostConfiguration:
    """Build image.single dependencies without opening HTTP or S3 resources."""

    if not isinstance(configuration, ServerConfiguration):
        raise ImageGenerationHostConfigurationError(
            "an explicit server configuration is required"
        )
    image_single_enabled = configuration.image_single_enabled
    image_batch_enabled = configuration.image_batch_enabled
    video_single_enabled = configuration.video_single_enabled
    if not image_single_enabled and not image_batch_enabled and not video_single_enabled:
        return ImageGenerationHostConfiguration(None, None, None)

    enabled_credentials = tuple(
        path
        for enabled, path in (
            (image_single_enabled, configuration.image_single_credentials_file),
            (image_batch_enabled, configuration.image_batch_credentials_file),
            (video_single_enabled, configuration.video_single_credentials_file),
        )
        if enabled
    )
    credentials_path = enabled_credentials[0] if enabled_credentials else None
    r2_configuration = configuration.r2_configuration
    if (
        credentials_path is None
        or not isinstance(credentials_path, Path)
        or not credentials_path.is_absolute()
        or r2_configuration is None
        or configuration.r2_access_key is None
        or configuration.r2_secret_key is None
        or not isinstance(s3_client_factory, Boto3S3ClientFactory)
        or not callable(session_factory)
    ):
        raise ImageGenerationHostConfigurationError(
            "enabled media workers require credentials and object storage"
        )
    if any(path != credentials_path for path in enabled_credentials[1:]):
        raise ImageGenerationHostConfigurationError(
            "enabled media workers must use the same credentials file"
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
    owns_media_resources = shared_media_resources is None
    media_resources = shared_media_resources or build_server_media_runtime_resources(
        configuration,
        s3_client_factory=s3_client_factory,
        include_video=video_single_enabled,
    )
    media_runtime = media_resources.runtime
    try:
        runtime = GenerationRuntimeConfig(
            session_factory=session_factory,
            db_clock=db_clock,
            id_factory=id_factory,
            http_client_factory=AiohttpAsyncHttpClientFactory(),
            credential_resolver=registry.resolve_original_credential,
            provider_factories=(),
            media_store_factory=media_resources.media_store_factory,
            local_muse_staging_dir=None,
            public_media_fetcher_factory=media_resources.public_media_fetcher_factory,
            bound_provider_content_fetcher_factory=_UnsupportedBoundContentFetcher,
            local_muse_capability_factory=_UnsupportedLocalMuseCapability,
            r2_storage_factory=_UnsupportedR2StorageCapability,
            timeout_settings=RuntimeTimeoutSettings(
                public_media_fetch_seconds=MAX_TRANSFER_SECONDS
            ),
            concurrency_settings=RuntimeConcurrencySettings(public_media_fetches=2),
        )
        single_worker = (
            GenerationWorkerConfiguration(runtime, capabilities)
            if image_single_enabled
            else None
        )
        batch_worker = (
            ImageBatchWorkerConfiguration(runtime, capabilities)
            if image_batch_enabled
            else None
        )
        video_worker = (
            VideoSingleWorkerConfiguration(runtime, capabilities)
            if video_single_enabled
            else None
        )
        admission = ChatMediaAdmissionConfiguration(
            registry_capabilities=capabilities,
            server_id_factory=id_factory,
            policies={
                "image.single": AdmissionPolicy(
                    max_concurrent_tasks=configuration.user_task_max_concurrency
                ),
                "video.single": AdmissionPolicy(
                    max_concurrent_tasks=configuration.user_task_max_concurrency
                ),
                "image.batch": AdmissionPolicy(
                    max_concurrent_tasks=configuration.user_task_max_concurrency
                ),
            },
            versions=SnapshotVersions(
                projector="server-image-single-projector-v1",
                reference_policy="server-image-single-reference-policy-v1",
            ),
            enabled_operations=frozenset(
                operation
                for operation, enabled in (
                    ("image.single", image_single_enabled),
                    ("image.batch", image_batch_enabled),
                    ("video.single", video_single_enabled),
                )
                if enabled
            ),
            image_references_enabled=True,
        )
    except BaseException:
        if owns_media_resources:
            media_runtime.close()
        raise

    return ImageGenerationHostConfiguration(
        admission_configuration=admission,
        generation_worker_configuration=single_worker,
        media_runtime=media_runtime,
        image_batch_worker_configuration=batch_worker,
        video_single_worker_configuration=video_worker,
    )


def _image_media_policy(*, include_video: bool = False) -> SafeMediaPolicy:
    image_rules = (
        MediaTypeRule("image/png", ".png"),
        MediaTypeRule("image/jpeg", ".jpg"),
        MediaTypeRule("image/gif", ".gif"),
        MediaTypeRule("image/bmp", ".bmp"),
        MediaTypeRule("image/webp", ".webp"),
    )
    video_rules = (
        MediaTypeRule("video/mp4", ".mp4"),
        MediaTypeRule("video/webm", ".webm"),
        MediaTypeRule("video/quicktime", ".mov"),
        MediaTypeRule("video/x-m4v", ".m4v"),
        MediaTypeRule("video/x-msvideo", ".avi"),
        MediaTypeRule("video/x-matroska", ".mkv"),
    )
    return SafeMediaPolicy(image_rules + (video_rules if include_video else ()))


def _build_http_library() -> AiohttpMatureHttpLibrary:
    try:
        import aiohttp
        from yarl import URL
    except ImportError:
        raise ImageGenerationHostConfigurationError(
            "image.single HTTP transport is unavailable"
        ) from None

    return AiohttpMatureHttpLibrary(
        url_factory=URL,
        connector_factory=aiohttp.TCPConnector,
        session_factory=aiohttp.ClientSession,
        timeout_factory=aiohttp.ClientTimeout,
        cookie_jar_factory=aiohttp.DummyCookieJar,
    )


async def _system_getaddrinfo(
    host: str,
    port: int,
    *,
    family: int,
    type: int,
) -> Sequence[object]:
    loop = asyncio.get_running_loop()
    return await loop.getaddrinfo(host, port, family=family, type=type)


def _managed_origin_map(configuration: R2Configuration) -> dict[str, str]:
    parsed = urlsplit(configuration.public_base_url)
    public_origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    origins = {old: new for old, new in configuration.managed_origin_map}
    origins[public_origin] = public_origin
    return origins


class _UnsupportedBoundContentFetcher(BoundProviderContentFetcher):
    async def fetch_bound_content(
        self,
        external_ref: ExternalOperationRef,
        capability: ProviderContentCapability,
    ) -> PreparedReference | TransferFailure:
        del external_ref, capability
        raise RuntimeError("bound provider content is not configured for image.single")


class _OwnedRuntimePublicMediaFetcher(PublicMediaFetcher):
    """Run pinned fetches on the host-owned loop under its shared media budget."""

    def __init__(
        self,
        runtime: OwnedMediaRuntime,
        fetcher: PinnedPublicMediaFetcher,
    ) -> None:
        self._runtime = runtime
        self._fetcher = fetcher

    async def fetch_public(
        self,
        reference: ReferenceInput,
        *,
        max_bytes: int,
        timeout_seconds: float,
    ) -> PreparedReference | TransferFailure:
        limits_failure = _validate_public_fetch_limits(max_bytes, timeout_seconds)
        if limits_failure is not None:
            return limits_failure

        async def fetch_on_owned_loop(
            services: OwnedMediaServices,
        ) -> PreparedReference | TransferFailure:
            try:
                async with asyncio.timeout(timeout_seconds):
                    async with services.shared_slots:
                        return await self._fetcher.fetch_public(
                            reference,
                            max_bytes=max_bytes,
                            timeout_seconds=timeout_seconds,
                        )
            except TimeoutError:
                return TransferFailure("timeout", "公网媒体获取超时")

        call = asyncio.create_task(
            asyncio.to_thread(self._runtime.run, fetch_on_owned_loop)
        )
        try:
            return await asyncio.shield(call)
        except asyncio.CancelledError as cancellation:
            await _wait_for_owned_fetch(call)
            raise cancellation
        except Exception:
            return TransferFailure("transport_unavailable", "公网媒体安全能力不可用")


def _validate_public_fetch_limits(
    max_bytes: int,
    timeout_seconds: float,
) -> TransferFailure | None:
    if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_TRANSFER_BYTES:
        return TransferFailure("invalid_limits", "公网媒体限制无效")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not 0 < timeout_seconds <= MAX_TRANSFER_SECONDS
    ):
        return TransferFailure("invalid_limits", "公网媒体限制无效")
    return None


async def _wait_for_owned_fetch(call: asyncio.Task[object]) -> None:
    while not call.done():
        try:
            await asyncio.shield(call)
        except asyncio.CancelledError:
            continue
        except BaseException:
            break
    try:
        call.result()
    except BaseException:
        pass


class _UnsupportedLocalMuseCapability(LocalMuseCapability):
    def stage_local_reference(
        self,
        reference: ReferenceInput,
        staging_directory: Path,
    ) -> PreparedReference | TransferFailure:
        del reference, staging_directory
        raise RuntimeError("local staging is not configured for image.single")


class _UnsupportedR2StorageCapability(R2StorageCapability):
    def store_prepared(
        self,
        reference: PreparedReference,
    ) -> StoredMedia | TransferFailure:
        del reference
        raise RuntimeError("direct R2 storage is not configured for image.single")

    def release(
        self,
        stored: StoredMedia,
    ) -> Literal["released", "already_released", "not_owned", "failed"]:
        del stored
        raise RuntimeError("direct R2 storage is not configured for image.single")


__all__ = (
    "ImageGenerationHostConfiguration",
    "ImageGenerationHostConfigurationError",
    "build_image_generation_host_configuration",
)
