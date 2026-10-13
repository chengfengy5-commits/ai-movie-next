"""Persistence contracts for legacy administrator series use cases."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Protocol, TypeAlias

from .domain import NewSeries, SeriesRecord


class AdminSeriesUnitOfWork(Protocol):
    def list_series(
        self,
        *,
        user_id: str | None,
        name: str | None,
        sort_field: str,
        sort_order: str,
        skip: int,
        limit: int,
    ) -> tuple[Sequence[SeriesRecord], int]: ...

    def load_series(self, series_id: str) -> SeriesRecord | None: ...

    def load_series_for_update(self, series_id: str) -> object | None: ...

    def add_series(self, series: NewSeries) -> object: ...

    def commit(self) -> None: ...

    def refresh_series(self, series: object) -> SeriesRecord: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


UnitOfWorkFactory: TypeAlias = Callable[[], AdminSeriesUnitOfWork]
SeriesBody: TypeAlias = Mapping[str, object]
