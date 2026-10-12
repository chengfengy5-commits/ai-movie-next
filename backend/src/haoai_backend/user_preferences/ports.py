"""Request-scoped persistence and value-generation ports."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from .domain import PreferenceValue


class PreferenceRecord(PreferenceValue, Protocol):
    id: str
    user_id: str
    updated_at: datetime


class UserPreferencesUnitOfWork(Protocol):
    def list_preferences(self, user_id: str) -> list[PreferenceRecord]: ...

    def find_preference(self, user_id: str, key: str) -> PreferenceRecord | None: ...

    def add_preference(
        self,
        *,
        preference_id: str,
        user_id: str,
        key: str,
        value: str,
        updated_at: datetime,
    ) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


UnitOfWorkFactory = Callable[[], UserPreferencesUnitOfWork]
Clock = Callable[[], datetime]
IdFactory = Callable[[], str]
