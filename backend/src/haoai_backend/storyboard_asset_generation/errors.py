"""Stable request and provider failures for storyboard asset generation."""

from haoai_backend.shared.errors import BusinessError


class StoryboardAssetGenerationError(BusinessError):
    pass


class StoryboardAssetBadRequest(StoryboardAssetGenerationError):
    def __init__(self, detail: str) -> None:
        super().__init__(400, detail)


class StoryboardAssetForbidden(StoryboardAssetGenerationError):
    def __init__(self, detail: str = "无权访问该剧集") -> None:
        super().__init__(403, detail)


class StoryboardAssetNotFound(StoryboardAssetGenerationError):
    def __init__(self, detail: str) -> None:
        super().__init__(404, detail)


class StoryboardAssetUnavailable(StoryboardAssetGenerationError):
    def __init__(self, detail: str = "分镜资产生成服务尚未接线") -> None:
        super().__init__(503, detail)
