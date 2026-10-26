from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import replace
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event, Thread

import pytest

from sqlalchemy import delete, event, func, insert, select, update
from sqlalchemy.orm import Session

from haoai_backend.authentication.tables import users as authentication_users
from haoai_backend.generation_contracts.ports import GenerationRuntimeConfig
from haoai_backend.generation_contracts.types import (
    BoundCredential,
    OwnerTaskScope,
    PreparedReference,
    StoredMedia,
)
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
    ResultEvidenceRow,
    UserCreditRow,
)
from haoai_backend.generation_core.admission_receipts import AdmissionStaged
from haoai_backend.generation_core.image_single_results import (
    SqlAlchemyImageSingleResults,
)
from haoai_backend.generation_core.schema import GenerationResultReceiptRow
from haoai_backend.generation_runtime.assembly import (
    GenerationWorkerConfiguration,
    build_generation_worker_runtime,
)
from haoai_backend.generation_credentials.application import (
    RegistryCapabilities,
    build_credential_registry,
)
import test_admission_staging as admission_staging_module
import haoai_backend.generation_providers.image.openai_provider as openai_provider_module
from test_admission_staging import (
    _joint_caller,
    _joint_prepare_and_stage,
)
from test_execution_snapshot_reader import (
    _authenticated_environment,
    _authenticated_joint_crypto,
)
from test_image_single_intents import _claimed_image_task, _intents


_PNG = b"\x89PNG\r\n\x1a\n"
_PNG_DATA_URI = "data:image/png;base64," + base64.b64encode(_PNG).decode("ascii")


class _Response:
    headers = {"content-length": "0"}

    def __init__(self, body: bytes, status_code: int) -> None:
        self._body = body
        self.status_code = status_code

    async def iter_bytes(self, *, chunk_size: int):
        assert chunk_size > 0
        yield self._body


class _HttpClient:
    def __init__(
        self, body: bytes, status_code: int, calls: list[dict[str, object]]
    ) -> None:
        self._body = body
        self._status_code = status_code
        self._calls = calls

    async def request(self, method, url, *, headers=None, content=None, **kwargs):
        self._calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers or {},
                "content": content,
                "timeout_seconds": kwargs.get("timeout_seconds"),
                "follow_redirects": kwargs.get("follow_redirects"),
            }
        )
        return _Response(self._body, self._status_code)


class _HttpClientContext(AbstractAsyncContextManager):
    def __init__(self, client: _HttpClient) -> None:
        self._client = client

    async def __aenter__(self):
        return self._client

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _Storage:
    def __init__(self, calls: list[StoredMedia], on_store=None) -> None:
        self._calls = calls
        self._on_store = on_store

    def store_bytes_or_provider_content(self, reference, object_policy):
        assert reference.kind == "bytes"
        assert object_policy == {"max_bytes": 10 * 1024 * 1024}
        assert reference.content is not None
        stored = StoredMedia(
            locator="https://media.example.test/managed/image.png",
            content_digest=hashlib.sha256(reference.content).hexdigest(),
            media_type=reference.media_type or "",
            size_bytes=len(reference.content),
        )
        self._calls.append(stored)
        if self._on_store is not None:
            self._on_store(stored)
        return stored


class _UnusedFetcher:
    async def fetch_public(self, reference, *, max_bytes, timeout_seconds):
        raise AssertionError("base64 image results must not make a public fetch")


def _runtime_configuration(
    environment,
    response_body: bytes,
    stores: list[StoredMedia],
    *,
    response_status: int = 200,
    http_client_factory=None,
    credential_resolver=None,
    public_media_fetcher_factory=None,
    media_store_factory=None,
):
    provider_calls: list[dict[str, object]] = []
    id_value = 0

    def new_id() -> str:
        nonlocal id_value
        id_value += 1
        return f"image-worker-id-{id_value}"

    registry = build_credential_registry(environment.capabilities)
    configuration = GenerationRuntimeConfig(
        session_factory=lambda: Session(environment.engine),
        db_clock=lambda: datetime(2026, 10, 16, 2, 0, 30, tzinfo=timezone.utc),
        id_factory=new_id,
        http_client_factory=http_client_factory
        or (lambda: _HttpClientContext(
            _HttpClient(response_body, response_status, provider_calls)
        )),
        credential_resolver=credential_resolver or registry.resolve_original_credential,
        provider_factories=(),
        media_store_factory=media_store_factory or (lambda: _Storage(stores)),
        local_muse_staging_dir=None,
        public_media_fetcher_factory=public_media_fetcher_factory
        or (lambda: _UnusedFetcher()),
        bound_provider_content_fetcher_factory=lambda: object(),
        local_muse_capability_factory=lambda: object(),
        r2_storage_factory=lambda: object(),
    )
    return configuration, provider_calls


def _admitted_image_task(
    tmp_path: Path,
    monkeypatch,
    raw_key: str,
    *,
    model_source=None,
    wallet_credits: int | None = 20,
    reference_images: tuple[str, ...] = (),
):
    monkeypatch.setattr(admission_staging_module, "_G2_JOINT_PROVIDER", "openai")
    if model_source is None:
        environment = _authenticated_environment(monkeypatch, tmp_path)
    else:
        monkeypatch.setattr(
            admission_staging_module,
            "_joint_local_dummy_crypto",
            _authenticated_joint_crypto,
        )
        environment = admission_staging_module._joint_environment(
            tmp_path,
            model_source=model_source,
            operation="image.single",
            wallet_credits=wallet_credits,
        )
    caller = _joint_caller(environment.engine)
    staged = _joint_prepare_and_stage(
        environment.binding,
        caller,
        operation="image.single",
        raw_key=raw_key,
        request={
            "model_id": "model-1",
            "prompt": "Worker integration test",
            "target": {"kind": "standalone"},
            "size": None,
            "reference_images": list(reference_images),
        },
    )
    assert isinstance(staged, AdmissionStaged)
    task_id = staged.ordered_tasks[0].task_id
    caller.commit()
    caller.close()
    return environment, task_id


def _provider_body(*, status: int = 200, media_uri: str = _PNG_DATA_URI) -> bytes:
    if status != 200:
        return b"{}"
    encoded = media_uri.split(",", 1)[1]
    return json.dumps({"data": [{"b64_json": encoded}]}).encode("utf-8")


def _cancel_task(environment, task_id: str) -> None:
    with Session(environment.engine) as mutator:
        changed = mutator.execute(
            update(AITaskRow.__table__)
            .where(AITaskRow.id == task_id)
            .values(
                status="cancelling",
                user_cancelled_at=datetime(2026, 10, 16, 2, 0, 31),
                cancellation_reason="user_request",
            )
        )
        assert changed.rowcount == 1
        mutator.commit()


def _run_in_thread(runtime, outcomes: list[object]) -> Thread:
    thread = Thread(
        target=lambda: outcomes.append(runtime.run_claimed_once("image-single")),
        daemon=True,
    )
    thread.start()
    return thread


@pytest.mark.parametrize("credential_state", ("none", "exception", "wrong_principal"))
@pytest.mark.parametrize("billing_state", ("charged", "byok"))
def test_unstarted_configuration_failure_settles_without_provider_request(
    tmp_path: Path,
    monkeypatch,
    credential_state: str,
    billing_state: str,
) -> None:
    model_source = (
        admission_staging_module.ModelSource.USER
        if billing_state == "byok"
        else None
    )
    environment, task_id = _admitted_image_task(
        tmp_path,
        monkeypatch,
        f"image-worker-unstarted-config-{credential_state}-{billing_state}",
        model_source=model_source,
        wallet_credits=None if billing_state == "byok" else 20,
    )
    stores: list[StoredMedia] = []
    runtime_config, provider_calls = _runtime_configuration(
        environment, _provider_body(), stores
    )
    registry = build_credential_registry(environment.capabilities)
    http_factory_calls: list[bool] = []

    def credential_resolver(binding, purpose):
        if credential_state == "none":
            return None
        if credential_state == "exception":
            raise RuntimeError("fixture credential resolution failure")
        credential = registry.resolve_original_credential(binding, purpose)
        assert isinstance(credential, BoundCredential)
        return replace(credential, principal_id=f"{credential.principal_id}-wrong")

    def http_client_factory():
        http_factory_calls.append(True)
        return _HttpClientContext(_HttpClient(_provider_body(), 200, provider_calls))

    runtime_config = replace(
        runtime_config,
        credential_resolver=credential_resolver,
        http_client_factory=http_client_factory,
    )
    runtime = build_generation_worker_runtime(
        GenerationWorkerConfiguration(runtime_config, environment.capabilities)
    )

    outcome = runtime.run_claimed_once("image-single")

    assert outcome is None
    assert http_factory_calls == []
    assert provider_calls == []
    assert stores == []
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow).where(AITaskRow.id == task_id)
        ).scalar_one()
        unit = reader.execute(
            select(BillingUnitRow).where(BillingUnitRow.task_id == task_id)
        ).scalar_one()
        refunds = reader.execute(
            select(CreditLogRow).where(
                CreditLogRow.task_id == task_id,
                CreditLogRow.type == "refund",
            )
        ).scalars().all()
        assert task.status == "failed"
        assert task.result is not None
        assert unit.result_status == "failed"
        assert unit.refunded_amount == (0 if billing_state == "byok" else 3)
        assert unit.billing_status == ("no_charge" if billing_state == "byok" else "refunded")
        assert len(refunds) == (0 if billing_state == "byok" else 1)
        if billing_state == "charged":
            assert reader.execute(
                select(UserCreditRow.credits).where(
                    UserCreditRow.user_id == task.user_id
                )
            ).scalar_one() == 20
        else:
            assert reader.execute(
                select(func.count()).select_from(UserCreditRow)
            ).scalar_one() == 0
    environment.engine.dispose()


@pytest.mark.parametrize(
    "failure_kind",
    (
        "invalid_frozen_snapshot",
        "request_body_too_large",
        "unsupported_provider",
        "wrong_execution_context",
    ),
)
def test_unstarted_request_preparation_failure_settles_without_http_context(
    tmp_path: Path,
    monkeypatch,
    failure_kind: str,
) -> None:
    environment, task_id = _admitted_image_task(
        tmp_path, monkeypatch, f"image-worker-unstarted-{failure_kind}"
    )
    stores: list[StoredMedia] = []
    runtime_config, provider_calls = _runtime_configuration(
        environment, _provider_body(), stores
    )
    if failure_kind == "invalid_frozen_snapshot":
        def raise_encoding_error(**_kwargs):
            raise ValueError("fixture encoding failure")

        monkeypatch.setattr(
            openai_provider_module,
            "build_openai_text_to_image_request",
            raise_encoding_error,
        )
    elif failure_kind == "request_body_too_large":
        monkeypatch.setattr(openai_provider_module, "_MAX_REQUEST_BODY_BYTES", 1)
    elif failure_kind == "unsupported_provider":
        monkeypatch.setattr(openai_provider_module, "_SUPPORTED_PROVIDERS", frozenset())
    else:
        monkeypatch.setattr(openai_provider_module, "_running_loop_exists", lambda: True)
    http_factory_calls: list[bool] = []

    def http_client_factory():
        http_factory_calls.append(True)
        return _HttpClientContext(_HttpClient(_provider_body(), 200, provider_calls))

    runtime_config = replace(runtime_config, http_client_factory=http_client_factory)
    runtime = build_generation_worker_runtime(
        GenerationWorkerConfiguration(runtime_config, environment.capabilities)
    )

    outcome = runtime.run_claimed_once("image-single")

    assert outcome is None
    assert http_factory_calls == []
    assert provider_calls == []
    assert stores == []
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow).where(AITaskRow.id == task_id)
        ).scalar_one()
        unit = reader.execute(
            select(BillingUnitRow).where(BillingUnitRow.task_id == task_id)
        ).scalar_one()
        assert task.status == "failed"
        assert json.loads(task.result)["code"] == failure_kind
        assert unit.result_status == "failed"
        assert unit.billing_status == "refunded"
        assert unit.refunded_amount == 3
        assert reader.execute(
            select(func.count()).select_from(CreditLogRow).where(
                CreditLogRow.business_key == f"refund:{unit.id}"
            )
        ).scalar_one() == 1
    environment.engine.dispose()


def test_http_context_enter_failure_before_request_settles_without_post(
    tmp_path: Path, monkeypatch
) -> None:
    environment, task_id = _admitted_image_task(
        tmp_path, monkeypatch, "image-worker-unstarted-context-enter"
    )
    stores: list[StoredMedia] = []
    runtime_config, provider_calls = _runtime_configuration(
        environment, _provider_body(), stores
    )
    http_factory_calls: list[bool] = []
    context_enter_calls: list[bool] = []

    class FailingHttpContext(AbstractAsyncContextManager):
        async def __aenter__(self):
            context_enter_calls.append(True)
            raise RuntimeError("fixture HTTP context startup failure")

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    def http_client_factory():
        http_factory_calls.append(True)
        return FailingHttpContext()

    runtime_config = replace(runtime_config, http_client_factory=http_client_factory)
    runtime = build_generation_worker_runtime(
        GenerationWorkerConfiguration(runtime_config, environment.capabilities)
    )

    outcome = runtime.run_claimed_once("image-single")

    assert outcome is None
    assert http_factory_calls == [True]
    assert context_enter_calls == [True]
    assert provider_calls == []
    assert stores == []
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow).where(AITaskRow.id == task_id)
        ).scalar_one()
        unit = reader.execute(
            select(BillingUnitRow).where(BillingUnitRow.task_id == task_id)
        ).scalar_one()
        assert task.status == "failed"
        assert unit.result_status == "failed"
        assert unit.refunded_amount == 3
        assert reader.execute(
            select(func.count()).select_from(CreditLogRow).where(
                CreditLogRow.business_key == f"refund:{unit.id}"
            )
        ).scalar_one() == 1
    environment.engine.dispose()


def test_transport_failure_after_actual_request_remains_unknown_without_refund(
    tmp_path: Path, monkeypatch
) -> None:
    environment, task_id = _admitted_image_task(
        tmp_path, monkeypatch, "image-worker-transport-failure-after-request"
    )
    stores: list[StoredMedia] = []
    runtime_config, provider_calls = _runtime_configuration(
        environment, _provider_body(), stores
    )

    class TimeoutClient:
        async def request(self, method, url, **_kwargs):
            provider_calls.append({"method": method, "url": url})
            raise TimeoutError("fixture timeout after request entry")

    runtime_config = replace(
        runtime_config,
        http_client_factory=lambda: _HttpClientContext(TimeoutClient()),
    )
    runtime = build_generation_worker_runtime(
        GenerationWorkerConfiguration(runtime_config, environment.capabilities)
    )

    outcome = runtime.run_claimed_once("image-single")

    assert outcome == "pending"
    assert len(provider_calls) == 1
    assert stores == []
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow).where(AITaskRow.id == task_id)
        ).scalar_one()
        unit = reader.execute(
            select(BillingUnitRow).where(BillingUnitRow.task_id == task_id)
        ).scalar_one()
        assert task.status == "reconciling"
        assert unit.result_status == "unknown"
        assert unit.billing_status == "charged"
        assert unit.refunded_amount == 0
        assert reader.execute(
            select(func.count()).select_from(CreditLogRow).where(
                CreditLogRow.type == "refund"
            )
        ).scalar_one() == 0
    environment.engine.dispose()


