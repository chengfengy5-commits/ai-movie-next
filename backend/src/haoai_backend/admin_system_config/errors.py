"""Stable business failures for the administrator system configuration API."""

from haoai_backend.shared.errors import BusinessError


class AdminSystemConfigError(BusinessError):
    pass


class AdminSystemConfigForbidden(AdminSystemConfigError):
    def __init__(self) -> None:
        super().__init__(403, "权限不足，仅管理员可执行此操作")


class AdminSystemConfigNotFound(AdminSystemConfigError):
    def __init__(self) -> None:
        super().__init__(404, "配置不存在")


class AdminSystemConfigUnavailable(AdminSystemConfigError):
    def __init__(self) -> None:
        super().__init__(503, "系统配置服务尚未接线")
