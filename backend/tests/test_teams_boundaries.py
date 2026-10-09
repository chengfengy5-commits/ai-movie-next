from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import text

from haoai_backend.teams import tables
from teams_support import OWNER_METADATA, teams_database


def test_shared_import_is_inert():
    source_root = Path(__file__).resolve().parents[1] / "src"
    code = (
        "import sys\n"
        "import haoai_backend.teams as teams\n"
        "assert callable(teams.build_teams_routers)\n"
        "for family in ('management', 'series', 'reporting'):\n"
        "    assert f'haoai_backend.teams.{family}.http' not in sys.modules\n"
        "assert not any(name.startswith('haoai_backend.teams.management.') "
        "for name in sys.modules)\n"
        "assert not any(name.startswith('haoai_backend.teams.series.') "
        "for name in sys.modules)\n"
        "assert not any(name.startswith('haoai_backend.teams.reporting.') "
        "for name in sys.modules)\n"
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(source_root)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONNOUSERSITE"] = "1"
    result = subprocess.run(
        [sys.executable, "-B", "-c", code],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert result.returncode == 0, result.stderr


def test_shared_public_dependency_boundaries(teams_database):
    ports_path = Path(__file__).resolve().parents[1] / "src" / "haoai_backend" / "teams" / "ports.py"
    ports_tree = ast.parse(ports_path.read_text())
    imported_modules = set()
    for node in ast.walk(ports_tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)
    assert not any(name == "fastapi" or name.startswith("fastapi.") for name in imported_modules)
    assert not any(name == "sqlalchemy" or name.startswith("sqlalchemy.") for name in imported_modules)
    assert "haoai_backend.shared.identity" in imported_modules

    projection_names = (
        "users",
        "teams",
        "team_members",
        "team_invites",
        "chapter_locks",
        "system_configs",
    )
    for table_name in projection_names:
        projection = tables.metadata.tables[table_name]
        owner = OWNER_METADATA.tables[table_name]
        assert set(projection.columns.keys()) <= set(owner.columns.keys())

    with teams_database.engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        foreign_keys = connection.execute(text("PRAGMA foreign_key_list(team_members)")).all()
        assert {row[2] for row in foreign_keys} == {"teams", "users"}


def test_shared_owner_fixture_cleanup(teams_database):
    assert teams_database.engine.pool.checkedout() == 0
    session = teams_database.session_factory()
    try:
        session.execute(text("SELECT 1")).scalar_one()
        assert teams_database.engine.pool.checkedout() == 1
    finally:
        session.close()
    assert teams_database.engine.pool.checkedout() == 0