def test_outer_transport_failure_after_request_entry_cannot_refund(
    tmp_path: Path, monkeypatch
) -> None:
    environment, task_id = _admitted_image_task(
        tmp_path, monkeypatch, "image-worker-outer-transport-after-request"
    )
    stores: list[StoredMedia] = []
    runtime_config, provider_calls = _runtime_configuration(
        environment, _provider_body(), stores
    )
    original_run = openai_provider_module.asyncio.run

    def run_then_lose_result(coroutine):
        original_run(coroutine)
        raise RuntimeError("fixture failure after the request returned")

    monkeypatch.setattr(openai_provider_module.asyncio, "run", run_then_lose_result)
    runtime = build_generation_worker_runtime(
        GenerationWorkerConfiguration(runtime_config, environment.capabilities)
    )

    outcome = runtime.run_claimed_once("image-single")

    assert outcome == "pending"
    assert len(provider_calls) == 1
    assert stores == []
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow).where(AITaskRow.id == task_id)
        ).scalar_one()
        unit = reader.execute(
            select(BillingUnitRow).where(BillingUnitRow.task_id == task_id)
        ).scalar_one()
        assert task.status == "queued"
        assert unit.result_status == "pending"
        assert unit.billing_status == "charged"
        assert unit.refunded_amount == 0
        assert reader.execute(
            select(func.count()).select_from(CreditLogRow).where(
                CreditLogRow.type == "refund"
            )
        ).scalar_one() == 0
    environment.engine.dispose()


@pytest.mark.parametrize(
    ("code", "provider_request_started"),
    (
        ("frozen_execution_mismatch", False),
        ("committed_intent_mismatch", False),
        ("unsupported_reference_images", False),
        ("provider_transport_unavailable", True),
        ("provider_rejected", False),
    ),
)
def test_failure_code_without_matching_request_phase_cannot_settle(
    tmp_path: Path,
    monkeypatch,
    code: str,
    provider_request_started: bool,
) -> None:
    environment, claim = _claimed_image_task(
        tmp_path, monkeypatch, f"image-worker-failclosed-{code}-{provider_request_started}"
    )
    prepared = _intents(environment).prepare(claim)
    assert prepared.disposition == "committed"
    assert prepared.intent is not None and prepared.verified_input is not None
    writer = SqlAlchemyImageSingleResults(
        lambda: Session(environment.engine),
        lambda: datetime(2026, 10, 16, 2, 0, 30, tzinfo=timezone.utc),
        lambda: f"failclosed-{code}-{provider_request_started}",
    )

    outcome = writer.fail(
        claim,
        prepared.intent,
        prepared.verified_input,
        code=code,
        safe_detail="fixture safe failure",
        provider_request_started=provider_request_started,
    )

    assert outcome == "pending"
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow).where(AITaskRow.id == claim.scope.task_id)
        ).scalar_one()
        unit = reader.execute(
            select(BillingUnitRow).where(
                BillingUnitRow.id == prepared.verified_input.unit_id
            )
        ).scalar_one()
        assert task.status == "processing"
        assert task.result is None
        assert unit.result_status == "pending"
        assert unit.billing_status == "charged"
        assert unit.refunded_amount == 0
        assert reader.execute(
            select(func.count()).select_from(CreditLogRow).where(
                CreditLogRow.type == "refund"
            )
        ).scalar_one() == 0
    environment.engine.dispose()


def test_registered_worker_runs_one_provider_post_and_commits_original_result(
    tmp_path: Path, monkeypatch
) -> None:
    environment, task_id = _admitted_image_task(
        tmp_path, monkeypatch, "image-worker-success"
    )
    stores: list[StoredMedia] = []
    runtime_config, provider_calls = _runtime_configuration(
        environment, _provider_body(), stores
    )
    runtime = build_generation_worker_runtime(
        GenerationWorkerConfiguration(runtime_config, environment.capabilities)
    )

    outcome = runtime.run_claimed_once("image-single")

    assert outcome is not None
    assert outcome != "pending"
    assert outcome != "ownership_lost", (len(provider_calls), len(stores), outcome)
    assert outcome.result_ref
    assert len(provider_calls) == 1
    assert len(stores) == 1
    request = provider_calls[0]
    assert request["method"] == "POST"
    assert request["url"].endswith("/images/generations")
    assert request["follow_redirects"] is False
    assert "Authorization" in request["headers"]
    assert b"api-key" not in request["content"]

    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow).where(AITaskRow.id == task_id)
        ).scalar_one()
        unit = reader.execute(
            select(BillingUnitRow).where(BillingUnitRow.task_id == task.id)
        ).scalar_one()
        step = reader.execute(
            select(ExecutionStepRow).where(ExecutionStepRow.id == outcome.step_id)
        ).scalar_one()
        submission = reader.execute(
            select(ExternalSubmissionRow).where(
                ExternalSubmissionRow.id == outcome.submission_id
            )
        ).scalar_one()
        evidence = reader.execute(
            select(ResultEvidenceRow).where(ResultEvidenceRow.id == outcome.evidence_id)
        ).scalar_one()
        result = json.loads(task.result)
        assert task.status == "completed"
        assert result["url"] == stores[0].locator
        assert result["content_digest"] == stores[0].content_digest
        assert unit.billing_status in {"charged", "no_charge"}
        assert unit.result_status == "succeeded"
        assert step.execution_status == "result_observed"
        assert submission.submission_status == "completed"
        assert submission.external_task_id is None
        assert evidence.validity == "valid"
        assert evidence.result_location == stores[0].locator
        assert reader.execute(select(func.count()).select_from(CreditLogRow)).scalar_one() >= 1
        assert reader.execute(select(func.count()).select_from(UserCreditRow)).scalar_one() == 1
    environment.engine.dispose()


def test_registered_worker_edits_from_authenticated_ordered_references(
    tmp_path: Path, monkeypatch
) -> None:
    first_content = _PNG + b"first-reference"
    remote_content = _PNG + b"remote-reference"
    first_data_uri = "data:image/png;base64," + base64.b64encode(
        first_content
    ).decode("ascii")
    reference_url = "https://ref.example.test/reference.png?signature=fixture"
    references = (first_data_uri, reference_url, first_data_uri)
    environment, task_id = _admitted_image_task(
        tmp_path,
        monkeypatch,
        "image-worker-reference-edit",
        reference_images=references,
    )
    stores: list[StoredMedia] = []
    fetched_references: list[object] = []

    class Fetcher:
        async def fetch_public(self, reference, *, max_bytes, timeout_seconds):
            fetched_references.append((reference, max_bytes, timeout_seconds))
            return PreparedReference(
                kind="bytes",
                locator=None,
                content=remote_content,
                media_type="image/png",
            )

    runtime_config, provider_calls = _runtime_configuration(
        environment,
        _provider_body(),
        stores,
        public_media_fetcher_factory=Fetcher,
    )
    runtime = build_generation_worker_runtime(
        GenerationWorkerConfiguration(runtime_config, environment.capabilities)
    )

    outcome = runtime.run_claimed_once("image-single")

    assert outcome is not None and outcome != "pending" and outcome != "ownership_lost"
    assert len(fetched_references) == 1
    assert fetched_references[0][0].locator == references[1]
    assert fetched_references[0][1] == 10 * 1024 * 1024
    assert len(provider_calls) == 1
    request = provider_calls[0]
    assert request["method"] == "POST"
    assert request["url"].endswith("/images/edits")
    assert request["follow_redirects"] is False
    assert request["headers"]["Authorization"].startswith("Bearer ")
    assert request["headers"]["Content-Type"].startswith("multipart/form-data; boundary=")
    body = request["content"]
    assert isinstance(body, bytes)
    assert body.count(b'name="image"; filename="ref_0.png"') == 1
    assert body.count(b'name="image"; filename="ref_1.png"') == 1
    assert body.count(b'name="image"; filename="ref_2.png"') == 1
    assert body.count(b"Content-Type: image/png") == 3
    assert body.count(first_content) == 2
    assert body.count(remote_content) == 1
    first_position = body.index(first_content)
    remote_position = body.index(remote_content)
    repeated_position = body.rindex(first_content)
    assert first_position < remote_position < repeated_position
    assert b'name="prompt"' in body and b"Worker integration test" in body
    assert b'name="model"' in body and b"wire-joint-fixture-model" in body
    assert b'name="n"' in body and b"1" in body
    assert len(stores) == 1

    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow).where(AITaskRow.id == task_id)
        ).scalar_one()
        unit = reader.execute(
            select(BillingUnitRow).where(BillingUnitRow.task_id == task_id)
        ).scalar_one()
        receipt = reader.execute(
            select(GenerationResultReceiptRow.__table__).where(
                GenerationResultReceiptRow.__table__.c.task_id == task_id
            )
        ).mappings().one()
        evidence = reader.execute(
            select(ResultEvidenceRow.__table__).where(
                ResultEvidenceRow.__table__.c.id == receipt["evidence_id"]
            )
        ).mappings().one()
        result = json.loads(task.result)
        assert task.status == "completed"
        assert unit.result_status == "succeeded"
        assert unit.refunded_amount == 0
        assert receipt["owner_id"] == task.user_id
        assert receipt["unit_id"] == unit.id
        assert receipt["operation"] == "image.single"
        assert receipt["result_ref"] == outcome.result_ref == result["result_ref"]
        assert receipt["recoverable_locator"] == stores[0].locator == result["url"]
        assert receipt["content_digest"] == stores[0].content_digest
        assert receipt["automatic_application_allowed"] is True
        assert evidence["validity"] == "valid"
        assert evidence["result_location"] == receipt["recoverable_locator"]
        assert reader.execute(
            select(func.count()).select_from(CreditLogRow).where(
                CreditLogRow.type == "refund"
            )
        ).scalar_one() == 0
    environment.engine.dispose()


@pytest.mark.parametrize(
    ("failure_point", "expected_code"),
    (
        ("data_uri", "invalid_frozen_snapshot"),
        ("remote_after_data_uri", "invalid_frozen_snapshot"),
        ("reference_count", "request_body_too_large"),
    ),
)
def test_bad_reference_stops_before_provider_post_or_storage(
    tmp_path: Path,
    monkeypatch,
    failure_point: str,
    expected_code: str,
) -> None:
    valid_data_uri = _PNG_DATA_URI
    if failure_point == "data_uri":
        reference_images = ("data:image/png;base64,ZmFrZQ==",)
    elif failure_point == "remote_after_data_uri":
        reference_images = (valid_data_uri, "https://ref.example.test/bad-signature.png")
    else:
        reference_images = (valid_data_uri,) * 9
    environment, task_id = _admitted_image_task(
        tmp_path,
        monkeypatch,
        f"image-worker-invalid-reference-{failure_point}",
        reference_images=reference_images,
    )
    stores: list[StoredMedia] = []
    fetch_calls: list[object] = []

    class Fetcher:
        async def fetch_public(self, reference, *, max_bytes, timeout_seconds):
            fetch_calls.append(reference)
            return PreparedReference(
                kind="bytes",
                locator=None,
                content=b"not-a-png-signature",
                media_type="image/png",
            )

    runtime_config, provider_calls = _runtime_configuration(
        environment,
        _provider_body(),
        stores,
        public_media_fetcher_factory=Fetcher,
    )
    runtime = build_generation_worker_runtime(
        GenerationWorkerConfiguration(runtime_config, environment.capabilities)
    )

    outcome = runtime.run_claimed_once("image-single")

    assert outcome is None
    assert len(fetch_calls) == int(failure_point == "remote_after_data_uri")
    assert provider_calls == []
    assert stores == []
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow).where(AITaskRow.id == task_id)
        ).scalar_one()
        unit = reader.execute(
            select(BillingUnitRow).where(BillingUnitRow.task_id == task_id)
        ).scalar_one()
        assert task.status == "failed"
        assert json.loads(task.result)["code"] == expected_code
        assert unit.result_status == "failed"
        assert unit.refunded_amount == 3
        assert reader.execute(
            select(func.count()).select_from(GenerationResultReceiptRow)
        ).scalar_one() == 0
    environment.engine.dispose()


def test_lost_claim_during_reference_fetch_never_posts_to_provider(
    tmp_path: Path, monkeypatch
) -> None:
    reference_url = "https://ref.example.test/reference.png"
    environment, task_id = _admitted_image_task(
        tmp_path,
        monkeypatch,
        "image-worker-lost-during-reference-fetch",
        reference_images=(reference_url,),
    )
    stores: list[StoredMedia] = []
    fetch_entered = Event()
    proceed = Event()
    fetch_calls: list[object] = []

    class Fetcher:
        async def fetch_public(self, reference, *, max_bytes, timeout_seconds):
            fetch_calls.append(reference)
            fetch_entered.set()
            assert proceed.wait(5)
            return PreparedReference(
                kind="bytes",
                locator=None,
                content=_PNG,
                media_type="image/png",
            )

    runtime_config, provider_calls = _runtime_configuration(
        environment,
        _provider_body(),
        stores,
        public_media_fetcher_factory=Fetcher,
    )
    runtime = build_generation_worker_runtime(
        GenerationWorkerConfiguration(runtime_config, environment.capabilities)
    )
    outcomes: list[object] = []
    thread = _run_in_thread(runtime, outcomes)
    try:
        assert fetch_entered.wait(5)
        _cancel_task(environment, task_id)
    finally:
        proceed.set()
        thread.join(timeout=5)

    assert not thread.is_alive()
    assert outcomes == ["ownership_lost"]
    assert len(fetch_calls) == 1
    assert provider_calls == []
    assert stores == []
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow).where(AITaskRow.id == task_id)
        ).scalar_one()
        assert task.status == "cancelling"
        assert reader.execute(
            select(func.count()).select_from(GenerationResultReceiptRow)
        ).scalar_one() == 0
    environment.engine.dispose()


def test_lost_claim_during_reference_fetcher_factory_never_fetches_or_posts(
    tmp_path: Path, monkeypatch
) -> None:
    reference_url = "https://ref.example.test/reference.png"
    environment, task_id = _admitted_image_task(
        tmp_path,
        monkeypatch,
        "image-worker-lost-during-reference-fetcher-factory",
        reference_images=(reference_url,),
    )
    stores: list[StoredMedia] = []
    factory_calls: list[bool] = []
    fetch_calls: list[object] = []

    class Fetcher:
        async def fetch_public(self, reference, *, max_bytes, timeout_seconds):
            fetch_calls.append(reference)
            return PreparedReference(
                kind="bytes",
                locator=None,
                content=_PNG,
                media_type="image/png",
            )

    def fetcher_factory():
        factory_calls.append(True)
        _cancel_task(environment, task_id)
        return Fetcher()

    runtime_config, provider_calls = _runtime_configuration(
        environment,
        _provider_body(),
        stores,
        public_media_fetcher_factory=fetcher_factory,
    )
    runtime = build_generation_worker_runtime(
        GenerationWorkerConfiguration(runtime_config, environment.capabilities)
    )

    outcome = runtime.run_claimed_once("image-single")

    assert outcome == "ownership_lost"
    assert factory_calls == [True]
    assert fetch_calls == []
    assert provider_calls == []
    assert stores == []
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow).where(AITaskRow.id == task_id)
        ).scalar_one()
        assert task.status == "cancelling"
        assert reader.execute(
            select(func.count()).select_from(GenerationResultReceiptRow)
        ).scalar_one() == 0
    environment.engine.dispose()


