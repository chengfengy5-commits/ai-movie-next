"""Stable application failures for administrator system models."""

from __future__ import annotations

from haoai_backend.shared.errors import BusinessError


class AdminSystemModelsError(BusinessError):
    pass


class AdminSystemModelsForbidden(AdminSystemModelsError):
    def __init__(self) -> None:
        super().__init__(403, "权限不足，仅管理员可执行此操作")


class AdminSystemModelNotFound(AdminSystemModelsError):
    def __init__(self) -> None:
        super().__init__(404, "系统模型不存在")


class AdminSystemModelsUnavailable(AdminSystemModelsError):
    def __init__(self) -> None:
        super().__init__(503, "系统模型服务尚未接线")


class AdminSystemModelCryptoUnavailable(AdminSystemModelsError):
    def __init__(self) -> None:
        super().__init__(503, "系统模型加密配置不可用")
