from __future__ import annotations

import asyncio
from datetime import datetime

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import update

from chapter_asset_replacement_support import create_owner_database, series as owner_series
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.teams.series.http import build_series_router
from haoai_backend.teams.series.persistence import create_series_uow_factory


NOW = datetime(2026, 10, 9, 12, 0, 0)


@pytest.fixture
def owner_database(tmp_path):
    database = create_owner_database(tmp_path / "teams-series-http.sqlite")
    try:
        database.assert_foreign_keys_enabled()
        yield database
    finally:
        database.close()


class SyncASGIClient:
    def __init__(self, app):
        self.app = app

    def request(self, method, path, **kwargs):
        async def send():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                return await client.request(method, path, **kwargs)

        return asyncio.run(send())

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, **kwargs):
        return self.request("POST", path, **kwargs)

    def delete(self, path, **kwargs):
        return self.request("DELETE", path, **kwargs)

    def __enter__(self):
        return self

    def __exit__(self, _type, _value, _traceback):
        return False


def make_client(owner_database, *, enabled=True):
    def factory():
        return create_series_uow_factory(
            owner_database.session_factory,
            utc_clock=lambda: NOW,
            id_source=lambda: "series-http-created",
        )()

    async def resolve_actor(request):
        return TrustedActor(request.headers.get("x-test-actor", "user-b"))

    app = FastAPI()
    app.include_router(
        build_series_router(
            uow_factory=factory if enabled else None,
            resolve_actor=resolve_actor if enabled else None,
        )
    )
    return SyncASGIClient(app)


def test_router_declares_all_seven_routes_and_stays_cold_when_unwired():
    router = build_series_router(uow_factory=None, resolve_actor=None)
    registered = {
        (method, route.path)
        for route in router.routes
        for method in route.methods or set()
        if method not in {"HEAD", "OPTIONS"}
    }
    assert registered == {
        ("GET", "/api/teams/{team_id}/series"),
        ("POST", "/api/teams/{team_id}/series"),
        ("POST", "/api/series/{series_id}/share"),
        ("DELETE", "/api/series/{series_id}/share"),
        ("POST", "/api/teams/{team_id}/series/{series_id}/claim"),
        ("DELETE", "/api/teams/{team_id}/series/{series_id}/claim"),
        ("POST", "/api/teams/{team_id}/series/{series_id}/transfer"),
    }

    app = FastAPI()
    app.include_router(router)
    response = SyncASGIClient(app).get(
        "/api/teams/team-a/series"
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "团队服务尚未接线"}


def test_http_routes_use_trusted_actor_and_preserve_success_statuses(owner_database):
    with owner_database.engine.begin() as connection:
        connection.execute(
            update(owner_series)
            .where(owner_series.c.id == "series-a")
            .values(claimed_by=None, claimed_at=None)
        )

    with make_client(owner_database) as client:
        headers = {"x-test-actor": "user-b"}

        listed = client.get("/api/teams/team-a/series", headers=headers)
        assert listed.status_code == 200
        assert listed.json()["total"] == 1

        created = client.post(
            "/api/teams/team-a/series",
            headers=headers,
            json={
                "name": "  新建剧集  ",
                "description": "HTTP 请求",
                "claim": True,
            },
        )
        assert created.status_code == 201
        assert created.json() == {
            "id": "series-http-created",
            "name": "新建剧集",
            "team_id": "team-a",
            "user_id": "user-b",
            "style_prompt_id": None,
        }

        claimed = client.post(
            "/api/teams/team-a/series/series-a/claim",
            headers=headers,
        )
        assert claimed.status_code == 200
        assert claimed.json()["claimed_by"] == "user-b"

        transferred = client.post(
            "/api/teams/team-a/series/series-a/transfer",
            headers=headers,
            json={"user_id": "user-a"},
        )
        assert transferred.status_code == 200
        assert transferred.json() == {
            "message": "已转交认领",
            "claimed_by": "user-a",
        }

        unclaimed = client.delete(
            "/api/teams/team-a/series/series-a/claim",
            headers={"x-test-actor": "user-a"},
        )
        assert unclaimed.status_code == 200
        assert unclaimed.json() == {"message": "已取消认领"}

        shared = client.post(
            "/api/series/series-a/share",
            headers={"x-test-actor": "user-a"},
            json={"team_id": "team-a", "claim": False},
        )
        assert shared.status_code == 200
        assert shared.json()["team_id"] == "team-a"

        unshared = client.delete(
            "/api/series/series-a/share",
            headers={"x-test-actor": "user-a"},
        )
        assert unshared.status_code == 200
        assert unshared.json() == {"message": "已移出团队，恢复为个人剧集"}


def test_http_errors_keep_route_status_and_validation(owner_database):
    with make_client(owner_database) as client:
        missing_team = client.get("/api/teams/missing/series")
        assert missing_team.status_code == 404
        assert missing_team.json() == {"detail": "团队不存在"}

        forbidden_share = client.post(
            "/api/series/series-a/share",
            headers={"x-test-actor": "user-b"},
            json={"team_id": "team-a"},
        )
        assert forbidden_share.status_code == 403
        assert forbidden_share.json() == {"detail": "只能分享自己创建的剧集"}

        invalid_transfer = client.post(
            "/api/teams/team-a/series/series-a/transfer",
            headers={"x-test-actor": "user-a"},
            json={"user_id": ""},
        )
        assert invalid_transfer.status_code == 422
        assert invalid_transfer.json() == {
            "detail": "String should have at least 1 character"
        }
