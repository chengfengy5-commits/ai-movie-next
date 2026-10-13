from __future__ import annotations

import ast
from pathlib import Path

import httpx
from fastapi import FastAPI

from haoai_backend.admin_system_config.http import build_admin_system_config_router


PACKAGE = Path(__file__).parents[1] / "src" / "haoai_backend" / "admin_system_config"


def test_domain_ports_application_imports_are_framework_free_and_environment_inert() -> None:
    forbidden_imports = {"fastapi", "sqlalchemy", "cryptography"}
    for name in ("domain.py", "ports.py", "application.py"):
        tree = ast.parse((PACKAGE / name).read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert imported.isdisjoint(forbidden_imports), (name, imported)

    for source_path in PACKAGE.glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                function = node.func
                if (
                    isinstance(function, ast.Attribute)
                    and isinstance(function.value, ast.Name)
                    and function.value.id == "os"
                ):
                    assert function.attr not in {"getenv", "environ"}, source_path.name
                if isinstance(function, ast.Attribute) and function.attr in {
                    "create_all",
                    "create_engine",
                }:
                    assert source_path.name not in {"tables.py", "persistence.py"}


def test_cold_router_returns_503_without_opening_sessions() -> None:
    app = FastAPI()
    app.include_router(
        build_admin_system_config_router(
            authentication_service=None,
            session_factory=None,
        )
    )
    async def send() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://cold-admin-system-config.test",
        ) as client:
            return await client.get("/api/admin/system-config")

    import asyncio

    response = asyncio.run(send())
    assert response.status_code == 503
    assert response.json()["detail"] == "系统配置服务尚未接线"


def test_table_module_has_no_database_bootstrap_or_runtime_engine() -> None:
    tree = ast.parse((PACKAGE / "tables.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {"create_all", "create_engine"}
    assert "sqlalchemy" in imported
    assert "os" not in imported