def test_reference_intent_reentry_never_refetches_or_reposts(
    tmp_path: Path, monkeypatch
) -> None:
    reference_url = "https://ref.example.test/reference.png"
    environment, task_id = _admitted_image_task(
        tmp_path,
        monkeypatch,
        "image-worker-reference-intent-reentry",
        reference_images=(reference_url,),
    )
    stores: list[StoredMedia] = []
    fetch_calls: list[object] = []

    class Fetcher:
        async def fetch_public(self, reference, *, max_bytes, timeout_seconds):
            fetch_calls.append(reference)
            return PreparedReference(
                kind="bytes",
                locator=None,
                content=_PNG,
                media_type="image/png",
            )

    runtime_config, provider_calls = _runtime_configuration(
        environment,
        _provider_body(status=500),
        stores,
        response_status=500,
        public_media_fetcher_factory=Fetcher,
    )
    runtime = build_generation_worker_runtime(
        GenerationWorkerConfiguration(runtime_config, environment.capabilities)
    )

    first = runtime.run_claimed_once("image-single")
    second = runtime.run_claimed_once("image-single")

    assert first == "pending"
    assert second == "pending"
    assert len(fetch_calls) == 1
    assert len(provider_calls) == 1
    assert stores == []
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow).where(AITaskRow.id == task_id)
        ).scalar_one()
        unit = reader.execute(
            select(BillingUnitRow).where(BillingUnitRow.task_id == task_id)
        ).scalar_one()
        assert task.status == "reconciling"
        assert unit.result_status == "unknown"
        assert unit.refunded_amount == 0
        assert reader.execute(
            select(func.count()).select_from(CreditLogRow).where(
                CreditLogRow.type == "refund"
            )
        ).scalar_one() == 0
    environment.engine.dispose()


def test_provider_url_is_fetched_validated_and_stored_before_terminal_commit(
    tmp_path: Path, monkeypatch
) -> None:
    environment, task_id = _admitted_image_task(
        tmp_path, monkeypatch, "image-worker-provider-url-success"
    )
    stores: list[StoredMedia] = []
    provider_url = "https://cdn.example.test/generated.png?signature=fixture"
    provider_body = json.dumps({"data": [{"url": provider_url}]}).encode("utf-8")
    fetch_calls: list[tuple[object, int, float]] = []

    class Fetcher:
        async def fetch_public(self, reference, *, max_bytes, timeout_seconds):
            fetch_calls.append((reference, max_bytes, timeout_seconds))
            return PreparedReference(
                kind="bytes",
                locator=None,
                content=_PNG,
                media_type="image/png",
            )

    runtime_config, provider_calls = _runtime_configuration(
        environment,
        provider_body,
        stores,
        public_media_fetcher_factory=Fetcher,
    )
    runtime = build_generation_worker_runtime(
        GenerationWorkerConfiguration(runtime_config, environment.capabilities)
    )

    outcome = runtime.run_claimed_once("image-single")

    assert outcome is not None and outcome != "pending" and outcome != "ownership_lost"
    assert len(provider_calls) == 1
    assert len(fetch_calls) == 1
    assert fetch_calls[0][0].locator == provider_url
    assert fetch_calls[0][1] == 10 * 1024 * 1024
    assert len(stores) == 1
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow).where(AITaskRow.id == task_id)
        ).scalar_one()
        assert task.status == "completed"
        assert json.loads(task.result)["content_digest"] == hashlib.sha256(_PNG).hexdigest()
    environment.engine.dispose()


def test_unknown_create_is_persisted_and_a_reclaimed_task_never_posts_again(
    tmp_path: Path, monkeypatch
) -> None:
    environment, task_id = _admitted_image_task(
        tmp_path, monkeypatch, "image-worker-unknown"
    )
    stores: list[StoredMedia] = []
    runtime_config, provider_calls = _runtime_configuration(
        environment, _provider_body(status=500), stores, response_status=500
    )
    runtime = build_generation_worker_runtime(
        GenerationWorkerConfiguration(runtime_config, environment.capabilities)
    )

    first = runtime.run_claimed_once("image-single")
    second = runtime.run_claimed_once("image-single")

    assert first == "pending", (len(provider_calls), len(stores), first)
    assert second == "pending"
    assert len(provider_calls) == 1
    assert stores == []
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == task_id)
        ).mappings().one()
        unit = reader.execute(
            select(BillingUnitRow.__table__).where(
                BillingUnitRow.__table__.c.task_id == task_id
            )
        ).mappings().one()
        step = reader.execute(
            select(ExecutionStepRow.__table__).where(
                ExecutionStepRow.__table__.c.billing_unit_id == unit["id"]
            )
        ).mappings().one()
        submission = reader.execute(
            select(ExternalSubmissionRow.__table__).where(
                ExternalSubmissionRow.__table__.c.step_id == step["id"]
            )
        ).mappings().one()
        assert task["status"] == "reconciling"
        assert unit["result_status"] == "unknown"
        assert step["execution_status"] == "reconciling"
        assert submission["submission_status"] == "unknown"
        assert submission["external_task_id"] is None
        assert unit["billing_status"] in {"charged", "no_charge"}
        assert unit["refunded_amount"] == 0
    environment.engine.dispose()


def test_due_settlement_ignores_late_unknown_observation_and_replays_exact_refund(
    tmp_path: Path, monkeypatch
) -> None:
    environment, task_id = _admitted_image_task(
        tmp_path, monkeypatch, "image-worker-due-late-unknown"
    )
    stores: list[StoredMedia] = []
    runtime_config, provider_calls = _runtime_configuration(
        environment, _provider_body(status=500), stores, response_status=500
    )
    clock_state = {
        "now": datetime(2026, 10, 16, 2, 0, 30, tzinfo=timezone.utc)
    }
    runtime_config = replace(runtime_config, db_clock=lambda: clock_state["now"])
    runtime = build_generation_worker_runtime(
        GenerationWorkerConfiguration(runtime_config, environment.capabilities)
    )

    assert runtime.run_claimed_once("image-single") == "pending"
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
        ).mappings().one()
        unit = reader.execute(
            select(BillingUnitRow.__table__).where(
                BillingUnitRow.__table__.c.task_id == task_id
            )
        ).mappings().one()
        step = reader.execute(
            select(ExecutionStepRow.__table__).where(
                ExecutionStepRow.__table__.c.billing_unit_id == unit["id"]
            )
        ).mappings().one()
        submission = reader.execute(
            select(ExternalSubmissionRow.__table__).where(
                ExternalSubmissionRow.__table__.c.step_id == step["id"]
            )
        ).mappings().one()
        first_intent = unit["first_intent_at"].replace(tzinfo=timezone.utc)
        deadline = unit["deadline_at"].replace(tzinfo=timezone.utc)
        wallet_after_debit = reader.execute(
            select(UserCreditRow.credits).where(UserCreditRow.user_id == task["user_id"])
        ).scalar_one()

    late_observation_at = deadline + timedelta(seconds=1)
    with Session(environment.engine) as mutator:
        mutator.execute(
            insert(ResultEvidenceRow.__table__).values(
                id="late-unknown-observation",
                step_id=step["id"],
                submission_id=submission["id"],
                evidence_key=f"image-single-observation-v1:{submission['id']}",
                source="provider",
                evidence_kind="provider_image_locator",
                payload_digest="a" * 64,
                result_location="https://provider.example.test/late.png",
                result_digest="a" * 64,
                received_at=late_observation_at.replace(tzinfo=None),
                validity="unknown",
                application_status="pending",
                applied_at=None,
            )
        )
        mutator.commit()

    clock_state["now"] = late_observation_at
    assert runtime.run_claimed_once("image-single") == "pending"
    assert len(provider_calls) == 1
    assert stores == []

    with Session(environment.engine) as reader:
        task_after_due = reader.execute(
            select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
        ).mappings().one()
        unit_after_due = reader.execute(
            select(BillingUnitRow.__table__).where(
                BillingUnitRow.__table__.c.task_id == task_id
            )
        ).mappings().one()
        debit = reader.execute(
            select(CreditLogRow.__table__).where(
                CreditLogRow.__table__.c.business_key == f"debit:{unit['id']}"
            )
        ).mappings().one()
        refund = reader.execute(
            select(CreditLogRow.__table__).where(
                CreditLogRow.__table__.c.business_key == f"refund:{unit['id']}"
            )
        ).mappings().one()
        assert task_after_due["status"] == "failed"
        assert task_after_due["billing_status"] == "refunded"
        assert json.loads(task_after_due["result"])["code"] == "result_unconfirmed"
        assert unit_after_due["result_status"] == "failed"
        assert unit_after_due["refunded_amount"] == unit_after_due["debited_amount"] == 3
        assert unit_after_due["first_intent_at"].replace(tzinfo=timezone.utc) == first_intent
        assert unit_after_due["deadline_at"].replace(tzinfo=timezone.utc) == deadline
        assert reader.execute(
            select(UserCreditRow.credits).where(UserCreditRow.user_id == task["user_id"])
        ).scalar_one() == wallet_after_debit + unit_after_due["debited_amount"]
        assert refund["related_debit_id"] == debit["id"]
        assert debit["amount"] == -unit_after_due["debited_amount"]
        assert refund["amount"] == unit_after_due["debited_amount"] == 3
        assert debit["amount"] + refund["amount"] == 0
        assert reader.execute(
            select(func.count()).select_from(CreditLogRow).where(
                CreditLogRow.business_key == f"refund:{unit['id']}"
            )
        ).scalar_one() == 1

    results = SqlAlchemyImageSingleResults(
        lambda: Session(environment.engine),
        lambda: clock_state["now"],
        lambda: "unused-due-replay-id",
    )
    assert results.settle_due(
        OwnerTaskScope(task["user_id"], task_id, "image.single")
    ) == "settled"
    assert runtime.run_claimed_once("image-single") == "pending"
    with Session(environment.engine) as reader:
        assert reader.execute(
            select(UserCreditRow.credits).where(UserCreditRow.user_id == task["user_id"])
        ).scalar_one() == wallet_after_debit + unit_after_due["debited_amount"]
        assert reader.execute(
            select(func.count()).select_from(CreditLogRow).where(
                CreditLogRow.business_key == f"debit:{unit['id']}"
            )
        ).scalar_one() == 1
        assert reader.execute(
            select(func.count()).select_from(CreditLogRow).where(
                CreditLogRow.business_key == f"refund:{unit['id']}"
            )
        ).scalar_one() == 1
    environment.engine.dispose()


def test_timely_linked_image_success_is_retained_without_due_refund(
    tmp_path: Path, monkeypatch
) -> None:
    environment, task_id = _admitted_image_task(
        tmp_path, monkeypatch, "image-worker-due-timely-success"
    )
    stores: list[StoredMedia] = []
    runtime_config, provider_calls = _runtime_configuration(
        environment, _provider_body(), stores
    )
    runtime = build_generation_worker_runtime(
        GenerationWorkerConfiguration(runtime_config, environment.capabilities)
    )

    outcome = runtime.run_claimed_once("image-single")

    assert outcome is not None and outcome != "pending" and outcome != "ownership_lost"
    assert len(provider_calls) == 1
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
        ).mappings().one()
        unit = reader.execute(
            select(BillingUnitRow.__table__).where(
                BillingUnitRow.__table__.c.task_id == task_id
            )
        ).mappings().one()
    deadline = unit["deadline_at"].replace(tzinfo=timezone.utc)
    results = SqlAlchemyImageSingleResults(
        lambda: Session(environment.engine),
        lambda: deadline + timedelta(microseconds=1),
        lambda: "unused-success-check-id",
    )

    assert results.settle_due(
        OwnerTaskScope(task["user_id"], task_id, "image.single")
    ) == "retained"
    with Session(environment.engine) as reader:
        assert reader.execute(
            select(func.count()).select_from(CreditLogRow).where(
                CreditLogRow.business_key == f"refund:{unit['id']}"
            )
        ).scalar_one() == 0


def _unresolved_due_case(
    tmp_path,
    monkeypatch,
    name,
    *,
    model_source=None,
    wallet_credits: int | None = 20,
):
    environment, task_id = _admitted_image_task(
        tmp_path, monkeypatch, name,
        model_source=model_source, wallet_credits=wallet_credits,
    )
    stores: list[StoredMedia] = []
    runtime_config, provider_calls = _runtime_configuration(
        environment, _provider_body(status=500), stores, response_status=500
    )
    clock_state = {"now": datetime(2026, 10, 16, 2, 0, 30, tzinfo=timezone.utc)}
    runtime_config = replace(runtime_config, db_clock=lambda: clock_state["now"])
    runtime = build_generation_worker_runtime(
        GenerationWorkerConfiguration(runtime_config, environment.capabilities)
    )
    assert runtime.run_claimed_once("image-single") == "pending"
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
        ).mappings().one()
        unit = reader.execute(
            select(BillingUnitRow.__table__).where(
                BillingUnitRow.__table__.c.task_id == task_id
            )
        ).mappings().one()
    assert json.loads(task["result"]) == {
        "message": "图片生成结果需要恢复确认",
        "status": "reconciling",
    }
    deadline = unit["deadline_at"].replace(tzinfo=timezone.utc)
    clock_state["now"] = deadline + timedelta(microseconds=1)
    results = SqlAlchemyImageSingleResults(
        lambda: Session(environment.engine),
        lambda: clock_state["now"],
        lambda: "unused-due-test-id",
    )
    return environment, task_id, task, unit, clock_state, results, provider_calls, stores, deadline


def test_zero_cost_byok_deadline_settlement_needs_no_wallet_or_ledger(
    tmp_path: Path, monkeypatch
) -> None:
    environment, task_id, task, unit, _, results, _, _, _ = _unresolved_due_case(
        tmp_path, monkeypatch, "image-worker-due-byok-no-charge",
        model_source=admission_staging_module.ModelSource.USER,
        wallet_credits=None,
    )
    assert unit["debited_amount"] == 0
    assert unit["billing_status"] == "no_charge"
    with Session(environment.engine) as reader:
        assert reader.execute(select(func.count()).select_from(UserCreditRow)).scalar_one() == 0
        assert reader.execute(
            select(func.count()).select_from(CreditLogRow).where(
                CreditLogRow.task_id == task_id
            )
        ).scalar_one() == 0
    scope = OwnerTaskScope(task["user_id"], task_id, "image.single")
    assert results.settle_due(scope) == "settled"
    assert results.settle_due(scope) == "settled"
    with Session(environment.engine) as reader:
        task_after = reader.execute(
            select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
        ).mappings().one()
        unit_after = reader.execute(
            select(BillingUnitRow.__table__).where(BillingUnitRow.__table__.c.id == unit["id"])
        ).mappings().one()
        assert task_after["billing_status"] == unit_after["billing_status"] == "no_charge"
        assert unit_after["refunded_amount"] == 0
        assert reader.execute(
            select(func.count()).select_from(CreditLogRow).where(
                CreditLogRow.task_id == task_id
            )
        ).scalar_one() == 0
        assert reader.execute(select(func.count()).select_from(UserCreditRow)).scalar_one() == 0
    environment.engine.dispose()

