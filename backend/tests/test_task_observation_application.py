from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import pytest

from haoai_backend.shared.identity import TrustedActor
from haoai_backend.task_observation.application import (
    get_ai_review_counts,
    get_ai_review_status,
    get_batch_optimize_status,
    get_running_batch_optimize,
    get_submission_receipt,
    get_task_receipt,
    get_task_request,
    list_tasks,
    request_batch_optimize_cancel,
)
from haoai_backend.task_observation.domain import (
    BillingUnitRecord,
    TaskRecord,
    TaskSubmissionRecord,
    TeamMembershipRecord,
)
from haoai_backend.task_observation.errors import (
    TaskObservationBadRequest,
    TaskObservationConflict,
    TaskObservationForbidden,
    TaskObservationNotFound,
    TaskObservationUnavailable,
)


ACTOR = TrustedActor(user_id="user-a")
NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)


class FakeUnitOfWork:
    def __init__(self, events: list[tuple[Any, ...]]) -> None:
        self.events = events
        self.owned_task: TaskRecord | None = None
        self.global_task: TaskRecord | None = None
        self.message_tasks: list[TaskRecord] = []
        self.first_unit: BillingUnitRecord | None = None
        self.submission: TaskSubmissionRecord | None = None
        self.unique_unit: BillingUnitRecord | None = None
        self.membership_results: dict[tuple[str, str], list[TeamMembershipRecord | None]] = defaultdict(list)
        self.superuser = False
        self.task_count = 0
        self.page_tasks: list[TaskRecord] = []
        self.messages: dict[str, dict[str, Any]] = {}
        self.chapter_titles: dict[str, str] = {}
        self.asset_names: dict[str, dict[str, str | None]] = {}
        self.ai_review_candidates: list[TaskRecord] = []
        self.running_candidates: list[TaskRecord] = []

    def load_owned_task(self, actor_id: str, task_id: str) -> TaskRecord | None:
        self.events.append(("load_owned_task", actor_id, task_id))
        if self.owned_task is not None and self.owned_task.id == task_id and self.owned_task.user_id == actor_id:
            return self.owned_task
        return None

    def load_task(self, task_id: str) -> TaskRecord | None:
        self.events.append(("load_task", task_id))
        if self.global_task is not None and self.global_task.id == task_id:
            return self.global_task
        return None

    def find_owned_by_message(self, actor_id: str, message_id: str, limit: int = 2) -> list[TaskRecord]:
        self.events.append(("find_owned_by_message", actor_id, message_id, limit))
        return [task for task in self.message_tasks if task.user_id == actor_id and task.message_id == message_id][:limit]

    def count_owned_tasks(self, actor_id: str) -> int:
        self.events.append(("count_owned_tasks", actor_id))
        return self.task_count

    def list_owned_tasks(self, actor_id: str, offset: int, limit: int) -> list[TaskRecord]:
        self.events.append(("list_owned_tasks", actor_id, offset, limit))
        return self.page_tasks

    def load_messages(self, message_ids: list[str]) -> dict[str, dict[str, Any]]:
        self.events.append(("load_messages", tuple(message_ids)))
        return self.messages

    def load_chapter_titles(self, chapter_ids: set[str]) -> dict[str, str]:
        self.events.append(("load_chapter_titles", frozenset(chapter_ids)))
        return self.chapter_titles

    def load_asset_names(self, asset_ids_by_kind: dict[str, list[str]]) -> dict[str, dict[str, str | None]]:
        self.events.append(("load_asset_names", asset_ids_by_kind))
        return self.asset_names

    def list_ai_review_count_candidates(self, actor_id: str, message_id: str) -> list[TaskRecord]:
        self.events.append(("list_ai_review_count_candidates", actor_id, message_id))
        return self.ai_review_candidates

    def list_running_batch_optimize_candidates(self, actor_id: str) -> list[TaskRecord]:
        self.events.append(("list_running_batch_optimize_candidates", actor_id))
        return self.running_candidates

    def first_billing_unit(self, task_id: str) -> BillingUnitRecord | None:
        self.events.append(("first_billing_unit", task_id))
        return self.first_unit

    def find_submission(self, actor_id: str, operation: str, raw_key: str) -> TaskSubmissionRecord | None:
        self.events.append(("find_submission", actor_id, operation, raw_key))
        return self.submission

    def unique_billing_unit(self, task_id: str) -> BillingUnitRecord | None:
        self.events.append(("unique_billing_unit", task_id))
        return self.unique_unit

    def load_sql_superuser(self, actor_id: str) -> bool:
        self.events.append(("load_sql_superuser", actor_id))
        return self.superuser

    def load_membership(self, team_id: str, user_id: str) -> TeamMembershipRecord | None:
        self.events.append(("load_membership", team_id, user_id))
        values = self.membership_results[(team_id, user_id)]
        return values.pop(0) if values else None

    def rollback(self) -> None:
        self.events.append(("rollback",))

    def close(self) -> None:
        self.events.append(("close",))


