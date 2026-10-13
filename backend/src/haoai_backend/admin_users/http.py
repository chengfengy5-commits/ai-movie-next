"""FastAPI routes for the legacy administrator user CRUD endpoints."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.authentication.passwords import BcryptPasswordHasher
from haoai_backend.authentication.ports import PasswordHasher
from haoai_backend.shared.errors import BusinessError

from . import application
from .authentication import authenticate_admin_users_actor
from .domain import AdminUsersActor
from .errors import AdminUsersUnavailable
from .persistence import SqlAlchemyAdminUsersUnitOfWork
from .ports import UnitOfWorkFactory
from .schemas import (
    UserCreateBody,
    UserDeletePayload,
    UserListPayload,
    UserMessagePayload,
    UserPayload,
)

SessionFactory = Callable[[], Any]


def _http_error(error: BusinessError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail=error.detail,
        headers=getattr(error, "headers", None),
    )


def build_admin_users_router(
    *,
    authentication_service: AuthenticationService | None,
    session_factory: SessionFactory | None,
    password_hasher: PasswordHasher | None = None,
) -> APIRouter:
    """Build an inert router; each business unit starts in its worker thread."""

    router = APIRouter(prefix="/api/admin", tags=["admin-users"])
    uow_factory: UnitOfWorkFactory | None = None
    if session_factory is not None:

        def make_uow() -> SqlAlchemyAdminUsersUnitOfWork:
            return SqlAlchemyAdminUsersUnitOfWork(session_factory())

        uow_factory = make_uow
    hasher = password_hasher if password_hasher is not None else BcryptPasswordHasher()

    def administrator(request: Request) -> AdminUsersActor:
        if authentication_service is None or uow_factory is None:
            raise _http_error(AdminUsersUnavailable())
        try:
            return authenticate_admin_users_actor(
                authentication_service,
                request.headers.get("authorization", ""),
            )
        except BusinessError as error:
            raise _http_error(error) from error

    async def invoke(call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        if uow_factory is None:
            raise _http_error(AdminUsersUnavailable())
        try:
            return await run_in_threadpool(call, uow_factory, *args, **kwargs)
        except BusinessError as error:
            raise _http_error(error) from error

    @router.post("/users", status_code=201, response_model=None)
    async def create_user(
        data: UserCreateBody,
        actor: AdminUsersActor = Depends(administrator),
    ) -> UserPayload:
        return await invoke(
            application.create_user,
            actor,
            data,
            password_hasher=hasher,
        )

    @router.get("/users", response_model=None)
    async def list_users(
        skip: int = Query(default=0),
        limit: int = Query(default=10),
        sort_field: str = Query(default="created_at"),
        sort_order: str = Query(default="desc"),
        username: str | None = Query(default=None),
        email: str | None = Query(default=None),
        membership_type: str | None = Query(default=None),
        is_superuser: bool | None = Query(default=None),
        actor: AdminUsersActor = Depends(administrator),
    ) -> UserListPayload:
        return await invoke(
            application.list_users,
            actor,
            skip=skip,
            limit=limit,
            sort_field=sort_field,
            sort_order=sort_order,
            username=username,
            email=email,
            membership_type=membership_type,
            is_superuser=is_superuser,
        )

    @router.put("/users/{user_id}", response_model=None)
    async def update_user(
        user_id: str,
        data: dict[str, Any],
        actor: AdminUsersActor = Depends(administrator),
    ) -> UserMessagePayload:
        return await invoke(application.update_user, actor, user_id, data)

    @router.get("/users/{user_id}", response_model=None)
    async def get_user(
        user_id: str,
        actor: AdminUsersActor = Depends(administrator),
    ) -> UserPayload:
        return await invoke(application.get_user, actor, user_id)

    @router.delete("/users/{user_id}", response_model=None)
    async def delete_user(
        user_id: str,
        actor: AdminUsersActor = Depends(administrator),
    ) -> UserDeletePayload:
        return await invoke(application.delete_user, actor, user_id)

    return router
