from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from sqlalchemy import create_engine, insert, select, update
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.asset_data.tables import ASSET_TABLES, metadata as asset_metadata
from haoai_backend.authentication.errors import AuthenticationError
from haoai_backend.authentication.tables import metadata as auth_metadata, users as auth_users
from haoai_backend.series_data.tables import (
    chapters,
    metadata as series_metadata,
    series,
)
from haoai_backend.series_maintenance.http import mount_series_maintenance_routes
from haoai_backend.series_maintenance.schemas import AssetMergeRequest
from haoai_backend.series_maintenance.persistence import (
    duplicate_exclusions,
    fused_assets,
    metadata as maintenance_metadata,
    series_maintenance_uow_factory,
)
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.series_access.tables import (
    metadata as series_access_metadata,
    team_members as series_team_members,
)

NOW = datetime(2026, 1, 1)


class RecordingSession(Session):
    events: list[str] = []

    def commit(self) -> None:
        self.events.append("commit")
        super().commit()

    def rollback(self) -> None:
        self.events.append("rollback")
        super().rollback()

    def close(self) -> None:
        self.events.append("close")
        super().close()


@pytest.fixture
def series_maintenance_app(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'series-maintenance.sqlite'}", future=True)
    auth_metadata.create_all(engine)
    series_metadata.create_all(engine, tables=[series, chapters])
    series_access_metadata.create_all(engine, tables=[series_team_members])
    asset_metadata.create_all(engine)
    maintenance_metadata.create_all(engine)
    RecordingSession.events = []
    sessions = sessionmaker(
        engine,
        class_=RecordingSession,
        expire_on_commit=False,
        future=True,
    )

    with engine.begin() as connection:
        connection.execute(
            insert(auth_users),
            [
                {
                    "id": "owner",
                    "username": "owner",
                    "email": "owner@example.test",
                    "hashed_password": "unused",
                    "is_superuser": False,
                    "membership_type": "premium",
                    "created_at": NOW,
                    "password_updated_at": None,
                },
                {
                    "id": "other",
                    "username": "other",
                    "email": "other@example.test",
                    "hashed_password": "unused",
                    "is_superuser": False,
                    "membership_type": "premium",
                    "created_at": NOW,
                    "password_updated_at": None,
                },
                {
                    "id": "viewer",
                    "username": "viewer",
                    "email": "viewer@example.test",
                    "hashed_password": "unused",
                    "is_superuser": False,
                    "membership_type": "free",
                    "created_at": NOW,
                    "password_updated_at": None,
                },
                {
                    "id": "editor",
                    "username": "editor",
                    "email": "editor@example.test",
                    "hashed_password": "unused",
                    "is_superuser": False,
                    "membership_type": "free",
                    "created_at": NOW,
                    "password_updated_at": None,
                },
                {
                    "id": "former-member",
                    "username": "former-member",
                    "email": "former-member@example.test",
                    "hashed_password": "unused",
                    "is_superuser": False,
                    "membership_type": "free",
                    "created_at": NOW,
                    "password_updated_at": None,
                },
            ],
        )
        connection.execute(
            insert(series).values(
                id="series-a",
                user_id="owner",
                name="测试剧集",
                description=None,
                image_url=None,
                style_prompt_id=None,
                team_id=None,
                claimed_by=None,
                claimed_at=None,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        connection.execute(
            insert(series),
            [
                {
                    "id": "series-team",
                    "user_id": "owner",
                    "name": "团队剧集",
                    "description": None,
                    "image_url": None,
                    "style_prompt_id": None,
                    "team_id": "team-a",
                    "claimed_by": None,
                    "claimed_at": None,
                    "created_at": NOW,
                    "updated_at": NOW,
                },
                {
                    "id": "series-claimed",
                    "user_id": "owner",
                    "name": "已认领团队剧集",
                    "description": None,
                    "image_url": None,
                    "style_prompt_id": None,
                    "team_id": "team-a",
                    "claimed_by": "owner",
                    "claimed_at": NOW,
                    "created_at": NOW,
                    "updated_at": NOW,
                },
            ],
        )
        connection.execute(
            insert(series_team_members),
            [
                {
                    "id": "membership-viewer",
                    "team_id": "team-a",
                    "user_id": "viewer",
                    "role": "viewer",
                    "permissions": None,
                },
                {
                    "id": "membership-editor",
                    "team_id": "team-a",
                    "user_id": "editor",
                    "role": "editor",
                    "permissions": '["enter_claimed_series"]',
                },
            ],
        )
        connection.execute(
            insert(chapters).values(
                id="chapter-a",
                series_id="series-a",
                title="第一集",
                content=json.dumps(
                    [
                        {
                            "character": ["char-keep", "char-merge", "char-merge"],
                            "scene": ["scene-a"],
                            "prop": ["prop-a"],
                        },
                        {"character": ["char-other"]},
                    ],
                    ensure_ascii=False,
                ),
                order=1,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        connection.execute(
            insert(ASSET_TABLES["character"]),
            [
                {
                    "id": "char-keep",
                    "series_id": "series-a",
                    "name": "周晴",
                    "description": "主角",
                    "image_url": "image://keep",
                    "aliases": '["晴晴"]',
                    "canonical_key": "周晴",
                    "created_at": NOW,
                    "updated_at": NOW,
                },
                {
                    "id": "char-merge",
                    "series_id": "series-a",
                    "name": "周 晴",
                    "description": "重复项",
                    "image_url": "image://merge",
                    "aliases": '["小晴"]',
                    "canonical_key": "周晴",
                    "created_at": NOW,
                    "updated_at": NOW,
                },
                {
                    "id": "char-other",
                    "series_id": "series-a",
                    "name": "王氏",
                    "description": None,
                    "image_url": None,
                    "aliases": "[]",
                    "canonical_key": "王氏",
                    "created_at": NOW,
                    "updated_at": NOW,
                },
            ],
        )
        connection.execute(
            insert(ASSET_TABLES["character"]),
            [
                {
                    "id": "team-char-a",
                    "series_id": "series-team",
                    "name": "团队角色",
                    "description": None,
                    "image_url": None,
                    "aliases": "[]",
                    "canonical_key": "团队角色",
                    "created_at": NOW,
                    "updated_at": NOW,
                },
                {
                    "id": "team-char-b",
                    "series_id": "series-team",
                    "name": "团队 角色",
                    "description": None,
                    "image_url": None,
                    "aliases": "[]",
                    "canonical_key": "团队角色",
                    "created_at": NOW,
                    "updated_at": NOW,
                },
                {
                    "id": "claimed-char-a",
                    "series_id": "series-claimed",
                    "name": "认领角色",
                    "description": None,
                    "image_url": None,
                    "aliases": "[]",
                    "canonical_key": "认领角色",
                    "created_at": NOW,
                    "updated_at": NOW,
                },
                {
                    "id": "claimed-char-b",
                    "series_id": "series-claimed",
                    "name": "认领 角色",
                    "description": None,
                    "image_url": None,
                    "aliases": "[]",
                    "canonical_key": "认领角色",
                    "created_at": NOW,
                    "updated_at": NOW,
                },
            ],
        )
        connection.execute(
            insert(ASSET_TABLES["scene"]).values(
                id="scene-a",
                series_id="series-a",
                title="盐宴厅_夜",
                description=None,
                image_url=None,
                aliases="[]",
                canonical_key="盐宴厅|夜晚",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        connection.execute(
            insert(ASSET_TABLES["prop"]).values(
                id="prop-a",
                series_id="series-a",
                name="信物",
                description=None,
                image_url=None,
                aliases="[]",
                canonical_key="信物",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        connection.execute(
            insert(fused_assets).values(
                id="fused-a",
                series_id="series-a",
                member_type="character",
                members='[{"kind":"asset","id":"char-merge","name":"旧名"}]',
                name="融合角色",
                image_url=None,
                size=None,
                status="completed",
                error_message=None,
                created_at=NOW,
                updated_at=NOW,
            )
        )

    async def resolve_actor(request: Request) -> TrustedActor:
        user_id = request.headers.get("x-test-user", "owner")
        if user_id not in {"owner", "other", "viewer", "editor", "former-member"}:
            raise AuthenticationError(401, "登录状态已失效", authenticate=True)
        return TrustedActor(user_id)

    app = FastAPI()
    mount_series_maintenance_routes(
        app,
        uow_factory=series_maintenance_uow_factory(sessions),
        resolve_actor=resolve_actor,
    )
    yield app, engine, sessions
    engine.dispose()


def send(
    app: FastAPI,
    method: str,
    path: str,
    body: object | None = None,
    *,
    user_id: str = "owner",
) -> httpx.Response:
    async def request() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://series-maintenance.test",
            headers={"x-test-user": user_id},
        ) as client:
            if body is None:
                return await client.request(method, path)
            return await client.request(method, path, json=body)

    return asyncio.run(request())


def send_raw(
    app: FastAPI,
    method: str,
    path: str,
    content: bytes,
    *,
    user_id: str | None = None,
    content_type: str | None = None,
) -> httpx.Response:
    async def request() -> httpx.Response:
        headers = {"x-test-user": user_id} if user_id is not None else {}
        if content_type is not None:
            headers["content-type"] = content_type
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://series-maintenance.test",
            headers=headers,
        ) as client:
            return await client.request(method, path, content=content)

    return asyncio.run(request())


def test_five_maintenance_routes_preserve_legacy_payloads_and_sql_effects(
    series_maintenance_app,
) -> None:
    app, engine, _sessions = series_maintenance_app

    duplicates = send(
        app,
        "GET",
        "/api/series/series-a/assets/duplicates?type=character",
    )
    assert duplicates.status_code == 200
    assert duplicates.json() == {
        "type": "character",
        "total": 3,
        "ignored_total": 0,
        "groups": [{
            "index": 0,
            "items": [
                {
                    "id": "char-merge",
                    "name": "周 晴",
                    "description": "重复项",
                    "image_url": "image://merge",
                    "ref_count": 2,
                    "aliases": ["小晴"],
                    "canonical_key": "周晴",
                },
                {
                    "id": "char-keep",
                    "name": "周晴",
                    "description": "主角",
                    "image_url": "image://keep",
                    "ref_count": 1,
                    "aliases": ["晴晴"],
                    "canonical_key": "周晴",
                },
            ],
        }],
    }

    invalid_type = send(
        app,
        "GET",
        "/api/series/series-a/assets/duplicates?type=bad",
    )
    assert invalid_type.status_code == 400
    assert invalid_type.json()["detail"] == "无效的资产类型"

    ignored = send(
        app,
        "POST",
        "/api/series/series-a/assets/duplicates/ignore",
        {"type": "character", "ids": ["char-keep", "char-merge", "char-keep"]},
    )
    assert ignored.status_code == 200
    assert ignored.json() == {
        "ignored": True,
        "pairs_added": 1,
        "pairs_total": 1,
    }
    assert RecordingSession.events.count("commit") == 1

    duplicate_ignore = send(
        app,
        "POST",
        "/api/series/series-a/assets/duplicates/ignore",
        {"type": "character", "ids": ["char-merge", "char-keep", "char-keep"]},
    )
    assert duplicate_ignore.status_code == 200
    assert duplicate_ignore.json() == {
        "ignored": True,
        "pairs_added": 0,
        "pairs_total": 1,
    }

    split_groups = send(
        app,
        "GET",
        "/api/series/series-a/assets/duplicates?type=character",
    )
    assert split_groups.json() == {
        "type": "character",
        "total": 3,
        "ignored_total": 1,
        "groups": [],
    }

    ignored_pairs = send(
        app,
        "GET",
        "/api/series/series-a/assets/duplicates/ignored?type=character",
    )
    assert ignored_pairs.status_code == 200
    assert ignored_pairs.json()["type"] == "character"
    assert ignored_pairs.json()["total"] == 1
    ignored_item = ignored_pairs.json()["items"][0]
    assert {
        "asset_id_a": "char-keep",
        "asset_id_b": "char-merge",
        "name_a": "周晴",
        "name_b": "周 晴",
        "image_a": "image://keep",
        "image_b": "image://merge",
    } == {key: ignored_item[key] for key in (
        "asset_id_a", "asset_id_b", "name_a", "name_b", "image_a", "image_b"
    )}
    assert ignored_item["id"]
    assert datetime.fromisoformat(ignored_item["created_at"])

    orphan_check = send(
        app,
        "POST",
        "/api/series/series-a/assets/orphan-check",
        {
            "items": [
                {"type": "character", "id": "char-keep"},
                {"type": "character", "id": "char-merge"},
                {"type": "prop", "id": "prop-a"},
                {"type": "other", "id": "ignored"},
                {"type": "character", "id": "not-owned"},
            ],
            "exclude_chapter_id": "chapter-a",
        },
    )
    assert orphan_check.status_code == 200
    assert orphan_check.json() == {
        "orphans": [
            {"type": "character", "id": "char-keep"},
            {"type": "character", "id": "char-merge"},
            {"type": "prop", "id": "prop-a"},
        ],
    }
    with engine.connect() as connection:
        still_owned = connection.execute(
            select(ASSET_TABLES["character"].c.id)
            .where(ASSET_TABLES["character"].c.id.in_(("char-keep", "char-merge")))
            .order_by(ASSET_TABLES["character"].c.id)
        ).scalars().all()
    assert still_owned == ["char-keep", "char-merge"]

    RecordingSession.events = []
    merged = send(
        app,
        "POST",
        "/api/series/series-a/assets/merge",
        {
            "type": "character",
            "keep_id": "char-keep",
            "merge_ids": ["char-keep", "char-merge", "char-merge"],
        },
    )
    assert merged.status_code == 200
    assert merged.json() == {
        "message": "已合并 1 条资产到「周晴」",
        "keep_id": "char-keep",
        "merged_count": 1,
        "redirected_frames": 1,
        "fused_updated": 1,
        "aliases_added": 1,
        "dropped_images": [{"id": "char-merge", "name": "周 晴"}],
        "aliases": ["晴晴", "小晴"],
    }
    assert RecordingSession.events.count("commit") == 2
    assert RecordingSession.events.count("close") == 1

    with engine.connect() as connection:
        keep = connection.execute(
            select(ASSET_TABLES["character"]).where(
                ASSET_TABLES["character"].c.id == "char-keep"
            )
        ).mappings().one()
        removed = connection.execute(
            select(ASSET_TABLES["character"].c.id).where(
                ASSET_TABLES["character"].c.id == "char-merge"
            )
        ).all()
        chapter = connection.execute(
            select(chapters.c.content).where(chapters.c.id == "chapter-a")
        ).scalar_one()
        fused = connection.execute(
            select(fused_assets.c.members).where(fused_assets.c.id == "fused-a")
        ).scalar_one()
        exclusion_count = connection.execute(
            select(duplicate_exclusions.c.id)
        ).all()

    assert json.loads(keep["aliases"]) == ["晴晴", "小晴"]
    assert keep["image_url"] == "image://keep"
    assert removed == []
    assert json.loads(chapter)[0]["character"] == ["char-keep"]
    assert json.loads(fused)[0] == {
        "kind": "asset",
        "id": "char-keep",
        "name": "周晴",
    }
    assert exclusion_count == []

    invalid_merge = send(
        app,
        "POST",
        "/api/series/series-a/assets/merge",
        {"type": "character", "keep_id": "missing", "merge_ids": ["char-other"]},
    )
    assert invalid_merge.status_code == 404
    assert invalid_merge.json()["detail"] == "保留的资产不存在"
    assert RecordingSession.events[-2:] == ["rollback", "close"]


def test_duplicate_pair_restore_canonicalizes_and_reports_wire_counts(
    series_maintenance_app,
) -> None:
    app, engine, _sessions = series_maintenance_app

    ignored = send(
        app,
        "POST",
        "/api/series/series-a/assets/duplicates/ignore",
        {"type": "character", "ids": ["char-merge", "char-keep", "char-keep"]},
    )
    assert ignored.status_code == 200
    assert ignored.json() == {
        "ignored": True,
        "pairs_added": 1,
        "pairs_total": 1,
    }

    with engine.connect() as connection:
        pair = connection.execute(
            select(
                duplicate_exclusions.c.asset_id_a,
                duplicate_exclusions.c.asset_id_b,
            )
        ).one()
    assert tuple(pair) == ("char-keep", "char-merge")

    restored = send(
        app,
        "POST",
        "/api/series/series-a/assets/duplicates/ignore",
        {
            "type": "character",
            "ids": ["char-merge", "char-keep", "char-merge"],
            "ignored": False,
        },
    )
    assert restored.status_code == 200
    assert restored.json() == {
        "ignored": False,
        "pairs_removed": 1,
        "pairs_total": 1,
    }

    duplicates = send(
        app,
        "GET",
        "/api/series/series-a/assets/duplicates?type=character",
    )
    assert duplicates.status_code == 200
    assert duplicates.json()["ignored_total"] == 0
    assert len(duplicates.json()["groups"]) == 1


@pytest.mark.parametrize(
    ("initial_key", "keep_image", "merged_aliases", "expected_aliases", "expected_image"),
    [
        (None, "image://keep", '["小晴"]', ["晴晴", "小晴"], "image://keep"),
        ("陈旧键", None, '["晴晴"]', ["晴晴"], "image://merge"),
        ("陈旧键", "image://keep", '["晴晴"]', ["晴晴"], "image://keep"),
    ],
    ids=[
        "alias-change",
        "image-change-without-alias-change",
        "stale-key-without-alias-or-image-change",
    ],
)
def test_merge_refreshes_canonical_key_on_every_legacy_dirty_update(
    series_maintenance_app,
    initial_key: str | None,
    keep_image: str | None,
    merged_aliases: str,
    expected_aliases: list[str],
    expected_image: str,
) -> None:
    # The fixed legacy before_update listener recomputes make_key from the
    # displayed name whenever an asset row is dirty, including alias/image edits.
    app, engine, _sessions = series_maintenance_app
    character = ASSET_TABLES["character"]

    with engine.begin() as connection:
        connection.execute(
            update(character)
            .where(character.c.id == "char-keep")
            .values(canonical_key=initial_key, image_url=keep_image)
        )
        connection.execute(
            update(character)
            .where(character.c.id == "char-merge")
            .values(aliases=merged_aliases)
        )
        connection.execute(
            insert(character).values(
                id="canonical-peer",
                series_id="series-a",
                name="完全不同",
                description=None,
                image_url=None,
                aliases="[]",
                canonical_key="周晴",
                created_at=NOW,
                updated_at=NOW,
            )
        )

    merged = send(
        app,
        "POST",
        "/api/series/series-a/assets/merge",
        {
            "type": "character",
            "keep_id": "char-keep",
            "merge_ids": ["char-merge"],
        },
    )
    assert merged.status_code == 200

    with engine.connect() as connection:
        keep = connection.execute(
            select(
                character.c.canonical_key,
                character.c.aliases,
                character.c.image_url,
            ).where(character.c.id == "char-keep")
        ).mappings().one()
    assert keep["canonical_key"] == "周晴"
    assert json.loads(keep["aliases"]) == expected_aliases
    assert keep["image_url"] == expected_image

    duplicates = send(
        app,
        "GET",
        "/api/series/series-a/assets/duplicates?type=character",
    )
    assert duplicates.status_code == 200
    assert len(duplicates.json()["groups"]) == 1
    duplicate_items = duplicates.json()["groups"][0]["items"]
    assert {item["id"] for item in duplicate_items} == {
        "char-keep",
        "canonical-peer",
    }
    assert {item["canonical_key"] for item in duplicate_items} == {"周晴"}


def test_merge_alias_and_first_image_order_matches_legacy_id_in_query(
    series_maintenance_app,
) -> None:
    app, engine, _sessions = series_maintenance_app
    character = ASSET_TABLES["character"]
    requested_ids = ["z-merge", "a-merge"]

    with engine.begin() as connection:
        connection.execute(
            update(character)
            .where(character.c.id == "char-keep")
            .values(aliases="[]", image_url=None)
        )
        connection.execute(
            insert(character),
            [
                {
                    "id": "z-merge",
                    "series_id": "series-a",
                    "name": "角色 Z",
                    "description": None,
                    "image_url": "image://z-first-inserted",
                    "aliases": '["别名 Z"]',
                    "canonical_key": "角色z",
                    "created_at": NOW,
                    "updated_at": NOW,
                },
                {
                    "id": "a-merge",
                    "series_id": "series-a",
                    "name": "角色 A",
                    "description": None,
                    "image_url": "image://a-second-inserted",
                    "aliases": '["别名 A"]',
                    "canonical_key": "角色a",
                    "created_at": NOW,
                    "updated_at": NOW,
                },
            ],
        )

    with engine.connect() as connection:
        legacy_rows = connection.execute(
            select(character).where(
                character.c.id.in_(requested_ids),
                character.c.series_id == "series-a",
            )
        ).mappings().all()
        full_series_rows = connection.execute(
            select(character).where(character.c.series_id == "series-a")
        ).mappings().all()

    # This query mirrors the old ORM filter and captures its real SQLite result
    # order for this fresh database; neither control query adds ORDER BY.
    legacy_order = [row["id"] for row in legacy_rows]
    full_series_order = [
        row["id"] for row in full_series_rows if row["id"] in requested_ids
    ]
    assert legacy_order != full_series_order, (
        f"legacy id-IN order {legacy_order} unexpectedly matched full-series order "
        f"{full_series_order}"
    )

    expected_aliases = []
    for row in legacy_rows:
        expected_aliases.extend([row["name"], *json.loads(row["aliases"])])
    expected_image = next(row["image_url"] for row in legacy_rows if row["image_url"])

    merged = send(
        app,
        "POST",
        "/api/series/series-a/assets/merge",
        {
            "type": "character",
            "keep_id": "char-keep",
            "merge_ids": requested_ids,
        },
    )
    assert merged.status_code == 200
    assert merged.json()["aliases"] == expected_aliases

    with engine.connect() as connection:
        keep_image = connection.execute(
            select(character.c.image_url).where(character.c.id == "char-keep")
        ).scalar_one()
    assert keep_image == expected_image


def test_owner_and_team_members_use_the_shared_series_access_gate(
    series_maintenance_app,
) -> None:
    app, _engine, _sessions = series_maintenance_app

    owner_read = send(
        app,
        "GET",
        "/api/series/series-a/assets/duplicates?type=character",
        user_id="owner",
    )
    unrelated_user = send(
        app,
        "GET",
        "/api/series/series-a/assets/duplicates?type=character",
        user_id="other",
    )
    assert owner_read.status_code == 200
    assert unrelated_user.status_code == 403

    viewer_read = send(
        app,
        "GET",
        "/api/series/series-team/assets/duplicates?type=character",
        user_id="viewer",
    )
    viewer_write = send(
        app,
        "POST",
        "/api/series/series-team/assets/duplicates/ignore",
        {"type": "character", "ids": ["team-char-a", "team-char-b"]},
        user_id="viewer",
    )
    assert viewer_read.status_code == 200
    assert viewer_write.status_code == 200
    assert viewer_write.json()["pairs_added"] == 1

    editor_read_claimed = send(
        app,
        "GET",
        "/api/series/series-claimed/assets/duplicates?type=character",
        user_id="editor",
    )
    editor_write_claimed = send(
        app,
        "POST",
        "/api/series/series-claimed/assets/duplicates/ignore",
        {"type": "character", "ids": ["claimed-char-a", "claimed-char-b"]},
        user_id="editor",
    )
    viewer_read_claimed = send(
        app,
        "GET",
        "/api/series/series-claimed/assets/duplicates?type=character",
        user_id="viewer",
    )
    removed_member = send(
        app,
        "GET",
        "/api/series/series-team/assets/duplicates?type=character",
        user_id="former-member",
    )
    assert editor_read_claimed.status_code == 200
    assert editor_write_claimed.status_code == 200
    assert viewer_read_claimed.status_code == 403
    assert removed_member.status_code == 403


def test_merge_rolls_back_asset_chapter_and_fused_changes_on_failure(
    series_maintenance_app,
) -> None:
    _app, engine, sessions = series_maintenance_app
    base_factory = series_maintenance_uow_factory(sessions)

    def fail_before_asset_delete():
        uow = base_factory()

        def fail_delete(_asset_type: str, _asset_id: str) -> None:
            raise RuntimeError("injected failure before asset deletion")

        uow.delete_asset = fail_delete
        return uow

    async def resolve_owner(_request) -> TrustedActor:
        return TrustedActor("owner")

    app = FastAPI()
    mount_series_maintenance_routes(
        app,
        uow_factory=fail_before_asset_delete,
        resolve_actor=resolve_owner,
    )

    with pytest.raises(RuntimeError, match="injected failure"):
        send(
            app,
            "POST",
            "/api/series/series-a/assets/merge",
            {
                "type": "character",
                "keep_id": "char-keep",
                "merge_ids": ["char-merge"],
            },
        )

    assert RecordingSession.events[-2:] == ["rollback", "close"]
    with engine.connect() as connection:
        keep_aliases = connection.execute(
            select(ASSET_TABLES["character"].c.aliases).where(
                ASSET_TABLES["character"].c.id == "char-keep"
            )
        ).scalar_one()
        asset_ids = connection.execute(
            select(ASSET_TABLES["character"].c.id)
            .where(ASSET_TABLES["character"].c.series_id == "series-a")
            .order_by(ASSET_TABLES["character"].c.id)
        ).scalars().all()
        chapter_content = connection.execute(
            select(chapters.c.content).where(chapters.c.id == "chapter-a")
        ).scalar_one()
        fused_members = connection.execute(
            select(fused_assets.c.members).where(fused_assets.c.id == "fused-a")
        ).scalar_one()

    assert json.loads(keep_aliases) == ["晴晴"]
    assert asset_ids == ["char-keep", "char-merge", "char-other"]
    assert json.loads(chapter_content)[0]["character"] == [
        "char-keep", "char-merge", "char-merge"
    ]
    assert json.loads(fused_members)[0]["id"] == "char-merge"


@pytest.mark.parametrize("content_type", ["text/plain", "application/octet-stream"])
def test_json_media_types_match_the_legacy_fastapi_dto_control(
    series_maintenance_app,
    content_type: str,
) -> None:
    app, _engine, _sessions = series_maintenance_app
    legacy_control = FastAPI()

    def valid_actor(_request: Request) -> TrustedActor:
        return TrustedActor("owner")

    @legacy_control.post("/api/series/{series_id}/assets/merge")
    def legacy_merge(
        series_id: str,
        payload: AssetMergeRequest,
        actor: TrustedActor = Depends(valid_actor),
    ) -> dict[str, str]:
        return {"series_id": series_id, "actor": actor.user_id, "type": payload.type}

    path = "/api/series/series-missing/assets/merge"
    body = json.dumps(
        {"type": "character", "keep_id": "keep", "merge_ids": ["merge"]}
    ).encode("utf-8")
    control_response = send_raw(
        legacy_control,
        "POST",
        path,
        body,
        user_id="owner",
        content_type=content_type,
    )
    candidate_response = send_raw(
        app,
        "POST",
        path,
        body,
        user_id="owner",
        content_type=content_type,
    )

    assert control_response.status_code == 422
    assert candidate_response.status_code == control_response.status_code
    assert candidate_response.json() == control_response.json()


def test_legacy_lexical_json_error_precedes_auth_but_semantic_body_error_does_not(
) -> None:
    legacy_control = FastAPI()
    candidate = FastAPI()
    business_sessions: list[str] = []

    def reject_identity(_request: Request) -> TrustedActor:
        raise HTTPException(
            status_code=401,
            detail="登录状态已失效",
            headers={"WWW-Authenticate": "Bearer"},
        )

    @legacy_control.post("/api/series/{series_id}/assets/merge")
    def legacy_merge(
        series_id: str,
        payload: AssetMergeRequest,
        actor: TrustedActor = Depends(reject_identity),
    ) -> dict[str, str]:
        return {"series_id": series_id, "actor": actor.user_id, "type": payload.type}

    def fail_if_business_session_opens() -> Session:
        business_sessions.append("opened")
        raise AssertionError("body-validation failures must not open a business session")

    mount_series_maintenance_routes(
        candidate,
        uow_factory=series_maintenance_uow_factory(fail_if_business_session_opens),
        resolve_actor=reject_identity,
    )
    path = "/api/series/series-a/assets/merge"

    legacy_lexical_error = send_raw(
        legacy_control,
        "POST",
        path,
        b"{",
        content_type="application/json",
    )
    candidate_lexical_error = send_raw(
        candidate,
        "POST",
        path,
        b"{",
        content_type="application/json",
    )
    legacy_semantic_error = send_raw(
        legacy_control,
        "POST",
        path,
        b'{"type":"character"}',
        content_type="application/json",
    )
    candidate_semantic_error = send_raw(
        candidate,
        "POST",
        path,
        b'{"type":"character"}',
        content_type="application/json",
    )

    assert legacy_lexical_error.status_code == 422
    assert candidate_lexical_error.status_code == legacy_lexical_error.status_code
    assert candidate_lexical_error.json() == legacy_lexical_error.json()
    assert legacy_semantic_error.status_code == 401
    assert candidate_semantic_error.status_code == legacy_semantic_error.status_code
    assert candidate_semantic_error.json() == legacy_semantic_error.json()
    assert business_sessions == []


def test_custom_actor_resolver_precedes_semantic_body_validation() -> None:
    calls: list[str] = []

    def session_factory() -> Session:
        calls.append("session")
        raise AssertionError("invalid bodies must not open a business session")

    def resolve_actor(request) -> TrustedActor:
        user_id = request.headers.get("x-test-user")
        if user_id is None:
            raise AuthenticationError(401, "登录状态已失效", authenticate=True)
        if user_id == "revoked":
            raise AuthenticationError(403, "账号已停用", authenticate=False)
        return TrustedActor(user_id)

    app = FastAPI()
    mount_series_maintenance_routes(
        app,
        uow_factory=series_maintenance_uow_factory(session_factory),
        resolve_actor=resolve_actor,
    )

    missing_identity = send_raw(
        app,
        "POST",
        "/api/series/series-a/assets/merge",
        b"{}",
    )
    revoked_identity = send_raw(
        app,
        "POST",
        "/api/series/series-a/assets/merge",
        b"{}",
        user_id="revoked",
    )
    active_identity = send_raw(
        app,
        "POST",
        "/api/series/series-a/assets/merge",
        b'{"type":"character"}',
        user_id="active-user",
    )

    assert missing_identity.status_code == 401
    assert missing_identity.json() == {"detail": "登录状态已失效"}
    assert missing_identity.headers["www-authenticate"] == "Bearer"
    assert revoked_identity.status_code == 403
    assert revoked_identity.json() == {"detail": "账号已停用"}
    assert active_identity.status_code == 422
    assert active_identity.json()["detail"][0]["loc"] == ["body", "keep_id"]
    assert calls == []
