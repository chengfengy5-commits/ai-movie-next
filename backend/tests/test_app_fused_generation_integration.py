from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import hashlib
import json
import struct
import threading
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, event, insert, select, update
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.app import create_app
from haoai_backend.admin_system_models.crypto import LegacyFernet
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.tables import (
    metadata as authentication_metadata,
    user_credits,
    user_sessions,
    users,
)
from haoai_backend.authentication.tokens import JoseTokenCodec
from haoai_backend.chat_data.tables import chat_messages, metadata as chat_metadata
from haoai_backend.fused_generation.domain import (
    FusedGenerationOutput,
    FusedServerImageModel,
    PreparedFusedGeneration,
)
from haoai_backend.fused_generation.errors import ProviderGenerationFailure
from haoai_backend.fused_generation.execution import FusedGenerationExecution
from haoai_backend.fused_generation.openai_runtime import FusedOpenAIImageRuntime
from haoai_backend.fused_generation.persistence import fused_generation_uow_factory
from haoai_backend.fused_generation.media_storage import (
    SynchronousFusedGenerationImageStorage,
)
from haoai_backend.generation_catalog.tables import (
    metadata as catalog_metadata,
    model_configs,
    prompt_configs,
    system_models,
    system_prompts,
)
from haoai_backend.generation_core.base_schema import AITaskRow, Base, CreditLogRow
from haoai_backend.generation_contracts import PreparedReference, StoredMedia
from haoai_backend.media_storage.owned_media_runtime import (
    OwnedMediaDependencies,
    OwnedMediaRuntime,
)
from haoai_backend.media_storage.sync_facade import SynchronousMediaStore
from haoai_backend.media_storage.transfer_types import (
    MediaTypeRule,
    R2Configuration,
    SafeMediaPolicy,
    SecretValue,
    WriteReceipt,
)
from haoai_backend.series_data.tables import (
    chapters,
    characters,
    metadata as series_metadata,
    props,
    series,
)
from haoai_backend.series_exchange.tables import fused_assets, metadata as exchange_metadata


SECRET = "fused-app-integration-secret"
SERVER_KEY = "fused-provider-secret-never-return"


class ObservedSession(Session):
    def close(self) -> None:
        if self.info.get("closed"):
            return
        self.info["closed"] = True
        self.info["close_count"] = self.info.get("close_count", 0) + 1
        self.info["events"].append((f"{self.info['kind']}-close", threading.get_ident()))
        super().close()


class FakeRuntime:
    def __init__(self, events, business_sessions):
        self.events = events
        self.business_sessions = business_sessions
        self.requests = []
        self.failure: ProviderGenerationFailure | None = None

    async def generate(self, request):
        assert all(session.info.get("closed") for session in self.business_sessions)
        self.events.append(("provider-runtime", threading.get_ident()))
        self.requests.append(request)
        if self.failure is not None:
            raise self.failure
        return FusedGenerationOutput("https://provider.test/fused-result.png")


class FailingStorage:
    def __init__(self, events):
        self.events = events
        self.urls = []

    async def store_generated_image(self, provider_url: str) -> str | None:
        self.events.append(("storage-transfer", threading.get_ident()))
        self.urls.append(provider_url)
        raise OSError("private storage endpoint detail")


class FakeProviderResponse:
    def __init__(self, status_code: int, body: bytes) -> None:
        self.status_code = status_code
        self.headers = {"content-length": str(len(body))}
        self.body = body

    async def iter_bytes(self, *, chunk_size: int):
        async def chunks():
            if self.body:
                yield self.body

        return chunks()


class FakeProviderHttpClient:
    def __init__(self, response: FakeProviderResponse) -> None:
        self.response = response
        self.calls = []

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.response


class FakeProviderHttpContext:
    def __init__(self, client: FakeProviderHttpClient) -> None:
        self.client = client

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, exc_type, exc, traceback):
        return None


class FakePublicMediaFetcher:
    def __init__(self) -> None:
        self.calls = []

    async def fetch_public(self, reference, *, max_bytes: int, timeout_seconds: float):
        self.calls.append((reference, max_bytes, timeout_seconds))
        return PreparedReference(
            kind="bytes",
            locator=None,
            content=b"synthetic-public-reference-image",
            media_type="image/png",
        )


@dataclass
class FakeR2State:
    mode: str = "success"
    calls: list[dict[str, Any]] = field(default_factory=list)
    clients: list[Any] = field(default_factory=list)
    stored_media: list[StoredMedia] = field(default_factory=list)
    close_calls: int = 0
    started: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)


class FakeR2Client:
    def __init__(self, state: FakeR2State) -> None:
        self.state = state

    def put_object(self, **kwargs: Any) -> WriteReceipt:
        body = kwargs["Body"].read()
        self.state.calls.append(
            {
                "bucket": kwargs["Bucket"],
                "key": kwargs["Key"],
                "content_type": kwargs["ContentType"],
                "cache_control": kwargs["CacheControl"],
                "body": body,
                "thread_id": threading.get_ident(),
            }
        )
        self.state.started.set()
        if self.state.mode == "storage_block" and not self.state.release.wait(timeout=5):
            raise TimeoutError("synthetic storage wait expired")
        if self.state.mode == "ack_unknown":
            raise TimeoutError("synthetic write acknowledgement lost")
        if self.state.mode == "wrong_receipt":
            return WriteReceipt("unexpected-key.png", 200, "synthetic-request-id")
        return WriteReceipt(kwargs["Key"], 200, "synthetic-request-id")

    def close(self) -> None:
        self.state.close_calls += 1
        if self.state.mode == "cleanup_failure" and self.state.close_calls == 1:
            raise OSError("synthetic SDK cleanup failure")


class FakeR2ClientFactory:
    def __init__(self, state: FakeR2State) -> None:
        self.state = state

    def create(self, settings, access_key, secret_key) -> FakeR2Client:
        client = FakeR2Client(self.state)
        self.state.clients.append(client)
        return client


class SyntheticSecretHandle:
    def __init__(self, value: str) -> None:
        self._value = SecretValue(value)

    def reveal(self) -> SecretValue:
        return self._value


class RecordingFusedImageStorage(SynchronousFusedGenerationImageStorage):
    def __init__(self, media_store: SynchronousMediaStore, state: FakeR2State) -> None:
        super().__init__(media_store)
        self.state = state

    def store_inline_image(self, image) -> StoredMedia:
        stored = super().store_inline_image(image)
        self.state.stored_media.append(stored)
        return stored


