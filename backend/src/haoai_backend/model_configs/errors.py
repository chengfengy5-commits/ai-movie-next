"""Stable business failures for model-configuration operations."""

from __future__ import annotations

from haoai_backend.shared.errors import BusinessError


class ModelConfigsError(BusinessError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(status_code, detail)


class ModelConfigsUnavailable(ModelConfigsError):
    def __init__(self) -> None:
        super().__init__(503, "模型配置服务尚未接线")


class ModelConfigNotFound(ModelConfigsError):
    def __init__(self, detail: str = "模型配置不存在") -> None:
        super().__init__(404, detail)


class ModelConfigsForbidden(ModelConfigsError):
    def __init__(self) -> None:
        super().__init__(403, "权限不足，仅管理员可执行此操作")
