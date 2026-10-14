from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from jose import jwt
from sqlalchemy import create_engine, event, insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.app import create_app
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.tables import (
    metadata as authentication_metadata,
    users as authentication_users,
)
from haoai_backend.user_profiles.tables import metadata as user_profiles_metadata
from haoai_backend.prompts.domain import PromptActor
from haoai_backend.prompts.errors import PromptForbidden
from haoai_backend.prompts.http import build_prompts_router
from haoai_backend.prompts.persistence import (
    SqlAlchemyPromptsUnitOfWork,
    prompts_unit_of_work_factory,
)
from haoai_backend.prompts.tables import (
    metadata,
    prompt_configs,
    system_prompts,
    user_prompt_orders,
    users,
)

STAMP = datetime(2026, 10, 13, 9, 30)
AUTH_SECRET = "prompts-app-integration-test-secret"


def make_app(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'prompts-http.sqlite'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    metadata.create_all(engine)
    session_factory = sessionmaker(
        engine,
        autoflush=False,
        expire_on_commit=False,
        future=True,
    )
    with engine.begin() as connection:
        connection.execute(insert(users), [{"id": "user-a"}, {"id": "user-b"}])

    def resolve_active(request) -> PromptActor:
        return PromptActor(
            request.headers.get("x-user-id", "user-a"),
            request.headers.get("x-admin") == "1",
        )

    def resolve_admin(request) -> PromptActor:
        if request.headers.get("x-admin") != "1":
            raise PromptForbidden()
        return PromptActor(request.headers.get("x-user-id", "user-a"), True)

    app = FastAPI()
    router = build_prompts_router(
        uow_factory=prompts_unit_of_work_factory(session_factory),
        resolve_active_actor=resolve_active,
        resolve_administrator=resolve_admin,
    )
    app.include_router(router)
    return engine, session_factory, app, router


def send(
    app: FastAPI,
    method: str,
    path: str,
    *,
    admin: bool = False,
    body: Any = None,
    headers: dict[str, str] | None = None,
):
    async def request():
        request_headers = {"x-user-id": "user-a"}
        if admin:
            request_headers["x-admin"] = "1"
        if headers is not None:
            request_headers.update(headers)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://prompts.test",
        ) as client:
            return await client.request(method, path, headers=request_headers, json=body)

    return asyncio.run(request())


def test_async_routes_dispatch_sync_database_work_to_worker_threads(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'prompts-worker.sqlite'}")
    metadata.create_all(engine)
    thread_events: list[tuple[str, int]] = []
    main_thread = threading.get_ident()

    class ObservedSession(Session):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.info["thread_events"].append(("session-open", threading.get_ident()))

        def close(self) -> None:
            self.info["thread_events"].append(("session-close", threading.get_ident()))
            super().close()

    session_maker = sessionmaker(
        engine,
        class_=ObservedSession,
        autoflush=False,
        expire_on_commit=False,
        future=True,
        info={"thread_events": thread_events},
    )
    with engine.begin() as connection:
        connection.execute(insert(users), [{"id": "user-a"}])
        connection.execute(
            insert(prompt_configs),
            [
                {
                    "id": prompt_id,
                    "user_id": "user-a",
                    "category": "story",
                    "name": prompt_id,
                    "system_prompt": "system",
                    "user_prompt": "user",
                    "sort_order": 0,
                    "created_at": STAMP,
                    "updated_at": STAMP,
                }
                for prompt_id in ("personal", "to-delete")
            ],
        )
        connection.execute(
            insert(system_prompts).values(
                id="system",
                category="story",
                name="system",
                system_prompt="system",
                user_prompt="user",
                sort_order=0,
                is_active=True,
                created_at=STAMP,
                updated_at=STAMP,
            )
        )

    @event.listens_for(engine, "before_cursor_execute")
    def record_sql(_connection, _cursor, _statement, _parameters, _context, _many) -> None:
        thread_events.append(("sql", threading.get_ident()))

    def make_uow() -> SqlAlchemyPromptsUnitOfWork:
        thread_events.append(("uow-open", threading.get_ident()))
        return SqlAlchemyPromptsUnitOfWork(session_maker())

    def active_actor(request) -> PromptActor:
        thread_events.append(("actor", threading.get_ident()))
        return PromptActor("user-a", request.headers.get("x-admin") == "1")

    def admin_actor(request) -> PromptActor:
        thread_events.append(("actor", threading.get_ident()))
        return PromptActor("user-a", True)

    app = FastAPI()
    app.include_router(
        build_prompts_router(
            uow_factory=make_uow,
            resolve_active_actor=active_actor,
            resolve_administrator=admin_actor,
        )
    )
    requests = (
        ("GET", "/api/prompts", None, False),
        ("GET", "/api/prompts/personal", None, False),
        ("POST", "/api/prompts", {"category": "story", "name": "created"}, False),
        ("PUT", "/api/prompts/personal", {"name": "updated"}, False),
        ("DELETE", "/api/prompts/to-delete", None, False),
        ("POST", "/api/prompts/personal/promote-to-system", None, True),
        ("POST", "/api/prompts/system/demote-to-personal", None, True),
        ("POST", "/api/prompts/reorder", {"category": "story", "prompt_ids": ["personal"]}, False),
    )
    try:
        for method, path, body, admin in requests:
            response = send(app, method, path, body=body, admin=admin)
            assert response.status_code == 200, response.text

        for action in ("actor", "uow-open", "session-open", "session-close", "sql"):
            action_threads = [thread for name, thread in thread_events if name == action]
            assert action_threads, action
            assert all(thread != main_thread for thread in action_threads), (action, thread_events)
        assert sum(name == "actor" for name, _ in thread_events) == len(requests)
        assert sum(name == "uow-open" for name, _ in thread_events) == len(requests)
        assert sum(name == "session-open" for name, _ in thread_events) == len(requests)
        assert sum(name == "session-close" for name, _ in thread_events) == len(requests)

        request_events = [event for event in thread_events if event[0] in {"actor", "uow-open", "session-open", "session-close"}]
        for index, (name, _thread) in enumerate(request_events):
            if name == "actor":
                next_actor = next(
                    (position for position in range(index + 1, len(request_events)) if request_events[position][0] == "actor"),
                    len(request_events),
                )
                actions = [event_name for event_name, _event_thread in request_events[index:next_actor]]
                assert actions.index("actor") < actions.index("uow-open") < actions.index("session-open") < actions.index("session-close"), actions
    finally:
        engine.dispose()