@dataclass
class AppFixture:
    engine: Any
    app: FastAPI
    events: list[tuple[str, int]]
    auth_sessions: list[ObservedSession]
    business_sessions: list[ObservedSession]
    loop_threads: list[int]
    token_for: Callable[[str, bool], str]
    runtime: FakeRuntime
    storage: FailingStorage
    inline_image_storage: RecordingFusedImageStorage
    inline_storage_state: FakeR2State
    inline_media_runtime: OwnedMediaRuntime
    authentication_runtime: AuthenticationRuntime
    business_factory: Callable[[], ObservedSession]


@pytest.fixture
def fused_app(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'fused-generation-app.sqlite'}",
        future=True,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    authentication_metadata.create_all(engine)
    Base.metadata.create_all(engine)
    series_metadata.create_all(engine, tables=[series, chapters, characters, props])
    catalog_metadata.create_all(
        engine,
        tables=[system_models, model_configs, system_prompts, prompt_configs],
    )
    chat_metadata.create_all(engine, tables=[chat_messages])
    exchange_metadata.create_all(engine)

    now = datetime(2026, 10, 15, 12, 0, 0)
    memberships = {
        "owner": ("premium", None, True),
        "free": ("free", None, True),
        "membership-expired": ("premium", now - timedelta(days=1), True),
    }
    with engine.begin() as connection:
        for user_id, (membership_type, expires, session_active) in memberships.items():
            connection.execute(
                insert(users).values(
                    id=user_id,
                    username=user_id,
                    email=f"{user_id}@example.test",
                    hashed_password="synthetic-test-hash",
                    is_superuser=False,
                    membership_type=membership_type,
                    membership_expires_at=expires,
                    avatar_url=None,
                    bio=None,
                    created_at=now - timedelta(days=10),
                    password_updated_at=now - timedelta(days=10),
                )
            )
            connection.execute(
                insert(user_sessions).values(
                    id=f"session-{user_id}",
                    user_id=user_id,
                    jti=f"jti-{user_id}",
                    user_agent="local integration test",
                    ip_address="127.0.0.1",
                    device_name="test",
                    is_active=session_active,
                    created_at=now - timedelta(hours=1),
                    last_seen_at=now - timedelta(minutes=2),
                    revoked_at=None,
                )
            )
            connection.execute(
                insert(user_credits).values(
                    id=f"credit-{user_id}",
                    user_id=user_id,
                    credits=20 if user_id == "owner" else 0,
                    created_at=now - timedelta(days=10),
                    updated_at=now - timedelta(days=10),
                )
            )
        connection.execute(
            insert(series).values(
                id="series-owner",
                user_id="owner",
                name="测试剧集",
                description="",
                image_url=None,
                style_prompt_id=None,
                team_id=None,
                claimed_by=None,
                claimed_at=None,
                created_at=now - timedelta(days=4),
                updated_at=now - timedelta(days=4),
            )
        )
        connection.execute(
            insert(series).values(
                id="series-foreign",
                user_id="free",
                name="其他用户剧集",
                description="",
                image_url=None,
                style_prompt_id=None,
                team_id=None,
                claimed_by=None,
                claimed_at=None,
                created_at=now - timedelta(days=4),
                updated_at=now - timedelta(days=4),
            )
        )
        connection.execute(
            insert(chapters).values(
                id="chapter-owner",
                series_id="series-owner",
                title="第一章",
                content='[{"text":"第一镜","fused":[]}]',
                order=0,
                created_at=now - timedelta(days=3),
                updated_at=now - timedelta(days=3),
            )
        )
        connection.execute(
            insert(chapters).values(
                id="chapter-foreign",
                series_id="series-foreign",
                title="外部章节",
                content='[{"text":"外部镜头","fused":[]}]',
                order=0,
                created_at=now - timedelta(days=3),
                updated_at=now - timedelta(days=3),
            )
        )
        connection.execute(
            insert(characters).values(
                id="character-owner",
                series_id="series-owner",
                name="林岚",
                aliases=None,
                canonical_key=None,
                gender=None,
                age=None,
                role=None,
                appearance=None,
                description=None,
                image_url="https://assets.test/lin.png",
                audio_url=None,
                voice_ref=None,
                created_at=now - timedelta(days=3),
                updated_at=now - timedelta(days=3),
            )
        )
        connection.execute(
            insert(characters).values(
                id="character-no-image",
                series_id="series-owner",
                name="无图角色",
                aliases=None,
                canonical_key=None,
                gender=None,
                age=None,
                role=None,
                appearance=None,
                description=None,
                image_url=None,
                audio_url=None,
                voice_ref=None,
                created_at=now - timedelta(days=3),
                updated_at=now - timedelta(days=3),
            )
        )
        connection.execute(
            insert(characters).values(
                id="character-foreign",
                series_id="series-foreign",
                name="越权角色",
                aliases=None,
                canonical_key=None,
                gender=None,
                age=None,
                role=None,
                appearance=None,
                description=None,
                image_url="https://assets.test/foreign.png",
                audio_url=None,
                voice_ref=None,
                created_at=now - timedelta(days=3),
                updated_at=now - timedelta(days=3),
            )
        )
        connection.execute(
            insert(system_models).values(
                id="system-model",
                category="text",  # Legacy _resolve_model did not add a category check.
                name="系统融合模型",
                provider="fake-provider",
                api_key=LegacyFernet(SECRET).encrypt(SERVER_KEY),
                model="image-v1",
                base_url="https://provider.test/api",
                cost_per_call=4,
                description=None,
                params="{}",
                is_active=True,
                sort_order=1,
                created_at=now - timedelta(days=4),
                updated_at=now - timedelta(days=4),
            )
        )
        connection.execute(
            insert(model_configs).values(
                id="owned-model",
                user_id="owner",
                category="video",
                name="自有融合模型",
                provider="fake-owned-provider",
                api_key=LegacyFernet(SECRET).encrypt("owned-secret"),
                model="owned-image-v1",
                base_url=None,
                sort_order=1,
                params="{}",
                created_at=now - timedelta(days=2),
                updated_at=now - timedelta(days=2),
            )
        )
        connection.execute(
            insert(system_prompts).values(
                id="prompt-system",
                category="fusion",
                name="融合提示词",
                system_prompt="保持画风",
                user_prompt="突出神态",
                is_active=True,
                sort_order=1,
                created_at=now - timedelta(days=2),
                updated_at=now - timedelta(days=2),
            )
        )

    events: list[tuple[str, int]] = []
    auth_sessions: list[ObservedSession] = []
    business_sessions: list[ObservedSession] = []

    def make_factory(kind: str, opened: list[ObservedSession]):
        maker = sessionmaker(
            engine,
            class_=ObservedSession,
            expire_on_commit=False,
            future=True,
            info={"kind": kind, "events": events},
        )

        def open_session():
            events.append((f"{kind}-open", threading.get_ident()))
            session = maker()
            opened.append(session)
            return session

        return open_session

    auth_factory = make_factory("auth", auth_sessions)
    business_factory = make_factory("business", business_sessions)
    authentication_runtime = AuthenticationRuntime(
        session_factory=auth_factory,
        secret_key=SECRET,
        clock=lambda: now,
    )
    codec = JoseTokenCodec(SECRET)

    def token_for(user_id: str, expired: bool = False) -> str:
        current_utc = datetime.now(timezone.utc)
        expiry = current_utc - timedelta(minutes=1) if expired else current_utc + timedelta(hours=1)
        return codec.encode(
            {
                "sub": user_id,
                "jti": f"jti-{user_id}",
                "exp": expiry,
            }
        )

    loop_threads: list[int] = []
    runtime = FakeRuntime(events, business_sessions)
    storage = FailingStorage(events)
    inline_storage_state = FakeR2State()
    media_directory = tmp_path / "fused-inline-media"
    media_directory.mkdir()
    media_configuration = R2Configuration(
        endpoint_url="https://objects.example.test",
        bucket="fused-test-media",
        public_base_url="https://cdn.example.test/media",
        transfer_tmp_dir=media_directory,
        business_type="generation",
        environment="test",
        connect_timeout_seconds=1,
        read_timeout_seconds=1,
    )
    inline_media_runtime = OwnedMediaRuntime(
        OwnedMediaDependencies(
            transfer_tmp_dir=media_directory,
            getaddrinfo=None,
            http_library=None,
            media_policy=SafeMediaPolicy((MediaTypeRule("image/png", ".png"),)),
            r2_configuration=media_configuration,
            access_key=SyntheticSecretHandle("synthetic-access-key"),
            secret_key=SyntheticSecretHandle("synthetic-secret-key"),
            s3_client_factory=FakeR2ClientFactory(inline_storage_state),
        ),
        loop_factory=asyncio.new_event_loop,
        thread_factory=threading.Thread,
        executor_factory=concurrent.futures.ThreadPoolExecutor,
    )
    synchronous_media_store = SynchronousMediaStore(
        inline_media_runtime,
        mode="url_or_inline",
        reference_stager=None,
        staging_directory=None,
    )
    inline_image_storage = RecordingFusedImageStorage(
        synchronous_media_store,
        inline_storage_state,
    )
    app = create_app(
        session_factory=business_factory,
        authentication_runtime=authentication_runtime,
        fused_generation_runtime=runtime,
        fused_generation_storage=storage,
        fused_generation_inline_image_storage=inline_image_storage,
    )
    try:
        yield AppFixture(
            engine=engine,
            app=app,
            events=events,
            auth_sessions=auth_sessions,
            business_sessions=business_sessions,
            loop_threads=loop_threads,
            token_for=token_for,
            runtime=runtime,
            storage=storage,
            inline_image_storage=inline_image_storage,
            inline_storage_state=inline_storage_state,
            inline_media_runtime=inline_media_runtime,
            authentication_runtime=authentication_runtime,
            business_factory=business_factory,
        )
    finally:
        inline_media_runtime.close()
        engine.dispose()


