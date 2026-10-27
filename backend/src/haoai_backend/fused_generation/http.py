"""Authenticated HTTP adapter for legacy fused-image generation."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from haoai_backend.shared.errors import BusinessError
from haoai_backend.generation_credentials.application import CredentialRegistry
from haoai_backend.generation_credentials.domain import CredentialCrypto, HmacProofAuthority
from haoai_backend.shared.identity import TrustedActor

from .application import prepare_fused_generation
from .durable_application import (
    lookup_durable_submission,
    lookup_durable_submission_receipt,
    public_submission_receipt,
    submit_durable_fused_generation,
)
from .ports import (
    FusedGenerationImageStorage,
    FusedGenerationRuntime,
    FusedGenerationStorage,
    FusedGenerationUnitOfWorkFactory,
)
from .execution import FusedGenerationExecution
from .schemas import FusedAssetGenerateRequest

ActorResolver = Callable[[Request], TrustedActor | Awaitable[TrustedActor]]


def _http_error(error: BusinessError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail=error.detail,
        headers=getattr(error, "headers", None),
    )


def build_fused_generation_router(
    *,
    uow_factory: FusedGenerationUnitOfWorkFactory | None,
    resolve_actor: ActorResolver | None,
    runtime: FusedGenerationRuntime | None,
    resolve_receipt_actor: ActorResolver | None = None,
    storage: FusedGenerationStorage | None = None,
    inline_image_storage: FusedGenerationImageStorage | None = None,
    credential_registry: CredentialRegistry | None = None,
    credential_crypto: CredentialCrypto | None = None,
    proof_authority: HmacProofAuthority | None = None,
    execution: FusedGenerationExecution | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["fused-generation"])
    if execution is None:
        execution = FusedGenerationExecution(
            runtime=runtime,
            uow_factory=uow_factory,
            storage=storage,
            inline_image_storage=inline_image_storage,
            credential_registry=credential_registry,
            credential_crypto=credential_crypto,
            proof_authority=proof_authority,
        )
    runtime_ready = runtime is not None and callable(getattr(runtime, "generate", None))

    async def resolve_trusted_actor(
        request: Request,
        resolver: ActorResolver | None,
    ) -> TrustedActor:
        if resolver is None:
            raise HTTPException(status_code=503, detail="认证服务尚未接线")
        try:
            if inspect.iscoroutinefunction(resolver) or inspect.iscoroutinefunction(
                getattr(resolver, "__call__", None)
            ):
                candidate = resolver(request)
            else:
                candidate = await run_in_threadpool(resolver, request)
            actor = await candidate if inspect.isawaitable(candidate) else candidate
        except BusinessError as error:
            raise _http_error(error) from error
        if not isinstance(actor, TrustedActor) or not actor.user_id:
            raise HTTPException(status_code=503, detail="可信身份端口未正确接线")
        return actor

    async def trusted_actor(request: Request) -> TrustedActor:
        return await resolve_trusted_actor(request, resolve_actor)

    async def receipt_actor(request: Request) -> TrustedActor:
        return await resolve_trusted_actor(
            request,
            (
                resolve_receipt_actor
                if resolve_receipt_actor is not None
                else resolve_actor
            ),
        )

    @router.post("/fused-assets/generate", response_model=None)
    async def generate_fused_asset(
        request: Request,
        background_tasks: BackgroundTasks,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> JSONResponse | dict[str, str | None]:
        # Do not parse a potentially large/malformed body or open a business
        # Session until the authenticated-member resolver has closed its UOW.
        if uow_factory is None or not runtime_ready:
            raise HTTPException(status_code=503, detail="融合生成服务尚未接线")
        idempotency_key = request.headers.get("Idempotency-Key")
        durable_capabilities = (
            credential_registry,
            credential_crypto,
            proof_authority,
        )
        durable_ready = all(capability is not None for capability in durable_capabilities)
        durable_partial = any(capability is not None for capability in durable_capabilities)
        if durable_partial and not durable_ready:
            raise HTTPException(status_code=503, detail="融合提交服务尚未完整接线")
        if idempotency_key is not None and not durable_ready:
            raise HTTPException(status_code=503, detail="融合提交幂等服务尚未接线")
        payload = await _validated_request(request)
        if durable_ready:
            if idempotency_key is None:
                # Older callers can still submit. They participate in the same
                # durable protocol, but cannot recover an acknowledgement they
                # never keyed themselves.
                import uuid

                idempotency_key = str(uuid.uuid4())
            try:
                outcome = await run_in_threadpool(
                    submit_durable_fused_generation,
                    uow_factory,
                    registry=credential_registry,
                    crypto=credential_crypto,
                    proof_authority=proof_authority,
                    actor=actor,
                    request=payload,
                    idempotency_key=idempotency_key,
                )
            except BusinessError as error:
                raise _http_error(error) from error
            row = await run_in_threadpool(
                lookup_durable_submission,
                uow_factory,
                owner_id=actor.user_id,
                idempotency_key=idempotency_key,
            )
            if (
                row is not None
                and row.get("stage") in {"accepted", "result_known"}
                and type(row.get("submission_id")) is str
            ):
                background_tasks.add_task(
                    execution.execute_submission,
                    row["submission_id"],
                    actor.user_id,
                )
            return JSONResponse(
                status_code=outcome.status_code,
                content=dict(outcome.body),
                background=background_tasks,
            )
        try:
            prepared = await run_in_threadpool(
                prepare_fused_generation,
                uow_factory,
                actor,
                payload,
            )
        except BusinessError as error:
            raise _http_error(error) from error
        background_tasks.add_task(execution.execute, prepared)
        return {"task_id": prepared.task_id, "message_id": prepared.message_id}

    @router.get("/fused-assets/submissions", response_model=None)
    async def get_fused_submission(
        request: Request,
        idempotency_key: str,
        actor: TrustedActor = Depends(receipt_actor),
    ) -> dict[str, object]:
        if uow_factory is None:
            raise HTTPException(status_code=503, detail="融合提交查询尚未接线")
        row = await run_in_threadpool(
            lookup_durable_submission_receipt,
            uow_factory,
            owner_id=actor.user_id,
            idempotency_key=idempotency_key,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="未找到融合任务提交记录")
        return public_submission_receipt(row)

    return router


async def _validated_request(request: Request) -> FusedAssetGenerateRequest:
    try:
        body = await request.json()
    except Exception as error:
        raise HTTPException(status_code=422, detail="请求 JSON 无效") from error
    try:
        return FusedAssetGenerateRequest.model_validate(body)
    except ValidationError as error:
        raise RequestValidationError(error.errors(), body=body) from error
