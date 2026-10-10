from __future__ import annotations

import asyncio
from datetime import datetime

import httpx
import pytest
from fastapi import FastAPI

from haoai_backend.generic_tasks.http import build_generic_tasks_router
from haoai_backend.shared.identity import TrustedActor
from generic_tasks_support import generic_uow_factory, owner_database, seed_task


class HttpPrepared:
    async def poll_once(self, external_task_id: str):
        assert external_task_id == "http-external"
        return "completed", {"result": "ok"}

    def extract_result(self, response):
        return response["result"]


class HttpProviderPort:
    def prepare(self, provider: str):
        assert provider == "test-provider"
        return HttpPrepared()


def request(app: FastAPI, method: str, path: str, *, json_body=None) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://generic-tasks.test",
            trust_env=False,
        ) as client:
            return await client.request(method, path, json=json_body)

    return asyncio.run(send())


def make_app(owner_database) -> FastAPI:
    sequence = iter(("http-created-task",))
    app = FastAPI()
    router = build_generic_tasks_router(
        uow_factory=generic_uow_factory(owner_database),
        resolve_active_actor=lambda _request: TrustedActor("user-a"),
        provider_status_port=HttpProviderPort(),
        clock=lambda: datetime(2026, 10, 10, 3, 0),
        id_factory=lambda: next(sequence),
    )
    app.include_router(router)
    return app


def test_router_registers_and_serves_all_four_generic_task_methods(owner_database) -> None:
    seed_task(
        owner_database,
        "http-owned-task",
        external_task_id="http-external",
        external_provider="test-provider",
    )
    app = make_app(owner_database)
    paths = {
        (method, route.path)
        for route in app.routes
        if hasattr(route, "methods")
        for method in route.methods or ()
        if method not in {"HEAD", "OPTIONS"}
        and route.path.startswith("/api/tasks")
    }
    assert paths == {
        ("POST", "/api/tasks"),
        ("PUT", "/api/tasks/{task_id}"),
        ("PUT", "/api/tasks/{task_id}/progress"),
        ("GET", "/api/tasks/{task_id}/external-status"),
    }

    created = request(
        app,
        "POST",
        "/api/tasks",
        json_body={"type": "image", "credit_cost": 0, "request_data": '{"model":"http"}'},
    )
    assert created.status_code == 200
    assert created.json() == {
        "id": "http-created-task",
        "status": "processing",
        "credit_cost": 0,
    }

    updated = request(
        app,
        "PUT",
        "/api/tasks/http-owned-task",
        json_body={"status": "completed", "result": ""},
    )
    assert updated.status_code == 200
    assert updated.json() == {"id": "http-owned-task", "status": "completed"}

    progress = request(
        app,
        "PUT",
        "/api/tasks/http-owned-task/progress",
        json_body={"progress": 40, "progress_message": "处理中"},
    )
    assert progress.status_code == 200
    assert progress.json() == {
        "id": "http-owned-task",
        "progress": 40,
        "progress_message": "处理中",
    }

    external = request(app, "GET", "/api/tasks/http-owned-task/external-status")
    assert external.status_code == 200
    assert external.json() == {"status": "completed", "result": "ok"}


def test_router_preserves_business_error_status_and_scalar_failure_500(owner_database) -> None:
    app = make_app(owner_database)
    controlled = request(
        app,
        "POST",
        "/api/tasks",
        json_body={"type": "batch-image", "credit_cost": 0, "request_data": "{}"},
    )
    assert controlled.status_code == 422
    assert controlled.json() == {"detail": "该任务类型由受控准入接口创建"}

    scalar = request(
        app,
        "POST",
        "/api/tasks",
        json_body={"type": "image", "credit_cost": 0, "request_data": "[]"},
    )
    assert scalar.status_code == 500


def test_router_without_active_identity_ports_returns_503(owner_database) -> None:
    app = FastAPI()
    app.include_router(
        build_generic_tasks_router(
            uow_factory=None,
            resolve_active_actor=None,
        )
    )
    response = request(app, "POST", "/api/tasks", json_body={"type": "image"})
    assert response.status_code == 503
    assert response.json() == {"detail": "通用任务服务暂不可用"}