def _request(
    fixture: AppFixture,
    *,
    token: str | None,
    content: bytes | None = None,
    body: dict[str, Any] | None = None,
) -> httpx.Response:
    async def send() -> httpx.Response:
        fixture.loop_threads.append(threading.get_ident())
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=fixture.app, raise_app_exceptions=False),
            base_url="http://fused-generation.test",
        ) as client:
            kwargs: dict[str, Any] = {"headers": headers}
            if content is not None:
                kwargs["content"] = content
                kwargs["headers"] = {**headers, "Content-Type": "application/json"}
            elif body is not None:
                kwargs["json"] = body
            return await client.post("/api/fused-assets/generate", **kwargs)

    return asyncio.run(send())


def _task_api_request(
    fixture: AppFixture,
    method: str,
    path: str,
    *,
    body: dict[str, Any],
) -> httpx.Response:
    async def send() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=fixture.app, raise_app_exceptions=False),
            base_url="http://fused-generation.test",
        ) as client:
            return await client.request(
                method,
                path,
                headers={
                    "Authorization": f"Bearer {fixture.token_for('owner', False)}"
                },
                json=body,
            )

    return asyncio.run(send())


def _configure_openai_runtime(
    fixture: AppFixture, response: FakeProviderResponse
) -> tuple[FusedOpenAIImageRuntime, FakeProviderHttpClient, FakePublicMediaFetcher]:
    with fixture.engine.begin() as connection:
        connection.execute(
            update(system_models)
            .where(system_models.c.id == "system-model")
            .values(provider="openai", base_url="https://api.openai.com/v1")
        )
    client = FakeProviderHttpClient(response)
    media_fetcher = FakePublicMediaFetcher()
    runtime = FusedOpenAIImageRuntime(
        http_client_factory=lambda: FakeProviderHttpContext(client),
        public_media_fetcher=media_fetcher,
    )
    fixture.app = create_app(
        session_factory=fixture.business_factory,
        authentication_runtime=fixture.authentication_runtime,
        fused_generation_runtime=runtime,
        fused_generation_storage=fixture.storage,
        fused_generation_inline_image_storage=fixture.inline_image_storage,
    )
    return runtime, client, media_fetcher


def _synthetic_png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        payload = kind + data
        return (
            struct.pack(">I", len(data))
            + payload
            + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00\xff"))
        + chunk(b"IEND", b"")
    )


def _base64_response(image_bytes: bytes) -> FakeProviderResponse:
    encoded = base64.b64encode(image_bytes).decode("ascii")
    body = json.dumps({"data": [{"b64_json": encoded}]}).encode("utf-8")
    return FakeProviderResponse(200, body)


