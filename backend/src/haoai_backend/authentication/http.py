"""FastAPI HTTP boundary for the 11 authentication methods."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from .application import AuthenticationService
from .ports import AuthenticationRuntimePort, UnitOfWorkFactory
from .errors import AuthenticationError
from .rate_limit import RateLimitExceeded
from .schemas import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    ResetPasswordRequest,
    SendCodeRequest,
    TokenResponse,
    UserLogin,
    UserRegister,
    UserResponse,
    VerifyCodeRequest,
    authentication_validation_message,
)


def _map_authentication_error(error: AuthenticationError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail=error.detail,
        headers=error.headers,
    )


class AuthenticationRoute(APIRoute):
    """Keep legacy string validation and rate-limit bodies local to auth routes."""

    def get_route_handler(self):
        original_handler = super().get_route_handler()

        async def handle(request: Request):
            try:
                return await original_handler(request)
            except RequestValidationError as exc:
                return JSONResponse(
                    status_code=422,
                    content={"detail": authentication_validation_message(exc.errors())},
                )
            except RateLimitExceeded as exc:
                return JSONResponse(status_code=429, content={"error": exc.detail})

        return handle


def build_authentication_router(service: AuthenticationService) -> APIRouter:
    router = APIRouter(
        prefix="/api/auth",
        tags=["authentication"],
        route_class=AuthenticationRoute,
    )

    def check_rate_limit(
        request: Request,
        *,
        limit: int,
        window_seconds: int,
        description: str,
    ) -> None:
        service.check_rate_limit(
            client_host=request.client.host if request.client else None,
            path=request.url.path,
            limit=limit,
            window_seconds=window_seconds,
            description=description,
        )

    @router.post("/register", status_code=201, response_model=UserResponse)
    def register(
        request: Request,
        body: UserRegister,
    ) -> dict[str, Any]:
        try:
            check_rate_limit(request, limit=5, window_seconds=3600, description="5 per 1 hour")
            return service.register(
                username=body.username,
                email=body.email,
                password=body.password,
                email_code=body.email_code,
            )
        except AuthenticationError as exc:
            raise _map_authentication_error(exc) from exc

    @router.post("/login", response_model=TokenResponse)
    def login(
        request: Request,
        body: UserLogin,
    ) -> dict[str, Any]:
        try:
            check_rate_limit(request, limit=10, window_seconds=60, description="10 per 1 minute")
            return service.login(
                username=body.username,
                password=body.password,
                user_agent=request.headers.get("user-agent"),
                headers=request.headers,
                client_host=request.client.host if request.client else None,
            )
        except AuthenticationError as exc:
            raise _map_authentication_error(exc) from exc

    @router.get("/me", response_model=UserResponse)
    def me(request: Request) -> dict[str, Any]:
        try:
            with service.authenticated(request.headers.get("authorization", "")) as principal:
                return service.current_user_payload(principal)
        except AuthenticationError as exc:
            raise _map_authentication_error(exc) from exc

    @router.get("/sessions")
    def sessions(request: Request) -> list[dict[str, Any]]:
        try:
            with service.authenticated(request.headers.get("authorization", "")) as principal:
                return service.sessions_payload(principal)
        except AuthenticationError as exc:
            raise _map_authentication_error(exc) from exc

    @router.delete("/sessions/{session_id}")
    def revoke_session(request: Request, session_id: str) -> dict[str, Any]:
        try:
            with service.authenticated(request.headers.get("authorization", "")) as principal:
                return service.revoke_session(principal, session_id)
        except AuthenticationError as exc:
            raise _map_authentication_error(exc) from exc

    @router.put("/password")
    def change_password(request: Request, body: ChangePasswordRequest) -> dict[str, str]:
        try:
            with service.authenticated(request.headers.get("authorization", "")) as principal:
                return service.change_password(
                    principal,
                    old_password=body.old_password,
                    new_password=body.new_password,
                )
        except AuthenticationError as exc:
            raise _map_authentication_error(exc) from exc

    @router.post("/forgot-password")
    def forgot_password(
        request: Request,
        body: ForgotPasswordRequest,
    ) -> dict[str, str]:
        try:
            check_rate_limit(request, limit=3, window_seconds=3600, description="3 per 1 hour")
            return service.forgot_password(username=body.username, email=body.email)
        except AuthenticationError as exc:
            raise _map_authentication_error(exc) from exc

    @router.post("/reset-password")
    def reset_password(body: ResetPasswordRequest) -> dict[str, str]:
        try:
            return service.reset_password(token=body.token, new_password=body.new_password)
        except AuthenticationError as exc:
            raise _map_authentication_error(exc) from exc

    @router.get("/credits/me")
    def my_credits(request: Request) -> dict[str, int]:
        try:
            with service.authenticated(request.headers.get("authorization", "")) as principal:
                return service.credits(principal)
        except AuthenticationError as exc:
            raise _map_authentication_error(exc) from exc

    @router.post("/send-code")
    def send_code(body: SendCodeRequest) -> dict[str, Any]:
        try:
            return service.send_code(email=body.email, username=body.username)
        except AuthenticationError as exc:
            raise _map_authentication_error(exc) from exc

    @router.post("/verify-code")
    def verify_code(body: VerifyCodeRequest) -> dict[str, str]:
        try:
            return service.verify_code(email=body.email, code=body.code)
        except AuthenticationError as exc:
            raise _map_authentication_error(exc) from exc

    return router


def mount_authentication_routes(
    app: FastAPI,
    runtime: AuthenticationRuntimePort | None,
    *,
    unit_of_work_factory: UnitOfWorkFactory | None,
) -> AuthenticationService:
    service = AuthenticationService(runtime, unit_of_work_factory)
    app.include_router(build_authentication_router(service))
    return service
