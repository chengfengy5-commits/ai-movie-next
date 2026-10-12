"""Stable business failures for prompt configuration use cases."""

from __future__ import annotations

from haoai_backend.shared.errors import BusinessError


class PromptsError(BusinessError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(status_code, detail)


class PromptsUnavailable(PromptsError):
    def __init__(self) -> None:
        super().__init__(503, "提示词服务尚未接线")


class PromptNotFound(PromptsError):
    def __init__(self, detail: str = "提示词配置不存在") -> None:
        super().__init__(404, detail)


class PromptForbidden(PromptsError):
    def __init__(self) -> None:
        super().__init__(403, "权限不足，仅管理员可执行此操作")
