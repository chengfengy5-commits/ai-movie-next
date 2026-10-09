from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from haoai_backend.task_observation.domain import TaskRecord
from haoai_backend.task_observation.errors import (
    TaskObservationBadRequest,
    TaskObservationConflict,
    TaskObservationForbidden,
    TaskObservationIntegrityError,
    TaskObservationNotFound,
    TaskObservationUnavailable,
)
from haoai_backend.task_observation.payload import (
    build_ai_review_counts,
    build_batch_optimize_status,
    build_task_list_payload,
    build_running_batch_optimize,
    build_task_request,
    find_running_batch_optimize,
    fold_long_strings,
)
from haoai_backend.task_observation.receipts import (
    build_submission_receipt,
    build_task_receipt,
)


@pytest.mark.parametrize(
    ("error_type", "status_code"),
    [
        (TaskObservationBadRequest, 422),
        (TaskObservationNotFound, 404),
        (TaskObservationForbidden, 403),
        (TaskObservationConflict, 409),
        (TaskObservationIntegrityError, 500),
        (TaskObservationUnavailable, 503),
    ],
)
def test_expected_errors_keep_shared_business_status(error_type, status_code: int) -> None:
    error = error_type("detail")
    assert error.status_code == status_code
    assert error.detail == "detail"


class ReceiptReader:
    def __init__(self, *, first=None, unique=None, submission=None) -> None:
        self.first = first
        self.unique = unique
        self.submission = submission
        self.calls: list[tuple[Any, ...]] = []

    def first_billing_unit(self, task_id: str):
        self.calls.append(("first", task_id))
        return self.first

    def unique_billing_unit(self, task_id: str):
        self.calls.append(("unique", task_id))
        return self.unique

    def find_submission(self, actor_id: str, operation: str, raw_key: str):
        self.calls.append(("submission", actor_id, operation, raw_key))
        return self.submission


class ObservationReader:
    def __init__(self, task=None) -> None:
        self.task = task
        self.calls: list[tuple[Any, ...]] = []

    def load_owned_task(self, actor_id: str, task_id: str):
        self.calls.append(("owned_task", actor_id, task_id))
        return self.task


def test_task_receipt_uses_present_unit_values_without_falsey_fallback() -> None:
    task = TaskRecord(
        id="task-a",
        user_id="user-a",
        type="image.single",
        status="completed",
        message_id="",
        result="raw result",
        credit_cost=91,
        billing_status="legacy",
        progress=-2,
        created_at=datetime(2026, 1, 2, 3, 4, 5),
    )
    reader = ReceiptReader(first={"billing_status": "", "quoted_amount": 0})

    receipt = build_task_receipt(task, reader)

    assert set(receipt) == {
        "id", "type", "message_id", "status", "result", "billing_status",
        "quoted_amount", "progress", "progress_message", "external_task_id",
        "external_provider", "created_at", "updated_at",
    }
    assert receipt["message_id"] is None
    assert receipt["billing_status"] == ""
    assert receipt["quoted_amount"] == 0
    assert receipt["progress"] == -2
    assert receipt["created_at"] == "2026-01-02T03:04:05"
    assert receipt["updated_at"] is None
    assert reader.calls == [("first", "task-a")]


def test_task_receipt_falls_back_only_when_no_billing_unit_exists() -> None:
    task = {
        "id": "task-a", "type": "video.single", "status": "queued",
        "billing_status": "pending", "credit_cost": -4, "progress": 0,
    }

    receipt = build_task_receipt(task, ReceiptReader(first=None))

    assert receipt["billing_status"] == "pending"
    assert receipt["quoted_amount"] == -4
    assert receipt["progress"] == 0


def test_submission_receipt_validates_operation_then_raw_key_and_keeps_seven_fields() -> None:
    with pytest.raises(TaskObservationBadRequest) as unsupported:
        build_submission_receipt("user-a", "other", None, ObservationReader(), ReceiptReader())
    assert unsupported.value.detail == "不支持的任务操作"

    submission = {
        "id": "submission-a", "user_id": "user-a", "operation": "image.single",
        "task_ids": ["task-a"],
    }
    observation = ObservationReader({"id": "task-a", "message_id": "", "status": "queued"})
    receipts = ReceiptReader(
        submission=submission,
        unique={"quoted_amount": 7, "billing_status": "reserved"},
    )
    receipt = build_submission_receipt(
        "user-a", "image.single", "  original key  ", observation, receipts
    )

    assert receipt == {
        "submission_id": "submission-a",
        "operation": "image.single",
        "task_id": "task-a",
        "message_id": None,
        "status": "queued",
        "quoted_amount": 7,
        "billing_status": "reserved",
    }
    assert receipts.calls == [("submission", "user-a", "image.single", "  original key  "),
                              ("unique", "task-a")]
    assert observation.calls == [("owned_task", "user-a", "task-a")]


@pytest.mark.parametrize("raw_key", [None, "  ", "x" * 256])
def test_submission_receipt_rejects_invalid_keys(raw_key: str | None) -> None:
    reader = ReceiptReader()
    with pytest.raises(TaskObservationBadRequest):
        build_submission_receipt("user-a", "image.single", raw_key, ObservationReader(), reader)
    assert reader.calls == []


