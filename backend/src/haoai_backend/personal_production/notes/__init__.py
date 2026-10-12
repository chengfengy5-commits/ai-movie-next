"""Personal production notes: identity, media maintenance, and private patches."""

from importlib import import_module
from typing import Any

from .ports import NotesGenerationWriter

__all__ = ("MediaReconciliationOutcome", "NotesGenerationWriter", "stage_media_reconciliation")

_FACADE_EXPORTS = frozenset({"MediaReconciliationOutcome", "stage_media_reconciliation"})


def __getattr__(name: str) -> Any:
    if name not in _FACADE_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(".facade", __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
