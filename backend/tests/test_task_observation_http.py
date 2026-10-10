from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import FastAPI

from haoai_backend.authentication.errors import AuthenticationError
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.task_observation.domain import TaskRecord
from haoai_backend.task_observation.http import build_task_observation_router


NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)


class HttpUnitOfWork:
    def __init__(self, events: list[tuple[Any, ...]]) -> None:
        self.events = events
        self.tasks: dict[str, TaskRecord] = {}
        self.review_candidates: list[TaskRecord] = []

    def load_owned_task(self, actor_id: str, task_id: str) -> TaskRecord | None:
        self.events.append(("load_owned_task", actor_id, task_id))
        task = self.tasks.get(task_id)
        return task if task is not None and task.user_id == actor_id else None

    def load_task(self, task_id: str) -> TaskRecord | None:
        self.events.append(("load_task", task_id))
        return self.tasks.get(task_id)

    def first_billing_unit(self, task_id: str) -> None:
        self.events.append(("first_billing_unit", task_id))
        return None

    def count_owned_tasks(self, actor_id: str) -> int:
        self.events.append(("count_owned_tasks", actor_id))
        return 0

    def list_owned_tasks(self, actor_id: str, offset: int, limit: int) -> list[TaskRecord]:
        self.events.append(("list_owned_tasks", actor_id, offset, limit))
        return []

    def load_messages(self, message_ids: list[str]) -> dict[str, dict[str, Any]]:
        self.events.append(("load_messages", tuple(message_ids)))
        return {}

    def load_chapter_titles(self, chapter_ids: set[str]) -> dict[str, str]:
        self.events.append(("load_chapter_titles", frozenset(chapter_ids)))
        return {}

    def load_asset_names(self, asset_ids_by_kind: dict[str, list[str]]) -> dict[str, dict[str, str | None]]:
        self.events.append(("load_asset_names", asset_ids_by_kind))
        return {}

    def list_ai_review_count_candidates(self, actor_id: str, message_id: str) -> list[TaskRecord]:
        self.events.append(("list_ai_review_count_candidates", actor_id, message_id))
        return self.review_candidates

    def rollback(self) -> None:
        self.events.append(("rollback",))

    def close(self) -> None:
        self.events.append(("close",))


def make_application(
    events: list[tuple[Any, ...]],
    *,
    configured: bool = True,
    cancellation_writer: Any = None,
    cancellation_signals: Any = None,
    account_resolver_override: Any = None,
) -> tuple[FastAPI, HttpUnitOfWork, list[str]]:
    uow = HttpUnitOfWork(events)

    def factory() -> HttpUnitOfWork:
        events.append(("open",))
        return uow

    resolver_calls: list[str] = []

    def account_resolver(_request) -> TrustedActor:
        resolver_calls.append("account")
        return TrustedActor(user_id="user-a")

    def active_resolver(_request) -> TrustedActor:
        resolver_calls.append("active")
        return TrustedActor(user_id="user-a")

    resolved_account_resolver = account_resolver if configured else None
    if account_resolver_override is not None:
        resolved_account_resolver = account_resolver_override

    router = build_task_observation_router(
        uow_factory=factory if configured else None,
        resolve_account_actor=resolved_account_resolver,
        resolve_active_actor=active_resolver if configured else None,
        cancellation_signals=cancellation_signals,
        cancellation_writer=cancellation_writer,
    )
    app = FastAPI()
    app.include_router(router)
    return app, uow, resolver_calls


def send_request(app: FastAPI, method: str, url: str, **kwargs: Any) -> httpx.Response:
    async def perform() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.request(method, url, **kwargs)

    return asyncio.run(perform())


def test_router_registers_exact_nine_methods_and_keeps_literal_routes_first() -> None:
    router = build_task_observation_router(
        uow_factory=None,
        resolve_account_actor=None,
        resolve_active_actor=None,
    )
    routes = [route for route in router.routes if hasattr(route, "methods")]
    entries = {(route.path, method) for route in routes for method in route.methods or () if method not in {"HEAD", "OPTIONS"}}

    assert entries == {
        ("/api/chat/tasks", "GET"),
        ("/api/chat/submissions/{operation}", "GET"),
        ("/api/chat/tasks/list", "GET"),
        ("/api/chat/tasks/{task_id}/request", "GET"),
        ("/api/chat/ai-review/counts", "GET"),
        ("/api/chat/ai-review/{task_id}", "GET"),
        ("/api/chat/batch-optimize/running", "GET"),
        ("/api/chat/batch-optimize/{task_id}/status", "GET"),
        ("/api/chat/batch-optimize/{task_id}/cancel", "POST"),
    }
    paths = [route.path for route in routes]
    assert paths.index("/api/chat/ai-review/counts") < paths.index("/api/chat/ai-review/{task_id}")
    assert paths.index("/api/chat/batch-optimize/running") < paths.index("/api/chat/batch-optimize/{task_id}/status")


