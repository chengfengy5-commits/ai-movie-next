"""SQLite/ASGI evidence for the explicit frame.review worker consumer."""

from __future__ import annotations

import asyncio
import json
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from haoai_backend.app import create_app
from haoai_backend.generation_contracts.ports import GenerationRuntimeConfig
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
    UserCreditRow,
)
from haoai_backend.generation_core.schema import (
    ApplicationRecordRow,
    GenerationResultReceiptRow,
    LocalContinuationWorkRow,
    ResultEvidenceRow,
    UnitFeeDecisionRow,
)
from haoai_backend.generation_core.frame_review_continuation_claims import (
    SqlAlchemyFrameReviewContinuationClaims,
)
from haoai_backend.generation_credentials.application import (
    CredentialRegistry,
    build_credential_registry,
)
from haoai_backend.generation_runtime.frame_review_assembly import (
    FrameReviewWorkerConfiguration,
)
from haoai_backend.series_data.tables import chapters
from haoai_backend.personal_production.notes.tables import metadata as notes_metadata
import generation_execution.test_frame_review_admission as admission
import generation_core.test_admission_staging as admission_fixtures


class _Response:
    def __init__(self, payload: object, status_code: int) -> None:
        self.status_code = status_code
        self._body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.headers = {"content-length": str(len(self._body))}

    async def iter_bytes(self, *, chunk_size: int):
        assert chunk_size > 0
        yield self._body


class _Client:
    def __init__(self, requests: list[dict[str, object]], status_code: int) -> None:
        self._requests = requests
        self._status_code = status_code

    async def request(self, method, url, *, headers, content, timeout_seconds, follow_redirects):
        self._requests.append(
            {
                "method": method,
                "url": url,
                "has_authorization": bool(headers.get("Authorization")),
                "content": content,
                "timeout_seconds": timeout_seconds,
                "follow_redirects": follow_redirects,
            }
        )
        return _Response(
            {"choices": [{"message": {"content": "优化后的分镜正文"}, "finish_reason": "stop"}]},
            self._status_code,
        )


class _ClientContext(AbstractAsyncContextManager):
    def __init__(self, client: _Client) -> None:
        self._client = client

    async def __aenter__(self):
        return self._client

    async def __aexit__(self, *_args):
        return False


def test_frame_review_worker_requires_explicit_configuration() -> None:
    app = create_app()

    async def inspect_lifespan() -> None:
        async with app.router.lifespan_context(app):
            assert app.state.frame_review_worker_runtime is None
            assert app.state.frame_review_worker_service is None

    asyncio.run(inspect_lifespan())