def _prepared_for_duplicate_execution(
    task_id: str,
    message_id: str,
) -> PreparedFusedGeneration:
    return PreparedFusedGeneration(
        task_id=task_id,
        message_id=message_id,
        user_id="owner",
        series_id="series-owner",
        chapter_id=None,
        member_type="character",
        members=({"kind": "asset", "id": "character-owner", "name": "林岚"},),
        name="角色融合 1合1",
        size=None,
        prompt="【角色资产对照】\n图片1 = 林岚",
        reference_images=("https://assets.test/lin.png",),
        model_id="system-model",
        model_name="系统融合模型",
        credit_cost=4,
        server_model=FusedServerImageModel(
            id="system-model",
            name="系统融合模型",
            cost_per_call=4,
            provider="openai",
            wire_model="image-v1",
            api_key=SERVER_KEY,
            base_url="https://api.openai.com/v1",
        ),
        target_frame=None,
    )


def _body(*, model_id: str = "system-model", prompt_id: str | None = None) -> dict[str, Any]:
    return {
        "series_id": "series-owner",
        "member_type": "character",
        "members": [{"kind": "asset", "id": "character-owner"}],
        "model_id": model_id,
        "prompt_id": prompt_id,
        "chapter_id": "chapter-owner",
        "frame_index": 0,
        "api_key": "attacker-supplied-key-is-ignored",
    }


def test_real_jwt_fused_task_commits_before_runtime_and_uses_server_config_and_wallet(
    fused_app: AppFixture,
):
    fixture = fused_app
    response = _request(
        fixture,
        token=fixture.token_for("owner", False),
        body=_body(prompt_id="prompt-system"),
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {"task_id", "message_id"}
    assert payload["task_id"] and payload["message_id"]
    assert fixture.runtime.requests[0].server_model.id == "system-model"
    assert fixture.runtime.requests[0].server_model.api_key == SERVER_KEY
    assert fixture.runtime.requests[0].prompt == (
        "保持画风\n突出神态\n【角色资产对照】\n图片1 = 林岚"
    )
    assert fixture.storage.urls == ["https://provider.test/fused-result.png"]

    auth_close = next(i for i, event in enumerate(fixture.events) if event[0] == "auth-close")
    business_open = next(i for i, event in enumerate(fixture.events) if event[0] == "business-open")
    assert auth_close < business_open
    assert all(
        event[1] != fixture.loop_threads[0]
        for event in fixture.events
        if event[0] in {"auth-open", "auth-close", "business-open", "business-close"}
    )
    assert all(session.info.get("closed") for session in fixture.business_sessions)

    with fixture.engine.connect() as connection:
        task = connection.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == payload["task_id"])
        ).mappings().one()
        message = connection.execute(
            select(chat_messages).where(chat_messages.c.id == payload["message_id"])
        ).mappings().one()
        asset = connection.execute(select(fused_assets)).mappings().one()
        balance = connection.execute(
            select(user_credits.c.credits).where(user_credits.c.user_id == "owner")
        ).scalar_one()
        logs = connection.execute(
            select(CreditLogRow.__table__).where(CreditLogRow.user_id == "owner")
        ).mappings().all()
        chapter_content = connection.execute(
            select(chapters.c.content).where(chapters.c.id == "chapter-owner")
        ).scalar_one()
    assert task["status"] == "completed"
    assert task["type"] == "fused"
    assert task["credit_cost"] == 4
    assert task["billing_status"] == "legacy_unreconciled"
    assert message["content"] == "https://provider.test/fused-result.png"
    assert asset["status"] == "completed"
    assert asset["image_url"] == "https://provider.test/fused-result.png"
    assert asset["name"] == "角色融合 1合1"
    assert balance == 16
    assert [(row["amount"], row["type"], row["description"]) for row in logs] == [
        (-4, "usage", "使用 系统融合模型 消耗积分")
    ]
    assert json.loads(chapter_content)[0]["fused"] == [asset["id"]]
    assert SERVER_KEY not in response.text


@pytest.mark.parametrize("wire_model", [None, ""], ids=["null", "empty"])
def test_nullable_server_wire_model_is_forwarded_after_normal_system_charge(
    fused_app: AppFixture,
    wire_model: str | None,
):
    fixture = fused_app
    with fixture.engine.begin() as connection:
        connection.execute(
            update(system_models)
            .where(system_models.c.id == "system-model")
            .values(model=wire_model)
        )

    response = _request(fixture, token=fixture.token_for("owner"), body=_body())

    assert response.status_code == 200, response.text
    assert len(fixture.runtime.requests) == 1
    assert fixture.runtime.requests[0].server_model.wire_model == wire_model
    with fixture.engine.connect() as connection:
        balance = connection.execute(
            select(user_credits.c.credits).where(user_credits.c.user_id == "owner")
        ).scalar_one()
        usage = connection.execute(
            select(CreditLogRow.__table__).where(CreditLogRow.type == "usage")
        ).mappings().all()
    assert balance == 16
    assert [(row["amount"], row["description"]) for row in usage] == [
        (-4, "使用 系统融合模型 消耗积分")
    ]


@pytest.mark.parametrize(
    ("case_name", "category", "name"),
    [
        ("url-name", "fusion", "https://参考.example"),
        ("empty-category", "", "有效融合模板"),
        ("empty-name", "fusion", ""),
    ],
    ids=["url-name", "empty-category", "empty-name"],
)
def test_owned_prompt_with_legacy_metadata_generates_and_charges_once(
    fused_app: AppFixture,
    case_name: str,
    category: str,
    name: str,
):
    fixture = fused_app
    prompt_id = f"owned-prompt-{case_name}"
    created_at = datetime(2026, 10, 15, 11, 0, 0)
    with fixture.engine.begin() as connection:
        connection.execute(
            insert(prompt_configs).values(
                id=prompt_id,
                user_id="owner",
                category=category,
                name=name,
                system_prompt="  系统模板内部  ",
                user_prompt="  用户模板内部  ",
                sort_order=1,
                created_at=created_at,
                updated_at=created_at,
            )
        )

    response = _request(
        fixture,
        token=fixture.token_for("owner"),
        body=_body(prompt_id=prompt_id),
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {"task_id", "message_id"}
    assert len(fixture.runtime.requests) == 1
    assert fixture.runtime.requests[0].prompt == (
        "系统模板内部  \n  用户模板内部  \n【角色资产对照】\n图片1 = 林岚"
    )
    with fixture.engine.connect() as connection:
        task = connection.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == payload["task_id"])
        ).mappings().one()
        message = connection.execute(
            select(chat_messages).where(chat_messages.c.id == payload["message_id"])
        ).mappings().one()
        assets = connection.execute(
            select(fused_assets).where(fused_assets.c.series_id == "series-owner")
        ).mappings().all()
        balance = connection.execute(
            select(user_credits.c.credits).where(user_credits.c.user_id == "owner")
        ).scalar_one()
        usage = connection.execute(
            select(CreditLogRow.__table__).where(
                CreditLogRow.user_id == "owner",
                CreditLogRow.type == "usage",
            )
        ).mappings().all()
    assert task["status"] == "completed"
    assert task["credit_cost"] == 4
    assert message["content"] == "https://provider.test/fused-result.png"
    assert len(assets) == 1 and assets[0]["status"] == "completed"
    assert balance == 16
    assert [(row["amount"], row["description"]) for row in usage] == [
        (-4, "使用 系统融合模型 消耗积分")
    ]


