"""Real JWT/SQLite composition tests for the four storyboard-generation routes."""

from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, insert, select, update
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.admin_tasks.tables import ai_tasks, metadata as task_metadata
from haoai_backend.app import create_app
from haoai_backend.admin_system_models.crypto import LegacyFernet
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.tables import (
    metadata as auth_metadata,
    user_credits,
    user_sessions,
    users,
)
from haoai_backend.authentication.tokens import JoseTokenCodec
from haoai_backend.generation_catalog.tables import (
    metadata as catalog_metadata,
    model_configs,
    prompt_configs,
    system_models,
    system_prompts,
)
from haoai_backend.personal_production.notes.tables import (
    metadata as notes_metadata,
    personal_production_notes,
    storyboard_media_states,
)
from haoai_backend.series_data.tables import (
    chapters,
    metadata as series_metadata,
    series,
    storyboard_assets,
)
from haoai_backend.teams.tables import metadata as teams_metadata, team_members, teams
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.storyboard_generation.domain import StoryboardRuntimeCall, StoryboardRuntimeResult
from haoai_backend.storyboard_generation.errors import StoryboardProviderFailure
from haoai_backend.storyboard_generation.persistence import SqlAlchemyStoryboardGenerationUnitOfWork
from haoai_backend.storyboard_generation.ports import StoryboardGenerationRuntime


SECRET = "local-storyboard-integration-secret"
STORYBOARD_ROUTES = {
    ("POST", "/api/storyboard/generate", "generate_storyboard"),
    ("POST", "/api/storyboard/generate-stream", "generate_storyboard_stream"),
    ("POST", "/api/storyboard/create-chapter-with-storyboard", "create_chapter_with_storyboard"),
    ("POST", "/api/storyboard/two-pass", "two_pass_storyboard"),
    ("POST", "/api/storyboard/two-pass/quote", "quote_two_pass_storyboard"),
    ("POST", "/api/storyboard/two-pass/stream", "two_pass_storyboard_stream"),
}
MARKED = "_::~OUTPUT_START::~_保留完整镜头文本。_::~OUTPUT_END::~_"


class ObservedSession(Session):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.close_count = 0

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        self.info["events"].append((f"{self.info['kind']}-execute", threading.get_ident()))
        return super().execute(*args, **kwargs)

    def commit(self) -> None:
        super().commit()
        fault = self.info.get("commit_fault")
        if self.info.get("kind") == "business" and fault and fault["after_commit"]:
            fault["after_commit"] = False
            raise RuntimeError("commit acknowledgement lost")

    def close(self) -> None:
        self.close_count += 1
        self.info["events"].append((f"{self.info['kind']}-close", threading.get_ident()))
        super().close()


class LocalRuntime:
    def __init__(self) -> None:
        self.calls: list[StoryboardRuntimeCall] = []
        self.business_sessions: list[ObservedSession] = []
        self.stream_chunks: dict[str, list[str]] = {}
        self.stream_failures: dict[str, str] = {}

    def _assert_sessions_closed(self) -> None:
        assert all(session.close_count == 1 for session in self.business_sessions)

    async def generate(self, call: StoryboardRuntimeCall) -> StoryboardRuntimeResult:
        self._assert_sessions_closed()
        self.calls.append(call)
        return StoryboardRuntimeResult(MARKED)

    async def stream(self, call: StoryboardRuntimeCall):
        self._assert_sessions_closed()
        self.calls.append(call)
        if call.operation in self.stream_failures:
            raise StoryboardProviderFailure(self.stream_failures[call.operation])
        if call.operation in self.stream_chunks:
            for chunk in self.stream_chunks[call.operation]:
                yield chunk
            return
        if call.operation == "scene_segmentation":
            yield "第一段_::~RECORD::~_第二段"
        elif call.operation == "frame_generation":
            yield "画面 第一段_::~RE"
            yield "CORD::~_画面 第二段"
        else:
            yield MARKED


