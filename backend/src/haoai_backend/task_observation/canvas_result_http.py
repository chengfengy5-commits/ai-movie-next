"""Authenticated HTTP route for one owned Canvas application recovery point."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError

from haoai_backend.shared.errors import BusinessError
from haoai_backend.shared.identity import TrustedActor

from .canvas_result_lookup import (
    SessionFactory,
    is_valid_canvas_identifier,
    lookup_canvas_result_application,
)
from .errors import TaskObservationBadRequest, TaskObservationUnavailable

ActorResolver = Callable[[Request], TrustedActor | Awaitable[TrustedActor]]


def _http_error(error: BusinessError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail=error.detail,
        headers=getattr(error, "headers", None),
    )


def build_canvas_result_http_router(
    *,
    session_factory: SessionFactory | None,
    resolve_account_actor: ActorResolver | None,
) -> APIRouter:
    router = APIRouter(tags=["canvas-owned-result"])

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

    @router.get(
        "/api/chapters/{chapter_id}/canvas-owned-results/{result_ref}/application"
    )
    def application_lookup(
        chapter_id: str,
        result_ref: str,
        request: Request,
        actor: TrustedActor = Depends(account_actor),
    ) -> dict[str, object]:
        query = request.query_params
        if set(query.keys()) != {"task_id"} or len(query.getlist("task_id")) != 1:
            raise _http_error(TaskObservationBadRequest("查询参数必须且只能包含 task_id"))
        task_id = query.getlist("task_id")[0]
        if not all(
            is_valid_canvas_identifier(value)
            for value in (chapter_id, result_ref, task_id)
        ):
            raise _http_error(TaskObservationBadRequest("画布结果标识必须为 1 到 36 个有效字符"))
        try:
            return lookup_canvas_result_application(
                session_factory,
                actor,
                chapter_id,
                result_ref,
                task_id,
            )
        except BusinessError as exc:
            raise _http_error(exc) from exc
        except SQLAlchemyError as exc:
            raise _http_error(TaskObservationUnavailable("数据库暂时不可用")) from exc

    return router


__all__ = ("build_canvas_result_http_router",)
