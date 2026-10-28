"""Expected failures for chapter generation-history operations."""

from haoai_backend.shared.errors import BusinessError


class ChapterGenerationHistoryError(BusinessError):
    """A generation-history failure with a stable HTTP status and detail code."""


class HistoryForbidden(ChapterGenerationHistoryError):
    def __init__(self) -> None:
        super().__init__(403, "series_access_denied")


class HistoryNotFound(ChapterGenerationHistoryError):
    def __init__(self) -> None:
        super().__init__(404, "chapter_not_found")


class HistoryConflict(ChapterGenerationHistoryError):
    def __init__(self) -> None:
        super().__init__(409, "generation_history_conflict")


class HistoryInvalid(ChapterGenerationHistoryError):
    def __init__(self) -> None:
        super().__init__(422, "invalid_generation_history_request")


class HistoryMediaUrlTooLong(ChapterGenerationHistoryError):
    def __init__(self) -> None:
        super().__init__(422, "storyboard_media_url_too_long")


class HistoryUnavailable(ChapterGenerationHistoryError):
    def __init__(self) -> None:
        super().__init__(503, "generation_history_unavailable")
