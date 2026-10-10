from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import event

from haoai_backend.admin_tasks.application import list_tasks
from haoai_backend.admin_tasks.domain import AdminTaskFilters
from haoai_backend.admin_tasks.persistence import admin_task_unit_of_work_factory

from admin_tasks_support import (
    admin_task_database,
    make_task_row,
    seed_admin_tasks,
    snapshot_admin_owner,
)


def test_package_import_stays_framework_and_persistence_free() -> None:
    source_root = Path(__file__).parents[1] / "src"
    code = "\n".join(
        (
            "import sys",
            "import haoai_backend.admin_tasks",
            "for forbidden in ('sqlalchemy', 'fastapi'):",
            "    assert forbidden not in sys.modules, forbidden",
            "for suffix in ('.application', '.http', '.persistence', '.tables'):",
            "    assert 'haoai_backend.admin_tasks' + suffix not in sys.modules, suffix",
        )
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(source_root)
    completed = subprocess.run(
        [sys.executable, "-B", "-c", code],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert completed.returncode == 0, completed.stderr


def test_admin_adapter_has_no_legacy_or_neighbor_private_imports() -> None:
    package_root = Path(__file__).parents[1] / "src" / "haoai_backend" / "admin_tasks"
    forbidden = (
        "app.",
        "haoai_backend.generic_tasks",
        "haoai_backend.task_observation",
    )
    for path in sorted(package_root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert not module.startswith(forbidden), (path.name, module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith(forbidden), (path.name, alias.name)


def test_full_owner_sqlite_snapshot_is_unchanged_after_real_admin_reads(admin_task_database) -> None:
    seed_admin_tasks(
        admin_task_database,
        [make_task_row("task-a", "user-a", request_data="raw", result="raw")],
    )
    before = snapshot_admin_owner(admin_task_database)
    statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many) -> None:
        statements.append(statement.lstrip().split(None, 1)[0].upper())

    event.listen(admin_task_database.engine, "before_cursor_execute", capture)
    factory = admin_task_unit_of_work_factory(admin_task_database.session_factory)
    try:
        page = list_tasks(factory, AdminTaskFilters())
    finally:
        event.remove(admin_task_database.engine, "before_cursor_execute", capture)

    assert page.total == 1
    assert statements[:2] == ["SELECT", "SELECT"]
    assert not any(word in {"INSERT", "UPDATE", "DELETE"} for word in statements)
    assert snapshot_admin_owner(admin_task_database) == before
    assert admin_task_database.session_factory.created[-1].close_calls == 1
