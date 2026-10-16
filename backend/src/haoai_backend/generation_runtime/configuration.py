"""Validation for explicit, lazy generation runtime dependencies."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TypeAlias

from haoai_backend.generation_contracts.ports import (
    GenerationExecutionPort,
    GenerationExecutor,
    GenerationRuntimeConfig,
)


ExecutorFactory: TypeAlias = Callable[[], GenerationExecutor]


@dataclass(frozen=True, slots=True)
class RegisteredExecutorFactory:
    """Lazy executor route paired with its explicit task-family claim port."""

    task_type: str
    execution_port_factory: Callable[[], GenerationExecutionPort]
    executor_factory: ExecutorFactory
    recovery_factory: Callable[[], object] | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.task_type, str)
            or not self.task_type
            or len(self.task_type) > 64
            or any(
                character not in "abcdefghijklmnopqrstuvwxyz0123456789-"
                for character in self.task_type
            )
        ):
            raise ValueError("registered task type must be a canonical lowercase identifier")
        if not callable(self.execution_port_factory) or not callable(self.executor_factory):
            raise TypeError("registered executor dependencies must be lazy callables")
        if self.recovery_factory is not None and not callable(self.recovery_factory):
            raise TypeError("recovery_factory must be a lazy callable when provided")

    def __call__(self) -> GenerationExecutor:
        """Preserve the public zero-argument executor-factory shape."""

        return self.executor_factory()


def validate_runtime_configuration(
    explicit_config: GenerationRuntimeConfig,
    executor_factories: Sequence[ExecutorFactory],
) -> tuple[ExecutorFactory, ...]:
    """Validate dependency shapes without invoking factories or opening resources."""

    if not isinstance(explicit_config, GenerationRuntimeConfig):
        raise TypeError("explicit_config must be a GenerationRuntimeConfig")

    factory_fields = (
        "session_factory",
        "db_clock",
        "id_factory",
        "http_client_factory",
        "credential_resolver",
        "media_store_factory",
        "public_media_fetcher_factory",
        "bound_provider_content_fetcher_factory",
        "local_muse_capability_factory",
        "r2_storage_factory",
    )
    for field_name in factory_fields:
        if not callable(getattr(explicit_config, field_name)):
            raise TypeError(f"{field_name} must be an explicit callable factory")

    provider_names: set[str] = set()
    for provider_name, provider_factory in explicit_config.provider_factories:
        if not provider_name or provider_name in provider_names:
            raise ValueError("provider factory names must be non-empty and unique")
        if not callable(provider_factory):
            raise TypeError(f"provider factory {provider_name!r} must be callable")
        provider_names.add(provider_name)

    timeout = explicit_config.shutdown_timeout_seconds
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("shutdown timeout must be a finite positive number")

    factories = tuple(executor_factories)
    if any(not callable(factory) for factory in factories):
        raise TypeError("executor factories must be callable")
    registered_types = [
        factory.task_type
        for factory in factories
        if isinstance(factory, RegisteredExecutorFactory)
    ]
    if len(registered_types) != len(set(registered_types)):
        raise ValueError("registered task types must be unique")
    return factories


__all__ = (
    "ExecutorFactory",
    "RegisteredExecutorFactory",
    "validate_runtime_configuration",
)
