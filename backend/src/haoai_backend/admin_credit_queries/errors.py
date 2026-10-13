"""Stable errors for administrator credit queries."""

from haoai_backend.shared.errors import BusinessError


class UserCreditNotFound(BusinessError):
    def __init__(self) -> None:
        super().__init__(404, "积分记录不存在")


class AdminCreditQueriesForbidden(BusinessError):
    def __init__(self) -> None:
        super().__init__(403, "权限不足，仅管理员可执行此操作")


class AdminCreditQueriesUnavailable(BusinessError):
    def __init__(self) -> None:
        super().__init__(503, "后台积分与流水查询服务尚未接线")
