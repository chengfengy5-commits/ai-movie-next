"""Authentication-only Pydantic request and response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from .avatar import resolve_avatar_url


class AuthRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")


class UserRegister(AuthRequest):
    username: str
    email: str
    password: str
    email_code: str = ""

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        from .domain import validate_complex_password

        validate_complex_password(value)
        return value


class UserLogin(AuthRequest):
    username: str
    password: str


class ChangePasswordRequest(AuthRequest):
    old_password: str
    new_password: str


class ForgotPasswordRequest(AuthRequest):
    username: str
    email: str


class ResetPasswordRequest(AuthRequest):
    token: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        from .domain import validate_complex_password

        validate_complex_password(value)
        return value


class SendCodeRequest(AuthRequest):
    email: str
    username: str = ""


class VerifyCodeRequest(AuthRequest):
    email: str
    code: str


class UserResponse(BaseModel):
    id: str
    username: str
    email: str
    is_superuser: bool = False
    membership_type: str = "free"
    membership_expires_at: datetime | None = None
    avatar_url: str | None = None
    bio: str | None = None
    created_at: datetime

    @model_validator(mode="after")
    def resolve_avatar(self) -> UserResponse:
        self.avatar_url = resolve_avatar_url(self.email)
        return self

    model_config = ConfigDict(from_attributes=True)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


def authentication_validation_message(errors: list[dict[str, Any]]) -> str:
    for error in errors:
        message = str(error.get("msg", ""))
        if message.startswith("Value error, "):
            message = message[13:]
        if message:
            return message
    return "输入数据不合法，请检查后重试"
