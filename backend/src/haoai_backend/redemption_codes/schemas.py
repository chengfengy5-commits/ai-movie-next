"""Pydantic request and response schemas matching the legacy wire contract."""

from __future__ import annotations

from pydantic import BaseModel, Field


class GenerateRequest(BaseModel):
    type: str = Field(
        ...,
        pattern="^(membership|credits)$",
        description="membership=会员时长, credits=积分",
    )
    value: int = Field(..., gt=0, description="会员天数或积分数额")
    count: int = Field(default=1, ge=1, le=1000, description="生成数量（1-1000）")
    expires_at: str | None = Field(default=None, description="过期时间 ISO 格式，null=永不过期")


class RedeemRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=32, description="兑换码")


class RedemptionCodeResponse(BaseModel):
    id: str
    code: str
    type: str
    value: int
    is_used: bool
    used_by: str | None = None
    used_at: str | None = None
    created_by: str
    expires_at: str | None = None
    created_at: str


class GenerateResponse(BaseModel):
    codes: list[RedemptionCodeResponse]
    count: int
    type: str
    value: int


class RedeemResponse(BaseModel):
    message: str
    type: str
    value: int
    detail: str | None = None
