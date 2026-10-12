"""Series, chapter and storyboard source-data module."""

from importlib import import_module
from typing import Any

from .ports import SeriesGenerationWriter

__all__ = ("SeriesGenerationWriter", "stage_frame_preview", "stage_frame_result")

_GENERATION_EXPORTS = frozenset({"stage_frame_preview", "stage_frame_result"})


def __getattr__(name: str) -> Any:
    if name not in _GENERATION_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(".generation", __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