@dataclass
class System:
    engine: Any
    app: FastAPI
    runtime: LocalRuntime
    business_sessions: list[ObservedSession]
    auth_sessions: list[ObservedSession]
    events: list[tuple[str, int]]
    commit_fault: dict[str, bool]
    token_for: Callable[[str], str]


def _build_system(path: Path, *, with_runtime: bool = True) -> System:
    engine = create_engine(f"sqlite:///{path}", future=True)
    auth_metadata.create_all(engine)
    teams_metadata.create_all(engine, tables=[teams, team_members])
    series_metadata.create_all(engine, tables=[series, chapters, storyboard_assets])
    notes_metadata.create_all(
        engine,
        tables=[storyboard_media_states, personal_production_notes],
    )
    catalog_metadata.create_all(
        engine,
        tables=[model_configs, system_models, prompt_configs, system_prompts],
    )
    task_metadata.create_all(engine, tables=[ai_tasks])

    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    with engine.begin() as connection:
        connection.execute(
            insert(users),
            [
                {
                    "id": user_id,
                    "username": user_id,
                    "email": f"{user_id}@example.test",
                    "hashed_password": "synthetic-test-hash",
                    "is_superuser": False,
                    "membership_type": membership,
                    "membership_expires_at": None,
                    "avatar_url": None,
                    "bio": None,
                    "created_at": now,
                    "password_updated_at": now,
                }
                for user_id, membership in (
                    ("member", "premium"),
                    ("peer", "premium"),
                    ("free", "free"),
                )
            ],
        )
        connection.execute(
            insert(user_sessions),
            [
                {
                    "id": f"session-{user_id}",
                    "user_id": user_id,
                    "jti": f"jti-{user_id}",
                    "user_agent": "integration",
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
            insert(user_credits).values(
                id="credit-member", user_id="member", credits=50,
                created_at=now, updated_at=now,
            )
        )
        connection.execute(
            insert(series),
            [
                {
                    "id": "series-member", "user_id": "member", "name": "Member series",
                    "description": None, "image_url": None, "style_prompt_id": None,
                    "team_id": None, "claimed_by": None, "claimed_at": None,
                    "created_at": now, "updated_at": now,
                },
                {
                    "id": "series-peer", "user_id": "peer", "name": "Peer series",
                    "description": None, "image_url": None, "style_prompt_id": None,
                    "team_id": None, "claimed_by": None, "claimed_at": None,
                    "created_at": now, "updated_at": now,
                },
            ],
        )
        connection.execute(
            insert(system_models).values(
                id="system-model", category="other", name="System model",
                provider="local", api_key="server-system-key", model=None,
                base_url=None, cost_per_call=7, description=None, params=None,
                is_active=True, sort_order=0, created_at=now, updated_at=now,
            )
        )
        connection.execute(
            insert(model_configs).values(
                id="owned-model", user_id="member", category="other",
                name="Owned model", provider="local", api_key="member-key",
                model="", base_url=None, params=None, sort_order=0,
                created_at=now, updated_at=now,
            )
        )
        connection.execute(
            insert(model_configs).values(
                id="peer-model", user_id="peer", category="other",
                name="Peer model", provider="local", api_key="peer-key",
                model="should-not-load", base_url=None, params=None, sort_order=0,
                created_at=now, updated_at=now,
            )
        )
        connection.execute(
            insert(prompt_configs).values(
                id="owned-prompt", user_id="member", category="",
                name="", system_prompt="本地系统模板", user_prompt="本地用户模板",
                sort_order=0, created_at=now, updated_at=now,
            )
        )
        connection.execute(
            insert(prompt_configs).values(
                id="peer-prompt", user_id="peer", category="storyboard",
                name="peer", system_prompt="foreign", user_prompt="foreign",
                sort_order=0, created_at=now, updated_at=now,
            )
        )
        connection.execute(
            insert(system_prompts).values(
                id="active-prompt", category="", name="", system_prompt="系统模板",
                user_prompt="系统用户模板", is_active=True, sort_order=0,
                created_at=now, updated_at=now,
            )
        )
        connection.execute(
            insert(ai_tasks),
            [
                {
                    "id": task_id,
                    "user_id": user_id,
                    "type": "storyboard",
                    "message_id": f"message-{task_id}",
                    "status": "pending",
                    "credit_cost": 0,
                    "result": None,
                    "request_data": None,
                    "model_name": None,
                    "progress": 0,
                    "progress_message": None,
                    "external_task_id": None,
                    "external_provider": None,
                    "claimed_by": None,
                    "lease_until": None,
                    "execution_generation": 0,
                    "claim_token": None,
                    "recovery_status": "ready",
                    "billing_status": "no_charge",
                    "user_cancelled_at": None,
                    "cancellation_reason": None,
                    "created_at": now,
                    "updated_at": now,
                }
                for task_id, user_id in (("task-member", "member"), ("task-peer", "peer"))
            ],
        )

    events: list[tuple[str, int]] = []
    commit_fault = {"after_commit": False}
    auth_sessions: list[ObservedSession] = []
    business_sessions: list[ObservedSession] = []

    def make_factory(kind: str, opened: list[ObservedSession]):
        maker = sessionmaker(
            engine,
            class_=ObservedSession,
            expire_on_commit=False,
            future=True,
            info={"kind": kind, "events": events, "commit_fault": commit_fault},
        )

        def open_session() -> ObservedSession:
            events.append((f"{kind}-open", threading.get_ident()))
            session = maker()
            opened.append(session)
            return session

        return open_session

    auth_factory = make_factory("auth", auth_sessions)
    business_factory = make_factory("business", business_sessions)
    runtime = LocalRuntime()
    runtime.business_sessions = business_sessions
    auth_runtime = AuthenticationRuntime(
        session_factory=auth_factory,
        secret_key=SECRET,
        clock=lambda: now,
    )
    app = create_app(
        session_factory=business_factory,
        authentication_runtime=auth_runtime,
        storyboard_generation_runtime=runtime if with_runtime else None,
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

    return System(engine, app, runtime, business_sessions, auth_sessions, events, commit_fault, token_for)


@pytest.fixture
def system(tmp_path: Path):
    value = _build_system(tmp_path / "storyboard-generation.sqlite3")
    try:
        yield value
    finally:
        value.engine.dispose()


def _post(system: System, path: str, *, user: str = "member", body: Any = None, raw: bytes | None = None):
    async def send():
        headers = {"Authorization": f"Bearer {system.token_for(user)}"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=system.app, raise_app_exceptions=False),
            base_url="http://storyboard-generation.test",
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


def test_real_jwt_http_flows_parse_stream_and_persist_text_and_assets(system: System) -> None:
    assert STORYBOARD_ROUTES <= {
        (method, route.path, route.name)
        for route in system.app.routes
        for method in route.methods
        if method not in {"HEAD", "OPTIONS"}
    }
    generated = _post(
        system,
        "/api/storyboard/generate",
        body={
            "raw_content": " 原始剧本 ",
            "model_id": "system-model",
            "prompt_id": "owned-prompt",
        },
    )
    assert generated.status_code == 200
    assert generated.json()["frames"] == [
        {"text": "保留完整镜头文本。", "character": "", "scene": "", "prop": ""}
    ]
    assert system.runtime.calls[-1].model.model is None
    assert system.runtime.calls[-1].model.api_key == "server-system-key"

    streamed = _post(
        system,
        "/api/storyboard/generate-stream",
        body={"raw_content": "原始剧本", "model_id": "owned-model", "task_id": "task-member"},
    )
    events = _events(streamed)
    assert streamed.status_code == 200
    assert [event["type"] for event in events][0] == "start"
    assert "payload" in {event["type"] for event in events}
    assert events[-1]["type"] == "complete"
    assert events[-1]["frames"][0]["text"] == "保留完整镜头文本。"

    created = _post(
        system,
        "/api/storyboard/create-chapter-with-storyboard",
        body={
            "series_id": "series-member",
            "chapter_title": "新章节",
            "raw_content": "原始剧本",
            "model_id": "owned-model",
            "prompt_id": "active-prompt",
            "order": 4,
        },
    )
    assert created.status_code == 201
    response_frame = created.json()["frames"][0]
    assert response_frame["original_text"] == "保留完整镜头文本。"
    assert len(response_frame["storyboard"]) == 1

    async_response = _post(
        system,
        "/api/storyboard/two-pass",
        body={
            "raw_content": "原始剧本",
            "model_id": "system-model",
            "skip_frame_generation": True,
        },
    )
    two_pass_events = _events(async_response)
    assert async_response.status_code == 200
    assert two_pass_events[-1]["type"] == "complete"
    assert two_pass_events[-1]["segments"] == [
        {"index": 0, "text": "第一段"},
        {"index": 1, "text": "第二段"},
    ]
    assert two_pass_events[-1]["frames"] == [
        {"text": "第一段", "duration": 5},
        {"text": "第二段", "duration": 5},
    ]
    assert not any(call.operation == "frame_generation" for call in system.runtime.calls)

    normal_call_start = len(system.runtime.calls)
    normal_two_pass = _post(
        system,
        "/api/storyboard/two-pass",
        body={
            "raw_content": "原始剧本",
            "model_id": "owned-model",
            "scene_prompt_id": "owned-prompt",
            "frame_prompt_id": "active-prompt",
            "skip_frame_generation": False,
        },
    )
    normal_events = _events(normal_two_pass)
    assert normal_two_pass.status_code == 200
    assert normal_events[-1]["type"] == "complete"
    assert normal_events[-1]["frames"] == [
        {"text": "画面 第一段", "duration": 5},
        {"text": "画面 第二段", "duration": 5},
    ]
    assert "".join(
        event["text"] for event in normal_events if event["type"] == "step2_output"
    ) == "画面 第一段_::~RECORD::~_画面 第二段"
    normal_calls = system.runtime.calls[normal_call_start:]
    scene_call = next(call for call in normal_calls if call.operation == "scene_segmentation")
    frame_call = next(call for call in normal_calls if call.operation == "frame_generation")
    assert scene_call.user_prompt == "本地用户模板\n\n原始剧本"
    assert frame_call.system_prompt == "系统模板"
    assert frame_call.user_prompt == "系统用户模板\n\n第一段_::~RECORD::~_第二段"
    assert sum(call.operation == "frame_generation" for call in system.runtime.calls) == 1

    with system.engine.connect() as connection:
        saved_chapter = connection.execute(
            select(chapters).where(chapters.c.id == created.json()["chapter_id"])
        ).mappings().one()
        saved_assets = connection.execute(
            select(storyboard_assets).where(storyboard_assets.c.chapter_id == saved_chapter["id"])
        ).mappings().all()
        credits = connection.execute(
            select(user_credits.c.credits).where(user_credits.c.user_id == "member")
        ).scalar_one()
        task = connection.execute(
            select(ai_tasks.c.status, ai_tasks.c.result).where(ai_tasks.c.id == "task-member")
        ).mappings().one()
    assert json.loads(saved_chapter["content"])[0]["original_text"] == "保留完整镜头文本。"
    assert len(saved_assets) == 1
    assert credits == 50
    assert task["status"] == "completed"
    assert "保留完整镜头文本" in task["result"]
    assert all(session.close_count == 1 for session in system.business_sessions)
    assert all(session.close_count == 1 for session in system.auth_sessions)
    loop_thread = threading.get_ident()
    for index, (name, thread_id) in enumerate(system.events):
        if name == "business-open":
            prefix = system.events[:index]
            assert sum(event == "auth-open" for event, _ in prefix) == sum(
                event == "auth-close" for event, _ in prefix
            )
            assert thread_id != loop_thread



@pytest.mark.parametrize(
    ("frame_prompt_id", "expected_user_prompt", "expected_system_prompt"),
    [
        (None, "第一段_::~RECORD::~_第二段", ""),
        ("active-prompt", "系统用户模板\n\n第一段_::~RECORD::~_第二段", "系统模板"),
    ],
)
def test_two_pass_streams_all_frames_with_exact_prompt_and_legacy_sse(
    system: System,
    frame_prompt_id: str | None,
    expected_user_prompt: str,
    expected_system_prompt: str,
) -> None:
    response = _post(
        system,
        "/api/storyboard/two-pass",
        body={
            "raw_content": "原始剧本",
            "model_id": "owned-model",
            "frame_prompt_id": frame_prompt_id,
        },
    )
    events = _events(response)
    calls = [call for call in system.runtime.calls if call.operation == "frame_generation"]

    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0].user_prompt == expected_user_prompt
    assert calls[0].system_prompt == expected_system_prompt
    assert [event["text"] for event in events if event["type"] == "step2_output"] == [
        "画面 第一段_::~RE",
        "CORD::~_画面 第二段",
    ]
    assert not any(event["type"] == "done" for event in events)
    assert events[-1]["frames"] == [
        {"text": "画面 第一段", "duration": 5},
        {"text": "画面 第二段", "duration": 5},
    ]

def test_auth_and_scope_fail_before_runtime_or_business_writes(system: System) -> None:
    response = _post(
        system,
        "/api/storyboard/generate",
        user="member",
        raw=b"{",
    )
    assert response.status_code == 422
    unauthenticated = _post_without_token(system.app, "/api/storyboard/generate", b"{")
    assert unauthenticated.status_code == 401

    before = len(system.runtime.calls)
    foreign_model = _post(
        system,
        "/api/storyboard/generate",
        body={"raw_content": "剧本", "model_id": "peer-model"},
    )
    assert foreign_model.status_code == 404
    foreign_prompt = _post(
        system,
        "/api/storyboard/generate",
        body={
            "raw_content": "剧本",
            "model_id": "owned-model",
            "prompt_id": "peer-prompt",
        },
    )
    assert foreign_prompt.status_code == 400
    foreign_series = _post(
        system,
        "/api/storyboard/create-chapter-with-storyboard",
        body={
            "series_id": "series-peer",
            "chapter_title": "不应创建",
            "raw_content": "剧本",
            "model_id": "owned-model",
        },
    )
    assert foreign_series.status_code == 403

    invalid_two_pass_prompt = _post(
        system,
        "/api/storyboard/two-pass",
        body={
            "raw_content": "剧本",
            "model_id": "owned-model",
            "frame_prompt_id": "peer-prompt",
        },
    )
    assert invalid_two_pass_prompt.status_code == 400

    foreign_task = _post(
        system,
        "/api/storyboard/generate-stream",
        body={
            "raw_content": "剧本",
            "model_id": "owned-model",
            "task_id": "task-peer",
        },
    )
    assert foreign_task.status_code == 409
    assert len(system.runtime.calls) == before

    free_member = _post(
        system,
        "/api/storyboard/generate",
        user="free",
        raw=b"{",
    )
    assert free_member.status_code == 403


def test_missing_runtime_is_authenticated_503_before_body_and_business_session(tmp_path: Path) -> None:
    system = _build_system(tmp_path / "storyboard-no-runtime.sqlite3", with_runtime=False)
    try:
        response = _post(
            system,
            "/api/storyboard/generate",
            raw=b"{",
        )
        assert response.status_code == 503
        assert system.business_sessions == []
        unauthenticated = asyncio.run(_unauthenticated(system.app))
        assert unauthenticated.status_code == 401
        assert system.business_sessions == []
    finally:
        system.engine.dispose()


async def _unauthenticated(app: FastAPI) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://storyboard-generation.test",
    ) as client:
        return await client.post("/api/storyboard/generate", content=b"{")


def _post_without_token(app: FastAPI, path: str, raw: bytes):
    async def send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://storyboard-generation.test",
        ) as client:
            return await client.post(
                path,
                content=raw,
                headers={"Content-Type": "application/json"},
            )
    return asyncio.run(send())


