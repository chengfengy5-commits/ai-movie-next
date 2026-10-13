"""Persistence contracts for administrator model-configuration use cases."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol


class AdminModelConfigUnitOfWork(Protocol):
    def list_model_configs(
        self,
        *,
        skip: int,
        limit: int,
        sort_field: str,
        sort_order: str,
        user_id: str | None,
    ) -> tuple[Sequence[Any], int]: ...

    def get_model_config(self, model_id: str) -> Any | None: ...

    def add_model_config(self, model_id: str, values: Mapping[str, Any]) -> Any: ...

    def update_model_config(
        self, model_id: str, changes: Mapping[str, Any]
    ) -> Any | None: ...

    def delete_model_config(self, model_id: str) -> Any | None: ...

    def commit(self) -> None: ...

    def refresh(self, row: Any) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


UnitOfWorkFactory = Callable[[], AdminModelConfigUnitOfWork]
