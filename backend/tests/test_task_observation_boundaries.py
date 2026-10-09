from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from task_observation_support import (
    create_empty_task_observation_database,
    snapshot_task_observation_owner,
    task_observation_owner_table_names,
)


def test_production_package_cold_imports_without_runtime_adapters() -> None:
    source_root = Path(__file__).parents[1] / "src"
    code = "\n".join((
        "import sys",
        "import haoai_backend.task_observation",
        "import haoai_backend.task_observation.authentication",
        "import haoai_backend.task_observation.payload",
        "for forbidden in ('sqlalchemy', 'fastapi', 'aiohttp'):",
        "    assert forbidden not in sys.modules, forbidden",
        "for suffix in ('.application', '.http', '.persistence', '.tables', '.cancellation'):",
        "    assert 'haoai_backend.task_observation' + suffix not in sys.modules, suffix",
    ))
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


def test_support_uses_full_owner_ddl_and_removes_its_exact_database() -> None:
    table_names = task_observation_owner_table_names()
    assert len(table_names) == 24
    assert len(set(table_names)) == 24

    directory = Path(os.environ["TMPDIR"]) / f"owner-fixture-{os.getpid()}"
    directory.mkdir()
    database_path = directory / "owner.sqlite"
    database = create_empty_task_observation_database(database_path)
    try:
        database.assert_foreign_keys_enabled()
        snapshot = snapshot_task_observation_owner(database)
        assert set(snapshot) == set(table_names)
        assert all(rows == [] for rows in snapshot.values())
        assert database_path.is_file()
    finally:
        database.close()
        directory.rmdir()
    assert not database_path.exists()
    assert not directory.exists()
