"""Real JWT and SQLite integration for the three chat media admission routes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest
from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.app import create_app
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.tables import user_sessions, users
from haoai_backend.authentication.tokens import JoseTokenCodec
from haoai_backend.chat_data.tables import chat_messages
from haoai_backend.chat_media_admission import ChatMediaAdmissionConfiguration
from haoai_backend.generation_catalog.admission import AdmissionPolicy
from haoai_backend.generation_catalog.domain import ModelSource
from haoai_backend.generation_catalog.tables import chapters as catalog_chapters
from haoai_backend.generation_catalog.tables import system_models
from haoai_backend.generation_core.admission_receipts import (
    AdmissionCommitUnknown,
    AdmissionConfirmed,
)
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    UserCreditRow,
)
from haoai_backend.generation_credentials import snapshot_producer
from generation_core.test_admission_staging import (
    _G2_JOINT_ACTOR_ID,
    _G2_JOINT_MODEL_ID,
    _G2_JOINT_NOW,
    _JointIdFactory,
    _joint_database,
    _joint_registered_capabilities,
)

SECRET = "chat-media-admission-integration-synthetic-secret"
JTI = "chat-media-admission-integration-session"
AUTH_SESSION_ID = "chat-media-admission-integration-auth-session"
BASE_URL = "http://chat-media-admission.test"
BusinessCloseGate = tuple[threading.Event, threading.Event, threading.Event]


class _ObservedSession(Session):
    def execute(self, *args: Any, **kwargs: Any) -> Any:
        self.info["events"].append(
            (f"execute:{self.info['kind']}", threading.get_ident())
        )
        return super().execute(*args, **kwargs)

    def close(self) -> None:
        if not self.info.get("observed_closed"):
            self.info["observed_closed"] = True
            self.info["events"].append(
                (f"close:{self.info['kind']}", threading.get_ident())
            )
        close_gate = self.info.pop("close_gate", None)
        if close_gate is not None:
            close_started, close_release = close_gate
            close_started.set()
            close_release.wait(timeout=5)
        super().close()
        self.info["close_count"] = int(self.info.get("close_count", 0)) + 1
        close_finished = self.info.get("close_finished")
        if isinstance(close_finished, threading.Event):
            close_finished.set()

    def commit(self) -> None:
        self.info["commit_calls"] = int(self.info.get("commit_calls", 0)) + 1
        super().commit()
        state = self.info.get("ack_loss_state")
        if isinstance(state, dict) and state.get("enabled") and not state.get("used"):
            state["used"] = True
            raise RuntimeError("synthetic lost commit acknowledgement")


@dataclass
class _System:
    engine: Engine
    app: Any
    events: list[tuple[str, int]]
    auth_sessions: list[_ObservedSession]
    business_sessions: list[_ObservedSession]
    token_for: Callable[..., str]


def _build_system(
    tmp_path: Path,
    *,
    operation_category: str = "image",
    with_configuration: bool = True,
    lose_first_business_commit_ack: bool = False,
    business_close_gate: BusinessCloseGate | None = None,
) -> _System:
    engine = _joint_database(
        tmp_path,
        model_source=ModelSource.SYSTEM,
        wallet_credits=500,
    )
    capabilities, _crypto_state = _joint_registered_capabilities(
        engine,
        model_source=ModelSource.SYSTEM,
    )
    events: list[tuple[str, int]] = []
    auth_sessions: list[_ObservedSession] = []
    business_sessions: list[_ObservedSession] = []
    ack_loss_state = {"enabled": lose_first_business_commit_ack, "used": False}

    with engine.begin() as connection:
        connection.execute(
            update(users)
            .where(users.c.id == _G2_JOINT_ACTOR_ID)
            .values(is_superuser=False)
        )
        connection.execute(
            insert(user_sessions).values(
                id=AUTH_SESSION_ID,
                user_id=_G2_JOINT_ACTOR_ID,
                jti=JTI,
                user_agent="local chat media integration",
                ip_address="127.0.0.1",
                device_name="synthetic test",
                is_active=True,
                created_at=_G2_JOINT_NOW.replace(tzinfo=None),
                last_seen_at=_G2_JOINT_NOW.replace(tzinfo=None),
                revoked_at=None,
            )
        )
        if operation_category != "image":
            connection.execute(
                update(system_models)
                .where(system_models.c.id == _G2_JOINT_MODEL_ID)
                .values(category=operation_category)
            )

    def make_factory(
        kind: str,
        bucket: list[_ObservedSession],
        *,
        use_ack_loss: bool = False,
    ) -> Callable[[], _ObservedSession]:
        maker = sessionmaker(
            engine,
            class_=_ObservedSession,
            expire_on_commit=False,
            info={
                "kind": kind,
                "events": events,
                "ack_loss_state": ack_loss_state if use_ack_loss else None,
                "close_gate": (
                    business_close_gate[:2]
                    if kind == "business" and business_close_gate is not None
                    else None
                ),
                "close_finished": (
                    business_close_gate[2]
                    if kind == "business" and business_close_gate is not None
                    else None
                ),
            },
        )

        def factory() -> _ObservedSession:
            events.append((f"open:{kind}", threading.get_ident()))
            session = maker()
            bucket.append(session)
            return session

        return factory

    auth_factory = make_factory("auth", auth_sessions)
    business_factory = make_factory(
        "business", business_sessions, use_ack_loss=True
    )
    runtime = AuthenticationRuntime(
        session_factory=auth_factory,
        secret_key=SECRET,
        token_codec=JoseTokenCodec(SECRET),
        clock=lambda: _G2_JOINT_NOW,
    )
    configuration = None
    if with_configuration:
        policies = {
            operation: AdmissionPolicy(max_concurrent_tasks=0)
            for operation in ("image.single", "video.single", "image.batch")
        }
        configuration = ChatMediaAdmissionConfiguration(
            registry_capabilities=capabilities,
            server_id_factory=_JointIdFactory("chat-media-server-id"),
            policies=policies,
            versions=snapshot_producer.SnapshotVersions(
                projector="chat-media-fixture-projector-v1",
                reference_policy="chat-media-fixture-reference-v1",
            ),
        )
    app = create_app(
        session_factory=business_factory,
        authentication_runtime=runtime,
        chat_media_admission_configuration=configuration,
    )
    codec = JoseTokenCodec(SECRET)

    def token_for(
        *,
        expired: bool = False,
        jti: str = JTI,
        password_version: str | None = None,
    ) -> str:
        expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        if expired:
            expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        claims: dict[str, object] = {
            "sub": _G2_JOINT_ACTOR_ID,
            "jti": jti,
            "exp": expires_at,
        }
        if password_version is not None:
            claims["password_version"] = password_version
        return codec.encode(claims)

    return _System(
        engine=engine,
        app=app,
        events=events,
        auth_sessions=auth_sessions,
        business_sessions=business_sessions,
        token_for=token_for,
    )


def _post(
    system: _System,
    path: str,
    *,
    body: object | None = None,
    raw: bytes | None = None,
    key: str | None = "chat-media-idempotency-key-01",
    token: str | None = None,
) -> httpx.Response:
    async def send() -> httpx.Response:
        headers: dict[str, str] = {}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        else:
            headers["Authorization"] = f"Bearer {system.token_for()}"
        if key is not None:
            headers["Idempotency-Key"] = key
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=system.app, raise_app_exceptions=False),
            base_url=BASE_URL,
        ) as client:
            if raw is not None:
                headers["Content-Type"] = "application/json"
                return await client.post(path, headers=headers, content=raw)
            return await client.post(path, headers=headers, json=body)

    return asyncio.run(send())


def _body(operation: str) -> dict[str, object]:
    if operation == "image.single":
        return {
            "model_id": _G2_JOINT_MODEL_ID,
            "prompt": "A quiet room at sunrise",
            "target": {"kind": "standalone"},
            "size": "1024x1024",
            "reference_image": "https://references.example.test/one.png",
        }
    if operation == "video.single":
        return {
            "model_id": _G2_JOINT_MODEL_ID,
            "prompt": "A slow camera pan",
            "target": {"kind": "standalone"},
            "duration": 5,
            "resolution": "720p",
            "reference_images": [],
            "reference_audios": [],
            "reference_videos": [],
        }
    if operation == "image.batch":
        return {
            "chapter_id": "joint-chapter-1",
            "assets": [
                {
                    "type": "character",
                    "id": "joint-character-1",
                    "name": "Fixture character",
                    "description": "First ordered description",
                    "model_id": _G2_JOINT_MODEL_ID,
                    "prompt_id": "",
                    "size": "1024x1024",
                    "reference_images": [],
                    "client_api_key": "ignored by legacy batch DTO",
                },
                {
                    "type": "scene",
                    "id": "joint-scene-1",
                    "name": "Fixture scene",
                    "description": "Second ordered description",
                    "model_id": _G2_JOINT_MODEL_ID,
                    "prompt_id": "",
                    "size": "1024x1024",
                    "reference_images": [],
                },
            ],
            "client_api_key": "ignored by legacy batch DTO",
        }
    raise AssertionError(operation)


@pytest.mark.parametrize(
    ("path", "operation", "category"),
    (
        ("/api/chat/image", "image.single", "image"),
        ("/api/chat/video", "video.single", "video"),
        ("/api/chat/batch-generate-images", "image.batch", "image"),
    ),
)
def test_real_jwt_sqlite_producer_stages_each_route_and_confirms_fresh_receipt(
    tmp_path: Path,
    path: str,
    operation: str,
    category: str,
) -> None:
    system = _build_system(tmp_path, operation_category=category)
    try:
        main_thread = threading.get_ident()
        response = _post(system, path, body=_body(operation))

        assert response.status_code == 200, response.text
        payload = response.json()
        if operation == "image.batch":
            assert set(payload) == {"tasks"}
            assert all(
                set(item)
                == {"type", "asset_id", "asset_name", "message_id", "task_id"}
                for item in payload["tasks"]
            )
            assert [
                {
                    "type": item["type"],
                    "asset_id": item["asset_id"],
                    "asset_name": item["asset_name"],
                }
                for item in payload["tasks"]
            ] == [
                {
                    "type": "character",
                    "asset_id": "joint-character-1",
                    "asset_name": "Fixture character",
                },
                {
                    "type": "scene",
                    "asset_id": "joint-scene-1",
                    "asset_name": "Fixture scene",
                },
            ]
            message_ids = [item["message_id"] for item in payload["tasks"]]
            task_ids = [item["task_id"] for item in payload["tasks"]]
            assert all(isinstance(value, str) and value for value in message_ids)
            assert all(isinstance(value, str) and value for value in task_ids)
            assert len(set(message_ids)) == len(message_ids)
            assert len(set(task_ids)) == len(task_ids)
            expected_tasks, expected_messages, expected_credits = 2, 4, 494
        else:
            assert set(payload) == {
                "submission_id",
                "operation",
                "task_id",
                "message_id",
                "status",
                "quoted_amount",
                "billing_status",
            }
            assert payload["operation"] == operation
            assert payload["status"] == "queued"
            assert payload["quoted_amount"] == 3
            assert payload["billing_status"] == "charged"
            expected_tasks, expected_messages, expected_credits = 1, 0, 497

        with system.engine.connect() as connection:
            tasks = connection.execute(
                select(AITaskRow.__table__)
            ).mappings().all()
            units = connection.execute(
                select(BillingUnitRow.__table__)
            ).mappings().all()
            credits = connection.execute(
                select(UserCreditRow.__table__.c.credits)
            ).scalar_one()
            ledger = connection.execute(
                select(CreditLogRow.__table__)
            ).mappings().all()
            messages = connection.execute(select(chat_messages)).mappings().all()
        assert len(tasks) == expected_tasks
        assert len(units) == expected_tasks
        assert len(ledger) == expected_tasks
        assert len(messages) == expected_messages
        assert credits == expected_credits
        assert sum(row["amount"] for row in ledger) == -3 * expected_tasks
        assert all(row["user_id"] == _G2_JOINT_ACTOR_ID for row in tasks)
        assert all(row["billing_status"] == "charged" for row in units)

        first_auth_close = next(
            index for index, event in enumerate(system.events) if event[0] == "close:auth"
        )
        first_business_open = next(
            index for index, event in enumerate(system.events) if event[0] == "open:business"
        )
        first_business_sql = next(
            index for index, event in enumerate(system.events) if event[0] == "execute:business"
        )
        writer_close = next(
            index for index, event in enumerate(system.events) if event[0] == "close:business"
        )
        receipt_open = next(
            index
            for index, event in enumerate(system.events)
            if event[0] == "open:business" and index > writer_close
        )
        auth_close_indexes = [
            index
            for index, event in enumerate(system.events)
            if event[0] == "close:auth"
        ]
        assert auth_close_indexes
        assert max(auth_close_indexes) < first_business_sql
        assert first_auth_close < first_business_open < first_business_sql
        assert writer_close < receipt_open
        assert all(thread_id != main_thread for _label, thread_id in system.events)
        assert all(session.info["observed_closed"] for session in system.auth_sessions)
        assert all(session.info["observed_closed"] for session in system.business_sessions)
    finally:
        system.engine.dispose()


_NO_ID_CANVAS_FRAMES = [
    {"text": "未绑定分镜", "storyboard": [None, ""]},
    {"text": "目标画面", "storyboard": []},
]


def _canonical_json_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _strict_no_id_canvas_body(
    *,
    frame_index: object = 1,
    frames: object | None = None,
) -> dict[str, object]:
    expected_frames = _NO_ID_CANVAS_FRAMES if frames is None else frames
    body = _body("image.single")
    body["target"] = {
        "kind": "canvas_apply",
        "destination": {
            "kind": "frame_preview",
            "chapter_id": "joint-chapter-1",
            "identity_mode": "strict-no-id-v1",
            "frame_index": frame_index,
            "expected_chapter_digest": _canonical_json_digest(expected_frames),
        },
    }
    return body


def test_real_http_admits_strict_no_id_canvas_frame_with_locked_canonical_facts(
    tmp_path: Path,
) -> None:
    system = _build_system(tmp_path)
    raw_content = json.dumps(
        _NO_ID_CANVAS_FRAMES,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    try:
        with system.engine.begin() as connection:
            connection.execute(
                update(catalog_chapters)
                .where(catalog_chapters.c.id == "joint-chapter-1")
                .values(content=raw_content)
            )

        response = _post(
            system,
            "/api/chat/image",
            body=_strict_no_id_canvas_body(),
            key="strict-no-id-canvas-admission-01",
        )

        assert response.status_code == 200, response.text
        receipt = response.json()
        assert receipt["operation"] == "image.single"
        assert receipt["billing_status"] == "charged"

        with system.engine.connect() as connection:
            task = connection.execute(
                select(AITaskRow.__table__).where(
                    AITaskRow.__table__.c.id == receipt["task_id"]
                )
            ).mappings().one()
            target = json.loads(task["request_data"])["target_snapshot"]
            source_storyboards = connection.execute(
                select(catalog_chapters.c.content).where(
                    catalog_chapters.c.id == "joint-chapter-1"
                )
            ).scalar_one()

        assert target["kind"] == "canvas_frame_preview"
        assert target["stable_storyboard_asset_id"] is None
        assert target["compat_frame_index"] == 1
        assert target["full_chapter_digest"] == _canonical_json_digest(
            _NO_ID_CANVAS_FRAMES
        )
        assert target["full_frame_digest"] == _canonical_json_digest(
            _NO_ID_CANVAS_FRAMES[1]
        )
        assert source_storyboards == raw_content
    finally:
        system.engine.dispose()


@pytest.mark.parametrize(
    ("mutation", "mutated_value", "expected_status", "expected_detail"),
    (
        ("identity_mode", None, 422, "请求格式无效"),
        ("identity_mode", "strict-no-id-v2", 422, "请求格式无效"),
        ("frame_index", True, 422, "请求格式无效"),
        ("frame_index", -1, 422, "请求格式无效"),
        ("expected_chapter_digest", "A" * 64, 422, "请求格式无效"),
        ("unexpected", "must-be-rejected", 422, "请求格式无效"),
        (
            "storyboard_asset_id",
            "must-not-fallback-to-stable-id",
            422,
            "请求格式无效",
        ),
        ("frame_index", 2, 409, "指定分镜不再是无稳定ID目标"),
        (
            "expected_chapter_digest",
            "0" * 64,
            409,
            "章节内容与画布目标断言不一致",
        ),
    ),
)
def test_real_http_rejects_malformed_or_mixed_strict_no_id_identity_without_writes(
    tmp_path: Path,
    mutation: str,
    mutated_value: object,
    expected_status: int,
    expected_detail: str,
) -> None:
    system = _build_system(tmp_path)
    raw_content = json.dumps(
        _NO_ID_CANVAS_FRAMES,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    body = _strict_no_id_canvas_body()
    destination = body["target"]["destination"]
    if mutated_value is None:
        destination.pop(mutation)
    else:
        destination[mutation] = mutated_value
    try:
        with system.engine.begin() as connection:
            connection.execute(
                update(catalog_chapters)
                .where(catalog_chapters.c.id == "joint-chapter-1")
                .values(content=raw_content)
            )

        response = _post(
            system,
            "/api/chat/image",
            body=body,
            key=f"strict-no-id-invalid-{mutation}",
        )

        assert response.status_code == expected_status, response.text
        assert response.json() == {"detail": expected_detail}
        if expected_status == 422:
            assert system.business_sessions == []
        with system.engine.connect() as connection:
            assert connection.execute(select(AITaskRow.__table__)).all() == []
            assert connection.execute(select(CreditLogRow.__table__)).all() == []
            assert connection.execute(
                select(UserCreditRow.__table__.c.credits)
            ).scalar_one() == 500
    finally:
        system.engine.dispose()


@pytest.mark.parametrize(
    ("chapter_content", "digest_frames", "expected_status"),
    (
        (
            json.dumps(
                [
                    {"text": "兄弟帧", "storyboard": [None, ""]},
                    {"text": "已绑定目标", "storyboard": ["existing-storyboard-id"]},
                ],
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            [
                {"text": "兄弟帧", "storyboard": [None, ""]},
                {"text": "已绑定目标", "storyboard": ["existing-storyboard-id"]},
            ],
            409,
        ),
        (
            '[{"text":"重复键","text":"重复键覆盖"},{"text":"目标","storyboard":[]}]',
            None,
            422,
        ),
        (
            '[{"text":"非有限值","value":NaN},{"text":"目标","storyboard":[]}]',
            None,
            422,
        ),
        ('[null,{"text":"目标","storyboard":[]}]', None, 422),
        ('{"text":"根不是数组"}', None, 422),
        ('not-json', None, 422),
        (
            json.dumps(
                [
                    {"text": "兄弟帧", "storyboard": [None, ""]},
                    {"text": "重复引用", "storyboard": ["same-id", "same-id"]},
                ],
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            [
                {"text": "兄弟帧", "storyboard": [None, ""]},
                {"text": "重复引用", "storyboard": ["same-id", "same-id"]},
            ],
            409,
        ),
        (
            json.dumps(
                [
                    {"text": "兄弟帧", "storyboard": [None, ""]},
                    {"text": "非法空白引用", "storyboard": [" "]},
                ],
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            [
                {"text": "兄弟帧", "storyboard": [None, ""]},
                {"text": "非法空白引用", "storyboard": [" "]},
            ],
            409,
        ),
        (
            json.dumps(
                [
                    {"text": "兄弟帧", "storyboard": [None, ""]},
                    {"text": "非数组引用", "storyboard": "existing-storyboard-id"},
                ],
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            [
                {"text": "兄弟帧", "storyboard": [None, ""]},
                {"text": "非数组引用", "storyboard": "existing-storyboard-id"},
            ],
            409,
        ),
    ),
)
def test_real_http_rejects_existing_or_invalid_strict_no_id_chapter_without_writes(
    tmp_path: Path,
    chapter_content: str,
    digest_frames: object | None,
    expected_status: int,
) -> None:
    system = _build_system(tmp_path)
    body = _strict_no_id_canvas_body(frames=digest_frames)
    if digest_frames is None:
        body["target"]["destination"]["expected_chapter_digest"] = "0" * 64
    try:
        with system.engine.begin() as connection:
            connection.execute(
                update(catalog_chapters)
                .where(catalog_chapters.c.id == "joint-chapter-1")
                .values(content=chapter_content)
            )

        response = _post(
            system,
            "/api/chat/image",
            body=body,
            key="strict-no-id-invalid-locked-chapter",
        )

        assert response.status_code == expected_status, response.text
        with system.engine.connect() as connection:
            assert connection.execute(select(AITaskRow.__table__)).all() == []
            assert connection.execute(select(CreditLogRow.__table__)).all() == []
            assert connection.execute(
                select(UserCreditRow.__table__.c.credits)
            ).scalar_one() == 500
    finally:
        system.engine.dispose()


def test_authentication_precedes_manual_json_and_dto_validation(tmp_path: Path) -> None:
    system = _build_system(tmp_path)
    try:
        malformed_without_auth = _post(
            system,
            "/api/chat/image",
            raw=b"{",
            token="invalid-token",
        )
        assert malformed_without_auth.status_code == 401
        assert malformed_without_auth.headers.get("www-authenticate") == "Bearer"
        assert system.business_sessions == []

        malformed_with_auth = _post(system, "/api/chat/image", raw=b"{")
        assert malformed_with_auth.status_code == 422
        assert system.business_sessions == []

        strict_extra = dict(_body("image.single"), api_key="client-controlled")
        rejected_extra = _post(system, "/api/chat/image", body=strict_extra)
        assert rejected_extra.status_code == 422
        assert system.business_sessions == []

        expired = _post(
            system,
            "/api/chat/image",
            raw=b"{",
            token=system.token_for(expired=True),
        )
        assert expired.status_code == 401
        assert system.business_sessions == []

        revoked_token = system.token_for()
        with system.engine.begin() as connection:
            connection.execute(
                update(user_sessions)
                .where(user_sessions.c.id == AUTH_SESSION_ID)
                .values(
                    is_active=False,
                    revoked_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )
            )
        revoked = _post(
            system,
            "/api/chat/image",
            raw=b"{",
            token=revoked_token,
        )
        assert revoked.status_code == 401
        assert system.business_sessions == []
    finally:
        system.engine.dispose()


def test_authenticated_default_without_admission_configuration_fails_closed(
    tmp_path: Path,
) -> None:
    system = _build_system(tmp_path, with_configuration=False)
    try:
        response = _post(system, "/api/chat/image", body=_body("image.single"))
        assert response.status_code == 503
        assert system.business_sessions == []
        with system.engine.connect() as connection:
            assert connection.execute(select(AITaskRow.__table__)).all() == []
            assert connection.execute(select(CreditLogRow.__table__)).all() == []
    finally:
        system.engine.dispose()


@pytest.mark.parametrize(
    ("membership_type", "expires_at"),
    (
        ("free", None),
        ("premium", _G2_JOINT_NOW - timedelta(days=1)),
    ),
)
def test_original_replay_survives_free_or_expired_membership_but_new_key_is_denied(
    tmp_path: Path,
    membership_type: str,
    expires_at: datetime | None,
) -> None:
    system = _build_system(tmp_path)
    try:
        request_body = _body("image.single")
        original = _post(
            system,
            "/api/chat/image",
            body=request_body,
            key="chat-media-original-replay-01",
        )
        assert original.status_code == 200
        with system.engine.begin() as connection:
            connection.execute(
                update(users)
                .where(users.c.id == _G2_JOINT_ACTOR_ID)
                .values(
                    membership_type=membership_type,
                    membership_expires_at=expires_at,
                )
            )
        replay = _post(
            system,
            "/api/chat/image",
            body=request_body,
            key="chat-media-original-replay-01",
        )
        assert replay.status_code == 200
        assert replay.json() == original.json()
        fresh = _post(
            system,
            "/api/chat/image",
            body=request_body,
            key="chat-media-new-admission-02",
        )
        assert fresh.status_code == 403
        with system.engine.connect() as connection:
            assert len(connection.execute(select(AITaskRow.__table__)).all()) == 1
            assert len(connection.execute(select(CreditLogRow.__table__)).all()) == 1
            assert connection.execute(select(UserCreditRow.__table__.c.credits)).scalar_one() == 497
    finally:
        system.engine.dispose()


def test_same_key_exact_replay_conflict_and_disabled_model_preserve_original_receipt(
    tmp_path: Path,
) -> None:
    system = _build_system(tmp_path)
    try:
        request_body = _body("image.single")
        first = _post(system, "/api/chat/image", body=request_body)
        assert first.status_code == 200
        with system.engine.begin() as connection:
            connection.execute(
                update(system_models)
                .where(system_models.c.id == _G2_JOINT_MODEL_ID)
                .values(is_active=False)
            )
        replay = _post(system, "/api/chat/image", body=request_body)
        assert replay.status_code == 200
        assert replay.json() == first.json()
        changed = dict(request_body, prompt="A different request")
        conflict = _post(system, "/api/chat/image", body=changed)
        assert conflict.status_code == 409
        with system.engine.connect() as connection:
            assert len(connection.execute(select(AITaskRow.__table__)).all()) == 1
            assert len(connection.execute(select(CreditLogRow.__table__)).all()) == 1
            assert connection.execute(select(UserCreditRow.__table__.c.credits)).scalar_one() == 497
    finally:
        system.engine.dispose()


def test_commit_acknowledgement_loss_is_confirmed_without_second_stage_or_charge(
    tmp_path: Path,
) -> None:
    system = _build_system(tmp_path, lose_first_business_commit_ack=True)
    try:
        response = _post(system, "/api/chat/image", body=_body("image.single"))
        assert response.status_code == 200, response.text
        writers = [
            session for session in system.business_sessions
            if int(session.info.get("commit_calls", 0)) > 0
        ]
        assert len(writers) == 1
        assert writers[0].info["commit_calls"] == 1
        with system.engine.connect() as connection:
            assert len(connection.execute(select(AITaskRow.__table__)).all()) == 1
            assert len(connection.execute(select(CreditLogRow.__table__)).all()) == 1
            assert connection.execute(select(UserCreditRow.__table__.c.credits)).scalar_one() == 497
    finally:
        system.engine.dispose()


def test_unconfirmed_fresh_receipt_returns_503_without_replaying_stage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from haoai_backend.chat_media_admission import http as chat_media_http

    system = _build_system(tmp_path)
    real_lookup = chat_media_http.lookup_committed_receipt
    lookups: list[object] = []

    def return_unconfirmed(session_factory: object, staged: object) -> object:
        result = real_lookup(session_factory, staged)
        assert isinstance(result, AdmissionConfirmed)
        lookups.append(staged)
        if isinstance(staged, AdmissionCommitUnknown):
            return staged
        return AdmissionCommitUnknown(staged=staged)

    monkeypatch.setattr(chat_media_http, "lookup_committed_receipt", return_unconfirmed)
    try:
        response = _post(system, "/api/chat/image", body=_body("image.single"))
        assert response.status_code == 503
        assert len(lookups) == 1
        assert len(system.business_sessions) == 2
        assert system.business_sessions[0].info.get("commit_calls") == 1
        explicit_replay = _post(
            system,
            "/api/chat/image",
            body=_body("image.single"),
        )
        assert explicit_replay.status_code == 200
        assert len(system.business_sessions) == 3
        assert system.business_sessions[-1].info.get("commit_calls", 0) == 0
        with system.engine.connect() as connection:
            assert len(connection.execute(select(AITaskRow.__table__)).all()) == 1
            assert len(connection.execute(select(CreditLogRow.__table__)).all()) == 1
            assert connection.execute(select(UserCreditRow.__table__.c.credits)).scalar_one() == 497
    finally:
        system.engine.dispose()


def test_http_task_repeated_cancel_drains_real_writer_session_close(
    tmp_path: Path,
) -> None:
    """Request cancellation waits for the actual SQLite writer close."""

    close_started = threading.Event()
    close_release = threading.Event()
    close_finished = threading.Event()
    system = _build_system(
        tmp_path,
        business_close_gate=(close_started, close_release, close_finished),
    )
    request_task: asyncio.Task[httpx.Response] | None = None

    async def cancel_request() -> None:
        nonlocal request_task
        headers = {
            "Authorization": f"Bearer {system.token_for()}",
            "Idempotency-Key": "chat-media-cancel-drain-01",
        }
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=system.app, raise_app_exceptions=False
            ),
            base_url=BASE_URL,
        ) as client:
            request_task = asyncio.create_task(
                client.post(
                    "/api/chat/image",
                    headers=headers,
                    json=_body("image.single"),
                )
            )
            assert request_task is not None
            try:
                assert await asyncio.to_thread(close_started.wait, 5)
                writer = system.business_sessions[0]
                assert writer.info.get("commit_calls") == 1
                assert writer.info.get("close_count", 0) == 0

                request_task.cancel()
                await asyncio.sleep(0)
                request_task.cancel()
                await asyncio.sleep(0)
                assert not request_task.done()
                assert writer.info.get("close_count", 0) == 0

                close_release.set()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(request_task, timeout=5)
                assert await asyncio.to_thread(close_finished.wait, 5)
                assert writer.info.get("close_count") == 1
                assert writer.info.get("observed_closed") is True
            finally:
                close_release.set()
                if request_task is not None and not request_task.done():
                    request_task.cancel()
                    try:
                        await asyncio.wait_for(request_task, timeout=5)
                    except asyncio.CancelledError:
                        pass
                if close_started.is_set():
                    assert await asyncio.to_thread(close_finished.wait, 5)

    main_thread = threading.get_ident()
    try:
        asyncio.run(cancel_request())
        assert len(system.business_sessions) == 1
        assert system.business_sessions[0].info.get("commit_calls") == 1
        assert system.business_sessions[0].info.get("close_count") == 1
        close_thread = next(
            thread_id
            for label, thread_id in system.events
            if label == "close:business"
        )
        assert close_thread != main_thread
        with system.engine.connect() as connection:
            assert len(connection.execute(select(AITaskRow.__table__)).all()) == 1
            assert len(connection.execute(select(CreditLogRow.__table__)).all()) == 1
            assert connection.execute(
                select(UserCreditRow.__table__.c.credits)
            ).scalar_one() == 497
    finally:
        close_release.set()
        system.engine.dispose()
