"""Import boundaries and explicit-construction guarantees for authentication."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import textwrap
from pathlib import Path

AUTH_ROOT = Path(__file__).parents[1] / "src" / "haoai_backend" / "authentication"


def import_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def test_application_and_pure_authentication_layers_do_not_import_framework_adapters() -> None:
    pure_files = (
        "avatar.py",
        "domain.py",
        "email_codes.py",
        "errors.py",
        "ports.py",
        "rate_limit.py",
        "application.py",
        "passwords.py",
        "tokens.py",
    )
    forbidden = {"fastapi", "sqlalchemy"}
    for filename in pure_files:
        imported = import_roots(ast.parse((AUTH_ROOT / filename).read_text(encoding="utf-8")))
        assert imported.isdisjoint(forbidden), (filename, imported & forbidden)


def test_cold_app_import_and_unwired_factory_do_not_open_env_or_construct_sqlalchemy(
    tmp_path: Path,
) -> None:
    child = textwrap.dedent(
        """
        import builtins
        import importlib
        import io
        import os
        import pathlib
        import sqlalchemy
        import sqlalchemy.engine
        import pydantic
        import pydantic_core
        from sqlalchemy import MetaData
        from sqlalchemy.orm import Session
        import sys
        import traceback

        environment = os.environ
        reads = []
        dotenv_attempts = []
        side_effects = []
        pydantic_root = pathlib.Path(pydantic.__file__).resolve().parent
        core_root = pathlib.Path(pydantic_core.__file__).resolve().parent

        class EnvironmentProbe:
            def __getitem__(self, key):
                stack = traceback.extract_stack()[:-1]
                caller = next((frame.filename for frame in reversed(stack) if frame.filename != '<string>'), '<unknown>')
                reads.append((str(key), caller))
                return environment[key]
            def get(self, key, default=None):
                stack = traceback.extract_stack()[:-1]
                caller = next((frame.filename for frame in reversed(stack) if frame.filename != '<string>'), '<unknown>')
                reads.append((str(key), caller))
                return environment.get(key, default)
            def __contains__(self, key):
                stack = traceback.extract_stack()[:-1]
                caller = next((frame.filename for frame in reversed(stack) if frame.filename != '<string>'), '<unknown>')
                reads.append((str(key), caller))
                return key in environment
            def __iter__(self):
                reads.append(('<iterate>', '<unknown>'))
                return iter(environment)
            def __len__(self):
                reads.append(('<length>', '<unknown>'))
                return len(environment)
            def __setitem__(self, key, value):
                environment[key] = value
            def __delitem__(self, key):
                del environment[key]

        def watched(open_function):
            def open_file(file, mode='r', *args, **kwargs):
                try:
                    candidate = pathlib.Path(os.fspath(file))
                except TypeError:
                    candidate = None
                if candidate is not None and candidate.name == '.env' and ('r' in mode or '+' in mode):
                    dotenv_attempts.append(str(candidate))
                    raise AssertionError('application must not read .env')
                return open_function(file, mode, *args, **kwargs)
            return open_file

        def forbidden(name):
            def fail(*args, **kwargs):
                side_effects.append(name)
                raise AssertionError('unexpected construction: ' + name)
            return fail

        builtins.open = watched(builtins.open)
        io.open = watched(io.open)
        os.environ = EnvironmentProbe()
        sqlalchemy.create_engine = forbidden('create_engine')
        sqlalchemy.engine.create_engine = forbidden('engine.create_engine')
        MetaData.create_all = forbidden('MetaData.create_all')
        Session.__init__ = forbidden('Session.__init__')

        module = importlib.import_module('haoai_backend.app')
        app = module.create_app()
        paths = [(route.path, route.methods) for route in app.routes]
        assert len(paths) == 57, paths
        for module_name in ("passlib", "jose"):
            assert module_name not in sys.modules, module_name
        assert side_effects == [], side_effects
        assert dotenv_attempts == [], dotenv_attempts
        for key, caller in reads:
            caller_path = pathlib.Path(caller).resolve()
            assert key == 'PYDANTIC_SKIP_VALIDATING_CORE_SCHEMAS', (key, caller)
            assert caller_path.is_relative_to(pydantic_root) or caller_path.is_relative_to(core_root), (key, caller)
        """
    )
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(AUTH_ROOT.parents[1]),
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
