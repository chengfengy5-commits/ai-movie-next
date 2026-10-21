"""HTTP boundary for the explicit frame.review user flow."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.chat_text_generation.errors import ChatTextGenerationError
from haoai_backend.generation_core.frame_review_admission import admit_frame_review
from haoai_backend.generation_credentials.application import (
    RegistryCapabilities,
    check_credential_registry_readiness,
)
from haoai_backend.generation_credentials.domain import CredentialCrypto, HmacProofAuthority
from haoai_backend.shared.errors import BusinessError
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.storyboard_generation.execution import run_owned_in_threadpool
from haoai_backend.task_observation.authentication import resolve_active_actor

from .chat_text_generation.schemas import AIReviewRequest

SessionFactory = Callable[[], Session]


def _review_worker_capabilities_ready(
    capabilities: RegistryCapabilities,
) -> bool:
    """Match the protected dependencies required by review worker assembly."""

    return (
        isinstance(capabilities.crypto, CredentialCrypto)
        and isinstance(capabilities.proof_authority, HmacProofAuthority)
        and callable(capabilities.read_session_factory)
        and callable(capabilities.db_clock)
        and callable(capabilities.id_factory)
    )


def _registry_is_ready(
    factory: SessionFactory,
    registry_capabilities: RegistryCapabilities,
) -> bool:
    session: Session | None = None
    readiness = None
    try:
        session = factory()
        readiness = check_credential_registry_readiness(
            session, registry_capabilities
        )
    except Exception:
        pass
    finally:
        if session is not None:
            try:
                session.rollback()
            except Exception:
                readiness = None
            try:
                session.close()
            except Exception:
                readiness = None
    return readiness is not None and readiness.ready


def build_frame_review_router(
    *,
    authentication_service: AuthenticationService,
    session_factory: SessionFactory,
    registry_capabilities: RegistryCapabilities,
    enabled: bool,
    max_user_concurrency: int,
    id_factory,
    clock,
) -> APIRouter:
    """Mount a capability read and registered-only submit behind one gate."""

    router = APIRouter(prefix="/api/chat", tags=["frame-review"])

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

    async def readiness_reason(request: Request) -> str | None:
        if not enabled:
            return "not_configured"
        if not _review_worker_capabilities_ready(registry_capabilities):
            return "not_ready"
        factory = registry_capabilities.read_session_factory
        if not callable(factory):
            return "not_ready"
        if not await run_owned_in_threadpool(
            _registry_is_ready, factory, registry_capabilities
        ):
            return "not_ready"
        worker = getattr(request.app.state, "frame_review_worker_service", None)
        if worker is None or not worker.is_running:
            return "worker_unavailable"
        return None

    async def require_enabled(request: Request) -> None:
        reason = await readiness_reason(request)
        if reason is not None:
            raise HTTPException(status_code=503, detail="分镜复核服务当前不可用")

    @router.get("/frame-review/capabilities", name="frame_review_capabilities")
    async def frame_review_capabilities(request: Request) -> dict[str, object]:
        await active_actor(request)
        reason = await readiness_reason(request)
        return {
            "operation": "frame.review",
            "enabled": reason is None,
            "reason": reason,
            "supported_providers": ["openai", "deepseek", "custom"],
        }

    @router.post("/frame-review", name="create_frame_review_task")
    async def create_frame_review_task(request: Request) -> dict[str, Any]:
        actor = await active_actor(request)
        await require_enabled(request)
        try:
            value = json.loads(await request.body())
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise HTTPException(status_code=422, detail="请求格式无效") from error
        if not isinstance(value, Mapping):
            raise HTTPException(status_code=422, detail="请求格式无效")
        try:
            payload = AIReviewRequest.model_validate(value)
        except ValidationError as error:
            raise HTTPException(status_code=422, detail=error.errors()) from error
        try:
            return await run_owned_in_threadpool(
                admit_frame_review,
                session_factory,
                actor.user_id,
                payload,
                registry_capabilities=registry_capabilities,
                max_user_concurrency=max_user_concurrency,
                id_factory=id_factory,
                clock=clock,
            )
        except ChatTextGenerationError as error:
            raise HTTPException(
                status_code=error.status_code,
                detail=error.detail,
            ) from error

    return router


__all__ = ("build_frame_review_router",)
