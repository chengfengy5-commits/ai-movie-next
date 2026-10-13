"""Stable application failures for model-configuration routes."""

from __future__ import annotations

from haoai_backend.shared.errors import BusinessError


class AdminModelConfigsError(BusinessError):
    pass


class AdminModelConfigsForbidden(AdminModelConfigsError):
    def __init__(self) -> None:
        super().__init__(403, "权限不足，仅管理员可执行此操作")


class AdminModelConfigNotFound(AdminModelConfigsError):
    def __init__(self) -> None:
        super().__init__(404, "模型配置不存在")


class AdminModelConfigsUnavailable(AdminModelConfigsError):
    def __init__(self) -> None:
        super().__init__(503, "模型配置服务尚未接线")
