from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from jose import jwt
from sqlalchemy import create_engine, event, insert, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.app import create_app
from haoai_backend.admin_users.tables import (
    AITaskRow,
    Base,
    CreditLogRow,
    ModelConfigRow,
    PromptConfigRow,
    RedemptionCodeRow,
    SeriesRow,
    TeamMemberRow,
    TeamRow,
    UserCreditRow,
    UserRow,
)
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.tables import (
    metadata as authentication_metadata,
    user_sessions,
    users,
)

SECRET = "admin-users-http-test-secret"


class ObservedSession(Session):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.commit_count = 0
        self.rollback_count = 0
        self.refresh_count = 0
        self.close_count = 0
        self.closed_marker = False
        self.info["events"].append(
            (self.info["kind"], "open", id(self), threading.get_ident())
        )

    def execute(self, *args: Any, **kwargs: Any):
        self.info["events"].append(
            (self.info["kind"], "execute", id(self), threading.get_ident())
        )
        return super().execute(*args, **kwargs)

    def commit(self) -> None:
        self.commit_count += 1
        self.info["events"].append(
            (self.info["kind"], "commit", id(self), threading.get_ident())
        )
        super().commit()

    def rollback(self) -> None:
        self.rollback_count += 1
        self.info["events"].append(
            (self.info["kind"], "rollback", id(self), threading.get_ident())
        )
        super().rollback()

    def refresh(self, instance: Any, *args: Any, **kwargs: Any) -> None:
        self.refresh_count += 1
        self.info["events"].append(
            (self.info["kind"], "refresh", id(self), threading.get_ident())
        )
        super().refresh(instance, *args, **kwargs)

    def close(self) -> None:
        self.close_count += 1
        self.closed_marker = True
        self.info["events"].append(
            (self.info["kind"], "close", id(self), threading.get_ident())
        )
        super().close()


class OperationalErrorBeforeCommitSession(ObservedSession):
    def commit(self) -> None:
        self.commit_count += 1
        self.info["events"].append(
            (self.info["kind"], "commit", id(self), threading.get_ident())
        )
        raise OperationalError("COMMIT", {}, RuntimeError("connection unavailable"))


class LostCommitAcknowledgementSession(ObservedSession):
    def commit(self) -> None:
        super().commit()
        raise OperationalError("COMMIT", {}, RuntimeError("commit acknowledgement lost"))


class FakePasswordHasher:
    def __init__(self) -> None:
        self.passwords: list[str] = []

    def hash(self, password: str) -> str:
        self.passwords.append(password)
        return f"test-hash:{password}"

    def verify(self, password: str, encoded: str) -> bool:
        raise AssertionError("the route does not verify passwords")


def request(
    app: FastAPI,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: Any = None,
) -> httpx.Response:
    async def send() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://admin-users.test",
        ) as client:
            return await client.request(method, path, headers=headers, json=json_body)

    return asyncio.run(send())


def _user(
    user_id: str,
    *,
    is_admin: bool = False,
    membership_type: str = "free",
    membership_expires_at: datetime | None = None,
    created_at: datetime,
) -> dict[str, Any]:
    return {
        "id": user_id,
        "username": user_id,
        "email": f"{user_id}@example.test",
        "hashed_password": "unused",
        "is_superuser": is_admin,
        "membership_type": membership_type,
        "membership_expires_at": membership_expires_at,
        "avatar_url": None,
        "bio": None,
        "created_at": created_at,
        "password_updated_at": None,
    }


