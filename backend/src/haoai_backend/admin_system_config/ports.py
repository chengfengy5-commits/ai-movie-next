"""Persistence ports for administrator system configuration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from .domain import SystemConfigRecord


class AdminSystemConfigUnitOfWork(Protocol):
    def first_config(self) -> SystemConfigRecord | None: ...

    def get_config(self, config_id: str) -> SystemConfigRecord | None: ...

    def load_config_for_update(self, config_id: str) -> object | None: ...

    def set_config_value(self, config: object, field: str, value: Any) -> None: ...

    def refresh_config(self, config: object) -> SystemConfigRecord: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


UnitOfWorkFactory = Callable[[], AdminSystemConfigUnitOfWork]
