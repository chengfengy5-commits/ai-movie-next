"""Framework-independent errors for the personal rough-cut capability."""

from __future__ import annotations

from haoai_backend.shared.errors import BusinessError


class RoughCutError(BusinessError):
    """An expected rough-cut business failure."""


class NotFound(RoughCutError):
    def __init__(self, detail: str = "章节不存在") -> None:
        super().__init__(404, detail)


class Unauthorized(RoughCutError):
    def __init__(self, detail: str = "身份验证失败") -> None:
        super().__init__(401, detail)


class Forbidden(RoughCutError):
    def __init__(self, detail: str = "无权访问此剧集") -> None:
        super().__init__(403, detail)


class Unavailable(RoughCutError):
    def __init__(self, detail: str = "粗剪服务尚未接线") -> None:
        super().__init__(503, detail)


class Conflict(RoughCutError):
    def __init__(self, detail: str = "草稿版本冲突，请刷新后重试") -> None:
        super().__init__(409, detail)


class InvalidSource(RoughCutError):
    def __init__(self, detail: str) -> None:
        super().__init__(422, detail)


class InvalidUpdate(RoughCutError):
    def __init__(self, detail: str) -> None:
        super().__init__(422, detail)


class SourceTooLarge(RoughCutError):
    def __init__(self, detail: str = "本章镜头超过粗剪上限 500") -> None:
        super().__init__(413, detail)