def make_factory(uow: FakeUnitOfWork, events: list[tuple[Any, ...]]):
    def factory() -> FakeUnitOfWork:
        events.append(("open",))
        return uow

    return factory


def task(
    task_id: str = "task-a",
    *,
    user_id: str = "user-a",
    task_type: str = "image",
    status: str = "completed",
    message_id: str | None = None,
    request_data: Any = None,
    result: Any = None,
) -> TaskRecord:
    return TaskRecord(
        id=task_id,
        user_id=user_id,
        type=task_type,
        status=status,
        message_id=message_id,
        request_data=request_data,
        result=result,
        created_at=NOW,
        updated_at=NOW,
    )


def test_task_receipt_keeps_selector_error_order_and_closes_request_uow() -> None:
    events: list[tuple[Any, ...]] = []
    uow = FakeUnitOfWork(events)
    factory = make_factory(uow, events)

    with pytest.raises(TaskObservationBadRequest, match="task_id 与 message_id 只能提供一个"):
        get_task_receipt(factory, ACTOR, task_id="", message_id="m")
    assert events == [("open",), ("rollback",), ("close",)]

    events.clear()
    with pytest.raises(TaskObservationBadRequest, match="task_id 不能为空"):
        get_task_receipt(factory, ACTOR, task_id="  ")
    assert events == [("open",), ("rollback",), ("close",)]

    events.clear()
    with pytest.raises(TaskObservationBadRequest, match="必须提供 task_id 或 message_id"):
        get_task_receipt(factory, ACTOR)
    assert events == [("open",), ("rollback",), ("close",)]


def test_task_receipt_message_lookup_is_bounded_and_no_match_has_legacy_shape() -> None:
    events: list[tuple[Any, ...]] = []
    uow = FakeUnitOfWork(events)
    factory = make_factory(uow, events)

    assert get_task_receipt(factory, ACTOR, message_id="missing") == {
        "status": None,
        "result": None,
    }
    assert ("find_owned_by_message", "user-a", "missing", 2) in events

    events.clear()
    uow.message_tasks = [task("one", message_id="same"), task("two", message_id="same")]
    with pytest.raises(TaskObservationConflict, match="message_id 对应多个任务"):
        get_task_receipt(factory, ACTOR, message_id="same")
    assert ("find_owned_by_message", "user-a", "same", 2) in events
    assert events[-2:] == [("rollback",), ("close",)]


def test_task_receipt_uses_the_first_unit_without_falsey_fallback() -> None:
    events: list[tuple[Any, ...]] = []
    uow = FakeUnitOfWork(events)
    uow.owned_task = TaskRecord(
        id="task-a",
        user_id="user-a",
        type="image",
        status="completed",
        credit_cost=99,
        billing_status="task-fallback",
        created_at=NOW,
    )
    uow.first_unit = BillingUnitRecord(
        task_id="task-a",
        quoted_amount=0,
        billing_status="",
        created_at=NOW,
    )

    result = get_task_receipt(make_factory(uow, events), ACTOR, task_id="task-a")

    assert result["quoted_amount"] == 0
    assert result["billing_status"] == ""
    assert len(result) == 13
    assert ("load_owned_task", "user-a", "task-a") in events
    assert ("first_billing_unit", "task-a") in events
    assert events[-2:] == [("rollback",), ("close",)]


