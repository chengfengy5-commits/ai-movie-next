"""Shared team contracts and delayed router composition.

Importing this package does not import any not-yet-wired team family.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .domain import (
    AssignmentPlan,
    TeamConfig,
    TeamMembershipRecord,
    TeamRecord,
    UserRecord,
)
from .errors import (
    JoinQuotaExceeded,
    StaleTeamWrite,
    TeamBadRequest,
    TeamError,
    TeamForbidden,
    TeamLimitExceeded,
    TeamNotFound,
    TeamsUnavailable,
    UnitOfWorkClosed,
)
from .policy import (
    DEFAULT_ADMIN_PERMISSIONS,
    TEAM_PERMISSIONS,
    default_permissions_for_role,
    has_team_permission,
    member_permissions,
    permissions_for_update,
)
from .quota import InMemoryJoinQuota
from .persistence import (
    SqlAlchemyTeamConfigReader,
    SqlAlchemyTeamPolicyReader,
    TeamSqlAlchemyUnitOfWork,
    assignment_plan,
    create_team_uow_factory,
)
from .schemas import (
    InviteCreate,
    JoinRequest,
    PermissionsUpdate,
    RoleUpdate,
    ShareSeriesRequest,
    TeamCreate,
    TeamSeriesCreate,
    TeamUpdate,
    TransferRequest,
)
from .ports import (
    Clock,
    ConfigReader,
    EpochClock,
    IdSource,
    InviteCodeSource,
    JoinQuota,
    TeamFamilyFactories,
    TeamPolicyReader,
    TeamsUnitOfWork,
    TeamsUnitOfWorkFactory,
)

if TYPE_CHECKING:
    from fastapi import APIRouter
    from .management.ports import ManagementUnitOfWorkFactory
    from .series.ports import SeriesUnitOfWorkFactory
else:
    APIRouter = Any


def build_teams_routers(
    *,
    management_uow_factory: ManagementUnitOfWorkFactory | None,
    series_uow_factory: SeriesUnitOfWorkFactory | None,
    reporting_uow_factory: TeamsUnitOfWorkFactory | None,
    resolve_actor: Any,
    join_quota: JoinQuota | None,
) -> tuple[APIRouter, APIRouter, APIRouter]:
    """Compose family routers only after all three families are installed."""
    from .management.http import build_management_router
    from .reporting.http import build_reporting_router
    from .series.http import build_series_router

    return (
        build_management_router(
            uow_factory=management_uow_factory,
            resolve_actor=resolve_actor,
            join_quota=join_quota,
        ),
        build_series_router(
            uow_factory=series_uow_factory,
            resolve_actor=resolve_actor,
        ),
        build_reporting_router(
            uow_factory=reporting_uow_factory,
            resolve_actor=resolve_actor,
        ),
    )


__all__ = [
    "AssignmentPlan",
    "Clock",
    "ConfigReader",
    "DEFAULT_ADMIN_PERMISSIONS",
    "EpochClock",
    "IdSource",
    "InMemoryJoinQuota",
    "InviteCodeSource",
    "InviteCreate",
    "JoinQuota",
    "JoinQuotaExceeded",
    "JoinRequest",
    "PermissionsUpdate",
    "RoleUpdate",
    "ShareSeriesRequest",
    "SqlAlchemyTeamConfigReader",
    "SqlAlchemyTeamPolicyReader",
    "StaleTeamWrite",
    "TEAM_PERMISSIONS",
    "TeamBadRequest",
    "TeamConfig",
    "TeamCreate",
    "TeamError",
    "TeamFamilyFactories",
    "TeamForbidden",
    "TeamLimitExceeded",
    "TeamMembershipRecord",
    "TeamNotFound",
    "TeamPolicyReader",
    "TeamRecord",
    "TeamSeriesCreate",
    "TeamSqlAlchemyUnitOfWork",
    "TeamsUnavailable",
    "TeamsUnitOfWork",
    "TeamsUnitOfWorkFactory",
    "TeamUpdate",
    "TransferRequest",
    "UnitOfWorkClosed",
    "UserRecord",
    "build_teams_routers",
    "default_permissions_for_role",
    "assignment_plan",
    "create_team_uow_factory",
    "has_team_permission",
    "member_permissions",
    "permissions_for_update",
]
