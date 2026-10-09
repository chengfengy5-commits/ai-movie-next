"""FastAPI adapter for exactly the existing personal rough-cut GET and PUT."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request

from .application import get_rough_cut, save_rough_cut
from .domain import RoughCutUpdate, UpdateFrame
from .errors import RoughCutError
from .ports import TrustedActor, UnitOfWorkFactory
from .schemas import RoughCutUpdateSchema

ActorResolver = Callable[[Request], TrustedActor | Awaitable[TrustedActor]]


def build_rough_cut_router(
    *,
    uow_factory: UnitOfWorkFactory | None,
    resolve_actor: ActorResolver | None,
) -> APIRouter:
    router = APIRouter(prefix="/api/chapters/{chapter_id}/rough-cut", tags=["rough-cut"])

    async def trusted_actor(request: Request) -> TrustedActor:
        if uow_factory is None or resolve_actor is None:
            raise HTTPException(status_code=503, detail="粗剪服务尚未接线")
        try:
            actor_value = resolve_actor(request)
            actor = await actor_value if inspect.isawaitable(actor_value) else actor_value
        except RoughCutError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        if not isinstance(actor, TrustedActor) or not actor.user_id:
            raise HTTPException(status_code=503, detail="可信身份端口未正确接线")
        return actor

    @router.get("")
    def read_rough_cut(
        chapter_id: str,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, Any]:
        try:
            return get_rough_cut(uow_factory, actor, chapter_id)
        except RoughCutError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    @router.put("")
    def write_rough_cut(
        chapter_id: str,
        update: RoughCutUpdateSchema,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, Any]:
        domain_update = RoughCutUpdate(
            expected_revision=update.expected_revision,
            frames=tuple(
                UpdateFrame(asset_id=frame.asset_id, included=frame.included)
                for frame in update.frames
            ),
        )
        try:
            return save_rough_cut(uow_factory, actor, chapter_id, domain_update)
        except RoughCutError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    return router


def mount_rough_cut_routes(
    app: FastAPI,
    *,
    uow_factory: UnitOfWorkFactory | None,
    resolve_actor: ActorResolver | None,
) -> None:
    app.include_router(
        build_rough_cut_router(uow_factory=uow_factory, resolve_actor=resolve_actor)
    )
