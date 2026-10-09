"""Layering and import-construction side-effect guardrails."""

from __future__ import annotations

import ast
import importlib
import os
import subprocess
import sys
import textwrap
from pathlib import Path

from fastapi import FastAPI

from haoai_backend.app import create_app

BACKEND_ROOT = Path(__file__).parents[1]
SRC_ROOT = BACKEND_ROOT / "src" / "haoai_backend"
PURE_MODULES = (
    SRC_ROOT / "shared" / "identity.py",
    SRC_ROOT / "shared" / "errors.py",
    SRC_ROOT / "personal_production" / "rough_cut" / "domain.py",
    SRC_ROOT / "personal_production" / "rough_cut" / "errors.py",
    SRC_ROOT / "personal_production" / "rough_cut" / "ports.py",
    SRC_ROOT / "personal_production" / "rough_cut" / "application.py",
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


def test_pure_layers_do_not_import_fastapi_sqlalchemy_or_legacy_app() -> None:
    for path in PURE_MODULES:
        tree = ast.parse(path.read_text())
        roots = imported_roots(tree)
        assert "fastapi" not in roots
        assert "sqlalchemy" not in roots
        assert "app" not in roots


def test_production_source_has_no_engine_schema_or_environment_bootstrap() -> None:
    production_files = tuple(SRC_ROOT.rglob("*.py"))
    forbidden = (
        "create_engine(",
        ".create_all(",
        "autoload_with=",
        "load_dotenv(",
        'open(".env"',
        "from app.",
        "import app.",
    )
    for path in production_files:
        source = path.read_text()
        assert not any(marker in source for marker in forbidden), path.relative_to(SRC_ROOT)


def test_import_and_unwired_factory_registers_the_complete_route_surface() -> None:
    app = create_app()
    assert isinstance(app, FastAPI)
    paths = {
        (route.path, frozenset(getattr(route, "methods", set())))
        for route in app.routes
    }
    assert paths == {
        ("/api/auth/register", frozenset({"POST"})),
        ("/api/auth/login", frozenset({"POST"})),
        ("/api/auth/me", frozenset({"GET"})),
        ("/api/auth/sessions", frozenset({"GET"})),
        ("/api/auth/sessions/{session_id}", frozenset({"DELETE"})),
        ("/api/auth/password", frozenset({"PUT"})),
        ("/api/auth/forgot-password", frozenset({"POST"})),
        ("/api/auth/reset-password", frozenset({"POST"})),
        ("/api/auth/credits/me", frozenset({"GET"})),
        ("/api/auth/send-code", frozenset({"POST"})),
        ("/api/auth/verify-code", frozenset({"POST"})),
        ("/api/chapters/{chapter_id}/rough-cut", frozenset({"GET"})),
        ("/api/chapters/{chapter_id}/rough-cut", frozenset({"PUT"})),
        ("/api/chapters/{chapter_id}/personal-production-notes", frozenset({"GET"})),
        ("/api/chapters/{chapter_id}/personal-production-notes", frozenset({"PUT"})),
        ("/api/chapters/{chapter_id}/canvas", frozenset({"GET"})),
        ("/api/chapters/{chapter_id}/canvas", frozenset({"PUT"})),
        ("/api/series", frozenset({"GET"})),
        ("/api/series/{series_id}", frozenset({"GET"})),
        ("/api/series", frozenset({"POST"})),
        ("/api/series/{series_id}", frozenset({"PUT"})),
        ("/api/series/{series_id}", frozenset({"DELETE"})),
        ("/api/series/{series_id}/chapters", frozenset({"GET"})),
        ("/api/series/{series_id}/chapters/reorder", frozenset({"PUT"})),
        ("/api/series/{series_id}/chapters", frozenset({"POST"})),
        ("/api/chapters/{chapter_id}", frozenset({"PUT"})),
        ("/api/chapters/{chapter_id}/delete-frame", frozenset({"PUT"})),
        ("/api/chapters/{chapter_id}", frozenset({"DELETE"})),
        ("/api/series/{series_id}/storyboard-assets", frozenset({"GET"})),
        ("/api/series/{series_id}/characters", frozenset({"GET"})),
        ("/api/series/{series_id}/characters", frozenset({"POST"})),
        ("/api/characters/{character_id}", frozenset({"PUT"})),
        ("/api/characters/{character_id}", frozenset({"DELETE"})),
        ("/api/series/{series_id}/scenes", frozenset({"GET"})),
        ("/api/series/{series_id}/scenes", frozenset({"POST"})),
        ("/api/scenes/{scene_id}", frozenset({"PUT"})),
        ("/api/scenes/{scene_id}", frozenset({"DELETE"})),
        ("/api/series/{series_id}/props", frozenset({"GET"})),
        ("/api/series/{series_id}/props", frozenset({"POST"})),
        ("/api/props/{prop_id}", frozenset({"PUT"})),
        ("/api/props/{prop_id}", frozenset({"DELETE"})),
        ("/api/storyboard-assets", frozenset({"POST"})),
        ("/api/storyboard-assets/{asset_id}", frozenset({"PUT"})),
        ("/api/storyboard-assets/{asset_id}", frozenset({"DELETE"})),
        ("/api/chapters/{chapter_id}/chat-messages", frozenset({"GET"})),
        ("/api/chapters/{chapter_id}/chat-messages", frozenset({"POST"})),
        ("/api/chapters/{chapter_id}/chat-messages/{message_id}", frozenset({"PUT"})),
        ("/api/chapters/{chapter_id}/asset-chat-messages/{message_id}", frozenset({"PUT"})),
        ("/api/chapters/{chapter_id}/asset-chat-messages", frozenset({"GET"})),
        ("/api/chapters/{chapter_id}/asset-chat-messages", frozenset({"POST"})),
        ("/api/chapters/{chapter_id}/chat-messages", frozenset({"DELETE"})),
        ("/api/chapters/{chapter_id}/chat-messages/single/{message_id}", frozenset({"DELETE"})),
        ("/api/chapters/{chapter_id}/asset-chat-messages/single/{message_id}", frozenset({"DELETE"})),
        ("/api/chapters/{chapter_id}/ai-stats", frozenset({"GET"})),
        ("/api/download", frozenset({"GET"})),
        ("/api/sign-download-urls", frozenset({"POST"})),
    }
    assert app.openapi_url is None
    assert app.docs_url is None
    assert app.redoc_url is None


def test_first_full_package_import_and_factory_have_no_startup_side_effects(tmp_path: Path) -> None:
    sentinel = tmp_path / ".env"
    sentinel.write_text("DO_NOT_READ=sentinel\\n", encoding="utf-8")
    child = textwrap.dedent(
        """
        import builtins
        import collections.abc
        import datetime
        import importlib
        import inspect
        import io
        import json
        import os
        import pkgutil
        import pathlib
        import re
        import sqlalchemy
        import sqlalchemy.engine
        import sqlalchemy.orm
        import sys
        import traceback
        import uuid
        import fastapi
        import pydantic
        from sqlalchemy import MetaData
        from sqlalchemy.orm import Session

        effects = []
        environment_reads = []
        dotenv_reads = []
        wrapped_environment = os.environ
        pydantic_root = pathlib.Path(pydantic.__file__).resolve().parent
        pydantic_core = importlib.import_module("pydantic_core")
        pydantic_core_root = pathlib.Path(pydantic_core.__file__).resolve().parent
        production_root = next(
            pathlib.Path(entry).resolve() / "haoai_backend"
            for entry in sys.path
            if entry and (pathlib.Path(entry) / "haoai_backend").is_dir()
        )

        def record_environment_read(key):
            stack = traceback.extract_stack()[:-1]
            frames = [(frame.filename, frame.name) for frame in stack]
            caller = next(
                (frame.filename for frame in reversed(stack) if frame.filename not in {"<string>", os.__file__}),
                "<unknown>",
            )
            environment_reads.append((str(key), caller, frames))

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
        assert len(app.routes) == 56, [(route.path, route.methods) for route in app.routes]
        assert effects == [], effects
        # Pydantic probes this optional schema flag; application modules must not read env.
        for key, caller, frames in environment_reads:
            caller_path = pathlib.Path(caller).resolve()
            assert key == "PYDANTIC_SKIP_VALIDATING_CORE_SCHEMAS", (key, caller, frames)
            assert caller_path.is_relative_to(pydantic_root) or caller_path.is_relative_to(pydantic_core_root), (key, caller, frames)
            assert not caller_path.is_relative_to(production_root), (key, caller, frames)
        assert dotenv_reads == [], dotenv_reads
        """
    )
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(SRC_ROOT.parent),
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


def test_import_and_factory_do_not_create_engine_or_schema(monkeypatch) -> None:
    import sqlalchemy
    from haoai_backend.personal_production.rough_cut import tables as rough_tables
    from haoai_backend.personal_production.notes import tables as notes_tables

    engine_calls = []
    schema_calls = []

    def engine_spy(*args, **kwargs):
        engine_calls.append((args, kwargs))
        raise AssertionError("module import must not create an engine")

    def schema_spy(*args, **kwargs):
        schema_calls.append((args, kwargs))
        raise AssertionError("module import must not create a schema")

    monkeypatch.setattr(sqlalchemy, "create_engine", engine_spy)
    monkeypatch.setattr(rough_tables.metadata, "create_all", schema_spy)
    monkeypatch.setattr(notes_tables.metadata, "create_all", schema_spy)
    app_module = importlib.reload(importlib.import_module("haoai_backend.app"))
    app_module.create_app()

    assert engine_calls == []
    assert schema_calls == []


def test_module_declares_only_the_existing_runtime_dependencies() -> None:
    pyproject = (BACKEND_ROOT / "pyproject.toml").read_text()
    assert '"fastapi==0.104.1"' in pyproject
    assert '"pydantic==2.5.0"' in pyproject
    assert '"SQLAlchemy==2.0.23"' in pyproject
    assert "uvicorn" not in pyproject
    assert "alembic" not in pyproject
