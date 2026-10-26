"""Opt-in chat runtime host wiring with a synthetic SQLite database."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import insert, select, update

from backend.server.application import create_server_host
from backend.server.configuration import load_server_configuration
from haoai_backend.authentication.tables import user_sessions, users
from haoai_backend.authentication.tokens import JoseTokenCodec
from haoai_backend.chat_data.tables import chat_messages
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    CreditLogRow,
    UserCreditRow,
)
from haoai_backend.generation_core.schema import StreamFeeRecordRow
from haoai_backend.generation_catalog.domain import ModelSource
from haoai_backend.model_configs.tables import SystemModelRow
from haoai_backend.chat_text_generation import openai_runtime as text_runtime_module
from generation_core.test_admission_staging import (
    _G2_JOINT_ACTOR_ID,
    _G2_JOINT_MODEL_ID,
    _joint_database,
)


_JWT_SECRET = "synthetic-chat-text-host-secret"
_SESSION_ID = "synthetic-chat-text-host-session"
_JWT_ID = "synthetic-chat-text-host-jti"
_MESSAGE_ID = "synthetic-chat-text-host-message"
_PROVIDER_KEY = "synthetic-chat-provider-key"
_FINAL_TEXT = "断开连接后仍完成"


class _ControlledResponse:
    status = 200

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.finished = asyncio.Event()

    async def iter_bytes(self):
        self.started.set()
        await asyncio.wait_for(self.release.wait(), timeout=5)
        try:
            yield (
                "data: "
                + json.dumps(
                    {"choices": [{"delta": {"content": _FINAL_TEXT}}]},
                    ensure_ascii=False,
                )
                + "\n\ndata: [DONE]\n\n"
            ).encode("utf-8")
        finally:
            self.finished.set()


class _ControlledTransport:
    def __init__(self, response: _ControlledResponse) -> None:
        self.response = response
        self.requests: list[dict[str, Any]] = []

    @asynccontextmanager
    async def open_stream(self, url, *, headers, json_body):
        self.requests.append(
            {"url": url, "headers": dict(headers), "json_body": dict(json_body)}
        )
        yield self.response


def test_server_text_chat_opt_in_stream_settles_after_sse_disconnect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _joint_database(tmp_path, model_source=ModelSource.SYSTEM)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with engine.begin() as connection:
        connection.execute(
            update(users)
            .where(users.c.id == _G2_JOINT_ACTOR_ID)
            .values(is_superuser=False)
        )
        connection.execute(
            update(SystemModelRow.__table__)
            .where(SystemModelRow.id == _G2_JOINT_MODEL_ID)
            .values(
                category="text",
                provider="deepseek",
                api_key=_PROVIDER_KEY,
                model=None,
                base_url=None,
                cost_per_call=3,
                is_active=True,
            )
        )
        connection.execute(
            insert(user_sessions).values(
                id=_SESSION_ID,
                user_id=_G2_JOINT_ACTOR_ID,
                jti=_JWT_ID,
                user_agent="synthetic chat text host test",
                ip_address="127.0.0.1",
                device_name="synthetic fixture",
                is_active=True,
                created_at=now,
                last_seen_at=now,
                revoked_at=None,
            )
        )
        connection.execute(
            insert(chat_messages).values(
                id=_MESSAGE_ID,
                chapter_id="joint-chapter-1",
                frame_index=0,
                asset_type=None,
                asset_id=None,
                chat_mode="chat",
                role="assistant",
                content="原始占位内容",
                model_name=None,
                created_at=now,
            )
        )
    engine.dispose()

    environment = {
        "ENVIRONMENT": "test",
        "DATABASE_URL": f"sqlite+pysqlite:///{tmp_path / 'g2-joint-fixture.sqlite'}",
        "SECRET_KEY": _JWT_SECRET,
    }
    disabled_configuration = load_server_configuration(environment)
    assert disabled_configuration.text_chat_enabled is False

    response = _ControlledResponse()
    transport = _ControlledTransport(response)
    transport_constructions: list[int] = []

    def make_transport(*, timeout_seconds: int):
        transport_constructions.append(timeout_seconds)
        return transport

    monkeypatch.setattr(
        text_runtime_module,
        "AiohttpStoryboardTextTransport",
        make_transport,
    )

    token_codec = JoseTokenCodec(_JWT_SECRET)
    token = token_codec.encode(
        {
            "sub": _G2_JOINT_ACTOR_ID,
            "jti": _JWT_ID,
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        }
    )
    request_body = {
        "model_id": _G2_JOINT_MODEL_ID,
        "message_id": _MESSAGE_ID,
        "messages": [{"role": "user", "content": "请继续生成"}],
        "enable_thinking": True,
    }

    async def exercise_host() -> None:
        disabled_host = create_server_host(disabled_configuration)
        try:
            assert disabled_host.app.state.server_chat_text_generation_configuration is None
            assert transport_constructions == []
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=disabled_host.app),
                base_url="http://chat-text-host.test",
            ) as client:
                disabled_response = await client.post(
                    "/api/chat/stream",
                    headers={"Authorization": f"Bearer {token}"},
                    json=request_body,
                )
            assert disabled_response.status_code == 503
            assert transport_constructions == []
        finally:
            disabled_host.close()

        enabled_environment = {**environment, "TEXT_CHAT_ENABLED": "true"}
        enabled_configuration = load_server_configuration(enabled_environment)
        assert enabled_configuration.text_chat_enabled is True
        host = create_server_host(enabled_configuration)
        try:
            runtime_configuration = (
                host.app.state.server_chat_text_generation_configuration
            )
            assert runtime_configuration is not None
            assert runtime_configuration.max_user_concurrency == 0
            assert transport_constructions == [600]

            incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
            await incoming.put(
                {
                    "type": "http.request",
                    "body": json.dumps(request_body).encode("utf-8"),
                    "more_body": False,
                }
            )
            sent: list[dict[str, Any]] = []

            async def receive() -> dict[str, Any]:
                return await incoming.get()

            async def send(message: dict[str, Any]) -> None:
                sent.append(message)

            asgi_request = asyncio.create_task(
                host.app(
                    {
                        "type": "http",
                        "asgi": {"version": "3.0", "spec_version": "2.4"},
                        "http_version": "1.1",
                        "method": "POST",
                        "scheme": "http",
                        "path": "/api/chat/stream",
                        "raw_path": b"/api/chat/stream",
                        "query_string": b"",
                        "headers": [
                            (b"authorization", f"Bearer {token}".encode()),
                            (b"content-type", b"application/json"),
                        ],
                        "server": ("chat-text-host.test", 80),
                        "client": ("127.0.0.1", 12345),
                        "root_path": "",
                    },
                    receive,
                    send,
                )
            )
            await asyncio.wait_for(response.started.wait(), timeout=5)
            await incoming.put({"type": "http.disconnect"})
            await asyncio.wait_for(asgi_request, timeout=5)
            assert not response.finished.is_set()
            assert any(
                message.get("type") == "http.response.start"
                and message.get("status") == 200
                for message in sent
            )

            response.release.set()
            await asyncio.wait_for(
                host.app.state.chat_text_generation_execution.wait_for_idle(),
                timeout=5,
            )
            assert response.finished.is_set()
            assert transport.requests == [
                {
                    "url": "https://api.deepseek.com/chat/completions",
                    "headers": {
                        "Authorization": f"Bearer {_PROVIDER_KEY}",
                        "Content-Type": "application/json",
                        "Accept": "text/event-stream",
                    },
                    "json_body": {
                        "model": "gpt-3.5-turbo",
                        "messages": request_body["messages"],
                        "temperature": 0.7,
                        "stream": True,
                        "thinking": {"type": "enabled"},
                        "reasoning_effort": "high",
                    },
                }
            ]

            with host.app.state.server_engine.connect() as connection:
                task = connection.execute(
                    select(AITaskRow.__table__)
                ).mappings().one()
                proof = connection.execute(
                    select(StreamFeeRecordRow.__table__)
                ).mappings().one()
                message = connection.execute(
                    select(chat_messages.c.content).where(
                        chat_messages.c.id == _MESSAGE_ID
                    )
                ).scalar_one()
                credits = connection.execute(
                    select(UserCreditRow.__table__.c.credits)
                ).scalar_one()
                credit_logs = connection.execute(
                    select(CreditLogRow.__table__)
                ).mappings().all()

            assert task["status"] == "completed"
            assert task["result"] == message == _FINAL_TEXT
            assert proof["settlement_state"] == "success_retained"
            assert credits == 497
            assert len(credit_logs) == 1
            assert credit_logs[0]["type"] == "usage"
        finally:
            response.release.set()
            host.close()

    asyncio.run(exercise_host())
