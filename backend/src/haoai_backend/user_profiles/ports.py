"""Ports used by user-profile application logic."""

from __future__ import annotations

from collections.abc import Collection
from typing import Protocol

from .domain import (
    SeriesAssetCounts,
    SeriesRecord,
    TeamMembership,
    TeamRecord,
    UserProfileRecord,
)


class UserProfileReader(Protocol):
    def load_user(self, user_id: str) -> UserProfileRecord | None: ...

    def list_team_ids(self, user_id: str) -> list[str]: ...

    def list_teams(self, team_ids: Collection[str]) -> list[TeamRecord]: ...

    def count_team_members(self, team_id: str) -> int: ...

    def load_team_membership(
        self,
        team_id: str,
        user_id: str,
    ) -> TeamMembership | None: ...

    def list_owned_series(
        self,
        user_id: str,
        team_ids: Collection[str],
    ) -> list[SeriesRecord]: ...

    def count_series_assets(self, series_id: str) -> SeriesAssetCounts: ...


class UserProfileUnitOfWork(UserProfileReader, Protocol):
    def set_bio(self, user_id: str, bio: str) -> None: ...

    def commit(self) -> None: ...