def test_wrong_graph_isolated_from_due_first_write_replay_and_retained_success(
    tmp_path: Path, monkeypatch
) -> None:
    due_path = tmp_path / "due-graph"
    due_path.mkdir()
    environment, task_id, task, unit, clock_state, results, _, _, _ = _unresolved_due_case(
        due_path, monkeypatch, "image-worker-due-wrong-graph"
    )
    original_request = task["request_data"]
    wrong_request = dict(json.loads(original_request))
    wrong_request["target_ref"] = wrong_request["protected_ref"] + "-other#target"
    with Session(environment.engine) as mutator:
        mutator.execute(
            update(AITaskRow.__table__)
            .where(AITaskRow.__table__.c.id == task_id)
            .values(request_data=json.dumps(wrong_request, sort_keys=True))
        )
        mutator.commit()
    scope = OwnerTaskScope(task["user_id"], task_id, "image.single")
    assert results.settle_due(scope) == "pending"
    with Session(environment.engine) as reader:
        assert reader.execute(
            select(func.count()).select_from(CreditLogRow).where(
                CreditLogRow.business_key == f"refund:{unit['id']}"
            )
        ).scalar_one() == 0

    with Session(environment.engine) as mutator:
        mutator.execute(
            update(AITaskRow.__table__)
            .where(AITaskRow.__table__.c.id == task_id)
            .values(request_data=original_request)
        )
        mutator.commit()
    original_confirm = results._confirm_due

    def break_reference_before_ack(ack_scope, expected, original_graph, *, stop_signal=None):
        with Session(environment.engine) as mutator:
            mutator.execute(
                update(AITaskRow.__table__)
                .where(AITaskRow.__table__.c.id == task_id)
                .values(request_data=json.dumps(wrong_request, sort_keys=True))
            )
            mutator.commit()
        return original_confirm(ack_scope, expected, original_graph, stop_signal=stop_signal)

    with monkeypatch.context() as patch_context:
        patch_context.setattr(results, "_confirm_due", break_reference_before_ack)
        assert results.settle_due(scope) == "pending"
    with Session(environment.engine) as reader:
        assert reader.execute(
            select(func.count()).select_from(CreditLogRow).where(
                CreditLogRow.business_key == f"refund:{unit['id']}"
            )
        ).scalar_one() == 1
    with Session(environment.engine) as mutator:
        mutator.execute(
            update(AITaskRow.__table__)
            .where(AITaskRow.__table__.c.id == task_id)
            .values(request_data=original_request)
        )
        mutator.commit()
    assert results.settle_due(scope) == "settled"
    with Session(environment.engine) as mutator:
        mutator.execute(
            update(AITaskRow.__table__)
            .where(AITaskRow.__table__.c.id == task_id)
            .values(request_data=json.dumps(wrong_request, sort_keys=True))
        )
        mutator.commit()
    assert results.settle_due(scope) == "pending"
    with Session(environment.engine) as reader:
        assert reader.execute(
            select(func.count()).select_from(CreditLogRow).where(
                CreditLogRow.business_key == f"refund:{unit['id']}"
            )
        ).scalar_one() == 1
    environment.engine.dispose()

    retained_path = tmp_path / "retained-graph"
    retained_path.mkdir()
    successful_environment, success_task_id = _admitted_image_task(
        retained_path, monkeypatch, "image-worker-due-wrong-graph-retained"
    )
    stores: list[StoredMedia] = []
    runtime_config, _ = _runtime_configuration(
        successful_environment, _provider_body(), stores
    )
    runtime = build_generation_worker_runtime(
        GenerationWorkerConfiguration(
            runtime_config, successful_environment.capabilities
        )
    )
    assert runtime.run_claimed_once("image-single") not in {"pending", "ownership_lost"}
    with Session(successful_environment.engine) as reader:
        success_task = reader.execute(
            select(AITaskRow.__table__).where(
                AITaskRow.__table__.c.id == success_task_id
            )
        ).mappings().one()
        success_unit = reader.execute(
            select(BillingUnitRow.__table__).where(
                BillingUnitRow.__table__.c.task_id == success_task_id
            )
        ).mappings().one()
    success_deadline = success_unit["deadline_at"].replace(tzinfo=timezone.utc)
    success_results = SqlAlchemyImageSingleResults(
        lambda: Session(successful_environment.engine),
        lambda: success_deadline + timedelta(microseconds=1),
        lambda: "unused-retained-test-id",
    )
    success_scope = OwnerTaskScope(success_task["user_id"], success_task_id, "image.single")
    assert success_results.settle_due(success_scope) == "retained"
    bad_success_request = dict(json.loads(success_task["request_data"]))
    bad_success_request["target_ref"] = (
        bad_success_request["protected_ref"] + "-other#target"
    )
    with Session(successful_environment.engine) as mutator:
        mutator.execute(
            update(AITaskRow.__table__)
            .where(AITaskRow.__table__.c.id == success_task_id)
            .values(request_data=json.dumps(bad_success_request, sort_keys=True))
        )
        mutator.commit()
    assert success_results.settle_due(success_scope) == "pending"
    with Session(successful_environment.engine) as reader:
        assert reader.execute(
            select(func.count()).select_from(CreditLogRow).where(
                CreditLogRow.business_key == f"refund:{success_unit['id']}"
            )
        ).scalar_one() == 0
    successful_environment.engine.dispose()


def _stage_follow_up_image_task(environment, raw_key: str) -> str:
    caller = _joint_caller(environment.engine)
    staged = _joint_prepare_and_stage(
        environment.binding,
        caller,
        operation="image.single",
        raw_key=raw_key,
        request={
            "model_id": "model-1",
            "prompt": "Follow-up legal wallet transaction",
            "target": {"kind": "standalone"},
            "size": None,
            "reference_images": [],
        },
    )
    assert isinstance(staged, AdmissionStaged)
    task_id = staged.ordered_tasks[0].task_id
    caller.commit()
    caller.close()
    return task_id


def test_due_refund_rolls_back_after_wallet_update_then_retries_once(
    tmp_path: Path, monkeypatch
) -> None:
    import haoai_backend.generation_core.image_single_results as results_module

    environment, task_id, task, unit, _, results, _, _, _ = _unresolved_due_case(
        tmp_path, monkeypatch, "image-worker-due-wallet-rollback"
    )
    scope = OwnerTaskScope(task["user_id"], task_id, "image.single")
    original_apply_refund = results_module._apply_refund

    def fail_after_financial_writes(session, settlement, now):
        original_apply_refund(session, settlement, now)
        raise RuntimeError("injected failure after wallet and refund writes")

    monkeypatch.setattr(results_module, "_apply_refund", fail_after_financial_writes)
    assert results.settle_due(scope) == "pending"
    with Session(environment.engine) as reader:
        task_after = reader.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == task_id)
        ).mappings().one()
        unit_after = reader.execute(
            select(BillingUnitRow.__table__).where(BillingUnitRow.__table__.c.id == unit["id"])
        ).mappings().one()
        assert task_after["status"] == "reconciling"
        assert unit_after["result_status"] == "unknown"
        assert reader.execute(
            select(UserCreditRow.credits).where(UserCreditRow.user_id == task["user_id"])
        ).scalar_one() == 17
        assert reader.execute(
            select(func.count()).select_from(CreditLogRow).where(
                CreditLogRow.business_key == f"refund:{unit['id']}"
            )
        ).scalar_one() == 0

    monkeypatch.undo()
    assert results.settle_due(scope) == "settled"
    assert results.settle_due(scope) == "settled"
    with Session(environment.engine) as reader:
        assert reader.execute(
            select(func.count()).select_from(CreditLogRow).where(
                CreditLogRow.business_key == f"refund:{unit['id']}"
            )
        ).scalar_one() == 1
    environment.engine.dispose()


def test_due_commit_ack_and_later_wallet_debits_keep_exact_replay_settled(
    tmp_path: Path, monkeypatch
) -> None:
    environment, task_id, task, unit, _, results, _, _, _ = _unresolved_due_case(
        tmp_path, monkeypatch, "image-worker-due-commit-ack-and-wallet"
    )
    scope = OwnerTaskScope(task["user_id"], task_id, "image.single")
    original_confirm = results._confirm_due
    follow_up_task_ids: list[str] = []

    def create_follow_up_before_ack(ack_scope, expected, original_graph, *, stop_signal=None):
        follow_up_task_ids.append(
            _stage_follow_up_image_task(environment, "image-worker-due-wallet-before-ack")
        )
        return original_confirm(
            ack_scope, expected, original_graph, stop_signal=stop_signal
        )

    monkeypatch.setattr(results, "_confirm_due", create_follow_up_before_ack)
    commit_ack_lost = False

    def raise_after_first_commit(session) -> None:
        nonlocal commit_ack_lost
        if not commit_ack_lost:
            commit_ack_lost = True
            raise RuntimeError("injected connection loss after durable commit")

    event.listen(Session, "after_commit", raise_after_first_commit)
    try:
        assert results.settle_due(scope) == "settled"
    finally:
        event.remove(Session, "after_commit", raise_after_first_commit)
    assert commit_ack_lost

    assert len(follow_up_task_ids) == 1
    with Session(environment.engine) as reader:
        refund = reader.execute(
            select(CreditLogRow.__table__).where(
                CreditLogRow.business_key == f"refund:{unit['id']}"
            )
        ).mappings().one()
        follow_up_debit = reader.execute(
            select(CreditLogRow.__table__).where(
                CreditLogRow.task_id == follow_up_task_ids[0],
                CreditLogRow.type == "usage",
            )
        ).mappings().one()
        assert follow_up_debit["amount"] == -3
        assert follow_up_debit["user_id"] == task["user_id"]
        assert refund["balance_after"] == 20
        assert reader.execute(
            select(UserCreditRow.credits).where(UserCreditRow.user_id == task["user_id"])
        ).scalar_one() == 17

    monkeypatch.setattr(results, "_confirm_due", original_confirm)
    later_task_id = _stage_follow_up_image_task(
        environment, "image-worker-due-wallet-after-settlement"
    )
    with Session(environment.engine) as reader:
        later_debit = reader.execute(
            select(CreditLogRow.__table__).where(
                CreditLogRow.task_id == later_task_id,
                CreditLogRow.type == "usage",
            )
        ).mappings().one()
        assert later_debit["amount"] == -3
        balance_before_replay = reader.execute(
            select(UserCreditRow.credits).where(UserCreditRow.user_id == task["user_id"])
        ).scalar_one()
        assert balance_before_replay == 14
    assert results.settle_due(scope) == "settled"
    with Session(environment.engine) as reader:
        assert reader.execute(
            select(UserCreditRow.credits).where(UserCreditRow.user_id == task["user_id"])
        ).scalar_one() == balance_before_replay
        assert reader.execute(
            select(func.count()).select_from(CreditLogRow).where(
                CreditLogRow.business_key == f"refund:{unit['id']}"
            )
        ).scalar_one() == 1
    environment.engine.dispose()


def test_due_shutdown_latches_before_graph_and_after_financial_locks(
    tmp_path: Path, monkeypatch
) -> None:
    import haoai_backend.generation_core.image_single_results as image_single_results_module

    environment, task_id, task, unit, _, results, _, _, _ = _unresolved_due_case(
        tmp_path, monkeypatch, "image-worker-due-shutdown-locks"
    )
    scope = OwnerTaskScope(task["user_id"], task_id, "image.single")
    stop_signal = Event()
    stop_signal.set()
    assert results.settle_due(scope, stop_signal=stop_signal) == "pending"

    original_lock = image_single_results_module._lock_image_graph
    stop_signal.clear()

    def lock_then_stop(session, authority):
        graph = original_lock(session, authority)
        stop_signal.set()
        return graph

    monkeypatch.setattr(image_single_results_module, "_lock_image_graph", lock_then_stop)
    assert results.settle_due(scope, stop_signal=stop_signal) == "pending"
    monkeypatch.setattr(image_single_results_module, "_lock_image_graph", original_lock)

    original_financial_state = results._with_financial_state
    stop_signal.clear()

    def financial_lock_then_stop(session, locked_scope, graph, expected):
        decision = original_financial_state(session, locked_scope, graph, expected)
        stop_signal.set()
        return decision

    monkeypatch.setattr(results, "_with_financial_state", financial_lock_then_stop)
    assert results.settle_due(scope, stop_signal=stop_signal) == "pending"
    with Session(environment.engine) as reader:
        current_unit = reader.execute(
            select(BillingUnitRow.__table__).where(
                BillingUnitRow.__table__.c.id == unit["id"]
            )
        ).mappings().one()
        assert current_unit["result_status"] == "unknown"
        assert reader.execute(
            select(func.count()).select_from(CreditLogRow).where(
                CreditLogRow.business_key == f"refund:{unit['id']}"
            )
        ).scalar_one() == 0
    environment.engine.dispose()


def test_due_discovery_rotates_over_more_than_one_page_and_honors_stop(
    monkeypatch
) -> None:
    from types import SimpleNamespace

    from haoai_backend.generation_core.image_single_recovery import (
        SqlAlchemyImageSingleRecovery,
    )

    now = datetime(2026, 10, 16, 2, 0, 30, tzinfo=timezone.utc)
    candidates = [
        SimpleNamespace(
            user_id="owner-1",
            task_id=f"task-{index:02d}",
            created_at=now,
        )
        for index in range(35)
    ]

    class RecordingResults:
        def __init__(self) -> None:
            self.task_ids: list[str] = []

        def settle_due(self, scope, *, stop_signal=None):
            self.task_ids.append(scope.task_id)
            if stop_signal is not None:
                stop_signal.set()
            return "pending"

    def read_page(self, observed_now, cursor, limit):
        eligible = [
            candidate
            for candidate in candidates
            if cursor is None
            or (candidate.created_at, candidate.task_id) > cursor
        ]
        return eligible[:limit]

    monkeypatch.setattr(SqlAlchemyImageSingleRecovery, "_read_candidates", read_page)
    recorder = RecordingResults()
    recovery = SqlAlchemyImageSingleRecovery(lambda: None, lambda: now, recorder)
    recovery.scan_due()
    assert len(recorder.task_ids) == 32
    recovery.scan_due()
    assert len(set(recorder.task_ids)) == 35

    stopped_recorder = RecordingResults()
    stopped_recovery = SqlAlchemyImageSingleRecovery(
        lambda: None, lambda: now, stopped_recorder, page_size=3
    )
    stop_signal = Event()
    stopped_recovery.scan_due(stop_signal=stop_signal)
    assert len(stopped_recorder.task_ids) == 1


def test_due_sql_discovery_pages_past_invalid_graph_to_later_due_task(
    tmp_path: Path, monkeypatch
) -> None:
    from haoai_backend.generation_core.image_single_recovery import (
        SqlAlchemyImageSingleRecovery,
    )

    work_path = tmp_path / "sql-discovery"
    work_path.mkdir()
    environment, first_task_id = _admitted_image_task(
        work_path, monkeypatch, "image-worker-due-sql-first"
    )
    second_task_id = _stage_follow_up_image_task(
        environment, "image-worker-due-sql-second"
    )
    stores: list[StoredMedia] = []
    runtime_config, provider_calls = _runtime_configuration(
        environment, _provider_body(status=500), stores, response_status=500
    )
    runtime = build_generation_worker_runtime(
        GenerationWorkerConfiguration(runtime_config, environment.capabilities)
    )
    assert runtime.run_claimed_once("image-single") == "pending"
    assert runtime.run_claimed_once("image-single") == "pending"
    assert len(provider_calls) == 2

    pending_task_id = _stage_follow_up_image_task(
        environment, "image-worker-due-sql-no-intent"
    )

    with Session(environment.engine) as reader:
        due_rows = reader.execute(
            select(AITaskRow.__table__)
            .where(
                AITaskRow.__table__.c.id.in_((first_task_id, second_task_id)),
                AITaskRow.__table__.c.status == "reconciling",
            )
            .order_by(AITaskRow.__table__.c.created_at, AITaskRow.__table__.c.id)
        ).mappings().all()
        assert len(due_rows) == 2
        invalid_first = due_rows[0]
        invalid_request = dict(json.loads(invalid_first["request_data"]))
        invalid_request["target_ref"] = (
            invalid_request["protected_ref"] + "-other#target"
        )
        invalid_task_id = invalid_first["id"]
        later_due_task_id = due_rows[1]["id"]
    with Session(environment.engine) as mutator:
        mutator.execute(
            update(AITaskRow.__table__)
            .where(AITaskRow.__table__.c.id == invalid_task_id)
            .values(request_data=json.dumps(invalid_request, sort_keys=True))
        )
        mutator.commit()

    due_now = datetime(2026, 10, 16, 4, 0, 31, tzinfo=timezone.utc)
    results = SqlAlchemyImageSingleResults(
        lambda: Session(environment.engine),
        lambda: due_now,
        lambda: "unused-sql-discovery-id",
    )
    recovery = SqlAlchemyImageSingleRecovery(
        lambda: Session(environment.engine),
        lambda: due_now,
        results,
        page_size=1,
    )
    assert recovery.scan_due() == ("pending",)
    assert recovery.scan_due() == ("settled",)
    with Session(environment.engine) as reader:
        invalid_task = reader.execute(
            select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == invalid_task_id)
        ).mappings().one()
        later_task = reader.execute(
            select(AITaskRow.__table__).where(
                AITaskRow.__table__.c.id == later_due_task_id
            )
        ).mappings().one()
        later_unit = reader.execute(
            select(BillingUnitRow.__table__).where(
                BillingUnitRow.__table__.c.task_id == later_due_task_id
            )
        ).mappings().one()
        pending_task = reader.execute(
            select(AITaskRow.__table__).where(
                AITaskRow.__table__.c.id == pending_task_id
            )
        ).mappings().one()
        assert invalid_task["status"] == "reconciling"
        assert later_task["status"] == "failed"
        assert later_unit["refunded_amount"] == later_unit["debited_amount"] == 3
        assert pending_task["status"] == "queued"
    environment.engine.dispose()


