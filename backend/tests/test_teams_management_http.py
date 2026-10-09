from __future__ import annotations

import asyncio
from datetime import datetime

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import insert

from haoai_backend.shared.identity import TrustedActor
from haoai_backend.teams import InMemoryJoinQuota, create_team_uow_factory
from haoai_backend.teams.management.http import build_management_router
from haoai_backend.teams.management.persistence import create_management_uow_factory
from chapter_asset_replacement_support import create_owner_database
from teams_support import OWNER_METADATA as TEAM_OWNER_METADATA, system_configs


@pytest.fixture
def owner_database(tmp_path):
    database = create_owner_database(tmp_path / "management-http.sqlite")
    TEAM_OWNER_METADATA.create_all(database.engine, checkfirst=True)
    with database.engine.begin() as connection:
        connection.execute(insert(system_configs).values(id="team-config"))
    try:
        yield database
    finally:
        database.close()


def make_app(owner_database, *, actor_id="user-a", quota=None, wired=True):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    shared_factory = create_team_uow_factory(
        owner_database.session_factory,
        utc_clock=lambda: datetime(2026, 10, 9, 12),
        id_source=lambda: f"http-{datetime.now().timestamp()}",
    )
    family_factory = create_management_uow_factory(shared_factory)
    calls = []

    def factory():
        calls.append(True)
        return family_factory()

    app.state.business_uow_calls = calls

    def resolve_actor(request):
        return TrustedActor(request.headers.get("x-test-user", actor_id))

    app.include_router(
        build_management_router(
            uow_factory=factory if wired else None,
            resolve_actor=resolve_actor if wired else None,
            join_quota=quota,
        )
    )
    return app


def request(app, method, url, *, json_body=None, headers=None):
    async def send():
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            trust_env=False,
        ) as client:
            return await client.request(method, url, json=json_body, headers=headers)

    return asyncio.run(send())


def test_management_router_registers_exactly_thirteen_routes(owner_database) -> None:
    app = make_app(owner_database, quota=InMemoryJoinQuota(lambda: 100.0))
    routes = {
        (method, route.path)
        for route in app.routes
        if hasattr(route, "methods")
        for method in route.methods
    }
    expected = {
        ("POST", "/api/teams"),
        ("GET", "/api/teams/my"),
        ("GET", "/api/teams/{team_id}"),
        ("PUT", "/api/teams/{team_id}"),
        ("DELETE", "/api/teams/{team_id}"),
        ("POST", "/api/teams/{team_id}/leave"),
        ("DELETE", "/api/teams/{team_id}/members/{user_id}"),
        ("PUT", "/api/teams/{team_id}/members/{user_id}/role"),
        ("PUT", "/api/teams/{team_id}/members/{user_id}/permissions"),
        ("POST", "/api/teams/{team_id}/invites"),
        ("GET", "/api/teams/{team_id}/invites"),
        ("DELETE", "/api/teams/{team_id}/invites/{invite_id}"),
        ("POST", "/api/teams/join"),
    }
    assert routes == expected


def test_http_create_detail_and_local_validation_body(owner_database) -> None:
    app = make_app(owner_database, quota=InMemoryJoinQuota(lambda: 100.0))
    created = request(app, "POST", "/api/teams", json_body={"name": "新团队"})
    assert created.status_code == 201
    assert created.json()["name"] == "新团队"

    detail = request(app, "GET", "/api/teams/team-a")
    assert detail.status_code == 200
    assert detail.json()["my_role"] == "owner"

    invalid = request(app, "POST", "/api/teams", json_body={})
    assert invalid.status_code == 422
    assert isinstance(invalid.json()["detail"], str)
    assert invalid.json()["detail"] == "Field required"


