from __future__ import annotations

import ast
from pathlib import Path

from fastapi import FastAPI

from haoai_backend.admin_model_configs.domain import AdminModelConfigActor
from haoai_backend.admin_model_configs.http import build_admin_model_configs_router

PACKAGE = Path(__file__).parents[1] / "src" / "haoai_backend" / "admin_model_configs"


def imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_pure_layers_avoid_frameworks_orm_and_legacy_imports():
    for filename in ("domain.py", "ports.py", "application.py"):
        used = imports(PACKAGE / filename)
        assert not any(
            name == "fastapi" or name.startswith("fastapi.")
            or name == "sqlalchemy" or name.startswith("sqlalchemy.")
            or name.startswith("app.") or name.startswith("haoai_backend.app")
            for name in used
        )
    for path in PACKAGE.glob("*.py"):
        used = imports(path)
        assert not any(name.startswith("app.") for name in used)


def test_router_is_inert_and_exposes_exact_five_legacy_routes():
    calls = 0
    def unused_session_factory():
        nonlocal calls
        calls += 1
        raise AssertionError("router construction must not open a database session")
    router = build_admin_model_configs_router(authentication_service=None, session_factory=unused_session_factory)
    routes = [route for route in router.routes if route.path.startswith("/api/admin/model-configs")]
    assert {(method, route.path) for route in routes for method in route.methods} == {
        ("GET", "/api/admin/model-configs"),
        ("GET", "/api/admin/model-configs/{model_id}"),
        ("POST", "/api/admin/model-configs"),
        ("PUT", "/api/admin/model-configs/{model_id}"),
        ("DELETE", "/api/admin/model-configs/{model_id}"),
    }
    assert all(route.response_model is None for route in routes)
    assert calls == 0
    app = FastAPI()
    app.include_router(router)
    assert app.router.on_startup == []


def test_actor_is_immutable_and_module_specific():
    actor = AdminModelConfigActor(user_id="admin", is_superuser=True)
    assert actor.user_id == "admin" and actor.is_superuser
    assert AdminModelConfigActor.__dataclass_params__.frozen is True
