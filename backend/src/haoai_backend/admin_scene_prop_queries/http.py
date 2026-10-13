"""Synchronous HTTP adapter for the legacy administrator scene and prop reads."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.authentication.errors import AuthenticationError
from haoai_backend.shared.errors import BusinessError

from . import application
from .authentication import authenticate_admin_scene_prop_queries_actor
from .domain import AdminScenePropQueriesActor
from .persistence import SqlAlchemyAdminScenePropQueriesUnitOfWork
from .ports import AdminScenePropQueriesUnitOfWork
from .schemas import (
    PropListPayload,
    PropPayload,
    SceneListPayload,
    ScenePayload,
)

SessionFactory = Callable[[], Session]
UnitOfWorkFactory = Callable[[], AdminScenePropQueriesUnitOfWork]
ADMIN_SCENE_PROP_QUERIES_UNAVAILABLE = "场景与道具查询服务尚未接线"


def _business_http_error(error: BusinessError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail=error.detail,
        headers=getattr(error, "headers", None),
    )


def build_admin_scene_prop_queries_router(
    *,
    authentication_service: AuthenticationService | None,
    session_factory: SessionFactory | None,
) -> APIRouter:
    router = APIRouter(prefix="/api/admin", tags=["admin-scene-prop-queries"])
    unit_of_work_factory: UnitOfWorkFactory | None = None
    if session_factory is not None:
        def make_unit_of_work() -> SqlAlchemyAdminScenePropQueriesUnitOfWork:
            return SqlAlchemyAdminScenePropQueriesUnitOfWork(session_factory())

        unit_of_work_factory = make_unit_of_work

    def administrator(request: Request) -> AdminScenePropQueriesActor:
        if authentication_service is None or unit_of_work_factory is None:
            raise HTTPException(
                status_code=503,
                detail=ADMIN_SCENE_PROP_QUERIES_UNAVAILABLE,
            )
        try:
            return authenticate_admin_scene_prop_queries_actor(
                authentication_service,
                request.headers.get("authorization", ""),
            )
        except AuthenticationError as error:
            raise HTTPException(
                status_code=error.status_code,
                detail=error.detail,
                headers=error.headers,
            ) from error

    def invoke(call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        factory = unit_of_work_factory
        if factory is None:
            raise HTTPException(
                status_code=503,
                detail=ADMIN_SCENE_PROP_QUERIES_UNAVAILABLE,
            )
        try:
            return call(factory, *args, **kwargs)
        except BusinessError as error:
            raise _business_http_error(error) from error

    @router.get("/scenes", response_model=None)
    def list_admin_scenes(
        actor: AdminScenePropQueriesActor = Depends(administrator),
        skip: int = Query(default=0),
        limit: int = Query(default=10),
        sort_field: str = Query(default="created_at"),
        sort_order: str = Query(default="desc"),
    ) -> SceneListPayload:
        return invoke(
            application.list_scenes,
            actor,
            skip=skip,
            limit=limit,
            sort_field=sort_field,
            sort_order=sort_order,
        )

    @router.get("/scenes/{scene_id}", response_model=None)
    def get_admin_scene(
        scene_id: str,
        actor: AdminScenePropQueriesActor = Depends(administrator),
    ) -> ScenePayload:
        return invoke(application.get_scene, actor, scene_id)

    @router.get("/props", response_model=None)
    def list_admin_props(
        actor: AdminScenePropQueriesActor = Depends(administrator),
        skip: int = Query(default=0),
        limit: int = Query(default=10),
        sort_field: str = Query(default="created_at"),
        sort_order: str = Query(default="desc"),
    ) -> PropListPayload:
        return invoke(
            application.list_props,
            actor,
            skip=skip,
            limit=limit,
            sort_field=sort_field,
            sort_order=sort_order,
        )

    @router.get("/props/{prop_id}", response_model=None)
    def get_admin_prop(
        prop_id: str,
        actor: AdminScenePropQueriesActor = Depends(administrator),
    ) -> PropPayload:
        return invoke(application.get_prop, actor, prop_id)

    return router
