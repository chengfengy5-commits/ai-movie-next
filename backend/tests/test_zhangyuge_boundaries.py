from __future__ import annotations

import ast
from pathlib import Path

from fastapi.routing import APIRoute

from haoai_backend.zhangyuge.http import build_zhangyuge_router


PACKAGE = Path(__file__).parents[1] / "src" / "haoai_backend" / "zhangyuge"


def test_framework_free_modules_do_not_import_web_database_or_crypto_runtime() -> None:
    forbidden = {"fastapi", "sqlalchemy", "cryptography"}
    for name in ("domain.py", "ports.py", "application.py"):
        tree = ast.parse((PACKAGE / name).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert imported.isdisjoint(forbidden), (name, imported & forbidden)


def test_router_exposes_only_the_three_legacy_routes() -> None:
    router = build_zhangyuge_router(
        authentication_service=None,
        session_factory=None,
    )
    routes = [route for route in router.routes if isinstance(route, APIRoute)]
    assert [(tuple(route.methods or ()), route.path) for route in routes] == [
        (("GET",), "/api/zhangyuge/balance"),
        (("GET",), "/api/zhangyuge/pat"),
        (("POST",), "/api/zhangyuge/pat"),
    ]


def test_module_sources_do_not_read_runtime_environment() -> None:
    for source_path in PACKAGE.glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "os":
                    assert node.func.attr not in {"getenv", "environ"}, source_path.name
