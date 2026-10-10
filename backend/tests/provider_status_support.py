"""Small injected fakes shared by provider-status unit tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from haoai_backend.provider_status import (
    ProviderConfiguration,
    ProviderStatusRuntime,
)


class FakeConfigurationSession:
    def __init__(
        self,
        *,
        system_configuration: ProviderConfiguration | None = None,
        model_configuration: ProviderConfiguration | None = None,
        query_error: Exception | None = None,
        close_error: Exception | None = None,
    ) -> None:
        self.system_configuration = system_configuration
        self.model_configuration = model_configuration
        self.query_error = query_error
        self.close_error = close_error
        self.calls: list[tuple[str, str]] = []
        self.closed = False

    def first_active_system_model(self, provider: str) -> ProviderConfiguration | None:
        self.calls.append(("system", provider))
        if self.query_error is not None:
            raise self.query_error
        return self.system_configuration

    def first_model_config(self, provider: str) -> ProviderConfiguration | None:
        self.calls.append(("model", provider))
        if self.query_error is not None:
            raise self.query_error
        return self.model_configuration

    def close(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


@dataclass(frozen=True)
class RecordedGet:
    url: str
    headers: Mapping[str, str]
    timeout_seconds: float


class FakeResponse:
    def __init__(
        self,
        *,
        status: int,
        payload: Any = None,
        json_error: Exception | None = None,
    ) -> None:
        self.status = status
        self.payload = payload
        self.json_error = json_error
        self.json_calls = 0

    async def json(self) -> Any:
        self.json_calls += 1
        if self.json_error is not None:
            raise self.json_error
        return self.payload


class FakeResponseContext:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.exited = False

    async def __aenter__(self) -> FakeResponse:
        return self.response

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        self.exited = True
        return False

    @property
    def closed(self) -> bool:
        return self.exited


class FakeClient:
    def __init__(self, factory: FakeHttpClientFactory) -> None:
        self.factory = factory

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> FakeResponseContext:
        self.factory.requests.append(RecordedGet(url, dict(headers), timeout_seconds))
        context = FakeResponseContext(self.factory.response)
        self.factory.response_contexts.append(context)
        return context


class FakeClientContext:
    def __init__(self, factory: FakeHttpClientFactory, use_ipv4: bool) -> None:
        self.factory = factory
        self.use_ipv4 = use_ipv4
        self.closed = False

    async def __aenter__(self) -> FakeClient:
        return FakeClient(self.factory)

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        self.closed = True
        return False


class FakeHttpClientFactory:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.client_contexts: list[FakeClientContext] = []
        self.response_contexts: list[FakeResponseContext] = []
        self.requests: list[RecordedGet] = []

    def __call__(self, use_ipv4: bool) -> FakeClientContext:
        context = FakeClientContext(self, use_ipv4)
        self.client_contexts.append(context)
        return context


def make_configuration(
    provider: str,
    *,
    base_url: str | None = "https://provider.example/v1/",
    api_key: str | None = "test-key",
) -> ProviderConfiguration:
    return ProviderConfiguration(provider=provider, base_url=base_url, api_key=api_key)


def make_runtime(
    session: FakeConfigurationSession,
    http_factory: Any,
    *,
    secret_key: str | None = "provider-status-test-secret",
) -> ProviderStatusRuntime:
    return ProviderStatusRuntime(
        configuration_session_factory=lambda: session,
        secret_key=secret_key,
        http_client_factory=http_factory,
    )