@pytest.mark.parametrize("task_ids", [None, "task-a", [], ["task-a", "task-b"]])
def test_submission_receipt_rejects_non_unique_task_sets(task_ids: Any) -> None:
    receipts = ReceiptReader(submission={"id": "sub", "user_id": "user-a", "task_ids": task_ids})
    observation = ObservationReader()
    with pytest.raises(TaskObservationIntegrityError, match="提交记录与任务集合不一致"):
        build_submission_receipt("user-a", "video.single", "key", observation, receipts)
    assert observation.calls == []
    assert all(call[0] != "unique" for call in receipts.calls)


def test_submission_receipt_reports_missing_submission_task_and_unit() -> None:
    with pytest.raises(TaskObservationNotFound):
        build_submission_receipt("user-a", "image.single", "key", ObservationReader(), ReceiptReader())

    submission = {"id": "sub", "user_id": "user-a", "task_ids": ["task-a"]}
    observation = ObservationReader()
    with pytest.raises(TaskObservationIntegrityError, match="提交任务记录不存在"):
        build_submission_receipt(
            "user-a", "image.single", "key", observation, ReceiptReader(submission=submission)
        )

    observation = ObservationReader({"id": "task-a"})
    with pytest.raises(TaskObservationIntegrityError, match="任务计费单元记录不存在"):
        build_submission_receipt(
            "user-a", "image.single", "key", observation,
            ReceiptReader(submission=submission, unique=None),
        )


def test_request_folding_preserves_depth_and_only_folds_long_base64() -> None:
    long_base64 = "A" * 501
    data_uri = "data:image/png;base64,AAAA"
    value = {"long": long_base64, "uri": data_uri, "ordinary": "A" * 500}
    assert fold_long_strings(value) == {
        "long": "<base64 len=501>",
        "uri": f"<base64 len={len(data_uri)}>",
        "ordinary": "A" * 500,
    }

    deep = "data:text/plain,kept"
    for _ in range(7):
        deep = [deep]
    folded = fold_long_strings(deep)
    for _ in range(7):
        folded = folded[0]
    assert folded == "data:text/plain,kept"

    request = build_task_request({"id": "task-a", "request_data": "not json"})
    assert request["request"] == {"_raw": "not json"}



class TaskListReader:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def count_owned_tasks(self, actor_id: str) -> int:
        self.calls.append(("count", actor_id))
        return 8

    def list_owned_tasks(self, actor_id: str, offset: int, limit: int):
        self.calls.append(("list", actor_id, offset, limit))
        return [TaskRecord(
            id="task-a", user_id=actor_id, type="image.single", status="completed",
            message_id="message-a", created_at=datetime(2026, 1, 2),
        )]

    def load_messages(self, message_ids):
        self.calls.append(("messages", tuple(message_ids)))
        return {"message-a": {
            "chapter_id": "chapter-a", "asset_type": "character",
            "asset_id": "character-a", "frame_index": 0,
        }}

    def load_chapter_titles(self, chapter_ids):
        self.calls.append(("chapters", frozenset(chapter_ids)))
        return {"chapter-a": "第一章"}

    def load_asset_names(self, asset_ids_by_kind):
        self.calls.append(("assets", asset_ids_by_kind))
        return {"character": {"character-a": "主角"}}


def test_task_list_uses_explicit_page_window_and_public_context_projection() -> None:
    reader = TaskListReader()

    payload = build_task_list_payload("user-a", 2, 3, reader)

    assert payload["total"] == 8
    assert payload["page"] == 2
    assert payload["page_size"] == 3
    assert payload["tasks"][0]["asset_name"] == "主角"
    assert payload["tasks"][0]["chapter_title"] == "第一章"
    assert payload["tasks"][0]["frame_index"] == 1
    assert payload["tasks"][0]["chapter_id"] is None
    assert reader.calls[:3] == [
        ("count", "user-a"),
        ("list", "user-a", 3, 3),
        ("messages", ("message-a",)),
    ]
    assert reader.calls[3][0] == "chapters"
    assert reader.calls[4][0] == "assets"

def test_ai_review_counts_keep_json_recheck_and_source_failures() -> None:
    candidates = [
        {"request_data": '{"source_message_id": "m1", "prompt_id": "p"}'},
        {"request_data": '{"source_message_id": "m2", "prompt_id": "p"}'},
        {"request_data": "broken"},
    ]
    assert build_ai_review_counts("m1", candidates) == {"counts": {"p": 1}}
    assert build_ai_review_counts("", candidates) == {"counts": {}}
    with pytest.raises(AttributeError):
        build_ai_review_counts("m1", [{"request_data": "[]"}])
    with pytest.raises(TypeError):
        build_ai_review_counts("m1", [{"request_data": '{"source_message_id": "m1", "prompt_id": ["p"]}'}])


def test_batch_helpers_keep_candidate_order_and_jsondecode_only_fallback() -> None:
    candidates = [
        {"id": "bad", "request_data": "broken"},
        {"id": "first", "request_data": '{"chapter_id": "chapter-a"}'},
        {"id": "later", "request_data": '{"chapter_id": "chapter-a"}'},
    ]
    assert find_running_batch_optimize("chapter-a", candidates)["id"] == "first"
    assert find_running_batch_optimize("missing", candidates) is None
    assert build_running_batch_optimize(None) == {"task": None}
    assert build_batch_optimize_status({
        "id": "task-a", "status": "processing", "result": "not json", "progress": -1,
    }) == {
        "task_id": "task-a", "status": "processing", "progress": -1,
        "progress_message": "", "result": "not json",
    }
    assert build_batch_optimize_status({"id": "task-a", "status": "completed", "result": "false"})[
        "result"
    ] is False
    with pytest.raises(AttributeError):
        find_running_batch_optimize("chapter-a", [{"request_data": "[]"}])
