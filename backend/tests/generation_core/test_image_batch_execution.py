from __future__ import annotations

import base64
import hashlib
import json
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select, func
from sqlalchemy.orm import Session

import test_admission_staging as joint_fixtures
from haoai_backend.generation_catalog.domain import ModelSource
from haoai_backend.generation_contracts.ports import GenerationRuntimeConfig
from haoai_backend.generation_contracts.types import PreparedReference, StoredMedia
from haoai_backend.generation_core.admission_receipts import AdmissionStaged
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
    ResultEvidenceRow,
    UserCreditRow,
)
from haoai_backend.generation_core.schema import GenerationResultReceiptRow
from haoai_backend.generation_credentials.application import build_credential_registry
from haoai_backend.generation_runtime.image_batch_assembly import (
    ImageBatchWorkerConfiguration,
    build_image_batch_worker_runtime,
)
from test_batch_execution_snapshot_reader import _authenticated_batch_environment

_OUTPUT_IMAGE = b"\x89PNG\r\n\x1a\nworker-output"
_REFERENCE_A = b"\x89PNG\r\n\x1a\nreference-a"
_REFERENCE_B = b"\x89PNG\r\n\x1a\nreference-b"
_REFERENCE_C = b"\x89PNG\r\n\x1a\nreference-c"


class _Response:
    headers = {"content-length": "0"}

    def __init__(self, body: bytes, status_code: int) -> None:
        self.body = body
        self.status_code = status_code

    async def iter_bytes(self, *, chunk_size: int):
        assert chunk_size > 0
        yield self.body


class _HttpClient:
    def __init__(self, calls: list[dict[str, object]], *, timeout: bool) -> None:
        self.calls = calls
        self.timeout = timeout

    async def request(self, method, url, *, headers=None, content=None, **kwargs):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers or {},
                "content": content,
                "follow_redirects": kwargs.get("follow_redirects"),
            }
        )
        if self.timeout:
            raise TimeoutError("synthetic timeout after request entry")
        body = json.dumps(
            {
                "data": [
                    {
                        "b64_json": base64.b64encode(_OUTPUT_IMAGE).decode("ascii")
                    }
                ]
            }
        ).encode("utf-8")
        return _Response(body, 200)


class _HttpContext(AbstractAsyncContextManager):
    def __init__(self, client: _HttpClient) -> None:
        self.client = client

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _PublicFetcher:
    def __init__(self, calls: list[tuple[str, int]]) -> None:
        self.calls = calls

    async def fetch_public(self, reference, *, max_bytes, timeout_seconds):
        assert reference.kind in {"public_http", "managed_url"}
        self.calls.append((reference.locator, max_bytes))
        if "first" in reference.locator:
            content = _REFERENCE_A
        elif "shared" in reference.locator:
            content = _REFERENCE_B
        else:
            content = _REFERENCE_C
        return PreparedReference(
            kind="bytes",
            locator=None,
            content=content,
            media_type="image/png",
        )


class _MediaStore:
    def __init__(self, stores: list[StoredMedia]) -> None:
        self.stores = stores

    def store_bytes_or_provider_content(self, reference, object_policy):
        assert reference.kind == "bytes"
        assert object_policy == {"max_bytes": 10 * 1024 * 1024}
        assert type(reference.content) is bytes
        stored = StoredMedia(
            locator="https://media.example.test/batch/managed-image.png",
            content_digest=hashlib.sha256(reference.content).hexdigest(),
            media_type=reference.media_type or "",
            size_bytes=len(reference.content),
        )
        self.stores.append(stored)
        return stored


def _runtime_configuration(environment, *, timeout: bool):
    calls: list[dict[str, object]] = []
    fetched_references: list[tuple[str, int]] = []
    stored_media: list[StoredMedia] = []
    registry = build_credential_registry(environment.capabilities)
    id_counter = 0

    def new_id() -> str:
        nonlocal id_counter
        id_counter += 1
        return f"batch-worker-{id_counter}"

    runtime = GenerationRuntimeConfig(
        session_factory=lambda: Session(environment.engine),
        db_clock=lambda: datetime(2026, 10, 16, 2, 0, 30, tzinfo=timezone.utc),
        id_factory=new_id,
        http_client_factory=lambda: _HttpContext(_HttpClient(calls, timeout=timeout)),
        credential_resolver=registry.resolve_original_credential,
        provider_factories=(),
        media_store_factory=lambda: _MediaStore(stored_media),
        local_muse_staging_dir=None,
        public_media_fetcher_factory=lambda: _PublicFetcher(fetched_references),
        bound_provider_content_fetcher_factory=lambda: object(),
        local_muse_capability_factory=lambda: object(),
        r2_storage_factory=lambda: object(),
    )
    return runtime, calls, fetched_references, stored_media


