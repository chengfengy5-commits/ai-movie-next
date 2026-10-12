"""Framework-independent failures for chapter lock operations."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from haoai_backend.shared.errors import BusinessError


class ChapterLockError(BusinessError):
    """An expected chapter lock failure."""


class ChapterLockUnavailable(ChapterLockError):
    def __init__(self, detail: str = "章节锁服务尚未接线") -> None:
        super().__init__(503, detail)


class ChapterNotFound(ChapterLockError):
    def __init__(self) -> None:
        super().__init__(404, "章节不存在")


class SeriesNotFound(ChapterLockError):
    def __init__(self) -> None:
        super().__init__(404, "剧集不存在")


class SeriesAccessDenied(ChapterLockError):
    def __init__(self, detail: str) -> None:
        super().__init__(403, detail)


class LockReleaseForbidden(ChapterLockError):
    def __init__(self) -> None:
        super().__init__(403, "只能释放自己持有的锁")


class LockOccupied(ChapterLockError):
    def __init__(self, username: str, expires_at: datetime | None) -> None:
        expires = expires_at.isoformat() if expires_at is not None else None
        self.payload: dict[str, Any] = {
            "detail": f"该章节正在被 {username} 编辑",
            "locked_by": username,
            "expires_at": expires,
        }
        super().__init__(409, self.payload["detail"])