def test_submission_receipt_uses_one_uow_for_task_and_unique_unit() -> None:
    events: list[tuple[Any, ...]] = []
    uow = FakeUnitOfWork(events)
    uow.submission = TaskSubmissionRecord(
        id="submission-a",
        user_id="user-a",
        operation="image.single",
        idempotency_key=" key ",
        task_ids=["task-a"],
        status="accepted",
    )
    uow.owned_task = task("task-a", message_id="message-a")
    uow.unique_unit = BillingUnitRecord(
        task_id="task-a", quoted_amount=12, billing_status="quoted", created_at=NOW
    )

    result = get_submission_receipt(make_factory(uow, events), ACTOR, "image.single", " key ")

    assert result == {
        "submission_id": "submission-a",
        "operation": "image.single",
        "task_id": "task-a",
        "message_id": "message-a",
        "status": "completed",
        "quoted_amount": 12,
        "billing_status": "quoted",
    }
    assert ("find_submission", "user-a", "image.single", " key ") in events
    assert ("load_owned_task", "user-a", "task-a") in events
    assert ("unique_billing_unit", "task-a") in events
    assert events[-2:] == [("rollback",), ("close",)]


def test_task_request_checks_global_existence_before_sql_privilege() -> None:
    events: list[tuple[Any, ...]] = []
    uow = FakeUnitOfWork(events)

    with pytest.raises(TaskObservationNotFound, match="任务不存在"):
        get_task_request(make_factory(uow, events), ACTOR, "missing", team_id="team-a")

    assert events == [("open",), ("load_task", "missing"), ("rollback",), ("close",)]


def test_task_request_repeats_membership_before_checking_target_membership() -> None:
    events: list[tuple[Any, ...]] = []
    uow = FakeUnitOfWork(events)
    uow.global_task = task("task-other", user_id="user-b", request_data='{"prompt":"keep"}')
    membership = TeamMembershipRecord(
        team_id="team-a",
        user_id="user-a",
        role="admin",
        permissions='["view_tasks"]',
    )
    uow.membership_results[("team-a", "user-a")] = [membership, membership]
    uow.membership_results[("team-a", "user-b")] = [
        TeamMembershipRecord("team-a", "user-b", "member", None)
    ]

    result = get_task_request(make_factory(uow, events), ACTOR, "task-other", team_id="team-a")

    assert result["request"] == {"prompt": "keep"}
    membership_calls = [event for event in events if event[0] == "load_membership"]
    assert membership_calls == [
        ("load_membership", "team-a", "user-a"),
        ("load_membership", "team-a", "user-a"),
        ("load_membership", "team-a", "user-b"),
    ]
    assert events.index(("load_task", "task-other")) < events.index(("load_sql_superuser", "user-a"))
    assert events[-2:] == [("rollback",), ("close",)]


def test_task_request_preserves_first_membership_error_and_superuser_bypass() -> None:
    events: list[tuple[Any, ...]] = []
    uow = FakeUnitOfWork(events)
    uow.global_task = task("task-other", user_id="user-b")

    with pytest.raises(TaskObservationForbidden, match="你不是该团队成员"):
        get_task_request(make_factory(uow, events), ACTOR, "task-other", team_id="team-a")
    assert [event for event in events if event[0] == "load_membership"] == [
        ("load_membership", "team-a", "user-a")
    ]

    events.clear()
    uow.superuser = True
    result = get_task_request(make_factory(uow, events), ACTOR, "task-other")
    assert result["id"] == "task-other"
    assert events[:3] == [("open",), ("load_task", "task-other"), ("load_sql_superuser", "user-a")]
    assert not any(event[0] == "load_membership" for event in events)


def test_list_keeps_unclamped_integer_window_and_rolls_back_read_uow() -> None:
    events: list[tuple[Any, ...]] = []
    uow = FakeUnitOfWork(events)
    uow.task_count = 17

    result = list_tasks(make_factory(uow, events), ACTOR, page=0, page_size=-3)

    assert result == {"total": 17, "tasks": [], "page": 0, "page_size": -3}
    assert ("list_owned_tasks", "user-a", 3, -3) in events
    assert events[-2:] == [("rollback",), ("close",)]