def test_chapter_commit_ack_loss_is_not_replayed(system: System) -> None:
    system.commit_fault["after_commit"] = True
    before = len(system.runtime.calls)
    response = _post(
        system,
        "/api/storyboard/create-chapter-with-storyboard",
        body={
            "series_id": "series-member",
            "chapter_title": "一次提交",
            "raw_content": "原始剧本",
            "model_id": "owned-model",
        },
    )
    assert response.status_code == 500
    assert len(system.runtime.calls) == before + 1
    with system.engine.connect() as connection:
        chapters_saved = connection.execute(
            select(chapters.c.id).where(chapters.c.title == "一次提交")
        ).all()
    assert len(chapters_saved) == 1


@pytest.mark.parametrize(
    ("chunks", "expected"),
    [
        (["abab", "!"], "[已隐藏]!"),
        (["ab", "ab", "!"], "[已隐藏]!"),
        (["普通", "文本"], "普通文本"),
        (["尾缀aba"], "尾缀aba"),
    ],
)
def test_real_sse_redacts_cross_chunk_secret_and_persists_safe_text(
    system: System,
    chunks: list[str],
    expected: str,
) -> None:
    with system.engine.begin() as connection:
        connection.execute(
            update(system_models)
            .where(system_models.c.id == "system-model")
            .values(api_key="abab")
        )
    system.runtime.stream_chunks["storyboard"] = chunks
    response = _post(
        system,
        "/api/storyboard/generate-stream",
        body={
            "raw_content": "剧本",
            "model_id": "system-model",
            "task_id": "task-member",
        },
    )
    events = _events(response)
    content = [event for event in events if event["type"] == "content"]
    with system.engine.connect() as connection:
        task = connection.execute(
            select(ai_tasks.c.status, ai_tasks.c.result).where(
                ai_tasks.c.id == "task-member"
            )
        ).mappings().one()
    result = json.loads(task["result"])

    assert response.status_code == 200
    assert "".join(event["text"] for event in content) == expected
    assert content[-1]["full_text"] == expected
    assert events[-1]["raw_response"] == expected
    assert task["status"] == "completed"
    assert result["raw_response"] == expected
    assert "abab" not in response.text
    assert "abab" not in task["result"]