def test_authentication_precedes_invalid_json_and_missing_runtime_writes_nothing(
    fused_app: AppFixture,
):
    fixture = fused_app
    invalid_json = b"{"

    missing = _request(fixture, token=None, content=invalid_json)
    assert missing.status_code == 401
    assert missing.headers.get("www-authenticate") == "Bearer"
    free = _request(fixture, token=fixture.token_for("free", False), content=invalid_json)
    assert free.status_code == 403
    expired_membership = _request(
        fixture,
        token=fixture.token_for("membership-expired", False),
        content=invalid_json,
    )
    assert expired_membership.status_code == 403
    expired_token = _request(
        fixture,
        token=fixture.token_for("owner", True),
        content=invalid_json,
    )
    assert expired_token.status_code == 401
    assert expired_token.headers.get("www-authenticate") == "Bearer"
    valid_bad_body = _request(fixture, token=fixture.token_for("owner", False), content=invalid_json)
    assert valid_bad_body.status_code == 422
    assert fixture.business_sessions == []

    cold = create_app(
        session_factory=fixture.business_factory,
        authentication_runtime=fixture.authentication_runtime,
    )
    async def request_cold():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=cold, raise_app_exceptions=False),
            base_url="http://fused-generation-cold.test",
        ) as client:
            return await client.post(
                "/api/fused-assets/generate",
                content=invalid_json,
                headers={
                    "Authorization": f"Bearer {fixture.token_for('owner', False)}",
                    "Content-Type": "application/json",
                },
            )
    unavailable = asyncio.run(request_cold())
    assert unavailable.status_code == 503
    assert unavailable.json() == {"detail": "融合生成服务尚未接线"}
    assert fixture.business_sessions == []

    with fixture.engine.begin() as connection:
        connection.execute(
            update(user_sessions)
            .where(user_sessions.c.user_id == "owner")
            .values(is_active=False, revoked_at=datetime(2026, 10, 15, 12, 0, 0))
        )
    revoked_session = _request(
        fixture,
        token=fixture.token_for("owner", False),
        content=invalid_json,
    )
    assert revoked_session.status_code == 401
    assert revoked_session.headers.get("www-authenticate") == "Bearer"
    assert fixture.business_sessions == []
    assert all(session.info.get("closed") for session in fixture.auth_sessions)


def test_invalid_prompt_keeps_legacy_independent_system_charge_order(fused_app: AppFixture):
    fixture = fused_app
    response = _request(
        fixture,
        token=fixture.token_for("owner", False),
        body=_body(prompt_id="missing-prompt"),
    )
    assert response.status_code == 400
    assert "提示词无法引用" in response.json()["detail"]
    assert fixture.business_sessions and all(session.info.get("closed") for session in fixture.business_sessions)
    with fixture.engine.connect() as connection:
        balance = connection.execute(
            select(user_credits.c.credits).where(user_credits.c.user_id == "owner")
        ).scalar_one()
        usage = connection.execute(
            select(CreditLogRow.__table__).where(CreditLogRow.type == "usage")
        ).mappings().all()
        tasks = connection.execute(select(AITaskRow.__table__)).mappings().all()
        messages = connection.execute(select(chat_messages)).mappings().all()
    assert balance == 16
    assert [(row["amount"], row["description"]) for row in usage] == [
        (-4, "使用 系统融合模型 消耗积分")
    ]
    assert tasks == []
    assert messages == []


def test_invalid_model_cipher_secret_fails_before_fee_task_or_provider(fused_app: AppFixture):
    fixture = fused_app
    now = datetime(2026, 10, 15, 12, 0, 0)
    wrong_secret = "incorrect-local-secret"
    runtime = AuthenticationRuntime(
        session_factory=fixture.authentication_runtime.session_factory,
        secret_key=wrong_secret,
        clock=lambda: now,
    )
    codec = JoseTokenCodec(wrong_secret)
    token = codec.encode(
        {
            "sub": "owner",
            "jti": "jti-owner",
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        }
    )
    app = create_app(
        session_factory=fixture.business_factory,
        authentication_runtime=runtime,
        fused_generation_runtime=fixture.runtime,
        fused_generation_storage=fixture.storage,
    )
    fixture.app = app
    response = _request(fixture, token=token, body=_body())

    assert response.status_code == 503
    assert response.json() == {"detail": "模型凭据不可用，请联系管理员"}
    assert fixture.runtime.requests == []
    assert fixture.storage.urls == []
    with fixture.engine.connect() as connection:
        balance = connection.execute(
            select(user_credits.c.credits).where(user_credits.c.user_id == "owner")
        ).scalar_one()
        tasks = connection.execute(select(AITaskRow.__table__)).mappings().all()
        logs = connection.execute(select(CreditLogRow.__table__)).mappings().all()
        messages = connection.execute(select(chat_messages)).mappings().all()
    assert balance == 20
    assert tasks == []
    assert logs == []
    assert messages == []
    assert all(session.info.get("closed") for session in fixture.business_sessions)


