"""FastAPI adapter for the legacy download-link endpoints."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request

from haoai_backend.shared.errors import BusinessError
from haoai_backend.shared.identity import TrustedActor

from . import application
from .compatibility import IdentityDownloadURLResolver
from .domain import (
    DISABLED_DOWNLOAD_DETAIL,
    MAX_DOWNLOAD_LINK_ITEMS,
    DownloadLinkRequestItem,
)
from .errors import DownloadLinksTooManyItems, DownloadLinksUnavailable
from .ports import DownloadURLResolver
from .schemas import DownloadLinksRequest, DownloadLinksResponse

ActorResolver = Callable[[Request], TrustedActor | Awaitable[TrustedActor]]


def build_download_links_router(
    *,
    resolve_actor: ActorResolver | None,
    url_resolver: DownloadURLResolver | None,
) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["download-links"])
    effective_url_resolver = (
        url_resolver if url_resolver is not None else IdentityDownloadURLResolver()
    )

    async def trusted_actor(request: Request) -> TrustedActor:
        if resolve_actor is None:
            error = DownloadLinksUnavailable("可信身份端口尚未接线")
            raise HTTPException(status_code=error.status_code, detail=error.detail)
        try:
            candidate = resolve_actor(request)
            actor = await candidate if inspect.isawaitable(candidate) else candidate
        except BusinessError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

        if not isinstance(actor, TrustedActor) or not actor.user_id:
            error = DownloadLinksUnavailable("可信身份端口未正确接线")
            raise HTTPException(status_code=error.status_code, detail=error.detail)
        return actor

    @router.get("/download")
    async def disabled_download(
        url: str,
        filename: str | None = None,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> None:
        del url, filename, actor
        raise HTTPException(status_code=403, detail=DISABLED_DOWNLOAD_DETAIL)

    @router.post("/sign-download-urls", response_model=DownloadLinksResponse)
    async def sign_download_urls(
        body: DownloadLinksRequest,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> DownloadLinksResponse:
        del actor
        items = [
            DownloadLinkRequestItem(url=item.url, filename=item.filename)
            for item in body.items
        ]
        if len(items) > MAX_DOWNLOAD_LINK_ITEMS:
            error = DownloadLinksTooManyItems()
            raise HTTPException(status_code=error.status_code, detail=error.detail)
        try:
            results = await application.resolve_download_links(
                effective_url_resolver,
                items,
            )
        except Exception as exc:
            raise RuntimeError("download URL resolver failed") from exc
        return DownloadLinksResponse(
            items=[
                {"url": item.url, "filename": item.filename, "signed": item.signed}
                for item in results
            ]
        )

    return router


def mount_download_links_routes(
    app: FastAPI,
    *,
    resolve_actor: ActorResolver | None,
    url_resolver: DownloadURLResolver | None,
) -> None:
    app.include_router(
        build_download_links_router(
            resolve_actor=resolve_actor,
            url_resolver=url_resolver,
        )
    )
