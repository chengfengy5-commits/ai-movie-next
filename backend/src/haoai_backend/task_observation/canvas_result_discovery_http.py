"""Authenticated HTTP surface for paged owned Canvas result discovery."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError

from haoai_backend.generation_contracts.canvas_targets import is_valid_canvas_identifier
from haoai_backend.shared.errors import BusinessError
from haoai_backend.shared.identity import TrustedActor

from .canvas_result_discovery import (
    SessionFactory,
    discover_canvas_results,
    normalize_canvas_target_selector,
)
from .errors import TaskObservationUnavailable

ActorResolver = Callable[[Request], TrustedActor | Awaitable[TrustedActor]]


def _http_error(error: BusinessError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail=error.detail,
        headers=getattr(error, "headers", None),
    )


def build_canvas_result_discovery_http_router(
    *,
    session_factory: SessionFactory | None,
    resolve_account_actor: ActorResolver | None,
) -> APIRouter:
    router = APIRouter(tags=["canvas-owned-results"])

    async def account_actor(request: Request) -> TrustedActor:
        if resolve_account_actor is None:
            raise _http_error(TaskObservationUnavailable("认证服务尚未接线"))
        try:
            candidate = resolve_account_actor(request)
            actor = await candidate if inspect.isawaitable(candidate) else candidate
        except BusinessError as exc:
            raise _http_error(exc) from exc
        if not isinstance(actor, TrustedActor) or not is_valid_canvas_identifier(
            actor.user_id
        ):
            raise _http_error(TaskObservationUnavailable("可信身份端口未正确接线"))
        return actor

    @router.get("/api/chapters/{chapter_id}/canvas-owned-results")
    def discover(
        chapter_id: str,
        request: Request,
        actor: TrustedActor = Depends(account_actor),
    ) -> dict[str, object]:
        try:
            selector, limit, cursor = normalize_canvas_target_selector(
                chapter_id,
                request.query_params.multi_items(),
            )
            return discover_canvas_results(
                session_factory,
                actor,
                chapter_id,
                selector,
                limit=limit,
                cursor=cursor,
            )
        except BusinessError as exc:
            raise _http_error(exc) from exc
        except SQLAlchemyError as exc:
            raise _http_error(TaskObservationUnavailable("数据库暂时不可用")) from exc

    return router


__all__ = ("build_canvas_result_discovery_http_router",)
