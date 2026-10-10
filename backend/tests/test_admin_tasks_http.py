from __future__ import annotations

import asyncio

import httpx
from fastapi import FastAPI
from sqlalchemy import event

from haoai_backend.authentication.errors import AuthenticationError
from haoai_backend.admin_tasks.http import build_admin_task_router
from haoai_backend.admin_tasks.persistence import admin_task_unit_of_work_factory
from haoai_backend.shared.identity import TrustedActor

from admin_tasks_support import NOW, admin_task_database, make_task_row, seed_admin_tasks


def send_request(app: FastAPI, method: str, url: str, **kwargs) -> httpx.Response:
    async def perform() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.request(method, url, **kwargs)

    return asyncio.run(perform())


def make_app(database, resolver):
    app = FastAPI()
    factory = admin_task_unit_of_work_factory(database.session_factory)
    app.include_router(
        build_admin_task_router(
            uow_factory=factory,
            resolve_admin_actor=resolver,
        )
    )
    return app


def test_routes_return_exact_list_and_detail_shapes_for_other_accounts(admin_task_database) -> None:
    seed_admin_tasks(
        admin_task_database,
        [
            make_task_row(
                "task-a", "user-a", type="image", model_name=None,
                request_data='{"raw":false}', result="",
            ),
            make_task_row(
                "task-b", "user-b", type="video", model_name="video-model",
                request_data="{malformed", result="z" * 3000,
            ),
        ],
    )
    app = make_app(admin_task_database, lambda _request: TrustedActor(user_id="admin-user"))

    listed = send_request(app, "GET", "/api/admin/tasks?type=image")
    detail = send_request(app, "GET", "/api/admin/tasks/task-b")

    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert set(listed.json()["data"][0]) == {
        "id", "user_id", "type", "status", "credit_cost", "message_id",
        "model_name", "created_at", "updated_at",
    }
    assert listed.json()["data"][0]["user_id"] == "user-a"
    assert listed.json()["data"][0]["model_name"] == ""
    assert detail.status_code == 200
    assert set(detail.json()) == {
        "id", "user_id", "type", "status", "message_id", "request_data",
        "result", "created_at", "updated_at",
    }
    assert detail.json()["user_id"] == "user-b"
    assert detail.json()["request_data"] == "{malformed"
    assert detail.json()["result"] == "z" * 3000
    assert [session.close_calls for session in admin_task_database.session_factory.created] == [1, 1]


def test_admin_error_preserves_403_and_opens_no_business_session(admin_task_database) -> None:
    def deny(_request):
        raise AuthenticationError(403, "权限不足，仅管理员可执行此操作")

    app = make_app(admin_task_database, deny)
    response = send_request(app, "GET", "/api/admin/tasks")

    assert response.status_code == 403
    assert response.json() == {"detail": "权限不足，仅管理员可执行此操作"}
    assert admin_task_database.session_factory.created == []


def test_missing_wiring_returns_503_without_auth_or_business_session() -> None:
    resolver_calls: list[bool] = []

    def resolver(_request):
        resolver_calls.append(True)
        return TrustedActor(user_id="admin-user")

    app = FastAPI()
    app.include_router(
        build_admin_task_router(
            uow_factory=None,
            resolve_admin_actor=resolver,
        )
    )
    response = send_request(app, "GET", "/api/admin/tasks")

    assert response.status_code == 503
    assert resolver_calls == []


def test_raw_filter_sort_and_negative_page_parameters_cross_http_boundary(
    admin_task_database,
) -> None:
    seed_admin_tasks(
        admin_task_database,
        [
            make_task_row("task-b", "user-b", model_name="model-B", created_at=NOW),
            make_task_row("task-a", "user-a", model_name="model_A", created_at=NOW),
            make_task_row("task-c", "user-c", model_name="unrelated", created_at=NOW),
        ],
    )
    app = make_app(admin_task_database, lambda _request: TrustedActor(user_id="admin-user"))
    statements: list[tuple[str, object]] = []

    def capture(_connection, _cursor, statement, parameters, _context, _many) -> None:
        statements.append((statement.lower(), parameters))

    event.listen(admin_task_database.engine, "before_cursor_execute", capture)
    try:
        response = send_request(
            app,
            "GET",
            "/api/admin/tasks?skip=-3&limit=-1&sort_field=unknown"
            "&sort_order=DESC&type=&model_name=model_%",
        )
    finally:
        event.remove(admin_task_database.engine, "before_cursor_execute", capture)

    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert [item["id"] for item in response.json()["data"]] == ["task-a", "task-b"]
    list_statement, parameters = next(
        (statement, parameters)
        for statement, parameters in statements
        if statement.startswith("select ai_tasks.")
    )
    assert "order by ai_tasks.id asc" in list_statement
    assert "ai_tasks.type =" not in list_statement
    assert any("%model_%%" in repr(value) for value in parameters)
    assert parameters[-2:] == (-1, -3)
