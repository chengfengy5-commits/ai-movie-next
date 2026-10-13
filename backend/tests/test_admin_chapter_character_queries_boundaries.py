from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from haoai_backend.admin_chapter_character_queries.domain import (
    AdminChapterCharacterActor,
)
from haoai_backend.admin_chapter_character_queries.http import (
    build_admin_chapter_character_queries_router,
)


def test_router_is_inert_and_exposes_only_the_four_legacy_paths() -> None:
    calls: list[str] = []

    def session_factory():
        calls.append("session")
        raise AssertionError("router construction must be inert")

    router = build_admin_chapter_character_queries_router(
        authentication_service=None,
        session_factory=session_factory,
    )
    assert calls == []
    assert {
        (tuple(route.methods or ()), route.path)
        for route in router.routes
    } == {
        (("GET",), "/api/admin/chapters"),
        (("GET",), "/api/admin/chapters/{chapter_id}"),
        (("GET",), "/api/admin/characters"),
        (("GET",), "/api/admin/characters/{character_id}"),
    }


def test_actor_is_module_local_immutable_and_pure_layers_avoid_framework_imports() -> None:
    actor = AdminChapterCharacterActor("admin-1", True)
    with pytest.raises(FrozenInstanceError):
        actor.is_superuser = False  # type: ignore[misc]

    package = Path(__file__).parents[1] / "src/haoai_backend/admin_chapter_character_queries"
    for name in ("domain.py", "ports.py", "application.py", "errors.py"):
        source = (package / name).read_text()
        assert "fastapi" not in source.lower()
        assert "legacy" not in source.lower()
        assert "haoai_backend.app" not in source
