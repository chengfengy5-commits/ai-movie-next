"""Stable business failures for storyboard generation."""

from haoai_backend.shared.errors import BusinessError


class StoryboardGenerationError(BusinessError):
    pass


class StoryboardGenerationBadRequest(StoryboardGenerationError):
    def __init__(self, detail: str) -> None:
        super().__init__(400, detail)


class StoryboardGenerationForbidden(StoryboardGenerationError):
    def __init__(self, detail: str = "无权访问该剧集") -> None:
        super().__init__(403, detail)


class StoryboardGenerationNotFound(StoryboardGenerationError):
    def __init__(self, detail: str) -> None:
        super().__init__(404, detail)


class StoryboardGenerationUnavailable(StoryboardGenerationError):
    def __init__(self, detail: str = "分镜生成服务尚未接线") -> None:
        super().__init__(503, detail)


class StoryboardGenerationTaskConflict(StoryboardGenerationError):
    def __init__(self, detail: str = "任务不存在、已终结或无法由当前用户操作") -> None:
        super().__init__(409, detail)


class StoryboardProviderFailure(Exception):
    """Provider failure with a user-displayable detail."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail
