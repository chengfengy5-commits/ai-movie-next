"""Stable business errors exposed by administrator series routes."""

from __future__ import annotations

from haoai_backend.shared.errors import BusinessError


class AdminSeriesError(BusinessError):
    """Base error for the administrator series boundary."""


class AdminSeriesForbidden(AdminSeriesError):
    def __init__(self) -> None:
        super().__init__(403, "权限不足，仅管理员可执行此操作")


class AdminSeriesNotFound(AdminSeriesError):
    def __init__(self) -> None:
        super().__init__(404, "剧集不存在")


class AdminSeriesUnavailable(AdminSeriesError):
    def __init__(self) -> None:
        super().__init__(503, "剧集管理服务尚未接线")
