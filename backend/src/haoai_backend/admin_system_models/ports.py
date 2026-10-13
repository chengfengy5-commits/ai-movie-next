"""Persistence and cipher ports for administrator system models."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

from .domain import SystemModelRecord


class AdminSystemModelUnitOfWork(Protocol):
    def list_models(
        self,
        *,
        category: str | None,
        provider: str | None,
        is_active: bool | None,
        skip: int,
        limit: int,
    ) -> tuple[Sequence[SystemModelRecord], int]: ...

    def get_model(self, model_id: str) -> SystemModelRecord | None: ...

    def add_model(self, model_id: str, values: Mapping[str, Any]) -> object: ...

    def load_model_for_update(self, model_id: str) -> object | None: ...

    def set_model_values(self, model: object, changes: Mapping[str, Any]) -> None: ...

    def refresh_model(self, model: object) -> SystemModelRecord: ...

    def delete_model(self, model_id: str) -> bool: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


UnitOfWorkFactory = Callable[[], AdminSystemModelUnitOfWork]


class Cipher(Protocol):
    def encrypt(self, plain_text: str | None) -> str: ...

    def decrypt(self, cipher_text: str | None) -> str: ...
