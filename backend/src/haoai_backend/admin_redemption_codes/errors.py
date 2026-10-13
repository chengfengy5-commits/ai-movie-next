"""Stable application failures for administrator redemption-code routes."""

from __future__ import annotations

from haoai_backend.shared.errors import BusinessError


class AdminRedemptionCodesError(BusinessError):
    pass


class AdminRedemptionCodesForbidden(AdminRedemptionCodesError):
    def __init__(self) -> None:
        super().__init__(403, "权限不足，仅管理员可执行此操作")


class AdminRedemptionCodeBadRequest(AdminRedemptionCodesError):
    def __init__(self, detail: str) -> None:
        super().__init__(400, detail)


class AdminRedemptionCodeNotFound(AdminRedemptionCodesError):
    def __init__(self) -> None:
        super().__init__(404, "兑换码不存在")


class AdminRedemptionCodesUnavailable(AdminRedemptionCodesError):
    def __init__(self) -> None:
        super().__init__(503, "兑换码管理服务尚未接线")