def test_prompt_body_routes_authenticate_before_field_validation(tmp_path: Path) -> None:
    engine, session_factory, _app, _router = make_app(tmp_path)
    calls: list[str] = []

    def reject_unauthenticated(request) -> PromptActor:
        calls.append(request.url.path)
        raise HTTPException(401, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})

    app = FastAPI()
    app.include_router(
        build_prompts_router(
            uow_factory=prompts_unit_of_work_factory(session_factory),
            resolve_active_actor=reject_unauthenticated,
            resolve_administrator=reject_unauthenticated,
        )
    )
    cases = (
        ("POST", "/api/prompts", {"category": [], "name": "bad"}),
        ("PUT", "/api/prompts/prompt-id", {"name": []}),
        ("POST", "/api/prompts/reorder", {"category": [], "prompt_ids": []}),
    )
    try:
        for method, path, body in cases:
            response = send(app, method, path, body=body)
            assert response.status_code == 401, response.text
            assert response.headers["www-authenticate"] == "Bearer"
        assert calls == [path for _method, path, _body in cases]
    finally:
        engine.dispose()


def test_create_app_wires_prompts_and_profiles_to_public_authentication(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'prompts-app-integration.sqlite'}")
    authentication_metadata.create_all(engine)
    metadata.create_all(engine)
    user_profiles_metadata.create_all(engine)
    events: list[str] = []
    auth_sessions: list[Session] = []
    business_sessions: list[Session] = []

    class ObservedSession(Session):
        def close(self) -> None:
            self.info["events"].append(f"{self.info['kind']}-close")
            super().close()

    def factory_for(kind: str, opened: list[Session]):
        maker = sessionmaker(
            engine,
            class_=ObservedSession,
            autoflush=False,
            expire_on_commit=False,
            future=True,
            info={"kind": kind, "events": events},
        )

        def factory() -> Session:
            events.append(f"{kind}-factory")
            session = maker()
            opened.append(session)
            return session

        return factory

    auth_session_factory = factory_for("auth", auth_sessions)
    business_session_factory = factory_for("business", business_sessions)
    with engine.begin() as connection:
        connection.execute(
            insert(authentication_users).values(
                id="viewer",
                username="viewer",
                email="viewer@example.test",
                hashed_password="unused",
                is_superuser=False,
                membership_type="premium",
                membership_expires_at=None,
                avatar_url="stored-avatar",
                bio="before",
                created_at=STAMP,
                password_updated_at=STAMP,
            )
        )
        connection.execute(
            insert(authentication_users).values(
                id="admin",
                username="admin",
                email="admin@example.test",
                hashed_password="unused",
                is_superuser=True,
                membership_type="premium",
                membership_expires_at=None,
                avatar_url="admin-avatar",
                bio=None,
                created_at=STAMP,
                password_updated_at=STAMP,
            )
        )
        connection.execute(
            insert(prompt_configs).values(
                id="viewer-prompt",
                user_id="viewer",
                category="story",
                name="viewer prompt",
                system_prompt="system",
                user_prompt="viewer",
                sort_order=0,
                created_at=STAMP,
                updated_at=STAMP,
            )
        )
        connection.execute(
            insert(prompt_configs).values(
                id="admin-prompt",
                user_id="admin",
                category="story",
                name="admin prompt",
                system_prompt="system",
                user_prompt="admin",
                sort_order=0,
                created_at=STAMP,
                updated_at=STAMP,
            )
        )

    runtime = AuthenticationRuntime(
        session_factory=auth_session_factory,
        secret_key=AUTH_SECRET,
        clock=lambda: STAMP,
    )
    app = create_app(
        session_factory=business_session_factory,
        authentication_runtime=runtime,
    )
    try:
        assert len(app.routes) == 242
        assert events == []
        assert auth_sessions == [] and business_sessions == []

        cold = send(
            create_app(),
            "GET",
            "/api/prompts",
            headers={"X-User-ID": "admin"},
        )
        assert cold.status_code == 503
        assert cold.json() == {"detail": "提示词服务尚未接线"}
        assert events == []

        missing_auth = send(
            app,
            "GET",
            "/api/prompts",
            headers={"X-User-ID": "admin"},
        )
        assert missing_auth.status_code == 401
        assert missing_auth.headers["www-authenticate"] == "Bearer"
        assert business_sessions == []
        assert len(auth_sessions) == 1 and events.count("auth-close") == 1

        events.clear()
        viewer_token = jwt.encode(
            {"sub": "viewer", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
            AUTH_SECRET,
            algorithm="HS256",
        )
        prompts = send(
            app,
            "GET",
            "/api/prompts",
            headers={
                "Authorization": f"Bearer {viewer_token}",
                "X-User-ID": "admin",
            },
        )
        assert prompts.status_code == 200, prompts.text
        assert [prompt["id"] for prompt in prompts.json()] == ["viewer-prompt"]
        assert events.index("auth-close") < events.index("business-factory")
        assert events.index("business-close") > events.index("business-factory")

        events.clear()
        profile = send(
            app,
            "PUT",
            "/api/users/me/profile",
            headers={
                "Authorization": f"Bearer {viewer_token}",
                "X-User-ID": "admin",
            },
            body={"bio": "updated", "user_id": "admin"},
        )
        assert profile.status_code == 200, profile.text
        assert profile.json()["id"] == "viewer"
        assert profile.json()["bio"] == "updated"
        assert events.index("auth-close") < events.index("business-factory")
        assert events.index("business-close") > events.index("business-factory")

        events.clear()
        auth_session_count = len(auth_sessions)

        def reject_generic_actor(_request):
            raise AssertionError("prompt routes must not use the generic TrustedActor resolver")

        custom_app = create_app(
            session_factory=business_session_factory,
            authentication_runtime=runtime,
            resolve_actor=reject_generic_actor,
            resolve_prompt_active_actor=lambda _request: PromptActor("viewer", False),
            resolve_prompt_administrator=lambda _request: PromptActor("admin", True),
        )
        custom_list = send(custom_app, "GET", "/api/prompts")
        assert custom_list.status_code == 200, custom_list.text
        assert [prompt["id"] for prompt in custom_list.json()] == ["viewer-prompt"]
        promoted = send(
            custom_app,
            "POST",
            "/api/prompts/admin-prompt/promote-to-system",
        )
        assert promoted.status_code == 200, promoted.text
        assert promoted.json()["is_system"] is True
        assert "is_active" not in promoted.json()
        assert len(auth_sessions) == auth_session_count
    finally:
        engine.dispose()


def test_asgi_exercises_all_eight_routes_and_preserves_wire_defaults(tmp_path: Path) -> None:
    engine, session_factory, app, router = make_app(tmp_path)
    with engine.begin() as connection:
        connection.execute(
            insert(system_prompts).values(
                id="disabled-system",
                category="story",
                name="disabled",
                system_prompt="system",
                user_prompt="user",
                sort_order=0,
                is_active=False,
                created_at=STAMP,
                updated_at=STAMP,
            )
        )
    try:
        routes = {
            (route.path, method)
            for route in router.routes
            for method in getattr(route, "methods", set())
        }
        assert routes == {
            ("/api/prompts", "GET"),
            ("/api/prompts/{prompt_id}", "GET"),
            ("/api/prompts", "POST"),
            ("/api/prompts/{prompt_id}", "PUT"),
            ("/api/prompts/{prompt_id}", "DELETE"),
            ("/api/prompts/{prompt_id}/promote-to-system", "POST"),
            ("/api/prompts/{prompt_id}/demote-to-personal", "POST"),
            ("/api/prompts/reorder", "POST"),
        }

        created = send(
            app,
            "POST",
            "/api/prompts",
            body={
                "category": "story",
                "name": "HTTP prompt",
                "system_prompt": None,
                "user_prompt": "write",
                "sort_order": 88,
                "is_system": True,
            },
        )
        assert created.status_code == 200
        prompt_id = created.json()["id"]
        assert created.json()["sort_order"] == 1
        assert created.json()["is_active"] is True

        updated_system = send(
            app,
            "PUT",
            "/api/prompts/disabled-system",
            admin=True,
            body={"name": "still disabled"},
        )
        assert updated_system.status_code == 200
        assert updated_system.json()["is_active"] is True
        with engine.connect() as connection:
            stored_active = connection.execute(
                select(system_prompts.c.is_active).where(
                    system_prompts.c.id == "disabled-system"
                )
            ).scalar_one()
        assert stored_active is False

        listed = send(app, "GET", "/api/prompts").json()
        assert [item["id"] for item in listed] == [prompt_id]
        assert "is_active" not in listed[0]
        fetched = send(app, "GET", f"/api/prompts/{prompt_id}")
        assert fetched.status_code == 200
        assert fetched.json()["is_active"] is True

        updated = send(
            app,
            "PUT",
            f"/api/prompts/{prompt_id}",
            body={"is_active": False, "system_prompt": None},
        )
        assert updated.status_code == 200
        assert updated.json()["is_active"] is False
        assert updated.json()["system_prompt"] is None
        assert send(app, "GET", f"/api/prompts/{prompt_id}").json()["is_active"] is True

        reordered = send(
            app,
            "POST",
            "/api/prompts/reorder",
            body={"category": "story", "prompt_ids": [prompt_id, prompt_id, "stale"]},
        )
        assert reordered.status_code == 200
        assert reordered.json() == {"message": "排序已更新"}
        with engine.connect() as connection:
            duplicate_refs = connection.execute(
                select(user_prompt_orders.c.prompt_id)
                .where(user_prompt_orders.c.user_id == "user-a")
                .order_by(user_prompt_orders.c.sort_order)
            ).all()
        assert duplicate_refs == [(prompt_id,), (prompt_id,), ("stale",)]

        promoted = send(
            app,
            "POST",
            f"/api/prompts/{prompt_id}/promote-to-system",
            admin=True,
        )
        assert promoted.status_code == 200
        promoted_id = promoted.json()["id"]
        assert promoted.json()["is_system"] is True
        assert "is_active" not in promoted.json()
        assert set(promoted.json()) == {
            "id",
            "user_id",
            "category",
            "name",
            "system_prompt",
            "user_prompt",
            "sort_order",
            "is_system",
            "created_at",
            "updated_at",
        }

        demoted = send(
            app,
            "POST",
            f"/api/prompts/{promoted_id}/demote-to-personal",
            admin=True,
        )
        assert demoted.status_code == 200
        assert demoted.json()["is_system"] is False
        assert demoted.json()["user_id"] == "user-a"
        assert demoted.json()["is_active"] is True
        assert set(demoted.json()) == {
            "id",
            "user_id",
            "category",
            "name",
            "system_prompt",
            "user_prompt",
            "sort_order",
            "is_system",
            "is_active",
            "created_at",
            "updated_at",
        }

        deleted = send(app, "DELETE", f"/api/prompts/{demoted.json()['id']}")
        assert deleted.status_code == 200
        assert deleted.json() == {"message": "提示词配置已删除"}
        assert send(app, "GET", f"/api/prompts/{demoted.json()['id']}").status_code == 404
        assert send(app, "POST", f"/api/prompts/{prompt_id}/promote-to-system").status_code == 403
        assert send(
            app,
            "DELETE",
            "/api/prompts/disabled-system",
            admin=True,
        ).json() == {"message": "系统提示词已删除"}
    finally:
        engine.dispose()
