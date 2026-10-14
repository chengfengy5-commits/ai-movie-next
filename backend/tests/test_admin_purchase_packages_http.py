from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
from jose import jwt
from sqlalchemy import create_engine, insert
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.admin_purchase_packages.http import (
    build_admin_purchase_packages_router,
)
from haoai_backend.admin_purchase_packages.persistence import (
    purchase_packages_uow_factory,
)
from haoai_backend.admin_purchase_packages.tables import PurchasePackageRow
from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.persistence import (
    SqlAlchemyAuthenticationUnitOfWork,
)
from haoai_backend.authentication.tables import metadata as auth_metadata, users

NOW = datetime.now(timezone.utc).replace(microsecond=0).replace(tzinfo=None)
SECRET = "purchase-package-http-test-secret"
FIELD_NAMES = {
    "id", "category", "name", "price", "credits", "days", "sort_order",
    "is_active", "created_at", "updated_at",
}


class ObservedSession(Session):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.commit_count = 0
        self.closed = False
        self.events = self.info["events"]
        self.kind = self.info["kind"]

    def query(self, *entities, **kwargs):
        self.events.append((self.kind, "query", threading.get_ident()))
        return super().query(*entities, **kwargs)

    def execute(self, *args, **kwargs):
        self.events.append((self.kind, "execute", threading.get_ident()))
        return super().execute(*args, **kwargs)

    def commit(self) -> None:
        self.commit_count += 1
        self.events.append((self.kind, "commit", threading.get_ident()))
        super().commit()

    def refresh(self, *args, **kwargs) -> None:
        self.events.append((self.kind, "refresh", threading.get_ident()))
        super().refresh(*args, **kwargs)

    def close(self) -> None:
        self.closed = True
        self.events.append((self.kind, "close", threading.get_ident()))
        super().close()


def request(
    app: FastAPI, method: str, path: str, *, body: Any = None, headers: dict[str, str] | None = None
) -> httpx.Response:
    async def send() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://packages.test",
        ) as client:
            return await client.request(method, path, json=body, headers=headers)

    return asyncio.run(send())


def build_system(path: Path, *, cold: bool = False, integrated: bool = False):
    engine = create_engine(f"sqlite:///{path}")
    auth_metadata.create_all(engine)
    PurchasePackageRow.metadata.create_all(engine)
    events: list[tuple[str, str, int]] = []
    auth_sessions: list[ObservedSession] = []
    business_sessions: list[ObservedSession] = []

    def session_factory(kind: str, opened: list[ObservedSession]):
        maker = sessionmaker(
            engine,
            class_=ObservedSession,
            expire_on_commit=False,
            info={"events": events, "kind": kind},
        )

        def create() -> ObservedSession:
            events.append((kind, "factory", threading.get_ident()))
            session = maker()
            opened.append(session)
            return session

        return create

    auth_factory = session_factory("auth", auth_sessions)
    business_factory = session_factory("business", business_sessions)
    with Session(engine) as session:
        session.execute(
            insert(users),
            [
                {
                    "id": "admin", "username": "admin", "email": "admin@example.test",
                    "hashed_password": "unused", "is_superuser": True,
                    "membership_type": "free", "membership_expires_at": None,
                    "avatar_url": None, "bio": None, "created_at": NOW,
                    "password_updated_at": NOW,
                },
                {
                    "id": "member", "username": "member", "email": "member@example.test",
                    "hashed_password": "unused", "is_superuser": False,
                    "membership_type": "premium", "membership_expires_at": NOW - timedelta(days=2),
                    "avatar_url": None, "bio": None, "created_at": NOW,
                    "password_updated_at": NOW,
                },
            ],
        )
        session.commit()

    runtime = AuthenticationRuntime(
        session_factory=auth_factory, secret_key=SECRET, clock=lambda: NOW
    )
    auth_service = AuthenticationService(
        runtime, lambda: SqlAlchemyAuthenticationUnitOfWork(auth_factory())
    )
    if integrated:
        from haoai_backend.app import create_app

        app = create_app(
            session_factory=business_factory,
            authentication_runtime=runtime,
        )
    else:
        app = FastAPI()
        app.include_router(
            build_admin_purchase_packages_router(
                authentication_service=None if cold else auth_service,
                uow_factory=None if cold else purchase_packages_uow_factory(business_factory),
            )
        )

    def token(user_id: str, *, exp: datetime | None = None) -> str:
        return jwt.encode(
            {"sub": user_id, "exp": exp or NOW + timedelta(minutes=5)},
            SECRET,
            algorithm="HS256",
        )

    return engine, app, events, auth_sessions, business_sessions, token