@pytest.mark.parametrize(
    "chunks",
    [["abab", "!"], ["ab", "ab", "!"]],
)
def test_real_two_pass_step_two_sse_redacts_secret_in_events_frames_and_task(
    system: System,
    chunks: list[str],
) -> None:
    with system.engine.begin() as connection:
        connection.execute(
            update(system_models)
            .where(system_models.c.id == "system-model")
            .values(api_key="abab")
        )
    system.runtime.stream_chunks["frame_generation"] = chunks
    response = _post(
        system,
        "/api/storyboard/two-pass",
        body={
            "raw_content": "剧本",
            "model_id": "system-model",
            "task_id": "task-member",
        },
    )
    events = _events(response)
    step_two = [event for event in events if event["type"] == "step2_output"]
    with system.engine.connect() as connection:
        task = connection.execute(
            select(ai_tasks.c.status, ai_tasks.c.result).where(
                ai_tasks.c.id == "task-member"
            )
        ).mappings().one()

    assert response.status_code == 200
    assert "".join(event["text"] for event in step_two) == "[已隐藏]!"
    assert events[-1]["frames"][0]["text"] == "[已隐藏]!"
    assert task["status"] == "completed"
    assert "abab" not in response.text
    assert "abab" not in task["result"]


@pytest.mark.parametrize("stage", ["prepare", "write"])
def test_native_cancellation_drains_real_sql_worker(
    system: System,
    monkeypatch: pytest.MonkeyPatch,
    stage: Literal["prepare", "write"],
) -> None:
    started = threading.Event()
    release = threading.Event()
    worker_threads: list[int] = []
    original_resolve = SqlAlchemyStoryboardGenerationUnitOfWork.resolve_model
    original_create = SqlAlchemyStoryboardGenerationUnitOfWork.create_chapter

    def gate_after_prepare(
        self: SqlAlchemyStoryboardGenerationUnitOfWork,
        actor: Any,
        model_id: str,
    ) -> Any:
        result = original_resolve(self, actor, model_id)
        worker_threads.append(threading.get_ident())
        started.set()
        if not release.wait(2):
            raise TimeoutError("prepare gate timeout")
        return result

    def gate_after_write(
        self: SqlAlchemyStoryboardGenerationUnitOfWork,
        actor: Any,
        **kwargs: Any,
    ) -> Any:
        result = original_create(self, actor, **kwargs)
        worker_threads.append(threading.get_ident())
        started.set()
        if not release.wait(2):
            raise TimeoutError("write gate timeout")
        return result

    if stage == "prepare":
        monkeypatch.setattr(
            SqlAlchemyStoryboardGenerationUnitOfWork,
            "resolve_model",
            gate_after_prepare,
        )
        path = "/api/storyboard/generate"
        body = {"raw_content": "剧本", "model_id": "owned-model"}
    else:
        monkeypatch.setattr(
            SqlAlchemyStoryboardGenerationUnitOfWork,
            "create_chapter",
            gate_after_write,
        )
        path = "/api/storyboard/create-chapter-with-storyboard"
        body = {
            "series_id": "series-member",
            "chapter_title": "取消期间写入",
            "raw_content": "剧本",
            "model_id": "owned-model",
        }

    async def scenario() -> tuple[bool, bool, int | None, int | None, bool]:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=system.app,
                raise_app_exceptions=False,
            ),
            base_url="http://storyboard-generation.test",
        ) as client:
            request_task = asyncio.create_task(
                client.post(
                    path,
                    headers={
                        "Authorization": f"Bearer {system.token_for('member')}"
                    },
                    json=body,
                )
            )
            session: ObservedSession | None = None
            reached_gate = False
            done_before_release = True
            close_count_before_release: int | None = None
            close_count_after_release: int | None = None
            cancelled_after_close = False
            request_error: Exception | None = None
            loop = asyncio.get_running_loop()
            try:
                reached_gate = await asyncio.to_thread(started.wait, 1.5)
                if reached_gate:
                    session = system.business_sessions[-1]
                    request_task.cancel()
                    await asyncio.sleep(0.03)
                    request_task.cancel()
                    await asyncio.sleep(0.03)
                    done_before_release = request_task.done()
                    close_count_before_release = session.close_count
            finally:
                # Always unblock the owned worker before draining the request task.
                release.set()
                if not request_task.done():
                    request_task.cancel()
                try:
                    await asyncio.wait_for(asyncio.shield(request_task), 3)
                except asyncio.CancelledError:
                    cancelled_after_close = (
                        session is not None and session.close_count == 1
                    )
                except Exception as error:
                    request_error = error

                if session is not None:
                    close_deadline = loop.time() + 3
                    while (
                        session.close_count != 1
                        and loop.time() < close_deadline
                    ):
                        await asyncio.sleep(0.01)
                    close_count_after_release = session.close_count

            if request_error is not None:
                raise request_error
            return (
                reached_gate,
                done_before_release,
                close_count_before_release,
                close_count_after_release,
                cancelled_after_close,
            )

    try:
        (
            reached_gate,
            done_before,
            closes_before,
            closes_after,
            cancelled,
        ) = asyncio.run(scenario())
    finally:
        release.set()
    assert reached_gate, "SQL worker did not reach gate"
    assert not done_before and closes_before == 0 and closes_after == 1 and cancelled
    assert len(worker_threads) == 1
    assert worker_threads[0] != threading.get_ident()
    if stage == "prepare":
        assert not system.runtime.calls
    else:
        assert sum(
            call.operation == "storyboard" for call in system.runtime.calls
        ) == 1
        with system.engine.connect() as connection:
            saved = connection.execute(
                select(chapters.c.id).where(chapters.c.title == "取消期间写入")
            ).all()
        assert len(saved) == 1

