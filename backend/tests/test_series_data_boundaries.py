from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_pure_series_data_modules_import_without_sqlalchemy_or_fastapi() -> None:
    source_root = Path(__file__).resolve().parents[1] / "src"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(source_root)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONNOUSERSITE"] = "1"

    code = "\n".join([
        "import sys",
        "import haoai_backend.series_data.domain",
        "import haoai_backend.series_data.schemas",
        "import haoai_backend.series_data.application",
        "modules = tuple(sys.modules)",
        "assert not any(name == 'sqlalchemy' or name.startswith('sqlalchemy.') for name in modules)",
        "assert not any(name == 'fastapi' or name.startswith('fastapi.') for name in modules)",
        "assert 'haoai_backend.series_data.persistence' not in sys.modules",
        "assert 'haoai_backend.series_data.http' not in sys.modules",
    ])
    result = subprocess.run(
        [sys.executable, "-B", "-c", code],
        cwd=source_root.parent.parent,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
