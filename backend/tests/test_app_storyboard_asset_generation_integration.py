"""Real JWT/SQLite composition checks for storyboard asset-generation routes."""

from __future__ import annotations

import asyncio
import json
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, insert, select, update, func
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.admin_credit_queries.tables import CreditLogRow, billing_units_reference, metadata as credit_metadata
from haoai_backend.admin_system_models.crypto import LegacyFernet
from haoai_backend.app import create_app
from haoai_backend.asset_data import naming
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.tables import metadata as auth_metadata, user_credits, user_sessions, users
from haoai_backend.authentication.tokens import JoseTokenCodec
from haoai_backend.generation_catalog.tables import (
    metadata as catalog_metadata,
    model_configs,
    prompt_configs,
    system_models,
    system_prompts,
)
from haoai_backend.series_data.tables import (
    chapters,
    characters,
    metadata as series_metadata,
    props,
    scenes,
    series,
)
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.storyboard_asset_generation.application import prepare_extract
from haoai_backend.storyboard_asset_generation.errors import StoryboardAssetUnavailable
from haoai_backend.storyboard_asset_generation.execution import stream_asset_events
from haoai_backend.storyboard_asset_generation.openai_runtime import (
    OpenAICompatibleStoryboardAssetRuntime,
)
from haoai_backend.storyboard_asset_generation.persistence import SqlAlchemyStoryboardAssetUnitOfWork
from haoai_backend.storyboard_asset_generation.schemas import ExtractRequest
from haoai_backend.teams.tables import metadata as teams_metadata, team_members, teams
from haoai_backend.storyboard_asset_generation.ports import StoryboardAssetRuntimeCall


SECRET = "synthetic-storyboard-assets-secret"
ROUTES = {
    ("POST", "/api/storyboard/extract-characters-stream", "extract_characters_stream"),
    ("POST", "/api/storyboard/judge-character-appearance-stream", "judge_character_appearance_stream"),
    ("POST", "/api/storyboard/extract-scenes-stream", "extract_scenes_stream"),
    ("POST", "/api/storyboard/judge-scene-appearance-stream", "judge_scene_appearance_stream"),
    ("POST", "/api/storyboard/extract-props-stream", "extract_props_stream"),
    ("POST", "/api/storyboard/judge-prop-appearance-stream", "judge_prop_appearance_stream"),
}


class ObservedSession(Session):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.close_count = 0
        self.commit_count = 0

    def commit(self) -> None:
        self.commit_count += 1
        super().commit()

    def close(self) -> None:
        self.close_count += 1
        super().close()


class LocalAssetRuntime:
    def __init__(self) -> None:
        self.calls: list[StoryboardAssetRuntimeCall] = []
        self.business_sessions: list[ObservedSession] = []
        self.responses: dict[str, list[Any]] = {
            "extract_character": ['[{"name":"顾宁","gender":"女"}]'],
            "judge_character": ['[[{"character":"顾宁"}]]'],
            "extract_scene": ['[{"name":"客厅_夜","visualPrompt":"暖色灯光"}]'],
            "judge_scene": ['[[null,{"title":"客厅_夜"}]]'],
            "extract_prop": ['[{"name":"旧钥匙","visualPrompt":"铜制"}]'],
            "judge_prop": ['[[{"prop":"旧钥匙"}]]'],
        }

    async def stream(self, call: StoryboardAssetRuntimeCall):
        assert all(session.close_count == 1 for session in self.business_sessions)
        self.calls.append(call)
        for value in self.responses.get(call.operation, ["[]"]):
            yield value


class _FakeProviderResponse:
    status = 200

    def __init__(self, content: str) -> None:
        payload = {
            "choices": [
                {"delta": {"content": content}, "finish_reason": "stop"}
            ]
        }
        self._body = f"data: {json.dumps(payload, ensure_ascii=False)}\n".encode()
        self.closed = False

    async def iter_bytes(self):
        for offset in range(0, len(self._body), 3):
            yield self._body[offset:offset + 3]


class _FakeProviderTransport:
    def __init__(self, contents: list[str]) -> None:
        self._contents = iter(contents)
        self.requests: list[dict[str, Any]] = []
        self.responses: list[_FakeProviderResponse] = []

    @asynccontextmanager
    async def open_stream(self, url, *, headers, json_body):
        self.requests.append(
            {
                "url": url,
                "headers": dict(headers),
                "json_body": dict(json_body),
            }
        )
        response = _FakeProviderResponse(next(self._contents))
        self.responses.append(response)
        try:
            yield response
        finally:
            response.closed = True