def build_system(
    path: Path,
    *,
    enable_foreign_keys: bool = False,
    business_session_class: type[ObservedSession] = ObservedSession,
):
    engine = create_engine(f"sqlite:///{path}")
    if enable_foreign_keys:
        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(connection: Any, _: Any) -> None:
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    authentication_metadata.create_all(engine)
    Base.metadata.create_all(engine)
    events: list[tuple[str, str, int, int]] = []
    auth_sessions: list[ObservedSession] = []
    business_sessions: list[ObservedSession] = []

    def factory_for(kind: str, opened: list[ObservedSession]):
        session_class = business_session_class if kind == "business" else ObservedSession
        maker = sessionmaker(
            engine,
            class_=session_class,
            expire_on_commit=False,
            future=True,
            info={"kind": kind, "events": events},
        )

        def factory() -> ObservedSession:
            events.append((kind, "factory", 0, threading.get_ident()))
            session = maker()
            opened.append(session)
            return session

        return factory

    auth_factory = factory_for("auth", auth_sessions)
    business_factory = factory_for("business", business_sessions)
    now = datetime.now(timezone.utc).replace(microsecond=0).replace(tzinfo=None)
    with engine.begin() as connection:
        connection.execute(
            insert(users),
            [
                _user("admin", is_admin=True, created_at=now - timedelta(days=5)),
                _user("free", created_at=now - timedelta(days=4)),
                _user(
                    "premium",
                    membership_type="premium",
                    membership_expires_at=now + timedelta(days=30),
                    created_at=now - timedelta(days=3),
                ),
                _user(
                    "expired",
                    membership_type="premium",
                    membership_expires_at=now - timedelta(days=2),
                    created_at=now - timedelta(days=2),
                ),
                _user(
                    "expired-admin",
                    is_admin=True,
                    membership_type="premium",
                    membership_expires_at=now - timedelta(days=1),
                    created_at=now - timedelta(days=1),
                ),
            ],
        )
        connection.execute(
            insert(user_sessions),
            [
                {
                    "id": f"{user_id}-session",
                    "user_id": user_id,
                    "jti": f"{user_id}-jti",
                    "user_agent": "test",
                    "ip_address": "127.0.0.1",
                    "device_name": "test",
                    "is_active": True,
                    "created_at": now,
                    "last_seen_at": now,
                    "revoked_at": None,
                }
                for user_id in ("admin", "free", "premium", "expired", "expired-admin")
            ],
        )
        connection.execute(
            insert(UserCreditRow.__table__),
            [
                {
                    "id": "free-credit",
                    "user_id": "free",
                    "credits": 8,
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": "expired-admin-credit",
                    "user_id": "expired-admin",
                    "credits": 3,
                    "created_at": now,
                    "updated_at": now,
                },
            ],
        )

    hasher = FakePasswordHasher()
    runtime = AuthenticationRuntime(
        session_factory=auth_factory,
        secret_key=SECRET,
        password_hasher=hasher,
    )
    app = create_app(
        session_factory=business_factory,
        authentication_runtime=runtime,
    )

    def token_for(user_id: str) -> str:
        issued_at = datetime.now(timezone.utc)
        return jwt.encode(
            {
                "sub": user_id,
                "jti": f"{user_id}-jti",
                "exp": issued_at + timedelta(minutes=5),
            },
            SECRET,
            algorithm="HS256",
        )

    return engine, app, events, auth_sessions, business_sessions, token_for, hasher


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _assert_business_worker_lifecycle(
    events: list[tuple[str, str, int, int]],
    start: int,
    auth_sessions: list[ObservedSession],
    business_sessions: list[ObservedSession],
    *,
    request_thread: int,
) -> ObservedSession:
    new_events = events[start:]
    auth_session = auth_sessions[-1]
    business_session = business_sessions[-1]
    auth_close = next(
        index
        for index, item in enumerate(new_events)
        if item[0] == "auth" and item[1] == "close" and item[2] == id(auth_session)
    )
    business_open = next(
        index
        for index, item in enumerate(new_events)
        if item[0] == "business" and item[1] == "open" and item[2] == id(business_session)
    )
    assert auth_close < business_open
    business_events = [
        item
        for item in new_events
        if item[0] == "business" and item[2] == id(business_session)
    ]
    assert business_events
    assert {item[3] for item in business_events} == {business_events[0][3]}
    assert business_events[0][3] != request_thread
    assert business_session.closed_marker
    return business_session


