from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from haoai_backend.shared.identity import TrustedActor
from haoai_backend.task_observation.domain import BillingUnitRecord, TaskRecord
from haoai_backend.task_observation.http import build_task_observation_router


NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)


class ControlsUnitOfWork:
    def __init__(self, task: TaskRecord | dict[str, Any] | None = None) -> None:
        self.task = task
        self.unique_unit: BillingUnitRecord | None = None
        self.units: list[dict[str, Any]] = []
        self.events: list[tuple[Any, ...]] = []

    def load_owned_task(self, actor_id: str, task_id: str):
        self.events.append(("load_owned_task", actor_id, task_id))
        task_id_value = (
            self.task.get("id")
            if isinstance(self.task, dict)
            else getattr(self.task, "id", None)
        )
        owner_id_value = (
            self.task.get("user_id")
            if isinstance(self.task, dict)
            else getattr(self.task, "user_id", None)
        )
        if self.task is None or task_id_value != task_id or owner_id_value != actor_id:
            return None
        return self.task

    def unique_billing_unit(self, task_id: str) -> BillingUnitRecord | None:
        self.events.append(("unique_billing_unit", task_id))
        return self.unique_unit

    def list_task_billing_units(self, task_id: str) -> list[dict[str, Any]]:
        self.events.append(("list_task_billing_units", task_id))
        return self.units

    def rollback(self) -> None:
        self.events.append(("rollback",))

    def close(self) -> None:
        self.events.append(("close",))


class CancellationPort:
    def __init__(self, events: list[tuple[Any, ...]]) -> None:
        self.events = events

    def cancel_owned(self, actor: TrustedActor, task_id: str) -> str:
        self.events.append(("cancel_owned", actor.user_id, task_id))
        return "settled"


def _app(
    uow: ControlsUnitOfWork,
    events: list[tuple[Any, ...]],
    *,
    frame_review: Any = None,
    frame_optimize: Any = None,
    haoai_group: Any = None,
) -> FastAPI:
    def factory() -> ControlsUnitOfWork:
        events.append(("open",))
        return uow

    def account_actor(_request) -> TrustedActor:
        events.append(("account_actor",))
        return TrustedActor(user_id="user-a")

    def active_actor(_request) -> TrustedActor:
        events.append(("active_actor",))
        return TrustedActor(user_id="user-a")

    router = build_task_observation_router(
        uow_factory=factory,
        resolve_account_actor=account_actor,
        resolve_active_actor=active_actor,
        frame_review_cancellation=frame_review,
        frame_optimize_cancellation=frame_optimize,
        haoai_group_cancellation=haoai_group,
    )
    app = FastAPI()
    app.include_router(router)
    return app


def _send(app: FastAPI, method: str, path: str) -> httpx.Response:
    async def perform() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            return await client.request(method, path)

    return asyncio.run(perform())


def test_controls_gets_fresh_frame_optimize_capability_without_cancel_side_effects() -> None:
    events: list[tuple[Any, ...]] = []
    uow = ControlsUnitOfWork(
        TaskRecord(
            id="task-a",
            user_id="user-a",
            type="batch-optimize",
            status="processing",
            request_data=json.dumps({"operation": "frame.optimize"}),
            created_at=NOW,
        )
    )
    uow.units = [{"operation": "frame.optimize", "step_graph_version": "frame-optimize-v1"}]
    app = _app(
        uow,
        events,
        frame_optimize=CancellationPort(events),
    )

    response = _send(app, "GET", "/api/chat/tasks/task-a/controls")

    assert response.status_code == 200
    assert response.json() == {
        "task_id": "task-a",
        "status": "processing",
        "cancel": {"allowed": True, "reason": "available"},
    }
    assert not any(event[0] in {"cancel_owned", "commit", "signal.set"} for event in events)
    assert uow.events[-2:] == [("rollback",), ("close",)]
    assert ("active_actor",) in events


def test_controls_remain_owner_scoped_and_keep_post_dispatch_contract() -> None:
    events: list[tuple[Any, ...]] = []
    uow = ControlsUnitOfWork(
        TaskRecord(
            id="task-a",
            user_id="user-b",
            type="batch-optimize",
            status="processing",
            request_data=json.dumps({"operation": "frame.optimize"}),
            created_at=NOW,
        )
    )
    uow.units = [{"operation": "frame.optimize", "step_graph_version": "frame-optimize-v1"}]
    port = CancellationPort(events)
    app = _app(uow, events, frame_optimize=port)

    get_response = _send(app, "GET", "/api/chat/tasks/task-a/controls")
    post_response = _send(app, "POST", "/api/chat/batch-optimize/task-a/cancel")

    assert get_response.status_code == 404
    assert get_response.json() == {"detail": "任务不存在"}
    assert post_response.status_code == 404
    assert post_response.json() == {"detail": "任务不存在"}
    assert not any(event[0] == "cancel_owned" for event in events)