def test_runtime_shutdown_signals_active_recovery_and_reports_resource_receipt(
    tmp_path: Path, monkeypatch
) -> None:
    from haoai_backend.generation_core.image_single_recovery import (
        SqlAlchemyImageSingleRecovery,
    )

    environment, task_id = _admitted_image_task(
        tmp_path, monkeypatch, "image-worker-due-runtime-shutdown"
    )
    stores: list[StoredMedia] = []
    runtime_config, provider_calls = _runtime_configuration(
        environment, _provider_body(status=500), stores, response_status=500
    )
    runtime = build_generation_worker_runtime(
        GenerationWorkerConfiguration(runtime_config, environment.capabilities)
    )
    scan_started = Event()
    stop_observed = Event()
    release_scan = Event()
    stop_signals: list[Event] = []

    def blocked_scan(self, *, stop_signal=None):
        assert stop_signal is not None
        stop_signals.append(stop_signal)
        scan_started.set()
        assert stop_signal.wait()
        stop_observed.set()
        assert release_scan.wait()
        return ()

    monkeypatch.setattr(SqlAlchemyImageSingleRecovery, "scan_due", blocked_scan)
    outcomes: list[object] = []
    worker = Thread(target=lambda: outcomes.append(runtime.run_claimed_once("image-single")))
    worker.start()
    assert scan_started.wait(timeout=2.0)

    def release_after_stop() -> None:
        assert stop_signals[0].wait()
        release_scan.set()

    releaser = Thread(target=release_after_stop)
    releaser.start()
    try:
        receipt = runtime.shutdown("test shutdown")
        worker.join(timeout=2.0)
        releaser.join(timeout=2.0)
        assert not worker.is_alive()
        assert not releaser.is_alive()
        assert stop_observed.is_set()
        assert outcomes == ["pending"]
        assert receipt.released_resources == ("recovery:image-single",)
        assert receipt.timed_out is False
        assert receipt.unreleased_resources == ()
        assert receipt.recovery_plan_refs == ("recovery:image-single",)
        assert receipt.revoked_claim_refs == ()
        assert provider_calls == []
        assert stores == []
        with Session(environment.engine) as reader:
            task = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
            ).mappings().one()
            assert task["user_cancelled_at"] is None
            assert task["status"] == "queued"
            assert reader.execute(
                select(func.count()).select_from(CreditLogRow).where(
                    CreditLogRow.business_key.like("refund:%"),
                    CreditLogRow.task_id == task_id,
                )
            ).scalar_one() == 0
    finally:
        for signal in stop_signals:
            signal.set()
        release_scan.set()
        worker.join(timeout=2.0)
        releaser.join(timeout=2.0)
        environment.engine.dispose()


@pytest.mark.parametrize(
    ("offset", "expected"),
    (
        (timedelta(microseconds=-1), "retained"),
        (timedelta(0), "settled"),
        (timedelta(microseconds=1), "settled"),
    ),
)
def test_due_classifies_actual_receipt_and_evidence_at_deadline_boundary(
    tmp_path: Path, monkeypatch, offset: timedelta, expected: str
) -> None:
    from haoai_backend.generation_core.image_single_recovery import (
        SqlAlchemyImageSingleRecovery,
    )

    environment, task_id = _admitted_image_task(
        tmp_path, monkeypatch, f"image-worker-due-receipt-boundary-{offset}"
    )
    intent_time = datetime(2026, 10, 16, 2, 0, 30, tzinfo=timezone.utc)
    deadline = intent_time + timedelta(hours=2)
    clock_state = {"now": intent_time}
    stores: list[StoredMedia] = []

    def advance_after_storage(_stored: StoredMedia) -> None:
        with Session(environment.engine) as lease_writer:
            lease_writer.execute(
                update(AITaskRow.__table__)
                .where(AITaskRow.__table__.c.id == task_id)
                .values(
                    lease_until=(deadline + timedelta(minutes=1)).replace(tzinfo=None)
                )
            )
            lease_writer.commit()
        clock_state["now"] = deadline + offset

    runtime_config, provider_calls = _runtime_configuration(
        environment,
        _provider_body(),
        stores,
        media_store_factory=lambda: _Storage(stores, on_store=advance_after_storage),
    )
    runtime_config = replace(runtime_config, db_clock=lambda: clock_state["now"])
    runtime = build_generation_worker_runtime(
        GenerationWorkerConfiguration(runtime_config, environment.capabilities)
    )
    outcome = runtime.run_claimed_once("image-single")
    assert len(provider_calls) == 1
    assert len(stores) == 1

    with Session(environment.engine) as reader:
        task_before_due = reader.execute(
            select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
        ).mappings().one()
        unit = reader.execute(
            select(BillingUnitRow.__table__).where(
                BillingUnitRow.__table__.c.task_id == task_id
            )
        ).mappings().one()
        step_id = reader.execute(
            select(ExecutionStepRow.__table__.c.id).where(
                ExecutionStepRow.__table__.c.billing_unit_id == unit["id"]
            )
        ).scalar_one()
        submission_id = reader.execute(
            select(ExternalSubmissionRow.__table__.c.id).where(
                ExternalSubmissionRow.__table__.c.step_id == step_id
            )
        ).scalar_one()
        receipts = reader.execute(
            select(GenerationResultReceiptRow.__table__).where(
                GenerationResultReceiptRow.__table__.c.task_id == task_id
            )
        ).mappings().all()
        evidence = reader.execute(
            select(ResultEvidenceRow.__table__).where(
                ResultEvidenceRow.__table__.c.submission_id == submission_id
            )
        ).mappings().all()
        wallet_after_debit = reader.execute(
            select(UserCreditRow.credits).where(
                UserCreditRow.user_id == task_before_due["user_id"]
            )
        ).scalar_one()
        assert wallet_after_debit == 17
        assert unit["first_intent_at"].replace(tzinfo=timezone.utc) == intent_time
        assert unit["deadline_at"].replace(tzinfo=timezone.utc) == deadline
        if offset < timedelta(0):
            assert task_before_due["status"] == "completed"
            assert len(receipts) == 1
            receipt = receipts[0]
            assert receipt["validated_at"].replace(tzinfo=timezone.utc) == deadline + offset
            assert receipt["generation_fulfilled_at"].replace(tzinfo=timezone.utc) == deadline + offset
            assert len(evidence) == 1
            assert evidence[0]["validity"] == "valid"
            assert evidence[0]["result_digest"] == receipt["content_digest"]
            assert evidence[0]["result_location"] == receipt["recoverable_locator"]
        else:
            assert outcome == "ownership_lost"
            assert task_before_due["status"] == "queued"
            assert task_before_due["claimed_by"] is None
            assert task_before_due["claim_token"] is None
            assert task_before_due["lease_until"] is None
            assert receipts == []
            assert all(row["validity"] != "valid" for row in evidence)

    clock_state["now"] = deadline + timedelta(microseconds=1)
    results = SqlAlchemyImageSingleResults(
        lambda: Session(environment.engine),
        lambda: clock_state["now"],
        lambda: "unused-receipt-boundary-id",
    )
    scope = OwnerTaskScope(task_before_due["user_id"], task_id, "image.single")
    recovery = SqlAlchemyImageSingleRecovery(
        lambda: Session(environment.engine),
        lambda: clock_state["now"],
        results,
        page_size=1,
    )
    if offset < timedelta(0):
        assert results.settle_due(scope) == expected
        assert recovery.scan_due() == ()
    else:
        assert recovery.scan_due() == (expected,)
        assert results.settle_due(scope) == expected

    with Session(environment.engine) as reader:
        terminal = reader.execute(
            select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
        ).mappings().one()
        terminal_unit = reader.execute(
            select(BillingUnitRow.__table__).where(
                BillingUnitRow.__table__.c.task_id == task_id
            )
        ).mappings().one()
        debit_rows = reader.execute(
            select(CreditLogRow.__table__).where(
                CreditLogRow.__table__.c.business_key == f"debit:{unit['id']}"
            )
        ).mappings().all()
        refund_rows = reader.execute(
            select(CreditLogRow.__table__).where(
                CreditLogRow.__table__.c.business_key == f"refund:{unit['id']}"
            )
        ).mappings().all()
        wallet_after_recovery = reader.execute(
            select(UserCreditRow.credits).where(
                UserCreditRow.user_id == task_before_due["user_id"]
            )
        ).scalar_one()
        assert terminal["status"] == ("completed" if expected == "retained" else "failed")
        assert terminal["claimed_by"] is None
        assert terminal["claim_token"] is None
        assert terminal["lease_until"] is None
        assert terminal_unit["first_intent_at"].replace(tzinfo=timezone.utc) == intent_time
        assert terminal_unit["deadline_at"].replace(tzinfo=timezone.utc) == deadline
        assert len(debit_rows) == 1
        debit = debit_rows[0]
        assert debit["amount"] == -unit["debited_amount"] == -3
        if expected == "retained":
            assert refund_rows == []
            assert terminal_unit["refunded_amount"] == 0
            assert wallet_after_recovery == wallet_after_debit == 17
        else:
            assert len(refund_rows) == 1
            refund = refund_rows[0]
            assert refund["related_debit_id"] == debit["id"]
            assert refund["amount"] == unit["debited_amount"] == 3
            assert debit["amount"] + refund["amount"] == 0
            assert terminal_unit["refunded_amount"] == unit["debited_amount"] == 3
            assert wallet_after_recovery == wallet_after_debit + unit["debited_amount"] == 20
    environment.engine.dispose()


@pytest.mark.parametrize(
    ("offset", "expected"),
    (
        (timedelta(microseconds=-1), True),
        (timedelta(0), False),
        (timedelta(microseconds=1), False),
    ),
)
def test_timely_success_deadline_is_strictly_before(
    offset: timedelta, expected: bool
) -> None:
    from haoai_backend.generation_core.image_single_results import (
        _is_strictly_before_deadline,
    )

    deadline = datetime(2026, 10, 16, 4, 0, tzinfo=timezone.utc)
    assert _is_strictly_before_deadline(deadline + offset, deadline) is expected


def test_http_200_malformed_response_is_unknown_and_never_refunded(
    tmp_path: Path, monkeypatch
) -> None:
    environment, task_id = _admitted_image_task(
        tmp_path, monkeypatch, "image-worker-malformed-200"
    )
    stores: list[StoredMedia] = []
    runtime_config, provider_calls = _runtime_configuration(
        environment, b'{"data": []}', stores
    )
    runtime = build_generation_worker_runtime(
        GenerationWorkerConfiguration(runtime_config, environment.capabilities)
    )

    assert runtime.run_claimed_once("image-single") == "pending"
    assert len(provider_calls) == 1
    assert stores == []
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow).where(AITaskRow.id == task_id)
        ).scalar_one()
        unit = reader.execute(
            select(BillingUnitRow).where(BillingUnitRow.task_id == task_id)
        ).scalar_one()
        assert task.status == "reconciling"
        assert unit.result_status == "unknown"
        assert unit.billing_status == "charged"
        assert unit.refunded_amount == 0
        assert reader.execute(
            select(func.count()).select_from(CreditLogRow).where(
                CreditLogRow.business_key == f"refund:{unit.id}"
            )
        ).scalar_one() == 0
    environment.engine.dispose()


def test_lost_after_credential_resolution_never_enters_http_client(
    tmp_path: Path, monkeypatch
) -> None:
    environment, task_id = _admitted_image_task(
        tmp_path, monkeypatch, "image-worker-lost-after-credential"
    )
    stores: list[StoredMedia] = []
    runtime_config, provider_calls = _runtime_configuration(
        environment, _provider_body(), stores
    )
    entered = Event()
    proceed = Event()
    http_factory_calls: list[bool] = []
    registry = build_credential_registry(environment.capabilities)

    def credential_resolver(binding, purpose):
        credential = registry.resolve_original_credential(binding, purpose)
        entered.set()
        assert proceed.wait(5)
        return credential

    def http_client_factory():
        http_factory_calls.append(True)
        return _HttpClientContext(_HttpClient(_provider_body(), 200, provider_calls))

    runtime_config = replace(
        runtime_config,
        credential_resolver=credential_resolver,
        http_client_factory=http_client_factory,
    )
    runtime = build_generation_worker_runtime(
        GenerationWorkerConfiguration(runtime_config, environment.capabilities)
    )
    outcomes: list[object] = []
    thread = _run_in_thread(runtime, outcomes)
    try:
        assert entered.wait(5)
        _cancel_task(environment, task_id)
    finally:
        proceed.set()
        thread.join(timeout=5)

    assert not thread.is_alive()
    assert outcomes == ["ownership_lost"]
    assert http_factory_calls == []
    assert provider_calls == []
    assert stores == []
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow).where(AITaskRow.id == task_id)
        ).scalar_one()
        assert task.status == "cancelling"
        assert task.result is None
        assert reader.execute(
            select(func.count()).select_from(GenerationResultReceiptRow)
        ).scalar_one() == 0
    environment.engine.dispose()


def test_lost_after_http_context_enter_never_sends_provider_post(
    tmp_path: Path, monkeypatch
) -> None:
    environment, task_id = _admitted_image_task(
        tmp_path, monkeypatch, "image-worker-lost-after-http-enter"
    )
    stores: list[StoredMedia] = []
    entered = Event()
    proceed = Event()
    provider_calls: list[dict[str, object]] = []
    exited: list[bool] = []

    class BlockingHttpContext(AbstractAsyncContextManager):
        async def __aenter__(self):
            entered.set()
            assert proceed.wait(5)
            return _HttpClient(_provider_body(), 200, provider_calls)

        async def __aexit__(self, exc_type, exc, traceback):
            exited.append(True)
            return False

    runtime_config, _ = _runtime_configuration(
        environment,
        _provider_body(),
        stores,
        http_client_factory=BlockingHttpContext,
    )
    runtime = build_generation_worker_runtime(
        GenerationWorkerConfiguration(runtime_config, environment.capabilities)
    )
    outcomes: list[object] = []
    thread = _run_in_thread(runtime, outcomes)
    try:
        assert entered.wait(5)
        _cancel_task(environment, task_id)
    finally:
        proceed.set()
        thread.join(timeout=5)

    assert not thread.is_alive()
    assert outcomes == ["ownership_lost"]
    assert provider_calls == []
    assert stores == []
    assert exited == [True]
    environment.engine.dispose()