def test_submission_idempotency_key_is_a_required_query_parameter() -> None:
    events: list[tuple[Any, ...]] = []
    app, _, resolver_calls = make_application(events)

    response = send_request(
        app,
        "GET",
        "/api/chat/submissions/image.single",
        headers={"Idempotency-Key": "key-from-header"},
    )

    assert response.status_code == 422
    details = response.json()["detail"]
    assert details[0]["loc"] == ["query", "idempotency_key"]
    assert details[0]["type"] == "missing"
    assert not any(event[0] == "open" for event in events)
    assert "account" in resolver_calls


def test_authentication_error_preserves_bearer_challenge_without_business_session() -> None:
    events: list[tuple[Any, ...]] = []

    def reject_account(_request) -> TrustedActor:
        raise AuthenticationError(401, "无法验证凭据", authenticate=True)

    app, _, _ = make_application(
        events, account_resolver_override=reject_account
    )

    response = send_request(app, "GET", "/api/chat/tasks", params={"task_id": "task-a"})

    assert response.status_code == 401
    assert response.json() == {"detail": "无法验证凭据"}
    assert response.headers["www-authenticate"] == "Bearer"
    assert events == []


def test_missing_runtime_configuration_returns_503_without_business_session() -> None:
    events: list[tuple[Any, ...]] = []
    app, _, _ = make_application(events, configured=False)

    response = send_request(app, "GET", "/api/chat/tasks/list")

    assert response.status_code == 503
    assert response.json() == {"detail": "任务观察服务尚未接线"}
    assert events == []


def test_account_and_active_routes_use_their_separate_resolvers() -> None:
    events: list[tuple[Any, ...]] = []
    app, uow, resolver_calls = make_application(events)
    uow.tasks["task-a"] = TaskRecord(
        id="task-a",
        user_id="user-a",
        type="image",
        status="completed",
        result="done",
        created_at=NOW,
    )

    receipt = send_request(app, "GET", "/api/chat/tasks", params={"task_id": "task-a"})
    task_list = send_request(app, "GET", "/api/chat/tasks/list")

    assert receipt.status_code == 200
    assert receipt.json()["result"] == "done"
    assert task_list.status_code == 200
    assert task_list.json() == {"total": 0, "tasks": [], "page": 1, "page_size": 50}
    assert resolver_calls == ["account", "active"]
    assert events.count(("open",)) == 2
    assert events.count(("rollback",)) == 2
    assert events.count(("close",)) == 2


def test_static_review_counts_route_is_not_consumed_as_a_task_id() -> None:
    events: list[tuple[Any, ...]] = []
    app, uow, _ = make_application(events)
    uow.review_candidates = [
        TaskRecord(
            id="review-a",
            user_id="user-a",
            type="ai-review",
            status="completed",
            request_data=json.dumps({"source_message_id": "message-a", "prompt_id": "prompt-a"}),
        )
    ]

    response = send_request(app, "GET", "/api/chat/ai-review/counts", params={"message_id": "message-a"})

    assert response.status_code == 200
    assert response.json() == {"counts": {"prompt-a": 1}}
    assert ("list_ai_review_count_candidates", "user-a", "message-a") in events
    assert not any(event[:1] == ("load_owned_task",) for event in events)


def test_business_errors_are_translated_to_their_original_http_status_and_detail() -> None:
    events: list[tuple[Any, ...]] = []
    app, _, _ = make_application(events)

    response = send_request(app, "GET", "/api/chat/tasks/missing/request")

    assert response.status_code == 404
    assert response.json() == {"detail": "任务不存在"}
    assert events[-2:] == [("rollback",), ("close",)]


def test_missing_cancellation_writer_is_503_before_business_session_open() -> None:
    events: list[tuple[Any, ...]] = []
    app, _, _ = make_application(events)

    response = send_request(app, "POST", "/api/chat/batch-optimize/task-a/cancel")

    assert response.status_code == 503
    assert response.json() == {"detail": "任务取消服务尚未接线"}
    assert not any(event[0] == "open" for event in events)


def test_cancel_route_sets_local_signal_before_independent_writer() -> None:
    events: list[tuple[Any, ...]] = []

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

    app, uow, _ = make_application(
        events,
        cancellation_writer=Writer(),
        cancellation_signals=Registry(),
    )
    uow.tasks["task-a"] = TaskRecord(
        id="task-a", user_id="user-a", type="batch-optimize", status="queued"
    )

    response = send_request(app, "POST", "/api/chat/batch-optimize/task-a/cancel")

    assert response.status_code == 200
    assert response.json() == {"task_id": "task-a", "status": "cancelling"}
    assert events.index(("signal.set",)) < events.index(("writer.request_cancel", "task-a"))
    assert events[-2:] == [("rollback",), ("close",)]
