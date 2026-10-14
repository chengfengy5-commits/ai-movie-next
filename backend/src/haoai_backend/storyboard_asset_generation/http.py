"""Authenticated HTTP routes for chapter-scoped asset extraction and judgments."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from haoai_backend.shared.errors import BusinessError
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.storyboard_generation.execution import run_owned_awaitable, run_owned_in_threadpool

from .application import prepare_extract, prepare_judge
from .errors import StoryboardAssetUnavailable
from .execution import stream_asset_events
from .ports import StoryboardAssetRuntime, StoryboardAssetUnitOfWorkFactory
from .schemas import ExtractRequest, JudgeRequest, StoryboardRequestError

ActorResolver = Callable[[Request], TrustedActor | Awaitable[TrustedActor]]


def build_storyboard_asset_generation_router(
    *,
    uow_factory: StoryboardAssetUnitOfWorkFactory | None,
    resolve_actor: ActorResolver | None,
    runtime: StoryboardAssetRuntime | None,
) -> APIRouter:
    router = APIRouter(tags=["storyboard-asset-generation"])

    async def trusted_actor(request: Request) -> TrustedActor:
        if resolve_actor is None:
            raise HTTPException(status_code=503, detail="认证服务尚未接线")
        try:
            resolver = resolve_actor
            if inspect.iscoroutinefunction(resolver) or inspect.iscoroutinefunction(
                getattr(resolver, "__call__", None)
            ):
                candidate = resolver(request)
            else:
                candidate = await run_owned_in_threadpool(resolver, request)
            actor = await run_owned_awaitable(candidate) if inspect.isawaitable(candidate) else candidate
        except BusinessError as error:
            raise HTTPException(
                status_code=error.status_code,
                detail=error.detail,
                headers=getattr(error, "headers", None),
            ) from error
        if not isinstance(actor, TrustedActor) or not actor.user_id:
            raise HTTPException(status_code=503, detail="可信身份端口未正确接线")
        return actor

    async def require_runtime() -> StoryboardAssetRuntime:
        if runtime is None:
            raise HTTPException(status_code=503, detail="分镜资产生成服务尚未接线")
        return runtime

    async def extract_endpoint(request: Request, actor: TrustedActor, kind: str) -> StreamingResponse:
        configured_runtime = await require_runtime()
        body = await _read_body(request)
        try:
            payload = ExtractRequest.parse(body)
        except ValueError as error:
            status_code = error.status_code if isinstance(error, StoryboardRequestError) else 422
            raise HTTPException(status_code=status_code, detail=str(error)) from error
        try:
            prepared = await run_owned_in_threadpool(
                prepare_extract,
                uow_factory,
                actor,
                kind=kind,
                request=payload,
            )
        except BusinessError as error:
            raise HTTPException(error.status_code, error.detail) from error
        return _stream_response(configured_runtime, prepared)

    async def judge_endpoint(request: Request, actor: TrustedActor, kind: str) -> StreamingResponse:
        configured_runtime = await require_runtime()
        body = await _read_body(request)
        try:
            payload = JudgeRequest.parse(body, kind)
        except ValueError as error:
            status_code = error.status_code if isinstance(error, StoryboardRequestError) else 400
            raise HTTPException(status_code=status_code, detail=str(error)) from error
        try:
            prepared = await run_owned_in_threadpool(
                prepare_judge,
                uow_factory,
                actor,
                kind=kind,
                request=payload,
            )
        except BusinessError as error:
            raise HTTPException(error.status_code, error.detail) from error
        return _stream_response(configured_runtime, prepared)

    @router.post("/api/storyboard/extract-characters-stream")
    async def extract_characters_stream(
        request: Request,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> StreamingResponse:
        return await extract_endpoint(request, actor, "character")

    @router.post("/api/storyboard/judge-character-appearance-stream")
    async def judge_character_appearance_stream(
        request: Request,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> StreamingResponse:
        return await judge_endpoint(request, actor, "character")

    @router.post("/api/storyboard/extract-scenes-stream")
    async def extract_scenes_stream(
        request: Request,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> StreamingResponse:
        return await extract_endpoint(request, actor, "scene")

    @router.post("/api/storyboard/judge-scene-appearance-stream")
    async def judge_scene_appearance_stream(
        request: Request,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> StreamingResponse:
        return await judge_endpoint(request, actor, "scene")

    @router.post("/api/storyboard/extract-props-stream")
    async def extract_props_stream(
        request: Request,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> StreamingResponse:
        return await extract_endpoint(request, actor, "prop")

    @router.post("/api/storyboard/judge-prop-appearance-stream")
    async def judge_prop_appearance_stream(
        request: Request,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> StreamingResponse:
        return await judge_endpoint(request, actor, "prop")

    return router


def _stream_response(runtime: StoryboardAssetRuntime, prepared: Any) -> StreamingResponse:
    async def body():
        events = stream_asset_events(runtime, prepared)
        try:
            async for event in events:
                yield event
        finally:
            await run_owned_awaitable(events.aclose())

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _read_body(request: Request) -> Mapping[str, Any]:
    try:
        value = await request.json()
    except Exception as error:
        raise HTTPException(status_code=422, detail="请求数据格式不正确") from error
    if not isinstance(value, Mapping):
        raise HTTPException(status_code=422, detail="请求数据格式不正确")
    return value
