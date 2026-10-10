"""Framework-free configuration and single-request HTTP ports."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeAlias

from .domain import ProviderConfiguration


class ProviderConfigurationSession(Protocol):
    """One explicitly supplied configuration Session hidden behind a reader port."""

    def first_active_system_model(self, provider: str) -> ProviderConfiguration | None: ...

    def first_model_config(self, provider: str) -> ProviderConfiguration | None: ...

    def close(self) -> None: ...


ProviderConfigurationSessionFactory: TypeAlias = Callable[[], ProviderConfigurationSession]


class ProviderStatusResponse(Protocol):
    """An owned HTTP response whose body is read before its context exits."""

    status: int

    async def json(self) -> Any: ...


class ProviderStatusHttpClient(Protocol):
    """An owned async client supporting exactly one provider status request."""

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> AbstractAsyncContextManager[ProviderStatusResponse]: ...


ProviderStatusHttpClientFactory: TypeAlias = Callable[
    [bool], AbstractAsyncContextManager[ProviderStatusHttpClient]
]


@dataclass(frozen=True, slots=True)
class ProviderStatusRuntime:
    """Explicit factories and secret input; construction creates no resources."""

    configuration_session_factory: ProviderConfigurationSessionFactory | None = None
    secret_key: str | None = field(default=None, repr=False, compare=False)
    http_client_factory: ProviderStatusHttpClientFactory | None = None


class PreparedProviderQuery(Protocol):
    """A query prepared outside the poll error boundary for one provider."""

    async def poll_once(self, external_task_id: str) -> Any: ...

    def extract_result(self, response: Any) -> Any: ...


class ProviderStatusPort(Protocol):
    """Prepare explicit credentials/configuration before a single status GET."""

    def prepare(self, provider: str) -> PreparedProviderQuery | None: ...


ProviderStatusServiceFactory: TypeAlias = Callable[
    [ProviderStatusRuntime | None], ProviderStatusPort
]
