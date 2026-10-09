"""Pure task-context collection and safe response projection."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any


ASSET_KINDS = ("character", "scene", "prop", "storyboard")
CONTEXT_TASK_TYPES = {"batch-optimize", "ai-review"}


def collect_task_context(
    tasks: Iterable[Mapping[str, Any]],
    messages: Mapping[str, Mapping[str, Any]],
) -> tuple[list[str], set[str], dict[str, list[str]]]:
    """Collect batch lookup keys using the fixed chat helper’s source rules."""
    chapter_ids: set[str] = set()
    asset_ids: dict[str, list[str]] = {kind: [] for kind in ASSET_KINDS}
    message_ids: list[str] = []

    for task in tasks:
        message_id = task.get("message_id")
        if message_id:
            message_ids.append(message_id)
        if task.get("type") in CONTEXT_TASK_TYPES and task.get("request_data"):
            try:
                request_data = json.loads(task["request_data"])
                chapter_id = request_data.get("chapter_id")
                if chapter_id:
                    chapter_ids.add(chapter_id)
            except Exception:
                pass

    for message_id in dict.fromkeys(message_ids):
        message = messages.get(message_id)
        if message is None:
            continue
        chapter_id = message.get("chapter_id")
        if chapter_id:
            chapter_ids.add(chapter_id)
        asset_type = message.get("asset_type")
        asset_id = message.get("asset_id")
        if asset_type in asset_ids and asset_id:
            asset_ids[asset_type].append(asset_id)

    return message_ids, chapter_ids, asset_ids


def project_task_items(
    tasks: Iterable[Mapping[str, Any]],
    messages: Mapping[str, Mapping[str, Any]],
    chapter_titles: Mapping[str, str],
    asset_names: Mapping[str, Mapping[str, str | None]],
) -> list[dict[str, Any]]:
    """Return source-compatible task dictionaries without importing chat code."""
    result: list[dict[str, Any]] = []
    for task in tasks:
        safe_result = task.get("result")
        if task.get("status") == "failed" and safe_result:
            if len(safe_result) > 5000:
                safe_result = safe_result[:5000]
        elif safe_result and (len(safe_result) > 500 or safe_result.startswith("data:")):
            safe_result = ""

        request_data = task.get("request_data")
        item: dict[str, Any] = {
            "id": task["id"],
            "type": task["type"],
            "message_id": task.get("message_id"),
            "status": task["status"],
            "result": safe_result,
            "request_data": (
                request_data[:200]
                if request_data and len(request_data) > 200
                else request_data
            ),
            "credit_cost": task.get("credit_cost") or 0,
            "progress": task.get("progress") or 0,
            "progress_message": task.get("progress_message"),
            "created_at": task["created_at"].isoformat() if task.get("created_at") else None,
            "updated_at": task["updated_at"].isoformat() if task.get("updated_at") else None,
            "asset_type": None,
            "asset_id": None,
            "asset_name": None,
            "chapter_title": None,
            "chapter_id": None,
            "frame_index": None,
            "frame_count": None,
            "frame_text": None,
        }

        message_id = task.get("message_id")
        message = messages.get(message_id) if message_id else None
        if message is not None:
            asset_type = message.get("asset_type")
            asset_id = message.get("asset_id")
            chapter_id = message.get("chapter_id")
            item["asset_type"] = asset_type
            item["asset_id"] = asset_id
            item["chapter_title"] = chapter_titles.get(chapter_id) if chapter_id else None
            frame_index = message.get("frame_index")
            item["frame_index"] = frame_index + 1 if frame_index is not None else None
            if asset_type in asset_names:
                item["asset_name"] = asset_names[asset_type].get(asset_id)
        elif task.get("type") in CONTEXT_TASK_TYPES and request_data:
            try:
                request_values = json.loads(request_data)
                chapter_id = request_values.get("chapter_id")
                if chapter_id:
                    item["chapter_id"] = chapter_id
                    item["chapter_title"] = chapter_titles.get(chapter_id)
                if task["type"] == "batch-optimize":
                    item["frame_count"] = request_values.get("frame_count")
                else:
                    frame_index = request_values.get("frame_index")
                    if frame_index is not None:
                        item["frame_index"] = int(frame_index) + 1
                    item["prompt_id"] = request_values.get("prompt_id") or ""
            except Exception:
                pass
        result.append(item)

    return result
