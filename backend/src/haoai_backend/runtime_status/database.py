"""Target-schema database readiness adapter for runtime status."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from sqlalchemy.orm import Session

from haoai_backend.generation_runtime.readiness import check_generation_readiness


class _ReadinessResult(Protocol):
    status: str


SessionFactory = Callable[[], Session]


def create_generation_schema_readiness_probe(
    session_factory: SessionFactory,
) -> Callable[[], None]:
    """Return a lazy, session-owning check of Target's foundation schema.

    This checks HaoAI-next's foundation and generation schema only. It does not
    claim parity with the legacy application's full Alembic-current check.
    Session construction and all database I/O happen only when the returned
    probe is called.
    """

    if not callable(session_factory):
        raise TypeError("session_factory must be callable")

    def probe() -> None:
        session = session_factory()
        try:
            result: _ReadinessResult = check_generation_readiness(session)
            if result.status != "ready":
                raise RuntimeError("target generation schema is not ready")
        finally:
            session.close()

    return probe


__all__ = ("SessionFactory", "create_generation_schema_readiness_probe")