@dataclass
class System:
    engine: Any
    app: FastAPI
    runtime: Any
    business_sessions: list[ObservedSession]
    auth_sessions: list[ObservedSession]
    opened: list[tuple[str, int]]
    token_for: Callable[[str], str]


def _build_system(
    path: Path,
    *,
    with_runtime: bool = True,
    cipher_secret_key: str | None = SECRET,
    runtime_override: Any = None,
) -> System:
    engine = create_engine(f"sqlite:///{path}", future=True)
    auth_metadata.create_all(engine)
    teams_metadata.create_all(engine, tables=[teams, team_members])
    series_metadata.create_all(
        engine,
        tables=[series, chapters, characters, scenes, props],
    )
    catalog_metadata.create_all(
        engine,
        tables=[model_configs, system_models, prompt_configs, system_prompts],
    )
    credit_metadata.create_all(engine, tables=[billing_units_reference])
    CreditLogRow.__table__.create(engine, checkfirst=True)

    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    with engine.begin() as connection:
        connection.execute(
            insert(users),
            [
                {
                    "id": user_id,
                    "username": user_id,
                    "email": f"{user_id}@example.test",
                    "hashed_password": "synthetic-hash-only",
                    "is_superuser": False,
                    "membership_type": membership,
                    "membership_expires_at": None,
                    "avatar_url": None,
                    "bio": None,
                    "created_at": now,
                    "password_updated_at": now,
                }
                for user_id, membership in (("member", "premium"), ("peer", "premium"), ("free", "free"))
            ],
        )
        connection.execute(
            insert(user_sessions),
            [
                {
                    "id": f"session-{user_id}",
                    "user_id": user_id,
                    "jti": f"jti-{user_id}",
                    "user_agent": "local-test",
                    "ip_address": "127.0.0.1",
                    "device_name": "test",
                    "is_active": True,
                    "created_at": now,
                    "last_seen_at": now,
                    "revoked_at": None,
                }
                for user_id in ("member", "peer", "free")
            ],
        )
        connection.execute(
            insert(user_credits),
            [
                {"id": "credit-member", "user_id": "member", "credits": 73, "created_at": now, "updated_at": now},
                {"id": "credit-peer", "user_id": "peer", "credits": 21, "created_at": now, "updated_at": now},
                {"id": "credit-free", "user_id": "free", "credits": 0, "created_at": now, "updated_at": now},
            ],
        )
        connection.execute(
            insert(series),
            [
                {
                    "id": f"series-{user_id}",
                    "user_id": user_id,
                    "name": f"{user_id} series",
                    "description": None,
                    "image_url": None,
                    "style_prompt_id": None,
                    "team_id": None,
                    "claimed_by": None,
                    "claimed_at": None,
                    "created_at": now,
                    "updated_at": now,
                }
                for user_id in ("member", "peer")
            ],
        )
        connection.execute(
            insert(chapters),
            [
                {
                    "id": f"chapter-{user_id}",
                    "series_id": f"series-{user_id}",
                    "title": "第1集",
                    "content": json.dumps(
                        [
                            {"text": "第一段"},
                            {"original_text": "第二段"},
                        ],
                        ensure_ascii=False,
                    ),
                    "order": 0,
                    "created_at": now,
                    "updated_at": now,
                }
                for user_id in ("member", "peer")
            ],
        )
        connection.execute(
            insert(system_models).values(
                id="system-model",
                category="other",
                name="Synthetic model",
                provider="local",
                api_key="server-only-key",
                model=None,
                base_url=None,
                cost_per_call=25,
                description=None,
                params=None,
                is_active=True,
                sort_order=0,
                created_at=now,
                updated_at=now,
            )
        )
        connection.execute(
            insert(model_configs).values(
                id="owned-model",
                user_id="member",
                category="other",
                name="Owned model",
                provider="local",
                api_key="member-only-key",
                model="",
                base_url=None,
                sort_order=0,
                params=None,
                created_at=now,
                updated_at=now,
            )
        )
        connection.execute(
            insert(prompt_configs),
            [
                {
                    "id": "owned-prompt",
                    "user_id": "member",
                    "category": "",
                    "name": "",
                    "system_prompt": "selected-system",
                    "user_prompt": "selected-user:{storyboard_texts}",
                    "sort_order": 0,
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": "peer-prompt",
                    "user_id": "peer",
                    "category": "",
                    "name": "foreign",
                    "system_prompt": "foreign",
                    "user_prompt": "foreign",
                    "sort_order": 0,
                    "created_at": now,
                    "updated_at": now,
                },
            ],
        )
        connection.execute(
            insert(system_prompts).values(
                id="active-prompt",
                category="",
                name="",
                system_prompt="active-system",
                user_prompt="active-user",
                is_active=True,
                sort_order=0,
                created_at=now,
                updated_at=now,
            )
        )
        connection.execute(
            insert(characters).values(
                id="existing-character",
                series_id="series-member",
                name="沈夜棠_青年_工服围裙",
                aliases=None,
                canonical_key=None,
                gender="女",
                age="青年",
                role="主角",
                appearance="工作服",
                description=None,
                image_url=None,
                audio_url=None,
                voice_ref=None,
                created_at=now,
                updated_at=now,
            )
        )

    opened: list[tuple[str, int]] = []
    business_sessions: list[ObservedSession] = []
    auth_sessions: list[ObservedSession] = []

    def make_factory(kind: str, bucket: list[ObservedSession]):
        maker = sessionmaker(engine, class_=ObservedSession, expire_on_commit=False, future=True)

        def factory() -> ObservedSession:
            opened.append((kind, threading.get_ident()))
            session = maker()
            bucket.append(session)
            return session

        return factory

    auth_factory = make_factory("auth", auth_sessions)
    business_factory = make_factory("business", business_sessions)
    runtime = runtime_override if runtime_override is not None else LocalAssetRuntime()
    if isinstance(runtime, LocalAssetRuntime):
        runtime.business_sessions = business_sessions
    auth_runtime = AuthenticationRuntime(
        session_factory=auth_factory,
        secret_key=cipher_secret_key,
        token_codec=JoseTokenCodec(SECRET),
        clock=lambda: now,
    )
    app = create_app(
        session_factory=business_factory,
        authentication_runtime=auth_runtime,
        storyboard_asset_generation_runtime=runtime if with_runtime else None,
    )
    codec = JoseTokenCodec(SECRET)

    def token_for(user_id: str) -> str:
        return codec.encode(
            {
                "sub": user_id,
                "jti": f"jti-{user_id}",
                "exp": datetime.now(timezone.utc) + timedelta(hours=1),
            }
        )

    return System(engine, app, runtime, business_sessions, auth_sessions, opened, token_for)


