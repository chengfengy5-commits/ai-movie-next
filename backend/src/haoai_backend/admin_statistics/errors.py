"""Business errors for the administrator statistics endpoint."""

from haoai_backend.shared.errors import BusinessError


class AdminStatisticsForbidden(BusinessError):
    def __init__(self) -> None:
        super().__init__(403, "权限不足，仅管理员可执行此操作")


class AdminStatisticsUnavailable(BusinessError):
    def __init__(self) -> None:
        super().__init__(503, "统计服务尚未接线")
