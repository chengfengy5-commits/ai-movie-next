"""Injected boundaries for the AIStarsLab compatibility adapter."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol


class AistarslabTransport(Protocol):
    """Return a response body without imposing HTTP-status semantics."""

    async def request_text(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        *,
        json_body: dict[str, Any] | None,
        timeout: int,
    ) -> str: ...


ProviderTransport = AistarslabTransport
Clock = Callable[[], float]
IdFactory = Callable[[], str]


class AistarslabProvider(Protocol):
    """Provider operations used by the application use cases."""

    async def fetch_config(
        self, api_key: str, base_url: str
    ) -> dict[str, Any]: ...

    async def get_credits(self, api_key: str, base_url: str) -> int | None: ...


class AistarslabConfigCache(Protocol):
    """Process-local provider configuration cache behavior."""

    def get_fresh(self, now: float) -> dict[str, Any] | None: ...

    def store(self, data: dict[str, Any], now: float) -> None: ...

    def clear(self) -> None: ...


class AistarslabSystemModelWriter(Protocol):
    """System-model writes used by the legacy add and synchronization routes."""

    def create_system_model(
        self,
        *,
        category: str,
        name: str,
        model: str,
        base_url: str,
        params: str,
        cost_per_call: int,
        description: str,
        api_key: str,
        id_factory: IdFactory,
    ) -> Any: ...

    def sync_system_models(
        self,
        *,
        video_config: list[dict[str, Any]],
        image_config: list[dict[str, Any]],
        api_key: str,
        base_url: str,
        multiplier: float,
        id_factory: IdFactory,
    ) -> dict[str, int]: ...


@dataclass(frozen=True, slots=True)
class AistarslabCredentialRecord:
    """The fields needed to resolve an active system provider credential."""

    api_key: str | None
    base_url: str | None


@dataclass(frozen=True, slots=True)
class AistarslabExistingModelRecord:
    """Identity fields used to preserve the legacy duplicate-row selection."""

    id: str
    model: str | None


class AistarslabLegacyReader(Protocol):
    """Narrow unordered reads whose ordering is part of the legacy behavior."""

    def list_active_system_credentials(
        self,
    ) -> Sequence[AistarslabCredentialRecord]: ...

    def list_existing_system_models(
        self,
        provider: str,
    ) -> Sequence[AistarslabExistingModelRecord]: ...


AistarslabLegacyReaderFactory = Callable[[], AistarslabLegacyReader]
