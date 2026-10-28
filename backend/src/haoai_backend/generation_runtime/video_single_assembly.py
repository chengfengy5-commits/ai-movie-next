"""Resource-free composition for the ordinary video.single worker."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock

from haoai_backend.generation_contracts.ports import (
    GenerationRuntimeConfig,
    InertGenerationRuntime,
)
from haoai_backend.generation_contracts.types import ClaimAuthority
from haoai_backend.generation_core.haoai_group.cancellation import (
    SqlAlchemyHaoaiGroupCancellation,
)
from haoai_backend.generation_core.haoai_group.late import (
    SqlAlchemyHaoaiGroupLateRecovery,
)
from haoai_backend.generation_core.haoai_group.recovery import (
    SqlAlchemyHaoaiGroupRecovery,
)
from haoai_backend.generation_core.haoai_group.results import (
    SqlAlchemyHaoaiGroupResults,
)
from haoai_backend.generation_core.haoai_group.settlement import (
    SqlAlchemyHaoaiGroupEventSettler,
)
from haoai_backend.generation_core.video_single_recovery import (
    SqlAlchemyVideoSingleRecovery,
)
from haoai_backend.generation_credentials.application import RegistryCapabilities
from haoai_backend.generation_credentials.application import build_credential_registry
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
from haoai_backend.generation_core.haoai_group.claims import (
    SqlAlchemyHaoaiGroupClaims,
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


class _VideoSingleClaimDispatcher:
    """Fairly route the shared worker to ordinary and HaoAI Group claim graphs."""

    def __init__(self, ordinary: object, group: object) -> None:
        self._ordinary = ordinary
        self._group = group
        self._prefer_group = True
        self._routes: dict[str, tuple[ClaimAuthority, object]] = {}
        self._lock = RLock()

    def claim_next(self, task_type: str, instance: str) -> ClaimAuthority | None:
        with self._lock:
            preferred = self._group if self._prefer_group else self._ordinary
            secondary = self._ordinary if self._prefer_group else self._group
            self._prefer_group = not self._prefer_group

        for adapter in (preferred, secondary):
            try:
                claim = adapter.claim_next(task_type, instance)
            except Exception:
                continue
            if not isinstance(claim, ClaimAuthority):
                continue
            with self._lock:
                if claim.token not in self._routes:
                    self._routes[claim.token] = (claim, adapter)
                    return claim
            try:
                adapter.release_claim(claim)
            except Exception:
                pass
        return None

    def renew_or_revoke(self, claim: ClaimAuthority) -> str:
        adapter = self._adapter_for(claim)
        if adapter is None:
            return "ownership_lost"
        try:
            return adapter.renew_or_revoke(claim)
        except Exception:
            return "ownership_lost"

    def release_claim(self, claim: ClaimAuthority) -> bool:
        adapter = self._adapter_for(claim)
        if adapter is None:
            return False
        try:
            return bool(adapter.release_claim(claim))
        except Exception:
            return False
        finally:
            with self._lock:
                current = self._routes.get(claim.token)
                if current is not None and current[0] is claim:
                    self._routes.pop(claim.token, None)

    def is_group_claim(self, claim: ClaimAuthority) -> bool:
        return self._adapter_for(claim) is self._group

    def _adapter_for(self, claim: ClaimAuthority) -> object | None:
        if not isinstance(claim, ClaimAuthority):
            return None
        with self._lock:
            route = self._routes.get(claim.token)
            if route is None or route[0] is not claim:
                return None
            return route[1]


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

    def group_cancellation() -> SqlAlchemyHaoaiGroupCancellation:
        return SqlAlchemyHaoaiGroupCancellation(
            session_factory=dependencies.session_factory,
            db_clock=dependencies.db_clock,
            proof_authority=group_proof_authority(),
            id_factory=dependencies.id_factory,
        )

    def group_proof_authority() -> HmacProofAuthority:
        proof_authority = configuration.registry_capabilities.proof_authority
        if not isinstance(proof_authority, HmacProofAuthority):
            raise TypeError("HaoAI Group recovery requires its proof authority")
        return proof_authority

    def group_recovery() -> SqlAlchemyHaoaiGroupRecovery:
        proof_authority = group_proof_authority()
        late_executor = VideoSingleExecutor(
            dependencies,
            configuration.registry_capabilities,
            poll_waiter=configuration.poll_waiter,
        )
        late_recovery = SqlAlchemyHaoaiGroupLateRecovery(
            session_factory=dependencies.session_factory,
            db_clock=dependencies.db_clock,
            registry=build_credential_registry(configuration.registry_capabilities),
            crypto=configuration.registry_capabilities.crypto,
            proof_authority=proof_authority,
            provider_factory=lambda candidate, guard: late_executor._build_provider(
                candidate.verified_input,
                candidate.intent,
                guard,
            ),
            media_store_factory=dependencies.media_store_factory,
            id_factory=dependencies.id_factory,
        )
        return SqlAlchemyHaoaiGroupRecovery(
            session_factory=dependencies.session_factory,
            db_clock=dependencies.db_clock,
            cancellation=group_cancellation(),
            event_settler=SqlAlchemyHaoaiGroupEventSettler(
                dependencies.session_factory,
                dependencies.db_clock,
                proof_authority,
                dependencies.id_factory,
            ),
            results=SqlAlchemyHaoaiGroupResults(
                dependencies.session_factory,
                dependencies.db_clock,
                proof_authority,
                dependencies.id_factory,
            ),
            late_recoverer=late_recovery.recover,
            page_size=16,
        )

    factory = RegisteredExecutorFactory(
        task_type="video-single",
        execution_port_factory=lambda: _VideoSingleClaimDispatcher(
            SqlAlchemyVideoSingleClaims(
                dependencies.session_factory,
                dependencies.db_clock,
                dependencies.id_factory,
            ),
            SqlAlchemyHaoaiGroupClaims(
                dependencies.session_factory,
                dependencies.db_clock,
                dependencies.id_factory,
                proof_authority=configuration.registry_capabilities.proof_authority,
            ),
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
            group_cancellation=group_cancellation(),
            group_recovery=group_recovery(),
            page_size=16,
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
