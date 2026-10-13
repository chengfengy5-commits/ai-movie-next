from __future__ import annotations

import ast
from pathlib import Path

from fastapi import FastAPI

from haoai_backend.admin_system_prompts.domain import AdminSystemPromptActor
from haoai_backend.admin_system_prompts.http import build_admin_system_prompts_router

PACKAGE = Path(__file__).parents[1] / "src" / "haoai_backend" / "admin_system_prompts"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_pure_layers_do_not_depend_on_frameworks_or_other_private_modules() -> None:
    for filename in ("domain.py", "ports.py", "application.py"):
        imports = _imports(PACKAGE / filename)
        assert not any(
            module == "fastapi"
            or module.startswith("fastapi.")
            or module == "sqlalchemy"
            or module.startswith("sqlalchemy.")
            or module.startswith("haoai_backend.prompts")
            or module.startswith("app.")
            for module in imports
        )

    for path in PACKAGE.glob("*.py"):
        imports = _imports(path)
        assert not any(
            module.startswith("haoai_backend.prompts")
            or module.startswith("app.")
            for module in imports
        )


def test_router_construction_is_inert_and_exposes_only_five_legacy_routes() -> None:
    calls = 0

    def unused_session_factory():
        nonlocal calls
        calls += 1
        raise AssertionError("session factory must remain cold during construction")

    router = build_admin_system_prompts_router(
        authentication_service=None,
        session_factory=unused_session_factory,
    )
    assert calls == 0
    routes = [
        route
        for route in router.routes
        if getattr(route, "path", "").startswith("/api/admin/system-prompts")
    ]
    assert {(method, route.path) for route in routes for method in route.methods} == {
        ("GET", "/api/admin/system-prompts"),
        ("GET", "/api/admin/system-prompts/{prompt_id}"),
        ("POST", "/api/admin/system-prompts"),
        ("PUT", "/api/admin/system-prompts/{prompt_id}"),
        ("DELETE", "/api/admin/system-prompts/{prompt_id}"),
    }
    assert all(route.response_model is None for route in routes)
    assert calls == 0

    app = FastAPI()
    app.include_router(router)
    assert app.router.on_startup == []


def test_actor_is_immutable_and_module_specific() -> None:
    actor = AdminSystemPromptActor(user_id="admin", is_superuser=True)
    assert actor.user_id == "admin"
    assert actor.is_superuser is True
    assert AdminSystemPromptActor.__dataclass_params__.frozen is True