def test_owned_model_does_not_change_wallet_or_create_usage_log(fused_app: AppFixture):
    fixture = fused_app
    response = _request(
        fixture,
        token=fixture.token_for("owner", False),
        body=_body(model_id="owned-model"),
    )
    assert response.status_code == 200, response.text
    assert fixture.runtime.requests[-1].server_model.id == "owned-model"
    assert fixture.runtime.requests[-1].server_model.is_system is False
    assert fixture.runtime.requests[-1].server_model.api_key == "owned-secret"
    with fixture.engine.connect() as connection:
        balance = connection.execute(
            select(user_credits.c.credits).where(user_credits.c.user_id == "owner")
        ).scalar_one()
        logs = connection.execute(select(CreditLogRow.__table__)).mappings().all()
    assert balance == 20
    assert logs == []


def test_sql_member_filter_keeps_descriptors_but_maps_only_scoped_images(fused_app: AppFixture):
    fixture = fused_app
    body = _body()
    body["members"] = [
        {"kind": "asset", "id": "character-no-image"},
        {"kind": "asset", "id": "character-foreign"},
        {"kind": "asset", "id": "character-owner"},
    ]
    response = _request(fixture, token=fixture.token_for("owner", False), body=body)

    assert response.status_code == 200, response.text
    request = fixture.runtime.requests[0]
    assert [member["id"] for member in request.members] == [
        "character-no-image",
        "character-owner",
    ]
    assert request.reference_images == ("https://assets.test/lin.png",)
    assert request.prompt == "【角色资产对照】\n图片1 = 林岚"


@pytest.mark.parametrize(
    ("body_changes", "expected_status", "detail"),
    [
        ({"members": [{"kind": "asset", "id": "character-no-image"}]}, 400, "都没有图片"),
        ({"chapter_id": "chapter-foreign"}, 400, "章节不属于当前剧集"),
        ({"series_id": "series-foreign", "members": [{"kind": "asset", "id": "character-foreign"}]}, 403, "无权"),
    ],
)
def test_sql_rejections_precede_fee_and_task_writes(
    fused_app: AppFixture,
    body_changes: dict[str, Any],
    expected_status: int,
    detail: str,
):
    fixture = fused_app
    body = _body()
    body.update(body_changes)

    response = _request(fixture, token=fixture.token_for("owner", False), body=body)

    assert response.status_code == expected_status, response.text
    assert detail in response.json()["detail"]
    with fixture.engine.connect() as connection:
        balance = connection.execute(
            select(user_credits.c.credits).where(user_credits.c.user_id == "owner")
        ).scalar_one()
        tasks = connection.execute(select(AITaskRow.__table__)).mappings().all()
        logs = connection.execute(select(CreditLogRow.__table__)).mappings().all()
        messages = connection.execute(select(chat_messages)).mappings().all()
    assert balance == 20
    assert tasks == []
    assert logs == []
    assert messages == []


def test_real_http_insufficient_credits_returns_402_without_task_or_usage_log(
    fused_app: AppFixture,
):
    fixture = fused_app
    with fixture.engine.begin() as connection:
        connection.execute(
            update(user_credits)
            .where(user_credits.c.user_id == "owner")
            .values(credits=3)
        )

    response = _request(fixture, token=fixture.token_for("owner", False), body=_body())

    assert response.status_code == 402
    assert "需要 4 积分" in response.json()["detail"]
    with fixture.engine.connect() as connection:
        balance = connection.execute(
            select(user_credits.c.credits).where(user_credits.c.user_id == "owner")
        ).scalar_one()
        tasks = connection.execute(select(AITaskRow.__table__)).mappings().all()
        logs = connection.execute(select(CreditLogRow.__table__)).mappings().all()
    assert balance == 3
    assert tasks == []
    assert logs == []


def test_real_http_provider_failure_refunds_once_and_sanitizes_persisted_error(
    fused_app: AppFixture,
):
    fixture = fused_app
    fixture.runtime.failure = ProviderGenerationFailure(f"上游拒绝 {SERVER_KEY}")

    response = _request(fixture, token=fixture.token_for("owner", False), body=_body())

    assert response.status_code == 200, response.text
    task_id = response.json()["task_id"]
    with fixture.engine.connect() as connection:
        task = connection.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == task_id)
        ).mappings().one()
        message = connection.execute(
            select(chat_messages).where(chat_messages.c.id == response.json()["message_id"])
        ).mappings().one()
        asset = connection.execute(select(fused_assets)).mappings().one()
        balance = connection.execute(
            select(user_credits.c.credits).where(user_credits.c.user_id == "owner")
        ).scalar_one()
        logs = connection.execute(
            select(CreditLogRow.__table__).where(CreditLogRow.user_id == "owner")
        ).mappings().all()
        chapter_content = connection.execute(
            select(chapters.c.content).where(chapters.c.id == "chapter-owner")
        ).scalar_one()
    assert task["status"] == "failed"
    assert task["result"] == "上游拒绝 [已隐藏]"
    assert message["content"] == "上游拒绝 [已隐藏]"
    assert asset["status"] == "failed"
    assert asset["error_message"] == "上游拒绝 [已隐藏]"
    fused_ids = json.loads(chapter_content)[0]["fused"]
    assert fused_ids == [asset["id"]]
    assert balance == 20
    assert {
        (row["amount"], row["type"], row["description"]) for row in logs
    } == {
        (-4, "usage", "使用 系统融合模型 消耗积分"),
        (4, "refund", "任务失败退回 4 积分"),
    }
    assert SERVER_KEY not in response.text


