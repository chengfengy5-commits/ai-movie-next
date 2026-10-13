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
