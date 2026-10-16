"""Explicit server-owned configuration for the cold generation worker runtime."""

from __future__ import annotations

from dataclasses import dataclass

from haoai_backend.generation_contracts.ports import GenerationRuntimeConfig, InertGenerationRuntime
from haoai_backend.generation_credentials.application import RegistryCapabilities
from haoai_backend.generation_credentials.domain import CredentialCrypto, HmacProofAuthority
from haoai_backend.generation_core.claims import SqlAlchemyImageSingleClaims

from .configuration import RegisteredExecutorFactory
from .image_single import ImageSingleExecutor
from .runtime import build_runtime


@dataclass(frozen=True, slots=True)
class GenerationWorkerConfiguration:
    """Internal dependency injection for one registered image.single worker."""

    runtime: GenerationRuntimeConfig
    registry_capabilities: RegistryCapabilities

    def __post_init__(self) -> None:
        if not isinstance(self.runtime, GenerationRuntimeConfig):
            raise TypeError("runtime must be an explicit GenerationRuntimeConfig")
        if not isinstance(self.registry_capabilities, RegistryCapabilities):
            raise TypeError("registry_capabilities must be explicit")


def build_generation_worker_runtime(
    configuration: GenerationWorkerConfiguration,
) -> InertGenerationRuntime:
    """Assemble only the image.single route without acquiring runtime resources."""

    if not isinstance(configuration, GenerationWorkerConfiguration):
        raise TypeError("configuration must be GenerationWorkerConfiguration")
    dependencies = configuration.runtime
    if not _image_single_registry_ready(configuration.registry_capabilities):
        return build_runtime(dependencies, ())

    factory = RegisteredExecutorFactory(
        task_type="image-single",
        execution_port_factory=lambda: SqlAlchemyImageSingleClaims(
            dependencies.session_factory,
            dependencies.db_clock,
            dependencies.id_factory,
        ),
        executor_factory=lambda: ImageSingleExecutor(
            dependencies,
            configuration.registry_capabilities,
        ),
    )
    return build_runtime(dependencies, (factory,))


def _image_single_registry_ready(capabilities: RegistryCapabilities) -> bool:
    return (
        isinstance(capabilities.crypto, CredentialCrypto)
        and isinstance(capabilities.proof_authority, HmacProofAuthority)
        and callable(capabilities.read_session_factory)
        and callable(capabilities.db_clock)
        and callable(capabilities.id_factory)
    )


__all__ = (
    "GenerationWorkerConfiguration",
    "build_generation_worker_runtime",
)
