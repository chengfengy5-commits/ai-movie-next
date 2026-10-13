from __future__ import annotations

import ast
from pathlib import Path

from fastapi import FastAPI

from haoai_backend.admin_redemption_codes.domain import AdminRedemptionCodesActor
from haoai_backend.admin_redemption_codes.http import build_admin_redemption_codes_router

PACKAGE = Path(__file__).parents[1] / "src" / "haoai_backend" / "admin_redemption_codes"


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_domain_application_ports_are_framework_and_orm_free():
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


def test_router_is_inert_and_registers_only_the_four_admin_routes():
    calls = 0

    def unused_session_factory():
        nonlocal calls
        calls += 1
        raise AssertionError("router construction must not open a session")

    router = build_admin_redemption_codes_router(
        authentication_service=None,
        session_factory=unused_session_factory,
    )
    routes = [
        route
        for route in router.routes
        if route.path.startswith("/api/admin/redemption-codes")
    ]
    assert {
        (method, route.path)
        for route in routes
        for method in route.methods
    } == {
        ("GET", "/api/admin/redemption-codes"),
        ("GET", "/api/admin/redemption-codes/{code_id}"),
        ("POST", "/api/admin/redemption-codes"),
        ("DELETE", "/api/admin/redemption-codes/{code_id}"),
    }
    assert all(route.response_model is None for route in routes)
    assert calls == 0
    app = FastAPI()
    app.include_router(router)
    assert app.router.on_startup == []


def test_actor_is_immutable_and_module_specific():
    actor = AdminRedemptionCodesActor(user_id="admin", is_superuser=True)
    assert actor.user_id == "admin" and actor.is_superuser
    assert AdminRedemptionCodesActor.__dataclass_params__.frozen is True
