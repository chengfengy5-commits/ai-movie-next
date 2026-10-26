"""Resource-free composition for the ordinary video.single worker."""

from __future__ import annotations

from dataclasses import dataclass

from haoai_backend.generation_contracts.ports import (
    GenerationRuntimeConfig,
    InertGenerationRuntime,
)
from haoai_backend.generation_core.video_single_recovery import (
    SqlAlchemyVideoSingleRecovery,
)
from haoai_backend.generation_credentials.application import RegistryCapabilities
from haoai_backend.generation_credentials.domain import (
    CredentialCrypto,
    HmacProofAuthority,
)
from haoai_backend.generation_core.video_single_claims import (
    SqlAlchemyVideoSingleClaims,
)
from haoai_backend.generation_core.video_single_results import (
    SqlAlchemyVideoSingleResults,
)

from .configuration import RegisteredExecutorFactory
from .runtime import build_runtime
from .video_single_executor import PollWaiter, VideoSingleExecutor, _wait_for_stop


@dataclass(frozen=True, slots=True)
class VideoSingleWorkerConfiguration:
    """Explicit dependencies for a cold video.single runtime."""

    runtime: GenerationRuntimeConfig
    registry_capabilities: RegistryCapabilities
    poll_waiter: PollWaiter = _wait_for_stop

    def __post_init__(self) -> None:
        if not isinstance(self.runtime, GenerationRuntimeConfig):
            raise TypeError("runtime must be an explicit GenerationRuntimeConfig")
        if not isinstance(self.registry_capabilities, RegistryCapabilities):
            raise TypeError("registry_capabilities must be explicit")
        if not callable(self.poll_waiter):
            raise TypeError("poll_waiter must be callable")


def build_video_single_worker_runtime(
    configuration: VideoSingleWorkerConfiguration,
) -> InertGenerationRuntime:
    """Register only video-single; factories stay lazy until a worker claims work."""

    if not isinstance(configuration, VideoSingleWorkerConfiguration):
        raise TypeError("configuration must be VideoSingleWorkerConfiguration")
    dependencies = configuration.runtime
    if not _video_single_registry_ready(configuration.registry_capabilities):
        return build_runtime(dependencies, ())

    def results() -> SqlAlchemyVideoSingleResults:
        return SqlAlchemyVideoSingleResults(
            dependencies.session_factory,
            dependencies.db_clock,
            dependencies.id_factory,
        )

    factory = RegisteredExecutorFactory(
        task_type="video-single",
        execution_port_factory=lambda: SqlAlchemyVideoSingleClaims(
            dependencies.session_factory,
            dependencies.db_clock,
            dependencies.id_factory,
        ),
        executor_factory=lambda: VideoSingleExecutor(
            dependencies,
            configuration.registry_capabilities,
            poll_waiter=configuration.poll_waiter,
        ),
        recovery_factory=lambda: SqlAlchemyVideoSingleRecovery(
            dependencies.session_factory,
            dependencies.db_clock,
            results(),
        ),
    )
    return build_runtime(dependencies, (factory,))


def _video_single_registry_ready(capabilities: RegistryCapabilities) -> bool:
    return (
        isinstance(capabilities.crypto, CredentialCrypto)
        and isinstance(capabilities.proof_authority, HmacProofAuthority)
        and callable(capabilities.read_session_factory)
        and callable(capabilities.db_clock)
        and callable(capabilities.id_factory)
    )


__all__ = (
    "VideoSingleWorkerConfiguration",
    "build_video_single_worker_runtime",
)
