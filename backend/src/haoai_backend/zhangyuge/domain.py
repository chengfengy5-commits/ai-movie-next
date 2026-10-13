"""Framework-free identities and records for Zhangyuge billing."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ZhangyugeAdminActor:
    """Administrator identity copied before the authentication session closes."""

    user_id: str
    is_superuser: bool


@dataclass(frozen=True, slots=True)
class SystemModelCredential:
    """Encrypted credentials copied from one active Zhangyuge model."""

    api_key: str | None
    base_url: str | None


@dataclass(frozen=True, slots=True)
class BalanceCredentials:
    """Decrypted credentials required for one account balance query."""

    api_key: str | None
    base_url: str | None
    pat: str
