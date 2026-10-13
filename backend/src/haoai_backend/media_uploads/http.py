"""FastAPI adapter for the five media upload compatibility routes."""

from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import FormData, UploadFile as StarletteUploadFile

from haoai_backend.shared.errors import BusinessError

from .application import MediaUploadApplication
from .authentication import MediaUploadActor, MediaUploadIdentityResolver
from .domain import MediaKind, upload_spec
from .errors import MediaUploadsError
from .schemas import RetryOssRequest

PROXY_DOWNLOAD_DISABLED_DETAIL = (
    "代理下载已停用（服务器不承载下载流量）：请使用 POST /api/sign-download-urls 获取签名直链，"
    "由浏览器直连 OSS 下载"
)


def build_media_uploads_router(
    *,
    application: MediaUploadApplication | None,
    resolve_active_member: MediaUploadIdentityResolver | None,
) -> APIRouter:
    """Build endpoints that authenticate before reading or validating request bodies."""

    router = APIRouter(prefix="/api", tags=["media uploads"])
    app = application or MediaUploadApplication(
        storage=None,
        public_media_fetcher=None,
    )

    async def active_member(request: Request) -> MediaUploadActor:
        resolver = resolve_active_member
        if resolver is None:
            raise HTTPException(status_code=503, detail="认证服务尚未接线")
        authorization = request.headers.get("authorization", "")
        try:
            is_async = inspect.iscoroutinefunction(resolver) or inspect.iscoroutinefunction(
                getattr(resolver, "__call__", None)
            )
            candidate = (
                resolver(authorization)
                if is_async
                else await run_in_threadpool(resolver, authorization)
            )
            actor = await candidate if inspect.isawaitable(candidate) else candidate
        except BusinessError as error:
            raise _business_http_error(error) from error
        if not isinstance(actor, MediaUploadActor) or not actor.user_id:
            raise HTTPException(status_code=503, detail="认证服务尚未接线")
        return actor

    @router.post("/upload/image", response_model=None)
    async def upload_image(
        request: Request,
        actor: MediaUploadActor = Depends(active_member),
    ) -> dict[str, str]:
        del actor
        return await _upload_form_file(request, app, "image")

    @router.post("/upload/audio", response_model=None)
    async def upload_audio(
        request: Request,
        actor: MediaUploadActor = Depends(active_member),
    ) -> dict[str, str]:
        del actor
        return await _upload_form_file(request, app, "audio")

    @router.post("/upload/video", response_model=None)
    async def upload_video(
        request: Request,
        actor: MediaUploadActor = Depends(active_member),
    ) -> dict[str, str]:
        del actor
        return await _upload_form_file(request, app, "video")

    @router.post("/upload/retry-oss", response_model=None)
    async def retry_oss_upload(
        request: Request,
        actor: MediaUploadActor = Depends(active_member),
    ) -> dict[str, str]:
        del actor
        payload = await _validated_json_body(request, RetryOssRequest)
        try:
            return await app.retry_oss_upload(payload.url)
        except MediaUploadsError as error:
            raise _media_http_error(error) from error

    @router.get("/chat/proxy-download", response_model=None)
    async def proxy_download(
        actor: MediaUploadActor = Depends(active_member),
    ) -> None:
        del actor
        raise HTTPException(status_code=403, detail=PROXY_DOWNLOAD_DISABLED_DETAIL)

    return router


async def _upload_form_file(
    request: Request,
    application: MediaUploadApplication,
    kind: MediaKind,
) -> dict[str, str]:
    try:
        form = await request.form()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=422, detail="无法解析上传文件") from None

    pending_cancellation: asyncio.CancelledError | None = None
    try:
        file = form.get("file")
        if not isinstance(file, StarletteUploadFile):
            _raise_file_validation_error(file)
        spec = upload_spec(kind, file.filename)
        content = await file.read(spec.max_bytes + 1)
        return await application.upload(kind, file.filename, content)
    except asyncio.CancelledError as cancellation:
        pending_cancellation = cancellation
        raise
    except MediaUploadsError as error:
        raise _media_http_error(error) from error
    finally:
        await _close_owned_form(form, pending_cancellation=pending_cancellation)


async def _close_owned_form(
    form: FormData,
    *,
    pending_cancellation: asyncio.CancelledError | None,
) -> None:
    """Close returned multipart files before allowing request cancellation to escape."""

    cleanup_task = asyncio.create_task(_close_form_files(form))
    try:
        await asyncio.shield(cleanup_task)
    except asyncio.CancelledError as cancellation:
        while not cleanup_task.done():
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break

        cleanup_task.result()

        # If the route is already unwinding a cancellation, returning preserves
        # that original exception while still absorbing later cancel requests.
        if pending_cancellation is None:
            raise cancellation


async def _close_form_files(form: FormData) -> None:
    first_error: BaseException | None = None
    for _field_name, value in form.multi_items():
        if not isinstance(value, StarletteUploadFile):
            continue
        try:
            await value.close()
        except BaseException as error:
            if first_error is None:
                first_error = error

    if first_error is not None:
        raise first_error


async def _validated_json_body(
    request: Request, model_type: type[RetryOssRequest]
) -> RetryOssRequest:
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        detail = [{
            "type": "json_invalid",
            "loc": ("body", getattr(error, "pos", 0)),
            "msg": "JSON decode error",
            "input": None,
            "ctx": {"error": str(error)},
        }]
        raise RequestValidationError(detail, body=None) from error
    try:
        return model_type.model_validate(body)
    except ValidationError as error:
        details = []
        for item in error.errors(include_url=False):
            details.append({**item, "loc": ("body", *item["loc"])})
        raise RequestValidationError(details, body=body) from error


def _raise_file_validation_error(value: Any) -> None:
    if value is None:
        error = {
            "type": "missing",
            "loc": ("body", "file"),
            "msg": "Field required",
            "input": None,
        }
    else:
        error = {
            "type": "value_error",
            "loc": ("body", "file"),
            "msg": "Expected an uploaded file",
            "input": value if isinstance(value, (str, int, float, bool)) else None,
        }
    raise RequestValidationError([error], body=None)


def _business_http_error(error: BusinessError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail=error.detail,
        headers=getattr(error, "headers", None),
    )


def _media_http_error(error: MediaUploadsError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=error.detail)
