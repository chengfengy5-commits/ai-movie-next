"""Persistence, cipher, and HTTP boundaries for Zhangyuge billing."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

from .domain import SystemModelCredential


class ZhangyugeUnitOfWork(Protocol):
    def active_model_credentials(self) -> Sequence[SystemModelCredential]: ...

    def first_system_config(self) -> object | None: ...

    def add_system_config(self, config_id: str) -> object: ...

    def set_billing_pat(self, config: object, ciphertext: str) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


UnitOfWorkFactory = Callable[[], ZhangyugeUnitOfWork]


class Cipher(Protocol):
    def encrypt(self, plain_text: str | None) -> str: ...

    def decrypt(self, cipher_text: str | None) -> str: ...


class BalanceTransport(Protocol):
    async def get_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout_seconds: float = 8,
    ) -> Any: ...
