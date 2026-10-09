"""Read-only persistence contracts for team reporting."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol

from haoai_backend.teams.domain import TeamMembershipRecord, TeamRecord


class ReportingPersistencePort(Protocol):
    """The reporting family’s batched SQL read model."""

    def list_member_tasks(
        self,
        user_id: str,
        page: int,
        page_size: int,
    ) -> tuple[list[Mapping[str, Any]], int]: ...

    def load_messages(
        self,
        message_ids: Sequence[str],
    ) -> Mapping[str, Mapping[str, Any]]: ...

    def load_chapter_titles(self, chapter_ids: Sequence[str]) -> Mapping[str, str]: ...

    def load_asset_names(
        self,
        kind: str,
        asset_ids: Sequence[str],
    ) -> Mapping[str, str | None]: ...

    def load_user_credit(self, user_id: str) -> int: ...

    def list_team_series(
        self,
        team_id: str,
        *,
        newest_first: bool,
    ) -> list[Mapping[str, Any]]: ...

    def load_team_series(
        self,
        team_id: str,
        series_id: str,
    ) -> Mapping[str, Any] | None: ...

    def list_series_chapters(
        self,
        series_id: str,
    ) -> list[Mapping[str, Any]]: ...

    def load_team_usage_rows(
        self,
        series_ids: Sequence[str],
        start_time: datetime | None,
        end_time: datetime | None,
    ) -> list[Sequence[Any]]: ...

    def load_chapter_scope(
        self,
        series_ids: Sequence[str],
        start_time: datetime | None,
        end_time: datetime | None,
    ) -> list[Sequence[Any]]: ...

    def load_series_usage_rows(
        self,
        series_id: str,
        start_time: datetime | None,
        end_time: datetime | None,
    ) -> list[Sequence[Any]]: ...

    def load_model_usage_rows(
        self,
        series_ids: Sequence[str],
        model_name: str,
        start_time: datetime | None,
        end_time: datetime | None,
    ) -> list[Sequence[Any]]: ...

    def load_export_usage_rows(
        self,
        series_ids: Sequence[str],
        start_time: datetime | None,
        end_time: datetime | None,
    ) -> list[Sequence[Any]]: ...

    def load_users(self, user_ids: Sequence[str]) -> Mapping[str, Any]: ...

    def load_export_model_types(
        self,
        model_names: Sequence[str],
    ) -> list[Sequence[Any]]: ...


class ReportingPermissionPort(Protocol):
    """The two shared-record reads required before reporting queries."""

    def load_team(self, team_id: str) -> TeamRecord | None: ...

    def load_membership(
        self,
        team_id: str,
        user_id: str,
    ) -> TeamMembershipRecord | None: ...


class ReportingUnitOfWork(ReportingPersistencePort, ReportingPermissionPort, Protocol):
    """Read-only reporting operations plus request resource lifecycle."""

    def ensure_clean(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


ReportingUnitOfWorkFactory = Callable[[], ReportingUnitOfWork]
