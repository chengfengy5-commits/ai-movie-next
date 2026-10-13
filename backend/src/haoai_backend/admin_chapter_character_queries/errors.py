"""Expected application errors for the administrator read endpoints."""

from __future__ import annotations

from haoai_backend.shared.errors import BusinessError


class AdminChapterCharacterForbidden(BusinessError):
    def __init__(self) -> None:
        super().__init__(403, "权限不足，仅管理员可执行此操作")


class ChapterNotFound(BusinessError):
    def __init__(self) -> None:
        super().__init__(404, "章节不存在")


class CharacterNotFound(BusinessError):
    def __init__(self) -> None:
        super().__init__(404, "角色不存在")


class AdminChapterCharacterQueriesUnavailable(BusinessError):
    def __init__(self) -> None:
        super().__init__(503, "章节和角色查询服务尚未接线")
