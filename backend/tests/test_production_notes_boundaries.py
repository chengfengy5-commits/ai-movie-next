"""Layering and public compatibility checks for the notes module."""

from __future__ import annotations

import ast
from pathlib import Path

from haoai_backend.personal_production.notes import application, domain, errors, media, ports, reconciliation
from haoai_backend.personal_production.rough_cut.errors import RoughCutError
from haoai_backend.personal_production.rough_cut.ports import TrustedActor as RoughCutTrustedActor
from haoai_backend.shared.errors import BusinessError
from haoai_backend.shared.identity import TrustedActor

BACKEND_ROOT = Path(__file__).parents[1]
SRC_ROOT = BACKEND_ROOT / "src" / "haoai_backend"
PURE_NOTES_MODULES = (
    SRC_ROOT / "shared" / "identity.py",
    SRC_ROOT / "shared" / "errors.py",
    SRC_ROOT / "personal_production" / "notes" / "domain.py",
    SRC_ROOT / "personal_production" / "notes" / "errors.py",
    SRC_ROOT / "personal_production" / "notes" / "media.py",
    SRC_ROOT / "personal_production" / "notes" / "ports.py",
    SRC_ROOT / "personal_production" / "notes" / "application.py",
    SRC_ROOT / "personal_production" / "notes" / "reconciliation.py",
)


def imported_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_shared_actor_is_the_same_public_class_and_errors_share_one_base() -> None:
    assert RoughCutTrustedActor is TrustedActor
    assert issubclass(RoughCutError, BusinessError)


def test_pure_notes_layers_do_not_import_http_sqlalchemy_or_legacy_app() -> None:
    assert all((domain, errors, media, ports, application, reconciliation))
    for path in PURE_NOTES_MODULES:
        roots = imported_roots(ast.parse(path.read_text(encoding="utf-8")))
        assert "fastapi" not in roots, path
        assert "sqlalchemy" not in roots, path
        assert "app" not in roots, path


def test_notes_package_does_not_depend_on_rough_cut_modules() -> None:
    notes_root = SRC_ROOT / "personal_production" / "notes"
    for path in notes_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported = [node.module or ""]
            else:
                continue
            assert all("personal_production.rough_cut" not in name for name in imported), path
