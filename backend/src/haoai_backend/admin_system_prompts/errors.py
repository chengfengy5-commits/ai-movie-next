"""Stable application failures for administrator system prompts."""

from __future__ import annotations

from haoai_backend.shared.errors import BusinessError


class AdminSystemPromptsError(BusinessError):
    pass


class AdminSystemPromptsForbidden(AdminSystemPromptsError):
    def __init__(self) -> None:
        super().__init__(403, "权限不足，仅管理员可执行此操作")


class AdminSystemPromptNotFound(AdminSystemPromptsError):
    def __init__(self) -> None:
        super().__init__(404, "系统提示词不存在")


class AdminSystemPromptsUnavailable(AdminSystemPromptsError):
    def __init__(self) -> None:
        super().__init__(503, "系统提示词服务尚未接线")


class AdminSystemPromptPersistenceError(RuntimeError):
    """A committed mutation could not be confirmed by a fresh database read."""
