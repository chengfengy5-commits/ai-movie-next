"""Identity values passed from trusted authentication adapters."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TrustedActor:
    """A user identity resolved by an explicitly wired trusted adapter."""

    user_id: str
