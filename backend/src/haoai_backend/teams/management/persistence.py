"""SQLAlchemy adapter for the management-family business ports."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import and_, delete, func, select, update

from haoai_backend.teams import assignment_plan, tables
from haoai_backend.teams.ports import TeamsUnitOfWork, TeamsUnitOfWorkFactory

from .ports import ManagementRecord, ManagementUnitOfWork, ManagementUnitOfWorkFactory


def count_rows(shared: TeamsUnitOfWork, table: Any, condition: Any) -> int:
    result = shared.execute_immediate(
        select(func.count()).select_from(table).where(condition)
    )
    return int(result.scalar_one())


def load_member_by_id(
    shared: TeamsUnitOfWork,
    team_id: str,
    user_id: str,
) -> ManagementRecord | None:
    rows = shared.load_rows(
        tables.team_members,
        tables.team_members.c.team_id == team_id,
        tables.team_members.c.user_id == user_id,
        limit=1,
    )
    return rows[0] if rows else None


def load_invite(
    shared: TeamsUnitOfWork,
    invite_id: str,
    team_id: str,
) -> ManagementRecord | None:
    return shared.load_row(
        tables.team_invites,
        {"id": invite_id},
        tables.team_invites.c.team_id == team_id,
    )


def load_invite_by_code(
    shared: TeamsUnitOfWork,
    code: str,
) -> ManagementRecord | None:
    rows = shared.load_rows(
        tables.team_invites,
        tables.team_invites.c.code == code,
        limit=1,
    )
    return rows[0] if rows else None


def release_team_locks(
    shared: TeamsUnitOfWork,
    team_id: str,
    user_id: str | None = None,
) -> None:
    series_ids = shared.execute_immediate(
        select(tables.series.c.id).where(tables.series.c.team_id == team_id)
    ).scalars().all()
    if not series_ids:
        return

    chapter_ids = shared.execute_immediate(
        select(tables.chapters.c.id).where(tables.chapters.c.series_id.in_(series_ids))
    ).scalars().all()
    if not chapter_ids:
        return

    statement = delete(tables.chapter_locks).where(
        tables.chapter_locks.c.chapter_id.in_(chapter_ids)
    )
    if user_id is not None:
        statement = statement.where(tables.chapter_locks.c.user_id == user_id)
    shared.execute_immediate(statement)


class SqlAlchemyManagementUnitOfWork:
    """Translate management operations to the frozen shared UoW and Core tables."""

    def __init__(self, shared: TeamsUnitOfWork) -> None:
        self._shared = shared

    def now_utc_naive(self) -> datetime:
        return self._shared.now_utc_naive()

    def new_id(self) -> str:
        return self._shared.new_id()

    def new_invite_code(self) -> str:
        return self._shared.new_invite_code()

    def invite_code_exists(self, code: str) -> bool:
        return self._shared.invite_code_exists(code)

    def load_first_config(self):
        return self._shared.load_first_config()

    def load_users(self, user_ids: Sequence[str]):
        return self._shared.load_users(user_ids)

    def ensure_clean(self) -> None:
        self._shared.ensure_clean()

    def flush(self) -> None:
        self._shared.flush()

    def commit(self) -> None:
        self._shared.commit()

    def rollback(self) -> None:
        self._shared.rollback()

    def close(self) -> None:
        self._shared.close()

    def _count(self, table: Any, condition: Any) -> int:
        return count_rows(self._shared, table, condition)

    def load_team_snapshot(self, team_id: str) -> ManagementRecord | None:
        return self._shared.load_row(tables.teams, {"id": team_id})

    def count_owned_teams(self, user_id: str) -> int:
        return self._count(tables.teams, tables.teams.c.owner_id == user_id)

    def memberships_for_user(self, user_id: str) -> list[ManagementRecord]:
        return self._shared.load_rows(
            tables.team_members,
            tables.team_members.c.user_id == user_id,
        )

    def teams_for_ids(self, team_ids: Sequence[str]) -> list[ManagementRecord]:
        if not team_ids:
            return []
        return self._shared.load_rows(
            tables.teams,
            tables.teams.c.id.in_(list(team_ids)),
        )

    def count_team_members(self, team_id: str) -> int:
        return self._count(
            tables.team_members,
            tables.team_members.c.team_id == team_id,
        )

    def count_joinable_memberships(self, user_id: str) -> int:
        return self._count(
            tables.team_members,
            and_(
                tables.team_members.c.user_id == user_id,
                tables.team_members.c.role == "member",
            ),
        )

    def memberships_for_team(self, team_id: str) -> list[ManagementRecord]:
        return self._shared.load_rows(
            tables.team_members,
            tables.team_members.c.team_id == team_id,
        )

    def membership(self, team_id: str, user_id: str):
        return self._shared.load_membership(team_id, user_id)

    def member_snapshot(
        self,
        team_id: str,
        user_id: str,
    ) -> ManagementRecord | None:
        return load_member_by_id(self._shared, team_id, user_id)

    def invite_snapshot(
        self,
        invite_id: str,
        team_id: str,
    ) -> ManagementRecord | None:
        return load_invite(self._shared, invite_id, team_id)

    def invite_by_code(self, code: str) -> ManagementRecord | None:
        return load_invite_by_code(self._shared, code)

    def expired_active_invites(
        self,
        team_id: str,
        now: datetime,
    ) -> list[ManagementRecord]:
        return self._shared.load_rows(
            tables.team_invites,
            tables.team_invites.c.team_id == team_id,
            tables.team_invites.c.status == "active",
            tables.team_invites.c.expires_at < now,
        )

    def invites_newest_first(self, team_id: str) -> list[ManagementRecord]:
        return self._shared.load_rows(
            tables.team_invites,
            tables.team_invites.c.team_id == team_id,
            order_by=(tables.team_invites.c.created_at.desc(),),
        )

    def stage_new_team(
        self,
        *,
        team_id: str,
        name: str,
        owner_id: str,
        created_at: datetime,
    ) -> None:
        self._shared.stage_insert(
            tables.teams,
            {
                "id": team_id,
                "name": name,
                "owner_id": owner_id,
                "created_at": created_at,
            },
        )

    def stage_new_member(
        self,
        *,
        member_id: str,
        team_id: str,
        user_id: str,
        role: str,
        permissions: str | None,
        joined_at: datetime,
    ) -> None:
        self._shared.stage_insert(
            tables.team_members,
            {
                "id": member_id,
                "team_id": team_id,
                "user_id": user_id,
                "role": role,
                "permissions": permissions,
                "joined_at": joined_at,
            },
        )

    def stage_new_invite(
        self,
        *,
        invite_id: str,
        team_id: str,
        code: str,
        created_by: str,
        created_at: datetime,
        expires_at: datetime,
    ) -> None:
        self._shared.stage_insert(
            tables.team_invites,
            {
                "id": invite_id,
                "team_id": team_id,
                "code": code,
                "created_by": created_by,
                "created_at": created_at,
                "expires_at": expires_at,
                "used_by": None,
                "used_at": None,
                "status": "active",
            },
        )

    def stage_team_name(self, team: ManagementRecord, name: str) -> None:
        self._shared.stage_assignment(
            assignment_plan(tables.teams, {"id": team["id"]}, team, {"name": name})
        )

    def stage_member_role(
        self,
        member: ManagementRecord,
        role: str,
        permissions: str | None,
    ) -> None:
        self._shared.stage_assignment(
            assignment_plan(
                tables.team_members,
                {"id": member["id"]},
                member,
                {"role": role, "permissions": permissions},
            )
        )

    def stage_member_permissions(
        self,
        member: ManagementRecord,
        permissions: str | None,
    ) -> None:
        self._shared.stage_assignment(
            assignment_plan(
                tables.team_members,
                {"id": member["id"]},
                member,
                {"permissions": permissions},
            )
        )

    def stage_invite_status(
        self,
        invite: ManagementRecord,
        status: str,
    ) -> None:
        self._shared.stage_assignment(
            assignment_plan(
                tables.team_invites,
                {"id": invite["id"]},
                invite,
                {"status": status},
            )
        )

    def stage_invite_used(
        self,
        invite: ManagementRecord,
        user_id: str,
        used_at: datetime,
    ) -> None:
        self._shared.stage_assignment(
            assignment_plan(
                tables.team_invites,
                {"id": invite["id"]},
                invite,
                {"used_by": user_id, "used_at": used_at, "status": "used"},
            )
        )

    def stage_member_delete(self, member_id: str) -> None:
        self._shared.stage_delete(tables.team_members, {"id": member_id})

    def stage_team_delete(self, team_id: str) -> None:
        self._shared.stage_delete(tables.teams, {"id": team_id})

    def detach_team_series(self, team_id: str) -> None:
        self._shared.execute_immediate(
            update(tables.series)
            .where(tables.series.c.team_id == team_id)
            .values(team_id=None, updated_at=self._shared.now_utc_naive())
        )

    def delete_team_invites(self, team_id: str) -> None:
        self._shared.execute_immediate(
            delete(tables.team_invites).where(tables.team_invites.c.team_id == team_id)
        )

    def delete_team_members(self, team_id: str) -> None:
        self._shared.execute_immediate(
            delete(tables.team_members).where(tables.team_members.c.team_id == team_id)
        )

    def release_team_locks(
        self,
        team_id: str,
        user_id: str | None = None,
    ) -> None:
        release_team_locks(self._shared, team_id, user_id)

    def refresh_team(self, team_id: str) -> ManagementRecord | None:
        return self._shared.refresh_row(tables.teams, {"id": team_id})

    def refresh_member(self, member_id: str) -> ManagementRecord | None:
        return self._shared.refresh_row(tables.team_members, {"id": member_id})


def create_management_uow_factory(
    shared_factory: TeamsUnitOfWorkFactory,
) -> ManagementUnitOfWorkFactory:
    """Adapt the shared request-owned UoW to management business operations."""

    def create() -> ManagementUnitOfWork:
        return SqlAlchemyManagementUnitOfWork(shared_factory())

    return create
