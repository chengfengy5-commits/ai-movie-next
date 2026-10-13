"""Stable failures for the legacy redemption-code endpoints."""

from __future__ import annotations

from haoai_backend.shared.errors import BusinessError


class RedemptionCodesError(BusinessError):
    pass


class RedemptionCodesForbidden(RedemptionCodesError):
    def __init__(self) -> None:
        super().__init__(403, "权限不足，仅管理员可执行此操作")


class RedemptionCodesBadRequest(RedemptionCodesError):
    def __init__(self, detail: str) -> None:
        super().__init__(400, detail)


class RedemptionCodeNotFound(RedemptionCodesError):
    def __init__(self) -> None:
        super().__init__(404, "兑换码不存在，请检查后重试")


class RedemptionCodeAlreadyUsed(RedemptionCodesError):
    def __init__(self) -> None:
        super().__init__(400, "该兑换码已被使用")


class RedemptionCodeExpired(RedemptionCodesError):
    def __init__(self) -> None:
        super().__init__(400, "该兑换码已过期")


class RedemptionCodeTypeUnknown(RedemptionCodesError):
    def __init__(self) -> None:
        super().__init__(500, "未知的兑换码类型")


class RedemptionCodesUnavailable(RedemptionCodesError):
    def __init__(self) -> None:
        super().__init__(503, "兑换码服务尚未接线")