def test_status_only_cancel_after_last_task_read_stops_provider_post(
    tmp_path: Path, monkeypatch
) -> None:
    environment, task_id = _admitted_image_task(
        tmp_path, monkeypatch, "image-worker-status-only-cancel-at-post-gate"
    )
    stores: list[StoredMedia] = []
    runtime_config, provider_calls = _runtime_configuration(
        environment, _provider_body(), stores
    )

    class Clock:
        armed = False
        armed_checks = 0
        mutations: list[bool] = []

        def __call__(self):
            if self.armed:
                self.armed_checks += 1
                if self.armed_checks == 2:
                    _cancel_task(environment, task_id)
                    self.mutations.append(True)
            return datetime(2026, 10, 16, 2, 0, 30, tzinfo=timezone.utc)

    clock = Clock()

    class ArmingHttpContext(_HttpClientContext):
        async def __aenter__(self):
            client = await super().__aenter__()
            clock.armed = True
            return client

    runtime_config = replace(
        runtime_config,
        db_clock=clock,
        http_client_factory=lambda: ArmingHttpContext(
            _HttpClient(_provider_body(), 200, provider_calls)
        ),
    )
    runtime = build_generation_worker_runtime(
        GenerationWorkerConfiguration(runtime_config, environment.capabilities)
    )

    outcome = runtime.run_claimed_once("image-single")

    assert clock.mutations == [True]
    assert outcome == "ownership_lost"
    assert provider_calls == []
    assert stores == []
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow).where(AITaskRow.id == task_id)
        ).scalar_one()
        assert task.status == "cancelling"
        assert task.result is None
        assert reader.execute(
            select(func.count()).select_from(GenerationResultReceiptRow)
        ).scalar_one() == 0
    environment.engine.dispose()


def test_stored_output_is_recorded_when_terminal_transaction_rolls_back_once(
    tmp_path: Path, monkeypatch
) -> None:
    environment, task_id = _admitted_image_task(
        tmp_path, monkeypatch, "image-worker-store-terminal-one-shot-failure"
    )
    stores: list[StoredMedia] = []
    runtime_config, provider_calls = _runtime_configuration(
        environment, _provider_body(), stores
    )
    fault_state = {"armed": False, "raised": False}

    def fail_terminal_task_update_once(
        conn, cursor, statement, parameters, context, executemany
    ):
        normalized = " ".join(statement.lower().split())
        if (
            fault_state["armed"]
            and not fault_state["raised"]
            and normalized.startswith("update ai_tasks set status")
        ):
            fault_state["raised"] = True
            raise RuntimeError("one-shot terminal task update fault")

    event.listen(environment.engine, "before_cursor_execute", fail_terminal_task_update_once)
    runtime_config = replace(
        runtime_config,
        media_store_factory=lambda: _Storage(
            stores, on_store=lambda _stored: fault_state.__setitem__("armed", True)
        ),
    )
    runtime = build_generation_worker_runtime(
        GenerationWorkerConfiguration(runtime_config, environment.capabilities)
    )
    try:
        first = runtime.run_claimed_once("image-single")
        second = runtime.run_claimed_once("image-single")
    finally:
        event.remove(
            environment.engine,
            "before_cursor_execute",
            fail_terminal_task_update_once,
        )

    assert fault_state == {"armed": True, "raised": True}
    assert first == "pending"
    assert second == "pending"
    assert len(provider_calls) == 1
    assert len(stores) == 1
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow).where(AITaskRow.id == task_id)
        ).scalar_one()
        unit = reader.execute(
            select(BillingUnitRow).where(BillingUnitRow.task_id == task_id)
        ).scalar_one()
        evidence = reader.execute(
            select(ResultEvidenceRow).where(
                ResultEvidenceRow.evidence_key.like(
                    "image-single-observation-v1:%"
                )
            )
        ).scalar_one_or_none()
        assert task.status in {"queued", "processing", "reconciling"}
        assert task.result is None
        assert unit.result_status == "pending"
        assert unit.refunded_amount == 0
        assert evidence is not None
        assert evidence.validity == "unknown"
        assert evidence.evidence_kind == "provider_image_bytes"
        assert evidence.result_location == stores[0].locator
        assert evidence.payload_digest == stores[0].content_digest
        assert reader.execute(
            select(func.count()).select_from(GenerationResultReceiptRow)
        ).scalar_one() == 0
        assert reader.execute(
            select(func.count()).select_from(CreditLogRow).where(
                CreditLogRow.type == "refund"
            )
        ).scalar_one() == 0
    environment.engine.dispose()


def test_lost_after_provider_response_records_only_safe_locator_observation(
    tmp_path: Path, monkeypatch
) -> None:
    environment, task_id = _admitted_image_task(
        tmp_path, monkeypatch, "image-worker-lost-before-fetch"
    )
    stores: list[StoredMedia] = []
    entered = Event()
    proceed = Event()
    fetch_calls: list[bool] = []
    provider_body = json.dumps(
        {"data": [{"url": "https://cdn.example.test/image.png?sig=secret"}]}
    ).encode("utf-8")

    class BlockingFetcherFactory:
        def __call__(self):
            entered.set()
            assert proceed.wait(5)
            return self

        async def fetch_public(self, reference, *, max_bytes, timeout_seconds):
            fetch_calls.append(True)
            return _UnusedFetcher().fetch_public(
                reference, max_bytes=max_bytes, timeout_seconds=timeout_seconds
            )

    runtime_config, provider_calls = _runtime_configuration(
        environment,
        provider_body,
        stores,
        public_media_fetcher_factory=BlockingFetcherFactory(),
    )
    runtime = build_generation_worker_runtime(
        GenerationWorkerConfiguration(runtime_config, environment.capabilities)
    )
    outcomes: list[object] = []
    thread = _run_in_thread(runtime, outcomes)
    try:
        assert entered.wait(5)
        _cancel_task(environment, task_id)
    finally:
        proceed.set()
        thread.join(timeout=5)

    assert not thread.is_alive()
    assert outcomes == ["ownership_lost"]
    assert len(provider_calls) == 1
    assert fetch_calls == []
    assert stores == []
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow).where(AITaskRow.id == task_id)
        ).scalar_one()
        unit = reader.execute(
            select(BillingUnitRow).where(BillingUnitRow.task_id == task_id)
        ).scalar_one()
        evidence = reader.execute(
            select(ResultEvidenceRow).where(
                ResultEvidenceRow.evidence_key.like("image-single-observation-v1:%")
            )
        ).scalar_one()
        assert task.status == "cancelling"
        assert unit.result_status == "pending"
        assert unit.refunded_amount == 0
        assert evidence.validity == "unknown"
        assert evidence.evidence_kind == "provider_image_locator"
        assert evidence.result_location == "https://cdn.example.test/image.png"
        assert evidence.payload_digest == hashlib.sha256(
            b"https://cdn.example.test/image.png?sig=secret"
        ).hexdigest()
        assert "secret" not in evidence.result_location
    environment.engine.dispose()


def test_lost_after_public_fetch_return_never_starts_media_storage(
    tmp_path: Path, monkeypatch
) -> None:
    environment, task_id = _admitted_image_task(
        tmp_path, monkeypatch, "image-worker-lost-after-fetch"
    )
    stores: list[StoredMedia] = []
    storage_factory_entered = Event()
    proceed = Event()
    fetch_calls: list[bool] = []
    provider_body = json.dumps(
        {"data": [{"url": "https://cdn.example.test/image.png?sig=secret"}]}
    ).encode("utf-8")

    class Fetcher:
        async def fetch_public(self, reference, *, max_bytes, timeout_seconds):
            fetch_calls.append(True)
            return PreparedReference(
                kind="bytes",
                locator=None,
                content=_PNG,
                media_type="image/png",
            )

    def blocking_storage_factory():
        storage_factory_entered.set()
        assert proceed.wait(5)
        return _Storage(stores)

    runtime_config, provider_calls = _runtime_configuration(
        environment,
        provider_body,
        stores,
        public_media_fetcher_factory=Fetcher,
        media_store_factory=blocking_storage_factory,
    )
    runtime = build_generation_worker_runtime(
        GenerationWorkerConfiguration(runtime_config, environment.capabilities)
    )
    outcomes: list[object] = []
    thread = _run_in_thread(runtime, outcomes)
    try:
        assert storage_factory_entered.wait(5)
        _cancel_task(environment, task_id)
    finally:
        proceed.set()
        thread.join(timeout=5)

    assert not thread.is_alive()
    assert outcomes == ["ownership_lost"]
    assert len(provider_calls) == 1
    assert fetch_calls == [True]
    assert stores == []
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow).where(AITaskRow.id == task_id)
        ).scalar_one()
        unit = reader.execute(
            select(BillingUnitRow).where(BillingUnitRow.task_id == task_id)
        ).scalar_one()
        evidence = reader.execute(
            select(ResultEvidenceRow).where(
                ResultEvidenceRow.evidence_key.like("image-single-observation-v1:%")
            )
        ).scalar_one()
        assert task.status == "cancelling"
        assert unit.result_status == "pending"
        assert unit.refunded_amount == 0
        assert evidence.validity == "unknown"
        assert evidence.evidence_kind == "provider_image_bytes"
        assert evidence.payload_digest == hashlib.sha256(_PNG).hexdigest()
        assert evidence.result_location is None
        assert reader.execute(
            select(func.count()).select_from(GenerationResultReceiptRow)
        ).scalar_one() == 0
    environment.engine.dispose()


def test_lost_after_http_response_keeps_provider_bytes_observation(
    tmp_path: Path, monkeypatch
) -> None:
    environment, task_id = _admitted_image_task(
        tmp_path, monkeypatch, "image-worker-lost-after-http-response"
    )
    stores: list[StoredMedia] = []
    http_calls: list[dict[str, object]] = []

    class CancelAfterHttpResponseClient(_HttpClient):
        async def request(self, method, url, *, headers=None, content=None, **kwargs):
            response = await super().request(
                method, url, headers=headers, content=content, **kwargs
            )
            _cancel_task(environment, task_id)
            return response

    runtime_config, _ = _runtime_configuration(
        environment,
        _provider_body(),
        stores,
        http_client_factory=lambda: _HttpClientContext(
            CancelAfterHttpResponseClient(
                _provider_body(), 200, calls=http_calls
            )
        ),
    )
    runtime = build_generation_worker_runtime(
        GenerationWorkerConfiguration(runtime_config, environment.capabilities)
    )
    outcome = runtime.run_claimed_once("image-single")

    assert outcome == "ownership_lost"
    assert len(http_calls) == 1
    assert stores == []
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow).where(AITaskRow.id == task_id)
        ).scalar_one()
        unit = reader.execute(
            select(BillingUnitRow).where(BillingUnitRow.task_id == task_id)
        ).scalar_one()
        evidence = reader.execute(
            select(ResultEvidenceRow).where(
                ResultEvidenceRow.evidence_key.like("image-single-observation-v1:%")
            )
        ).scalar_one_or_none()
        assert task.status == "cancelling"
        assert unit.result_status == "pending"
        assert unit.refunded_amount == 0
        assert evidence is not None
        assert evidence.validity == "unknown"
        assert evidence.evidence_kind == "provider_image_bytes"
        assert evidence.payload_digest == hashlib.sha256(_PNG).hexdigest()
        assert evidence.result_location is None
        assert reader.execute(
            select(func.count()).select_from(GenerationResultReceiptRow)
        ).scalar_one() == 0
        assert reader.execute(
            select(func.count()).select_from(CreditLogRow).where(
                CreditLogRow.type == "refund"
            )
        ).scalar_one() == 0
    environment.engine.dispose()


def test_lost_during_storage_preserves_only_late_unknown_observation(
    tmp_path: Path, monkeypatch
) -> None:
    environment, task_id = _admitted_image_task(
        tmp_path, monkeypatch, "image-worker-lost-during-storage"
    )
    stores: list[StoredMedia] = []
    storage_entered = Event()
    proceed = Event()
    provider_body = _provider_body()

    class BlockingStorage(_Storage):
        def store_bytes_or_provider_content(self, reference, object_policy):
            storage_entered.set()
            assert proceed.wait(5)
            return super().store_bytes_or_provider_content(reference, object_policy)

    runtime_config, provider_calls = _runtime_configuration(
        environment,
        provider_body,
        stores,
        media_store_factory=lambda: BlockingStorage(stores),
    )
    runtime = build_generation_worker_runtime(
        GenerationWorkerConfiguration(runtime_config, environment.capabilities)
    )
    outcomes: list[object] = []
    thread = _run_in_thread(runtime, outcomes)
    try:
        assert storage_entered.wait(5)
        _cancel_task(environment, task_id)
    finally:
        proceed.set()
        thread.join(timeout=5)

    assert not thread.is_alive()
    assert outcomes == ["ownership_lost"]
    assert len(provider_calls) == 1
    assert len(stores) == 1
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow).where(AITaskRow.id == task_id)
        ).scalar_one()
        unit = reader.execute(
            select(BillingUnitRow).where(BillingUnitRow.task_id == task_id)
        ).scalar_one()
        evidence = reader.execute(
            select(ResultEvidenceRow).where(
                ResultEvidenceRow.evidence_key.like("image-single-observation-v1:%")
            )
        ).scalar_one()
        assert task.status == "cancelling"
        assert task.result is None
        assert unit.result_status == "pending"
        assert unit.refunded_amount == 0
        assert evidence.validity == "unknown"
        assert evidence.application_status == "pending"
        assert evidence.payload_digest == stores[0].content_digest
        assert evidence.result_location == "https://media.example.test/managed/image.png"
        assert reader.execute(
            select(func.count()).select_from(GenerationResultReceiptRow)
        ).scalar_one() == 0
        assert reader.execute(
            select(func.count()).select_from(CreditLogRow).where(
                CreditLogRow.business_key.like("refund:%")
            )
        ).scalar_one() == 0
    environment.engine.dispose()


def test_unconfigured_registry_fails_closed_before_claiming_an_admitted_task(
    tmp_path: Path, monkeypatch
) -> None:
    environment, task_id = _admitted_image_task(
        tmp_path, monkeypatch, "image-worker-unconfigured-registry"
    )
    stores: list[StoredMedia] = []
    runtime_config, provider_calls = _runtime_configuration(
        environment, _provider_body(), stores
    )
    worker_session_calls = 0

    def counted_session_factory():
        nonlocal worker_session_calls
        worker_session_calls += 1
        return Session(environment.engine)

    runtime_config = runtime_config.__class__(
        **{
            field: (
                counted_session_factory
                if field == "session_factory"
                else getattr(runtime_config, field)
            )
            for field in runtime_config.__dataclass_fields__
        }
    )
    runtime = build_generation_worker_runtime(
        GenerationWorkerConfiguration(runtime_config, RegistryCapabilities.unconfigured())
    )

    assert runtime.run_claimed_once("image-single") == "pending"
    assert worker_session_calls == 0
    assert provider_calls == []
    assert stores == []
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow).where(AITaskRow.id == task_id)
        ).scalar_one()
        assert task.status == "queued"
        assert task.claim_token is None
        assert task.execution_generation == 0
    environment.engine.dispose()


@pytest.mark.parametrize("response_status", (400, 429))
def test_known_provider_rejection_refunds_only_the_exact_original_debit(
    tmp_path: Path, monkeypatch, response_status: int
) -> None:
    environment, task_id = _admitted_image_task(
        tmp_path, monkeypatch, f"image-worker-http-{response_status}-refund"
    )
    stores: list[StoredMedia] = []
    runtime_config, provider_calls = _runtime_configuration(
        environment, _provider_body(), stores, response_status=response_status
    )
    runtime = build_generation_worker_runtime(
        GenerationWorkerConfiguration(runtime_config, environment.capabilities)
    )

    outcome = runtime.run_claimed_once("image-single")

    assert outcome is None
    assert len(provider_calls) == 1
    assert stores == []
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow).where(AITaskRow.id == task_id)
        ).scalar_one()
        unit = reader.execute(
            select(BillingUnitRow).where(BillingUnitRow.task_id == task_id)
        ).scalar_one()
        debit = reader.execute(
            select(CreditLogRow).where(
                CreditLogRow.business_key == f"debit:{unit.id}"
            )
        ).scalar_one()
        refund = reader.execute(
            select(CreditLogRow).where(
                CreditLogRow.business_key == f"refund:{unit.id}"
            )
        ).scalar_one()
        wallet = reader.execute(
            select(UserCreditRow).where(UserCreditRow.user_id == task.user_id)
        ).scalar_one()
        assert task.status == "failed"
        assert task.billing_status == "refunded"
        assert unit.result_status == "failed"
        assert unit.billing_status == "refunded"
        assert unit.refunded_amount == unit.debited_amount == 3
        assert debit.user_id == task.user_id
        assert debit.task_id == task.id
        assert debit.type == "usage"
        assert debit.amount == -3
        assert refund.user_id == task.user_id
        assert refund.task_id == task.id
        assert refund.billing_unit_id == unit.id
        assert refund.related_debit_id == debit.id
        assert refund.type == "refund"
        assert refund.amount == 3
        assert wallet.credits == 20
        assert reader.execute(
            select(func.count()).select_from(CreditLogRow).where(
                CreditLogRow.business_key == f"refund:{unit.id}"
            )
        ).scalar_one() == 1
    environment.engine.dispose()