def test_two_pass_provider_failure_persists_failed_task_without_retry_or_complete(
    system: System,
) -> None:
    system.runtime.stream_failures["frame_generation"] = (
        "frame provider failed server-system-key"
    )
    response = _post(
        system,
        "/api/storyboard/two-pass",
        body={
            "raw_content": "剧本",
            "model_id": "system-model",
            "task_id": "task-member",
        },
    )
    events = _events(response)
    frame_calls = [
        call
        for call in system.runtime.calls
        if call.operation == "frame_generation"
    ]
    with system.engine.connect() as connection:
        task = connection.execute(
            select(ai_tasks.c.status, ai_tasks.c.result).where(
                ai_tasks.c.id == "task-member"
            )
        ).mappings().one()
    result = json.loads(task["result"])

    assert response.status_code == 200
    assert len(frame_calls) == 1
    assert events[-1] == {
        "type": "error",
        "message": "frame provider failed [已隐藏]",
    }
    assert not any(event["type"] in {"complete", "done"} for event in events)
    assert task["status"] == "failed"
    assert result == {"error": "frame provider failed [已隐藏]"}
    assert "server-system-key" not in response.text
    assert "server-system-key" not in task["result"]


def test_encrypted_server_model_uses_explicit_jwt_secret(system: System) -> None:
    plaintext_key = "synthetic-encrypted-provider-key"
    ciphertext = LegacyFernet(SECRET).encrypt(plaintext_key)
    with system.engine.begin() as connection:
        connection.execute(
            update(system_models)
            .where(system_models.c.id == "system-model")
            .values(api_key=ciphertext)
        )

    response = _post(
        system,
        "/api/storyboard/generate",
        body={"raw_content": "剧本", "model_id": "system-model"},
    )

    assert response.status_code == 200
    assert system.runtime.calls[-1].model.api_key == plaintext_key
    assert ciphertext not in response.text


