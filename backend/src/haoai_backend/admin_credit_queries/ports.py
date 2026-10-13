"""Persistence contract for administrator credit queries."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Protocol

from .domain import CreditLogRecord, UserCreditRecord


class AdminCreditQueriesUnitOfWork(Protocol):
    def list_user_credits(
        self,
        *,
        skip: int,
        limit: int,
        sort_field: str,
        sort_order: str,
    ) -> tuple[Sequence[UserCreditRecord], int]: ...

    def get_user_credit(self, credit_id: str) -> UserCreditRecord | None: ...

    def load_user_credit_for_update(self, credit_id: str) -> Any | None: ...

    def set_user_credit_amount(self, row: Any, credits: int) -> None: ...

    def refresh_user_credit(self, row: Any) -> UserCreditRecord: ...

    def list_credit_logs(
        self,
        *,
        skip: int,
        limit: int,
        sort_field: str,
        sort_order: str,
        user_id: str | None,
    ) -> tuple[Sequence[CreditLogRecord], int]: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


UnitOfWorkFactory = Callable[[], AdminCreditQueriesUnitOfWork]
