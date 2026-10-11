"""Resource-free runtime composition boundary for generation workers."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Literal

from haoai_backend.generation_contracts.ports import (
    GenerationRuntimeConfig,
    InertGenerationRuntime,
    WorkerExecutionOutcome,
)
from haoai_backend.generation_contracts.types import (
    OwnerTaskScope,
    ShutdownReceipt,
    TextEvent,
    WebStreamAuthority,
    WebStreamCommand,
)

from .configuration import ExecutorFactory, validate_runtime_configuration


@dataclass(slots=True)
class _InertRuntime:
    _config: GenerationRuntimeConfig
    _executor_factories: tuple[ExecutorFactory, ...]

    def run_claimed_once(self, task_type: str) -> WorkerExecutionOutcome:
        """Remain pending until a reviewed claim-acquisition adapter is installed."""

        del task_type
        return "pending"

    async def run_web_stream(
        self,
        command: WebStreamCommand,
        authority: WebStreamAuthority,
    ) -> AsyncIterator[TextEvent]:
        """Report an unavailable executor through the existing error event shape."""

        del command, authority
        yield TextEvent(
            kind="text_error",
            safe_error="Generation execution is not configured.",
        )

    def consume_cancel_and_due(
        self, scope: OwnerTaskScope
    ) -> Literal["settled", "retained", "pending", "terminal_projection"]:
        """Keep business settlement pending until its owner adapter is installed."""

        del scope
        return "pending"

    def shutdown(self, reason: str) -> ShutdownReceipt:
        """Return bounded operational evidence; this inert object owns no resources."""

        if not reason:
            raise ValueError("shutdown reason must be non-empty")
        return ShutdownReceipt(
            reason=reason,
            released_resources=(),
            unreleased_resources=(),
            revoked_claim_refs=(),
            recovery_plan_refs=(),
            timed_out=False,
        )


def build_runtime(
    explicit_config: GenerationRuntimeConfig,
    executor_factories: Sequence[ExecutorFactory],
) -> InertGenerationRuntime:
    """Build a lazy runtime without invoking any dependency or executor factory."""

    factories = validate_runtime_configuration(explicit_config, executor_factories)
    return _InertRuntime(explicit_config, factories)


__all__ = ("build_runtime",)
