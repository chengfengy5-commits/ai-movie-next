"""Authenticated routes preserving the legacy chat text request contracts."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ValidationError

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.shared.errors import BusinessError
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.storyboard_generation.execution import (
    run_owned_awaitable,
    run_owned_in_threadpool,
)
from haoai_backend.generation_core.frame_review_admission import admit_frame_review
from haoai_backend.task_observation.authentication import resolve_active_actor

from .configuration import ChatTextGenerationConfiguration
from .errors import ChatTextGenerationError
from .execution import (
    ChatTextGenerationExecution,
    disconnect_consumer,
    sse_events,
)
from .persistence import (
    admit_ai_review,
    admit_batch_optimize,
    bind_stream_task,
    mark_stream_started,
    prepare_stream_and_commit_fee,
    settle_stream,
)
from .ports import ApiKeyDecryptor, SessionFactory
from .schemas import AIReviewRequest, BatchOptimizeRequest, ChatStreamRequest


def build_chat_text_generation_router(
    *,
    authentication_service: AuthenticationService,
    session_factory: SessionFactory | None,
    configuration: ChatTextGenerationConfiguration | None,
    decrypt_api_key: ApiKeyDecryptor | None,
) -> APIRouter:
    router = APIRouter(prefix="/api/chat", tags=["chat-text-generation"])
    execution = (
        ChatTextGenerationExecution(
            configuration.runtime,
            session_factory,
            configuration,
        )
        if configuration is not None and configuration.runtime is not None
        else None
    )
    setattr(router, "chat_text_generation_execution", execution)

    async def active_actor(request: Request) -> TrustedActor:
        try:
            return await run_owned_in_threadpool(
                resolve_active_actor,
                authentication_service,
                request.headers.get("authorization", ""),
            )
        except BusinessError as error:
            raise HTTPException(
                status_code=error.status_code,
                detail=error.detail,
                headers=getattr(error, "headers", None),
            ) from error

    async def parse_request(request: Request, schema: type[BaseModel]) -> Any:
        try:
            payload = json.loads(await request.body())
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise HTTPException(status_code=422, detail="请求格式无效") from error
        if not isinstance(payload, Mapping):
            raise HTTPException(status_code=422, detail="请求格式无效")
        try:
            return schema.model_validate(payload)
        except ValidationError as error:
            raise HTTPException(status_code=422, detail=error.errors()) from error

    def raise_service_error(error: ChatTextGenerationError) -> None:
        raise HTTPException(
            status_code=error.status_code,
            detail=error.detail,
        ) from error

    @router.post("/stream", name="chat_stream")
    async def chat_stream(request: Request) -> StreamingResponse:
        actor = await active_actor(request)
        payload = await parse_request(request, ChatStreamRequest)
        if execution is None:
            raise HTTPException(status_code=503, detail="聊天运行服务尚未接线")
        id_factory = configuration.server_id_factory if configuration else None
        clock = configuration.clock if configuration else None

        queue_holder: list[asyncio.Queue[dict[str, Any] | None]] = []

        async def prepare_and_start() -> asyncio.Queue[dict[str, Any] | None]:
            prepared = None
            try:
                prepared = await run_owned_in_threadpool(
                    prepare_stream_and_commit_fee,
                    session_factory,
                    actor.user_id,
                    payload,
                    decrypt_api_key=decrypt_api_key,
                    id_factory=id_factory,
                    clock=clock,
                )
                if prepared.message_id:
                    prepared = await run_owned_in_threadpool(
                        bind_stream_task,
                        session_factory,
                        prepared,
                        payload.task_type or "chat",
                        id_factory=id_factory,
                        clock=clock,
                    )
                started = await run_owned_in_threadpool(
                    mark_stream_started,
                    session_factory,
                    prepared,
                    clock=clock,
                )
                if not started:
                    raise ChatTextGenerationError(503, "聊天任务暂时无法启动")
                queue = execution.start(prepared)
                queue_holder.append(queue)
                return queue
            except Exception:
                if prepared is not None:
                    try:
                        await run_owned_in_threadpool(
                            settle_stream,
                            session_factory,
                            prepared,
                            succeeded=False,
                            result="任务启动失败",
                            clock=clock,
                        )
                    except Exception:
                        pass
                raise

        try:
            queue = await run_owned_awaitable(prepare_and_start())
        except asyncio.CancelledError:
            if queue_holder:
                disconnect_consumer(queue_holder[-1])
            raise
        except ChatTextGenerationError as error:
            raise_service_error(error)

        return StreamingResponse(
            sse_events(queue),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @router.post("/ai-review", name="create_ai_review_task")
    async def create_ai_review_task(request: Request) -> dict[str, Any]:
        actor = await active_actor(request)
        payload = await parse_request(request, AIReviewRequest)
        try:
            frame_review = (
                configuration.frame_review_admission
                if configuration is not None
                else None
            )
            if frame_review is not None:
                return await run_owned_in_threadpool(
                    admit_frame_review,
                    session_factory,
                    actor.user_id,
                    payload,
                    registry_capabilities=frame_review.registry_capabilities,
                    max_user_concurrency=configuration.max_user_concurrency,
                    id_factory=configuration.server_id_factory,
                    clock=configuration.clock,
                )
            return await run_owned_in_threadpool(
                admit_ai_review,
                session_factory,
                actor.user_id,
                payload,
                max_user_concurrency=(
                    configuration.max_user_concurrency if configuration else 0
                ),
                id_factory=configuration.server_id_factory if configuration else None,
                clock=configuration.clock if configuration else None,
            )
        except ChatTextGenerationError as error:
            raise_service_error(error)

    @router.post("/batch-optimize", name="batch_optimize_background")
    async def batch_optimize_background(request: Request) -> dict[str, Any]:
        actor = await active_actor(request)
        payload = await parse_request(request, BatchOptimizeRequest)
        try:
            return await run_owned_in_threadpool(
                admit_batch_optimize,
                session_factory,
                actor.user_id,
                payload,
                max_user_concurrency=(
                    configuration.max_user_concurrency if configuration else 0
                ),
                id_factory=configuration.server_id_factory if configuration else None,
                clock=configuration.clock if configuration else None,
            )
        except ChatTextGenerationError as error:
            raise_service_error(error)

    return router
