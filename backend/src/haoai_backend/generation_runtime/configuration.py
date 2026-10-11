"""Validation for explicit, lazy generation runtime dependencies."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import TypeAlias

from haoai_backend.generation_contracts.ports import GenerationExecutor, GenerationRuntimeConfig


ExecutorFactory: TypeAlias = Callable[[], GenerationExecutor]


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
    return factories


__all__ = ("ExecutorFactory", "validate_runtime_configuration")
