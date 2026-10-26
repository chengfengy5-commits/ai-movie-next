"""Explicit, lazy assembly for the opt-in image.batch worker runtime."""

from __future__ import annotations

from dataclasses import dataclass

from haoai_backend.generation_contracts.ports import (
    GenerationRuntimeConfig,
    InertGenerationRuntime,
)
from haoai_backend.generation_core.image_batch_claims import SqlAlchemyImageBatchClaims
from haoai_backend.generation_core.image_batch_recovery import SqlAlchemyImageBatchRecovery
from haoai_backend.generation_credentials.application import RegistryCapabilities
from haoai_backend.generation_credentials.domain import CredentialCrypto, HmacProofAuthority

from .configuration import RegisteredExecutorFactory
from .image_batch_executor import ImageBatchExecutor
from .runtime import build_runtime


@dataclass(frozen=True, slots=True)
class ImageBatchWorkerConfiguration:
    """Dependencies required to opt into the server-owned image.batch runtime."""

    runtime: GenerationRuntimeConfig
    registry_capabilities: RegistryCapabilities

    def __post_init__(self) -> None:
        if not isinstance(self.runtime, GenerationRuntimeConfig):
            raise TypeError("runtime must be an explicit GenerationRuntimeConfig")
        if not isinstance(self.registry_capabilities, RegistryCapabilities):
            raise TypeError("registry_capabilities must be explicit")


def build_image_batch_worker_runtime(
    configuration: ImageBatchWorkerConfiguration,
) -> InertGenerationRuntime:
    """Register image.batch without calling any configured resource factory."""

    if not isinstance(configuration, ImageBatchWorkerConfiguration):
        raise TypeError("configuration must be ImageBatchWorkerConfiguration")
    runtime = configuration.runtime
    capabilities = configuration.registry_capabilities
    if not _batch_registry_ready(capabilities):
        return build_runtime(runtime, ())

    factory = RegisteredExecutorFactory(
        task_type="batch-image",
        execution_port_factory=lambda: SqlAlchemyImageBatchClaims(
            runtime.session_factory,
            runtime.db_clock,
            runtime.id_factory,
        ),
        executor_factory=lambda: ImageBatchExecutor(
            runtime,
            capabilities,
        ),
        recovery_factory=lambda: SqlAlchemyImageBatchRecovery(
            runtime.session_factory,
            runtime.db_clock,
            _build_batch_results(runtime),
        ),
    )
    return build_runtime(runtime, (factory,))


def _batch_registry_ready(capabilities: RegistryCapabilities) -> bool:
    return (
        isinstance(capabilities.crypto, CredentialCrypto)
        and isinstance(capabilities.proof_authority, HmacProofAuthority)
        and callable(capabilities.read_session_factory)
        and callable(capabilities.db_clock)
        and callable(capabilities.id_factory)
    )


def _build_batch_results(runtime: GenerationRuntimeConfig):
    from haoai_backend.generation_core.image_batch_results import SqlAlchemyImageBatchResults

    return SqlAlchemyImageBatchResults(
        runtime.session_factory,
        runtime.db_clock,
        runtime.id_factory,
    )


__all__ = (
    "ImageBatchWorkerConfiguration",
    "build_image_batch_worker_runtime",
)
