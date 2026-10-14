"""Authenticated HTTP routes for storyboard generation."""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from haoai_backend.shared.errors import BusinessError
from haoai_backend.shared.identity import TrustedActor

from .application import (
    PreparedChapterStoryboard,
    PreparedStoryboard,
    ensure_runtime_available,
    generate_storyboard as run_storyboard_generation,
    prepare_chapter_storyboard,
    prepare_storyboard,
    prepare_two_pass_storyboard,
    safe_provider_detail,
    save_generated_chapter,
    save_task_result,
    standard_runtime_call,
)
from .domain import StoryboardRuntimeCall
from .execution import (
    StoryboardGenerationExecution,
    run_owned_awaitable,
    run_owned_in_threadpool,
)
from .ports import StoryboardGenerationRuntime, StoryboardGenerationUnitOfWorkFactory
from .schemas import (
    CreateChapterWithStoryboardRequest,
    GenerateStoryboardRequest,
    TwoPassStoryboardRequest,
)

ActorResolver = Callable[[Request], TrustedActor | Awaitable[TrustedActor]]


def build_storyboard_generation_router(
    *,
    uow_factory: StoryboardGenerationUnitOfWorkFactory | None,
    resolve_actor: ActorResolver | None,
    runtime: StoryboardGenerationRuntime | None,
) -> APIRouter:
    router = APIRouter(prefix="/api/storyboard", tags=["storyboard-generation"])
    execution = (
        StoryboardGenerationExecution(runtime, uow_factory)
        if runtime is not None
        else None
    )

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
            actor = (
                await run_owned_awaitable(candidate)
                if inspect.isawaitable(candidate)
                else candidate
            )
        except BusinessError as error:
            raise HTTPException(error.status_code, error.detail) from error
        if not isinstance(actor, TrustedActor) or not actor.user_id:
            raise HTTPException(status_code=503, detail="可信身份端口未正确接线")
        return actor

    def require_runtime() -> tuple[StoryboardGenerationRuntime, StoryboardGenerationExecution]:
        try:
            ensure_runtime_available(runtime)
        except BusinessError as error:
            raise HTTPException(error.status_code, error.detail) from error
        assert runtime is not None and execution is not None
        return runtime, execution

    @router.post("/generate")
    async def generate_storyboard(
        request: Request,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, Any]:
        configured_runtime, _ = require_runtime()
        payload = await _validated_request(request, GenerateStoryboardRequest)
        _validate_content(payload.raw_content, payload.model_id)
        prepared: PreparedStoryboard | None = None
        try:
            prepared = await run_owned_in_threadpool(
                prepare_storyboard,
                uow_factory,
                actor,
                payload,
            )
            frames, raw_response = await run_storyboard_generation(
                configured_runtime,
                standard_runtime_call(prepared),
            )
            if prepared.task_id:
                await run_owned_in_threadpool(
                    save_task_result,
                    uow_factory,
                    actor,
                    prepared.task_id,
                    status="completed",
                    result={"frames": frames, "raw_response": raw_response},
                )
            return {"success": True, "frames": frames, "raw_response": raw_response}
        except BusinessError as error:
            raise HTTPException(error.status_code, error.detail) from error
        except Exception as error:
            detail = (
                safe_provider_detail(error, prepared.model)
                if prepared is not None
                else "生成分镜失败"
            )
            if payload.task_id:
                await _try_save_task(
                    uow_factory,
                    actor,
                    payload.task_id,
                    {"error": detail},
                )
            raise HTTPException(status_code=500, detail=detail) from error

    @router.post("/generate-stream")
    async def generate_storyboard_stream(
        request: Request,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> StreamingResponse:
        require_runtime()
        payload = await _validated_request(request, GenerateStoryboardRequest)
        _validate_content(payload.raw_content, payload.model_id)
        try:
            prepared = await run_owned_in_threadpool(
                prepare_storyboard,
                uow_factory,
                actor,
                payload,
            )
        except BusinessError as error:
            raise HTTPException(error.status_code, error.detail) from error
        assert execution is not None
        queue = execution.start_storyboard_stream(prepared)
        return StreamingResponse(
            _sse_events(queue, {"type": "start", "message": "开始生成分镜..."}),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @router.post("/create-chapter-with-storyboard", status_code=201)
    async def create_chapter_with_storyboard(
        request: Request,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, Any]:
        configured_runtime, _ = require_runtime()
        payload = await _validated_request(request, CreateChapterWithStoryboardRequest)
        _validate_content(payload.raw_content, payload.model_id)
        prepared: PreparedChapterStoryboard | None = None
        try:
            prepared = await run_owned_in_threadpool(
                prepare_chapter_storyboard,
                uow_factory,
                actor,
                payload,
            )
            call = StoryboardRuntimeCall(
                prepared.model,
                prepared.prompt.system_prompt,
                prepared.prompt.user_prompt,
                prepared.raw_content,
                0.7,
                operation="storyboard",
            )
            frames, _raw_response = await run_storyboard_generation(configured_runtime, call)
            chapter_id, saved_frames = await run_owned_in_threadpool(
                save_generated_chapter,
                uow_factory,
                prepared,
                frames,
            )
            return {
                "success": True,
                "chapter_id": chapter_id,
                "frames_count": len(saved_frames),
                "frames": saved_frames,
            }
        except BusinessError as error:
            raise HTTPException(error.status_code, error.detail) from error
        except Exception as error:
            detail = (
                safe_provider_detail(error, prepared.model)
                if prepared is not None
                else "创建章节失败"
            )
            raise HTTPException(status_code=500, detail=f"创建章节失败: {detail}") from error

    @router.post("/two-pass")
    async def two_pass_storyboard(
        request: Request,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> StreamingResponse:
        _, configured_execution = require_runtime()
        payload = await _validated_request(request, TwoPassStoryboardRequest)
        _validate_content(payload.raw_content, payload.model_id)
        try:
            prepared = await run_owned_in_threadpool(
                prepare_two_pass_storyboard,
                uow_factory,
                actor,
                payload,
            )
        except BusinessError as error:
            raise HTTPException(error.status_code, error.detail) from error
        queue = configured_execution.start_two_pass_stream(prepared)
        return StreamingResponse(
            _sse_events(queue, {"type": "start", "message": "开始两步分镜..."}),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return router


async def _validated_request(request: Request, schema: type[Any]) -> Any:
    try:
        body = await request.json()
    except Exception as error:
        raise HTTPException(status_code=422, detail="请求 JSON 无效") from error
    try:
        return schema.model_validate(body)
    except ValidationError as error:
        raise RequestValidationError(error.errors(), body=body) from error


def _validate_content(raw_content: str, model_id: str) -> None:
    if not raw_content.strip():
        raise HTTPException(status_code=400, detail="剧本内容不能为空")
    if not model_id.strip():
        raise HTTPException(status_code=400, detail="模型ID不能为空")


async def _sse_events(
    queue: Any,
    first: dict[str, Any],
):
    yield f"data: {json.dumps(first, ensure_ascii=False)}\n\n"
    while True:
        event = await queue.get()
        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        if event.get("type") in {"error", "complete", "done"}:
            return


async def _try_save_task(
    factory: StoryboardGenerationUnitOfWorkFactory | None,
    actor: TrustedActor,
    task_id: str,
    result: object,
) -> None:
    if factory is None:
        return
    try:
        await run_owned_in_threadpool(
            save_task_result,
            factory,
            actor,
            task_id,
            status="failed",
            result=result,
        )
    except Exception:
        return
