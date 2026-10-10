"""Framework-independent provider configuration and polling values."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ProviderConfiguration:
    """The minimal provider-specific values read from a configured model row."""

    provider: str
    base_url: str | None
    api_key: str | None = field(repr=False, compare=False)
