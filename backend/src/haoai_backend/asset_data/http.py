"""HTTP router for the fifteen legacy asset-data methods."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response, status

from haoai_backend.shared.errors import BusinessError
from haoai_backend.shared.identity import TrustedActor

from . import application
from .errors import AssetDataUnavailable
from .ports import AssetDataUnitOfWorkFactory
from .schemas import (
    CharacterCreate,
    CharacterResponse,
    CharacterUpdate,
    PropCreate,
    PropResponse,
    PropUpdate,
    SceneCreate,
    SceneResponse,
    SceneUpdate,
    StoryboardAssetCreate,
    StoryboardAssetResponse,
    StoryboardAssetUpdate,
)

ActorResolver = Callable[[Request], TrustedActor | Awaitable[TrustedActor]]


def build_asset_data_router(
    *,
    uow_factory: AssetDataUnitOfWorkFactory | None,
    resolve_actor: ActorResolver | None,
) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["asset-data"])

    async def trusted_actor(request: Request) -> TrustedActor:
        if uow_factory is None or resolve_actor is None:
            error = AssetDataUnavailable()
            raise HTTPException(status_code=error.status_code, detail=error.detail)
        try:
            candidate = resolve_actor(request)
            actor = await candidate if inspect.isawaitable(candidate) else candidate
        except BusinessError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        if not isinstance(actor, TrustedActor) or not actor.user_id:
            error = AssetDataUnavailable("可信身份端口未正确接线")
            raise HTTPException(status_code=error.status_code, detail=error.detail)
        return actor

    def invoke(call: Callable[..., Any], *args: Any) -> Any:
        try:
            return call(uow_factory, *args)
        except BusinessError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    @router.get("/series/{series_id}/characters", response_model=list[CharacterResponse])
    def list_characters(series_id: str, actor: TrustedActor = Depends(trusted_actor)):
        return invoke(application.list_assets, actor, "character", series_id)

    @router.post(
        "/series/{series_id}/characters",
        response_model=CharacterResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_character(
        series_id: str,
        request: CharacterCreate,
        actor: TrustedActor = Depends(trusted_actor),
    ):
        return invoke(application.create_asset, actor, "character", series_id, request)

    @router.put("/characters/{character_id}", response_model=CharacterResponse)
    def update_character(
        character_id: str,
        request: CharacterUpdate,
        actor: TrustedActor = Depends(trusted_actor),
    ):
        return invoke(application.update_asset, actor, "character", character_id, request)

    @router.delete("/characters/{character_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_character(character_id: str, actor: TrustedActor = Depends(trusted_actor)) -> Response:
        invoke(application.delete_asset, actor, "character", character_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/series/{series_id}/scenes", response_model=list[SceneResponse])
    def list_scenes(series_id: str, actor: TrustedActor = Depends(trusted_actor)):
        return invoke(application.list_assets, actor, "scene", series_id)

    @router.post(
        "/series/{series_id}/scenes",
        response_model=SceneResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_scene(
        series_id: str,
        request: SceneCreate,
        actor: TrustedActor = Depends(trusted_actor),
    ):
        return invoke(application.create_asset, actor, "scene", series_id, request)

    @router.put("/scenes/{scene_id}", response_model=SceneResponse)
    def update_scene(
        scene_id: str,
        request: SceneUpdate,
        actor: TrustedActor = Depends(trusted_actor),
    ):
        return invoke(application.update_asset, actor, "scene", scene_id, request)

    @router.delete("/scenes/{scene_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_scene(scene_id: str, actor: TrustedActor = Depends(trusted_actor)) -> Response:
        invoke(application.delete_asset, actor, "scene", scene_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/series/{series_id}/props", response_model=list[PropResponse])
    def list_props(series_id: str, actor: TrustedActor = Depends(trusted_actor)):
        return invoke(application.list_assets, actor, "prop", series_id)

    @router.post(
        "/series/{series_id}/props",
        response_model=PropResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_prop(
        series_id: str,
        request: PropCreate,
        actor: TrustedActor = Depends(trusted_actor),
    ):
        return invoke(application.create_asset, actor, "prop", series_id, request)

    @router.put("/props/{prop_id}", response_model=PropResponse)
    def update_prop(
        prop_id: str,
        request: PropUpdate,
        actor: TrustedActor = Depends(trusted_actor),
    ):
        return invoke(application.update_asset, actor, "prop", prop_id, request)

    @router.delete("/props/{prop_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_prop(prop_id: str, actor: TrustedActor = Depends(trusted_actor)) -> Response:
        invoke(application.delete_asset, actor, "prop", prop_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post(
        "/storyboard-assets",
        response_model=StoryboardAssetResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_storyboard_asset(
        request: StoryboardAssetCreate,
        actor: TrustedActor = Depends(trusted_actor),
    ):
        return invoke(application.create_storyboard_asset, actor, request)

    @router.put("/storyboard-assets/{asset_id}", response_model=StoryboardAssetResponse)
    def update_storyboard_asset(
        asset_id: str,
        request: StoryboardAssetUpdate,
        actor: TrustedActor = Depends(trusted_actor),
    ):
        return invoke(application.update_storyboard_asset, actor, asset_id, request)

    @router.delete("/storyboard-assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_storyboard_asset(
        asset_id: str,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> Response:
        invoke(application.delete_storyboard_asset, actor, asset_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router


def mount_asset_data_routes(
    app: FastAPI,
    *,
    uow_factory: AssetDataUnitOfWorkFactory | None,
    resolve_actor: ActorResolver | None,
) -> None:
    app.include_router(
        build_asset_data_router(
            uow_factory=uow_factory,
            resolve_actor=resolve_actor,
        )
    )