def test_controls_show_runtime_and_lifecycle_reasons_without_changing_task_state() -> None:
    cases = (
        ("processing", None, "runtime_unavailable"),
        ("completed", CancellationPort([]), "terminal"),
        ("cancelling", CancellationPort([]), "already_requested"),
    )
    for status, port, reason in cases:
        events: list[tuple[Any, ...]] = []
        task = TaskRecord(
            id="task-a",
            user_id="user-a",
            type="batch-optimize",
            status=status,
            request_data=json.dumps({"operation": "frame.optimize"}),
            created_at=NOW,
        )
        uow = ControlsUnitOfWork(task)
        uow.units = [{"operation": "frame.optimize", "step_graph_version": "frame-optimize-v1"}]
        response = _send(
            _app(uow, events, frame_optimize=port),
            "GET",
            "/api/chat/tasks/task-a/controls",
        )

        assert response.status_code == 200
        assert response.json() == {
            "task_id": "task-a",
            "status": status,
            "cancel": {"allowed": False, "reason": reason},
        }
        assert not any(event[0] == "cancel_owned" for event in events)


def test_controls_recognize_persisted_cancel_marker_from_owned_task_row() -> None:
    events: list[tuple[Any, ...]] = []
    uow = ControlsUnitOfWork(
        {
            "id": "task-a",
            "user_id": "user-a",
            "type": "batch-optimize",
            "status": "processing",
            "request_data": json.dumps({"operation": "frame.optimize"}),
            "cancellation_reason": "frame_optimize_cancel:cancel-request-1",
        }
    )
    uow.units = [{"operation": "frame.optimize", "step_graph_version": "frame-optimize-v1"}]

    response = _send(
        _app(uow, events, frame_optimize=CancellationPort(events)),
        "GET",
        "/api/chat/tasks/task-a/controls",
    )

    assert response.status_code == 200
    assert response.json()["cancel"] == {
        "allowed": False,
        "reason": "already_requested",
    }


def test_controls_reject_unsupported_task_family() -> None:
    events: list[tuple[Any, ...]] = []
    uow = ControlsUnitOfWork(
        TaskRecord(
            id="task-a",
            user_id="user-a",
            type="image-single",
            status="processing",
            request_data="{}",
            created_at=NOW,
        )
    )

    response = _send(_app(uow, events), "GET", "/api/chat/tasks/task-a/controls")

    assert response.status_code == 200
    assert response.json()["cancel"] == {
        "allowed": False,
        "reason": "unsupported_operation",
    }


def test_controls_supports_frame_review_and_rejects_conflicting_billing_graphs() -> None:
    events: list[tuple[Any, ...]] = []
    review_uow = ControlsUnitOfWork(
        TaskRecord(
            id="review-a",
            user_id="user-a",
            type="ai-review",
            status="queued",
            request_data=json.dumps({"operation": "frame.review"}),
            created_at=NOW,
        )
    )
    review_uow.unique_unit = BillingUnitRecord(
        task_id="review-a",
        quoted_amount=4,
        billing_status="quoted",
        operation="frame.review",
        step_graph_version="frame-review-v1",
    )
    review_response = _send(
        _app(review_uow, events, frame_review=CancellationPort(events)),
        "GET",
        "/api/chat/tasks/review-a/controls",
    )
    assert review_response.status_code == 200
    assert review_response.json()["cancel"] == {
        "allowed": True,
        "reason": "available",
    }

    optimize_uow = ControlsUnitOfWork(
        TaskRecord(
            id="task-b",
            user_id="user-a",
            type="batch-optimize",
            status="processing",
            request_data=json.dumps({"operation": "frame.optimize"}),
            created_at=NOW,
        )
    )
    optimize_uow.units = [
        {"operation": "frame.optimize", "step_graph_version": "frame-optimize-v1"},
        {"operation": "image.single", "step_graph_version": "image-single-v1"},
    ]
    conflict_response = _send(
        _app(optimize_uow, events, frame_optimize=CancellationPort(events)),
        "GET",
        "/api/chat/tasks/task-b/controls",
    )
    assert conflict_response.status_code == 200
    assert conflict_response.json()["cancel"] == {
        "allowed": False,
        "reason": "unsafe_task_graph",
    }


