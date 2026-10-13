from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).parents[1] / "src" / "haoai_backend" / "admin_purchase_packages"


def test_core_layers_keep_framework_and_private_orm_dependencies_outside_domain() -> None:
    framework_free = ("domain.py", "ports.py", "application.py", "errors.py")
    for name in framework_free:
        tree = ast.parse((PACKAGE / name).read_text())
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        assert not any(module.startswith(("fastapi", "sqlalchemy")) for module in imports), name


def test_boundary_uses_local_admin_actor_and_no_legacy_model_or_generic_trusted_actor() -> None:
    production = "\n".join(path.read_text() for path in PACKAGE.glob("*.py"))
    assert "TrustedActor" not in production
    assert "app.models.purchase_package" not in production
    assert "X-Admin" not in production and "x-admin" not in production
    assert "require_membership=False" in (PACKAGE / "authentication.py").read_text()
    assert "AdminPurchasePackagesActor" in (PACKAGE / "domain.py").read_text()


def test_authorized_lane_contains_exact_ten_modules_and_four_focused_tests() -> None:
    assert {path.name for path in PACKAGE.glob("*.py")} == {
        "__init__.py", "domain.py", "ports.py", "application.py", "persistence.py",
        "tables.py", "http.py", "schemas.py", "authentication.py", "errors.py",
    }
    tests = Path(__file__).parent
    assert {
        path.name for path in tests.glob("test_admin_purchase_packages_*.py")
    } == {
        "test_admin_purchase_packages_application.py",
        "test_admin_purchase_packages_persistence.py",
        "test_admin_purchase_packages_http.py",
        "test_admin_purchase_packages_boundaries.py",
    }
