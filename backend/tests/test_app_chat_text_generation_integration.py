"""Real JWT and SQLite coverage for chat text streaming and settlement."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import threading
from typing import Any

import httpx
import pytest
from starlette.requests import Request
from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.app import create_app
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.tables import user_sessions, users
from haoai_backend.authentication.tokens import JoseTokenCodec
from haoai_backend.admin_smtp_accounts.crypto import LegacyFernet
from haoai_backend.chat_data.tables import chapters, chat_messages
from haoai_backend.chat_text_generation import ChatTextGenerationConfiguration
from haoai_backend.chat_text_generation.ports import ChatTextRuntimeCall
from haoai_backend.generation_core.base_schema import AITaskRow, CreditLogRow, UserCreditRow
from haoai_backend.generation_core.schema import StreamFeeRecordRow
from haoai_backend.generation_catalog.domain import ModelSource
from haoai_backend.model_configs.tables import (
    ModelConfigRow,
    SystemModelRow,
    UserModelOrderRow,
)

system_models = SystemModelRow.__table__
from haoai_backend.prompts.tables import prompt_configs, system_prompts
from generation_core.test_admission_staging import (
    _G2_JOINT_ACTOR_ID,
    _G2_JOINT_MODEL_ID,
    _joint_database,
)

SECRET = "chat-text-generation-synthetic-jwt-secret"
JTI = "chat-text-generation-synthetic-jti"
AUTH_SESSION_ID = "chat-text-generation-synthetic-session"
MESSAGE_ID = "chat-text-generation-synthetic-message"
BASE_URL = "http://chat-text-generation.test"
PROVIDER_KEY = "ababab"


class _TrackedSession(Session):
    def commit(self) -> None:
        super().commit()
        state = self.info.get("commit_ack_state")
        if isinstance(state, dict) and state.get("enabled") and not state.get("used"):
            state["used"] = True
            raise RuntimeError("synthetic acknowledgement loss")

    def close(self) -> None:
        self.info["close_count"] = int(self.info.get("close_count", 0)) + 1
        if not self.info.get("closed"):
            gate = self.info.get("close_gate")
            gate_state = self.info.get("close_gate_state")
            if isinstance(gate, tuple) and isinstance(gate_state, dict) and not gate_state.get("used"):
                gate_state["used"] = True
                close_started, close_release = gate
                close_started.set()
                close_release.wait(timeout=5)
            self.info["closed"] = True
            events = self.info.get("events")
            if isinstance(events, list):
                events.append("close:business")
        super().close()


class _LocalRuntime:
    def __init__(
        self,
        events: tuple[str | Mapping[str, object], ...],
        business_sessions: list[_TrackedSession],
        *,
        before_first_event: Any = None,
        provider_gate: tuple[asyncio.Event, asyncio.Event] | None = None,
        provider_finished: asyncio.Event | None = None,
    ) -> None:
        self.events = events
        self.business_sessions = business_sessions
        self.before_first_event = before_first_event
        self.provider_gate = provider_gate
        self.provider_finished = provider_finished
        self.calls: list[ChatTextRuntimeCall] = []

    async def stream(
        self,
        call: ChatTextRuntimeCall,
    ) -> AsyncIterator[str | Mapping[str, object]]:
        self.calls.append(call)
        assert call.model.api_key == PROVIDER_KEY
        assert all(session.info.get("closed") for session in self.business_sessions)
        if self.before_first_event is not None:
            self.before_first_event()
        try:
            if self.provider_gate is not None:
                started, release = self.provider_gate
                started.set()
                await asyncio.wait_for(release.wait(), timeout=5)
            for event in self.events:
                yield event
        finally:
            if self.provider_finished is not None:
                self.provider_finished.set()


class _System:
    def __init__(
        self,
        engine: Engine,
        app: Any,
        business_sessions: list[_TrackedSession],
        business_events: list[str],
        token: str,
    ) -> None:
        self.engine = engine
        self.app = app
        self.business_sessions = business_sessions
        self.business_events = business_events
        self.token = token


def _build_system(
    tmp_path: Path,
    events: tuple[str | Mapping[str, object], ...],
    *,
    message: bool = True,
    before_first_event: Any = None,
    business_close_gate: tuple[threading.Event, threading.Event] | None = None,
    provider_gate: tuple[asyncio.Event, asyncio.Event] | None = None,
    provider_finished: asyncio.Event | None = None,
    max_user_concurrency: int = 0,
    model_source: ModelSource = ModelSource.SYSTEM,
    model_api_key: str = PROVIDER_KEY,
    model_cost: int = 3,
    configure_runtime: bool = True,
    wallet_credits: int | None = 500,
    lose_settlement_commit_ack: bool = False,
    lose_queue_commit_ack: bool = False,
    business_expire_on_commit: bool = False,
    server_id_factory: Callable[[], str] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> tuple[_System, _LocalRuntime]:
    engine = _joint_database(
        tmp_path,
        model_source=model_source,
        wallet_credits=wallet_credits,
    )
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with engine.begin() as connection:
        connection.execute(
            update(users)
            .where(users.c.id == _G2_JOINT_ACTOR_ID)
            .values(is_superuser=False)
        )
        model_table = (
            system_models if model_source is ModelSource.SYSTEM else ModelConfigRow.__table__
        )
        model_values: dict[str, object] = {"api_key": model_api_key}
        if model_source is ModelSource.SYSTEM:
            model_values["cost_per_call"] = model_cost
        connection.execute(
            update(model_table)
            .where(model_table.c.id == _G2_JOINT_MODEL_ID)
            .values(**model_values)
        )
        connection.execute(
            insert(user_sessions).values(
                id=AUTH_SESSION_ID,
                user_id=_G2_JOINT_ACTOR_ID,
                jti=JTI,
                user_agent="local chat text test",
                ip_address="127.0.0.1",
                device_name="synthetic fixture",
                is_active=True,
                created_at=now,
                last_seen_at=now,
                revoked_at=None,
            )
        )
        if message:
            connection.execute(
                insert(chat_messages).values(
                    id=MESSAGE_ID,
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

    business_sessions: list[_TrackedSession] = []
    business_events: list[str] = []
    commit_ack_state = {"enabled": lose_queue_commit_ack, "used": False}
    if lose_settlement_commit_ack:
        previous_callback = before_first_event

        def enable_commit_ack_loss() -> None:
            commit_ack_state["enabled"] = True
            if previous_callback is not None:
                previous_callback()

        before_first_event = enable_commit_ack_loss
    close_gate_state = {"used": False}
    business_maker = sessionmaker(
        engine,
        class_=_TrackedSession,
        expire_on_commit=business_expire_on_commit,
        info={
            "kind": "business",
            "events": business_events,
            "close_gate": business_close_gate,
            "close_gate_state": close_gate_state,
            "commit_ack_state": commit_ack_state,
        },
    )

    def business_factory() -> _TrackedSession:
        session = business_maker()
        business_sessions.append(session)
        business_events.append("open:business")
        return session

    auth_maker = sessionmaker(engine, expire_on_commit=False)
    token_codec = JoseTokenCodec(SECRET)
    auth_runtime = AuthenticationRuntime(
        session_factory=auth_maker,
        secret_key=SECRET,
        token_codec=token_codec,
    )
    runtime = _LocalRuntime(
        events,
        business_sessions,
        before_first_event=before_first_event,
        provider_gate=provider_gate,
        provider_finished=provider_finished,
    )
    app = create_app(
        session_factory=business_factory,
        authentication_runtime=auth_runtime,
        chat_text_generation_configuration=(
            ChatTextGenerationConfiguration(
                runtime=runtime,
                max_user_concurrency=max_user_concurrency,
                server_id_factory=server_id_factory,
                clock=clock,
            )
            if configure_runtime
            else None
        ),
    )
    token = token_codec.encode(
        {
            "sub": _G2_JOINT_ACTOR_ID,
            "jti": JTI,
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        }
    )
    return _System(engine, app, business_sessions, business_events, token), runtime


def _post_stream(
    system: _System,
    *,
    message: bool = True,
    skip_credit_check: bool = False,
) -> httpx.Response:
    async def send() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=system.app, raise_app_exceptions=False),
            base_url=BASE_URL,
        ) as client:
            body: dict[str, object] = {
                "model_id": _G2_JOINT_MODEL_ID,
                "messages": [{"role": "user", "content": "请继续生成"}],
                "skip_credit_check": skip_credit_check,
            }
            if message:
                body["message_id"] = MESSAGE_ID
            return await client.post(
                "/api/chat/stream",
                headers={"Authorization": f"Bearer {system.token}"},
                json=body,
            )

    return asyncio.run(send())


def _post_json(system: _System, path: str, payload: object) -> httpx.Response:
    async def send() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=system.app, raise_app_exceptions=False),
            base_url=BASE_URL,
        ) as client:
            return await client.post(
                path,
                headers={"Authorization": f"Bearer {system.token}"},
                json=payload,
            )

    return asyncio.run(send())


def _post_json_with_raw_body(
    system: _System,
    path: str,
    body: bytes,
    *,
    authorization: str | None,
) -> httpx.Response:
    async def send() -> httpx.Response:
        headers = {"Content-Type": "application/json"}
        if authorization is not None:
            headers["Authorization"] = authorization
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=system.app, raise_app_exceptions=False),
            base_url=BASE_URL,
        ) as client:
            return await client.post(path, headers=headers, content=body)

    return asyncio.run(send())


def _insert_existing_task(
    system: _System,
    *,
    task_id: str,
    task_type: str,
    status: str,
    billing_status: str,
    request_data: str,
) -> None:
    stamp = datetime.now(timezone.utc).replace(tzinfo=None)
    with system.engine.begin() as connection:
        connection.execute(
            insert(AITaskRow.__table__).values(
                id=task_id,
                user_id=_G2_JOINT_ACTOR_ID,
                type=task_type,
                status=status,
                billing_status=billing_status,
                execution_generation=0,
                recovery_status="ready",
                message_id="fixture-message-" + task_id,
                credit_cost=0,
                model_name="Fixture model",
                request_data=request_data,
                result=None,
                progress=0,
                progress_message=None,
                created_at=stamp,
                updated_at=stamp,
            )
        )


def _await_workers(app: Any) -> None:
    async def wait() -> None:
        await app.state.chat_text_generation_execution.wait_for_idle()

    asyncio.run(wait())


def test_real_stream_redacts_full_overlapping_key_and_confirms_fee_after_writer_close(
    tmp_path: Path,
) -> None:
    system, runtime = _build_system(
        tmp_path,
        (
            {"type": "complete", "raw_response": "provider-forged-success"},
            {"type": "done"},
            PROVIDER_KEY,
        ),
    )
    try:
        response = _post_stream(system)
        assert response.status_code == 200, response.text
        _await_workers(system.app)

        assert PROVIDER_KEY not in response.text
        assert "[已隐藏]" in response.text
        assert response.text.count('"type": "complete"') == 1
        assert response.text.count('"type": "done"') == 1
        assert "provider-forged-success" not in response.text
        assert len(runtime.calls) == 1
        assert len(system.business_sessions) >= 2
        assert all(
            session.info.get("closed") and session.info.get("close_count") == 1
            for session in system.business_sessions
        )
        assert system.business_events[:2] == ["open:business", "close:business"]
        assert system.business_events[2] == "open:business"

        with system.engine.connect() as connection:
            proof = connection.execute(
                select(StreamFeeRecordRow.__table__)
            ).mappings().one()
            task = connection.execute(select(AITaskRow.__table__)).mappings().one()
            message = connection.execute(
                select(chat_messages.c.content).where(chat_messages.c.id == MESSAGE_ID)
            ).scalar_one()
            message_scope = connection.execute(
                select(chat_messages.c.chapter_id, chapters.c.series_id)
                .select_from(
                    chat_messages.join(chapters, chapters.c.id == chat_messages.c.chapter_id)
                )
                .where(chat_messages.c.id == MESSAGE_ID)
            ).one()
            balance = connection.execute(
                select(UserCreditRow.__table__.c.credits)
            ).scalar_one()
            logs = connection.execute(select(CreditLogRow.__table__)).mappings().all()

        assert proof["settlement_state"] == "success_retained"
        assert proof["task_id"] == task["id"]
        assert task["status"] == "completed"
        assert task["result"] == "[已隐藏]"
        assert message == "[已隐藏]"
        assert tuple(message_scope) == ("joint-chapter-1", "joint-series-1")
        assert message_scope.chapter_id != message_scope.series_id
        assert PROVIDER_KEY not in task["result"]
        assert balance == 497
        assert len(logs) == 1
        assert logs[0]["type"] == "usage"
    finally:
        system.engine.dispose()


def test_real_stream_redacts_cross_chunk_key_in_sse_error_and_refunds_once(
    tmp_path: Path,
) -> None:
    system, _runtime = _build_system(
        tmp_path,
        (
            "ab",
            "abab",
            {"type": "error", "detail": "provider rejected key=" + PROVIDER_KEY},
        ),
    )
    try:
        response = _post_stream(system)
        assert response.status_code == 200, response.text
        _await_workers(system.app)

        assert PROVIDER_KEY not in response.text
        assert response.text.count('"type": "error"') == 1
        assert "[已隐藏]" in response.text

        with system.engine.connect() as connection:
            proof = connection.execute(
                select(StreamFeeRecordRow.__table__)
            ).mappings().one()
            task = connection.execute(select(AITaskRow.__table__)).mappings().one()
            message = connection.execute(
                select(chat_messages.c.content).where(chat_messages.c.id == MESSAGE_ID)
            ).scalar_one()
            message_scope = connection.execute(
                select(chat_messages.c.chapter_id, chapters.c.series_id)
                .select_from(
                    chat_messages.join(chapters, chapters.c.id == chat_messages.c.chapter_id)
                )
                .where(chat_messages.c.id == MESSAGE_ID)
            ).one()
            balance = connection.execute(
                select(UserCreditRow.__table__.c.credits)
            ).scalar_one()
            logs = connection.execute(
                select(CreditLogRow.__table__).order_by(CreditLogRow.created_at)
            ).mappings().all()

        assert proof["settlement_state"] == "refunded"
        assert task["status"] == "failed"
        assert PROVIDER_KEY not in task["result"]
        assert task["result"] == message
        assert tuple(message_scope) == ("joint-chapter-1", "joint-series-1")
        assert message_scope.chapter_id != message_scope.series_id
        assert balance == 500
        assert [row["type"] for row in logs] == ["usage", "refund"]
        assert logs[1]["amount"] == 3
        assert logs[1]["related_debit_id"] == logs[0]["id"]
        assert logs[1]["task_id"] == task["id"]
        assert logs[1]["business_key"] == "stream:refund:" + proof["server_attempt_id"]
    finally:
        system.engine.dispose()


def test_taskless_stream_keeps_and_settles_its_real_fee_proof(tmp_path: Path) -> None:
    system, _runtime = _build_system(tmp_path, ("完成",), message=False)
    try:
        response = _post_stream(system, message=False)
        assert response.status_code == 200, response.text
        _await_workers(system.app)
        assert '"type": "complete"' in response.text

        with system.engine.connect() as connection:
            proof = connection.execute(
                select(StreamFeeRecordRow.__table__)
            ).mappings().one()
            task_count = connection.execute(select(AITaskRow.__table__)).all()
            logs = connection.execute(select(CreditLogRow.__table__)).mappings().all()
            balance = connection.execute(
                select(UserCreditRow.__table__.c.credits)
            ).scalar_one()

        assert proof["settlement_state"] == "success_retained"
        assert proof["task_id"] is None
        assert task_count == []
        assert len(logs) == 1 and logs[0]["type"] == "usage"
        assert balance == 497
    finally:
        system.engine.dispose()


def test_stream_does_not_settle_or_overwrite_after_task_execution_changes(
    tmp_path: Path,
) -> None:
    captured: dict[str, Engine] = {}

    def take_over_task() -> None:
        engine = captured["engine"]
        with engine.begin() as connection:
            task_id = connection.execute(
                select(AITaskRow.id).where(
                    AITaskRow.user_id == _G2_JOINT_ACTOR_ID,
                    AITaskRow.message_id == MESSAGE_ID,
                )
            ).scalar_one()
            connection.execute(
                update(AITaskRow)
                .where(AITaskRow.id == task_id)
                .values(execution_generation=1, claim_token="other-execution")
            )

    system, _runtime = _build_system(
        tmp_path,
        (
            {"type": "complete", "raw_response": "provider-forged-success"},
            {"type": "done"},
            "stale response must not be saved",
        ),
        before_first_event=take_over_task,
    )
    captured["engine"] = system.engine
    try:
        response = _post_stream(system)
        assert response.status_code == 200, response.text
        _await_workers(system.app)
        assert '"type": "error"' in response.text
        assert '"type": "complete"' not in response.text
        assert response.text.count('"type": "done"') == 1
        assert "provider-forged-success" not in response.text

        with system.engine.connect() as connection:
            proof = connection.execute(
                select(StreamFeeRecordRow.__table__)
            ).mappings().one()
            task = connection.execute(select(AITaskRow.__table__)).mappings().one()
            message = connection.execute(
                select(chat_messages.c.content).where(chat_messages.c.id == MESSAGE_ID)
            ).scalar_one()
            balance = connection.execute(
                select(UserCreditRow.__table__.c.credits)
            ).scalar_one()
            logs = connection.execute(select(CreditLogRow.__table__)).mappings().all()

        assert proof["settlement_state"] == "committed"
        assert task["execution_generation"] == 1
        assert task["claim_token"] == "other-execution"
        assert task["status"] == "processing"
        assert task["result"] is None
        assert message == "原始占位内容"
        assert balance == 497
        assert len(logs) == 1 and logs[0]["type"] == "usage"
    finally:
        system.engine.dispose()


def test_cancelled_http_admission_drains_fee_and_task_setup_then_finishes_stream(
    tmp_path: Path,
) -> None:
    close_started = threading.Event()
    close_release = threading.Event()
    system, runtime = _build_system(
        tmp_path,
        ("生成完成",),
        business_close_gate=(close_started, close_release),
    )
    execution = system.app.state.chat_text_generation_execution
    original_start = execution.start
    started_queues: list[asyncio.Queue[dict[str, Any] | None]] = []

    def capture_started_queue(prepared: Any) -> asyncio.Queue[dict[str, Any] | None]:
        queue = original_start(prepared)
        started_queues.append(queue)
        return queue

    execution.start = capture_started_queue

    async def cancel_during_close() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=system.app, raise_app_exceptions=False),
            base_url=BASE_URL,
        ) as client:
            request = asyncio.create_task(
                client.post(
                    "/api/chat/stream",
                    headers={"Authorization": f"Bearer {system.token}"},
                    json={
                        "model_id": _G2_JOINT_MODEL_ID,
                        "message_id": MESSAGE_ID,
                        "messages": [{"role": "user", "content": "继续"}],
                    },
                )
            )
            started = await asyncio.wait_for(asyncio.to_thread(close_started.wait, 3), 4)
            assert started
            request.cancel()
            await asyncio.sleep(0)
            request.cancel()
            await asyncio.sleep(0.05)
            done_before_release = request.done()
            close_release.set()
            try:
                await request
            except asyncio.CancelledError:
                pass
            else:
                raise AssertionError("cancelled admission unexpectedly returned a response")
            assert not done_before_release
            await system.app.state.chat_text_generation_execution.wait_for_idle()
            assert len(started_queues) == 1
            assert getattr(started_queues[0], "consumer_disconnected", False)
            assert started_queues[0].empty()

    try:
        try:
            asyncio.run(cancel_during_close())
        finally:
            close_release.set()

        assert len(runtime.calls) == 1
        with system.engine.connect() as connection:
            proof = connection.execute(
                select(StreamFeeRecordRow.__table__)
            ).mappings().one()
            task = connection.execute(select(AITaskRow.__table__)).mappings().one()
            balance = connection.execute(
                select(UserCreditRow.__table__.c.credits)
            ).scalar_one()
            logs = connection.execute(select(CreditLogRow.__table__)).mappings().all()

        assert proof["settlement_state"] == "success_retained"
        assert task["status"] == "completed"
        assert task["result"] == "生成完成"
        assert balance == 497
        assert len(logs) == 1 and logs[0]["type"] == "usage"
        assert all(
            session.info.get("closed") and session.info.get("close_count") == 1
            for session in system.business_sessions
        )
    finally:
        close_release.set()
        system.engine.dispose()


def test_unknown_settlement_commit_requires_exact_fresh_task_and_refund_confirmation(
    tmp_path: Path,
) -> None:
    system, _runtime = _build_system(
        tmp_path,
        ("acknowledged only by fresh SQL",),
        lose_settlement_commit_ack=True,
    )
    try:
        response = _post_stream(system)
        assert response.status_code == 200, response.text
        _await_workers(system.app)

        with system.engine.connect() as connection:
            proof = connection.execute(
                select(StreamFeeRecordRow.__table__)
            ).mappings().one()
            task = connection.execute(select(AITaskRow.__table__)).mappings().one()
            logs = connection.execute(select(CreditLogRow.__table__)).mappings().all()
            balance = connection.execute(
                select(UserCreditRow.__table__.c.credits)
            ).scalar_one()

        assert proof["settlement_state"] == "success_retained"
        assert task["status"] == "completed"
        assert task["result"] == "acknowledged only by fresh SQL"
        assert len(logs) == 1 and logs[0]["type"] == "usage"
        assert balance == 497
    finally:
        system.engine.dispose()


def test_expired_queue_commit_ack_is_confirmed_without_reinserting_task(
    tmp_path: Path,
) -> None:
    system, _runtime = _build_system(
        tmp_path,
        (),
        business_expire_on_commit=True,
        lose_queue_commit_ack=True,
    )
    try:
        response = _post_json(
            system,
            "/api/chat/ai-review",
            {
                "chapter_id": "joint-chapter-1",
                "frame_index": 0,
                "model_id": _G2_JOINT_MODEL_ID,
            },
        )
        assert response.status_code == 200, response.text
        task_id = response.json()["task_id"]
        with system.engine.connect() as connection:
            tasks = connection.execute(select(AITaskRow.__table__)).mappings().all()
            wallet = connection.execute(
                select(UserCreditRow.__table__.c.credits)
            ).scalar_one()
            logs = connection.execute(select(CreditLogRow.__table__)).all()
            proofs = connection.execute(
                select(StreamFeeRecordRow.__table__)
            ).all()

        assert len(tasks) == 1
        assert tasks[0]["id"] == task_id
        assert tasks[0]["status"] == "queued"
        assert tasks[0]["credit_cost"] == 0
        assert wallet == 500
        assert logs == []
        assert proofs == []
        assert len(system.business_sessions) == 1
        assert system.business_sessions[0].info["closed"]
        assert system.business_sessions[0].info["close_count"] == 1
    finally:
        system.engine.dispose()


@pytest.mark.parametrize("callback_name", ("server_id_factory", "clock"))
def test_stream_callback_failures_do_not_leak_a_business_session(
    tmp_path: Path,
    callback_name: str,
) -> None:
    callback_options: dict[str, Any] = {}
    if callback_name == "server_id_factory":
        calls = 0

        def fail_after_session_opens() -> str:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("synthetic ID callback failure")
            return f"stream-callback-{calls}"

        callback_options[callback_name] = fail_after_session_opens
    else:
        def fail_clock() -> datetime:
            raise RuntimeError("synthetic clock callback failure")

        callback_options[callback_name] = fail_clock

    system, runtime = _build_system(tmp_path, (), **callback_options)
    try:
        response = _post_stream(system)
        assert response.status_code == 503
        assert runtime.calls == []
        expected_business_sessions = 1 if callback_name == "server_id_factory" else 0
        assert len(system.business_sessions) == expected_business_sessions
        assert all(
            session.info["closed"] and session.info["close_count"] == 1
            for session in system.business_sessions
        )
        with system.engine.connect() as connection:
            assert connection.execute(select(StreamFeeRecordRow.__table__)).all() == []
            assert connection.execute(select(AITaskRow.__table__)).all() == []
            assert connection.execute(select(CreditLogRow.__table__)).all() == []
            assert connection.execute(
                select(UserCreditRow.__table__.c.credits)
            ).scalar_one() == 500
    finally:
        system.engine.dispose()


def test_review_and_batch_admission_persist_queued_tasks_without_debit(
    tmp_path: Path,
) -> None:
    system, _runtime = _build_system(tmp_path, ())
    try:
        stamp = datetime.now(timezone.utc).replace(tzinfo=None)
        with system.engine.begin() as connection:
            for model_id, name, sort_order in (
                ("review-text-fallback", "Fallback text", 0),
                ("review-text-preferred", "Preferred text", 1),
            ):
                connection.execute(
                    insert(system_models).values(
                        id=model_id,
                        category="text",
                        name=name,
                        provider="fixture-provider",
                        api_key="fixture-key",
                        model=None,
                        base_url=None,
                        cost_per_call=3,
                        description="local fixture",
                        is_active=True,
                        sort_order=sort_order,
                        params=None,
                        created_at=stamp,
                        updated_at=stamp,
                    )
                )
            connection.execute(
                insert(UserModelOrderRow.__table__).values(
                    id="review-model-order",
                    user_id=_G2_JOINT_ACTOR_ID,
                    model_id="review-text-preferred",
                    category="text",
                    sort_order=0,
                    created_at=stamp,
                    updated_at=stamp,
                )
            )
        review = _post_json(
            system,
            "/api/chat/ai-review",
            {
                "chapter_id": "joint-chapter-1",
                "frame_index": 0,
                "model_id": "",
                "error_reason": "请核对人物动作",
                "asset_context": "人物位于门边",
                "prev_context": "上一格仍在走廊",
                "enable_thinking": "false",
            },
        )
        batch_payload = {
            "chapter_id": "joint-chapter-1",
            "model_id": _G2_JOINT_MODEL_ID,
            "prompt_id": "",
            "concurrency": "4",
            "chapter_texts": ["第一场", "第二场"],
            "frames": [
                {"index": 0, "text": "人物走向门口", "asset_context": "主角"},
                {"index": 1, "text": "人物推开门"},
            ],
            "unknown_legacy_field": "ignored",
        }
        batch = _post_json(system, "/api/chat/batch-optimize", batch_payload)
        duplicate_batch = _post_json(
            system, "/api/chat/batch-optimize", batch_payload
        )

        assert review.status_code == 200, review.text
        assert batch.status_code == 200, batch.text
        assert duplicate_batch.status_code == 409
        review_body = review.json()
        batch_body = batch.json()
        assert review_body["task_id"]
        assert review_body["model_name"] == "Preferred text"
        assert review_body["credit_cost"] == 3
        assert batch_body == {
            "task_id": batch_body["task_id"],
            "chapter_id": "joint-chapter-1",
            "frame_count": 2,
        }

        with system.engine.connect() as connection:
            tasks = connection.execute(
                select(AITaskRow.__table__).order_by(AITaskRow.created_at)
            ).mappings().all()
            wallet = connection.execute(
                select(UserCreditRow.__table__.c.credits)
            ).scalar_one()
            logs = connection.execute(select(CreditLogRow.__table__)).mappings().all()
            proofs = connection.execute(
                select(StreamFeeRecordRow.__table__)
            ).mappings().all()

        assert {task["id"] for task in tasks} == {
            review_body["task_id"],
            batch_body["task_id"],
        }
        by_type = {task["type"]: task for task in tasks}
        assert set(by_type) == {"ai-review", "batch-optimize"}
        assert all(task["status"] == "queued" for task in tasks)
        assert all(task["credit_cost"] == 0 for task in tasks)
        assert all(task["message_id"] for task in tasks)
        review_data = json.loads(by_type["ai-review"]["request_data"])
        assert review_data == {
            "chapter_id": "joint-chapter-1",
            "frame_index": 0,
            "prompt_id": "",
            "model_id": "review-text-preferred",
            "is_system": True,
            "cost_per_call": 3,
            "enable_thinking": False,
            "error_reason": "请核对人物动作",
            "asset_context": "人物位于门边",
            "prev_context": "上一格仍在走廊",
            "source_message_id": "",
        }
        batch_data = json.loads(by_type["batch-optimize"]["request_data"])
        assert batch_data["chapter_texts"] == ["第一场", "第二场"]
        assert batch_data["chapter_background"] == ""
        assert batch_data["frame_count"] == 2
        assert batch_data["concurrency"] == 4
        assert "unknown_legacy_field" not in batch_data
        assert review_data["enable_thinking"] is False
        assert wallet == 500
        assert logs == []
        assert proofs == []
        invalid_boolean = _post_json(
            system,
            "/api/chat/ai-review",
            {
                "chapter_id": "joint-chapter-1",
                "frame_index": 0,
                "model_id": _G2_JOINT_MODEL_ID,
                "enable_thinking": "not-a-boolean",
            },
        )
        assert invalid_boolean.status_code == 422
        with system.engine.connect() as connection:
            assert set(connection.execute(select(AITaskRow.id)).scalars()) == {
                review_body["task_id"],
                batch_body["task_id"],
            }
    finally:
        system.engine.dispose()


@pytest.mark.parametrize(
    ("existing_status", "expected_status"),
    (("queued", 409), ("processing", 409), ("cancelling", 409), ("reconciling", 200)),
)
def test_batch_duplicate_guard_uses_legacy_running_states(
    tmp_path: Path,
    existing_status: str,
    expected_status: int,
) -> None:
    system, _runtime = _build_system(tmp_path, ())
    request_data = json.dumps({"chapter_id": "joint-chapter-1"})
    _insert_existing_task(
        system,
        task_id="existing-batch-" + existing_status,
        task_type="batch-optimize",
        status=existing_status,
        billing_status="unbilled",
        request_data=request_data,
    )
    try:
        response = _post_json(
            system,
            "/api/chat/batch-optimize",
            {
                "chapter_id": "joint-chapter-1",
                "model_id": _G2_JOINT_MODEL_ID,
                "frames": [{"index": 0, "text": "兼容状态检查"}],
            },
        )
        assert response.status_code == expected_status, response.text
        with system.engine.connect() as connection:
            tasks = connection.execute(select(AITaskRow.__table__)).mappings().all()
        same_chapter = [
            task
            for task in tasks
            if json.loads(task["request_data"] or "{}").get("chapter_id")
            == "joint-chapter-1"
        ]
        expected_count = 1 if expected_status == 409 else 2
        assert len(same_chapter) == expected_count
        assert any(task["status"] == existing_status for task in same_chapter)
    finally:
        system.engine.dispose()


def test_concurrent_batch_admission_allows_only_one_same_chapter_task(
    tmp_path: Path,
) -> None:
    system, _runtime = _build_system(tmp_path, ())
    payload = {
        "chapter_id": "joint-chapter-1",
        "model_id": _G2_JOINT_MODEL_ID,
        "frames": [{"index": 0, "text": "并发测试"}],
    }

    async def submit_concurrently() -> list[httpx.Response]:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=system.app, raise_app_exceptions=False),
            base_url=BASE_URL,
        ) as client:
            headers = {"Authorization": f"Bearer {system.token}"}
            return list(
                await asyncio.gather(
                    *(
                        client.post(
                            "/api/chat/batch-optimize", headers=headers, json=payload
                        )
                        for _ in range(2)
                    )
                )
            )

    try:
        responses = asyncio.run(submit_concurrently())
        assert sorted(response.status_code for response in responses) == [200, 409]
        with system.engine.connect() as connection:
            tasks = connection.execute(select(AITaskRow.__table__)).mappings().all()
        assert len(tasks) == 1
        assert tasks[0]["type"] == "batch-optimize"
        assert tasks[0]["status"] == "queued"
        assert json.loads(tasks[0]["request_data"])["chapter_id"] == "joint-chapter-1"
    finally:
        system.engine.dispose()


def test_positive_concurrency_limit_serializes_two_review_requests(
    tmp_path: Path,
) -> None:
    system, _runtime = _build_system(tmp_path, (), max_user_concurrency=1)
    _insert_existing_task(
        system,
        task_id="already-refunded-review",
        task_type="ai-review",
        status="processing",
        billing_status="refunded",
        request_data="{}",
    )
    payload = {
        "chapter_id": "joint-chapter-1",
        "frame_index": 1,
        "model_id": _G2_JOINT_MODEL_ID,
    }

    async def submit_concurrently() -> list[httpx.Response]:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=system.app, raise_app_exceptions=False),
            base_url=BASE_URL,
        ) as client:
            headers = {"Authorization": f"Bearer {system.token}"}
            return list(
                await asyncio.gather(
                    *(
                        client.post("/api/chat/ai-review", headers=headers, json=payload)
                        for _ in range(2)
                    )
                )
            )

    try:
        responses = asyncio.run(submit_concurrently())
        assert sorted(response.status_code for response in responses) == [200, 429]
        with system.engine.connect() as connection:
            tasks = connection.execute(select(AITaskRow.__table__)).mappings().all()
            wallet = connection.execute(
                select(UserCreditRow.__table__.c.credits)
            ).scalar_one()
            logs = connection.execute(select(CreditLogRow.__table__)).mappings().all()
        assert len(tasks) == 2
        refunded = next(task for task in tasks if task["id"] == "already-refunded-review")
        queued = next(task for task in tasks if task["id"] != "already-refunded-review")
        assert refunded["status"] == "processing"
        assert refunded["billing_status"] == "refunded"
        assert queued["type"] == "ai-review"
        assert queued["status"] == "queued"
        assert queued["credit_cost"] == 0
        assert wallet == 500
        assert logs == []
    finally:
        system.engine.dispose()


def test_selected_foreign_or_disabled_prompt_is_rejected_without_queue_write(
    tmp_path: Path,
) -> None:
    system, _runtime = _build_system(tmp_path, ())
    stamp = datetime.now(timezone.utc).replace(tzinfo=None)
    with system.engine.begin() as connection:
        connection.execute(
            insert(users).values(
                id="prompt-owner-2",
                username="prompt-owner-2",
                email="prompt-owner-2@example.test",
                hashed_password="fixture-only-hash",
                is_superuser=False,
                membership_type="free",
                membership_expires_at=None,
                created_at=stamp,
                password_updated_at=None,
            )
        )
        connection.execute(
            insert(prompt_configs).values(
                id="foreign-prompt",
                user_id="prompt-owner-2",
                category="unrestricted-legacy-category",
                name="foreign",
                system_prompt="foreign system",
                user_prompt="foreign user",
                sort_order=0,
                created_at=stamp,
                updated_at=stamp,
            )
        )
        connection.execute(
            insert(system_prompts).values(
                id="disabled-prompt",
                category="",
                name="disabled",
                system_prompt="disabled system",
                user_prompt="disabled user",
                sort_order=0,
                is_active=False,
                created_at=stamp,
                updated_at=stamp,
            )
        )

    try:
        base_payload = {
            "chapter_id": "joint-chapter-1",
            "frame_index": 0,
            "model_id": _G2_JOINT_MODEL_ID,
        }
        foreign = _post_json(
            system,
            "/api/chat/ai-review",
            {**base_payload, "prompt_id": "foreign-prompt"},
        )
        disabled = _post_json(
            system,
            "/api/chat/batch-optimize",
            {
                "chapter_id": "joint-chapter-1",
                "model_id": _G2_JOINT_MODEL_ID,
                "prompt_id": "disabled-prompt",
                "frames": [{"index": 0, "text": "不可入队"}],
            },
        )
        assert foreign.status_code == 400
        assert disabled.status_code == 400
        with system.engine.connect() as connection:
            tasks = connection.execute(select(AITaskRow.__table__)).all()
            wallet = connection.execute(
                select(UserCreditRow.__table__.c.credits)
            ).scalar_one()
            logs = connection.execute(select(CreditLogRow.__table__)).all()
        assert tasks == []
        assert wallet == 500
        assert logs == []
    finally:
        system.engine.dispose()


def test_chat_text_authentication_precedes_invalid_json(tmp_path: Path) -> None:
    system, _runtime = _build_system(tmp_path, ())
    try:
        response = _post_json_with_raw_body(
            system,
            "/api/chat/ai-review",
            b"{",
            authorization=None,
        )
        assert response.status_code == 401
    finally:
        system.engine.dispose()


def test_unconfigured_stream_runtime_returns_503_before_fee_or_task_write(
    tmp_path: Path,
) -> None:
    system, runtime = _build_system(tmp_path, ("不应调用",), configure_runtime=False)
    try:
        response = _post_stream(system)
        assert response.status_code == 503
        assert runtime.calls == []
        with system.engine.connect() as connection:
            proofs = connection.execute(
                select(StreamFeeRecordRow.__table__)
            ).all()
            tasks = connection.execute(select(AITaskRow.__table__)).all()
            wallet = connection.execute(
                select(UserCreditRow.__table__.c.credits)
            ).scalar_one()
            logs = connection.execute(select(CreditLogRow.__table__)).all()
        assert proofs == []
        assert tasks == []
        assert wallet == 500
        assert logs == []
    finally:
        system.engine.dispose()


@pytest.mark.parametrize("skip_credit_check", (False, True))
def test_system_model_insufficient_balance_returns_402_without_writes(
    tmp_path: Path,
    skip_credit_check: bool,
) -> None:
    system, runtime = _build_system(
        tmp_path,
        ("不应调用",),
        wallet_credits=0,
    )
    try:
        response = _post_stream(system, skip_credit_check=skip_credit_check)
        assert response.status_code == 402
        assert runtime.calls == []
        with system.engine.connect() as connection:
            proofs = connection.execute(
                select(StreamFeeRecordRow.__table__)
            ).all()
            tasks = connection.execute(select(AITaskRow.__table__)).all()
            wallet = connection.execute(
                select(UserCreditRow.__table__.c.credits)
            ).scalar_one()
            logs = connection.execute(select(CreditLogRow.__table__)).all()
        assert proofs == []
        assert tasks == []
        assert wallet == 0
        assert logs == []
    finally:
        system.engine.dispose()


def test_closing_sse_consumer_does_not_cancel_provider_or_sql_settlement(
    tmp_path: Path,
) -> None:
    provider_started = asyncio.Event()
    provider_release = asyncio.Event()
    provider_finished = asyncio.Event()
    system, runtime = _build_system(
        tmp_path,
        ("在客户端断开后仍完成",),
        provider_gate=(provider_started, provider_release),
        provider_finished=provider_finished,
    )

    async def close_consumer() -> None:
        route = next(
            route
            for route in system.app.routes
            if getattr(route, "path", None) == "/api/chat/stream"
            and "POST" in getattr(route, "methods", set())
        )
        body = json.dumps(
            {
                "model_id": _G2_JOINT_MODEL_ID,
                "message_id": MESSAGE_ID,
                "messages": [{"role": "user", "content": "继续"}],
            }
        ).encode()
        received = False

        async def receive() -> dict[str, object]:
            nonlocal received
            if received:
                return {"type": "http.disconnect"}
            received = True
            return {"type": "http.request", "body": body, "more_body": False}

        request = Request(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/api/chat/stream",
                "raw_path": b"/api/chat/stream",
                "query_string": b"",
                "headers": [(b"authorization", f"Bearer {system.token}".encode())],
                "server": ("chat-text-generation.test", 80),
                "client": ("127.0.0.1", 12345),
                "root_path": "",
            },
            receive=receive,
        )
        response = await route.endpoint(request)
        iterator = response.body_iterator
        first = await anext(iterator)
        assert '"type": "start"' in first
        assert await asyncio.wait_for(provider_started.wait(), timeout=3)
        await iterator.aclose()
        assert not provider_finished.is_set()
        provider_release.set()
        await asyncio.wait_for(
            system.app.state.chat_text_generation_execution.wait_for_idle(),
            timeout=3,
        )
        assert provider_finished.is_set()

    try:
        try:
            asyncio.run(close_consumer())
        finally:
            provider_release.set()
        assert len(runtime.calls) == 1
        with system.engine.connect() as connection:
            proof = connection.execute(
                select(StreamFeeRecordRow.__table__)
            ).mappings().one()
            task = connection.execute(select(AITaskRow.__table__)).mappings().one()
            message = connection.execute(
                select(chat_messages.c.content).where(chat_messages.c.id == MESSAGE_ID)
            ).scalar_one()
        assert proof["settlement_state"] == "success_retained"
        assert task["status"] == "completed"
        assert task["result"] == message == "在客户端断开后仍完成"
    finally:
        provider_release.set()
        system.engine.dispose()


def test_empty_stream_is_a_failed_task_with_error_event(tmp_path: Path) -> None:
    system, _runtime = _build_system(
        tmp_path,
        (
            {"type": "complete", "raw_response": "provider-forged-success"},
            {"type": "done"},
        ),
    )
    try:
        response = _post_stream(system)
        assert response.status_code == 200, response.text
        _await_workers(system.app)
        assert '"type": "error"' in response.text
        assert '"type": "complete"' not in response.text
        assert response.text.count('"type": "done"') == 1
        assert "provider-forged-success" not in response.text

        with system.engine.connect() as connection:
            task = connection.execute(select(AITaskRow.__table__)).mappings().one()
            proof = connection.execute(
                select(StreamFeeRecordRow.__table__)
            ).mappings().one()
        assert task["status"] == "failed"
        assert task["result"] == "AI 返回为空"
        assert proof["settlement_state"] == "refunded"
    finally:
        system.engine.dispose()


@pytest.mark.parametrize(
    ("case_name", "model_source", "model_cost", "skip_credit_check", "disposition"),
    (
        ("skipped", ModelSource.SYSTEM, 3, True, "skipped"),
        ("byok", ModelSource.USER, 3, False, "byok"),
        ("zero-cost", ModelSource.SYSTEM, 0, False, "zero"),
    ),
)
def test_stream_fee_dispositions_check_balance_without_unearned_debits(
    tmp_path: Path,
    case_name: str,
    model_source: ModelSource,
    model_cost: int,
    skip_credit_check: bool,
    disposition: str,
) -> None:
    case_path = tmp_path / case_name
    case_path.mkdir()
    system, _runtime = _build_system(
        case_path,
        ("完成",),
        model_source=model_source,
        model_cost=model_cost,
    )
    try:
        response = _post_stream(system, skip_credit_check=skip_credit_check)
        assert response.status_code == 200, response.text
        _await_workers(system.app)

        with system.engine.connect() as connection:
            proof = connection.execute(
                select(StreamFeeRecordRow.__table__)
            ).mappings().one()
            task = connection.execute(select(AITaskRow.__table__)).mappings().one()
            wallet = connection.execute(
                select(UserCreditRow.__table__.c.credits)
            ).scalar_one()
            logs = connection.execute(select(CreditLogRow.__table__)).mappings().all()
        assert proof["disposition"] == disposition
        assert proof["actual_debited_amount"] == 0
        assert task["credit_cost"] == (model_cost if model_source is ModelSource.SYSTEM else 0)
        assert proof["settlement_state"] == "success_retained"
        assert task["status"] == "completed"
        assert wallet == 500
        assert logs == []
    finally:
        system.engine.dispose()


def test_encrypted_provider_key_is_decrypted_only_with_explicit_auth_secret(
    tmp_path: Path,
) -> None:
    encrypted_key = LegacyFernet(SECRET).encrypt(PROVIDER_KEY)
    system, runtime = _build_system(tmp_path, ("完成"), model_api_key=encrypted_key)
    try:
        response = _post_stream(system)
        assert response.status_code == 200, response.text
        _await_workers(system.app)
        assert len(runtime.calls) == 1
        assert runtime.calls[0].model.api_key == PROVIDER_KEY
    finally:
        system.engine.dispose()


def test_wrong_encryption_secret_fails_before_fee_or_provider_call(tmp_path: Path) -> None:
    encrypted_key = LegacyFernet("different synthetic secret").encrypt(PROVIDER_KEY)
    system, runtime = _build_system(tmp_path, ("不应调用"), model_api_key=encrypted_key)
    try:
        response = _post_stream(system)
        assert response.status_code == 503
        assert runtime.calls == []
        with system.engine.connect() as connection:
            proofs = connection.execute(
                select(StreamFeeRecordRow.__table__)
            ).all()
            tasks = connection.execute(select(AITaskRow.__table__)).all()
            wallet = connection.execute(
                select(UserCreditRow.__table__.c.credits)
            ).scalar_one()
            logs = connection.execute(select(CreditLogRow.__table__)).all()
        assert proofs == []
        assert tasks == []
        assert wallet == 500
        assert logs == []
    finally:
        system.engine.dispose()