def _financial_state(environment, owner_id: str):
    with Session(environment.engine) as session:
        balance = session.execute(
            select(UserCreditRow.__table__.c.credits).where(
                UserCreditRow.__table__.c.user_id == owner_id
            )
        ).scalar_one()
        ledger = session.execute(
            select(
                CreditLogRow.__table__.c.id,
                CreditLogRow.__table__.c.amount,
                CreditLogRow.__table__.c.business_key,
            )
            .where(CreditLogRow.__table__.c.user_id == owner_id)
            .order_by(CreditLogRow.__table__.c.id)
        ).all()
    return balance, tuple(ledger)


def _task_owner_id(environment, task_id: str) -> str:
    with Session(environment.engine) as session:
        return session.execute(
            select(AITaskRow.__table__.c.user_id).where(
                AITaskRow.__table__.c.id == task_id
            )
        ).scalar_one()


def _image_batch_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    item_count: int,
):
    monkeypatch.setattr(joint_fixtures, "_G2_JOINT_PROVIDER", "openai")
    environment, staged = _authenticated_batch_environment(
        monkeypatch,
        tmp_path,
        model_source=ModelSource.SYSTEM,
        item_count=item_count,
    )
    assert isinstance(staged, AdmissionStaged)
    return environment, staged


def test_batch_worker_prepares_all_references_posts_once_and_saves_selected_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment, staged = _image_batch_environment(
        tmp_path, monkeypatch, item_count=2
    )
    runtime_configuration, calls, fetched, stored = _runtime_configuration(
        environment, timeout=False
    )
    owner_id = _task_owner_id(environment, staged.ordered_tasks[0].task_id)
    financials_before = _financial_state(environment, owner_id)
    runtime = build_image_batch_worker_runtime(
        ImageBatchWorkerConfiguration(runtime_configuration, environment.capabilities)
    )

    outcome = runtime.run_claimed_once("batch-image")

    assert outcome is not None and outcome not in {"pending", "ownership_lost"}
    selected_index = next(
        index
        for index, task in enumerate(staged.ordered_tasks)
        if task.task_id == outcome.task_id
    )
    sibling_task_id = staged.ordered_tasks[1 - selected_index].task_id
    expected_locators = (
        (
            "https://ref.example.test/first?ordered=1",
            "https://ref.example.test/shared?ordered=2",
            "https://ref.example.test/first?ordered=1",
        )
        if selected_index == 0
        else (
            "https://ref.example.test/shared?ordered=2",
            "https://ref.example.test/second?ordered=3",
        )
    )
    expected_contents = (
        (_REFERENCE_A, _REFERENCE_B, _REFERENCE_A)
        if selected_index == 0
        else (_REFERENCE_B, _REFERENCE_C)
    )
    assert len(calls) == 1
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"].endswith("/images/edits")
    assert calls[0]["follow_redirects"] is False
    assert calls[0]["headers"]["Authorization"].startswith("Bearer ")
    assert len(fetched) == len(expected_locators)
    assert tuple(locator for locator, _limit in fetched) == expected_locators
    assert all(limit == 10 * 1024 * 1024 for _locator, limit in fetched)
    body = calls[0]["content"]
    assert type(body) is bytes
    for index in range(len(expected_contents)):
        assert body.count(f'name="image"; filename="ref_{index}.png"'.encode()) == 1
    cursor = 0
    positions: list[int] = []
    for content in expected_contents:
        position = body.index(content, cursor)
        positions.append(position)
        cursor = position + len(content)
    assert positions == sorted(positions)
    assert len(stored) == 1

    with Session(environment.engine) as session:
        task = session.execute(
            select(AITaskRow.__table__).where(
                AITaskRow.__table__.c.id == outcome.task_id
            )
        ).mappings().one()
        sibling = session.execute(
            select(AITaskRow.__table__).where(
                AITaskRow.__table__.c.id == sibling_task_id
            )
        ).mappings().one()
        unit = session.execute(
            select(BillingUnitRow.__table__).where(
                BillingUnitRow.__table__.c.task_id == outcome.task_id
            )
        ).mappings().one()
        step = session.execute(
            select(ExecutionStepRow.__table__).where(
                ExecutionStepRow.__table__.c.id == outcome.step_id
            )
        ).mappings().one()
        external = session.execute(
            select(ExternalSubmissionRow.__table__).where(
                ExternalSubmissionRow.__table__.c.step_id == outcome.step_id
            )
        ).mappings().one()
        receipt = session.execute(
            select(GenerationResultReceiptRow.__table__).where(
                GenerationResultReceiptRow.__table__.c.task_id == outcome.task_id
            )
        ).mappings().one()
        evidence = session.execute(
            select(ResultEvidenceRow.__table__).where(
                ResultEvidenceRow.__table__.c.id == receipt["evidence_id"]
            )
        ).mappings().one()
        assert task["status"] == "completed"
        assert json.loads(task["result"])["url"] == stored[0].locator
        assert sibling["status"] == "queued"
        assert unit["result_status"] == "succeeded"
        assert unit["refunded_amount"] == 0
        assert step["execution_status"] == "result_observed"
        assert external["submission_status"] == "completed"
        assert external["external_task_id"] is None
        assert receipt["operation"] == "image.batch"
        assert receipt["task_id"] == outcome.task_id
        assert receipt["result_ref"] == outcome.result_ref
        assert receipt["recoverable_locator"] == stored[0].locator
        assert receipt["content_digest"] == stored[0].content_digest
        assert evidence["validity"] == "valid"
    assert _financial_state(environment, owner_id) == financials_before
    runtime.shutdown("test_complete")
    environment.engine.dispose()


