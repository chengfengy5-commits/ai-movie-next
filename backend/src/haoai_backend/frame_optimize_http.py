"""Authenticated public control routes for registered frame.optimize batches."""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.chat_text_generation.errors import ChatTextGenerationError
from haoai_backend.chat_text_generation.ports import SessionFactory
from haoai_backend.chat_text_generation.schemas import BatchOptimizeRequest
from haoai_backend.generation_core.frame_optimize_admission import admit_frame_optimize
from haoai_backend.generation_credentials.application import check_credential_registry_readiness
from haoai_backend.generation_runtime.frame_optimize_assembly import FrameOptimizeWorkerConfiguration
from haoai_backend.shared.errors import BusinessError
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.storyboard_generation.execution import run_owned_in_threadpool
from haoai_backend.task_observation.authentication import resolve_active_actor


def build_frame_optimize_router(
    *,
    authentication_service: AuthenticationService,
    session_factory: SessionFactory | None,
    configuration: FrameOptimizeWorkerConfiguration | None,
) -> APIRouter:
    router = APIRouter(prefix="/api/chat/frame-optimize", tags=["frame-optimize"])

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

    def availability(request: Request) -> tuple[bool, str | None]:
        if configuration is None or not configuration.enabled:
            return False, "not_configured"
        service = getattr(request.app.state, "frame_optimize_worker_service", None)
        if service is None or not service.is_running:
            return False, "worker_unavailable"
        try:
            with configuration.runtime.session_factory() as session:
                readiness = check_credential_registry_readiness(
                    session, configuration.registry_capabilities
                )
        except Exception:
            return False, "not_ready"
        if not readiness.ready:
            return False, "not_ready"
        return True, None

    @router.get("/capabilities", name="frame_optimize_capabilities")
    async def capabilities(request: Request) -> dict[str, object]:
        await active_actor(request)
        enabled, reason = await run_owned_in_threadpool(availability, request)
        return {
            "operation": "frame.optimize",
            "enabled": enabled,
            "reason": reason,
            "supported_providers": ["openai", "deepseek", "custom"],
        }

    @router.post("", name="create_registered_frame_optimize_task")
    async def create_frame_optimize_task(request: Request) -> dict[str, object]:
        actor = await active_actor(request)
        enabled, _reason = await run_owned_in_threadpool(availability, request)
        if not enabled or configuration is None:
            raise HTTPException(status_code=503, detail="分镜优化服务暂不可用")
        try:
            raw = await request.json()
            if not isinstance(raw, Mapping):
                raise HTTPException(status_code=422, detail="请求格式无效")
            payload = BatchOptimizeRequest.model_validate(raw)
        except ValidationError as error:
            raise HTTPException(status_code=422, detail=error.errors()) from error
        except (ValueError, UnicodeDecodeError) as error:
            raise HTTPException(status_code=422, detail="请求格式无效") from error
        try:
            return await run_owned_in_threadpool(
                admit_frame_optimize,
                session_factory,
                actor.user_id,
                payload,
                registry_capabilities=configuration.registry_capabilities,
                max_user_concurrency=configuration.max_user_concurrency,
                id_factory=configuration.runtime.id_factory,
                clock=configuration.runtime.db_clock,
            )
        except ChatTextGenerationError as error:
            raise HTTPException(status_code=error.status_code, detail=error.detail) from error

    return router


__all__ = ("build_frame_optimize_router",)
