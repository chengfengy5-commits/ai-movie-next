"""FastAPI adapter for the twelve existing series-data methods."""

from __future__ import annotations

import inspect
import hashlib
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from haoai_backend.canvas_applications.schemas import GenerationApplicationRequest
from haoai_backend.shared.errors import BusinessError
from haoai_backend.shared.identity import TrustedActor

from . import application
from . import continuous_operations
from .errors import SeriesDataUnavailable
from .ports import UnitOfWorkFactory
from .schemas import (
    ChapterCreate,
    ChapterResponse,
    ChapterUpdate,
    DeleteFrameRequest,
    ReorderChaptersRequest,
    SeriesCreate,
    SeriesResponse,
    SeriesUpdate,
    StoryboardAssetResponse,
)
from .continuous_operation_schemas import ContinuousChapterOperationRequest

ActorResolver = Callable[[Request], TrustedActor | Awaitable[TrustedActor]]


def build_series_data_router(
    *,
    uow_factory: UnitOfWorkFactory | None,
    resolve_actor: ActorResolver | None,
    generation_session_factory: Callable[[], Session] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["series-data"])

    async def trusted_actor(request: Request) -> TrustedActor:
        if (uow_factory is None and generation_session_factory is None) or resolve_actor is None:
            error = SeriesDataUnavailable()
            raise HTTPException(status_code=error.status_code, detail=error.detail)
        try:
            candidate = resolve_actor(request)
            actor = await candidate if inspect.isawaitable(candidate) else candidate
        except BusinessError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        if not isinstance(actor, TrustedActor) or not actor.user_id:
            error = SeriesDataUnavailable("可信身份端口未正确接线")
            raise HTTPException(status_code=error.status_code, detail=error.detail)
        return actor

    def invoke(call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return call(uow_factory, *args, **kwargs)
        except BusinessError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    def apply_generation(
        generation_application: GenerationApplicationRequest,
        chapter_id: str,
        actor: TrustedActor,
    ) -> JSONResponse:
        try:
            command = generation_application.to_command()
            from haoai_backend.canvas_applications.application import apply_chapter_generation

            payload = apply_chapter_generation(
                generation_session_factory,
                actor,
                chapter_id,
                command,
            )
        except BusinessError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        return JSONResponse(content=payload)

    @router.get("/chapters/continuous-edit/capabilities")
    def continuous_chapter_edit_capabilities(
        _actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, dict[str, int]]:
        features = {"chapter_edit_guard": 1}
        if continuous_operations.continuous_operation_receipts_ready(uow_factory):
            features["continuous_edit_operation_receipt"] = 1
        return {"features": features}

    @router.get("/series", response_model=list[SeriesResponse])
    def list_series(actor: TrustedActor = Depends(trusted_actor)) -> list[dict[str, Any]]:
        return invoke(application.get_series_list, actor)

    @router.get("/series/{series_id}", response_model=SeriesResponse)
    def read_series(series_id: str, actor: TrustedActor = Depends(trusted_actor)) -> dict[str, Any]:
        return invoke(application.get_series, actor, series_id)

    @router.post("/series", response_model=SeriesResponse, status_code=status.HTTP_201_CREATED)
    def add_series(request: SeriesCreate, actor: TrustedActor = Depends(trusted_actor)) -> dict[str, Any]:
        return invoke(application.create_series, actor, request)

    @router.put("/series/{series_id}", response_model=SeriesResponse)
    def edit_series(series_id: str, request: SeriesUpdate, actor: TrustedActor = Depends(trusted_actor)) -> dict[str, Any]:
        return invoke(application.update_series, actor, series_id, request)

    @router.delete("/series/{series_id}", status_code=status.HTTP_204_NO_CONTENT)
    def remove_series(series_id: str, actor: TrustedActor = Depends(trusted_actor)) -> Response:
        invoke(application.delete_series, actor, series_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/series/{series_id}/chapters", response_model=list[ChapterResponse])
    def list_chapters(series_id: str, actor: TrustedActor = Depends(trusted_actor)) -> list[dict[str, Any]]:
        return invoke(application.get_chapters, actor, series_id)

    @router.put("/series/{series_id}/chapters/reorder")
    def reorder(series_id: str, request: ReorderChaptersRequest, actor: TrustedActor = Depends(trusted_actor)) -> dict[str, str]:
        return invoke(application.reorder_chapters, actor, series_id, request)

    @router.post("/series/{series_id}/chapters", response_model=ChapterResponse, status_code=status.HTTP_201_CREATED)
    def add_chapter(series_id: str, request: ChapterCreate, actor: TrustedActor = Depends(trusted_actor)) -> dict[str, Any]:
        return invoke(application.create_chapter, actor, series_id, request)

    @router.post("/chapters/{chapter_id}/continuous-edit/operations/{operation_id}")
    async def execute_continuous_chapter_operation_route(
        chapter_id: str,
        operation_id: str,
        operation_request: ContinuousChapterOperationRequest,
        http_request: Request,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> JSONResponse:
        raw_body = await http_request.body()
        try:
            execution = await run_in_threadpool(
                continuous_operations.execute_continuous_chapter_operation,
                uow_factory,
                actor,
                chapter_id,
                operation_id,
                operation_request,
                hashlib.sha256(raw_body).hexdigest(),
            )
        except BusinessError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        return JSONResponse(status_code=execution.status_code, content=execution.payload)

    @router.get("/chapters/{chapter_id}/continuous-edit/operations/{operation_id}")
    def read_continuous_chapter_operation_route(
        chapter_id: str,
        operation_id: str,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, Any]:
        return invoke(
            continuous_operations.get_continuous_chapter_operation,
            actor,
            chapter_id,
            operation_id,
        )

    @router.put("/chapters/{chapter_id}", response_model=ChapterResponse)
    def edit_chapter(chapter_id: str, request: ChapterUpdate, actor: TrustedActor = Depends(trusted_actor)) -> Any:
        if request.generation_application is not None:
            return apply_generation(request.generation_application, chapter_id, actor)
        if request.chapter_edit_guard is not None:
            assert request.content is not None
            return invoke(
                application.update_chapter_guarded,
                actor,
                chapter_id,
                request.content,
                request.chapter_edit_guard,
            )
        return invoke(application.update_chapter, actor, chapter_id, request)

    @router.put("/chapters/{chapter_id}/delete-frame", response_model=ChapterResponse)
    def remove_frame(chapter_id: str, request: DeleteFrameRequest, actor: TrustedActor = Depends(trusted_actor)) -> dict[str, Any]:
        return invoke(application.delete_frame, actor, chapter_id, request)

    @router.delete("/chapters/{chapter_id}", status_code=status.HTTP_204_NO_CONTENT)
    def remove_chapter(chapter_id: str, actor: TrustedActor = Depends(trusted_actor)) -> Response:
        invoke(application.delete_chapter, actor, chapter_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/series/{series_id}/storyboard-assets", response_model=list[StoryboardAssetResponse])
    def list_storyboard_assets(
        series_id: str,
        chapter_id: str | None = None,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> list[dict[str, Any]]:
        return invoke(application.get_storyboard_assets, actor, series_id, chapter_id)

    return router


def mount_series_data_routes(
    app: FastAPI,
    *,
    uow_factory: UnitOfWorkFactory | None,
    resolve_actor: ActorResolver | None,
    generation_session_factory: Callable[[], Session] | None = None,
) -> None:
    app.include_router(
        build_series_data_router(
            uow_factory=uow_factory,
            resolve_actor=resolve_actor,
            generation_session_factory=generation_session_factory,
        )
    )