@pytest.fixture
def system(tmp_path: Path):
    value = _build_system(tmp_path / "storyboard-assets.sqlite3")
    try:
        yield value
    finally:
        value.engine.dispose()


def _post(system: System, path: str, *, user: str = "member", body: Any = None, raw: bytes | None = None):
    async def send():
        headers = {"Authorization": f"Bearer {system.token_for(user)}"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=system.app, raise_app_exceptions=False),
            base_url="http://storyboard-asset-generation.test",
        ) as client:
            if raw is not None:
                headers["Content-Type"] = "application/json"
                return await client.post(path, headers=headers, content=raw)
            return await client.post(path, headers=headers, json=body)

    return asyncio.run(send())


def _events(response: httpx.Response) -> list[dict[str, Any]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def test_default_jwt_and_real_sqlite_cover_all_six_routes_without_fee_writes(system: System) -> None:
    assert ROUTES <= {
        (method, route.path, route.name)
        for route in system.app.routes
        for method in route.methods
        if method not in {"HEAD", "OPTIONS"}
    }
    requests = [
        (
            "/api/storyboard/extract-characters-stream",
            {"chapter_id": "chapter-member", "model_id": "system-model", "task_id": "unused-task", "frame_indices": [1, 0, 1]},
            "extract_character",
        ),
        (
            "/api/storyboard/judge-character-appearance-stream",
            {"chapter_id": "chapter-member", "model_id": "system-model", "prompt_id": "owned-prompt", "characters": [{"name": "顾宁", "is_existing": False}], "storyboard_texts": ["角色入镜"], "frame_numbers": [8], "skip_merge": True},
            "judge_character",
        ),
        (
            "/api/storyboard/extract-scenes-stream",
            {"chapter_id": "chapter-member", "model_id": "owned-model", "frame_indices": [0]},
            "extract_scene",
        ),
        (
            "/api/storyboard/judge-scene-appearance-stream",
            {"chapter_id": "chapter-member", "model_id": "system-model", "scenes": [{"title": "客厅_夜", "is_existing": False}], "storyboard_texts": ["客厅里"], "frame_numbers": [3], "skip_merge": True},
            "judge_scene",
        ),
        (
            "/api/storyboard/extract-props-stream",
            {"chapter_id": "chapter-member", "model_id": "system-model", "prompt_id": "active-prompt"},
            "extract_prop",
        ),
        (
            "/api/storyboard/judge-prop-appearance-stream",
            {"chapter_id": "chapter-member", "model_id": "system-model", "props": [{"name": "旧钥匙", "is_existing": False}], "storyboard_texts": ["拿起钥匙"], "skip_merge": True},
            "judge_prop",
        ),
    ]
    for path, body, operation in requests:
        response = _post(system, path, body=body)
        assert response.status_code == 200, response.text
        events = _events(response)
        assert events[-1] == {"type": "done"}
        assert any(event.get("type") == "complete" for event in events)
        call = next(call for call in reversed(system.runtime.calls) if call.operation == operation)
        assert call.model.api_key in {"server-only-key", "member-only-key"}
        assert call.temperature == 0.7

    extraction_call = next(call for call in system.runtime.calls if call.operation == "extract_character")
    assert extraction_call.raw_content == "第二段\n\n第一段\n\n第二段"
    assert system.runtime.calls[1].system_prompt == "selected-system"
    assert "selected-user:" in system.runtime.calls[1].user_prompt
    assert "【分镜 8】" in system.runtime.calls[1].user_prompt
    assert all(session.close_count == 1 for session in system.business_sessions)
    assert all(session.close_count == 1 for session in system.auth_sessions)
    assert system.opened
    assert all(thread_id != threading.main_thread().ident for _, thread_id in system.opened)
    first_business = next(index for index, (kind, _) in enumerate(system.opened) if kind == "business")
    assert all(kind == "auth" for kind, _ in system.opened[:first_business])

    with system.engine.connect() as connection:
        assert connection.execute(select(user_credits.c.credits).where(user_credits.c.user_id == "member")).scalar_one() == 73
        assert connection.execute(select(func.count()).select_from(CreditLogRow.__table__)).scalar_one() == 0


def test_real_provider_runtime_serves_all_six_routes_through_fake_http(tmp_path: Path) -> None:
    transport = _FakeProviderTransport(
        [
            '[{"name":"顾宁","gender":"女"}]',
            '[[{"character":"顾宁"}]]',
            '[{"name":"客厅_夜","visualPrompt":"暖光"}]',
            '[[{"title":"客厅_夜"}]]',
            '[{"name":"旧钥匙","visualPrompt":"铜制"}]',
            '[[{"prop":"旧钥匙"}]]',
        ]
    )
    runtime = OpenAICompatibleStoryboardAssetRuntime(transport=transport)
    system = _build_system(
        tmp_path / "storyboard-assets-provider.sqlite3",
        runtime_override=runtime,
    )
    requests = [
        (
            "/api/storyboard/extract-characters-stream",
            {
                "chapter_id": "chapter-member",
                "model_id": "system-model",
                "frame_indices": [1, 0, 1],
            },
        ),
        (
            "/api/storyboard/judge-character-appearance-stream",
            {
                "chapter_id": "chapter-member",
                "model_id": "system-model",
                "prompt_id": "owned-prompt",
                "characters": [{"name": "顾宁", "is_existing": False}],
                "storyboard_texts": ["角色入镜"],
                "frame_numbers": [8],
                "skip_merge": True,
            },
        ),
        (
            "/api/storyboard/extract-scenes-stream",
            {
                "chapter_id": "chapter-member",
                "model_id": "owned-model",
                "frame_indices": [0],
            },
        ),
        (
            "/api/storyboard/judge-scene-appearance-stream",
            {
                "chapter_id": "chapter-member",
                "model_id": "system-model",
                "scenes": [{"title": "客厅_夜", "is_existing": False}],
                "storyboard_texts": ["客厅里"],
                "frame_numbers": [3],
                "skip_merge": True,
            },
        ),
        (
            "/api/storyboard/extract-props-stream",
            {
                "chapter_id": "chapter-member",
                "model_id": "system-model",
                "prompt_id": "active-prompt",
            },
        ),
        (
            "/api/storyboard/judge-prop-appearance-stream",
            {
                "chapter_id": "chapter-member",
                "model_id": "system-model",
                "props": [{"name": "旧钥匙", "is_existing": False}],
                "storyboard_texts": ["拿起钥匙"],
                "skip_merge": True,
            },
        ),
    ]
    try:
        denied = _post(
            system,
            requests[0][0],
            user="free",
            body={"chapter_id": "chapter-member", "model_id": "system-model"},
        )
        foreign = _post(
            system,
            requests[0][0],
            body={"chapter_id": "chapter-peer", "model_id": "system-model"},
        )
        assert denied.status_code == 403
        assert foreign.status_code == 403
        assert transport.requests == []

        completed: list[dict[str, Any]] = []
        for path, body in requests:
            response = _post(system, path, body=body)
            assert response.status_code == 200, response.text
            events = _events(response)
            assert events[-1] == {"type": "done"}
            complete = next(event for event in events if event.get("type") == "complete")
            completed.append(complete)

        assert [row["name"] for row in completed[0]["characters"]] == ["顾宁"]
        assert completed[1]["appearances"] == [["顾宁"]]
        assert completed[1]["merged_characters"] == [{"name": "顾宁", "is_existing": False}]
        assert completed[2]["scenes"][0]["title"] == "客厅_夜"
        assert completed[3]["appearances"] == ["客厅_夜"]
        assert completed[3]["scenes"] == [{"title": "客厅_夜", "is_existing": False}]
        assert completed[4]["props"][0]["name"] == "旧钥匙"
        assert completed[5]["appearances"] == [["旧钥匙"]]

        first_request = transport.requests[0]
        assert first_request["url"] == "https://api.openai.com/v1/chat/completions"
        assert first_request["headers"]["Authorization"] == "Bearer server-only-key"
        assert first_request["json_body"]["messages"] == [
            {"role": "system", "content": ""},
            {"role": "user", "content": "\n\n第二段\n\n第一段\n\n第二段"},
        ]
        assert transport.requests[1]["json_body"]["messages"][0]["content"] == "selected-system"
        assert "【分镜 8】" in transport.requests[1]["json_body"]["messages"][1]["content"]
        assert transport.requests[2]["headers"]["Authorization"] == "Bearer member-only-key"
        assert transport.requests[2]["json_body"]["model"] == "gpt-3.5-turbo"
        assert len(transport.responses) == 6
        assert all(response.closed for response in transport.responses)
    finally:
        system.engine.dispose()


def test_authentication_precedes_body_validation_and_missing_runtime_writes_nothing(tmp_path: Path) -> None:
    system = _build_system(tmp_path / "storyboard-assets-no-runtime.sqlite3", with_runtime=False)
    try:
        path = "/api/storyboard/extract-characters-stream"
        unauthenticated = asyncio.run(_post_without_token(system, path, b"{"))
        forbidden = _post(system, path, user="free", raw=b"{")
        unavailable = _post(system, path, raw=b"{")
        assert unauthenticated.status_code == 401
        assert forbidden.status_code == 403
        assert unavailable.status_code == 503
        assert system.business_sessions == []
    finally:
        system.engine.dispose()


async def _post_without_token(system: System, path: str, raw: bytes) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=system.app, raise_app_exceptions=False),
        base_url="http://storyboard-asset-generation.test",
    ) as client:
        return await client.post(path, content=raw, headers={"Content-Type": "application/json"})