def test_real_openai_runtime_url_success_creates_asset_once_through_jwt_route(
    fused_app: AppFixture,
):
    fixture = fused_app
    provider_url = "https://cdn.openai.example/fused-result.png"
    body = json.dumps({"data": [{"url": provider_url}]}).encode("utf-8")
    runtime, client, fetcher = _configure_openai_runtime(
        fixture, FakeProviderResponse(200, body)
    )

    response = _request(fixture, token=fixture.token_for("owner", False), body=_body())

    assert response.status_code == 200, response.text
    task_id = response.json()["task_id"]
    assert len(client.calls) == 1
    method, url, kwargs = client.calls[0]
    assert method == "POST"
    assert url == "https://api.openai.com/v1/images/edits"
    assert kwargs["follow_redirects"] is False
    assert kwargs["headers"]["Authorization"] == f"Bearer {SERVER_KEY}"
    assert kwargs["content"].count(b'name="image"') == 1
    assert fetcher.calls
    assert fetcher.calls[0][0].locator == "https://assets.test/lin.png"
    assert fixture.storage.urls == [provider_url]

    with fixture.engine.connect() as connection:
        task = connection.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == task_id)
        ).mappings().one()
        assets = connection.execute(select(fused_assets)).mappings().all()
        logs = connection.execute(
            select(CreditLogRow.__table__).where(CreditLogRow.user_id == "owner")
        ).mappings().all()
        balance = connection.execute(
            select(user_credits.c.credits).where(user_credits.c.user_id == "owner")
        ).scalar_one()
        chapter_content = connection.execute(
            select(chapters.c.content).where(chapters.c.id == "chapter-owner")
        ).scalar_one()
    assert task["status"] == "completed"
    assert task["claim_token"] is None
    assert task["recovery_status"] == "ready"
    assert len(assets) == 1 and assets[0]["status"] == "completed"
    assert assets[0]["image_url"] == provider_url
    assert len(logs) == 1 and logs[0]["type"] == "usage"
    assert balance == 16
    assert json.loads(chapter_content)[0]["fused"] == [assets[0]["id"]]

    duplicate = _prepared_for_duplicate_execution(
        task_id,
        response.json()["message_id"],
    )
    asyncio.run(
        FusedGenerationExecution(
            runtime,
            fused_generation_uow_factory(fixture.business_factory),
            fixture.storage,
        ).execute(duplicate)
    )
    assert len(client.calls) == 1


def test_real_openai_base64_result_uses_owned_storage_before_asset_success(
    fused_app: AppFixture,
):
    fixture = fused_app
    image_bytes = _synthetic_png()
    runtime, client, fetcher = _configure_openai_runtime(
        fixture,
        _base64_response(image_bytes),
    )

    response = _request(fixture, token=fixture.token_for("owner", False), body=_body())

    assert response.status_code == 200, response.text
    task_id = response.json()["task_id"]
    assert len(client.calls) == 1
    assert fixture.storage.urls == []
    assert fetcher.calls
    assert len(fixture.inline_storage_state.calls) == 1
    assert len(fixture.inline_storage_state.stored_media) == 1
    storage_call = fixture.inline_storage_state.calls[0]
    stored = fixture.inline_storage_state.stored_media[0]
    assert storage_call["body"] == image_bytes
    assert storage_call["content_type"] == "image/png"
    assert storage_call["bucket"] == "fused-test-media"
    assert stored.content_digest == hashlib.sha256(image_bytes).hexdigest()
    assert stored.size_bytes == len(image_bytes)
    assert stored.media_type == "image/png"
    assert stored.locator == (
        f"https://cdn.example.test/media/{storage_call['key']}"
    )

    with fixture.engine.connect() as connection:
        task = connection.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == task_id)
        ).mappings().one()
        message = connection.execute(
            select(chat_messages).where(chat_messages.c.id == task["message_id"])
        ).mappings().one()
        assets = connection.execute(select(fused_assets)).mappings().all()
        wallet = connection.execute(
            select(user_credits.c.credits).where(user_credits.c.user_id == "owner")
        ).scalar_one()
        logs = connection.execute(
            select(CreditLogRow.__table__).where(CreditLogRow.user_id == "owner")
        ).mappings().all()
        chapter_content = connection.execute(
            select(chapters.c.content).where(chapters.c.id == "chapter-owner")
        ).scalar_one()
    assert task["status"] == "completed"
    assert task["claim_token"] is None
    assert message["content"] == stored.locator
    assert len(assets) == 1
    assert assets[0]["status"] == "completed"
    assert assets[0]["image_url"] == stored.locator
    assert wallet == 16
    assert len(logs) == 1 and logs[0]["type"] == "usage"
    assert json.loads(chapter_content)[0]["fused"] == [assets[0]["id"]]

    duplicate = _prepared_for_duplicate_execution(task_id, task["message_id"])
    asyncio.run(
        FusedGenerationExecution(
            runtime,
            fused_generation_uow_factory(fixture.business_factory),
            inline_image_storage=fixture.inline_image_storage,
        ).execute(duplicate)
    )
    assert len(client.calls) == 1
    assert len(fixture.inline_storage_state.calls) == 1


@pytest.mark.parametrize(
    "storage_mode",
    ["ack_unknown", "wrong_receipt", "cleanup_failure"],
    ids=["write-ack-unknown", "wrong-receipt-key", "sdk-cleanup-failure"],
)
def test_unverified_inline_storage_retains_claim_without_refund_or_retry(
    fused_app: AppFixture,
    storage_mode: str,
):
    fixture = fused_app
    fixture.inline_storage_state.mode = storage_mode
    runtime, client, _ = _configure_openai_runtime(
        fixture,
        _base64_response(_synthetic_png()),
    )

    response = _request(fixture, token=fixture.token_for("owner", False), body=_body())

    assert response.status_code == 200, response.text
    task_id = response.json()["task_id"]
    assert len(client.calls) == 1
    assert len(fixture.inline_storage_state.calls) == 1
    assert fixture.inline_storage_state.stored_media == []
    with fixture.engine.connect() as connection:
        task = connection.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == task_id)
        ).mappings().one()
        assets = connection.execute(select(fused_assets)).mappings().all()
        wallet = connection.execute(
            select(user_credits.c.credits).where(user_credits.c.user_id == "owner")
        ).scalar_one()
        logs = connection.execute(
            select(CreditLogRow.__table__).where(CreditLogRow.user_id == "owner")
        ).mappings().all()
    assert task["status"] == "processing"
    assert task["claim_token"]
    assert task["recovery_status"] == "provider_result_unknown"
    assert task["result"] == "图片已返回，媒体保存结果待核对"
    assert task["progress_message"] == "图片已返回，保存结果待核对"
    assert assets == []
    assert wallet == 16
    assert [(row["amount"], row["type"]) for row in logs] == [(-4, "usage")]

    duplicate = _prepared_for_duplicate_execution(task_id, task["message_id"])
    asyncio.run(
        FusedGenerationExecution(
            runtime,
            fused_generation_uow_factory(fixture.business_factory),
            inline_image_storage=fixture.inline_image_storage,
        ).execute(duplicate)
    )
    assert len(client.calls) == 1
    assert len(fixture.inline_storage_state.calls) == 1