def _seed_protected_delete_case(engine: Any, association: str) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0).replace(tzinfo=None)
    with Session(engine) as session:
        session.add_all(
            [
                UserCreditRow(
                    id="admin-delete-control-credit",
                    user_id="admin",
                    credits=37,
                    created_at=now,
                    updated_at=now,
                ),
                CreditLogRow(
                    id="free-delete-log",
                    user_id="free",
                    amount=-2,
                    balance_after=6,
                    type="usage",
                    description="target rollback control",
                    task_id=None,
                    business_key=None,
                    billing_unit_id=None,
                    related_debit_id=None,
                    created_at=now,
                ),
                CreditLogRow(
                    id="admin-delete-control-log",
                    user_id="admin",
                    amount=5,
                    balance_after=42,
                    type="charge",
                    description="keeper control",
                    task_id=None,
                    business_key=None,
                    billing_unit_id=None,
                    related_debit_id=None,
                    created_at=now,
                ),
                ModelConfigRow(
                    id="free-delete-model",
                    user_id="free",
                    category="text",
                    name="target model",
                    provider="local",
                    api_key="",
                    model="fixture-model",
                    base_url=None,
                    sort_order=0,
                    params=None,
                    created_at=now,
                    updated_at=now,
                ),
                ModelConfigRow(
                    id="admin-delete-control-model",
                    user_id="admin",
                    category="text",
                    name="keeper model",
                    provider="local",
                    api_key="",
                    model="fixture-model",
                    base_url=None,
                    sort_order=0,
                    params=None,
                    created_at=now,
                    updated_at=now,
                ),
                PromptConfigRow(
                    id="free-delete-prompt",
                    user_id="free",
                    category="fixture",
                    name="target prompt",
                    system_prompt=None,
                    user_prompt=None,
                    sort_order=0,
                    created_at=now,
                    updated_at=now,
                ),
                PromptConfigRow(
                    id="admin-delete-control-prompt",
                    user_id="admin",
                    category="fixture",
                    name="keeper prompt",
                    system_prompt=None,
                    user_prompt=None,
                    sort_order=0,
                    created_at=now,
                    updated_at=now,
                ),
                SeriesRow(
                    id="free-delete-series",
                    user_id="free",
                    name="target series",
                    description=None,
                    image_url=None,
                    team_id=None,
                    created_at=now,
                    updated_at=now,
                ),
                SeriesRow(
                    id="admin-delete-control-series",
                    user_id="admin",
                    name="keeper series",
                    description=None,
                    image_url=None,
                    team_id=None,
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
        if association == "ai_task":
            session.add(
                AITaskRow(
                    id="protected-task",
                    user_id="free",
                    type="image",
                    message_id="protected-task-message",
                    billing_status="unbilled",
                )
            )
        elif association == "team_member":
            session.add(TeamRow(id="keeper-team", name="keeper team", owner_id="admin"))
            session.flush()
            session.add(
                TeamMemberRow(
                    id="protected-team-member",
                    team_id="keeper-team",
                    user_id="free",
                    role="member",
                    permissions=None,
                )
            )
        elif association == "created_code":
            session.add(
                RedemptionCodeRow(
                    id="protected-created-code",
                    code="delete-constraint-code",
                    type="credits",
                    value=9,
                    is_used=False,
                    used_by=None,
                    used_at=None,
                    created_by="free",
                    expires_at=None,
                    created_at=now,
                )
            )
        elif association == "external_task_quote":
            quote_table = Base.metadata.tables["task_quotes"]
            session.execute(
                insert(quote_table).values(id="protected-task-quote", user_id="free")
            )
        else:
            raise AssertionError(f"unknown protected association: {association}")
        session.commit()

    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1


def _assert_protected_delete_state_unchanged(engine: Any, association: str) -> None:
    with Session(engine) as session:
        assert session.get(UserRow, "free") is not None
        assert session.get(UserRow, "admin") is not None
        assert session.get(UserCreditRow, "free-credit").credits == 8
        assert session.get(UserCreditRow, "admin-delete-control-credit").credits == 37

        def ids(model: Any, user_id: str) -> set[str]:
            return {
                row[0]
                for row in session.query(model.id).filter(model.user_id == user_id).all()
            }

        assert ids(CreditLogRow, "free") == {"free-delete-log"}
        assert ids(CreditLogRow, "admin") == {"admin-delete-control-log"}
        assert ids(ModelConfigRow, "free") == {"free-delete-model"}
        assert ids(ModelConfigRow, "admin") == {"admin-delete-control-model"}
        assert ids(PromptConfigRow, "free") == {"free-delete-prompt"}
        assert ids(PromptConfigRow, "admin") == {"admin-delete-control-prompt"}
        assert ids(SeriesRow, "free") == {"free-delete-series"}
        assert ids(SeriesRow, "admin") == {"admin-delete-control-series"}

        if association == "ai_task":
            assert session.get(AITaskRow, "protected-task").user_id == "free"
        elif association == "team_member":
            member = session.get(TeamMemberRow, "protected-team-member")
            assert (member.team_id, member.user_id) == ("keeper-team", "free")
            assert session.get(TeamRow, "keeper-team").owner_id == "admin"
        elif association == "created_code":
            assert session.get(RedemptionCodeRow, "protected-created-code").created_by == "free"
        else:
            quote_table = Base.metadata.tables["task_quotes"]
            quote = session.execute(
                select(quote_table.c.id, quote_table.c.user_id)
                .where(quote_table.c.id == "protected-task-quote")
            ).one()
            assert tuple(quote) == ("protected-task-quote", "free")


@pytest.mark.parametrize(
    "association",
    ["ai_task", "team_member", "created_code", "external_task_quote"],
)
def test_delete_protected_references_return_conflict_and_rollback_allowed_deletes(
    tmp_path: Path,
    association: str,
) -> None:
    engine, app, events, auth_sessions, business_sessions, token_for, _ = build_system(
        tmp_path / f"admin-users-delete-{association}.sqlite",
        enable_foreign_keys=True,
    )
    try:
        _seed_protected_delete_case(engine, association)
        start = len(events)
        response = request(
            app,
            "DELETE",
            "/api/admin/users/free",
            headers=_headers(token_for("admin")),
        )

        delete_session = _assert_business_worker_lifecycle(
            events,
            start,
            auth_sessions,
            business_sessions,
            request_thread=threading.get_ident(),
        )
        assert delete_session.commit_count == 1
        assert delete_session.rollback_count == 1
        assert delete_session.close_count == 1
        _assert_protected_delete_state_unchanged(engine, association)

        assert response.status_code == 409, response.text
        assert response.json() == {
            "detail": "当前用户不满足删除约束，暂时无法删除，请核对关联数据。"
        }
    finally:
        engine.dispose()


def test_delete_operational_commit_failure_stays_500_and_does_not_retry(
    tmp_path: Path,
) -> None:
    engine, app, events, auth_sessions, business_sessions, token_for, _ = build_system(
        tmp_path / "admin-users-delete-operational-error.sqlite",
        enable_foreign_keys=True,
        business_session_class=OperationalErrorBeforeCommitSession,
    )
    start = len(events)
    try:
        response = request(
            app,
            "DELETE",
            "/api/admin/users/free",
            headers=_headers(token_for("admin")),
        )
        assert response.status_code == 500
        assert len(business_sessions) == 1
        delete_session = _assert_business_worker_lifecycle(
            events,
            start,
            auth_sessions,
            business_sessions,
            request_thread=threading.get_ident(),
        )
        assert delete_session.commit_count == 1
        assert delete_session.rollback_count == 1
        assert delete_session.close_count == 1
        assert request(
            app,
            "GET",
            "/api/admin/users/free",
            headers=_headers(token_for("admin")),
        ).status_code == 200
        assert request(
            app,
            "GET",
            "/api/admin/users/admin",
            headers=_headers(token_for("admin")),
        ).status_code == 200
    finally:
        engine.dispose()


def test_delete_lost_commit_acknowledgement_stays_500_and_reads_committed_state(
    tmp_path: Path,
) -> None:
    engine, app, events, auth_sessions, business_sessions, token_for, _ = build_system(
        tmp_path / "admin-users-delete-ack-lost.sqlite",
        enable_foreign_keys=True,
        business_session_class=LostCommitAcknowledgementSession,
    )
    start = len(events)
    try:
        response = request(
            app,
            "DELETE",
            "/api/admin/users/free",
            headers=_headers(token_for("admin")),
        )
        assert response.status_code == 500
        assert len(business_sessions) == 1
        delete_session = _assert_business_worker_lifecycle(
            events,
            start,
            auth_sessions,
            business_sessions,
            request_thread=threading.get_ident(),
        )
        assert delete_session.commit_count == 1
        assert delete_session.rollback_count == 1
        assert delete_session.close_count == 1
        assert request(
            app,
            "GET",
            "/api/admin/users/free",
            headers=_headers(token_for("admin")),
        ).status_code == 404
        assert request(
            app,
            "GET",
            "/api/admin/users/admin",
            headers=_headers(token_for("admin")),
        ).status_code == 200
    finally:
        engine.dispose()


def test_real_routes_cover_user_crud_auth_dtos_and_worker_transactions(tmp_path: Path) -> None:
    engine, app, events, auth_sessions, business_sessions, token_for, hasher = build_system(
        tmp_path / "admin-users-http.sqlite"
    )
    admin_headers = _headers(token_for("admin"))
    request_thread = threading.get_ident()
    try:
        start = len(events)
        listed = request(
            app,
            "GET",
            "/api/admin/users?username=%25&membership_type=free&is_superuser=false&sort_field=username&sort_order=asc",
            headers=admin_headers,
        )
        assert listed.status_code == 200, listed.text
        page = listed.json()
        assert page["total"] == 1
        assert [row["id"] for row in page["data"]] == ["free"]
        assert set(page["data"][0]) == {
            "id",
            "username",
            "email",
            "is_superuser",
            "membership_type",
            "membership_expires_at",
            "credits",
            "created_at",
        }
        list_session = _assert_business_worker_lifecycle(
            events,
            start,
            auth_sessions,
            business_sessions,
            request_thread=request_thread,
        )
        assert list_session.commit_count == 0
        assert list_session.refresh_count == 0

        start = len(events)
        created = request(
            app,
            "POST",
            "/api/admin/users",
            headers=admin_headers,
            json_body={
                "username": "delete-me",
                "email": "delete-me@example.test",
                "password": "secret",
                "is_superuser": False,
                "membership_type": "premium",
                "membership_expires_at": "2027-03-04T05:06:07Z",
                "credits": 11,
            },
        )
        assert created.status_code == 201, created.text
        created_body = created.json()
        assert set(created_body) == set(page["data"][0])
        assert created_body["credits"] == 11
        assert created_body["membership_expires_at"].startswith("2027-03-04T05:06:07")
        assert hasher.passwords == ["secret"]
        create_session = _assert_business_worker_lifecycle(
            events,
            start,
            auth_sessions,
            business_sessions,
            request_thread=request_thread,
        )
        assert create_session.commit_count == 1
        assert create_session.refresh_count == 1

        start = len(events)
        detail = request(
            app,
            "GET",
            f"/api/admin/users/{created_body['id']}",
            headers=admin_headers,
        )
        assert detail.status_code == 200
        assert detail.json() == created_body
        detail_session = _assert_business_worker_lifecycle(
            events,
            start,
            auth_sessions,
            business_sessions,
            request_thread=request_thread,
        )
        assert detail_session.commit_count == 0
        assert detail_session.refresh_count == 0

        start = len(events)
        updated = request(
            app,
            "PUT",
            f"/api/admin/users/{created_body['id']}",
            headers=admin_headers,
            json_body={"credits": 6.7, "membership_type": "free"},
        )
        assert updated.status_code == 200
        assert updated.json() == {"message": "用户更新成功"}
        update_session = _assert_business_worker_lifecycle(
            events,
            start,
            auth_sessions,
            business_sessions,
            request_thread=request_thread,
        )
        assert update_session.commit_count == 1
        assert update_session.refresh_count == 0

        start = len(events)
        deleted = request(
            app,
            "DELETE",
            f"/api/admin/users/{created_body['id']}",
            headers=admin_headers,
        )
        assert deleted.status_code == 200, deleted.text
        assert deleted.json() == {
            "success": True,
            "message": "用户 delete-me 已删除",
        }
        delete_session = _assert_business_worker_lifecycle(
            events,
            start,
            auth_sessions,
            business_sessions,
            request_thread=request_thread,
        )
        assert delete_session.commit_count == 1
        assert delete_session.refresh_count == 0
    finally:
        engine.dispose()


def test_auth_requires_superuser_but_does_not_require_active_membership(tmp_path: Path) -> None:
    engine, app, _, _, _, token_for, _ = build_system(
        tmp_path / "admin-users-auth.sqlite"
    )
    try:
        for user_id in ("free", "premium", "expired"):
            response = request(
                app,
                "GET",
                "/api/admin/users",
                headers=_headers(token_for(user_id)),
            )
            assert response.status_code == 403
            assert response.json() == {"detail": "权限不足，仅管理员可执行此操作"}

        expired_admin = request(
            app,
            "GET",
            "/api/admin/users?is_superuser=true",
            headers=_headers(token_for("expired-admin")),
        )
        assert expired_admin.status_code == 200, expired_admin.text
        assert expired_admin.json()["total"] == 2
    finally:
        engine.dispose()


def test_post_checks_unique_fields_before_expiry_and_hashing(tmp_path: Path) -> None:
    engine, app, _, _, business_sessions, token_for, hasher = build_system(
        tmp_path / "admin-users-post-validation.sqlite"
    )
    headers = _headers(token_for("admin"))
    cases = (
        (
            {"username": "free", "email": "free@example.test"},
            "用户名已存在",
        ),
        (
            {"username": "new-name", "email": "free@example.test"},
            "该邮箱已被其他账号绑定",
        ),
        (
            {"username": "new-name", "email": "new@example.test"},
            "会员到期时间格式不正确",
        ),
    )
    try:
        for identity, expected_detail in cases:
            before = len(business_sessions)
            response = request(
                app,
                "POST",
                "/api/admin/users",
                headers=headers,
                json_body={
                    **identity,
                    "password": "secret",
                    "membership_expires_at": "invalid",
                },
            )
            assert response.status_code == 400
            assert response.json() == {"detail": expected_detail}
            assert len(business_sessions) == before + 1
            session = business_sessions[-1]
            assert session.rollback_count == 1
            assert session.commit_count == 0
            assert session.refresh_count == 0
            assert session.closed_marker

        assert hasher.passwords == []
    finally:
        engine.dispose()


def test_invalid_typed_post_keeps_authentication_precedence(tmp_path: Path) -> None:
    engine, app, _, _, _, token_for, _ = build_system(
        tmp_path / "admin-users-validation.sqlite"
    )
    try:
        invalid_body = request(
            app,
            "POST",
            "/api/admin/users",
            headers=_headers(token_for("admin")),
            json_body={"username": "missing-fields"},
        )
        assert invalid_body.status_code == 422

        unauthenticated_query = request(
            app,
            "GET",
            "/api/admin/users?skip=invalid",
        )
        assert unauthenticated_query.status_code == 401
    finally:
        engine.dispose()