def test_ai_review_counts_skip_sql_for_empty_message_and_keep_json_recheck() -> None:
    events: list[tuple[Any, ...]] = []
    uow = FakeUnitOfWork(events)
    factory = make_factory(uow, events)

    assert get_ai_review_counts(factory, ACTOR, "") == {"counts": {}}
    assert not any(event[0] == "list_ai_review_count_candidates" for event in events)

    events.clear()
    uow.ai_review_candidates = [
        TaskRecord(
            id="review-a",
            user_id="user-a",
            type="ai-review",
            status="completed",
            request_data=json.dumps({"source_message_id": "message-a", "prompt_id": "p1"}),
        )
    ]
    assert get_ai_review_counts(factory, ACTOR, "message-a") == {"counts": {"p1": 1}}
    assert ("list_ai_review_count_candidates", "user-a", "message-a") in events
    assert events[-2:] == [("rollback",), ("close",)]


def test_status_reads_keep_ownership_without_adding_task_type_filters() -> None:
    events: list[tuple[Any, ...]] = []
    uow = FakeUnitOfWork(events)
    uow.owned_task = task("status-a", task_type="unrecognized", result='[1,2]')
    factory = make_factory(uow, events)

    assert get_ai_review_status(factory, ACTOR, "status-a")["type"] == "unrecognized"
    assert get_batch_optimize_status(factory, ACTOR, "status-a")["result"] == [1, 2]
    assert get_running_batch_optimize(factory, ACTOR, "chapter-a") == {"task": None}
    assert any(event[0] == "list_running_batch_optimize_candidates" for event in events)
    assert events[-2:] == [("rollback",), ("close",)]


def test_cancel_sets_truthy_signal_before_writer_and_ignores_zero_match() -> None:
    events: list[tuple[Any, ...]] = []
    uow = FakeUnitOfWork(events)
    uow.owned_task = task("queued-a", status="queued")

    class Signal:
        def __bool__(self) -> bool:
            return True

        def set(self) -> None:
            events.append(("signal.set",))

    class Registry:
        def get(self, task_id: str) -> Signal:
            events.append(("signal.get", task_id))
            return Signal()

    class Writer:
        def request_cancel(self, task_id: str) -> bool:
            events.append(("writer.request_cancel", task_id))
            return False

    result = request_batch_optimize_cancel(
        make_factory(uow, events), ACTOR, "queued-a", Registry(), Writer()
    )

    assert result == {"task_id": "queued-a", "status": "cancelling"}
    assert events.index(("signal.set",)) < events.index(("writer.request_cancel", "queued-a"))
    assert events[-2:] == [("rollback",), ("close",)]


def test_cancel_terminal_skips_signal_and_writer_and_missing_writer_opens_no_uow() -> None:
    events: list[tuple[Any, ...]] = []
    uow = FakeUnitOfWork(events)
    uow.owned_task = task("finished-a", status="completed")

    class NeverRegistry:
        def get(self, task_id: str) -> None:
            raise AssertionError("terminal task must not access the signal registry")

    class NeverWriter:
        def request_cancel(self, task_id: str) -> bool:
            raise AssertionError("terminal task must not invoke the writer")

    assert request_batch_optimize_cancel(
        make_factory(uow, events), ACTOR, "finished-a", NeverRegistry(), NeverWriter()
    ) == {"task_id": "finished-a", "status": "cancelling"}

    events.clear()
    factory = make_factory(uow, events)
    with pytest.raises(TaskObservationUnavailable, match="任务取消服务尚未接线"):
        request_batch_optimize_cancel(factory, ACTOR, "finished-a", NeverRegistry(), None)
    assert events == []


def test_cancel_writer_failure_still_closes_the_read_uow() -> None:
    events: list[tuple[Any, ...]] = []
    uow = FakeUnitOfWork(events)
    uow.owned_task = task("processing-a", status="processing")

    class Writer:
        def request_cancel(self, task_id: str) -> bool:
            raise RuntimeError("writer acknowledgment failed")

    with pytest.raises(RuntimeError, match="writer acknowledgment failed"):
        request_batch_optimize_cancel(
            make_factory(uow, events), ACTOR, "processing-a", None, Writer()
        )
    assert events[-2:] == [("rollback",), ("close",)]
