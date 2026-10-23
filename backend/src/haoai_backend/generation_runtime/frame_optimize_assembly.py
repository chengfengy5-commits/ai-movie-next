"""Explicit opt-in assembly for the registered frame.optimize worker."""

from __future__ import annotations

from dataclasses import dataclass

from haoai_backend.generation_contracts.ports import GenerationRuntimeConfig, InertGenerationRuntime
from haoai_backend.generation_contracts.types import (
    ClaimAuthority,
    ContinuationClaimAuthority,
)
from haoai_backend.generation_core.frame_optimize_application import (
    SqlAlchemyFrameOptimizeApplications,
)
from haoai_backend.generation_core.frame_optimize_cancellation import (
    SqlAlchemyFrameOptimizeCancellation,
)
from haoai_backend.generation_core.frame_optimize_claims import (
    SqlAlchemyFrameOptimizeClaims,
)
from haoai_backend.generation_core.frame_optimize_continuation_claims import (
    SqlAlchemyFrameOptimizeContinuationClaims,
)
from haoai_backend.generation_core.frame_optimize_fees import SqlAlchemyFrameOptimizeFees
from haoai_backend.generation_core.frame_optimize_finalization import (
    SqlAlchemyFrameOptimizeFinalization,
)
from haoai_backend.generation_core.frame_optimize_recovery import (
    SqlAlchemyFrameOptimizeRecovery,
)
from haoai_backend.generation_core.frame_optimize_results import (
    SqlAlchemyFrameOptimizeResults,
)
from haoai_backend.generation_credentials.application import RegistryCapabilities
from haoai_backend.generation_credentials.domain import (
    CredentialCrypto,
    HmacProofAuthority,
    RootEnrollmentSource,
)

from .configuration import RegisteredExecutorFactory
from .frame_optimize_executor import FrameOptimizeExecutor
from .runtime import build_runtime


@dataclass(frozen=True, slots=True)
class FrameOptimizeWorkerConfiguration:
    """Dependencies for the server-owned worker; disabled unless explicitly enabled."""

    runtime: GenerationRuntimeConfig
    registry_capabilities: RegistryCapabilities
    max_user_concurrency: int = 0
    enabled: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.runtime, GenerationRuntimeConfig):
            raise TypeError("runtime must be an explicit GenerationRuntimeConfig")
        if not isinstance(self.registry_capabilities, RegistryCapabilities):
            raise TypeError("registry_capabilities must be explicit")
        if type(self.max_user_concurrency) is not int or self.max_user_concurrency < 0:
            raise ValueError("max_user_concurrency must be a non-negative integer")
        if type(self.enabled) is not bool:
            raise TypeError("enabled must be an explicit boolean")
        capabilities = self.registry_capabilities
        if (
            capabilities.read_session_factory is not self.runtime.session_factory
            or capabilities.db_clock is not self.runtime.db_clock
            or capabilities.id_factory is not self.runtime.id_factory
        ):
            raise ValueError("optimize runtime and registry must share Session, clock, and ID factories")


def build_frame_optimize_worker_runtime(
    configuration: FrameOptimizeWorkerConfiguration,
) -> InertGenerationRuntime:
    """Return an inert runtime until the dedicated worker assembly is installed."""

    if not isinstance(configuration, FrameOptimizeWorkerConfiguration):
        raise TypeError("configuration must be FrameOptimizeWorkerConfiguration")
    if (
        not configuration.enabled
        or not _registry_shape_ready(configuration.registry_capabilities)
    ):
        return build_runtime(configuration.runtime, ())

    runtime = configuration.runtime
    capabilities = configuration.registry_capabilities
    crypto = capabilities.crypto
    proof_authority = capabilities.proof_authority
    assert type(crypto) is CredentialCrypto
    assert type(proof_authority) is HmacProofAuthority
    claims = SqlAlchemyFrameOptimizeClaims(
        runtime.session_factory,
        runtime.db_clock,
        crypto,
        proof_authority,
        runtime.id_factory,
    )
    continuation_claims = SqlAlchemyFrameOptimizeContinuationClaims(
        runtime.session_factory,
        runtime.db_clock,
        crypto,
        proof_authority,
        runtime.id_factory,
    )
    cancellation = SqlAlchemyFrameOptimizeCancellation(
        runtime.session_factory,
        runtime.db_clock,
        runtime.id_factory,
        crypto,
        proof_authority,
    )
    recovery = SqlAlchemyFrameOptimizeRecovery(
        runtime.session_factory,
        runtime.db_clock,
        runtime.id_factory,
        crypto,
        proof_authority,
        cancellation,
    )
    results = SqlAlchemyFrameOptimizeResults(
        runtime.session_factory,
        runtime.db_clock,
        crypto,
        proof_authority,
        runtime.id_factory,
    )
    fees = SqlAlchemyFrameOptimizeFees(
        runtime.session_factory,
        runtime.db_clock,
        crypto,
        proof_authority,
        continuation_claims,
        runtime.id_factory,
    )
    applications = SqlAlchemyFrameOptimizeApplications(
        runtime.session_factory,
        runtime.db_clock,
        crypto,
        proof_authority,
        continuation_claims,
    )
    finalization = SqlAlchemyFrameOptimizeFinalization(
        runtime.session_factory,
        runtime.db_clock,
        crypto,
        proof_authority,
        continuation_claims,
    )

    factory = RegisteredExecutorFactory(
        task_type="batch-optimize",
        execution_port_factory=lambda: _FrameOptimizeExecutionPort(
            claims, continuation_claims
        ),
        executor_factory=lambda: FrameOptimizeExecutor(
            runtime,
            capabilities,
            recovery,
            continuation_claims,
            fees,
            applications,
            finalization,
        ),
        recovery_factory=lambda: recovery,
    )
    return build_runtime(runtime, (factory,))


def _registry_shape_ready(capabilities: RegistryCapabilities) -> bool:
    return (
        type(capabilities.crypto) is CredentialCrypto
        and type(capabilities.proof_authority) is HmacProofAuthority
        and type(capabilities.enrollment_source) is RootEnrollmentSource
        and capabilities.enrollment_source.ready
        and callable(capabilities.read_session_factory)
        and callable(capabilities.db_clock)
        and callable(capabilities.id_factory)
    )


@dataclass(slots=True)
class _FrameOptimizeExecutionPort:
    generation: SqlAlchemyFrameOptimizeClaims
    continuations: SqlAlchemyFrameOptimizeContinuationClaims

    @property
    def lease_seconds(self) -> int:
        return self.generation.lease_seconds

    def claim_next(self, task_type: str, instance: str) -> ClaimAuthority | None:
        return self.generation.claim_next(task_type, instance)

    def renew_or_revoke(self, claim: ClaimAuthority) -> str:
        return self.generation.renew_or_revoke(claim)

    def release_claim(self, claim: ClaimAuthority) -> bool:
        return self.generation.release_claim(claim)

    def claim_continuation(
        self, task_type: str, instance: str, lease_seconds: int
    ) -> ContinuationClaimAuthority | None:
        return self.continuations.claim_continuation(task_type, instance, lease_seconds)

    def renew_continuation(
        self, claim: ContinuationClaimAuthority
    ) -> ContinuationClaimAuthority | None:
        return self.continuations.renew_continuation(claim)

    def release_continuation(self, claim: ContinuationClaimAuthority) -> bool:
        return self.continuations.release_continuation(claim)


__all__ = (
    "FrameOptimizeWorkerConfiguration",
    "build_frame_optimize_worker_runtime",
)
