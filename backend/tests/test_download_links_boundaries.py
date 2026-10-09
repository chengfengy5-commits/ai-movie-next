"""Layering checks for the framework-independent download-link modules."""

from __future__ import annotations

import ast
from pathlib import Path

from fastapi import FastAPI

from haoai_backend.app import create_app


BACKEND_ROOT = Path(__file__).parents[1]
DOWNLOAD_ROOT = BACKEND_ROOT / "src" / "haoai_backend" / "download_links"
PURE_MODULES = (
    "domain.py",
    "errors.py",
    "ports.py",
    "application.py",
    "compatibility.py",
)


def imported_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_domain_and_application_modules_have_no_framework_dependencies() -> None:
    for filename in PURE_MODULES:
        tree = ast.parse((DOWNLOAD_ROOT / filename).read_text(encoding="utf-8"))
        imports = imported_roots(tree)
        assert "fastapi" not in imports
        assert "sqlalchemy" not in imports
        assert "app" not in imports


def test_factory_registers_download_routes_without_opening_storage() -> None:
    sessions = 0

    def forbidden_session_factory():
        nonlocal sessions
        sessions += 1
        raise AssertionError("factory construction must not open a session")

    app = create_app(session_factory=forbidden_session_factory)
    assert isinstance(app, FastAPI)
    methods = {
        (route.path, method)
        for route in app.routes
        for method in getattr(route, "methods", set())
    }
    assert {
        ("/api/download", "GET"),
        ("/api/sign-download-urls", "POST"),
    } <= methods
    assert sessions == 0
