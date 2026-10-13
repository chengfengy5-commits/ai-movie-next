from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import httpx
from fastapi import FastAPI

from haoai_backend.admin_storyboard_chat_queries.http import (
    build_admin_storyboard_chat_queries_router,
)


PACKAGE = (
    Path(__file__).parents[1]
    / "src"
    / "haoai_backend"
    / "admin_storyboard_chat_queries"
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_domain_ports_and_application_remain_framework_independent() -> None:
    for filename in ("domain.py", "ports.py", "application.py", "schemas.py"):
        imports = _imports(PACKAGE / filename)
        assert not any(
            module == "fastapi"
            or module.startswith("fastapi.")
            or module == "sqlalchemy"
            or module.startswith("sqlalchemy.")
            or module.startswith("app.")
            for module in imports
        )

    for path in PACKAGE.glob("*.py"):
        imports = _imports(path)
        assert not any(
            module == "haoai_backend.app"
            or module.startswith("app.")
            for module in imports
        )


def test_router_is_cold_and_exposes_only_four_legacy_get_routes() -> None:
    calls = 0

    def unused_session_factory():
        nonlocal calls
        calls += 1
        raise AssertionError("session factory must stay cold")

    router = build_admin_storyboard_chat_queries_router(
        authentication_service=None,
        session_factory=unused_session_factory,
    )
    routes = [route for route in router.routes if route.path.startswith("/api/admin/")]
    assert {(method, route.path) for route in routes for method in route.methods} == {
        ("GET", "/api/admin/storyboard-assets"),
        ("GET", "/api/admin/storyboard-assets/{asset_id}"),
        ("GET", "/api/admin/chat-messages"),
        ("GET", "/api/admin/chat-messages/{message_id}"),
    }
    assert all(route.response_model is None for route in routes)
    assert calls == 0

    app = FastAPI()
    app.include_router(router)
    assert app.router.on_startup == []

    async def send() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://cold-admin.test",
        ) as client:
            return await client.get("/api/admin/storyboard-assets")

    response = asyncio.run(send())
    assert response.status_code == 503
    assert calls == 0
