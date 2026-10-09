"""Pure merging of grouped AI task statistics."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

UNKNOWN_MODEL = "未知模型"


def merge_model_statistics(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Merge null and empty model names without changing first-seen group order."""
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        model_name = row["model_name"] or UNKNOWN_MODEL
        entry = merged.get(model_name)
        if entry is None:
            entry = {
                "model_name": model_name,
                "calls": 0,
                "credits": 0,
                "failed_calls": 0,
            }
            merged[model_name] = entry

        calls = row["calls"]
        if row["status"] == "failed":
            entry["failed_calls"] += calls
        else:
            entry["calls"] += calls
            entry["credits"] += row["credits"]

    return list(merged.values())
