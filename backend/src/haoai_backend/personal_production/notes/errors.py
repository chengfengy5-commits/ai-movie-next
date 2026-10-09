"""Business and reconciliation errors for personal production notes."""

from __future__ import annotations

from haoai_backend.shared.errors import BusinessError


class NotesError(BusinessError):
    """Base for expected HTTP-facing notes failures."""


class NotesNotFound(NotesError):
    def __init__(self, detail: str = "章节不存在") -> None:
        super().__init__(404, detail)


class NotesUnauthorized(NotesError):
    def __init__(self, detail: str = "登录状态已失效") -> None:
        super().__init__(401, detail)


class NotesUnavailable(NotesError):
    def __init__(self, detail: str = "章节不存在或暂不可读取") -> None:
        super().__init__(404, detail)


class NotesConflict(NotesError):
    def __init__(self, detail: str) -> None:
        super().__init__(409, detail)


class InvalidNotesUpdate(NotesError):
    def __init__(self, detail: str) -> None:
        super().__init__(422, detail)


class ChapterMediaStateError(RuntimeError):
    """Current media facts or a reconciliation CAS could not be read safely."""
