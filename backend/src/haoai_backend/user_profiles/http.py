"""FastAPI router for the two legacy-compatible user profile endpoints."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.authentication.errors import AuthenticationError

from .application import UserProfileApplication
from .authentication import authenticate_user_profile_actor
from .domain import UserProfileActor
from .errors import UserProfileForbidden, UserProfileNotFound
from .persistence import SqlAlchemyUserProfileUnitOfWork
from .schemas import ProfileUpdateRequest

SessionFactory = Callable[[], Session]
_UNAVAILABLE_DETAIL = "用户资料服务尚未接线"


def build_user_profiles_router(
    *,
    authentication_service: AuthenticationService | None = None,
    session_factory: SessionFactory | None = None,
) -> APIRouter:
    """Build an inert router; factories are called only after a request arrives."""
    router = APIRouter(prefix="/api", tags=["users"])
    application = UserProfileApplication()

    def resolve_actor(request: Request) -> UserProfileActor:
        if authentication_service is None or session_factory is None:
            raise HTTPException(status_code=503, detail=_UNAVAILABLE_DETAIL)
        try:
            return authenticate_user_profile_actor(
                authentication_service,
                request.headers.get("authorization", ""),
            )
        except AuthenticationError as error:
            raise HTTPException(
                status_code=error.status_code,
                detail=error.detail,
                headers=error.headers,
            ) from error

    def map_profile_error(error: Exception) -> HTTPException:
        if isinstance(error, UserProfileNotFound):
            return HTTPException(status_code=404, detail=str(error))
        if isinstance(error, UserProfileForbidden):
            return HTTPException(status_code=403, detail=str(error))
        raise error

    @router.get("/users/{user_id}/card")
    def get_user_card(
        user_id: str,
        actor: UserProfileActor = Depends(resolve_actor),
    ) -> dict[str, Any]:
        session = session_factory()
        try:
            unit_of_work = SqlAlchemyUserProfileUnitOfWork(session)
            try:
                return application.get_user_card(
                    unit_of_work,
                    viewer=actor,
                    target_user_id=user_id,
                )
            except (UserProfileNotFound, UserProfileForbidden) as error:
                raise map_profile_error(error) from error
        finally:
            session.close()

    @router.put("/users/me/profile")
    def update_my_profile(
        body: ProfileUpdateRequest,
        actor: UserProfileActor = Depends(resolve_actor),
    ) -> dict[str, Any]:
        session = session_factory()
        try:
            unit_of_work = SqlAlchemyUserProfileUnitOfWork(session)
            return application.update_my_profile(
                unit_of_work,
                actor=actor,
                requested_bio=body.bio,
            )
        finally:
            session.close()

    return router
