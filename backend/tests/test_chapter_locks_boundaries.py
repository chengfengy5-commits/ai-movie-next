"""Layering and cold-construction checks for chapter locks."""

from __future__ import annotations

import ast
from pathlib import Path

from haoai_backend.app import create_app

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "haoai_backend"
PURE_FILES = (
    "chapter_locks/domain.py",
    "chapter_locks/errors.py",
    "chapter_locks/ports.py",
    "chapter_locks/authentication.py",
    "chapter_locks/application.py",
)


def imported_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def test_pure_chapter_lock_layers_do_not_import_framework_adapters() -> None:
    for relative in PURE_FILES:
        roots = imported_roots(
            ast.parse((SOURCE_ROOT / relative).read_text(encoding="utf-8"))
        )
        assert "fastapi" not in roots, relative
        assert "sqlalchemy" not in roots, relative
        assert "app" not in roots, relative


def test_factory_registers_only_the_three_chapter_lock_methods_without_io() -> None:
    app = create_app()
    lock_routes = {
        (route.path, method)
        for route in app.routes
        if route.path == "/api/chapters/{chapter_id}/lock"
        for method in getattr(route, "methods", set())
    }

    assert lock_routes == {
        ("/api/chapters/{chapter_id}/lock", "POST"),
        ("/api/chapters/{chapter_id}/lock", "GET"),
        ("/api/chapters/{chapter_id}/lock", "DELETE"),
    }
    assert app.openapi_url is None
    assert app.docs_url is None
    assert app.redoc_url is None


def test_persistence_adapter_uses_only_supplied_session() -> None:
    source = (SOURCE_ROOT / "chapter_locks" / "persistence.py").read_text(
        encoding="utf-8"
    )

    assert "create_engine(" not in source
    assert ".create_all(" not in source
    assert "Session(" not in source
    assert "sessionmaker(" not in source