def test_encrypted_server_model_with_wrong_secret_fails_before_task_or_runtime(
    system: System,
) -> None:
    ciphertext = LegacyFernet("different-local-test-secret").encrypt(
        "wrong-secret-provider-key"
    )
    with system.engine.begin() as connection:
        connection.execute(
            update(system_models)
            .where(system_models.c.id == "system-model")
            .values(api_key=ciphertext)
        )

    response = _post(
        system,
        "/api/storyboard/generate",
        body={
            "raw_content": "剧本",
            "model_id": "system-model",
            "task_id": "task-member",
        },
    )
    with system.engine.connect() as connection:
        task = connection.execute(
            select(ai_tasks.c.status, ai_tasks.c.result).where(
                ai_tasks.c.id == "task-member"
            )
        ).mappings().one()

    assert response.status_code == 503
    assert system.runtime.calls == []
    assert task["status"] == "pending" and task["result"] is None
    assert ciphertext not in response.text
    assert "wrong-secret-provider-key" not in response.text


def test_encrypted_server_model_without_cipher_secret_fails_before_task_or_runtime(
    system: System,
) -> None:
    """Use an explicit trusted actor while the app has no cipher secret."""
    ciphertext = LegacyFernet(SECRET).encrypt("synthetic-provider-key")
    with system.engine.begin() as connection:
        connection.execute(
            update(system_models)
            .where(system_models.c.id == "system-model")
            .values(api_key=ciphertext)
        )

    maker = sessionmaker(
        system.engine,
        class_=ObservedSession,
        expire_on_commit=False,
        future=True,
        info={
            "kind": "business",
            "events": system.events,
            "commit_fault": system.commit_fault,
        },
    )

    def business_factory() -> ObservedSession:
        system.events.append(("business-open", threading.get_ident()))
        session = maker()
        system.business_sessions.append(session)
        return session

    app = create_app(
        session_factory=business_factory,
        resolve_actor=lambda _request: TrustedActor("member"),
        storyboard_generation_runtime=system.runtime,
    )

    async def send() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://storyboard-generation.test",
        ) as client:
            return await client.post(
                "/api/storyboard/generate",
                json={
                    "raw_content": "剧本",
                    "model_id": "system-model",
                    "task_id": "task-member",
                },
            )

    response = asyncio.run(send())
    with system.engine.connect() as connection:
        task = connection.execute(
            select(ai_tasks.c.status, ai_tasks.c.result).where(
                ai_tasks.c.id == "task-member"
            )
        ).mappings().one()

    assert response.status_code == 503
    assert system.runtime.calls == []
    assert task["status"] == "pending" and task["result"] is None
    assert ciphertext not in response.text
    assert system.business_sessions[-1].close_count == 1
