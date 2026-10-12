from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

_PURE_IMPORT_CASES = (
    (
        "haoai_backend.chat_data",
        "haoai_backend.chat_data.ports",
        (
            "haoai_backend.chat_data.domain",
            "haoai_backend.chat_data.ports",
            "haoai_backend.chat_data.application",
        ),
        ("ChatGenerationWriter", "apply_web_text"),
        ("haoai_backend.chat_data.generation",),
    ),
    (
        "haoai_backend.series_data",
        "haoai_backend.series_data.ports",
        (
            "haoai_backend.series_data.domain",
            "haoai_backend.series_data.ports",
            "haoai_backend.series_data.application",
        ),
        ("SeriesGenerationWriter", "stage_frame_result", "stage_frame_preview"),
        ("haoai_backend.series_data.generation", "haoai_backend.series_data.media_writes"),
    ),
    (
        "haoai_backend.asset_data",
        "haoai_backend.asset_data.ports",
        (
            "haoai_backend.asset_data.domain",
            "haoai_backend.asset_data.ports",
            "haoai_backend.asset_data.application",
        ),
        ("AssetGenerationWriter", "digest_asset_target", "stage_asset_image"),
        ("haoai_backend.asset_data.generation", "haoai_backend.asset_data.media_writes"),
    ),
    (
        "haoai_backend.personal_production.notes",
        "haoai_backend.personal_production.notes.ports",
        (
            "haoai_backend.personal_production.notes.domain",
            "haoai_backend.personal_production.notes.errors",
            "haoai_backend.personal_production.notes.ports",
            "haoai_backend.personal_production.notes.media",
            "haoai_backend.personal_production.notes.application",
            "haoai_backend.personal_production.notes.reconciliation",
        ),
        ("MediaReconciliationOutcome", "NotesGenerationWriter", "stage_media_reconciliation"),
        (
            "haoai_backend.personal_production.notes.facade",
            "haoai_backend.personal_production.notes.persistence",
            "haoai_backend.personal_production.notes.tables",
        ),
    ),
    (
        "haoai_backend.task_observation",
        "haoai_backend.task_observation.ports",
        (
            "haoai_backend.task_observation.authentication",
            "haoai_backend.task_observation.domain",
            "haoai_backend.task_observation.errors",
            "haoai_backend.task_observation.payload",
            "haoai_backend.task_observation.ports",
            "haoai_backend.task_observation.receipts",
        ),
        (
            "GenerationResultApplicationFacade",
            "application_key_for",
            "application_ref_for",
            "current_target_application",
            "confirm_worker_fence_after_target_lock",
            "expected_target_digest",
            "find_application_record",
            "load_owned_completed_result",
            "load_validated_worker_result",
            "stage_application_record",
            "target_matches_receipt",
            "target_scope_for_snapshot",
        ),
        ("haoai_backend.task_observation.generation",),
    ),
)

_PORT_ANNOTATIONS = (
    (
        "chat_data/ports.py",
        "ChatGenerationWriter",
        {"apply_web_text": ("shared_session",)},
    ),
    (
        "series_data/ports.py",
        "SeriesGenerationWriter",
        {
            "stage_frame_result": ("shared_session",),
            "stage_frame_preview": ("shared_session",),
        },
    ),
    (
        "asset_data/ports.py",
        "AssetGenerationWriter",
        {"stage_asset_image": ("shared_session",)},
    ),
    (
        "personal_production/notes/ports.py",
        "NotesGenerationWriter",
        {"stage_media_reconciliation": ("shared_session",)},
    ),
    (
        "task_observation/ports.py",
        "GenerationResultReader",
        {"load_owned_completed_result": ("shared_tx",)},
    ),
)


