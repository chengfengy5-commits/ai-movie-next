from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).parents[2]
SOURCE_ROOT = BACKEND_ROOT / "src"


def test_cold_contract_and_registry_imports_do_not_open_resources() -> None:
    script = r'''
import builtins
import importlib
import os
import socket
import sys

blocked = {"aiohttp", "boto3", "fastapi", "httpx", "requests", "sqlalchemy"}
original_import = builtins.__import__

def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name.split(".", 1)[0] in blocked:
        raise AssertionError("forbidden resource dependency imported: " + name)
    return original_import(name, globals, locals, fromlist, level)

def forbidden_resource(*args, **kwargs):
    raise AssertionError("contract import attempted to open a resource")

builtins.__import__ = guarded_import
socket.socket = forbidden_resource
socket.create_connection = forbidden_resource
os.environ = object()

contracts = importlib.import_module("haoai_backend.generation_contracts")
runtime = importlib.import_module("haoai_backend.generation_runtime")
assert len(contracts.PUBLIC_TYPE_NAMES) == 40
assert runtime.operation_policy("text.stream").graph_version is None
assert not any(name in sys.modules for name in blocked)
'''
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(SOURCE_ROOT)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=BACKEND_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
