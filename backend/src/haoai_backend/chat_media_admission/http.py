"""Authenticated HTTP routes for single and batch chat media admission."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.generation_catalog.admission_types import AdmissionRefusal
from haoai_backend.generation_core.admission_receipts import (
    AdmissionCommitUnknown,
    AdmissionConfirmed,
    AdmissionStaged,
)
from haoai_backend.generation_core.admission_staging import (
    AdmissionStageRefusal,
    MediaSnapshotProducerBinding,
)
from haoai_backend.shared.errors import BusinessError
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.storyboard_generation.execution import run_owned_in_threadpool
from haoai_backend.task_observation.authentication import resolve_account_actor

from .application import receipt_to_wire, validate_admission_request
from .errors import ChatMediaRequestError
from .persistence import SessionFactory, lookup_committed_receipt, stage_admission
from .schemas import parse_batch_request, parse_single_request


def build_chat_media_admission_router(
    *,
    authentication_service: AuthenticationService,
    session_factory: SessionFactory | None,
    producer_binding: MediaSnapshotProducerBinding | None,
) -> APIRouter:
    router = APIRouter(prefix="/api/chat", tags=["chat-media-admission"])

    async def account_actor(request: Request) -> TrustedActor:
        try:
            return await run_owned_in_threadpool(
                resolve_account_actor,
                authentication_service,
                request.headers.get("authorization", ""),
            )
        except BusinessError as error:
            raise HTTPException(
                status_code=error.status_code,
                detail=error.detail,
                headers=getattr(error, "headers", None),
            ) from error

    async def read_json_body(request: Request) -> Mapping[str, Any]:
        try:
            body = json.loads(await request.body())
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise HTTPException(status_code=422, detail="请求格式无效") from error
        if not isinstance(body, Mapping):
            raise HTTPException(status_code=422, detail="请求格式无效")
        return body

    async def admit(
        request: Request,
        *,
        operation: str,
        video: bool = False,
        batch: bool = False,
    ) -> dict[str, Any]:
        actor = await account_actor(request)
        body = await read_json_body(request)
        try:
            payload = (
                parse_batch_request(body)
                if batch
                else parse_single_request(body, video=video)
            )
        except ChatMediaRequestError as error:
            raise HTTPException(
                status_code=error.status_code,
                detail=str(error),
            ) from error

        if session_factory is None or producer_binding is None:
            raise HTTPException(status_code=503, detail="媒体准入服务尚未接线")

        raw_key = request.headers.get("idempotency-key")
        try:
            validate_admission_request(actor, operation, raw_key, payload)
        except ChatMediaRequestError as error:
            raise HTTPException(
                status_code=error.status_code,
                detail=str(error),
            ) from error

        authorization = request.headers.get("authorization", "")
        try:
            staged_result = await run_owned_in_threadpool(
                stage_admission,
                session_factory,
                producer_binding,
                authorization=authorization,
                operation=operation,
                raw_key=raw_key or "",
                request=payload,
            )
        except BusinessError as error:
            raise HTTPException(
                status_code=error.status_code,
                detail=error.detail,
                headers=getattr(error, "headers", None),
            ) from error
        except Exception as error:
            raise HTTPException(
                status_code=503,
                detail="提交结果暂时无法安全确认",
            ) from error

        if isinstance(staged_result, AdmissionConfirmed):
            return receipt_to_wire(staged_result.receipt)
        if isinstance(staged_result, AdmissionRefusal):
            raise HTTPException(
                status_code=staged_result.status_code,
                detail=staged_result.message,
            )
        if isinstance(staged_result, AdmissionStageRefusal):
            raise HTTPException(
                status_code=staged_result.status_code,
                detail=staged_result.message,
            )
        if not isinstance(staged_result, (AdmissionStaged, AdmissionCommitUnknown)):
            raise HTTPException(status_code=503, detail="准入回执无法核实")

        try:
            confirmed = await run_owned_in_threadpool(
                lookup_committed_receipt,
                session_factory,
                staged_result,
            )
        except Exception as error:
            raise HTTPException(
                status_code=503,
                detail="提交结果暂时无法确认，请使用原幂等键查询",
            ) from error
        if isinstance(confirmed, AdmissionConfirmed):
            return receipt_to_wire(confirmed.receipt)
        raise HTTPException(
            status_code=503,
            detail="提交结果暂时无法确认，请使用原幂等键查询",
        )

    @router.post("/image", name="chat_image")
    async def chat_image(request: Request) -> dict[str, Any]:
        return await admit(request, operation="image.single")

    @router.post("/video", name="chat_video")
    async def chat_video(request: Request) -> dict[str, Any]:
        return await admit(request, operation="video.single", video=True)

    @router.post("/batch-generate-images", name="batch_generate_images")
    async def batch_generate_images(request: Request) -> dict[str, Any]:
        return await admit(request, operation="image.batch", batch=True)

    return router
