"""FastAPI adapter for the existing private production-notes GET and PUT."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request

from haoai_backend.shared.errors import BusinessError
from haoai_backend.shared.identity import TrustedActor

from .application import get_personal_production_notes, save_personal_production_notes
from .domain import FrameNoteChange, PersonalNotesUpdate
from .ports import NotesUnitOfWorkFactory
from .schemas import PersonalProductionNotesUpdateSchema

ActorResolver = Callable[[Request], TrustedActor | Awaitable[TrustedActor]]


def build_personal_production_notes_router(
    *,
    uow_factory: NotesUnitOfWorkFactory | None,
    resolve_actor: ActorResolver | None,
) -> APIRouter:
    router = APIRouter(
        prefix="/api/chapters/{chapter_id}/personal-production-notes",
        tags=["personal-production-notes"],
    )

    async def trusted_actor(request: Request) -> TrustedActor:
        if uow_factory is None or resolve_actor is None:
            raise HTTPException(status_code=503, detail="个人制作记录服务尚未接线")
        try:
            value = resolve_actor(request)
            actor = await value if inspect.isawaitable(value) else value
        except BusinessError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        if not isinstance(actor, TrustedActor) or not actor.user_id:
            raise HTTPException(status_code=503, detail="可信身份端口未正确接线")
        return actor

    @router.get("")
    def read_notes(
        chapter_id: str,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, Any]:
        try:
            return get_personal_production_notes(uow_factory, actor, chapter_id)
        except BusinessError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    @router.put("")
    def write_notes(
        chapter_id: str,
        update: PersonalProductionNotesUpdateSchema,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, Any]:
        domain_update = PersonalNotesUpdate(
            expected_revision=update.expected_revision,
            frames=tuple(
                FrameNoteChange(
                    storyboard_asset_id=frame.storyboard_asset_id,
                    expected_media_revision=frame.expected_media_revision,
                    status=frame.status,
                    note=frame.note,
                )
                for frame in update.frames
            ),
            has_resume_patch="resume_frame_id" in update.model_fields_set,
            resume_frame_id=update.resume_frame_id,
        )
        try:
            return save_personal_production_notes(uow_factory, actor, chapter_id, domain_update)
        except BusinessError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    return router


def mount_personal_production_notes_routes(
    app: FastAPI,
    *,
    uow_factory: NotesUnitOfWorkFactory | None,
    resolve_actor: ActorResolver | None,
) -> None:
    app.include_router(
        build_personal_production_notes_router(
            uow_factory=uow_factory,
            resolve_actor=resolve_actor,
        )
    )