def test_http_400_commit_ack_exception_confirms_once_without_repost_or_double_refund(
    tmp_path: Path, monkeypatch
) -> None:
    environment, task_id = _admitted_image_task(
        tmp_path, monkeypatch, "image-worker-refund-commit-ack"
    )
    stores: list[StoredMedia] = []
    runtime_config, provider_calls = _runtime_configuration(
        environment, _provider_body(), stores, response_status=400
    )
    runtime = build_generation_worker_runtime(
        GenerationWorkerConfiguration(runtime_config, environment.capabilities)
    )
    injected: list[bool] = []

    def lose_ack_after_failure_commit(session) -> None:
        if injected:
            return
        with Session(environment.engine) as reader:
            task = reader.execute(
                select(AITaskRow.status, AITaskRow.billing_status).where(
                    AITaskRow.id == task_id
                )
            ).one()
        if task == ("failed", "refunded"):
            injected.append(True)
            raise RuntimeError("injected result commit acknowledgement loss")

    event.listen(Session, "after_commit", lose_ack_after_failure_commit)
    try:
        first = runtime.run_claimed_once("image-single")
        second = runtime.run_claimed_once("image-single")
    finally:
        event.remove(Session, "after_commit", lose_ack_after_failure_commit)

    assert injected == [True]
    assert first is None
    assert second == "pending"
    assert len(provider_calls) == 1
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow).where(AITaskRow.id == task_id)
        ).scalar_one()
        unit = reader.execute(
            select(BillingUnitRow).where(BillingUnitRow.task_id == task_id)
        ).scalar_one()
        assert task.status == "failed"
        assert unit.billing_status == "refunded"
        assert unit.refunded_amount == 3
        assert reader.execute(
            select(func.count()).select_from(CreditLogRow).where(
                CreditLogRow.business_key == f"refund:{unit.id}"
            )
        ).scalar_one() == 1
        wallet = reader.execute(
            select(UserCreditRow.credits).where(UserCreditRow.user_id == task.user_id)
        ).scalar_one()
        assert wallet == 20
    environment.engine.dispose()


def test_zero_cost_byok_failure_does_not_create_a_refund(
    tmp_path: Path, monkeypatch
) -> None:
    environment, task_id = _admitted_image_task(
        tmp_path,
        monkeypatch,
        "image-worker-byok-400",
        model_source=admission_staging_module.ModelSource.USER,
        wallet_credits=None,
    )
    stores: list[StoredMedia] = []
    runtime_config, provider_calls = _runtime_configuration(
        environment, _provider_body(), stores, response_status=400
    )
    runtime = build_generation_worker_runtime(
        GenerationWorkerConfiguration(runtime_config, environment.capabilities)
    )

    assert runtime.run_claimed_once("image-single") is None
    assert len(provider_calls) == 1
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow).where(AITaskRow.id == task_id)
        ).scalar_one()
        unit = reader.execute(
            select(BillingUnitRow).where(BillingUnitRow.task_id == task_id)
        ).scalar_one()
        assert task.status == "failed"
        assert task.billing_status == "no_charge"
        assert unit.debited_amount == unit.refunded_amount == 0
        assert unit.billing_status == "no_charge"
        assert reader.execute(
            select(func.count()).select_from(CreditLogRow).where(
                CreditLogRow.business_key == f"refund:{unit.id}"
            )
        ).scalar_one() == 0
        assert reader.execute(select(func.count()).select_from(UserCreditRow)).scalar_one() == 0
    environment.engine.dispose()


def test_missing_original_debit_keeps_http_400_failure_pending(
    tmp_path: Path, monkeypatch
) -> None:
    environment, task_id = _admitted_image_task(
        tmp_path, monkeypatch, "image-worker-missing-debit"
    )
    with Session(environment.engine) as mutator:
        unit = mutator.execute(
            select(BillingUnitRow).where(BillingUnitRow.task_id == task_id)
        ).scalar_one()
        mutator.execute(
            delete(CreditLogRow.__table__).where(
                CreditLogRow.business_key == f"debit:{unit.id}"
            )
        )
        mutator.commit()

    stores: list[StoredMedia] = []
    runtime_config, provider_calls = _runtime_configuration(
        environment, _provider_body(), stores, response_status=400
    )
    runtime = build_generation_worker_runtime(
        GenerationWorkerConfiguration(runtime_config, environment.capabilities)
    )

    assert runtime.run_claimed_once("image-single") == "pending"
    assert len(provider_calls) == 1
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow).where(AITaskRow.id == task_id)
        ).scalar_one()
        unit = reader.execute(
            select(BillingUnitRow).where(BillingUnitRow.task_id == task_id)
        ).scalar_one()
        assert task.status == "queued"
        assert task.claim_token is None
        assert task.result is None
        assert unit.result_status == "pending"
        assert unit.billing_status == "charged"
        assert unit.refunded_amount == 0
        assert reader.execute(
            select(func.count()).select_from(CreditLogRow).where(
                CreditLogRow.business_key == f"refund:{unit.id}"
            )
        ).scalar_one() == 0
    environment.engine.dispose()


@pytest.mark.parametrize("corruption", ("wrong_owner", "wrong_unit", "wrong_amount"))
def test_http_400_never_refunds_a_nonmatching_original_debit(
    tmp_path: Path, monkeypatch, corruption: str
) -> None:
    environment, task_id = _admitted_image_task(
        tmp_path, monkeypatch, f"image-worker-invalid-debit-{corruption}"
    )
    with Session(environment.engine) as mutator:
        debit = mutator.execute(
            select(CreditLogRow.__table__).where(
                CreditLogRow.task_id == task_id,
                CreditLogRow.type == "usage",
            )
        ).mappings().one()
        unit = mutator.execute(
            select(BillingUnitRow).where(BillingUnitRow.task_id == task_id)
        ).scalar_one()
        mutator.execute(
            delete(CreditLogRow.__table__).where(CreditLogRow.id == debit["id"])
        )
        if corruption == "wrong_owner":
            mutator.execute(
                insert(authentication_users).values(
                    id="other-valid-owner",
                    username="other-valid-owner",
                    email="other-valid-owner@example.test",
                    hashed_password="fixture-only",
                    created_at=datetime(2026, 10, 16),
                )
            )
        replacement = dict(debit)
        replacement["id"] = f"{debit['id']}-corrupt"
        if corruption == "wrong_owner":
            replacement["user_id"] = "other-valid-owner"
        elif corruption == "wrong_unit":
            replacement["billing_unit_id"] = None
        else:
            replacement["amount"] = -2
        mutator.execute(insert(CreditLogRow.__table__).values(**replacement))
        mutator.commit()

    stores: list[StoredMedia] = []
    runtime_config, provider_calls = _runtime_configuration(
        environment, _provider_body(), stores, response_status=400
    )
    runtime = build_generation_worker_runtime(
        GenerationWorkerConfiguration(runtime_config, environment.capabilities)
    )
    assert runtime.run_claimed_once("image-single") == "pending"
    assert len(provider_calls) == 1
    assert stores == []
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow).where(AITaskRow.id == task_id)
        ).scalar_one()
        unit = reader.execute(
            select(BillingUnitRow).where(BillingUnitRow.task_id == task_id)
        ).scalar_one()
        wallet = reader.execute(
            select(UserCreditRow).where(UserCreditRow.user_id == task.user_id)
        ).scalar_one()
        assert task.status == "queued"
        assert task.result is None
        assert unit.result_status == "pending"
        assert unit.billing_status == "charged"
        assert unit.refunded_amount == 0
        assert wallet.credits == 17
        assert reader.execute(
            select(func.count()).select_from(CreditLogRow).where(
                CreditLogRow.business_key == f"refund:{unit.id}"
            )
        ).scalar_one() == 0
    environment.engine.dispose()


def test_no_charge_projection_with_an_actual_debit_fails_closed(
    tmp_path: Path, monkeypatch
) -> None:
    environment, claim = _claimed_image_task(
        tmp_path, monkeypatch, "image-worker-nocharge-with-ledger"
    )
    prepared = _intents(environment).prepare(claim)
    assert prepared.disposition == "committed"
    assert prepared.intent is not None and prepared.verified_input is not None
    with Session(environment.engine) as mutator:
        mutator.execute(
            update(BillingUnitRow.__table__)
            .where(BillingUnitRow.id == prepared.verified_input.unit_id)
            .values(debited_amount=0, billing_status="no_charge")
        )
        mutator.execute(
            update(AITaskRow.__table__)
            .where(AITaskRow.id == claim.scope.task_id)
            .values(billing_status="no_charge")
        )
        mutator.commit()
    writer = SqlAlchemyImageSingleResults(
        lambda: Session(environment.engine),
        lambda: datetime(2026, 10, 16, 2, 0, 30, tzinfo=timezone.utc),
        lambda: "nocharge-with-ledger-id",
    )

    outcome = writer.fail(
        claim,
        prepared.intent,
        prepared.verified_input,
        code="provider_rejected",
        safe_detail="图片生成失败 (400)",
    )

    assert outcome == "pending"
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow).where(AITaskRow.id == claim.scope.task_id)
        ).scalar_one()
        unit = reader.execute(
            select(BillingUnitRow).where(BillingUnitRow.id == prepared.verified_input.unit_id)
        ).scalar_one()
        assert task.status == "processing"
        assert unit.result_status == "pending"
        assert unit.debited_amount == 0
        assert reader.execute(
            select(func.count()).select_from(CreditLogRow).where(
                CreditLogRow.task_id == claim.scope.task_id,
                CreditLogRow.type == "usage",
            )
        ).scalar_one() == 1
        assert reader.execute(
            select(func.count()).select_from(CreditLogRow).where(
                CreditLogRow.business_key == f"refund:{unit.id}"
            )
        ).scalar_one() == 0
    environment.engine.dispose()


def test_alternate_refund_linked_to_original_debit_cannot_be_paid_twice(
    tmp_path: Path, monkeypatch
) -> None:
    environment, claim = _claimed_image_task(
        tmp_path, monkeypatch, "image-worker-alternate-related-refund"
    )
    prepared = _intents(environment).prepare(claim)
    assert prepared.disposition == "committed"
    assert prepared.intent is not None and prepared.verified_input is not None
    with Session(environment.engine) as mutator:
        debit = mutator.execute(
            select(CreditLogRow.__table__).where(
                CreditLogRow.task_id == claim.scope.task_id,
                CreditLogRow.type == "usage",
            )
        ).mappings().one()
        wallet = mutator.execute(
            select(UserCreditRow.__table__).where(
                UserCreditRow.user_id == claim.scope.user_id
            )
        ).mappings().one()
        balance_after = int(wallet["credits"]) + 1
        mutator.execute(
            update(UserCreditRow.__table__)
            .where(UserCreditRow.user_id == claim.scope.user_id)
            .values(credits=balance_after)
        )
        mutator.execute(
            insert(CreditLogRow.__table__).values(
                id="preexisting-alternate-refund",
                user_id=claim.scope.user_id,
                amount=1,
                balance_after=balance_after,
                type="refund",
                description="fixture partial refund",
                task_id=claim.scope.task_id,
                business_key="alternate-refund-business-key",
                billing_unit_id=prepared.verified_input.unit_id,
                related_debit_id=debit["id"],
                created_at=datetime(2026, 10, 16),
            )
        )
        mutator.commit()
    writer = SqlAlchemyImageSingleResults(
        lambda: Session(environment.engine),
        lambda: datetime(2026, 10, 16, 2, 0, 30, tzinfo=timezone.utc),
        lambda: "alternate-refund-id",
    )

    outcome = writer.fail(
        claim,
        prepared.intent,
        prepared.verified_input,
        code="provider_rejected",
        safe_detail="图片生成失败 (400)",
    )

    assert outcome == "pending"
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow).where(AITaskRow.id == claim.scope.task_id)
        ).scalar_one()
        unit = reader.execute(
            select(BillingUnitRow).where(BillingUnitRow.id == prepared.verified_input.unit_id)
        ).scalar_one()
        wallet_balance = reader.execute(
            select(UserCreditRow.credits).where(
                UserCreditRow.user_id == claim.scope.user_id
            )
        ).scalar_one()
        assert task.status == "processing"
        assert unit.result_status == "pending"
        assert unit.refunded_amount == 0
        assert wallet_balance == balance_after
        assert reader.execute(
            select(func.count()).select_from(CreditLogRow).where(
                CreditLogRow.related_debit_id == debit["id"],
                CreditLogRow.type == "refund",
            )
        ).scalar_one() == 1
        assert reader.execute(
            select(func.count()).select_from(CreditLogRow).where(
                CreditLogRow.business_key == f"refund:{unit.id}"
            )
        ).scalar_one() == 0
    environment.engine.dispose()


@pytest.mark.parametrize("loss_kind", ("local_lost", "deadline"))
def test_refund_rechecks_authority_after_waiting_at_wallet_read(
    tmp_path: Path, monkeypatch, loss_kind: str
) -> None:
    environment, claim = _claimed_image_task(
        tmp_path, monkeypatch, f"image-worker-refund-recheck-{loss_kind}"
    )
    prepared = _intents(environment).prepare(claim)
    assert prepared.disposition == "committed"
    assert prepared.intent is not None and prepared.verified_input is not None
    now_value = [datetime(2026, 10, 16, 2, 0, 30, tzinfo=timezone.utc)]
    wallet_read_entered = Event()
    continue_writer = Event()
    writer_outcomes: list[object] = []
    statement_trace: list[str] = []

    def pause_at_wallet_read(conn, cursor, statement, parameters, context, executemany):
        lowered = statement.lower()
        if "from user_credits" in lowered:
            statement_trace.append("wallet")
            if not wallet_read_entered.is_set():
                wallet_read_entered.set()
                assert continue_writer.wait(5)
        elif "from credit_logs" in lowered:
            statement_trace.append("ledger")

    writer = SqlAlchemyImageSingleResults(
        lambda: Session(environment.engine),
        lambda: now_value[0],
        lambda: f"wallet-recheck-id-{loss_kind}",
    )
    event.listen(environment.engine, "before_cursor_execute", pause_at_wallet_read)
    thread = Thread(
        target=lambda: writer_outcomes.append(
            writer.fail(
                claim,
                prepared.intent,
                prepared.verified_input,
                code="provider_rejected",
                safe_detail="图片生成失败 (400)",
            )
        ),
        daemon=True,
    )
    thread.start()
    try:
        assert wallet_read_entered.wait(5)
        if loss_kind == "local_lost":
            claim.local_lost_signal.set()
        else:
            now_value[0] = prepared.intent.deadline_at + timedelta(microseconds=1)
    finally:
        continue_writer.set()
        thread.join(timeout=5)
        event.remove(environment.engine, "before_cursor_execute", pause_at_wallet_read)

    assert not thread.is_alive()
    assert writer_outcomes == ["ownership_lost"]
    assert statement_trace.index("wallet") < statement_trace.index("ledger")
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow).where(AITaskRow.id == claim.scope.task_id)
        ).scalar_one()
        unit = reader.execute(
            select(BillingUnitRow).where(BillingUnitRow.id == prepared.verified_input.unit_id)
        ).scalar_one()
        wallet_balance = reader.execute(
            select(UserCreditRow.credits).where(
                UserCreditRow.user_id == claim.scope.user_id
            )
        ).scalar_one()
        assert task.status == "processing"
        assert task.result is None
        assert unit.result_status == "pending"
        assert unit.refunded_amount == 0
        assert wallet_balance == 17
        assert reader.execute(
            select(func.count()).select_from(CreditLogRow).where(
                CreditLogRow.business_key == f"refund:{unit.id}"
            )
        ).scalar_one() == 0
    environment.engine.dispose()


