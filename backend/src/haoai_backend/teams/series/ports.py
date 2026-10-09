"""Series-family ports built on the frozen shared team UoW contract."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol

from haoai_backend.teams.domain import TeamRecord
from haoai_backend.teams.ports import TeamsUnitOfWork
from .domain import TeamSeriesPage


class SeriesUnitOfWork(TeamsUnitOfWork, Protocol):
    """Framework-free query and write-intent boundary for series use cases."""

    def load_series(
        self,
        series_id: str,
        *,
        team_id: str | None = None,
    ) -> Mapping[str, object] | None: ...

    def list_team_series_page(
        self,
        team_id: str,
        page: int,
        page_size: int,
    ) -> TeamSeriesPage: ...

    def stage_series_insert(self, values: Mapping[str, object]) -> None: ...

    def stage_series_assignment(
        self,
        row: Mapping[str, object],
        assigned_values: Mapping[str, object],
    ) -> None: ...

    def release_series_locks(self, series_id: str) -> None: ...

    def refresh_series(self, series_id: str) -> Mapping[str, object] | None: ...

    def refresh_team(self, team_id: str) -> TeamRecord | None: ...


SeriesUnitOfWorkFactory = Callable[[], SeriesUnitOfWork]
