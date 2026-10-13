"""Persistence, cryptography, and explicit provider-request ports."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeAlias


class Cipher(Protocol):
    def encrypt(self, plain_text: str) -> str: ...

    def decrypt(self, cipher_text: str) -> str: ...


class ProviderQuotaUnitOfWork(Protocol):
    def list_accounts(self) -> list[Any]: ...

    def get_account(self, account_id: str) -> Any | None: ...

    def add_account(self, account_id: str, values: Mapping[str, Any]) -> Any: ...

    def set_account_value(self, account: Any, field: str, value: Any) -> None: ...

    def delete_account(self, account: Any) -> None: ...

    def commit(self) -> None: ...

    def refresh(self, account: Any) -> Any: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


UnitOfWorkFactory: TypeAlias = Callable[[], ProviderQuotaUnitOfWork]


@dataclass(frozen=True, slots=True)
class ProviderQuotaHttpResponse:
    status: int
    text: str
    headers: Mapping[str, str | tuple[str, ...]] = field(default_factory=dict)
    cookies: Mapping[str, str] = field(default_factory=dict)


class ProviderQuotaTransport(Protocol):
    """One explicitly injected HTTP request boundary for quota providers."""

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        json_body: Mapping[str, Any] | None = None,
        timeout_seconds: float,
    ) -> ProviderQuotaHttpResponse: ...