@pytest.mark.parametrize(
    "package_name,first_module,pure_modules,public_exports,implementation_modules",
    _PURE_IMPORT_CASES,
    ids=("chat", "series", "asset", "notes", "task_observation"),
)
def test_each_pure_package_and_submodule_has_a_cold_import_boundary(
    package_name: str,
    first_module: str,
    pure_modules: tuple[str, ...],
    public_exports: tuple[str, ...],
    implementation_modules: tuple[str, ...],
) -> None:
    backend_src = Path(__file__).resolve().parents[2] / "src"
    source = r'''
import importlib
import sys

forbidden_roots = {"sqlalchemy", "fastapi", "aiohttp"}
forbidden_modules = set(sys.argv[5].split(","))
package_name = sys.argv[1]
first_module = sys.argv[2]
pure_modules = tuple(sys.argv[3].split(","))
public_exports = tuple(sys.argv[4].split(","))

assert not any(name.split(".", 1)[0] in forbidden_roots for name in sys.modules)

class BlockRuntimeImports:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".", 1)[0] in forbidden_roots:
            raise AssertionError(f"pure import attempted runtime dependency: {fullname}")
        return None

blocker = BlockRuntimeImports()
sys.meta_path.insert(0, blocker)
importlib.import_module(first_module)
package = importlib.import_module(package_name)
for module_name in pure_modules:
    importlib.import_module(module_name)

assert not any(name.split(".", 1)[0] in forbidden_roots for name in sys.modules)
loaded_adapters = [
    module_name
    for module_name in forbidden_modules
    if module_name in sys.modules
    or any(name.startswith(module_name + ".") for name in sys.modules)
]
assert not loaded_adapters, loaded_adapters
assert set(public_exports).issubset(set(package.__all__))
# Do not resolve any implementation export until the pure boundary is proven.
sys.meta_path.remove(blocker)
for name in public_exports:
    assert getattr(package, name) is not None
try:
    getattr(package, "__codex_unknown_generation_export__")
except AttributeError:
    pass
else:
    raise AssertionError(f"{package_name} accepted an unknown public name")
'''
    command = [
        sys.executable,
        "-B",
        "-c",
        source,
        package_name,
        first_module,
        ",".join(pure_modules),
        ",".join(public_exports),
        ",".join(implementation_modules),
    ]
    environment = {
        "PYTHONPATH": str(backend_src),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }

    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_all_public_package_exports_remain_resolvable_and_unknown_names_raise() -> None:
    backend_src = Path(__file__).resolve().parents[2] / "src"
    source = r'''
import sqlalchemy
import sqlalchemy.orm
import socket


def forbidden(*args, **kwargs):
    raise AssertionError("module import or facade construction created an external resource")

sqlalchemy.create_engine = forbidden
sqlalchemy.orm.sessionmaker = forbidden
socket.socket = forbidden

import haoai_backend.asset_data as asset_data
import haoai_backend.chat_data as chat_data
import haoai_backend.personal_production.notes as notes
import haoai_backend.series_data as series_data
import haoai_backend.task_observation as task_observation

packages = (asset_data, chat_data, notes, series_data, task_observation)
for package in packages:
    for name in package.__all__:
        assert getattr(package, name) is not None, (package.__name__, name)
    try:
        getattr(package, "__codex_unknown_generation_export__")
    except AttributeError:
        pass
    else:
        raise AssertionError(f"{package.__name__} accepted an unknown public name")

from sqlalchemy.orm import Session
session = Session()
facade = task_observation.GenerationResultApplicationFacade(session)
assert session.bind is None
assert not session.in_transaction()
assert callable(asset_data.stage_asset_image)
assert callable(chat_data.apply_web_text)
assert callable(notes.stage_media_reconciliation)
assert callable(series_data.stage_frame_result)
assert callable(series_data.stage_frame_preview)
assert "GenerationResultReader" in task_observation.__all__
session.close()
'''
    environment = {
        "PYTHONPATH": str(backend_src),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    result = subprocess.run(
        [sys.executable, "-B", "-c", source],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_all_five_pure_ports_avoid_framework_imports_and_use_caller_transaction() -> None:
    backend = Path(__file__).resolve().parents[2]
    forbidden_roots = {"sqlalchemy", "fastapi", "aiohttp", "app"}
    for relative, class_name, methods in _PORT_ANNOTATIONS:
        tree = ast.parse((backend / "src/haoai_backend" / relative).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".", 1)[0] for alias in node.names}
                assert not roots & forbidden_roots, (relative, node.lineno, roots)
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".", 1)[0]
                assert root not in forbidden_roots, (relative, node.lineno, root)

        protocol = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        )
        for method_name, parameter_names in methods.items():
            method = next(
                node
                for node in protocol.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == method_name
            )
            arguments = {argument.arg: argument for argument in method.args.args}
            for parameter_name in parameter_names:
                annotation = arguments[parameter_name].annotation
                assert isinstance(annotation, ast.Name) and annotation.id == "CallerTransaction", (
                    relative,
                    method_name,
                    parameter_name,
                    ast.unparse(annotation) if annotation else None,
                )
