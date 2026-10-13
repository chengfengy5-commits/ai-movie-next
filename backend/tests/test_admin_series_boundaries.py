from __future__ import annotations

import ast
from pathlib import Path

from fastapi import FastAPI
from sqlalchemy import inspect

from haoai_backend.admin_series.domain import AdminSeriesActor
from haoai_backend.admin_series.http import build_admin_series_router
from haoai_backend.admin_series.tables import (
    AssetDuplicateExclusionRow,
    Base,
    CharacterRow,
    ChapterRow,
    ChatMessageRow,
    FusedAssetRow,
    PropRow,
    SceneRow,
    SeriesRow,
    StoryboardAssetRow,
    TeamReferenceRow,
    UserRow,
)

PACKAGE = Path(__file__).parents[1] / "src" / "haoai_backend" / "admin_series"


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_domain_application_and_ports_are_framework_and_orm_free():
    for filename in ("domain.py", "ports.py", "application.py"):
        used = imported_modules(PACKAGE / filename)
        assert not any(
            name == "fastapi"
            or name.startswith("fastapi.")
            or name == "sqlalchemy"
            or name.startswith("sqlalchemy.")
            or name.startswith("app.")
            or name.startswith("haoai_backend.app")
            for name in used
        )
    for path in PACKAGE.glob("*.py"):
        assert not any(
            module.startswith("app.")
            for module in imported_modules(path)
        )


def test_router_is_inert_and_registers_only_the_four_fixed_routes():
    calls = 0

    def unused_session_factory():
        nonlocal calls
        calls += 1
        raise AssertionError("router construction must not open a session")

    router = build_admin_series_router(
        authentication_service=None,
        session_factory=unused_session_factory,
    )
    routes = [
        route
        for route in router.routes
        if route.path.startswith("/api/admin/series")
    ]
    assert {
        (method, route.path)
        for route in routes
        for method in route.methods
    } == {
        ("GET", "/api/admin/series"),
        ("GET", "/api/admin/series/{series_id}"),
        ("PUT", "/api/admin/series/{series_id}"),
        ("POST", "/api/admin/series"),
    }
    assert all(route.response_model is None for route in routes)
    assert calls == 0
    app = FastAPI()
    app.include_router(router)
    assert app.router.on_startup == []


def test_private_mapping_keeps_all_legacy_columns_and_exact_relationship_graph():
    assert len(SeriesRow.__table__.columns) == 11
    assert len(UserRow.__table__.columns) == 11
    assert set(inspect(SeriesRow).relationships.keys()) == {
        "user",
        "chapters",
        "characters",
        "scenes",
        "props",
        "storyboard_assets",
        "fused_assets",
        "duplicate_exclusions",
    }
    assert set(inspect(ChapterRow).relationships.keys()) == {
        "series",
        "chat_messages",
    }
    assert "team" not in inspect(SeriesRow).relationships
    assert not hasattr(SeriesRow, "team")
    assert {
        foreign_key.target_fullname
        for foreign_key in SeriesRow.team_id.property.columns[0].foreign_keys
    } == {"teams.id"}
    for row_type in (CharacterRow, SceneRow, PropRow):
        assert {"aliases", "canonical_key"} <= set(row_type.__table__.columns.keys())
    assert SeriesRow.chapters.property.cascade.delete_orphan
    assert ChapterRow.chat_messages.property.cascade.delete_orphan
    assert {
        foreign_key.target_fullname
        for foreign_key in AssetDuplicateExclusionRow.created_by.property.columns[0].foreign_keys
    } == {"users.id"}
    assert set(TeamReferenceRow.__table__.columns.keys()) == {"id"}
    assert Base.metadata is SeriesRow.metadata


def test_actor_is_immutable_and_module_specific():
    actor = AdminSeriesActor(user_id="admin", is_superuser=True)
    assert actor.user_id == "admin" and actor.is_superuser
    assert AdminSeriesActor.__dataclass_params__.frozen is True
