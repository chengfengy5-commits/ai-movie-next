from __future__ import annotations

import ast
from pathlib import Path

from fastapi import FastAPI

from haoai_backend.generic_tasks.http import build_generic_tasks_router


BACKEND_ROOT = Path(__file__).parents[1]
SOURCE_ROOT = BACKEND_ROOT / "src" / "haoai_backend" / "generic_tasks"


def imports(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_application_boundary_is_framework_free_and_uses_only_public_provider_port() -> None:
    application_imports = imports(ast.parse((SOURCE_ROOT / "application.py").read_text()))
    assert not any(name.startswith("fastapi") for name in application_imports)
    assert not any(name.startswith("sqlalchemy") for name in application_imports)
    assert not any(name.startswith("haoai_backend.app") for name in application_imports)
    provider_imports = {
        name for name in application_imports | imports(ast.parse((SOURCE_ROOT / "http.py").read_text()))
        if name.startswith("haoai_backend.provider_status")
    }
    assert provider_imports == {"haoai_backend.provider_status"}


def test_private_modules_do_not_bootstrap_application_engine_or_provider_io() -> None:
    forbidden_calls = (
        "create_engine(",
        ".create_all(",
        "requests.",
        "httpx.",
        "oss2.",
        "subprocess.",
        "runner.run(",
        "download(",
    )
    for path in SOURCE_ROOT.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert not any(marker in source for marker in forbidden_calls), path.name
    imports_found = set().union(
        *(imports(ast.parse(path.read_text(encoding="utf-8"))) for path in SOURCE_ROOT.glob("*.py"))
    )
    assert not any(name.startswith("haoai_backend.app") for name in imports_found)
    assert not any(
        name.startswith("haoai_backend.provider_status.")
        for name in imports_found
    )


def test_router_construction_is_inert_and_registers_only_four_routes() -> None:
    router = build_generic_tasks_router(
        uow_factory=lambda: (_ for _ in ()).throw(AssertionError("factory must be inert")),
        resolve_active_actor=lambda _request: (_ for _ in ()).throw(AssertionError("resolver must be inert")),
    )
    app = FastAPI()
    app.include_router(router)
    routes = {
        (method, route.path)
        for route in app.routes
        if hasattr(route, "methods")
        for method in route.methods or ()
        if method not in {"HEAD", "OPTIONS"}
        and route.path.startswith("/api/tasks")
    }
    assert routes == {
        ("POST", "/api/tasks"),
        ("PUT", "/api/tasks/{task_id}"),
        ("PUT", "/api/tasks/{task_id}/progress"),
        ("GET", "/api/tasks/{task_id}/external-status"),
    }


def test_g04_calls_one_public_prepared_provider_protocol_only() -> None:
    tree = ast.parse((SOURCE_ROOT / "application.py").read_text(encoding="utf-8"))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "get_external_task_status"
    )
    calls = [
        node.func.attr
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ]
    assert calls.count("prepare") == 1
    assert calls.count("poll_once") == 1
    assert calls.count("extract_result") == 1
    assert "run" not in calls
