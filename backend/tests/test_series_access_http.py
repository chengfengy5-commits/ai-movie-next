"""ASGI integration of the current SQL access policy with rough-cut GET/PUT."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine, event, insert, select
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.app import create_app
from haoai_backend.personal_production.rough_cut.errors import Unauthorized
from haoai_backend.personal_production.rough_cut.ports import TrustedActor
from haoai_backend.personal_production.rough_cut.tables import (
    chapters,
    metadata as rough_cut_metadata,
    rough_cut_drafts,
    storyboard_assets,
)
from haoai_backend.series_access.rough_cut_policy import require_rough_cut_series_access
from haoai_backend.series_access.tables import (
    metadata as access_metadata,
    series,
    team_members,
    users,
)


@pytest.fixture
def database(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'series-access-http.sqlite'}", future=True)
    access_metadata.create_all(engine)
    rough_cut_metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False, future=True)
    with engine.begin() as connection:
        connection.execute(
            insert(users),
            [
                {"id": "author", "username": "作者"},
                {"id": "member", "username": "成员"},
                {"id": "permitted", "username": "授权成员"},
                {"id": "owner", "username": "队长"},
                {"id": "claimant", "username": "当前负责人"},
                {"id": "outsider", "username": "外部用户"},
                {"id": "admin", "username": "管理员"},
            ],
        )
        connection.execute(
            insert(series),
            [
                {
                    "id": "team-series",
                    "user_id": "author",
                    "team_id": "team-a",
                    "claimed_by": None,
                },
                {
                    "id": "claimed-series",
                    "user_id": "author",
                    "team_id": "team-a",
                    "claimed_by": "claimant",
                },
                {
                    "id": "personal-series",
                    "user_id": "author",
                    "team_id": None,
                    "claimed_by": None,
                },
            ],
        )
        connection.execute(
            insert(team_members),
            [
                {
                    "id": "member-team",
                    "team_id": "team-a",
                    "user_id": "member",
                    "role": "member",
                    "permissions": "[]",
                },
                {
                    "id": "owner-team",
                    "team_id": "team-a",
                    "user_id": "owner",
                    "role": "owner",
                    "permissions": None,
                },
                {
                    "id": "permission-team",
                    "team_id": "team-a",
                    "user_id": "permitted",
                    "role": "admin",
                    "permissions": '["enter_claimed_series"]',
                },
                {
                    "id": "admin-team",
                    "team_id": "team-a",
                    "user_id": "admin",
                    "role": "admin",
                    "permissions": "[]",
                },
            ],
        )
        connection.execute(
            insert(chapters),
            [
                {
                    "id": "chapter-team",
                    "series_id": "team-series",
                    "title": "团队章节",
                    "content": json.dumps(
                        [
                            {
                                "storyboard": ["asset-a"],
                                "text": "团队镜头",
                                "preview": "https://media.invalid/a.mp4",
                            }
                        ]
                    ),
                },
                {
                    "id": "chapter-claimed",
                    "series_id": "claimed-series",
                    "title": "认领章节",
                    "content": json.dumps(
                        [
                            {
                                "storyboard": ["asset-c"],
                                "text": "认领镜头",
                                "preview": "https://media.invalid/c.mp4",
                            }
                        ]
                    ),
                },
                {
                    "id": "chapter-personal",
                    "series_id": "personal-series",
                    "title": "个人章节",
                    "content": "[]",
                },
                {
                    "id": "chapter-missing-series",
                    "series_id": "deleted-series",
                    "title": "悬空章节",
                    "content": "[]",
                },
            ],
        )
        connection.execute(
            insert(storyboard_assets),
            [
                {
                    "id": "asset-a",
                    "series_id": "team-series",
                    "chapter_id": "chapter-team",
                    "frame_index": 0,
                    "image_url": None,
                },
                {
                    "id": "asset-c",
                    "series_id": "claimed-series",
                    "chapter_id": "chapter-claimed",
                    "frame_index": 0,
                    "image_url": None,
                },
            ],
        )
    yield engine, sessions
    engine.dispose()


def build_app(sessions: sessionmaker[Session]):
    def resolve_actor(request) -> TrustedActor:
        user_id = request.headers.get("x-test-user")
        if not user_id:
            raise Unauthorized()
        return TrustedActor(user_id=user_id)

    return create_app(
        session_factory=sessions,
        resolve_actor=resolve_actor,
        series_access_policy=require_rough_cut_series_access,
    )


def request(
    app,
    method: str,
    chapter_id: str,
    *,
    user_id: str | None,
    body: dict[str, Any] | None = None,
) -> httpx.Response:
    async def send() -> httpx.Response:
        headers = {} if user_id is None else {"x-test-user": user_id}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://series-access.test",
        ) as client:
            return await client.request(
                method,
                f"/api/chapters/{chapter_id}/rough-cut",
                headers=headers,
                json=body,
            )

    return asyncio.run(send())


def test_real_sql_policy_controls_existing_rough_cut_routes_and_private_drafts(database) -> None:
    engine, sessions = database
    app = build_app(sessions)
    initial = request(app, "GET", "chapter-team", user_id="member")
    assert initial.status_code == 200
    assert initial.json()["saved"] is False

    saved = request(
        app,
        "PUT",
        "chapter-team",
        user_id="member",
        body={
            "expected_revision": 0,
            "frames": [{"asset_id": "asset-a", "included": True}],
        },
    )
    assert saved.status_code == 200
    assert saved.json()["revision"] == 1

    same_user = request(app, "GET", "chapter-team", user_id="member")
    other_member = request(app, "GET", "chapter-team", user_id="owner")
    outsider = request(app, "GET", "chapter-team", user_id="outsider")
    assert same_user.status_code == 200 and same_user.json()["saved"] is True
    assert other_member.status_code == 200 and other_member.json()["saved"] is False
    assert outsider.status_code == 403
    with Session(engine, future=True) as session:
        rows = session.execute(select(rough_cut_drafts)).mappings().all()
    assert len(rows) == 1
    assert rows[0]["user_id"] == "member"


def test_claim_priority_owner_permission_and_claimed_actor_behavior(database) -> None:
    _, sessions = database
    app = build_app(sessions)

    author_denied = request(app, "GET", "chapter-claimed", user_id="author")
    assert author_denied.status_code == 403
    assert author_denied.json()["detail"] == "该剧集已由「当前负责人」负责制作，暂不可进入"

    admin_denied = request(app, "GET", "chapter-claimed", user_id="admin")
    assert admin_denied.status_code == 403
    assert admin_denied.json()["detail"] == "该剧集已由「当前负责人」负责制作，暂不可进入"

    owner_allowed = request(app, "GET", "chapter-claimed", user_id="owner")
    permission_allowed = request(app, "GET", "chapter-claimed", user_id="permitted")
    claimed_without_membership = request(app, "GET", "chapter-claimed", user_id="claimant")
    assert owner_allowed.status_code == 200
    assert permission_allowed.status_code == 200
    assert claimed_without_membership.status_code == 403
    assert claimed_without_membership.json()["detail"] == "无权访问该剧集"


def test_denial_happens_before_source_assets_and_private_draft_reads(database) -> None:
    engine, sessions = database
    statements: list[str] = []

    def observe(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", observe)
    response = request(build_app(sessions), "GET", "chapter-claimed", user_id="admin")
    event.remove(engine, "before_cursor_execute", observe)

    assert response.status_code == 403
    assert any("chapters" in sql for sql in statements)
    assert any("series" in sql for sql in statements)
    assert any("team_members" in sql for sql in statements)
    assert any("users" in sql for sql in statements)
    assert not any("storyboard_assets" in sql or "rough_cut_drafts" in sql for sql in statements)


def test_missing_series_is_404_before_source_or_private_reads(database) -> None:
    engine, sessions = database
    statements: list[str] = []

    def observe(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", observe)
    response = request(build_app(sessions), "GET", "chapter-missing-series", user_id="author")
    event.remove(engine, "before_cursor_execute", observe)

    assert response.status_code == 404
    assert response.json()["detail"] == "剧集不存在"
    assert not any("team_members" in sql or "users" in sql for sql in statements)
    assert not any("storyboard_assets" in sql or "rough_cut_drafts" in sql for sql in statements)


def test_next_request_observes_membership_and_claim_permission_revocation(database) -> None:
    engine, sessions = database
    app = build_app(sessions)

    before_member = request(app, "GET", "chapter-team", user_id="member")
    before_permission = request(app, "GET", "chapter-claimed", user_id="permitted")
    assert before_member.status_code == 200
    assert before_permission.status_code == 200

    with engine.begin() as connection:
        connection.execute(team_members.delete().where(team_members.c.id == "member-team"))
        connection.execute(
            team_members.update()
            .where(team_members.c.id == "permission-team")
            .values(permissions="[]")
        )
        connection.execute(
            users.update()
            .where(users.c.id == "claimant")
            .values(username="更新后的负责人")
        )

    after_member = request(app, "GET", "chapter-team", user_id="member")
    after_permission = request(app, "GET", "chapter-claimed", user_id="permitted")
    assert after_member.status_code == 403
    assert after_permission.status_code == 403
    assert after_permission.json()["detail"] == "该剧集已由「更新后的负责人」负责制作，暂不可进入"


def test_author_can_read_unclaimed_personal_series_but_not_skip_missing_series(database) -> None:
    _, sessions = database
    app = build_app(sessions)
    allowed = request(app, "GET", "chapter-personal", user_id="author")
    absent = request(app, "GET", "chapter-missing-series", user_id="outsider")
    assert allowed.status_code == 200
    assert absent.status_code == 404
