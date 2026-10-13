"""Stable business errors for the purchase-package boundary."""

from haoai_backend.shared.errors import BusinessError


class PurchasePackageNotFound(BusinessError):
    def __init__(self) -> None:
        super().__init__(404, "套餐不存在")


class PurchasePackagesUnavailable(BusinessError):
    def __init__(self) -> None:
        super().__init__(503, "购买套餐管理服务尚未接线")


class PurchasePackageAdminForbidden(BusinessError):
    def __init__(self) -> None:
        super().__init__(403, "权限不足，仅管理员可执行此操作")