def test_invalid_json_is_422_only_after_valid_membership(system: System) -> None:
    response = _post(system, "/api/storyboard/extract-characters-stream", raw=b"{")
    assert response.status_code == 422
    assert system.runtime.calls == []


def test_foreign_chapter_and_foreign_prompt_are_rejected_before_provider(system: System) -> None:
    foreign_chapter = _post(
        system,
        "/api/storyboard/extract-scenes-stream",
        body={"chapter_id": "chapter-peer", "model_id": "system-model"},
    )
    foreign_prompt = _post(
        system,
        "/api/storyboard/extract-props-stream",
        body={"chapter_id": "chapter-member", "model_id": "system-model", "prompt_id": "peer-prompt"},
    )
    assert foreign_chapter.status_code == 403
    assert foreign_prompt.status_code == 400
    assert system.runtime.calls == []


def test_two_aliases_for_one_existing_character_are_accumulated_in_one_sql_commit(system: System) -> None:
    session_before = len(system.business_sessions)
    response = _post(
        system,
        "/api/storyboard/judge-character-appearance-stream",
        body={
            "chapter_id": "chapter-member",
            "model_id": "system-model",
            "characters": [
                {"name": "沈夜棠_青年_工服围裙甲"},
                {"name": "沈夜棠_青年_工服围裙乙"},
            ],
            "storyboard_texts": ["女主走进画面"],
        },
    )
    assert response.status_code == 200
    session = system.business_sessions[session_before]
    assert session.commit_count == 1
    with system.engine.connect() as connection:
        aliases = connection.execute(
            select(characters.c.aliases).where(characters.c.id == "existing-character")
        ).scalar_one()
    assert json.loads(aliases) == ["沈夜棠_青年_工服围裙甲", "沈夜棠_青年_工服围裙乙"]
    assert system.runtime.calls[-1].operation == "judge_character"


