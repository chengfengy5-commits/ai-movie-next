"""Layer and cold-import checks for the new series-access modules."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import textwrap
from pathlib import Path

SRC_ROOT = Path(__file__).parents[1] / "src"
ACCESS_ROOT = SRC_ROOT / "haoai_backend" / "series_access"
PURE_MODULES = (
    ACCESS_ROOT / "domain.py",
    ACCESS_ROOT / "errors.py",
    ACCESS_ROOT / "ports.py",
    ACCESS_ROOT / "application.py",
)


def imported_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_pure_access_layers_do_not_import_framework_persistence_or_legacy_app() -> None:
    for path in PURE_MODULES:
        roots = imported_roots(ast.parse(path.read_text(encoding="utf-8")))
        assert "fastapi" not in roots
        assert "sqlalchemy" not in roots
        assert "app" not in roots
        assert "personal_production" not in roots


def test_new_production_package_has_no_startup_bootstrap() -> None:
    production_root = SRC_ROOT / "haoai_backend"
    forbidden = (
        "create_engine(",
        ".create_all(",
        "autoload_with=",
        "load_dotenv(",
        'open(".env"',
        "from app.",
        "import app.",
    )
    for path in production_root.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert not any(marker in source for marker in forbidden), path.relative_to(production_root)


def test_first_full_package_import_and_factory_have_no_startup_side_effects(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("DO_NOT_READ=sentinel\n", encoding="utf-8")
    child = textwrap.dedent(
        """
        import builtins
        import collections.abc
        import datetime
        import importlib
        import io
        import json
        import os
        import pathlib
        import pkgutil
        import re
        import sqlalchemy
        import sqlalchemy.engine
        import sqlalchemy.orm
        import sys
        import traceback
        import uuid
        import fastapi
        import pydantic
        import pydantic_core
        from sqlalchemy import MetaData
        from sqlalchemy.orm import Session

        effects = []
        environment_reads = []
        dotenv_reads = []
        wrapped_environment = os.environ
        pydantic_root = pathlib.Path(pydantic.__file__).resolve().parent
        pydantic_core_root = pathlib.Path(pydantic_core.__file__).resolve().parent
        production_root = next(
            pathlib.Path(entry).resolve() / "haoai_backend"
            for entry in sys.path
            if entry and (pathlib.Path(entry) / "haoai_backend").is_dir()
        )

        def record_environment_read(key):
            stack = traceback.extract_stack()[:-1]
            caller = next(
                (frame.filename for frame in reversed(stack) if frame.filename not in {"<string>", os.__file__}),
                "<unknown>",
            )
            environment_reads.append((str(key), caller, stack))

        class EnvironmentProbe:
            def __getitem__(self, key):
                record_environment_read(key)
                return wrapped_environment[key]

            def get(self, key, default=None):
                record_environment_read(key)
                return wrapped_environment.get(key, default)

            def __contains__(self, key):
                record_environment_read(key)
                return key in wrapped_environment

            def __iter__(self):
                record_environment_read("<iterate>")
                return iter(wrapped_environment)

            def __len__(self):
                record_environment_read("<length>")
                return len(wrapped_environment)

            def __setitem__(self, key, value):
                wrapped_environment[key] = value

            def __delitem__(self, key):
                del wrapped_environment[key]

        def watched_open(open_function):
            def open_file(file, mode="r", *args, **kwargs):
                try:
                    candidate = pathlib.Path(os.fspath(file))
                except TypeError:
                    candidate = None
                if candidate is not None and candidate.name == ".env" and ("r" in mode or "+" in mode):
                    dotenv_reads.append(str(candidate))
                    raise AssertionError("production import must not read .env")
                return open_function(file, mode, *args, **kwargs)
            return open_file

        def forbidden(name):
            def fail(*args, **kwargs):
                effects.append(name)
                raise AssertionError("unexpected startup side effect: " + name)
            return fail

        builtins.open = watched_open(builtins.open)
        io.open = watched_open(io.open)
        os.environ = EnvironmentProbe()
        Session.__init__ = forbidden("Session.__init__")
        sqlalchemy.create_engine = forbidden("sqlalchemy.create_engine")
        sqlalchemy.engine.create_engine = forbidden("sqlalchemy.engine.create_engine")
        MetaData.create_all = forbidden("MetaData.create_all")

        package = importlib.import_module("haoai_backend")
        for module in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
            importlib.import_module(module.name)
        app_module = importlib.import_module("haoai_backend.app")
        app = app_module.create_app()
        assert len(app.routes) == 91
        assert effects == [], effects
        for key, caller, stack in environment_reads:
            caller_path = pathlib.Path(caller).resolve()
            assert key == "PYDANTIC_SKIP_VALIDATING_CORE_SCHEMAS", (key, caller, stack)
            assert caller_path.is_relative_to(pydantic_root) or caller_path.is_relative_to(pydantic_core_root), (key, caller, stack)
            assert not caller_path.is_relative_to(production_root), (key, caller, stack)
        assert dotenv_reads == [], dotenv_reads
        """
    )
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(SRC_ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONNOUSERSITE": "1",
    }
    completed = subprocess.run(
        [sys.executable, "-B", "-c", child],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
