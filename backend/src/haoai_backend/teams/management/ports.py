"""Framework-independent business ports for team management."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol

from haoai_backend.shared.identity import TrustedActor
from haoai_backend.teams.domain import TeamConfig, TeamMembershipRecord, UserRecord


ManagementRecord = Mapping[str, Any]


class ManagementUnitOfWork(Protocol):
    """One request-owned business Session and its management operations."""

    def now_utc_naive(self) -> datetime: ...

    def new_id(self) -> str: ...

    def new_invite_code(self) -> str: ...

    def invite_code_exists(self, code: str) -> bool: ...

    def load_first_config(self) -> TeamConfig: ...

    def load_users(self, user_ids: Sequence[str]) -> Mapping[str, UserRecord]: ...

    def ensure_clean(self) -> None: ...

    def flush(self) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...

    def load_team_snapshot(self, team_id: str) -> ManagementRecord | None: ...

    def count_owned_teams(self, user_id: str) -> int: ...

    def memberships_for_user(self, user_id: str) -> list[ManagementRecord]: ...

    def teams_for_ids(self, team_ids: Sequence[str]) -> list[ManagementRecord]: ...

    def count_team_members(self, team_id: str) -> int: ...

    def count_joinable_memberships(self, user_id: str) -> int: ...

    def memberships_for_team(self, team_id: str) -> list[ManagementRecord]: ...

    def membership(self, team_id: str, user_id: str) -> TeamMembershipRecord | None: ...

    def member_snapshot(
        self,
        team_id: str,
        user_id: str,
    ) -> ManagementRecord | None: ...

    def invite_snapshot(
        self,
        invite_id: str,
        team_id: str,
    ) -> ManagementRecord | None: ...

    def invite_by_code(self, code: str) -> ManagementRecord | None: ...

    def expired_active_invites(
        self,
        team_id: str,
        now: datetime,
    ) -> list[ManagementRecord]: ...

    def invites_newest_first(self, team_id: str) -> list[ManagementRecord]: ...

    def stage_new_team(
        self,
        *,
        team_id: str,
        name: str,
        owner_id: str,
        created_at: datetime,
    ) -> None: ...

    def stage_new_member(
        self,
        *,
        member_id: str,
        team_id: str,
        user_id: str,
        role: str,
        permissions: str | None,
        joined_at: datetime,
    ) -> None: ...

    def stage_new_invite(
        self,
        *,
        invite_id: str,
        team_id: str,
        code: str,
        created_by: str,
        created_at: datetime,
        expires_at: datetime,
    ) -> None: ...

    def stage_team_name(self, team: ManagementRecord, name: str) -> None: ...

    def stage_member_role(
        self,
        member: ManagementRecord,
        role: str,
        permissions: str | None,
    ) -> None: ...

    def stage_member_permissions(
        self,
        member: ManagementRecord,
        permissions: str | None,
    ) -> None: ...

    def stage_invite_status(
        self,
        invite: ManagementRecord,
        status: str,
    ) -> None: ...

    def stage_invite_used(
        self,
        invite: ManagementRecord,
        user_id: str,
        used_at: datetime,
    ) -> None: ...

    def stage_member_delete(self, member_id: str) -> None: ...

    def stage_team_delete(self, team_id: str) -> None: ...

    def detach_team_series(self, team_id: str) -> None: ...

    def delete_team_invites(self, team_id: str) -> None: ...

    def delete_team_members(self, team_id: str) -> None: ...

    def release_team_locks(
        self,
        team_id: str,
        user_id: str | None = None,
    ) -> None: ...

    def refresh_team(self, team_id: str) -> ManagementRecord | None: ...

    def refresh_member(self, member_id: str) -> ManagementRecord | None: ...


ManagementUnitOfWorkFactory = Callable[[], ManagementUnitOfWork]
ActorResolver = Callable[..., TrustedActor]