def test_ciphertext_model_is_decrypted_from_explicit_auth_secret_before_runtime(system: System) -> None:
    encrypted = LegacyFernet(SECRET).encrypt("encrypted-server-key")
    with system.engine.begin() as connection:
        connection.execute(update(system_models).where(system_models.c.id == "system-model").values(api_key=encrypted))
    response = _post(
        system,
        "/api/storyboard/extract-props-stream",
        body={"chapter_id": "chapter-member", "model_id": "system-model"},
    )
    assert response.status_code == 200
    assert system.runtime.calls[-1].model.api_key == "encrypted-server-key"


def test_invalid_ciphertext_fails_before_runtime_and_asset_merge(system: System) -> None:
    encrypted = LegacyFernet("different-synthetic-key").encrypt("must-not-leak")
    with system.engine.begin() as connection:
        connection.execute(update(system_models).where(system_models.c.id == "system-model").values(api_key=encrypted))
    response = _post(
        system,
        "/api/storyboard/judge-character-appearance-stream",
        body={
            "chapter_id": "chapter-member",
            "model_id": "system-model",
            "characters": [{"name": "沈夜棠_青年_工服围裙甲"}],
            "storyboard_texts": ["剧本"],
        },
    )
    assert response.status_code == 503
    assert system.runtime.calls == []
    assert "must-not-leak" not in response.text



