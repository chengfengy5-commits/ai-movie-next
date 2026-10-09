from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import text

from haoai_backend.teams import tables
from teams_support import OWNER_METADATA, teams_database


def test_shared_import_is_inert():
    source_root = Path(__file__).resolve().parents[1] / "src"
    code = (
        "import sys\n"
        "import haoai_backend.teams as teams\n"
        "assert callable(teams.build_teams_routers)\n"
        "for family in ('management', 'series', 'reporting'):\n"
        "    assert f'haoai_backend.teams.{family}.http' not in sys.modules\n"
        "assert not any(name.startswith('haoai_backend.teams.management.') "
        "for name in sys.modules)\n"
        "assert not any(name.startswith('haoai_backend.teams.series.') "
        "for name in sys.modules)\n"
        "assert not any(name.startswith('haoai_backend.teams.reporting.') "
        "for name in sys.modules)\n"
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(source_root)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONNOUSERSITE"] = "1"
    result = subprocess.run(
        [sys.executable, "-B", "-c", code],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert result.returncode == 0, result.stderr


def test_shared_public_dependency_boundaries(teams_database):
    ports_path = Path(__file__).resolve().parents[1] / "src" / "haoai_backend" / "teams" / "ports.py"
    ports_tree = ast.parse(ports_path.read_text())
    imported_modules = set()
    for node in ast.walk(ports_tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)
    assert not any(name == "fastapi" or name.startswith("fastapi.") for name in imported_modules)
    assert not any(name == "sqlalchemy" or name.startswith("sqlalchemy.") for name in imported_modules)
    assert "haoai_backend.shared.identity" in imported_modules

    projection_names = (
        "users",
        "teams",
        "team_members",
        "team_invites",
        "chapter_locks",
        "system_configs",
    )
    for table_name in projection_names:
        projection = tables.metadata.tables[table_name]
        owner = OWNER_METADATA.tables[table_name]
        assert set(projection.columns.keys()) <= set(owner.columns.keys())

    with teams_database.engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        foreign_keys = connection.execute(text("PRAGMA foreign_key_list(team_members)")).all()
        assert {row[2] for row in foreign_keys} == {"teams", "users"}


def test_shared_owner_fixture_cleanup(teams_database):
    assert teams_database.engine.pool.checkedout() == 0
    session = teams_database.session_factory()
    try:
        session.execute(text("SELECT 1")).scalar_one()
        assert teams_database.engine.pool.checkedout() == 1
    finally:
        session.close()
    assert teams_database.engine.pool.checkedout() == 0


def test_integrated_registry_has_82_methods():
    from fastapi.routing import APIRoute

    from haoai_backend.app import create_app

    app = create_app()
    registered_methods = {
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
        if method not in {"HEAD", "OPTIONS"}
    }
    assert len(registered_methods) == 82


def test_integrated_all_25_team_methods_are_registered():
    from fastapi.routing import APIRoute

    from haoai_backend.app import create_app

    expected_team_methods = {
        ("POST", "/api/teams"),
        ("GET", "/api/teams/my"),
        ("GET", "/api/teams/{team_id}"),
        ("PUT", "/api/teams/{team_id}"),
        ("DELETE", "/api/teams/{team_id}"),
        ("POST", "/api/teams/{team_id}/leave"),
        ("DELETE", "/api/teams/{team_id}/members/{user_id}"),
        ("PUT", "/api/teams/{team_id}/members/{user_id}/role"),
        ("PUT", "/api/teams/{team_id}/members/{user_id}/permissions"),
        ("POST", "/api/teams/{team_id}/invites"),
        ("GET", "/api/teams/{team_id}/invites"),
        ("DELETE", "/api/teams/{team_id}/invites/{invite_id}"),
        ("POST", "/api/teams/join"),
        ("GET", "/api/teams/{team_id}/series"),
        ("POST", "/api/teams/{team_id}/series"),
        ("POST", "/api/series/{series_id}/share"),
        ("DELETE", "/api/series/{series_id}/share"),
        ("POST", "/api/teams/{team_id}/series/{series_id}/claim"),
        ("DELETE", "/api/teams/{team_id}/series/{series_id}/claim"),
        ("POST", "/api/teams/{team_id}/series/{series_id}/transfer"),
        ("GET", "/api/teams/{team_id}/members/{user_id}/tasks"),
        ("GET", "/api/teams/{team_id}/usage"),
        ("GET", "/api/teams/{team_id}/series/{series_id}/usage"),
        ("GET", "/api/teams/{team_id}/usage/model"),
        ("GET", "/api/teams/{team_id}/usage/export"),
    }
    app = create_app()
    actual_team_methods = {
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        and {"teams", "teams-reporting"}.intersection(route.tags)
        for method in route.methods
        if method not in {"HEAD", "OPTIONS"}
    }
    assert actual_team_methods == expected_team_methods


def test_integrated_default_factory_is_inert(monkeypatch):
    import sqlalchemy
    from sqlalchemy import MetaData
    from sqlalchemy.orm import Session

    from haoai_backend.app import create_app
    from haoai_backend.teams import InMemoryJoinQuota

    startup_calls = []
    session_factory_calls = []
    actor_resolver_calls = []

    def reject_startup(*args, **kwargs):
        startup_calls.append((args, kwargs))
        raise AssertionError("app composition must not create database resources")

    monkeypatch.setattr(sqlalchemy, "create_engine", reject_startup)
    monkeypatch.setattr(sqlalchemy.orm, "sessionmaker", reject_startup)
    monkeypatch.setattr(MetaData, "create_all", reject_startup)
    monkeypatch.setattr(Session, "__init__", reject_startup)
    monkeypatch.setattr(InMemoryJoinQuota, "__init__", reject_startup)

    def supplied_session_factory():
        session_factory_calls.append(True)
        raise AssertionError("the supplied Session factory must remain lazy")

    async def supplied_actor_resolver(request):
        actor_resolver_calls.append(request)
        raise AssertionError("the supplied actor resolver must remain lazy")

    default_app = create_app()
    configured_app = create_app(
        session_factory=supplied_session_factory,
        resolve_actor=supplied_actor_resolver,
    )

    assert default_app is not configured_app
    assert startup_calls == []
    assert session_factory_calls == []
    assert actor_resolver_calls == []
