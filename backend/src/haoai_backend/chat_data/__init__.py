"""Chapter chat messages and read-only AI usage statistics."""

from importlib import import_module
from typing import Any

from .ports import ChatGenerationWriter

__all__ = ("ChatGenerationWriter", "apply_web_text")

_GENERATION_EXPORTS = frozenset({"apply_web_text"})


def __getattr__(name: str) -> Any:
    if name not in _GENERATION_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(".generation", __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
