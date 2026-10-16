"""Persistence contracts consumed by redemption-code use cases."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol, TypeAlias

from .domain import (
    NewCreditLog,
    NewRedemptionCode,
    NewUserCredit,
    RedemptionCode,
    UserMembership,
)


class RedemptionCodesUnitOfWork(Protocol):
    def code_exists(self, code: str) -> bool: ...

    def add_generated_code(self, code: NewRedemptionCode) -> None: ...

    def refresh_generated_code(self, code_id: str) -> RedemptionCode | None: ...

    def load_code(self, code: str) -> RedemptionCode | None: ...

    def claim_code_for_redemption(
        self,
        code: str,
        *,
        user_id: str,
    ) -> RedemptionCode | None: ...

    def mark_code_used(
        self,
        code_id: str,
        *,
        user_id: str,
        used_at: datetime,
    ) -> None: ...

    def set_membership(
        self,
        user_id: str,
        *,
        membership_type: str,
        membership_expires_at: datetime,
    ) -> None: ...

    def lock_membership(self, user_id: str) -> UserMembership | None: ...

    def increase_user_credit_balance(
        self,
        credit: NewUserCredit,
        amount: int,
    ) -> int: ...

    def add_credit_log(self, log: NewCreditLog) -> None: ...

    def user_credit_balance(self, user_id: str) -> int: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


UnitOfWorkFactory: TypeAlias = Callable[[], RedemptionCodesUnitOfWork]
CodeGenerator: TypeAlias = Callable[[], str]
Clock: TypeAlias = Callable[[], datetime]
