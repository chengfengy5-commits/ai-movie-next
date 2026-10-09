"""
认证相关的 Pydantic 模型
"""
from pydantic import BaseModel, field_validator, model_validator
from typing import Optional
from datetime import datetime

from app.services.avatar_service import resolve_avatar_url


class UserRegister(BaseModel):
    username: str
    email: str
    password: str
    email_code: str = ""  # 邮箱验证码
    
    @field_validator('password')
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("密码长度不能少于8位")
        if not any(c.isalpha() for c in v):
            raise ValueError("密码必须包含至少一个字母")
        if not any(c.isdigit() for c in v):
            raise ValueError("密码必须包含至少一个数字")
        return v


class UserLogin(BaseModel):
    username: str
    password: str


class UserResponse(BaseModel):
    id: str
    username: str
    email: str
    is_superuser: bool = False
    membership_type: str = "free"
    membership_expires_at: Optional[datetime] = None
    avatar_url: Optional[str] = None
    bio: Optional[str] = None
    created_at: datetime

    @model_validator(mode="after")
    def _resolve_avatar(self):
        """头像统一按邮箱解析（如 QQ 邮箱 → QQ 头像）。

        注意：这里**不再读取**历史的自定义头像值——自定义头像功能已下线，
        库里可能残留已失效的旧链接（云端尤其常见），沿用会导致前端加载破图。
        """
        self.avatar_url = resolve_avatar_url(self.email)
        return self

    class Config:
        from_attributes = True


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class ForgotPasswordRequest(BaseModel):
    username: str
    email: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str
    
    @field_validator('new_password')
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("密码长度不能少于8位")
        if not any(c.isalpha() for c in v):
            raise ValueError("密码必须包含至少一个字母")
        if not any(c.isdigit() for c in v):
            raise ValueError("密码必须包含至少一个数字")
        return v
