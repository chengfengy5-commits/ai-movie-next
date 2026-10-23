from __future__ import annotations

import json
from pathlib import Path

from haoai_backend.task_observation.payload import build_batch_optimize_status


_FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "src"
    / "testing"
    / "fixtures"
    / "frameOptimizeStatusWire.json"
)


def test_batch_optimize_status_projection_matches_shared_wire_fixture() -> None:
    fixture = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))

    for case_name, case in fixture["cases"].items():
        assert build_batch_optimize_status(case["task"]) == case["wire"], case_name
