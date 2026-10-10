from __future__ import annotations

import ast
import asyncio
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import text

from chapter_asset_replacement_support import create_owner_database
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


def test_integrated_registry_has_91_methods():
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
    assert len(registered_methods) == 91


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


@pytest.fixture
def task_observation_owner_database(tmp_path):
    database = create_owner_database(tmp_path / "task-observation-app.sqlite")
    try:
        yield database
    finally:
        database.close()


def task_observation_request(app, method: str, path: str):
    async def send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://task-observation.test",
        ) as client:
            return await client.request(
                method,
                path,
                headers={"authorization": "Bearer test-token"},
            )

    return asyncio.run(send())


def test_task_observation_uses_distinct_account_and_active_authentication(
    task_observation_owner_database,
    monkeypatch,
):
    from haoai_backend.app import create_app
    from haoai_backend.authentication.application import AuthenticationService
    from haoai_backend.authentication.configuration import AuthenticationRuntime
    from haoai_backend.shared.identity import TrustedActor

    database = task_observation_owner_database
    authentication_calls = []
    authentication_lifecycle = []
    active_resolver_calls = []
    runtime_factory_calls = []
    business_factory_calls = []

    @contextmanager
    def authenticated(self, authorization, *, require_membership=False):
        authentication_calls.append((authorization, require_membership))
        authentication_lifecycle.append("auth.enter")
        try:
            yield SimpleNamespace(user=SimpleNamespace(id="user-a"))
        finally:
            authentication_lifecycle.append("auth.exit")

    monkeypatch.setattr(AuthenticationService, "authenticated", authenticated)

    def unused_runtime_factory():
        runtime_factory_calls.append(True)
        raise AssertionError("explicit application Session factory must take precedence")

    def explicit_session_factory():
        business_factory_calls.append("business.session")
        return database.session_factory()

    async def resolve_legacy_active_actor(request):
        active_resolver_calls.append(request.url.path)
        return TrustedActor("user-b")

    app = create_app(
        session_factory=explicit_session_factory,
        resolve_actor=resolve_legacy_active_actor,
        authentication_runtime=AuthenticationRuntime(session_factory=unused_runtime_factory),
    )

    account_receipt = task_observation_request(app, "GET", "/api/chat/tasks?task_id=task-a")
    assert account_receipt.status_code == 200
    assert account_receipt.json()["id"] == "task-a"
    assert account_receipt.json()["status"] == "processing"
    assert authentication_lifecycle == ["auth.enter", "auth.exit"]
    assert business_factory_calls == ["business.session"]

    active_list = task_observation_request(app, "GET", "/api/chat/tasks/list")
    assert active_list.status_code == 200
    assert active_list.json()["total"] == 0
    assert active_resolver_calls == ["/api/chat/tasks/list"]
    assert authentication_calls == [("Bearer test-token", False)]
    assert runtime_factory_calls == []

    active_fallback_app = create_app(
        session_factory=explicit_session_factory,
        authentication_runtime=AuthenticationRuntime(session_factory=unused_runtime_factory),
    )
    active_fallback = task_observation_request(
        active_fallback_app,
        "GET",
        "/api/chat/tasks/list",
    )
    assert active_fallback.status_code == 200
    assert active_fallback.json()["total"] == 1
    assert authentication_calls[-1] == ("Bearer test-token", True)
    assert authentication_lifecycle[-2:] == ["auth.enter", "auth.exit"]
    assert business_factory_calls == [
        "business.session",
        "business.session",
        "business.session",
    ]
    assert runtime_factory_calls == []
    assert all(session.close_calls == 1 for session in database.session_factory.created)


def test_task_observation_preserves_falsey_adapters_and_cancel_order(
    task_observation_owner_database,
):
    from haoai_backend.app import create_app
    from haoai_backend.shared.identity import TrustedActor

    database = task_observation_owner_database
    events = []

    class FalseyResolver:
        def __init__(self, name: str) -> None:
            self.name = name

        def __bool__(self) -> bool:
            return False

        async def __call__(self, request):
            events.append(f"{self.name}.resolve")
            return TrustedActor("user-a")

    class Signal:
        def set(self) -> None:
            events.append("signal.set")

    class FalseySignalRegistry:
        def __bool__(self) -> bool:
            return False

        def get(self, task_id: str):
            events.append(("signal.get", task_id))
            return Signal()

    class Result:
        rowcount = 0

    class Connection:
        def execute(self, statement, parameters):
            events.append(("sql.execute", str(statement), parameters))
            return Result()

    class FalseyConnectionFactory:
        def __bool__(self) -> bool:
            return False

        def __call__(self):
            events.append("connection.factory")

            @contextmanager
            def connection_scope():
                events.append("connection.open")
                yield Connection()
                events.append("connection.close")

            return connection_scope()

    connection_factory = FalseyConnectionFactory()
    app = create_app(
        session_factory=database.session_factory,
        resolve_task_account=FalseyResolver("account"),
        resolve_task_active=FalseyResolver("active"),
        task_cancellation_connection_factory=connection_factory,
        task_cancellation_signals=FalseySignalRegistry(),
    )
    assert events == []
    assert database.session_factory.created == []

    account_receipt = task_observation_request(app, "GET", "/api/chat/tasks?task_id=task-a")
    assert account_receipt.status_code == 200
    assert events == ["account.resolve"]

    active_list = task_observation_request(app, "GET", "/api/chat/tasks/list")
    assert active_list.status_code == 200
    assert events[-1] == "active.resolve"

    events.clear()
    cancelled = task_observation_request(
        app,
        "POST",
        "/api/chat/batch-optimize/task-a/cancel",
    )
    assert cancelled.status_code == 200
    assert cancelled.json() == {"task_id": "task-a", "status": "cancelling"}
    assert events[0] == "active.resolve"
    assert events[1] == ("signal.get", "task-a")
    assert events[2] == "signal.set"
    assert events[3:6] == ["connection.factory", "connection.open", (
        "sql.execute",
        "UPDATE ai_tasks SET status='cancelling' WHERE id=:id AND status IN ('queued','processing')",
        {"id": "task-a"},
    )]
    assert events[6] == "connection.close"
    assert all(session.close_calls == 1 for session in database.session_factory.created)
