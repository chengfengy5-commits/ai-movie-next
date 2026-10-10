"""Public framework-independent contracts for provider status queries."""

from .domain import ProviderConfiguration
from .errors import ProviderStatusError
from .ports import (
    PreparedProviderQuery,
    ProviderConfigurationSession,
    ProviderConfigurationSessionFactory,
    ProviderStatusHttpClient,
    ProviderStatusHttpClientFactory,
    ProviderStatusPort,
    ProviderStatusResponse,
    ProviderStatusRuntime,
    ProviderStatusServiceFactory,
)


def create_provider_status_service(
    runtime: ProviderStatusRuntime | None,
) -> ProviderStatusPort:
    """Build the provider adapter lazily so importing this package is inert."""

    from .configuration import build_provider_status_service

    return build_provider_status_service(runtime)


__all__ = [
    "PreparedProviderQuery",
    "ProviderConfiguration",
    "ProviderConfigurationSession",
    "ProviderConfigurationSessionFactory",
    "ProviderStatusError",
    "ProviderStatusHttpClient",
    "ProviderStatusHttpClientFactory",
    "ProviderStatusPort",
    "ProviderStatusResponse",
    "ProviderStatusRuntime",
    "ProviderStatusServiceFactory",
    "create_provider_status_service",
]