def test_real_routes_auth_order_lifecycle_and_package_contract(tmp_path: Path) -> None:
    engine, app, events, auth_sessions, business_sessions, token = build_system(
        tmp_path / "packages.sqlite"
    )
    try:
        routes = {
            (method, route.path)
            for route in app.routes
            for method in getattr(route, "methods", set())
            if route.path.startswith("/api/admin/purchase-packages")
        }
        assert routes == {
            ("GET", "/api/admin/purchase-packages"),
            ("GET", "/api/admin/purchase-packages/{pkg_id}"),
            ("POST", "/api/admin/purchase-packages"),
            ("PUT", "/api/admin/purchase-packages/{pkg_id}"),
            ("DELETE", "/api/admin/purchase-packages/{pkg_id}"),
        }

        unauthenticated = request(
            app, "POST", "/api/admin/purchase-packages", body={"category": []}
        )
        assert unauthenticated.status_code == 401
        assert unauthenticated.headers["www-authenticate"] == "Bearer"
        assert unauthenticated.json()["detail"] == "Not authenticated"
        assert not business_sessions

        non_admin_invalid = request(
            app, "POST", "/api/admin/purchase-packages",
            body={"category": [], "name": "x"},
            headers={"Authorization": f"Bearer {token('member')}"},
        )
        assert non_admin_invalid.status_code == 403
        assert non_admin_invalid.json()["detail"] == "权限不足，仅管理员可执行此操作"
        assert "www-authenticate" not in non_admin_invalid.headers
        assert not business_sessions

        admin_invalid = request(
            app, "POST", "/api/admin/purchase-packages",
            body={"category": [], "name": "x"},
            headers={"Authorization": f"Bearer {token('admin')}"},
        )
        assert admin_invalid.status_code == 422
        assert not business_sessions

        auth_header = {"Authorization": f"Bearer {token('admin')}"}
        created = request(
            app, "POST", "/api/admin/purchase-packages",
            body={"category": "unrestricted", "name": "试验套餐", "price": -7},
            headers=auth_header,
        )
        assert created.status_code == 200, created.text
        created_payload = created.json()
        assert set(created_payload) == FIELD_NAMES
        package_id = created_payload["id"]
        assert created_payload["category"] == "unrestricted"
        assert created_payload["price"] == -7
        assert created_payload["credits"] is None and created_payload["days"] is None
        assert created_payload["sort_order"] == 0 and created_payload["is_active"] is True
        assert created_payload["created_at"] and created_payload["updated_at"]
        assert business_sessions[-1].commit_count == 1

        auth_closed_index = max(
            i for i, (kind, action, _) in enumerate(events)
            if kind == "auth" and action == "close"
        )
        business_open_index = max(
            i for i, (kind, action, _) in enumerate(events)
            if kind == "business" and action == "factory"
        )
        assert auth_closed_index < business_open_index

        business_thread_events = [
            thread_id for kind, action, thread_id in events
            if kind == "business" and action in {"factory", "query", "execute", "commit", "refresh", "close"}
        ]
        assert business_thread_events
        assert len(set(business_thread_events)) == 1

        fetched = request(
            app, "GET", f"/api/admin/purchase-packages/{package_id}", headers=auth_header
        )
        assert fetched.status_code == 200 and set(fetched.json()) == FIELD_NAMES

        listed = request(
            app, "GET", "/api/admin/purchase-packages?sort_field=sort_order&skip=0&limit=50",
            headers=auth_header,
        )
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        assert listed.json()["data"][0]["id"] == package_id
        assert set(listed.json()["data"][0]) == FIELD_NAMES

        updated = request(
            app, "PUT", f"/api/admin/purchase-packages/{package_id}",
            body={"credits": 25, "days": None, "ignored": "legacy extra"},
            headers=auth_header,
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["credits"] == 25 and updated.json()["days"] is None
        assert set(updated.json()) == FIELD_NAMES
        assert business_sessions[-1].commit_count == 1

        deleted = request(
            app, "DELETE", f"/api/admin/purchase-packages/{package_id}", headers=auth_header
        )
        assert deleted.status_code == 200
        assert deleted.json() == {"message": "删除成功"}
        assert business_sessions[-1].commit_count == 1
        missing = request(
            app, "GET", f"/api/admin/purchase-packages/{package_id}", headers=auth_header
        )
        assert missing.status_code == 404 and missing.json()["detail"] == "套餐不存在"
        assert business_sessions[-1].commit_count == 0

        expired_token = token("admin", exp=NOW - timedelta(seconds=1))
        invalid = request(
            app, "GET", "/api/admin/purchase-packages",
            headers={"Authorization": f"Bearer {expired_token}"},
        )
        assert invalid.status_code == 401
        assert invalid.headers["www-authenticate"] == "Bearer"
    finally:
        engine.dispose()


def test_cold_router_fails_closed_before_body_validation_and_startup_is_inert(
    tmp_path: Path,
) -> None:
    engine, app, events, _auth, business, _token = build_system(
        tmp_path / "cold.sqlite", cold=True
    )
    try:
        assert events == []
        response = request(
            app, "POST", "/api/admin/purchase-packages", body={"category": []}
        )
        assert response.status_code == 503
        assert response.json()["detail"] == "购买套餐管理服务尚未接线"
        assert events == []
        assert business == []
    finally:
        engine.dispose()



def test_create_app_composes_package_routes_with_public_auth_and_supplied_sessions(
    tmp_path: Path,
) -> None:
    engine, app, events, auth_sessions, business_sessions, token = build_system(
        tmp_path / "packages-composed.sqlite", integrated=True
    )
    try:
        assert len(app.routes) == 231
        package_routes = {
            (method, route.path)
            for route in app.routes
            for method in getattr(route, "methods", set())
            if route.path.startswith("/api/admin/purchase-packages")
        }
        assert package_routes == {
            ("GET", "/api/admin/purchase-packages"),
            ("GET", "/api/admin/purchase-packages/{pkg_id}"),
            ("POST", "/api/admin/purchase-packages"),
            ("PUT", "/api/admin/purchase-packages/{pkg_id}"),
            ("DELETE", "/api/admin/purchase-packages/{pkg_id}"),
        }
        assert events == []
        assert auth_sessions == [] and business_sessions == []

        from haoai_backend.app import create_app

        cold = request(
            create_app(), "POST", "/api/admin/purchase-packages", body={"category": []}
        )
        assert cold.status_code == 503
        assert cold.json() == {"detail": "购买套餐管理服务尚未接线"}
        assert events == []

        missing = request(app, "GET", "/api/admin/purchase-packages")
        assert missing.status_code == 401
        assert missing.json() == {"detail": "Not authenticated"}
        assert missing.headers.get("www-authenticate") == "Bearer"
        assert not business_sessions

        invalid = request(
            app,
            "GET",
            "/api/admin/purchase-packages",
            headers={"Authorization": "Bearer invalid-token"},
        )
        assert invalid.status_code == 401
        assert invalid.headers.get("www-authenticate") == "Bearer"
        assert not business_sessions

        non_admin = request(
            app,
            "POST",
            "/api/admin/purchase-packages",
            body={"category": [], "name": "invalid before auth"},
            headers={
                "Authorization": f"Bearer {token('member')}",
                "X-User-ID": "admin",
                "X-Admin": "true",
            },
        )
        assert non_admin.status_code == 403
        assert non_admin.json()["detail"] == "权限不足，仅管理员可执行此操作"
        assert "www-authenticate" not in non_admin.headers
        assert not business_sessions

        admin_header = {"Authorization": f"Bearer {token('admin')}"}
        invalid_admin_body = request(
            app,
            "POST",
            "/api/admin/purchase-packages",
            body={"category": []},
            headers=admin_header,
        )
        assert invalid_admin_body.status_code == 422
        assert not business_sessions

        event_start = len(events)
        created = request(
            app,
            "POST",
            "/api/admin/purchase-packages",
            body={"category": "other", "name": "Composed", "price": 7},
            headers=admin_header,
        )
        assert created.status_code == 200, created.text
        package_id = created.json()["id"]
        assert set(created.json()) == FIELD_NAMES
        assert business_sessions[-1].commit_count == 1
        assert business_sessions[-1].closed

        request_events = events[event_start:]
        auth_close = max(
            index for index, event in enumerate(request_events)
            if event[0] == "auth" and event[1] == "close"
        )
        business_factory = min(
            index for index, event in enumerate(request_events)
            if event[0] == "business" and event[1] == "factory"
        )
        assert auth_close < business_factory
        business_threads = {
            event[2] for event in request_events if event[0] == "business"
        }
        assert len(business_threads) == 1

        listed = request(
            app, "GET", "/api/admin/purchase-packages", headers=admin_header
        )
        assert listed.status_code == 200 and listed.json()["total"] == 1
        fetched = request(
            app,
            "GET",
            f"/api/admin/purchase-packages/{package_id}",
            headers=admin_header,
        )
        assert fetched.status_code == 200 and fetched.json()["id"] == package_id
        updated = request(
            app,
            "PUT",
            f"/api/admin/purchase-packages/{package_id}",
            body={"name": "Updated"},
            headers=admin_header,
        )
        assert updated.status_code == 200
        assert updated.json()["name"] == "Updated"
        assert business_sessions[-1].commit_count == 1
        deleted = request(
            app,
            "DELETE",
            f"/api/admin/purchase-packages/{package_id}",
            headers=admin_header,
        )
        assert deleted.status_code == 200
        assert deleted.json() == {"message": "删除成功"}
        assert business_sessions[-1].commit_count == 1
        assert all(session.closed for session in business_sessions)
    finally:
        engine.dispose()