def test_real_sql_uses_legacy_character_fuzzy_threshold_but_strict_scene_and_prop_thresholds(system: System) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with system.engine.begin() as connection:
        connection.execute(
            insert(characters).values(
                id="fuzzy-character",
                series_id="series-member",
                name="甲乙丙丁",
                aliases=None,
                canonical_key=None,
                gender="",
                age="",
                role="",
                appearance="",
                description=None,
                image_url=None,
                audio_url=None,
                voice_ref=None,
                created_at=now,
                updated_at=now,
            )
        )
        connection.execute(
            insert(scenes).values(
                id="fuzzy-scene",
                series_id="series-member",
                title="甲乙丙丁",
                description="旧场景",
                image_url=None,
                aliases=None,
                canonical_key=None,
                created_at=now,
                updated_at=now,
            )
        )
        connection.execute(
            insert(props).values(
                id="fuzzy-prop",
                series_id="series-member",
                name="甲乙丙丁",
                description="旧道具",
                image_url=None,
                aliases=None,
                canonical_key=None,
                created_at=now,
                updated_at=now,
            )
        )

    character_response = _post(
        system,
        "/api/storyboard/judge-character-appearance-stream",
        body={
            "chapter_id": "chapter-member",
            "model_id": "system-model",
            "characters": [{"name": "甲乙丙戊"}],
            "storyboard_texts": ["新角色名称与旧名称相似但不相同"],
        },
    )
    scene_response = _post(
        system,
        "/api/storyboard/judge-scene-appearance-stream",
        body={
            "chapter_id": "chapter-member",
            "model_id": "system-model",
            "scenes": [{"title": "甲乙丙戊"}],
            "storyboard_texts": ["新场景名称与旧名称相似但不相同"],
        },
    )
    prop_response = _post(
        system,
        "/api/storyboard/judge-prop-appearance-stream",
        body={
            "chapter_id": "chapter-member",
            "model_id": "system-model",
            "props": [{"name": "甲乙丙戊"}],
            "storyboard_texts": ["新道具名称与旧名称相似但不相同"],
        },
    )

    assert character_response.status_code == 200, character_response.text
    assert scene_response.status_code == 200, scene_response.text
    assert prop_response.status_code == 200, prop_response.text
    character_rows = next(
        event["merged_characters"]
        for event in _events(character_response)
        if event.get("type") == "merged"
    )
    scene_rows = next(
        event["merged_scenes"]
        for event in _events(scene_response)
        if event.get("type") == "merged"
    )
    prop_rows = next(
        event["merged_props"]
        for event in _events(prop_response)
        if event.get("type") == "merged"
    )
    assert character_rows[0]["existing_id"] == "fuzzy-character"
    assert character_rows[0]["is_existing"] is True
    with system.engine.connect() as connection:
        character_key, character_aliases = connection.execute(
            select(characters.c.canonical_key, characters.c.aliases).where(
                characters.c.id == "fuzzy-character"
            )
        ).one()
    assert character_key == naming.make_key("character", "甲乙丙丁")
    assert json.loads(character_aliases) == ["甲乙丙戊"]
    assert scene_rows[0]["title"] == "甲乙丙戊"
    assert scene_rows[0]["existing_id"] is None
    assert scene_rows[0]["is_existing"] is False
    assert any(row.get("existing_id") == "fuzzy-scene" for row in scene_rows)
    assert prop_rows[0]["name"] == "甲乙丙戊"
    assert prop_rows[0]["existing_id"] is None
    assert prop_rows[0]["is_existing"] is False
    assert any(row.get("existing_id") == "fuzzy-prop" for row in prop_rows)


