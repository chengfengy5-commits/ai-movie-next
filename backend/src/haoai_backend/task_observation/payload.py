"""Pure JSON folding and source-compatible task response projections."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from typing import Any

from .domain import TaskRecord
from .ports import TaskObservationReader, TaskRow

_BASE64_PATTERN = re.compile(r"[A-Za-z0-9+/=\s]+")


def fold_long_strings(node: Any, depth: int = 0) -> Any:
    """Fold data URIs and long base64 values without changing other JSON values."""
    if depth > 6:
        return node
    if isinstance(node, str):
        if node.startswith("data:") or (
            len(node) > 500 and _BASE64_PATTERN.fullmatch(node) is not None
        ):
            return f"<base64 len={len(node)}>"
        return node
    if isinstance(node, list):
        return [fold_long_strings(value, depth + 1) for value in node]
    if isinstance(node, dict):
        return {key: fold_long_strings(value, depth + 1) for key, value in node.items()}
    return node


def decode_request_data(raw: Any) -> Any:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {"_raw": raw}


def build_task_request(task: Mapping[str, Any] | TaskRecord) -> dict[str, Any]:
    def value(name: str, default: Any = None) -> Any:
        if isinstance(task, Mapping):
            return task.get(name, default)
        return getattr(task, name, default)

    created_at = value("created_at")
    return {
        "id": value("id"),
        "type": value("type"),
        "status": value("status"),
        "model_name": value("model_name"),
        "credit_cost": value("credit_cost") or 0,
        "external_task_id": value("external_task_id"),
        "external_provider": value("external_provider"),
        "created_at": created_at.isoformat() if created_at else None,
        "request": fold_long_strings(decode_request_data(value("request_data"))),
    }


def build_task_list_payload(
    actor_id: str,
    page: int,
    page_size: int,
    reader: TaskObservationReader,
) -> dict[str, Any]:
    """Load and project one page using the existing public team payload helpers."""
    total = reader.count_owned_tasks(actor_id)
    tasks = [
        _as_mapping(task)
        for task in reader.list_owned_tasks(actor_id, (page - 1) * page_size, page_size)
    ]
    collect_task_context, project_task_items = _task_payload_helpers()
    message_ids = [task.get("message_id") for task in tasks if task.get("message_id")]
    messages = reader.load_messages(message_ids) if message_ids else {}
    _, chapter_ids, asset_ids = collect_task_context(tasks, messages)
    chapter_titles = reader.load_chapter_titles(chapter_ids) if chapter_ids else {}
    asset_names = reader.load_asset_names(asset_ids) if any(asset_ids.values()) else {}
    return {
        "total": total,
        "tasks": project_task_items(tasks, messages, chapter_titles, asset_names),
        "page": page,
        "page_size": page_size,
    }


def _task_payload_helpers():
    # reporting.__init__ currently imports HTTP, so keep this public pure helper lazy.
    from haoai_backend.teams.reporting.task_payload import (
        collect_task_context,
        project_task_items,
    )

    return collect_task_context, project_task_items


def _as_mapping(task: TaskRow | TaskRecord) -> TaskRow:
    if isinstance(task, Mapping):
        return task
    if is_dataclass(task) and not isinstance(task, type):
        return {field.name: getattr(task, field.name) for field in fields(task)}
    raise TypeError("task row must be a mapping or dataclass record")


def build_ai_review_counts(
    message_id: str,
    candidates: Sequence[TaskRow | TaskRecord],
) -> dict[str, Any]:
    if not message_id:
        return {"counts": {}}

    counts: dict[Any, int] = {}
    for candidate in candidates:
        task = _as_mapping(candidate)
        try:
            request_data = json.loads(task.get("request_data") or "{}")
        except Exception:
            continue
        if (request_data.get("source_message_id") or "") != message_id:
            continue
        prompt_id = request_data.get("prompt_id") or ""
        if not prompt_id:
            continue
        counts[prompt_id] = counts.get(prompt_id, 0) + 1
    return {"counts": counts}


def find_running_batch_optimize(
    chapter_id: str,
    candidates: Sequence[TaskRow | TaskRecord],
) -> TaskRow | None:
    for candidate in candidates:
        task = _as_mapping(candidate)
        try:
            request_data = json.loads(task.get("request_data") or "{}")
        except Exception:
            continue
        if request_data.get("chapter_id") == chapter_id:
            return task
    return None


def build_running_batch_optimize(task: TaskRow | TaskRecord | None) -> dict[str, Any]:
    if task is None:
        return {"task": None}
    row = _as_mapping(task)
    created_at = row.get("created_at")
    return {
        "task": {
            "task_id": row["id"],
            "status": row["status"],
            "progress": row.get("progress") or 0,
            "progress_message": row.get("progress_message") or "",
            "created_at": created_at.isoformat() if created_at else None,
        }
    }


def build_ai_review_status(task: TaskRow | TaskRecord) -> dict[str, Any]:
    row = _as_mapping(task)
    return {
        "id": row["id"],
        "type": row["type"],
        "status": row["status"],
        "progress": row.get("progress") or 0,
        "progress_message": row.get("progress_message"),
        "result": row.get("result"),
        "credit_cost": row.get("credit_cost") or 0,
        "model_name": row.get("model_name"),
    }


def build_batch_optimize_status(task: TaskRow | TaskRecord) -> dict[str, Any]:
    row = _as_mapping(task)
    result_data = None
    if row.get("result"):
        try:
            result_data = json.loads(row["result"])
        except json.JSONDecodeError:
            result_data = row["result"]
    return {
        "task_id": row["id"],
        "status": row["status"],
        "progress": row.get("progress") or 0,
        "progress_message": row.get("progress_message") or "",
        "result": result_data,
    }
