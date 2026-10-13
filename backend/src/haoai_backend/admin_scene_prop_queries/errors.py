"""Errors exposed by the administrator scene and prop query module."""

from __future__ import annotations

from haoai_backend.shared.errors import BusinessError


class AdminScenePropQueriesForbidden(BusinessError):
    def __init__(self) -> None:
        super().__init__(403, "权限不足，仅管理员可执行此操作")


class SceneNotFound(BusinessError):
    def __init__(self) -> None:
        super().__init__(404, "场景不存在")


class PropNotFound(BusinessError):
    def __init__(self) -> None:
        super().__init__(404, "道具不存在")
