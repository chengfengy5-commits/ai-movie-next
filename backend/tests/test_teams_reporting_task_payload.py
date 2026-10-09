from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from haoai_backend.teams.reporting.task_payload import (
    collect_task_context,
    project_task_items,
)


def _task(
    task_id: str,
    *,
    task_type: str = "chat",
    message_id: str | None = None,
    status: str = "completed",
    result: str | None = None,
    request_data: str | None = None,
    credit_cost: int | None = 0,
    progress: int | None = 0,
) -> dict[str, Any]:
    return {
        "id": task_id,
        "type": task_type,
        "message_id": message_id,
        "status": status,
        "result": result,
        "request_data": request_data,
        "credit_cost": credit_cost,
        "progress": progress,
        "progress_message": None,
        "created_at": datetime(2026, 10, 9, 10),
        "updated_at": datetime(2026, 10, 9, 11),
    }


def test_collect_task_context_batches_messages_chapters_and_all_asset_kinds() -> None:
    tasks = [
        _task("message-task", message_id="message"),
        _task(
            "batch",
            task_type="batch-optimize",
            request_data=json.dumps({"chapter_id": "batch-chapter"}),
        ),
        _task(
            "review",
            task_type="ai-review",
            request_data=json.dumps({"chapter_id": "review-chapter"}),
        ),
        _task(
            "invalid",
            task_type="ai-review",
            request_data="{",
        ),
    ]
    messages = {
        "message": {
            "chapter_id": "message-chapter",
            "asset_type": "storyboard",
            "asset_id": "asset",
        }
    }

    message_ids, chapter_ids, asset_ids = collect_task_context(tasks, messages)

    assert message_ids == ["message"]
    assert chapter_ids == {
        "message-chapter",
        "batch-chapter",
        "review-chapter",
    }
    assert asset_ids == {
        "character": [],
        "scene": [],
        "prop": [],
        "storyboard": ["asset"],
    }


def test_task_projection_preserves_safe_results_and_special_context_rules() -> None:
    failed = _task(
        "failed",
        status="failed",
        result="x" * 6000,
        request_data="r" * 250,
        credit_cost=None,
        progress=None,
    )
    successful_large = _task(
        "large-success",
        result="x" * 501,
    )
    data_url = _task("data-url", result="data:image/png;base64,AAAA")
    normal = _task("normal", message_id="message")
    batch = _task(
        "batch",
        task_type="batch-optimize",
        message_id="orphan",
        request_data=json.dumps(
            {"chapter_id": "chapter-batch", "frame_count": 7, "frame_index": 99}
        ),
    )
    review = _task(
        "review",
        task_type="ai-review",
        message_id=None,
        request_data=json.dumps(
            {"chapter_id": "chapter-review", "frame_index": "2", "prompt_id": "p1"}
        ),
    )
    broken_review = _task(
        "broken-review",
        task_type="ai-review",
        request_data="[]",
    )
    items = project_task_items(
        [failed, successful_large, data_url, normal, batch, review, broken_review],
        {
            "message": {
                "chapter_id": "chapter-message",
                "frame_index": 0,
                "asset_type": "character",
                "asset_id": "character",
            }
        },
        {
            "chapter-message": "Message chapter",
            "chapter-batch": "Batch chapter",
            "chapter-review": "Review chapter",
        },
        {"character": {"character": "Mira"}},
    )
    by_id = {item["id"]: item for item in items}

    assert by_id["failed"]["result"] == "x" * 5000
    assert by_id["failed"]["request_data"] == "r" * 200
    assert by_id["failed"]["credit_cost"] == 0
    assert by_id["failed"]["progress"] == 0
    assert by_id["large-success"]["result"] == ""
    assert by_id["data-url"]["result"] == ""
    assert by_id["normal"]["asset_name"] == "Mira"
    assert by_id["normal"]["frame_index"] == 1
    assert by_id["normal"]["chapter_title"] == "Message chapter"
    assert by_id["normal"]["chapter_id"] is None
    assert by_id["batch"]["chapter_id"] == "chapter-batch"
    assert by_id["batch"]["frame_count"] == 7
    assert "prompt_id" not in by_id["batch"]
    assert by_id["review"]["frame_index"] == 3
    assert by_id["review"]["prompt_id"] == "p1"
    assert by_id["broken-review"]["chapter_id"] is None
    assert by_id["normal"]["created_at"] == "2026-10-09T10:00:00"
