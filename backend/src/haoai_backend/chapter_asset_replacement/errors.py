"""Stable business failures for chapter asset replacement."""

from __future__ import annotations

from haoai_backend.shared.errors import BusinessError


class ChapterAssetReplacementError(BusinessError):
    """Base class for expected replacement failures."""


class ChapterAssetReplacementBadRequest(ChapterAssetReplacementError):
    def __init__(self, detail: str) -> None:
        super().__init__(400, detail)


class ChapterAssetReplacementNotFound(ChapterAssetReplacementError):
    def __init__(self, detail: str) -> None:
        super().__init__(404, detail)


class ChapterAssetReplacementUnavailable(ChapterAssetReplacementError):
    def __init__(self, detail: str = "素材替换服务尚未接线") -> None:
        super().__init__(503, detail)


class ChapterAssetReplacementFailed(ChapterAssetReplacementError):
    def __init__(self, detail: str = "章节素材替换失败") -> None:
        super().__init__(500, detail)
