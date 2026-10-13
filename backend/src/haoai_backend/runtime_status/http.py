"""FastAPI routes for root metadata, health, readiness, and image proxying."""

from __future__ import annotations

import inspect
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import Response as HttpResponse
from starlette.concurrency import run_in_threadpool

from haoai_backend.shared.errors import BusinessError

from .application import ReadinessGate
from .configuration import RuntimeStatusConfiguration
from .ports import (
    CurrentUserResolver,
    DatabaseReadinessProbe,
    ObjectStorageReadinessProbe,
    PublicImageFetcher,
    RemoteContentTooLarge,
    RemoteContentTypeRejected,
    RemoteFetchError,
    UnsafeRemoteURL,
)

logger = logging.getLogger(__name__)

_IMAGE_MAX_BYTES = 10 * 1024 * 1024
_IMAGE_TIMEOUT_SECONDS = 30


def _http_error(error: BusinessError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail=error.detail,
        headers=getattr(error, "headers", None),
    )


def build_runtime_status_router(
    *,
    configuration: RuntimeStatusConfiguration,
    image_fetcher: PublicImageFetcher | None,
    resolve_current_user: CurrentUserResolver | None,
    database_probe: DatabaseReadinessProbe | None,
    object_storage_probe: ObjectStorageReadinessProbe | None,
) -> APIRouter:
    """Build the legacy endpoints without opening resources during composition."""

    router = APIRouter(tags=["runtime-status"])
    readiness = ReadinessGate(
        configuration,
        database_probe=database_probe,
        object_storage_probe=object_storage_probe,
    )

    async def require_current_user(request: Request) -> Any:
        resolver = resolve_current_user
        if resolver is None:
            raise HTTPException(status_code=503, detail="认证服务尚未接线")
        try:
            is_async_resolver = inspect.iscoroutinefunction(
                resolver
            ) or inspect.iscoroutinefunction(getattr(resolver, "__call__", None))
            candidate = (
                resolver(request)
                if is_async_resolver
                else await run_in_threadpool(resolver, request)
            )
            return await candidate if inspect.isawaitable(candidate) else candidate
        except BusinessError as exc:
            raise _http_error(exc) from exc

    @router.get("/api/chat/proxy-image")
    async def proxy_image(
        url: str = Query(default=""),
        current_user: Any = Depends(require_current_user),
    ) -> HttpResponse:
        del current_user
        if not url:
            raise HTTPException(status_code=400, detail="url 参数不能为空")
        if image_fetcher is None:
            raise HTTPException(status_code=503, detail="图片代理服务尚未接线")

        try:
            result = await image_fetcher.fetch(
                url,
                max_bytes=_IMAGE_MAX_BYTES,
                timeout_seconds=_IMAGE_TIMEOUT_SECONDS,
                allowed_content_prefixes=("image/",),
            )
        except UnsafeRemoteURL as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RemoteContentTypeRejected as exc:
            raise HTTPException(status_code=415, detail="目标资源不是图片") from exc
        except RemoteContentTooLarge as exc:
            raise HTTPException(status_code=413, detail="图片大小超过 10MB") from exc
        except RemoteFetchError as exc:
            logger.exception("图片代理请求失败")
            raise HTTPException(status_code=502, detail="图片代理请求失败") from exc
        return HttpResponse(content=result.content, media_type=result.content_type)

    @router.get("/")
    def read_root() -> dict[str, str]:
        return {
            "name": configuration.service_name,
            "version": configuration.version,
            "status": "running",
            "docs_url": "/docs",
            "admin_url": "/admin",
        }

    @router.get("/health")
    def health_check() -> dict[str, str]:
        return {
            "status": "healthy",
            "service": configuration.service_name,
        }

    @router.get("/ready")
    async def ready_check(response: Response) -> dict[str, str]:
        result = await readiness.check()
        if not result.ready:
            response.status_code = 503
            return {"status": "not_ready"}
        return {"status": "ready"}

    return router


__all__ = ("build_runtime_status_router",)
