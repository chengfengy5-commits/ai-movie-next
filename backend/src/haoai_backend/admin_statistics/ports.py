"""Ports for statistics aggregation and the loopback balance reader."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol


class BalanceReader(Protocol):
    def read_balance(self, token: str | None) -> Any: ...


class AdminStatisticsUnitOfWork(Protocol):
    def aggregate_statistics(self, now: datetime, month_start: datetime) -> dict[str, Any]: ...

    def get_muse_token(self) -> str | None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


UnitOfWorkFactory = Callable[[], AdminStatisticsUnitOfWork]
Clock = Callable[[], datetime]