@pytest.mark.parametrize(
    ("provider_status", "cost_per_call", "wallet_credits"),
    ((200, 3, 500), (200, 0, None), (500, 3, 500)),
)
def test_http_admission_reaches_sql_result_and_task_observation_via_lifespan_worker(
    tmp_path: Path,
    monkeypatch,
    provider_status: int,
    cost_per_call: int,
    wallet_credits: int | None,
) -> None:
    requests: list[dict[str, object]] = []
    released_phases: list[tuple[int, bool]] = []
    claimed_phases: list[int] = []
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(admission, "_G2_JOINT_NOW", now)
    monkeypatch.setattr(admission_fixtures, "_G2_JOINT_NOW", now)
    runtime_session_calls = 0
    worker_id = 0
    original_create_app = admission.create_app
    original_claim_continuation = (
        SqlAlchemyFrameReviewContinuationClaims.claim_continuation
    )
    original_release_continuation = (
        SqlAlchemyFrameReviewContinuationClaims.release_continuation
    )

    def trace_claim(self, task_type, instance, lease_seconds):
        claim = original_claim_continuation(self, task_type, instance, lease_seconds)
        if claim is not None:
            claimed_phases.append(claim.phase_version)
        return claim

    def trace_release(self, claim):
        released = original_release_continuation(self, claim)
        released_phases.append((claim.phase_version, released))
        return released

    monkeypatch.setattr(
        SqlAlchemyFrameReviewContinuationClaims, "claim_continuation", trace_claim
    )
    monkeypatch.setattr(
        SqlAlchemyFrameReviewContinuationClaims,
        "release_continuation",
        trace_release,
    )

    def create_app_with_frame_review_worker(**kwargs):
        nonlocal runtime_session_calls, worker_id
        capabilities = kwargs["chat_text_generation_configuration"].frame_review_admission.registry_capabilities
        session_factory = kwargs["session_factory"]

        def worker_sessions() -> Session:
            nonlocal runtime_session_calls
            runtime_session_calls += 1
            return session_factory()

        def next_worker_id() -> str:
            nonlocal worker_id
            worker_id += 1
            return f"review-runtime-{worker_id}"

        registry: CredentialRegistry = build_credential_registry(capabilities)
        runtime = GenerationRuntimeConfig(
            session_factory=worker_sessions,
            db_clock=lambda: admission._G2_JOINT_NOW,
            id_factory=next_worker_id,
            http_client_factory=lambda: _ClientContext(
                _Client(requests, provider_status)
            ),
            credential_resolver=registry.resolve_original_credential,
            provider_factories=(),
            media_store_factory=lambda: object(),
            local_muse_staging_dir=None,
            public_media_fetcher_factory=lambda: object(),
            bound_provider_content_fetcher_factory=lambda: object(),
            local_muse_capability_factory=lambda: object(),
            r2_storage_factory=lambda: object(),
            shutdown_timeout_seconds=2.0,
        )
        kwargs["frame_review_worker_configuration"] = FrameReviewWorkerConfiguration(
            runtime=runtime,
            registry_capabilities=capabilities,
        )
        system = original_create_app(**kwargs)
        return system

    monkeypatch.setattr(admission, "create_app", create_app_with_frame_review_worker)
    engine, app, token, capabilities, _ = admission._build_app(
        tmp_path,
        monkeypatch,
        cost_per_call=cost_per_call,
        wallet_credits=wallet_credits,
    )
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "ALTER TABLE storyboard_assets ADD COLUMN created_at DATETIME"
            )
        notes_metadata.create_all(engine)
        assert app.state.frame_review_worker_runtime is not None
        assert runtime_session_calls == 0
        assert requests == []

        async def post_and_observe():
            async with app.router.lifespan_context(app):
                assert app.state.frame_review_worker_service is not None
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                    base_url="http://frame-review-worker.test",
                ) as client:
                    response = await client.post(
                        "/api/chat/ai-review",
                        headers={"Authorization": f"Bearer {token}"},
                        json=admission._review_body(),
                    )
                    assert response.status_code == 200, response.text
                    task_id = response.json()["task_id"]
                    deadline = monotonic() + 5
                    task_row = None
                    work = None
                    external = None
                    while monotonic() < deadline:
                        with engine.connect() as connection:
                            work = connection.execute(
                                select(LocalContinuationWorkRow.__table__).where(
                                    LocalContinuationWorkRow.__table__.c.task_id == task_id
                                )
                            ).mappings().one_or_none()
                            task_row = connection.execute(
                                select(AITaskRow.__table__).where(
                                    AITaskRow.id == task_id
                                )
                            ).mappings().one_or_none()
                            step_id = (
                                select(ExecutionStepRow.id)
                                .join(
                                    BillingUnitRow,
                                    ExecutionStepRow.billing_unit_id == BillingUnitRow.id,
                                )
                                .where(BillingUnitRow.task_id == task_id)
                                .scalar_subquery()
                            )
                            external = connection.execute(
                                select(ExternalSubmissionRow.__table__).where(
                                    ExternalSubmissionRow.__table__.c.step_id == step_id
                                )
                            ).mappings().one_or_none()
                        durable_state = (
                            work is not None and work["phase"] == "done"
                            if provider_status == 200
                            else external is not None
                        )
                        if (
                            task_row is not None
                            and task_row["claim_token"] is None
                            and durable_state
                        ):
                            break
                        await asyncio.sleep(0.025)
                    assert task_row is not None
                    if provider_status == 200:
                        assert work is not None, "worker did not persist continuation work"
                    else:
                        assert external is not None, "worker did not commit the original intent"
                        await asyncio.sleep(0.35)
                        assert len(requests) == 1

                    status = await client.get(
                        f"/api/chat/ai-review/{task_id}",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    counts = await client.get(
                        "/api/chat/ai-review/counts",
                        params={"message_id": "source-message-1"},
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    assert status.status_code == counts.status_code == 200
                    return task_id, status.json(), counts.json(), work

        task_id, status_body, count_body, work = asyncio.run(post_and_observe())

        assert len(requests) == 1
        request = requests[0]
        assert request["method"] == "POST"
        assert str(request["url"]).endswith("/chat/completions")
        assert request["timeout_seconds"] == 300.0
        assert request["follow_redirects"] is False
        body = json.loads(request["content"])
        assert body["stream"] is False
        assert body["model"] == "review-wire-model"
        assert status_body["id"] == task_id
        assert status_body["status"] == (
            "completed" if provider_status == 200 else "queued"
        )
        if provider_status == 200:
            assert claimed_phases == [0, 1, 2]
            assert released_phases == [(0, True), (1, True), (2, True)]
            assert work is not None and work["phase"] == "done"
            assert isinstance(status_body.get("result"), str)
        else:
            assert claimed_phases == []
            assert released_phases == []
            assert status_body.get("result") in (None, "")
        assert count_body == {"counts": {"review-prompt-1": 1}}

        with engine.connect() as connection:
            task = connection.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == task_id)
            ).mappings().one()
            unit = connection.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == task_id
                )
            ).mappings().one()
            receipts = connection.execute(
                select(GenerationResultReceiptRow.__table__).where(
                    GenerationResultReceiptRow.__table__.c.task_id == task_id
                )
            ).mappings().all()
            evidence = connection.execute(
                select(ResultEvidenceRow.__table__)
            ).mappings().all()
            logs = connection.execute(select(CreditLogRow.__table__)).mappings().all()
            fees = connection.execute(
                select(UnitFeeDecisionRow.__table__).where(
                    UnitFeeDecisionRow.__table__.c.unit_id == unit["id"]
                )
            ).mappings().all()
            applications = connection.execute(
                select(ApplicationRecordRow.__table__).where(
                    ApplicationRecordRow.__table__.c.task_id == task_id
                )
            ).mappings().all()
            wallets = connection.execute(
                select(UserCreditRow.__table__).where(
                    UserCreditRow.__table__.c.user_id == task["user_id"]
                )
            ).mappings().all()
            chapter = connection.execute(
                select(chapters).where(chapters.c.id == "joint-chapter-1")
            ).mappings().one()

        if provider_status == 200:
            assert task["status"] == "completed"
            assert task["progress"] == 100
            assert task["result"] == status_body["result"]
            assert work is not None and work["phase"] == "done"
            assert unit["result_status"] == "succeeded"
            assert len(receipts) == len(evidence) == 1
            assert receipts[0]["recoverable_locator"].startswith(
                "frame-review-inline-text-v1:"
            )
            assert len(applications) == 1
            assert applications[0]["delivery_status"] == "applied"
            assert len(fees) == 1
            assert fees[0]["charge_phase"] == "post_apply"
            assert fees[0]["generation_result_ref"] == receipts[0]["result_ref"]
            assert fees[0]["application_ref"] == applications[0]["application_ref"]
            if cost_per_call:
                assert fees[0]["resolution"] == "charged"
                assert len(logs) == 1 and logs[0]["amount"] == -cost_per_call
                assert len(wallets) == 1
                assert wallets[0]["credits"] == wallet_credits - cost_per_call
                assert unit["billing_status"] == task["billing_status"] == "charged"
            else:
                assert fees[0]["resolution"] == "no_charge"
                assert logs == [] and wallets == []
                assert unit["billing_status"] == task["billing_status"] == "no_charge"
            frames = json.loads(chapter["content"])
            assert frames[0]["text"] == "优化后的分镜正文"
            assert frames[0]["original_text"] == "原始分镜正文"
            assert frames[0]["custom"] == {"keep": True}
            assert frames[1]["text"] == "非目标帧"
        else:
            assert task["status"] == "queued"
            assert task["result"] is None
            assert work is None
            assert unit["result_status"] == "pending"
            assert unit["billing_status"] == "unbilled"
            assert unit["debited_amount"] == 0
            assert len(receipts) == len(evidence) == 0
            assert applications == [] and fees == [] and logs == []
            assert len(wallets) == 1 and wallets[0]["credits"] == 500
        assert runtime_session_calls > 0
        assert capabilities.crypto is not None
    finally:
        engine.dispose()
