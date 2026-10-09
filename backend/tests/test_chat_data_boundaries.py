from __future__ import annotations

import ast
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
CHAT_DATA_ROOT = SRC_ROOT / "haoai_backend" / "chat_data"


def _import_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_domain_and_application_layers_do_not_depend_on_http_or_sqlalchemy() -> None:
    pure_modules = (
        "domain.py",
        "errors.py",
        "ports.py",
        "presentation.py",
        "schemas.py",
        "statistics.py",
        "application.py",
    )
    for name in pure_modules:
        modules = _import_modules(CHAT_DATA_ROOT / name)
        assert not any(module == "fastapi" or module.startswith("fastapi.") for module in modules), name
        assert not any(module == "sqlalchemy" or module.startswith("sqlalchemy.") for module in modules), name
        assert not any(module == "haoai_backend.app" or module.startswith("haoai_backend.app.") for module in modules), name

    application_imports = (CHAT_DATA_ROOT / "application.py").read_text()
    assert ".persistence" not in application_imports


def test_sql_adapter_and_http_router_keep_framework_dependencies_at_edges() -> None:
    persistence_modules = _import_modules(CHAT_DATA_ROOT / "persistence.py")
    http_modules = _import_modules(CHAT_DATA_ROOT / "http.py")

    assert any(module == "sqlalchemy" or module.startswith("sqlalchemy.") for module in persistence_modules)
    assert not any(module == "fastapi" or module.startswith("fastapi.") for module in persistence_modules)
    assert any(module == "fastapi" or module.startswith("fastapi.") for module in http_modules)
    assert not any(module == "sqlalchemy" or module.startswith("sqlalchemy.") for module in http_modules)


def test_chat_data_package_has_no_engine_or_schema_bootstrap() -> None:
    forbidden = (
        "create_engine(",
        ".create_all(",
        "autoload_with=",
        "load_dotenv(",
    )
    for path in CHAT_DATA_ROOT.glob("*.py"):
        source = path.read_text()
        assert not any(marker in source for marker in forbidden), path.name
