"""Ports for SMTP-account persistence, encryption, and local daily reset."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date
from typing import Any, Protocol


class Cipher(Protocol):
    def encrypt(self, plain_text: str) -> str: ...

    def decrypt(self, cipher_text: str) -> str: ...


class DailyReset(Protocol):
    def reset_if_needed(self) -> None: ...


class AdminSmtpAccountUnitOfWork(Protocol):
    def list_accounts(self) -> list[Any]: ...

    def get_account(self, account_id: str) -> Any | None: ...

    def add_account(self, account_id: str, values: Mapping[str, Any]) -> Any: ...

    def set_account_value(self, account: Any, field: str, value: Any) -> None: ...

    def delete_account(self, account: Any) -> None: ...

    def commit(self) -> None: ...

    def refresh(self, account: Any) -> Any: ...

    def reset_all_daily_counts(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


UnitOfWorkFactory = Callable[[], AdminSmtpAccountUnitOfWork]
SessionFactory = Callable[[], Any]
DateClock = Callable[[], date]