def test_real_sql_falls_back_to_generated_canonical_key_for_legacy_scene_rows(system: System) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with system.engine.begin() as connection:
        connection.execute(
            insert(scenes).values(
                id="legacy-empty-key-scene",
                series_id="series-member",
                title="盐宴厅_夜",
                description="旧场景",
                image_url=None,
                aliases=None,
                canonical_key=None,
                created_at=now,
                updated_at=now,
            )
        )

    response = _post(
        system,
        "/api/storyboard/judge-scene-appearance-stream",
        body={
            "chapter_id": "chapter-member",
            "model_id": "system-model",
            "scenes": [{"title": "盐宴厅·夜晚"}],
            "storyboard_texts": ["夜宴厅内"],
        },
    )
    assert response.status_code == 200, response.text
    merged_rows = next(
        event["merged_scenes"]
        for event in _events(response)
        if event.get("type") == "merged"
    )
    assert merged_rows[0]["existing_id"] == "legacy-empty-key-scene"
    assert merged_rows[0]["is_existing"] is True
    with system.engine.connect() as connection:
        canonical_key = connection.execute(
            select(scenes.c.canonical_key).where(scenes.c.id == "legacy-empty-key-scene")
        ).scalar_one()
        aliases = connection.execute(
            select(scenes.c.aliases).where(scenes.c.id == "legacy-empty-key-scene")
        ).scalar_one()
    assert canonical_key == naming.make_key("scene", "盐宴厅_夜")
    assert json.loads(aliases) == ["盐宴厅·夜晚"]


def test_trusted_actor_real_sql_without_cipher_secret_accepts_plain_and_rejects_ciphertext(
    tmp_path: Path,
) -> None:
    # This isolates model-key decryption with a trusted actor; the default JWT
    # application cannot demonstrate a missing cipher secret because auth needs one.
    system = _build_system(
        tmp_path / "storyboard-assets-without-cipher-secret.sqlite3",
        cipher_secret_key=None,
    )
    try:
        maker = sessionmaker(
            system.engine,
            class_=ObservedSession,
            expire_on_commit=False,
            future=True,
        )

        def make_uow() -> SqlAlchemyStoryboardAssetUnitOfWork:
            session = maker()
            system.business_sessions.append(session)
            return SqlAlchemyStoryboardAssetUnitOfWork(session)

        actor = TrustedActor("member")
        request = ExtractRequest(
            chapter_id="chapter-member",
            model_id="system-model",
            prompt_id=None,
            enable_thinking=False,
            frame_indices=None,
        )
        prepared = prepare_extract(make_uow, actor, kind="prop", request=request)
        assert prepared.model.api_key == "server-only-key"
        assert system.business_sessions[-1].close_count == 1
        asyncio.run(_consume_prepared(system.runtime, prepared))
        assert system.runtime.calls[-1].model.api_key == "server-only-key"

        encrypted = LegacyFernet(SECRET).encrypt("must-not-be-decrypted")
        with system.engine.begin() as connection:
            connection.execute(
                update(system_models)
                .where(system_models.c.id == "system-model")
                .values(api_key=encrypted)
            )
        calls_before = len(system.runtime.calls)
        with pytest.raises(StoryboardAssetUnavailable):
            prepare_extract(make_uow, actor, kind="prop", request=request)
        assert len(system.runtime.calls) == calls_before
        failed_session = system.business_sessions[-1]
        assert failed_session.close_count == 1
        assert failed_session.commit_count == 0
    finally:
        system.engine.dispose()


async def _consume_prepared(runtime: LocalAssetRuntime, prepared: Any) -> None:
    async for _event in stream_asset_events(runtime, prepared):
        pass


EXTRACT_ROUTES = [
    ("/api/storyboard/extract-characters-stream", "extract_character"),
    ("/api/storyboard/extract-scenes-stream", "extract_scene"),
    ("/api/storyboard/extract-props-stream", "extract_prop"),
]


@pytest.mark.parametrize(("path", "operation"), EXTRACT_ROUTES)
@pytest.mark.parametrize(
    ("thinking", "indices", "expected_thinking", "expected_content"),
    [
        ("false", ["1"], False, "第二段"),
        (0, None, False, "第一段\n\n第二段"),
        ("true", [0], True, "第一段"),
    ],
)
def test_extract_http_uses_legacy_pydantic_bool_and_integer_coercion(
    system: System,
    path: str,
    operation: str,
    thinking: Any,
    indices: list[Any] | None,
    expected_thinking: bool,
    expected_content: str,
) -> None:
    response = _post(
        system,
        path,
        body={
            "chapter_id": "chapter-member",
            "model_id": "system-model",
            "enable_thinking": thinking,
            "frame_indices": indices,
        },
    )
    assert response.status_code == 200, response.text
    call = system.runtime.calls[-1]
    assert call.operation == operation
    assert call.enable_thinking is expected_thinking
    assert call.raw_content == expected_content


