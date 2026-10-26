"""Opt-in server integration for image.single reference editing."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import socket
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
from sqlalchemy import event, inspect, select
from sqlalchemy.engine import Engine

from backend.server.application import create_server_host
from backend.server.configuration import load_server_configuration
from haoai_backend.authentication.tokens import JoseTokenCodec
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    ResultEvidenceRow,
    UserCreditRow,
)
from haoai_backend.generation_core.schema import GenerationResultReceiptRow
from haoai_backend.generation_credentials.application import RegistryCapabilities
import test_app_chat_media_admission_integration as media_integration
import test_server_image_batch_worker as batch_host_fixtures
from test_server_media_storage import _prepared_png


_PNG_DATA = _prepared_png()
_GIF_DATA = b"GIF89a\x01\x00\x01\x00\x80\x00\x00"
_BMP_DATA = b"BM\x1a\x00\x00\x00\x00\x00\x00\x00\x1a\x00\x00\x00"
_GENERATED_PNG = _prepared_png()
_IMAGE_URLS = {
    "https://references.example.test/scene.gif?v=1": (_GIF_DATA, "image/gif"),
    "https://references.example.test/prop.bmp?v=2": (_BMP_DATA, "image/bmp"),
}
_IDEMPOTENCY_KEY = "image-reference-host-970"


class _PublicResponse:
    status = 200

    def __init__(self, content: bytes, media_type: str) -> None:
        self._content = content
        self.headers = {
            "content-type": media_type,
            "content-length": str(len(content)),
        }

    def iter_chunks(self, chunk_size: int):
        assert chunk_size > 0

        async def chunks():
            yield self._content

        return chunks()

    async def close(self) -> None:
        return None


class _PublicConnector:
    async def close(self) -> None:
        return None


class _PublicSession:
    def __init__(self, library: _PublicHttpLibrary) -> None:
        self._library = library

    async def get(
        self,
        target: Any,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
        allow_redirects: bool,
    ) -> _PublicResponse:
        assert headers == {"Host": urlsplit(target.raw_url).netloc}
        assert timeout_seconds > 0
        assert allow_redirects is False
        self._library.get_calls.append(target.raw_url)
        content, media_type = _IMAGE_URLS[target.raw_url]
        return _PublicResponse(content, media_type)

    async def close(self) -> None:
        return None


class _PublicHttpLibrary:
    def __init__(self) -> None:
        from haoai_backend.media_storage.pinned_http import EncodedRequestTarget

        self._target_type = EncodedRequestTarget
        self.get_calls: list[str] = []

    def encoded_url(self, raw_url: str, *, preserve_encoded: bool):
        assert preserve_encoded is True
        parsed = urlsplit(raw_url)
        return self._target_type(
            raw_url=raw_url,
            authority=parsed.netloc,
            tls_hostname=parsed.hostname or "",
            encoded=True,
        )

    def connector(
        self,
        _resolver: Any,
        *,
        tls_hostname: str,
        verify_tls: bool,
        use_dns_cache: bool,
    ) -> _PublicConnector:
        assert tls_hostname
        assert verify_tls is True
        assert use_dns_cache is False
        return _PublicConnector()

    def session(
        self,
        _connector: _PublicConnector,
        *,
        trust_env: bool,
        cookies_enabled: bool,
        auto_decompress: bool,
        connector_owned_by_session: bool,
    ) -> _PublicSession:
        assert trust_env is False
        assert cookies_enabled is False
        assert auto_decompress is True
        assert connector_owned_by_session is False
        return _PublicSession(self)


class _ProviderResponse:
    status_code = 200

    def __init__(self) -> None:
        self._body = json.dumps(
            {"data": [{"b64_json": base64.b64encode(_GENERATED_PNG).decode("ascii")}]}
        ).encode("ascii")
        self.headers = {
            "content-type": "application/json",
            "content-length": str(len(self._body)),
        }

    async def iter_bytes(self, *, chunk_size: int):
        assert chunk_size > 0
        yield self._body


class _ProviderClient:
    def __init__(self, calls: list[dict[str, object]]) -> None:
        self._calls = calls

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        content: bytes | None = None,
        **kwargs: object,
    ) -> _ProviderResponse:
        self._calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers or {},
                "content": content,
                "follow_redirects": kwargs.get("follow_redirects"),
            }
        )
        return _ProviderResponse()


class _ProviderContext:
    def __init__(self, calls: list[dict[str, object]]) -> None:
        self._client = _ProviderClient(calls)

    async def __aenter__(self) -> _ProviderClient:
        return self._client

    async def __aexit__(self, _exc_type, _exc, _traceback) -> bool:
        return False


class _ProviderFactory:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(self) -> _ProviderContext:
        return _ProviderContext(self.calls)


def _build_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Any, Any, _PublicHttpLibrary, _ProviderFactory, list[bytes]]:
    monkeypatch.setattr(
        media_integration.admission_staging_module,
        "_G2_JOINT_PROVIDER",
        "openai",
    )
    fixture = media_integration._build_system(tmp_path)
    _, credential_file = batch_host_fixtures._provisioned_bundle(tmp_path)
    with fixture.engine.begin() as connection:
        columns = {
            column["name"]
            for column in inspect(connection).get_columns("system_configs")
        }
        if "worker_max_concurrency" not in columns:
            connection.exec_driver_sql(
                "ALTER TABLE system_configs ADD COLUMN worker_max_concurrency "
                "VARCHAR(10) NOT NULL DEFAULT '1'"
            )

    from backend.server import application as server_application
    from backend.server import image_generation as image_generation_module
    from haoai_backend import app as app_module

    def load_fixture_capabilities(
        _path: Path,
        *,
        session_factory: Any,
        db_clock: Any,
        id_factory: Any,
        **_kwargs: object,
    ) -> RegistryCapabilities:
        return replace(
            fixture.capabilities,
            read_session_factory=session_factory,
            db_clock=db_clock,
            id_factory=id_factory,
        )

    public_http = _PublicHttpLibrary()
    provider_http = _ProviderFactory()
    uploaded: list[bytes] = []
    monkeypatch.setattr(
        image_generation_module,
        "load_registry_capabilities",
        load_fixture_capabilities,
    )
    monkeypatch.setattr(
        image_generation_module,
        "_build_http_library",
        lambda: public_http,
    )

    async def public_dns(
        host: str,
        port: int,
        *,
        family: int,
        type: int,
    ) -> tuple[object, ...]:
        assert host.endswith(".example.test")
        assert family == socket.AF_UNSPEC
        assert type == socket.SOCK_STREAM
        return (
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", port)),
        )

    monkeypatch.setattr(image_generation_module, "_system_getaddrinfo", public_dns)
    monkeypatch.setattr(
        image_generation_module,
        "AiohttpAsyncHttpClientFactory",
        lambda: provider_http,
    )

    original_create_engine = server_application._create_engine

    def create_sqlite_engine_with_foreign_keys(database_url: str) -> Engine:
        engine = original_create_engine(database_url)
        if database_url.startswith("sqlite"):

            def enable_foreign_keys(connection, _record) -> None:
                cursor = connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

            event.listen(engine, "connect", enable_foreign_keys)
        return engine

    monkeypatch.setattr(
        server_application,
        "_create_engine",
        create_sqlite_engine_with_foreign_keys,
    )

    worker_service_class = app_module.GenerationWorkerService

    class FastImageRecoveryWorkerService(worker_service_class):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            if kwargs.get("task_type") == "image-single":
                kwargs["recovery_scan_interval_seconds"] = 0.05
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(
        app_module,
        "GenerationWorkerService",
        FastImageRecoveryWorkerService,
    )

    environment = batch_host_fixtures._batch_host_environment(
        tmp_path,
        credential_file,
    )
    environment["IMAGE_BATCH_ENABLED"] = "false"
    environment.pop("IMAGE_BATCH_CREDENTIALS_FILE")
    host = create_server_host(
        load_server_configuration(environment),
        sdk_factories=(
            lambda **kwargs: batch_host_fixtures._FakeS3Session(uploaded, **kwargs),
            lambda **kwargs: kwargs,
        ),
        worker_environment={"WORKER_CONCURRENCY": "1"},
    )
    return fixture, host, public_http, provider_http, uploaded


def _reference_body(references: list[str]) -> dict[str, object]:
    body = media_integration._body("image.single")
    body["reference_images"] = references
    return body


def _make_token() -> str:
    return JoseTokenCodec(batch_host_fixtures._SERVER_SECRET).encode(
        {
            "sub": media_integration._G2_JOINT_ACTOR_ID,
            "jti": media_integration.JTI,
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        }
    )


def test_server_worker_edits_ordered_references_and_replays_original_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture, host, public_http, provider_http, uploaded = _build_host(
        tmp_path,
        monkeypatch,
    )
    token = _make_token()
    data_uri = "data:image/png;base64," + base64.b64encode(_PNG_DATA).decode("ascii")
    gif_url = "https://references.example.test/scene.gif?v=1"
    bmp_url = "https://references.example.test/prop.bmp?v=2"
    fallback_url = "https://references.example.test/legacy.png"
    body = _reference_body([data_uri, gif_url, bmp_url, gif_url])
    body["reference_image"] = fallback_url

    async def run() -> None:
        async with host.app.router.lifespan_context(host.app):
            assert host.app.state.generation_worker_service is not None
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(
                    app=host.app,
                    raise_app_exceptions=False,
                ),
                base_url="http://image-reference-host.test",
            ) as client:
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Idempotency-Key": _IDEMPOTENCY_KEY,
                }
                response = await client.post(
                    "/api/chat/image",
                    headers=headers,
                    json=body,
                )
                assert response.status_code == 200, response.text
                task_id = response.json()["task_id"]

                for _ in range(120):
                    with host.session_factory() as session:
                        task_status = session.execute(
                            select(AITaskRow.__table__.c.status).where(
                                AITaskRow.__table__.c.id == task_id
                            )
                        ).scalar_one()
                    if task_status == "completed":
                        break
                    await asyncio.sleep(0.05)
                assert task_status == "completed"
                assert public_http.get_calls == [gif_url, bmp_url, gif_url]
                assert len(provider_http.calls) == 1
                assert len(uploaded) == 1
                assert uploaded == [_GENERATED_PNG]

                provider_request = provider_http.calls[0]
                assert provider_request["method"] == "POST"
                assert str(provider_request["url"]).endswith("/images/edits")
                assert provider_request["follow_redirects"] is False
                multipart = provider_request["content"]
                assert isinstance(multipart, bytes)
                assert multipart.count(b'name="image"; filename="ref_0.png"') == 1
                assert multipart.count(b'name="image"; filename="ref_1.png"') == 1
                assert multipart.count(b'name="image"; filename="ref_2.png"') == 1
                assert multipart.count(b'name="image"; filename="ref_3.png"') == 1
                assert multipart.count(b"Content-Type: image/png") == 1
                assert multipart.count(b"Content-Type: image/gif") == 2
                assert multipart.count(b"Content-Type: image/bmp") == 1
                positions = [
                    multipart.index(_PNG_DATA),
                    multipart.index(_GIF_DATA),
                    multipart.index(_BMP_DATA),
                    multipart.rindex(_GIF_DATA),
                ]
                assert positions == sorted(positions)
                assert fallback_url.encode() not in multipart

                with host.session_factory() as session:
                    task = session.execute(
                        select(AITaskRow.__table__).where(
                            AITaskRow.__table__.c.id == task_id
                        )
                    ).mappings().one()
                    unit = session.execute(
                        select(BillingUnitRow.__table__).where(
                            BillingUnitRow.__table__.c.task_id == task_id
                        )
                    ).mappings().one()
                    receipt = session.execute(
                        select(GenerationResultReceiptRow.__table__).where(
                            GenerationResultReceiptRow.__table__.c.task_id == task_id
                        )
                    ).mappings().one()
                    evidence = session.execute(
                        select(ResultEvidenceRow.__table__).where(
                            ResultEvidenceRow.__table__.c.id == receipt["evidence_id"]
                        )
                    ).mappings().one()
                    wallet_before_replay = session.execute(
                        select(UserCreditRow.__table__.c.credits)
                    ).scalar_one()
                    ledger_before_replay = session.execute(
                        select(CreditLogRow.__table__.c.id).order_by(
                            CreditLogRow.__table__.c.id
                        )
                    ).scalars().all()
                result = json.loads(task["result"])
                assert unit["result_status"] == "succeeded"
                assert unit["refunded_amount"] == 0
                assert receipt["operation"] == "image.single"
                assert receipt["result_ref"] == result["result_ref"]
                assert receipt["recoverable_locator"] == result["url"]
                assert evidence["validity"] == "valid"

                replay = await client.post(
                    "/api/chat/image",
                    headers=headers,
                    json=body,
                )
                assert replay.status_code == 200, replay.text
                assert replay.json()["task_id"] == task_id
                assert len(provider_http.calls) == 1
                assert len(uploaded) == 1
                with host.session_factory() as session:
                    wallet_after_replay = session.execute(
                        select(UserCreditRow.__table__.c.credits)
                    ).scalar_one()
                    ledger_after_replay = session.execute(
                        select(CreditLogRow.__table__.c.id).order_by(
                            CreditLogRow.__table__.c.id
                        )
                    ).scalars().all()
                assert wallet_after_replay == wallet_before_replay
                assert ledger_after_replay == ledger_before_replay

    try:
        asyncio.run(run())
    finally:
        host.close()
        fixture.engine.dispose()


def test_unsafe_reference_fails_before_provider_post_or_result_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture, host, public_http, provider_http, uploaded = _build_host(
        tmp_path,
        monkeypatch,
    )
    token = _make_token()
    body = _reference_body(["http://127.0.0.1/private/reference.png"])
    body.pop("reference_image", None)

    async def run() -> None:
        async with host.app.router.lifespan_context(host.app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(
                    app=host.app,
                    raise_app_exceptions=False,
                ),
                base_url="http://image-reference-host.test",
            ) as client:
                response = await client.post(
                    "/api/chat/image",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Idempotency-Key": f"{_IDEMPOTENCY_KEY}-unsafe",
                    },
                    json=body,
                )
                assert response.status_code == 200, response.text
                task_id = response.json()["task_id"]

                for _ in range(120):
                    with host.session_factory() as session:
                        task_status = session.execute(
                            select(AITaskRow.__table__.c.status).where(
                                AITaskRow.__table__.c.id == task_id
                            )
                        ).scalar_one()
                    if task_status == "failed":
                        break
                    await asyncio.sleep(0.05)
                assert task_status == "failed"
                assert public_http.get_calls == []
                assert provider_http.calls == []
                assert uploaded == []
                with host.session_factory() as session:
                    task = session.execute(
                        select(AITaskRow.__table__).where(
                            AITaskRow.__table__.c.id == task_id
                        )
                    ).mappings().one()
                    unit = session.execute(
                        select(BillingUnitRow.__table__).where(
                            BillingUnitRow.__table__.c.task_id == task_id
                        )
                    ).mappings().one()
                    receipt_count = session.execute(
                        select(GenerationResultReceiptRow.__table__.c.result_ref).where(
                            GenerationResultReceiptRow.__table__.c.task_id == task_id
                        )
                    ).all()
                    credits = session.execute(
                        select(UserCreditRow.__table__.c.credits)
                    ).scalar_one()
                assert task["status"] == "failed"
                assert unit["result_status"] == "failed"
                assert unit["refunded_amount"] == unit["quoted_amount"]
                assert receipt_count == []
                assert credits == 500

    try:
        asyncio.run(run())
    finally:
        host.close()
        fixture.engine.dispose()
