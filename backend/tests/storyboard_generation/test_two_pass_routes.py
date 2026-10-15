from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import insert, inspect, select, update
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.app import create_app
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.tables import user_credits
from haoai_backend.generation_catalog.tables import (
    model_configs,
    prompt_configs,
    system_models,
    system_prompts,
)
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    TaskQuoteRow,
    TaskSubmissionRow,
)
from haoai_backend.series_data.tables import series
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.storyboard_generation.errors import StoryboardGenerationTaskConflict
from haoai_backend.storyboard_generation.persistence import (
    SqlAlchemyStoryboardGenerationUnitOfWork,
)
from haoai_backend.storyboard_generation.two_pass_billing import (
    TwoPassBillingService,
    TwoPassQuoteRequest,
)
from haoai_backend.storyboard_generation.two_pass_execution import TwoPassStoryboardExecution

from two_pass_test_support import TEST_SECRET, TwoPassTestSystem, build_two_pass_system


@pytest.fixture
def two_pass_system(tmp_path):
    system = build_two_pass_system(tmp_path / "two-pass-routes.sqlite", multiplier=2)
    try:
        yield system
    finally:
        system.engine.dispose()


def _quote_body(model_id: str = "system-text-model") -> dict[str, object]:
    return {
        "series_id": "series-member",
        "raw_content": "synthetic source text",
        "model_id": model_id,
        "scene_prompt_id": "scene-prompt",
        "frame_prompt_id": "frame-prompt",
    }


def _request(
    system: TwoPassTestSystem,
    method: str,
    path: str,
    *,
    body: object | None = None,
    user_id: str = "member",
    headers: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
) -> httpx.Response:
    async def send() -> httpx.Response:
        request_headers = {
            "Authorization": f"Bearer {system.token_for(user_id)}",
            **(headers or {}),
        }
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=system.app, raise_app_exceptions=False),
            base_url="http://two-pass.test",
        ) as client:
            return await client.request(
                method,
                path,
                json=body,
                headers=request_headers,
                params=params,
            )

    return asyncio.run(send())