def test_controls_rejects_review_unit_when_request_names_optimize_graph() -> None:
    events: list[tuple[Any, ...]] = []
    task = TaskRecord(
        id="review-conflict",
        user_id="user-a",
        type="ai-review",
        status="queued",
        request_data=json.dumps({"operation": "frame.optimize"}),
        created_at=NOW,
    )
    uow = ControlsUnitOfWork(task)
    uow.unique_unit = BillingUnitRecord(
        task_id="review-conflict",
        quoted_amount=4,
        billing_status="quoted",
        operation="frame.review",
        step_graph_version="frame-review-v1",
    )
    response = _send(
        _app(uow, events, frame_review=CancellationPort(events)),
        "GET",
        "/api/chat/tasks/review-conflict/controls",
    )

    assert response.status_code == 200
    assert response.json()["cancel"] == {
        "allowed": False,
        "reason": "unsafe_task_graph",
    }
    assert not any(event[0] == "cancel_owned" for event in events)


def test_controls_rejects_optimize_unit_when_request_names_single_image_graph() -> None:
    events: list[tuple[Any, ...]] = []
    uow = ControlsUnitOfWork(
        TaskRecord(
            id="optimize-conflict",
            user_id="user-a",
            type="batch-optimize",
            status="processing",
            request_data=json.dumps({"operation": "image.single"}),
            created_at=NOW,
        )
    )
    uow.units = [{"operation": "frame.optimize", "step_graph_version": "frame-optimize-v1"}]
    response = _send(
        _app(uow, events, frame_optimize=CancellationPort(events)),
        "GET",
        "/api/chat/tasks/optimize-conflict/controls",
    )

    assert response.status_code == 200
    assert response.json()["cancel"] == {
        "allowed": False,
        "reason": "unsafe_task_graph",
    }
    assert not any(event[0] == "cancel_owned" for event in events)


def test_controls_allow_legacy_registered_units_without_request_graph_markers() -> None:
    events: list[tuple[Any, ...]] = []
    review_uow = ControlsUnitOfWork(
        TaskRecord(
            id="legacy-review",
            user_id="user-a",
            type="ai-review",
            status="queued",
            request_data="{}",
            created_at=NOW,
        )
    )
    review_uow.unique_unit = BillingUnitRecord(
        task_id="legacy-review",
        quoted_amount=4,
        billing_status="quoted",
        operation="frame.review",
        step_graph_version="frame-review-v1",
    )
    optimize_uow = ControlsUnitOfWork(
        TaskRecord(
            id="legacy-optimize",
            user_id="user-a",
            type="batch-optimize",
            status="processing",
            request_data="{}",
            created_at=NOW,
        )
    )
    optimize_uow.units = [
        {"operation": "frame.optimize", "step_graph_version": "frame-optimize-v1"}
    ]

    for uow, task_id, kwargs in (
        (review_uow, "legacy-review", {"frame_review": CancellationPort(events)}),
        (optimize_uow, "legacy-optimize", {"frame_optimize": CancellationPort(events)}),
    ):
        response = _send(
            _app(uow, events, **kwargs),
            "GET",
            f"/api/chat/tasks/{task_id}/controls",
        )

        assert response.status_code == 200
        assert response.json()["cancel"] == {
            "allowed": True,
            "reason": "available",
        }
    assert not any(event[0] == "cancel_owned" for event in events)


@pytest.mark.parametrize("field", ("graph_version", "step_graph_version"))
def test_controls_rejects_review_unit_when_request_names_other_graph_version(
    field: str,
) -> None:
    events: list[tuple[Any, ...]] = []
    uow = ControlsUnitOfWork(
        TaskRecord(
            id="review-version-conflict",
            user_id="user-a",
            type="ai-review",
            status="queued",
            request_data=json.dumps({field: "frame-optimize-v1"}),
            created_at=NOW,
        )
    )
    uow.unique_unit = BillingUnitRecord(
        task_id="review-version-conflict",
        quoted_amount=4,
        billing_status="quoted",
        operation="frame.review",
        step_graph_version="frame-review-v1",
    )

    response = _send(
        _app(uow, events, frame_review=CancellationPort(events)),
        "GET",
        "/api/chat/tasks/review-version-conflict/controls",
    )

    assert response.status_code == 200
    assert response.json()["cancel"] == {
        "allowed": False,
        "reason": "unsafe_task_graph",
    }


