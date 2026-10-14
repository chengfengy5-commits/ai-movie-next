"""Stable failures for administrator user management."""

from haoai_backend.shared.errors import BusinessError


class AdminUsersUnavailable(BusinessError):
    def __init__(self) -> None:
        super().__init__(503, "后台用户服务尚未接线")


class AdminUsersForbidden(BusinessError):
    def __init__(self) -> None:
        super().__init__(403, "权限不足，仅管理员可执行此操作")


class AdminUserNotFound(BusinessError):
    def __init__(self) -> None:
        super().__init__(404, "用户不存在")


class AdminUsersBadRequest(BusinessError):
    def __init__(self, detail: str) -> None:
        super().__init__(400, detail)


class AdminUserDeleteConflict(BusinessError):
    def __init__(self) -> None:
        super().__init__(409, "当前用户不满足删除约束，暂时无法删除，请核对关联数据。")