def test_cancellation_during_real_owned_media_write_drains_and_retains_task(
    fused_app: AppFixture,
):
    fixture = fused_app
    fixture.inline_storage_state.mode = "storage_block"
    runtime, client, _ = _configure_openai_runtime(
        fixture,
        _base64_response(_synthetic_png()),
    )

    async def send_and_cancel() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=fixture.app, raise_app_exceptions=False),
            base_url="http://fused-generation.test",
        ) as client_session:
            request = asyncio.create_task(
                client_session.post(
                    "/api/fused-assets/generate",
                    headers={
                        "Authorization": f"Bearer {fixture.token_for('owner', False)}"
                    },
                    json=_body(),
                )
            )
            assert await asyncio.to_thread(fixture.inline_storage_state.started.wait, 3)
            request.cancel()
            await asyncio.sleep(0)
            assert not request.done()
            fixture.inline_storage_state.release.set()
            with pytest.raises(asyncio.CancelledError):
                await request

    asyncio.run(send_and_cancel())

    assert len(client.calls) == 1
    assert len(fixture.inline_storage_state.calls) == 1
    assert len(fixture.inline_storage_state.stored_media) == 1
    with fixture.engine.connect() as connection:
        task = connection.execute(select(AITaskRow.__table__)).mappings().one()
        assets = connection.execute(select(fused_assets)).mappings().all()
        wallet = connection.execute(
            select(user_credits.c.credits).where(user_credits.c.user_id == "owner")
        ).scalar_one()
        logs = connection.execute(
            select(CreditLogRow.__table__).where(CreditLogRow.user_id == "owner")
        ).mappings().all()
    assert task["status"] == "processing"
    assert task["claim_token"]
    assert task["recovery_status"] == "provider_result_unknown"
    assert task["result"] == "图片已返回，媒体保存结果待核对"
    assert assets == []
    assert wallet == 16
    assert [(row["amount"], row["type"]) for row in logs] == [(-4, "usage")]

    duplicate = _prepared_for_duplicate_execution(task["id"], task["message_id"])
    asyncio.run(
        FusedGenerationExecution(
            runtime,
            fused_generation_uow_factory(fixture.business_factory),
            inline_image_storage=fixture.inline_image_storage,
        ).execute(duplicate)
    )
    assert len(client.calls) == 1
    assert len(fixture.inline_storage_state.calls) == 1


def test_real_openai_runtime_unknown_is_retained_and_generic_writes_are_guarded(
    fused_app: AppFixture,
):
    fixture = fused_app
    runtime, client, _ = _configure_openai_runtime(
        fixture, FakeProviderResponse(500, b"provider response with private detail")
    )

    response = _request(fixture, token=fixture.token_for("owner", False), body=_body())

    assert response.status_code == 200, response.text
    task_id = response.json()["task_id"]
    assert len(client.calls) == 1
    assert fixture.storage.urls == []

    with fixture.engine.connect() as connection:
        task_before = connection.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == task_id)
        ).mappings().one()
        assets_before = connection.execute(select(fused_assets)).mappings().all()
        logs_before = connection.execute(
            select(CreditLogRow.__table__).where(CreditLogRow.user_id == "owner")
        ).mappings().all()
        balance_before = connection.execute(
            select(user_credits.c.credits).where(user_credits.c.user_id == "owner")
        ).scalar_one()
        chapter_before = connection.execute(
            select(chapters.c.content).where(chapters.c.id == "chapter-owner")
        ).scalar_one()
    claim_token = task_before["claim_token"]
    assert task_before["status"] == "processing"
    assert task_before["recovery_status"] == "provider_result_unknown"
    assert task_before["result"] == "图片生成结果待核对，请勿重新提交"
    assert task_before["progress_message"] == "图片生成结果待核对"
    assert claim_token
    assert json.loads(task_before["request_data"])["server_controlled_execution"] == (
        "fused_generation_runtime_v1"
    )
    assert assets_before == []
    assert len(logs_before) == 1 and logs_before[0]["type"] == "usage"
    assert balance_before == 16
    assert json.loads(chapter_before)[0]["fused"] == []

    status_update = _task_api_request(
        fixture,
        "PUT",
        f"/api/tasks/{task_id}",
        body={"status": "failed", "result": "late generic status"},
    )
    progress_update = _task_api_request(
        fixture,
        "PUT",
        f"/api/tasks/{task_id}/progress",
        body={"progress": 100, "progress_message": "late generic progress"},
    )
    assert status_update.status_code == 409, status_update.text
    assert progress_update.status_code == 409, progress_update.text

    duplicate = _prepared_for_duplicate_execution(
        task_id,
        response.json()["message_id"],
    )
    asyncio.run(
        FusedGenerationExecution(
            runtime,
            fused_generation_uow_factory(fixture.business_factory),
            fixture.storage,
        ).execute(duplicate)
    )
    assert len(client.calls) == 1

    legacy_create = _task_api_request(
        fixture,
        "POST",
        "/api/tasks",
        body={"type": "fused", "credit_cost": 0, "request_data": "{}"},
    )
    assert legacy_create.status_code == 200, legacy_create.text
    legacy_task_id = legacy_create.json()["id"]
    legacy_status = _task_api_request(
        fixture,
        "PUT",
        f"/api/tasks/{legacy_task_id}",
        body={"status": "failed", "result": "legacy update"},
    )
    legacy_progress = _task_api_request(
        fixture,
        "PUT",
        f"/api/tasks/{legacy_task_id}/progress",
        body={"progress": 31, "progress_message": "legacy progress"},
    )
    assert legacy_status.status_code == 200, legacy_status.text
    assert legacy_progress.status_code == 200, legacy_progress.text

    with fixture.engine.connect() as connection:
        task_after = connection.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == task_id)
        ).mappings().one()
        assets_after = connection.execute(select(fused_assets)).mappings().all()
        logs_after = connection.execute(
            select(CreditLogRow.__table__).where(CreditLogRow.user_id == "owner")
        ).mappings().all()
        balance_after = connection.execute(
            select(user_credits.c.credits).where(user_credits.c.user_id == "owner")
        ).scalar_one()
    assert task_after["status"] == "processing"
    assert task_after["claim_token"] == claim_token
    assert task_after["recovery_status"] == "provider_result_unknown"
    assert task_after["progress_message"] == "图片生成结果待核对"
    assert assets_after == []
    assert len(logs_after) == 1 and logs_after[0]["type"] == "usage"
    assert balance_after == 16