@pytest.mark.parametrize("field", ("graph_version", "step_graph_version"))
def test_controls_rejects_optimize_unit_when_request_names_other_graph_version(
    field: str,
) -> None:
    events: list[tuple[Any, ...]] = []
    uow = ControlsUnitOfWork(
        TaskRecord(
            id="optimize-version-conflict",
            user_id="user-a",
            type="batch-optimize",
            status="processing",
            request_data=json.dumps({field: "image-single-v1"}),
            created_at=NOW,
        )
    )
    uow.units = [
        {"operation": "frame.optimize", "step_graph_version": "frame-optimize-v1"}
    ]

    response = _send(
        _app(uow, events, frame_optimize=CancellationPort(events)),
        "GET",
        "/api/chat/tasks/optimize-version-conflict/controls",
    )

    assert response.status_code == 200
    assert response.json()["cancel"] == {
        "allowed": False,
        "reason": "unsafe_task_graph",
    }


def test_controls_use_read_only_group_inspection_and_never_call_cancel_adapter() -> None:
    events: list[tuple[Any, ...]] = []

    class GroupPort(CancellationPort):
        def inspect_controls(self, actor: TrustedActor, task_id: str):
            self.events.append(("inspect_controls", actor.user_id, task_id))
            return "processing", "available"

    uow = ControlsUnitOfWork(
        TaskRecord(
            id="task-a",
            user_id="user-a",
            type="video-single",
            status="processing",
            request_data=json.dumps({"graph_version": "haoai-attempt-group-v1"}),
            created_at=NOW,
        )
    )
    app = _app(uow, events, haoai_group=GroupPort(events))

    response = _send(app, "GET", "/api/chat/tasks/task-a/controls")

    assert response.status_code == 200
    assert response.json() == {
        "task_id": "task-a",
        "status": "processing",
        "cancel": {"allowed": True, "reason": "available"},
    }
    assert ("inspect_controls", "user-a", "task-a") in events
    assert not any(event[0] == "cancel_owned" for event in events)


def test_controls_fail_closed_for_malformed_group_and_keep_ordinary_video_unsupported() -> None:
    class GroupPort(CancellationPort):
        def __init__(self, events, *, reason):
            super().__init__(events)
            self.reason = reason

        def inspect_controls(self, actor: TrustedActor, task_id: str):
            self.events.append(("inspect_controls", actor.user_id, task_id))
            return "processing", self.reason

    for request_data, group_reason, expected in (
        (
            json.dumps({"step_graph_version": "haoai-attempt-group-v1"}),
            "unsafe_task_graph",
            "unsafe_task_graph",
        ),
        ("{}", "not_selected", "unsupported_operation"),
    ):
        events: list[tuple[Any, ...]] = []
        uow = ControlsUnitOfWork(
            TaskRecord(
                id="task-a",
                user_id="user-a",
                type="video-single",
                status="processing",
                request_data=request_data,
                created_at=NOW,
            )
        )
        app = _app(uow, events, haoai_group=GroupPort(events, reason=group_reason))

        response = _send(app, "GET", "/api/chat/tasks/task-a/controls")

        assert response.status_code == 200
        assert response.json()["cancel"] == {"allowed": False, "reason": expected}
        assert not any(event[0] == "cancel_owned" for event in events)


def test_existing_cancel_post_still_dispatches_once_to_the_selected_adapter() -> None:
    events: list[tuple[Any, ...]] = []
    uow = ControlsUnitOfWork(
        TaskRecord(
            id="task-a",
            user_id="user-a",
            type="batch-optimize",
            status="processing",
            request_data=json.dumps({"operation": "frame.optimize"}),
            created_at=NOW,
        )
    )
    uow.units = [{"operation": "frame.optimize", "step_graph_version": "frame-optimize-v1"}]
    app = _app(uow, events, frame_optimize=CancellationPort(events))

    response = _send(app, "POST", "/api/chat/batch-optimize/task-a/cancel")

    assert response.status_code == 200
    assert response.json() == {"task_id": "task-a", "status": "cancelling"}
    assert events.count(("cancel_owned", "user-a", "task-a")) == 1