def test_http_access_order_and_unhashable_permissions_remain_generic_500(
    owner_database,
) -> None:
    app = make_app(owner_database, actor_id="user-c", quota=InMemoryJoinQuota(lambda: 100.0))
    missing_team = request(app, "GET", "/api/teams/no-team")
    assert missing_team.status_code == 404
    forbidden = request(app, "GET", "/api/teams/team-a")
    assert forbidden.status_code == 403
    assert forbidden.json() == {"detail": "你不是该团队成员"}

    owner_app = make_app(owner_database, quota=InMemoryJoinQuota(lambda: 100.0))
    from haoai_backend.teams.management.application import set_member_role
    from haoai_backend.teams import RoleUpdate
    set_member_role(
        create_management_uow_factory(
            create_team_uow_factory(owner_database.session_factory)
        ),
        TrustedActor("user-a"),
        "team-a",
        "user-b",
        RoleUpdate(role="admin"),
    )
    invalid_permissions = request(
        owner_app,
        "PUT",
        "/api/teams/team-a/members/user-b/permissions",
        json_body={"permissions": [{"unhashable": True}]},
    )
    assert invalid_permissions.status_code == 500


def test_join_quota_only_counts_valid_body_and_returns_exact_429(owner_database) -> None:
    class RecordingQuota:
        def __init__(self):
            self.inner = InMemoryJoinQuota(lambda: 100.0)
            self.calls: list[tuple[str | None, str]] = []

        def check(self, remote_ip: str | None, pathname: str) -> None:
            self.calls.append((remote_ip, pathname))
            self.inner.check(remote_ip, pathname)

    quota = RecordingQuota()
    app = make_app(owner_database, actor_id="user-b", quota=quota)
    invalid = request(app, "POST", "/api/teams/join", json_body={})
    assert invalid.status_code == 422
    assert quota.calls == []

    for _ in range(10):
        response = request(
            app,
            "POST",
            "/api/teams/join",
            json_body={"code": "NOTFOUND"},
        )
        assert response.status_code == 400
    assert len(quota.calls) == 10
    calls_before_limited = len(app.state.business_uow_calls)
    limited = request(
        app,
        "POST",
        "/api/teams/join",
        json_body={"code": "NOTFOUND"},
    )
    assert limited.status_code == 429
    assert limited.json() == {"error": "Rate limit exceeded: 10 per 1 hour"}
    assert "retry-after" not in limited.headers
    assert len(quota.calls) == 11
    assert len(app.state.business_uow_calls) == calls_before_limited

    for _ in range(12):
        unrelated = request(app, "GET", "/api/teams/my")
        assert unrelated.status_code == 200
    assert len(quota.calls) == 11


def test_actor_failure_does_not_consume_join_quota_or_open_business_uow(
    owner_database,
) -> None:
    from haoai_backend.teams import TeamForbidden

    quota_calls: list[tuple[str | None, str]] = []
    business_uow_calls: list[bool] = []

    class CountingQuota:
        def check(self, remote_ip: str | None, pathname: str) -> None:
            quota_calls.append((remote_ip, pathname))

    def resolve_actor(_request):
        raise TeamForbidden("身份验证失败")

    shared_factory = create_team_uow_factory(owner_database.session_factory)
    family_factory = create_management_uow_factory(shared_factory)

    def uow_factory():
        business_uow_calls.append(True)
        return family_factory()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(
        build_management_router(
            uow_factory=uow_factory,
            resolve_actor=resolve_actor,
            join_quota=CountingQuota(),
        )
    )

    response = request(
        app,
        "POST",
        "/api/teams/join",
        json_body={"code": "VALID-DTO"},
    )
    assert response.status_code == 403
    assert quota_calls == []
    assert business_uow_calls == []


def test_missing_factory_and_missing_join_quota_fail_closed(owner_database) -> None:
    unwired = FastAPI()
    unwired.include_router(
        build_management_router(
            uow_factory=None,
            resolve_actor=None,
            join_quota=None,
        )
    )
    response = request(unwired, "GET", "/api/teams/my")
    assert response.status_code == 503

    app = make_app(owner_database, actor_id="user-b", quota=None)
    unavailable = request(
        app,
        "POST",
        "/api/teams/join",
        json_body={"code": "NOTFOUND"},
    )
    assert unavailable.status_code == 503
    assert app.state.business_uow_calls == []