@pytest.mark.parametrize(
    "mutation",
    ("step_snapshot", "dependencies", "submission_snapshot", "extra_step", "extra_unit"),
)
def test_terminal_writer_rechecks_the_full_graph_after_task_dml(
    tmp_path: Path, monkeypatch, mutation: str
) -> None:
    environment, claim = _claimed_image_task(
        tmp_path, monkeypatch, f"image-worker-graph-mutation-{mutation}"
    )
    prepared = _intents(environment).prepare(claim)
    assert prepared.disposition == "committed"
    assert prepared.intent is not None and prepared.verified_input is not None
    with Session(environment.engine) as reader:
        step = reader.execute(
            select(ExecutionStepRow).where(
                ExecutionStepRow.id == prepared.intent.step_id
            )
        ).scalar_one()
        submission = reader.execute(
            select(ExternalSubmissionRow).where(
                ExternalSubmissionRow.id == prepared.intent.external_submission_id
            )
        ).scalar_one()
        unit = reader.execute(
            select(BillingUnitRow.__table__).where(
                BillingUnitRow.id == step.billing_unit_id
            )
        ).mappings().one()
        unit_id = step.billing_unit_id

    clock_calls = 0
    fixed_now = datetime(2026, 10, 16, 2, 0, 30, tzinfo=timezone.utc)

    def mutate_between_graph_read_and_first_dml() -> datetime:
        nonlocal clock_calls
        clock_calls += 1
        if clock_calls == 2:
            with Session(environment.engine) as mutator:
                if mutation == "step_snapshot":
                    snapshot = dict(step.input_snapshot)
                    snapshot["protected_ref"] = "changed-after-graph-read"
                    mutator.execute(
                        update(ExecutionStepRow.__table__)
                        .where(ExecutionStepRow.id == step.id)
                        .values(input_snapshot=snapshot)
                    )
                elif mutation == "dependencies":
                    mutator.execute(
                        update(ExecutionStepRow.__table__)
                        .where(ExecutionStepRow.id == step.id)
                        .values(dependencies=["unexpected-dependency"])
                    )
                elif mutation == "submission_snapshot":
                    snapshot = dict(submission.request_snapshot)
                    snapshot["attempt_id"] = "changed-after-graph-read"
                    mutator.execute(
                        update(ExternalSubmissionRow.__table__)
                        .where(ExternalSubmissionRow.id == submission.id)
                        .values(request_snapshot=snapshot)
                    )
                elif mutation == "extra_step":
                    mutator.execute(
                        insert(ExecutionStepRow.__table__).values(
                            id="unexpected-image-step",
                            billing_unit_id=unit_id,
                            step_key="unexpected",
                            dependencies=[],
                            input_digest=prepared.intent.request_digest,
                            input_snapshot={},
                            required=True,
                            step_order=1,
                            execution_status="prepared",
                        )
                    )
                else:
                    extra_unit = dict(unit)
                    extra_unit["id"] = "unexpected-image-unit"
                    extra_unit["item_key"] = "unexpected-item"
                    mutator.execute(insert(BillingUnitRow.__table__).values(**extra_unit))
                mutator.commit()
        return fixed_now

    writer = SqlAlchemyImageSingleResults(
        lambda: Session(environment.engine),
        mutate_between_graph_read_and_first_dml,
        lambda: f"graph-mutation-id-{mutation}",
    )
    stored = StoredMedia(
        locator="https://media.example.test/managed/image.png",
        content_digest=hashlib.sha256(_PNG).hexdigest(),
        media_type="image/png",
        size_bytes=len(_PNG),
    )

    outcome = writer.complete(
        claim, prepared.intent, prepared.verified_input, stored
    )

    assert outcome == "pending"
    assert clock_calls == 2
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow).where(AITaskRow.id == claim.scope.task_id)
        ).scalar_one()
        unit = reader.execute(
            select(BillingUnitRow).where(BillingUnitRow.id == unit_id)
        ).scalar_one()
        assert task.status == "processing"
        assert task.result is None
        assert unit.result_status == "pending"
        assert unit.refunded_amount == 0
        assert reader.execute(
            select(func.count()).select_from(GenerationResultReceiptRow)
        ).scalar_one() == 0
        assert reader.execute(
            select(func.count()).select_from(ResultEvidenceRow)
        ).scalar_one() == 0
        assert reader.execute(
            select(func.count()).select_from(CreditLogRow).where(
                CreditLogRow.business_key == f"refund:{unit_id}"
            )
        ).scalar_one() == 0
        if mutation == "extra_step":
            assert reader.execute(
                select(func.count()).select_from(ExecutionStepRow).where(
                    ExecutionStepRow.billing_unit_id == unit_id
                )
            ).scalar_one() == 2
        if mutation == "extra_unit":
            assert reader.execute(
                select(func.count()).select_from(BillingUnitRow).where(
                    BillingUnitRow.task_id == claim.scope.task_id
                )
            ).scalar_one() == 2
    environment.engine.dispose()


@pytest.mark.parametrize(
    ("offset_microseconds", "accepted"),
    ((-1, True), (0, False), (1, False)),
)
def test_terminal_writer_uses_strict_deadline_boundary(
    tmp_path: Path, monkeypatch, offset_microseconds: int, accepted: bool
) -> None:
    environment, claim = _claimed_image_task(
        tmp_path, monkeypatch, f"image-worker-deadline-{offset_microseconds}"
    )
    prepared = _intents(environment).prepare(claim)
    assert prepared.disposition == "committed"
    assert prepared.intent is not None and prepared.verified_input is not None
    deadline = prepared.intent.deadline_at
    first_now = deadline - timedelta(microseconds=2)
    terminal_now = deadline + timedelta(microseconds=offset_microseconds)
    clock_values = iter((first_now, terminal_now))
    with Session(environment.engine) as mutator:
        mutator.execute(
            update(AITaskRow.__table__)
            .where(AITaskRow.id == claim.scope.task_id)
            .values(lease_until=(deadline + timedelta(seconds=1)).replace(tzinfo=None))
        )
        mutator.commit()
    writer = SqlAlchemyImageSingleResults(
        lambda: Session(environment.engine),
        lambda: next(clock_values),
        lambda: f"deadline-result-{offset_microseconds}",
    )
    stored = StoredMedia(
        locator="https://media.example.test/deadline-image.png",
        content_digest=hashlib.sha256(_PNG).hexdigest(),
        media_type="image/png",
        size_bytes=len(_PNG),
    )

    outcome = writer.complete(claim, prepared.intent, prepared.verified_input, stored)

    if accepted:
        assert outcome is not None and outcome != "pending" and outcome != "ownership_lost"
    else:
        assert outcome == "ownership_lost"
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow).where(AITaskRow.id == claim.scope.task_id)
        ).scalar_one()
        receipts = reader.execute(
            select(func.count()).select_from(GenerationResultReceiptRow)
        ).scalar_one()
        assert (task.status == "completed") is accepted
        assert receipts == int(accepted)
    environment.engine.dispose()


def test_success_commit_ack_rejects_a_post_commit_static_graph_change_without_repost(
    tmp_path: Path, monkeypatch
) -> None:
    environment, task_id = _admitted_image_task(
        tmp_path, monkeypatch, "image-worker-success-ack-graph-change"
    )
    stores: list[StoredMedia] = []
    runtime_config, provider_calls = _runtime_configuration(
        environment, _provider_body(), stores
    )
    runtime = build_generation_worker_runtime(
        GenerationWorkerConfiguration(runtime_config, environment.capabilities)
    )
    mutated: list[bool] = []

    def mutate_graph_after_result_commit(session) -> None:
        if mutated:
            return
        with Session(environment.engine) as reader:
            task = reader.execute(
                select(AITaskRow).where(AITaskRow.id == task_id)
            ).scalar_one()
            if task.status != "completed" or task.result is None:
                return
            step = reader.execute(
                select(ExecutionStepRow).where(
                    ExecutionStepRow.billing_unit_id.in_(
                        select(BillingUnitRow.id).where(
                            BillingUnitRow.task_id == task_id
                        )
                    )
                )
            ).scalar_one()
            changed_snapshot = dict(step.input_snapshot)
            changed_snapshot["protected_ref"] = "post-commit-mutated-binding"
        mutated.append(True)
        with Session(environment.engine) as mutator:
            mutator.execute(
                update(ExecutionStepRow.__table__)
                .where(ExecutionStepRow.id == step.id)
                .values(input_snapshot=changed_snapshot)
            )
            mutator.commit()

    event.listen(Session, "after_commit", mutate_graph_after_result_commit)
    try:
        first = runtime.run_claimed_once("image-single")
        second = runtime.run_claimed_once("image-single")
    finally:
        event.remove(Session, "after_commit", mutate_graph_after_result_commit)

    assert mutated == [True]
    assert first == "pending"
    assert second == "pending"
    assert len(provider_calls) == 1
    assert len(stores) == 1
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow).where(AITaskRow.id == task_id)
        ).scalar_one()
        step = reader.execute(
            select(ExecutionStepRow).where(
                ExecutionStepRow.billing_unit_id.in_(
                    select(BillingUnitRow.id).where(BillingUnitRow.task_id == task_id)
                )
            )
        ).scalar_one()
        assert task.status == "completed"
        assert task.result is not None
        assert step.input_snapshot["protected_ref"] == "post-commit-mutated-binding"
        assert reader.execute(
            select(func.count()).select_from(ResultEvidenceRow).where(
                ResultEvidenceRow.evidence_key.like(
                    "image-single-observation-v1:%"
                )
            )
        ).scalar_one() == 0
        assert reader.execute(
            select(func.count()).select_from(ResultEvidenceRow).where(
                ResultEvidenceRow.evidence_key.like("image-single-result-v1:%"),
                ResultEvidenceRow.validity == "valid",
            )
        ).scalar_one() == 1
        assert reader.execute(
            select(func.count()).select_from(GenerationResultReceiptRow)
        ).scalar_one() == 1
    environment.engine.dispose()


def test_late_provider_observation_is_unknown_idempotent_and_financially_inert(
    tmp_path: Path, monkeypatch
) -> None:
    environment, claim = _claimed_image_task(
        tmp_path, monkeypatch, "image-worker-late-observation"
    )
    prepared = _intents(environment).prepare(claim)
    assert prepared.disposition == "committed"
    assert prepared.intent is not None and prepared.verified_input is not None
    with Session(environment.engine) as mutator:
        mutator.execute(
            update(AITaskRow.__table__)
            .where(AITaskRow.id == claim.scope.task_id)
            .values(
                status="cancelling",
                user_cancelled_at=datetime(2026, 10, 16, 2, 0, 31),
                cancellation_reason="user_request",
            )
        )
        mutator.commit()

    writer = SqlAlchemyImageSingleResults(
        lambda: Session(environment.engine),
        lambda: datetime(2026, 10, 16, 2, 0, 32, tzinfo=timezone.utc),
        lambda: "late-observation-evidence-id",
    )
    digest = hashlib.sha256(_PNG).hexdigest()

    first = writer.record_unknown_observation(
        claim,
        prepared.intent,
        prepared.verified_input,
        payload_digest=digest,
        result_location=None,
        evidence_kind="provider_image_bytes",
    )
    replay = writer.record_unknown_observation(
        claim,
        prepared.intent,
        prepared.verified_input,
        payload_digest=digest,
        result_location=None,
        evidence_kind="provider_image_bytes",
    )
    conflict = writer.record_unknown_observation(
        claim,
        prepared.intent,
        prepared.verified_input,
        payload_digest=hashlib.sha256(b"different image").hexdigest(),
        result_location=None,
        evidence_kind="provider_image_bytes",
    )

    assert first == replay == "recorded"
    assert conflict == "pending"
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow).where(AITaskRow.id == claim.scope.task_id)
        ).scalar_one()
        unit = reader.execute(
            select(BillingUnitRow).where(BillingUnitRow.task_id == claim.scope.task_id)
        ).scalar_one()
        evidence = reader.execute(
            select(ResultEvidenceRow).where(
                ResultEvidenceRow.evidence_key
                == f"image-single-observation-v1:{prepared.intent.external_submission_id}"
            )
        ).scalar_one()
        assert task.status == "cancelling"
        assert task.user_cancelled_at is not None
        assert unit.result_status == "pending"
        assert unit.refunded_amount == 0
        assert evidence.validity == "unknown"
        assert evidence.application_status == "pending"
        assert evidence.payload_digest == digest
        assert evidence.result_digest == digest
        assert evidence.result_location is None
        assert reader.execute(
            select(func.count()).select_from(GenerationResultReceiptRow)
        ).scalar_one() == 0
        assert reader.execute(
            select(func.count()).select_from(ResultEvidenceRow)
        ).scalar_one() == 1
    environment.engine.dispose()


def test_result_writer_refuses_a_cancelled_claim_without_financial_or_result_writes(
    tmp_path: Path, monkeypatch
) -> None:
    environment, claim = _claimed_image_task(
        tmp_path, monkeypatch, "image-worker-cancel-fence"
    )
    prepared = _intents(environment).prepare(claim)
    assert prepared.disposition == "committed"
    assert prepared.intent is not None and prepared.verified_input is not None
    with Session(environment.engine) as mutator:
        mutator.execute(
            update(AITaskRow.__table__)
            .where(AITaskRow.id == claim.scope.task_id)
            .values(
                status="cancelling",
                user_cancelled_at=datetime(2026, 10, 16, 2, 0, 31),
                cancellation_reason="user_request",
            )
        )
        mutator.commit()

    ids = iter(("cancel-result-ref", "cancel-evidence-id"))
    writer = SqlAlchemyImageSingleResults(
        lambda: Session(environment.engine),
        lambda: datetime(2026, 10, 16, 2, 0, 32, tzinfo=timezone.utc),
        lambda: next(ids),
    )
    stored = StoredMedia(
        locator="https://media.example.test/managed/image.png",
        content_digest=hashlib.sha256(_PNG).hexdigest(),
        media_type="image/png",
        size_bytes=len(_PNG),
    )

    outcome = writer.complete(claim, prepared.intent, prepared.verified_input, stored)

    assert outcome == "ownership_lost"
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == claim.scope.task_id)
        ).mappings().one()
        unit = reader.execute(
            select(BillingUnitRow.__table__).where(
                BillingUnitRow.__table__.c.task_id == claim.scope.task_id
            )
        ).mappings().one()
        assert task["status"] == "cancelling"
        assert unit["result_status"] == "pending"
        assert unit["refunded_amount"] == 0
        assert reader.execute(select(func.count()).select_from(GenerationResultReceiptRow)).scalar_one() == 0
        assert reader.execute(select(func.count()).select_from(ResultEvidenceRow)).scalar_one() == 0
    environment.engine.dispose()
