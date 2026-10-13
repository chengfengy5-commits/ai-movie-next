"""Expected HTTP failures for storyboard save routes."""

from __future__ import annotations

from haoai_backend.shared.errors import BusinessError


class StoryboardSaveChapterNotFound(Exception):
    """The requested chapter does not exist."""


class StoryboardSaveForeignAssetReference(Exception):
    """An explicitly referenced asset belongs to a different series."""

    def __str__(self) -> str:
        return "素材引用不属于当前剧集"


class StoryboardSaveUnavailable(BusinessError):
    """The route has no authenticated SQL persistence wiring."""

    def __init__(self, detail: str = "分镜保存服务尚未接线") -> None:
        super().__init__(503, detail)
