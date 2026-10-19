"""Explicit, lazy assembly for the opt-in frame.review worker."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Event

from haoai_backend.generation_contracts.ports import (
    GenerationRuntimeConfig,
    InertGenerationRuntime,
)
from haoai_backend.generation_core.frame_review_claims import (
    SqlAlchemyFrameReviewClaims,
)
from haoai_backend.generation_core.frame_review_continuation import (
    SqlAlchemyFrameReviewContinuationConsumer,
)
from haoai_backend.generation_core.frame_review_continuation_claims import (
    SqlAlchemyFrameReviewContinuationClaims,
)
from haoai_backend.generation_core.frame_review_recovery import (
    SqlAlchemyFrameReviewRecovery,
)
from haoai_backend.generation_core.frame_review_cancellation import (
    SqlAlchemyFrameReviewCancellation,
)
from haoai_backend.generation_credentials.application import RegistryCapabilities
from haoai_backend.generation_credentials.domain import CredentialCrypto, HmacProofAuthority
from haoai_backend.generation_contracts.types import ClaimAuthority, ContinuationClaimAuthority

from .configuration import RegisteredExecutorFactory
from .frame_review_executor import FrameReviewExecutor
from .runtime import build_runtime


@dataclass(frozen=True, slots=True)
class FrameReviewWorkerConfiguration:
    """Dependencies required to opt in to the server-owned frame.review worker."""

    runtime: GenerationRuntimeConfig
    registry_capabilities: RegistryCapabilities

    def __post_init__(self) -> None:
        if not isinstance(self.runtime, GenerationRuntimeConfig):
            raise TypeError("runtime must be an explicit GenerationRuntimeConfig")
        if not isinstance(self.registry_capabilities, RegistryCapabilities):
            raise TypeError("registry_capabilities must be explicit")


@dataclass(slots=True)
class _FrameReviewExecutionPort:
    generation: SqlAlchemyFrameReviewClaims
    continuations: SqlAlchemyFrameReviewContinuationClaims
    consumer: SqlAlchemyFrameReviewContinuationConsumer

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

    def consume_continuation(
        self,
        claim: ContinuationClaimAuthority,
        *,
        local_stop_signal: Event | None = None,
    ) -> str:
        return self.consumer.consume(claim, local_stop_signal=local_stop_signal)


def build_frame_review_worker_runtime(
    configuration: FrameReviewWorkerConfiguration,
) -> InertGenerationRuntime:
    """Register only ai-review and invoke no dependency until execution starts."""

    if not isinstance(configuration, FrameReviewWorkerConfiguration):
        raise TypeError("configuration must be FrameReviewWorkerConfiguration")
    runtime = configuration.runtime
    capabilities = configuration.registry_capabilities
    if not _review_registry_ready(capabilities):
        return build_runtime(runtime, ())

    recovery: SqlAlchemyFrameReviewRecovery | None = None

    def get_recovery() -> SqlAlchemyFrameReviewRecovery:
        nonlocal recovery
        if recovery is None:
            recovery = _build_review_recovery(runtime, capabilities)
        return recovery

    factory = RegisteredExecutorFactory(
        task_type="ai-review",
        execution_port_factory=lambda: _build_review_execution_port(
            runtime, capabilities
        ),
        executor_factory=lambda: FrameReviewExecutor(
            runtime, capabilities, get_recovery()
        ),
        recovery_factory=get_recovery,
    )
    return build_runtime(runtime, (factory,))


def _review_registry_ready(capabilities: RegistryCapabilities) -> bool:
    return (
        isinstance(capabilities.crypto, CredentialCrypto)
        and isinstance(capabilities.proof_authority, HmacProofAuthority)
        and callable(capabilities.read_session_factory)
        and callable(capabilities.db_clock)
        and callable(capabilities.id_factory)
    )


def _build_review_execution_port(runtime, capabilities) -> _FrameReviewExecutionPort:
    generation = SqlAlchemyFrameReviewClaims(
        runtime.session_factory,
        runtime.db_clock,
        runtime.id_factory,
    )
    continuations = SqlAlchemyFrameReviewContinuationClaims(
        runtime.session_factory,
        runtime.db_clock,
        runtime.id_factory,
    )
    consumer = SqlAlchemyFrameReviewContinuationConsumer(
        runtime.session_factory,
        runtime.db_clock,
        runtime.id_factory,
        capabilities.crypto,
        capabilities.proof_authority,
        continuations,
    )
    return _FrameReviewExecutionPort(generation, continuations, consumer)


def _build_review_recovery(runtime, capabilities) -> SqlAlchemyFrameReviewRecovery:
    cancellation = SqlAlchemyFrameReviewCancellation(
        session_factory=runtime.session_factory,
        db_clock=runtime.db_clock,
        id_factory=runtime.id_factory,
        crypto=capabilities.crypto,
        proof_authority=capabilities.proof_authority,
    )
    return SqlAlchemyFrameReviewRecovery(
        session_factory=runtime.session_factory,
        db_clock=runtime.db_clock,
        id_factory=runtime.id_factory,
        crypto=capabilities.crypto,
        proof_authority=capabilities.proof_authority,
        cancellation=cancellation,
    )


__all__ = (
    "FrameReviewWorkerConfiguration",
    "build_frame_review_worker_runtime",
)
