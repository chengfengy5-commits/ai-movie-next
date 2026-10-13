"""Persistence and hashing contracts for administrator user management."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Protocol

from .domain import UserRecord


class AdminUsersUnitOfWork(Protocol):
    def username_exists(self, username: str) -> bool: ...

    def email_exists(self, email: str) -> bool: ...

    def create_user(
        self,
        *,
        username: str,
        email: str,
        hashed_password: str,
        is_superuser: bool,
        membership_type: str,
        membership_expires_at: Any,
    ) -> Any: ...

    def create_user_credit(self, user_id: str, credits: int) -> Any: ...

    def refresh_user(self, row: Any) -> UserRecord: ...

    def list_users(
        self,
        *,
        skip: int,
        limit: int,
        sort_field: str,
        sort_order: str,
        username: str | None,
        email: str | None,
        membership_type: str | None,
        is_superuser: bool | None,
    ) -> tuple[Sequence[UserRecord], int]: ...

    def get_user_record(self, user_id: str) -> UserRecord | None: ...

    def load_user(self, user_id: str) -> Any | None: ...

    def set_user_field(self, row: Any, field: str, value: Any) -> None: ...

    def get_user_credit(self, user_id: str) -> Any | None: ...

    def set_credit_amount(self, row: Any, credits: int) -> None: ...

    def delete_related_user_data(self, user_id: str) -> None: ...

    def delete_user(self, row: Any) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


UnitOfWorkFactory = Callable[[], AdminUsersUnitOfWork]
