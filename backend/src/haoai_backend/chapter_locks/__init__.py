"""Chapter editing lock capability."""

from .authentication import resolve_chapter_lock_actor
from .domain import ChapterLockActor, ChapterLockRecord
from .http import ChapterLockActorResolver, build_chapter_locks_router

__all__ = [
    "ChapterLockActor",
    "ChapterLockActorResolver",
    "ChapterLockRecord",
    "build_chapter_locks_router",
    "resolve_chapter_lock_actor",
]
