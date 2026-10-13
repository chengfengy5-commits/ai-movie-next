"""FastAPI router for authenticated local upload-file reads."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from haoai_backend.shared.errors import BusinessError

from . import application
from .domain import UploadFileActor
from .errors import UploadFilesError, UploadFilesUnavailable
from .ports import ApprovedLocalFileReader, UploadDirectoryProvider

UploadFileActorResolver = Callable[
    [Request], UploadFileActor | Awaitable[UploadFileActor]
]


def _http_error(error: BusinessError | UploadFilesError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail=error.detail,
        headers=getattr(error, "headers", None),
    )


def build_upload_files_router(
    *,
    directory_provider: UploadDirectoryProvider | None,
    file_reader: ApprovedLocalFileReader | None,
    resolve_active_actor: UploadFileActorResolver | None,
) -> APIRouter:
    """Build the authenticated route without opening SQL or touching the filesystem."""

    router = APIRouter(prefix="/uploads", tags=["files"])

    async def require_active_actor(request: Request) -> UploadFileActor:
        resolver = resolve_active_actor
        if resolver is None:
            raise _http_error(UploadFilesUnavailable())
        try:
            is_async_resolver = inspect.iscoroutinefunction(
                resolver
            ) or inspect.iscoroutinefunction(getattr(resolver, "__call__", None))
            candidate = (
                resolver(request)
                if is_async_resolver
                else await run_in_threadpool(resolver, request)
            )
            actor = await candidate if inspect.isawaitable(candidate) else candidate
        except BusinessError as exc:
            raise _http_error(exc) from exc
        if not isinstance(actor, UploadFileActor) or not actor.user_id:
            raise _http_error(UploadFilesUnavailable())
        return actor

    @router.get("/{file_path:path}")
    async def serve_file(
        file_path: str,
        actor: UploadFileActor = Depends(require_active_actor),
    ) -> FileResponse:
        del actor
        try:
            approved_file = await run_in_threadpool(
                application.resolve_upload_file,
                directory_provider,
                file_reader,
                file_path,
            )
        except UploadFilesError as exc:
            raise _http_error(exc) from exc
        return await run_in_threadpool(
            FileResponse,
            str(approved_file.path),
            stat_result=approved_file.stat,
        )

    return router