def test_batch_timeout_after_request_entry_remains_unknown_without_repost_or_refund(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment, staged = _image_batch_environment(
        tmp_path, monkeypatch, item_count=1
    )
    runtime_configuration, calls, _fetched, stored = _runtime_configuration(
        environment, timeout=True
    )
    task_id = staged.ordered_tasks[0].task_id
    owner_id = _task_owner_id(environment, task_id)
    financials_before = _financial_state(environment, owner_id)
    runtime = build_image_batch_worker_runtime(
        ImageBatchWorkerConfiguration(runtime_configuration, environment.capabilities)
    )

    first = runtime.run_claimed_once("batch-image")
    second = runtime.run_claimed_once("batch-image")

    assert first == "pending"
    assert second == "pending"
    assert len(calls) == 1
    assert calls[0]["method"] == "POST"
    assert stored == []
    with Session(environment.engine) as session:
        task = session.execute(
            select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
        ).mappings().one()
        unit = session.execute(
            select(BillingUnitRow.__table__).where(
                BillingUnitRow.__table__.c.task_id == task_id
            )
        ).mappings().one()
        external = session.execute(
            select(ExternalSubmissionRow.__table__).where(
                ExternalSubmissionRow.__table__.c.step_id
                == session.execute(
                    select(ExecutionStepRow.__table__.c.id).where(
                        ExecutionStepRow.__table__.c.billing_unit_id == unit["id"]
                    )
                ).scalar_one()
            )
        ).mappings().all()
        refunds = session.execute(
            select(func.count()).select_from(CreditLogRow.__table__).where(
                CreditLogRow.__table__.c.user_id == owner_id,
                CreditLogRow.__table__.c.type == "refund",
            )
        ).scalar_one()
        assert task["status"] == "reconciling"
        assert unit["result_status"] == "unknown"
        assert unit["billing_status"] == "charged"
        assert unit["refunded_amount"] == 0
        assert len(external) == 1
        assert external[0]["external_task_id"] is None
        assert refunds == 0
    assert _financial_state(environment, owner_id) == financials_before
    runtime.shutdown("test_complete")
    environment.engine.dispose()
