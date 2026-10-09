"""Read-only application ports for series access."""

from __future__ import annotations

from typing import Protocol

from .domain import SeriesAccessRecord, TeamMembership


class SeriesAccessReader(Protocol):
    def load_series(self, series_id: str) -> SeriesAccessRecord | None:
        ...

    def load_team_membership(
        self,
        team_id: str,
        user_id: str,
    ) -> TeamMembership | None:
        ...

    def load_username(self, user_id: str) -> str | None:
        ...