@pytest.mark.parametrize("path", [path for path, _operation in EXTRACT_ROUTES])
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("frame_indices", ["1", "bad"]),
        ("enable_thinking", "not-a-boolean"),
        ("enable_thinking", None),
    ],
)
def test_extract_http_rejects_invalid_legacy_boolean_or_integer_list_before_provider(
    system: System,
    path: str,
    field: str,
    value: Any,
) -> None:
    calls_before = len(system.runtime.calls)
    response = _post(
        system,
        path,
        body={
            "chapter_id": "chapter-member",
            "model_id": "system-model",
            "enable_thinking": False,
            "frame_indices": [0],
            field: value,
        },
    )
    assert response.status_code == 422
    assert len(system.runtime.calls) == calls_before
    assert all(session.commit_count == 0 for session in system.business_sessions)


def test_real_jwt_sqlite_keeps_whitespace_character_outside_raw_fuzzy_threshold(system: System) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with system.engine.begin() as connection:
        connection.execute(
            insert(characters).values(
                id="raw-fuzzy-character",
                series_id="series-member",
                name="甲乙丙丁",
                aliases=None,
                canonical_key=None,
                gender="",
                age="",
                role="",
                appearance="",
                description=None,
                image_url=None,
                audio_url=None,
                voice_ref=None,
                created_at=now,
                updated_at=now,
            )
        )
    response = _post(
        system,
        "/api/storyboard/judge-character-appearance-stream",
        body={
            "chapter_id": "chapter-member",
            "model_id": "system-model",
            "characters": [{"name": " 甲乙丙戊 "}],
            "storyboard_texts": ["新角色名称"],
        },
    )
    assert response.status_code == 200, response.text
    merged_rows = next(
        event["merged_characters"]
        for event in _events(response)
        if event.get("type") == "merged"
    )
    assert merged_rows[0]["name"] == " 甲乙丙戊 "
    assert merged_rows[0]["existing_id"] is None
    assert merged_rows[0]["is_existing"] is False
    session = system.business_sessions[-1]
    assert session.commit_count == 0
    with system.engine.connect() as connection:
        aliases = connection.execute(
            select(characters.c.aliases).where(characters.c.id == "raw-fuzzy-character")
        ).scalar_one()
    assert aliases is None


def test_judge_default_and_selected_scene_prompts_use_legacy_slots(system: System) -> None:
    request_body = {
        "chapter_id": "chapter-member",
        "model_id": "system-model",
        "scenes": [{"title": "客厅_夜", "description": "暖色", "is_existing": False}],
        "storyboard_texts": ["客厅内"],
        "skip_merge": True,
    }
    default_response = _post(
        system,
        "/api/storyboard/judge-scene-appearance-stream",
        body=request_body,
    )
    assert default_response.status_code == 200, default_response.text
    default_call = system.runtime.calls[-1]
    assert default_call.operation == "judge_scene"
    assert "同一个场景" in default_call.system_prompt
    assert "同一种物品" not in default_call.system_prompt
    assert "{scenes_list}" not in default_call.user_prompt
    assert default_call.user_prompt.count("客厅_夜") == 1

    with system.engine.begin() as connection:
        connection.execute(
            update(prompt_configs)
            .where(prompt_configs.c.id == "owned-prompt")
            .values(user_prompt="selected scenes:\n{scenes_list}\n{storyboard_texts}")
        )
    selected_response = _post(
        system,
        "/api/storyboard/judge-scene-appearance-stream",
        body={**request_body, "prompt_id": "owned-prompt", "frame_numbers": [4]},
    )
    assert selected_response.status_code == 200, selected_response.text
    selected_call = system.runtime.calls[-1]
    assert selected_call.system_prompt == "selected-system"
    assert "{scenes_list}" not in selected_call.user_prompt
    assert "- 客厅_夜：暖色" in selected_call.user_prompt
    assert "【分镜 4】\n客厅内" in selected_call.user_prompt

    prop_response = _post(
        system,
        "/api/storyboard/judge-prop-appearance-stream",
        body={
            "chapter_id": "chapter-member",
            "model_id": "system-model",
            "props": [{"name": "盐签", "description": "竹制", "is_existing": False}],
            "storyboard_texts": ["手中握着盐签"],
            "skip_merge": True,
        },
    )
    assert prop_response.status_code == 200, prop_response.text
    prop_call = system.runtime.calls[-1]
    assert prop_call.operation == "judge_prop"
    assert "若与某条已有道具其实是同一种物品" in prop_call.user_prompt
    assert "若与某条已有道具其实是同一个道具" not in prop_call.user_prompt
