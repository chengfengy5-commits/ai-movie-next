"""Persistence contracts for administrator redemption-code use cases."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol, TypeAlias

from .domain import NewRedemptionCode, RedemptionCode


class AdminRedemptionCodesUnitOfWork(Protocol):
    def list_codes(
        self,
        *,
        code: str | None,
        code_type: str | None,
        is_used: str | None,
        value: int | None,
        sort_field: str,
        sort_order: str,
        skip: int,
        limit: int,
    ) -> tuple[Sequence[RedemptionCode], int]: ...

    def load_code(self, code_id: str) -> RedemptionCode | None: ...

    def code_exists(self, code: str) -> bool: ...

    def add_code(self, code: NewRedemptionCode) -> None: ...

    def delete_code(self, code_id: str) -> bool: ...

    def commit(self) -> None: ...

    def refresh_code(self, code_id: str) -> RedemptionCode | None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


UnitOfWorkFactory: TypeAlias = Callable[[], AdminRedemptionCodesUnitOfWork]
CodeGenerator: TypeAlias = Callable[[], str]
