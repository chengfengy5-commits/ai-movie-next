"""FastAPI HTTP adapter for the thirteen team management methods."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from haoai_backend.shared.errors import BusinessError
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.teams import (
    JoinQuotaExceeded,
    InviteCreate,
    JoinRequest,
    PermissionsUpdate,
    RoleUpdate,
    TeamCreate,
    TeamUpdate,
    TeamsUnavailable,
)
from haoai_backend.teams.ports import JoinQuota

from . import application
from .ports import ManagementUnitOfWorkFactory

ActorResolver = Callable[[Request], TrustedActor | Awaitable[TrustedActor]]


class ManagementRoute(APIRoute):
    """Keep the legacy first-message validation body local to team routes."""

    def get_route_handler(self):
        original_handler = super().get_route_handler()

        async def handle(request: Request):
            try:
                return await original_handler(request)
            except RequestValidationError as exc:
                errors = exc.errors()
                message = errors[0].get("msg", "请求参数无效") if errors else "请求参数无效"
                if message.startswith("Value error, "):
                    message = message[len("Value error, "):]
                return JSONResponse(status_code=422, content={"detail": message})

        return handle


def _http_error(error: BusinessError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=error.detail)


def build_management_router(
    *,
    uow_factory: ManagementUnitOfWorkFactory | None,
    resolve_actor: ActorResolver | None,
    join_quota: JoinQuota | None,
) -> APIRouter:
    router = APIRouter(
        prefix="/api",
        tags=["teams"],
        route_class=ManagementRoute,
    )

    async def trusted_actor(request: Request) -> TrustedActor:
        if uow_factory is None or resolve_actor is None:
            error = TeamsUnavailable()
            raise _http_error(error)
        try:
            candidate = resolve_actor(request)
            actor = await candidate if inspect.isawaitable(candidate) else candidate
        except BusinessError as exc:
            raise _http_error(exc) from exc
        if not isinstance(actor, TrustedActor) or not actor.user_id:
            raise _http_error(TeamsUnavailable("可信身份端口未正确接线"))
        return actor

    def invoke(operation: Callable[..., Any], *args: Any) -> Any:
        try:
            return operation(uow_factory, *args)
        except BusinessError as exc:
            raise _http_error(exc) from exc

    @router.post("/teams", status_code=status.HTTP_201_CREATED)
    def create_team_route(
        body: TeamCreate,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, Any]:
        return invoke(application.create_team, actor, body)

    @router.get("/teams/my")
    def my_teams_route(
        actor: TrustedActor = Depends(trusted_actor),
    ) -> list[dict[str, Any]]:
        return invoke(application.my_teams, actor)

    @router.get("/teams/{team_id}")
    def team_detail_route(
        team_id: str,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, Any]:
        return invoke(application.team_detail, actor, team_id)

    @router.put("/teams/{team_id}")
    def update_team_route(
        team_id: str,
        body: TeamUpdate,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, Any]:
        return invoke(application.update_team, actor, team_id, body)

    @router.delete("/teams/{team_id}")
    def delete_team_route(
        team_id: str,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, str]:
        return invoke(application.delete_team, actor, team_id)

    @router.post("/teams/{team_id}/leave")
    def leave_team_route(
        team_id: str,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, str]:
        return invoke(application.leave_team, actor, team_id)

    @router.delete("/teams/{team_id}/members/{user_id}")
    def remove_member_route(
        team_id: str,
        user_id: str,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, str]:
        return invoke(application.remove_member, actor, team_id, user_id)

    @router.put("/teams/{team_id}/members/{user_id}/role")
    def set_member_role_route(
        team_id: str,
        user_id: str,
        body: RoleUpdate,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, Any]:
        return invoke(application.set_member_role, actor, team_id, user_id, body)

    @router.put("/teams/{team_id}/members/{user_id}/permissions")
    def update_member_permissions_route(
        team_id: str,
        user_id: str,
        body: PermissionsUpdate,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, Any]:
        return invoke(application.update_member_permissions, actor, team_id, user_id, body)

    @router.post("/teams/{team_id}/invites", status_code=status.HTTP_201_CREATED)
    def create_invites_route(
        team_id: str,
        body: InviteCreate,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, Any]:
        return invoke(application.create_invites, actor, team_id, body)

    @router.get("/teams/{team_id}/invites")
    def list_invites_route(
        team_id: str,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> list[dict[str, Any]]:
        return invoke(application.list_invites, actor, team_id)

    @router.delete("/teams/{team_id}/invites/{invite_id}")
    def revoke_invite_route(
        team_id: str,
        invite_id: str,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, str]:
        return invoke(application.revoke_invite, actor, team_id, invite_id)

    @router.post("/teams/join")
    def join_team_route(
        request: Request,
        body: JoinRequest,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, Any]:
        if join_quota is None:
            raise _http_error(TeamsUnavailable())
        try:
            join_quota.check(
                request.client.host if request.client is not None else None,
                request.url.path,
            )
        except JoinQuotaExceeded as exc:
            return JSONResponse(
                status_code=429,
                content={"error": exc.detail},
            )
        except BusinessError as exc:
            raise _http_error(exc) from exc
        return invoke(application.join_team, actor, body)

    return router


def mount_management_routes(
    app: FastAPI,
    *,
    uow_factory: ManagementUnitOfWorkFactory | None,
    resolve_actor: ActorResolver | None,
    join_quota: JoinQuota | None,
) -> None:
    app.include_router(
        build_management_router(
            uow_factory=uow_factory,
            resolve_actor=resolve_actor,
            join_quota=join_quota,
        )
    )
