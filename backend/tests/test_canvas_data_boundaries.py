from __future__ import annotations

import ast
from pathlib import Path

from fastapi import FastAPI

from haoai_backend.app import create_app


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "haoai_backend"


def test_canvas_domain_and_application_do_not_import_framework_or_legacy_app() -> None:
    for relative in ("canvas_data/domain.py", "canvas_data/application.py", "canvas_data/ports.py"):
        tree = ast.parse((SOURCE_ROOT / relative).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                names = {(node.module or "").split(".")[0]}
            else:
                continue
            assert "fastapi" not in names
            assert "sqlalchemy" not in names
            assert "app" not in names


def test_factory_registers_two_canvas_methods_without_startup_io() -> None:
    app = create_app()
    assert isinstance(app, FastAPI)
    methods = {
        (route.path, method)
        for route in app.routes
        for method in getattr(route, "methods", set())
    }
    assert ("/api/chapters/{chapter_id}/canvas", "GET") in methods
    assert ("/api/chapters/{chapter_id}/canvas", "PUT") in methods
    assert {("/api/download", "GET"), ("/api/sign-download-urls", "POST")} <= methods
    assert len(methods) == 97
    assert app.docs_url is app.redoc_url is app.openapi_url is None
