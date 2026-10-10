"""HTTP routes for the legacy task-observation surface."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from haoai_backend.shared.errors import BusinessError
from haoai_backend.shared.identity import TrustedActor

from . import application
from .errors import TaskObservationUnavailable
from .ports import (
    CancellationSignalRegistry,
    TaskCancellationWriter,
    UnitOfWorkFactory,
)

ActorResolver = Callable[[Request], TrustedActor | Awaitable[TrustedActor]]


def _http_error(error: BusinessError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail=error.detail,
        headers=getattr(error, "headers", None),
    )


def build_task_observation_router(
    *,
    uow_factory: UnitOfWorkFactory | None,
    resolve_account_actor: ActorResolver | None,
    resolve_active_actor: ActorResolver | None,
    cancellation_signals: CancellationSignalRegistry | None = None,
    cancellation_writer: TaskCancellationWriter | None = None,
) -> APIRouter:
    """Build the nine routes from explicitly supplied read/auth/cancel adapters."""
    router = APIRouter(prefix="/api/chat", tags=["task-observation"])

    async def trusted_actor(
        request: Request,
        resolver: ActorResolver | None,
    ) -> TrustedActor:
        if uow_factory is None or resolver is None:
            raise _http_error(TaskObservationUnavailable())
        try:
            candidate = resolver(request)
            actor = await candidate if inspect.isawaitable(candidate) else candidate
        except BusinessError as exc:
            raise _http_error(exc) from exc
        if not isinstance(actor, TrustedActor) or not actor.user_id:
            raise _http_error(TaskObservationUnavailable("可信身份端口未正确接线"))
        return actor

    async def account_actor(request: Request) -> TrustedActor:
        return await trusted_actor(request, resolve_account_actor)

    async def active_actor(request: Request) -> TrustedActor:
        return await trusted_actor(request, resolve_active_actor)

    def invoke(call: Callable[..., Any], *args: Any) -> Any:
        try:
            return call(uow_factory, *args)
        except BusinessError as exc:
            raise _http_error(exc) from exc

    @router.get("/tasks")
    def task_receipt(
        message_id: str | None = None,
        task_id: str | None = None,
        actor: TrustedActor = Depends(account_actor),
    ) -> dict[str, Any]:
        return invoke(application.get_task_receipt, actor, task_id, message_id)

    @router.get("/submissions/{operation}")
    def submission_receipt(
        operation: str,
        idempotency_key: str,
        actor: TrustedActor = Depends(account_actor),
    ) -> dict[str, Any]:
        return invoke(application.get_submission_receipt, actor, operation, idempotency_key)

    # Keep the literal route before parameterized routes if the set grows.
    @router.get("/tasks/list")
    def task_list(
        page: int = 1,
        page_size: int = 50,
        actor: TrustedActor = Depends(active_actor),
    ) -> dict[str, Any]:
        return invoke(application.list_tasks, actor, page, page_size)

    @router.get("/tasks/{task_id}/request")
    def task_request(
        task_id: str,
        team_id: str | None = None,
        actor: TrustedActor = Depends(active_actor),
    ) -> dict[str, Any]:
        return invoke(application.get_task_request, actor, task_id, team_id)

    @router.get("/ai-review/counts")
    def ai_review_counts(
        message_id: str,
        actor: TrustedActor = Depends(active_actor),
    ) -> dict[str, Any]:
        return invoke(application.get_ai_review_counts, actor, message_id)

    @router.get("/ai-review/{task_id}")
    def ai_review_status(
        task_id: str,
        actor: TrustedActor = Depends(active_actor),
    ) -> dict[str, Any]:
        return invoke(application.get_ai_review_status, actor, task_id)

    @router.get("/batch-optimize/running")
    def batch_optimize_running(
        chapter_id: str,
        actor: TrustedActor = Depends(active_actor),
    ) -> dict[str, Any]:
        return invoke(application.get_running_batch_optimize, actor, chapter_id)

    @router.get("/batch-optimize/{task_id}/status")
    def batch_optimize_status(
        task_id: str,
        actor: TrustedActor = Depends(active_actor),
    ) -> dict[str, Any]:
        return invoke(application.get_batch_optimize_status, actor, task_id)

    @router.post("/batch-optimize/{task_id}/cancel")
    def batch_optimize_cancel(
        task_id: str,
        actor: TrustedActor = Depends(active_actor),
    ) -> dict[str, Any]:
        return invoke(
            application.request_batch_optimize_cancel,
            actor,
            task_id,
            cancellation_signals,
            cancellation_writer,
        )

    return router