def _events(response: httpx.Response) -> list[dict[str, object]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def test_paid_quote_stream_and_recovery_replay_without_second_charge(two_pass_system) -> None:
    quoted = _request(
        two_pass_system,
        "POST",
        "/api/storyboard/two-pass/quote",
        body=_quote_body(),
    )
    assert quoted.status_code == 200
    quote = quoted.json()
    assert quote["unit_price"] == 3
    assert quote["multiplier"] == 2
    assert quote["quoted_amount"] == 6
    assert "api_key" not in quote
    assert "base_url" not in quote
    assert two_pass_system.runtime.calls == []

    stream_headers = {"Idempotency-Key": "two-pass-paid-1"}
    streamed = _request(
        two_pass_system,
        "POST",
        "/api/storyboard/two-pass/stream",
        body={"quote_id": quote["quote_id"]},
        headers=stream_headers,
    )
    events = _events(streamed)
    assert streamed.status_code == 200
    receipt = events[0]
    assert receipt["type"] == "receipt"
    assert isinstance(receipt["submission_id"], str)
    assert receipt["operation"] == "storyboard.two_pass"
    assert isinstance(receipt["task_id"], str)
    assert receipt["status"] == "processing"
    assert receipt["quoted_amount"] == 6
    assert receipt["billing_status"] == "charged"
    assert events[-1]["type"] == "complete"
    assert len(two_pass_system.runtime.calls) == 2
    assert "two-pass-synthetic-provider-key" not in streamed.text

    task_id = str(receipt["task_id"])
    task_response = _request(
        two_pass_system,
        "GET",
        "/api/chat/tasks",
        params={"task_id": task_id},
    )
    submission_response = _request(
        two_pass_system,
        "GET",
        "/api/chat/submissions/storyboard.two_pass",
        params={"idempotency_key": "two-pass-paid-1"},
    )
    assert task_response.status_code == 200
    assert task_response.json()["status"] == "completed"
    assert task_response.json()["billing_status"] == "charged"
    assert submission_response.status_code == 200
    assert submission_response.json()["task_id"] == task_id

    replayed = _request(
        two_pass_system,
        "POST",
        "/api/storyboard/two-pass/stream",
        body={"quote_id": quote["quote_id"]},
        headers=stream_headers,
    )
    replay_events = _events(replayed)
    assert replayed.status_code == 200
    assert replay_events[0]["type"] == "receipt"
    assert replay_events[-1]["type"] == "complete"
    assert len(two_pass_system.runtime.calls) == 2

    with two_pass_system.session_factory() as session:
        balance = session.execute(
            select(user_credits.c.credits).where(user_credits.c.user_id == "member")
        ).scalar_one()
        debits = session.execute(
            select(CreditLogRow).where(CreditLogRow.type == "usage")
        ).scalars().all()
    assert balance == 44
    assert len(debits) == 1


def test_step_two_failure_refunds_paid_work_once(two_pass_system) -> None:
    two_pass_system.runtime.frame_chunks = ["only one frame"]
    quote_response = _request(
        two_pass_system,
        "POST",
        "/api/storyboard/two-pass/quote",
        body=_quote_body(),
    )
    quote = quote_response.json()
    streamed = _request(
        two_pass_system,
        "POST",
        "/api/storyboard/two-pass/stream",
        body={"quote_id": quote["quote_id"]},
        headers={"Idempotency-Key": "two-pass-fails-once"},
    )
    events = _events(streamed)
    assert streamed.status_code == 200
    assert events[-1]["type"] == "error"
    task_id = str(events[0]["task_id"])

    with two_pass_system.session_factory() as session:
        task = session.execute(select(AITaskRow).where(AITaskRow.id == task_id)).scalar_one()
        balance = session.execute(
            select(user_credits.c.credits).where(user_credits.c.user_id == "member")
        ).scalar_one()
        refunds = session.execute(
            select(CreditLogRow).where(CreditLogRow.type == "refund")
        ).scalars().all()
    assert task.status == "failed"
    assert task.billing_status == "refunded"
    assert balance == 50
    assert len(refunds) == 1


def test_byok_failure_does_not_create_a_fake_refund(two_pass_system) -> None:
    two_pass_system.runtime.frame_chunks = ["only one frame"]
    quote_response = _request(
        two_pass_system,
        "POST",
        "/api/storyboard/two-pass/quote",
        body=_quote_body("owned-text-model"),
    )
    assert quote_response.status_code == 200
    assert quote_response.json()["model_source"] == "user"
    assert quote_response.json()["quoted_amount"] == 0
    assert "owned-test-key" not in quote_response.text

    streamed = _request(
        two_pass_system,
        "POST",
        "/api/storyboard/two-pass/stream",
        body={"quote_id": quote_response.json()["quote_id"]},
        headers={"Idempotency-Key": "two-pass-byok-fails"},
    )
    assert _events(streamed)[-1]["type"] == "error"

    with two_pass_system.session_factory() as session:
        task = session.execute(
            select(AITaskRow).where(AITaskRow.id == _events(streamed)[0]["task_id"])
        ).scalar_one()
        balance = session.execute(
            select(user_credits.c.credits).where(user_credits.c.user_id == "member")
        ).scalar_one()
        logs = session.execute(select(CreditLogRow)).scalars().all()
    assert task.status == "failed"
    assert task.billing_status == "no_charge"
    assert balance == 50
    assert logs == []


def test_invalid_idempotency_header_is_client_error(two_pass_system) -> None:
    quote_response = _request(
        two_pass_system,
        "POST",
        "/api/storyboard/two-pass/quote",
        body=_quote_body(),
    )
    response = _request(
        two_pass_system,
        "POST",
        "/api/storyboard/two-pass/stream",
        body={"quote_id": quote_response.json()["quote_id"]},
        headers={"Idempotency-Key": "   "},
    )
    assert response.status_code == 400


def test_stream_does_not_start_when_wallet_debit_fails(two_pass_system) -> None:
    quote_response = _request(
        two_pass_system,
        "POST",
        "/api/storyboard/two-pass/quote",
        body=_quote_body(),
    )
    with two_pass_system.session_factory.begin() as session:
        session.execute(
            update(user_credits)
            .where(user_credits.c.user_id == "member")
            .values(credits=0)
        )

    streamed = _request(
        two_pass_system,
        "POST",
        "/api/storyboard/two-pass/stream",
        body={"quote_id": quote_response.json()["quote_id"]},
        headers={"Idempotency-Key": "two-pass-insufficient"},
    )
    assert streamed.status_code == 402
    assert two_pass_system.runtime.calls == []
    with two_pass_system.session_factory() as session:
        assert session.execute(select(AITaskRow.id)).first() is None
        assert session.execute(select(CreditLogRow.id)).first() is None


@pytest.mark.parametrize(
    "drift",
    ["expired", "price", "prompt", "base_url", "credential", "params"],
)
def test_quote_expiry_or_configuration_drift_rejects_without_side_effects(
    two_pass_system,
    drift: str,
) -> None:
    quote_response = _request(
        two_pass_system,
        "POST",
        "/api/storyboard/two-pass/quote",
        body=_quote_body(),
    )
    quote_id = quote_response.json()["quote_id"]
    with two_pass_system.session_factory.begin() as session:
        if drift == "expired":
            session.execute(
                update(TaskQuoteRow)
                .where(TaskQuoteRow.id == quote_id)
                .values(expires_at=two_pass_system.now - timedelta(seconds=1))
            )
        elif drift == "price":
            session.execute(
                update(system_models)
                .where(system_models.c.id == "system-text-model")
                .values(cost_per_call=4)
            )
        elif drift == "prompt":
            session.execute(
                update(system_prompts)
                .where(system_prompts.c.id == "scene-prompt")
                .values(user_prompt="changed scene prompt")
            )
        elif drift == "base_url":
            session.execute(
                update(system_models)
                .where(system_models.c.id == "system-text-model")
                .values(base_url="https://other-provider.example.test/v1")
            )
        elif drift == "credential":
            session.execute(
                update(system_models)
                .where(system_models.c.id == "system-text-model")
                .values(api_key="rotated-private-test-key")
            )
        else:
            session.execute(
                update(system_models)
                .where(system_models.c.id == "system-text-model")
                .values(params='{"max_tokens":100}')
            )

    streamed = _request(
        two_pass_system,
        "POST",
        "/api/storyboard/two-pass/stream",
        body={"quote_id": quote_id},
        headers={"Idempotency-Key": f"two-pass-drift-{drift}"},
    )
    assert streamed.status_code == 409
    assert two_pass_system.runtime.calls == []
    with two_pass_system.session_factory() as session:
        balance = session.execute(
            select(user_credits.c.credits).where(user_credits.c.user_id == "member")
        ).scalar_one()
        assert session.execute(select(AITaskRow.id)).first() is None
        assert session.execute(select(CreditLogRow.id)).first() is None
    assert balance == 50


def test_inactive_or_foreign_catalog_and_series_are_rejected(two_pass_system) -> None:
    with two_pass_system.session_factory.begin() as session:
        session.execute(
            update(system_models)
            .where(system_models.c.id == "system-text-model")
            .values(is_active=False)
        )
        session.execute(
            insert(model_configs).values(
                id="free-owned-model",
                user_id="free",
                category="text",
                name="Free user's model",
                provider="openai-compatible",
                api_key="private-free-key",
                model="free-wire-model",
                base_url="https://provider.example.test/v1",
                sort_order=0,
                params=None,
                created_at=two_pass_system.now,
                updated_at=two_pass_system.now,
            )
        )
        session.execute(
            insert(prompt_configs).values(
                id="free-scene-prompt",
                user_id="free",
                category="scene-segmentation",
                name="Free user's prompt",
                system_prompt="private prompt",
                user_prompt="split text",
                sort_order=0,
                created_at=two_pass_system.now,
                updated_at=two_pass_system.now,
            )
        )
        session.execute(
            insert(series).values(
                id="series-free",
                user_id="free",
                name="Another user's series",
                description=None,
                image_url=None,
                style_prompt_id=None,
                team_id=None,
                claimed_by=None,
                claimed_at=None,
                created_at=two_pass_system.now,
                updated_at=two_pass_system.now,
            )
        )

    disabled = _request(
        two_pass_system,
        "POST",
        "/api/storyboard/two-pass/quote",
        body=_quote_body(),
    )
    foreign_model = _request(
        two_pass_system,
        "POST",
        "/api/storyboard/two-pass/quote",
        body=_quote_body("free-owned-model"),
    )
    foreign_prompt = _request(
        two_pass_system,
        "POST",
        "/api/storyboard/two-pass/quote",
        body={**_quote_body("owned-text-model"), "scene_prompt_id": "free-scene-prompt"},
    )
    foreign_series = _request(
        two_pass_system,
        "POST",
        "/api/storyboard/two-pass/quote",
        body={**_quote_body("owned-text-model"), "series_id": "series-free"},
    )
    assert disabled.status_code == 404
    assert foreign_model.status_code == 404
    assert foreign_prompt.status_code == 404
    assert foreign_series.status_code == 403
    assert two_pass_system.runtime.calls == []


def test_commit_acknowledgement_loss_returns_503_and_receipt_is_recoverable(tmp_path) -> None:
    system = build_two_pass_system(tmp_path / "two-pass-unknown-commit.sqlite", multiplier=2)
    fault = {"armed": False}

    class CommitThenRaiseSession(Session):
        def commit(self) -> None:
            super().commit()
            if fault["armed"]:
                fault["armed"] = False
                raise RuntimeError("synthetic lost commit acknowledgement")

    factory = sessionmaker(
        system.engine,
        class_=CommitThenRaiseSession,
        expire_on_commit=False,
        future=True,
        info={"fault": fault},
    )
    auth_runtime = AuthenticationRuntime(
        session_factory=system.session_factory,
        secret_key=TEST_SECRET,
        clock=lambda: system.now,
    )
    app = create_app(
        session_factory=factory,
        authentication_runtime=auth_runtime,
        storyboard_generation_runtime=system.runtime,
    )
    system.app = app
    quote_response = _request(
        system,
        "POST",
        "/api/storyboard/two-pass/quote",
        body=_quote_body(),
    )
    fault["armed"] = True

    response = _request(
        system,
        "POST",
        "/api/storyboard/two-pass/stream",
        body={"quote_id": quote_response.json()["quote_id"]},
        headers={"Idempotency-Key": "two-pass-unknown-commit"},
    )
    assert response.status_code == 503
    assert system.runtime.calls == []

    recovered = _request(
        system,
        "GET",
        "/api/chat/submissions/storyboard.two_pass",
        params={"idempotency_key": "two-pass-unknown-commit"},
    )
    assert recovered.status_code == 200
    assert recovered.json()["status"] == "processing"
    assert recovered.json()["quoted_amount"] == 6
    system.engine.dispose()


def test_missing_foundation_quote_table_is_service_unavailable(tmp_path) -> None:
    system = build_two_pass_system(tmp_path / "two-pass-missing-foundation.sqlite", multiplier=2)
    with system.engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE task_quotes")

    response = _request(
        system,
        "POST",
        "/api/storyboard/two-pass/quote",
        body=_quote_body(),
    )
    assert response.status_code == 503
    assert system.runtime.calls == []
    assert not inspect(system.engine).has_table("task_quotes")
    system.engine.dispose()


def test_worker_start_failure_refunds_and_returns_error(monkeypatch, two_pass_system) -> None:
    quote_response = _request(
        two_pass_system,
        "POST",
        "/api/storyboard/two-pass/quote",
        body=_quote_body(),
    )

    def fail_to_start(_self, _execution):
        raise RuntimeError("synthetic scheduler failure")

    monkeypatch.setattr(TwoPassStoryboardExecution, "start", fail_to_start)
    streamed = _request(
        two_pass_system,
        "POST",
        "/api/storyboard/two-pass/stream",
        body={"quote_id": quote_response.json()["quote_id"]},
        headers={"Idempotency-Key": "two-pass-start-failure"},
    )
    events = _events(streamed)
    assert streamed.status_code == 200
    assert events[0]["status"] == "failed"
    assert events[0]["billing_status"] == "refunded"
    assert events[-1]["type"] == "error"
    assert two_pass_system.runtime.calls == []
    with two_pass_system.session_factory() as session:
        balance = session.execute(
            select(user_credits.c.credits).where(user_credits.c.user_id == "member")
        ).scalar_one()
        refunds = session.execute(
            select(CreditLogRow).where(CreditLogRow.type == "refund")
        ).scalars().all()
    assert balance == 50
    assert len(refunds) == 1


def test_legacy_storyboard_routes_cannot_take_over_paid_two_pass_task(two_pass_system) -> None:
    actor = TrustedActor("member")
    billing = TwoPassBillingService(
        two_pass_system.session_factory,
        now=lambda: two_pass_system.now,
    )
    quote = billing.create_quote(
        actor,
        TwoPassQuoteRequest(**_quote_body()),
    )
    admission = billing.admit(actor, quote["quote_id"], "legacy-takeover-check")
    assert admission.execution is not None
    task_id = admission.execution.task_id
    assert two_pass_system.runtime.calls == []

    legacy_generate = _request(
        two_pass_system,
        "POST",
        "/api/storyboard/generate",
        body={
            "raw_content": "attacker-provided replacement script",
            "model_id": "system-text-model",
            "task_id": task_id,
        },
    )
    legacy_two_pass = _request(
        two_pass_system,
        "POST",
        "/api/storyboard/two-pass",
        body={
            "raw_content": "attacker-provided replacement script",
            "model_id": "system-text-model",
            "task_id": task_id,
            "skip_frame_generation": True,
        },
    )
    assert legacy_generate.status_code == 409
    assert legacy_two_pass.status_code == 409
    assert two_pass_system.runtime.calls == []

    with two_pass_system.session_factory() as session:
        with pytest.raises(StoryboardGenerationTaskConflict):
            SqlAlchemyStoryboardGenerationUnitOfWork(session).write_task_result(
                actor,
                task_id,
                status="completed",
                result='{"frames":[{"text":"forged"}]}',
            )
        session.rollback()

    with two_pass_system.session_factory() as session:
        task = session.execute(select(AITaskRow).where(AITaskRow.id == task_id)).scalar_one()
        unit = session.execute(
            select(BillingUnitRow).where(BillingUnitRow.task_id == task_id)
        ).scalar_one()
        submission = session.execute(
            select(TaskSubmissionRow).where(TaskSubmissionRow.task_ids == [task_id])
        ).scalar_one()
        balance = session.execute(
            select(user_credits.c.credits).where(user_credits.c.user_id == actor.user_id)
        ).scalar_one()
    assert task.status == "processing"
    assert unit.result_status == "pending"
    assert submission.status == "accepted"
    assert balance == 44


def test_legacy_task_result_writes_remain_available_with_foundation_tables(two_pass_system) -> None:
    actor = TrustedActor("member")
    task_id = "legacy-storyboard-task"
    with two_pass_system.session_factory.begin() as session:
        session.add(
            AITaskRow(
                id=task_id,
                user_id=actor.user_id,
                type="storyboard",
                status="processing",
                billing_status="unbilled",
                message_id="legacy-message",
                credit_cost=0,
                result=None,
                request_data="{}",
                model_name="legacy-model",
                progress=0,
                progress_message="",
                created_at=two_pass_system.now,
                updated_at=two_pass_system.now,
            )
        )

    with two_pass_system.session_factory.begin() as session:
        uow = SqlAlchemyStoryboardGenerationUnitOfWork(session)
        uow.validate_task(actor, task_id)
        assert uow.write_task_result(
            actor,
            task_id,
            status="completed",
            result='{"frames":[]}',
        )
