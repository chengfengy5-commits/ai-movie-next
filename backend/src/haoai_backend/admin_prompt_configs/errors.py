"""Stable application failures for administrator prompt configs."""

from __future__ import annotations

from haoai_backend.shared.errors import BusinessError


class AdminPromptConfigsError(BusinessError):
    pass


class AdminPromptConfigsForbidden(AdminPromptConfigsError):
    def __init__(self) -> None:
        super().__init__(403, "权限不足，仅管理员可执行此操作")


class AdminPromptConfigNotFound(AdminPromptConfigsError):
    def __init__(self) -> None:
        super().__init__(404, "提示词不存在")


class AdminPromptConfigsUnavailable(AdminPromptConfigsError):
    def __init__(self) -> None:
        super().__init__(503, "提示词配置服务尚未接线")
