"""Prepare provider status queries from explicitly owned configuration inputs."""

from __future__ import annotations

from typing import Any

from .credentials import CredentialDecryptor
from .errors import ProviderStatusError
from .ports import (
    ProviderStatusHttpClientFactory,
    ProviderStatusPort,
    ProviderStatusRuntime,
    PreparedProviderQuery,
)
from .protocols import ProviderProtocol, get_provider_protocol


class _PreparedProviderQuery:
    def __init__(
        self,
        *,
        protocol: ProviderProtocol,
        base_url: str,
        api_key: str,
        http_client_factory: ProviderStatusHttpClientFactory,
    ) -> None:
        self._protocol = protocol
        self._base_url = base_url
        self._api_key = api_key
        self._http_client_factory = http_client_factory
        self._last_external_task_id = ""

    async def poll_once(self, external_task_id: str) -> tuple[str, Any]:
        self._last_external_task_id = external_task_id
        url = self._protocol.poll_url(self._base_url, external_task_id)
        headers = self._protocol.headers(self._api_key)
        async with self._http_client_factory(
            self._protocol.use_ipv4_connector
        ) as client:
            async with client.get(
                url,
                headers=headers,
                timeout_seconds=self._protocol.timeout_seconds,
            ) as response:
                if response.status != 200:
                    return "pending", {}
                payload = await response.json()
                return self._protocol.parse_status(payload)

    def extract_result(self, response: Any) -> Any:
        content_url = self._protocol.content_url(
            self._base_url,
            self._last_external_task_id,
        )
        return self._protocol.extract_result(response, content_url)


class _ProviderStatusService(ProviderStatusPort):
    def __init__(self, runtime: ProviderStatusRuntime | None) -> None:
        self._runtime = runtime
        self._credential_decryptor = CredentialDecryptor(
            runtime.secret_key if runtime is not None else None
        )

    def prepare(self, provider: str) -> PreparedProviderQuery | None:
        if not provider:
            return None
        if self._runtime is None:
            raise ProviderStatusError(503, "Provider status runtime is not configured.")

        session_factory = self._runtime.configuration_session_factory
        if session_factory is None:
            raise ProviderStatusError(
                503, "Provider configuration session factory is not configured."
            )

        session = session_factory()
        try:
            configuration = session.first_active_system_model(provider)
            if configuration is None:
                configuration = session.first_model_config(provider)
            if configuration is None:
                return None

            base_url = configuration.base_url or ""
            api_key = self._credential_decryptor.decrypt(configuration.api_key)
        finally:
            session.close()

        protocol = get_provider_protocol(provider)
        if protocol is None:
            return None

        http_client_factory = self._runtime.http_client_factory
        if http_client_factory is None:
            raise ProviderStatusError(
                503, "Provider status HTTP client factory is not configured."
            )

        return _PreparedProviderQuery(
            protocol=protocol,
            base_url=base_url,
            api_key=api_key,
            http_client_factory=http_client_factory,
        )


def build_provider_status_service(
    runtime: ProviderStatusRuntime | None,
) -> ProviderStatusPort:
    """Build an inert provider adapter around explicit runtime dependencies."""

    return _ProviderStatusService(runtime)
