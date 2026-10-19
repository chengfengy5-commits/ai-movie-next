"""HTTP evidence for the owner-scoped registered frame.review cancel route."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Callable

import httpx
from sqlalchemy import event, select
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.app import create_app
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.tokens import JoseTokenCodec
from haoai_backend.chat_text_generation.ports import (
    ChatTextGenerationConfiguration,
    FrameReviewAdmissionConfiguration,
)
from haoai_backend.generation_contracts.ports import GenerationRuntimeConfig
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
    UserCreditRow,
)
from haoai_backend.generation_core.schema import UnitFeeDecisionRow
from haoai_backend.generation_credentials.persistence import prepared_payloads
from haoai_backend.generation_runtime.frame_review_assembly import (
    FrameReviewWorkerConfiguration,
)
from generation_execution.test_frame_review_admission import (
    _JWT_SECRET,
    _build_app,
    _post_review,
    _review_body,
)


def _effective_session_factory(engine, opened_sessions: list[Session]):
    sessions = sessionmaker(engine, expire_on_commit=False)

    def open_session() -> Session:
        session = sessions()
        opened_sessions.append(session)
        return session

    return open_session


def _install_cancel_ack_loss_proxy(
    monkeypatch,
    *,
    engine,
    opened_sessions: list[Session],
):
    import importlib

    app_module = importlib.import_module("haoai_backend.app")
    real_builder = app_module.build_frame_review_cancellation_facade
    acknowledgement_losses: list[Session] = []
    attempts: list[tuple[str, tuple[Session, ...]]] = []

    class CancellationProxy:
        def __init__(self, delegate) -> None:
            self._delegate = delegate

        def cancel_owned(self, actor, task_id: str) -> str:
            start = len(opened_sessions)

            def lose_first_ack(session: Session) -> None:
                if not acknowledgement_losses and session.get_bind() is engine:
                    acknowledgement_losses.append(session)
                    raise RuntimeError("synthetic frame.review cancellation ACK loss")

            event.listen(Session, "after_commit", lose_first_ack)
            try:
                outcome = self._delegate.cancel_owned(actor, task_id)
            finally:
                event.remove(Session, "after_commit", lose_first_ack)
            attempts.append((outcome, tuple(opened_sessions[start:])))
            return outcome

        def consume_persisted(self, scope, *, stop_signal=None) -> str:
            return self._delegate.consume_persisted(scope, stop_signal=stop_signal)

    def build_proxy(**kwargs):
        facade = real_builder(**kwargs)
        return None if facade is None else CancellationProxy(facade)

    monkeypatch.setattr(
        app_module, "build_frame_review_cancellation_facade", build_proxy
    )
    return acknowledgement_losses, attempts


def _worker_runtime(session_factory: Callable[[], Session], capabilities):
    assert callable(capabilities.db_clock)
    assert callable(capabilities.id_factory)
    return GenerationRuntimeConfig(
        session_factory=session_factory,
        db_clock=capabilities.db_clock,
        id_factory=capabilities.id_factory,
        http_client_factory=lambda: None,
        credential_resolver=lambda *_args: None,
        provider_factories=(),
        media_store_factory=lambda: object(),
        local_muse_staging_dir=None,
        public_media_fetcher_factory=lambda: object(),
        bound_provider_content_fetcher_factory=lambda: object(),
        local_muse_capability_factory=lambda: object(),
        r2_storage_factory=lambda: object(),
    )


async def _post_cancel(app, token: str, task_id: str) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://frame-review-cancel.test",
    ) as client:
        return await client.post(
            f"/api/chat/batch-optimize/{task_id}/cancel",
            headers={"Authorization": f"Bearer {token}"},
        )


def test_registered_frame_review_cancel_is_owned_and_durable(tmp_path, monkeypatch) -> None:
    engine, app, token, _capabilities, _crypto_state = _build_app(
        tmp_path,
        monkeypatch,
        cost_per_call=3,
        wallet_credits=500,
    )
    try:
        request_body = _review_body()
        request_body.update(
            {
                "model_owner_id": "attacker-selected-owner",
                "owner_id": "attacker-selected-owner",
                "source": "user",
                "is_system": False,
            }
        )
        admitted = asyncio.run(_post_review(app, token, request_body))
        assert admitted.status_code == 200, admitted.text
        task_id = admitted.json()["task_id"]

        cancelled = asyncio.run(_post_cancel(app, token, task_id))
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json() == {"task_id": task_id, "status": "cancelling"}

        sessions = sessionmaker(engine, expire_on_commit=False)
        with sessions() as session:
            task = session.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == task_id)
            ).mappings().one()
            request = json.loads(task["request_data"])
            payload = session.execute(
                select(
                    prepared_payloads.c.source,
                    prepared_payloads.c.owner_or_system,
                ).where(
                    prepared_payloads.c.prepared_payload_id
                    == request["prepared_payload_id"]
                )
            ).mappings().one()
            unit = session.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.task_id == task_id
                )
            ).mappings().one()
            fees = session.execute(
                select(UnitFeeDecisionRow.__table__).where(
                    UnitFeeDecisionRow.unit_id == unit["id"]
                )
            ).mappings().all()
            ledgers = session.execute(
                select(CreditLogRow.__table__).where(
                    CreditLogRow.billing_unit_id == unit["id"]
                )
            ).mappings().all()
            submissions = session.execute(
                select(ExternalSubmissionRow.__table__).where(
                    ExternalSubmissionRow.step_id.in_(
                        select(ExecutionStepRow.__table__.c.id).where(
                            ExecutionStepRow.__table__.c.billing_unit_id == unit["id"]
                        )
                    )
                )
            ).mappings().all()
            wallet = session.execute(
                select(UserCreditRow.__table__.c.credits).where(
                    UserCreditRow.user_id == task["user_id"]
                )
            ).scalar_one()

        assert task["status"] == "failed"
        assert request["model_source"] == "system"
        assert request["model_owner_id"] == "system"
        assert payload["source"] == "system"
        assert payload["owner_or_system"] == "system"
        assert task["claimed_by"] is None
        assert task["claim_token"] is None
        assert task["lease_until"] is None
        assert task["user_cancelled_at"] is not None
        assert task["cancellation_reason"].startswith("user_cancelled:v1:")
        assert unit["user_cancelled_at"] == task["user_cancelled_at"]
        assert unit["cancellation_reason"] == task["cancellation_reason"]
        assert unit["billing_status"] == "unbilled"
        assert unit["debited_amount"] == 0
        assert unit["refunded_amount"] == 0
        assert len(fees) == 1
        assert fees[0]["charge_phase"] == "post_apply"
        assert fees[0]["resolution"] == "waived_cancelled"
        assert fees[0]["ledger_ref"] is None
        assert fees[0]["generation_result_ref"] is None
        assert fees[0]["application_ref"] is None
        assert ledgers == []
        assert submissions == []
        assert wallet == 500

        marker = task["cancellation_reason"]
        replayed = asyncio.run(_post_cancel(app, token, task_id))
        assert replayed.status_code == 200, replayed.text
        assert replayed.json() == {"task_id": task_id, "status": "cancelling"}
        with sessions() as session:
            replayed_task = session.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == task_id)
            ).mappings().one()
            replayed_fees = session.execute(
                select(UnitFeeDecisionRow.__table__).where(
                    UnitFeeDecisionRow.unit_id == unit["id"]
                )
            ).mappings().all()
        assert replayed_task["cancellation_reason"] == marker
        assert replayed_fees == fees
    finally:
        engine.dispose()


def test_frame_review_cancellation_fails_closed_when_worker_database_differs(
    tmp_path, monkeypatch
) -> None:
    database_a = tmp_path / "database-a"
    database_b = tmp_path / "database-b"
    database_a.mkdir()
    database_b.mkdir()
    engine_a, app_a, token, _capabilities_a, _crypto_a = _build_app(
        database_a, monkeypatch
    )
    engine_b, app_b, _token_b, capabilities_b, _crypto_b = _build_app(
        database_b, monkeypatch
    )
    calls = {"application": 0, "worker": 0, "legacy": 0}

    def application_sessions():
        calls["application"] += 1
        return sessionmaker(engine_a, expire_on_commit=False)()

    worker_registry_factory = capabilities_b.read_session_factory
    assert callable(worker_registry_factory)

    def worker_sessions():
        calls["worker"] += 1
        return worker_registry_factory()

    runtime = GenerationRuntimeConfig(
        session_factory=worker_sessions,
        db_clock=capabilities_b.db_clock,
        id_factory=capabilities_b.id_factory,
        http_client_factory=lambda: None,
        credential_resolver=lambda *_args: None,
        provider_factories=(),
        media_store_factory=lambda: object(),
        local_muse_staging_dir=None,
        public_media_fetcher_factory=lambda: object(),
        bound_provider_content_fetcher_factory=lambda: object(),
        local_muse_capability_factory=lambda: object(),
        r2_storage_factory=lambda: object(),
    )
    worker_capabilities = replace(
        capabilities_b,
        read_session_factory=worker_sessions,
    )

    def reject_legacy_connection():
        calls["legacy"] += 1
        raise AssertionError("selected registered reviews must not use legacy writer")

    authentication_runtime = AuthenticationRuntime(
        session_factory=application_sessions,
        secret_key=_JWT_SECRET,
        token_codec=JoseTokenCodec(_JWT_SECRET),
    )
    mismatched_app = create_app(
        session_factory=application_sessions,
        authentication_runtime=authentication_runtime,
        task_cancellation_connection_factory=reject_legacy_connection,
        frame_review_worker_configuration=FrameReviewWorkerConfiguration(
            runtime=runtime,
            registry_capabilities=worker_capabilities,
        ),
    )
    sessions_b = sessionmaker(engine_b, expire_on_commit=False)
    try:
        admitted_a = asyncio.run(_post_review(app_a, token))
        admitted_b = asyncio.run(_post_review(app_b, token))
        assert admitted_a.status_code == 200, admitted_a.text
        assert admitted_b.status_code == 200, admitted_b.text
        task_id = admitted_a.json()["task_id"]
        assert admitted_b.json()["task_id"] == task_id
        assert calls == {"application": 0, "worker": 0, "legacy": 0}

        with sessions_b() as session:
            task_before = dict(
                session.execute(
                    select(AITaskRow.__table__).where(AITaskRow.id == task_id)
                ).mappings().one()
            )
            unit_before = dict(
                session.execute(
                    select(BillingUnitRow.__table__).where(
                        BillingUnitRow.__table__.c.task_id == task_id
                    )
                ).mappings().one()
            )
            fees_before = session.execute(
                select(UnitFeeDecisionRow.__table__)
            ).mappings().all()
            ledgers_before = session.execute(
                select(CreditLogRow.__table__)
            ).mappings().all()

        response = asyncio.run(_post_cancel(mismatched_app, token, task_id))
        assert response.status_code == 503, response.text
        assert calls["application"] > 0
        assert calls["worker"] == 0
        assert calls["legacy"] == 0

        with sessions_b() as session:
            task_after = dict(
                session.execute(
                    select(AITaskRow.__table__).where(AITaskRow.id == task_id)
                ).mappings().one()
            )
            unit_after = dict(
                session.execute(
                    select(BillingUnitRow.__table__).where(
                        BillingUnitRow.__table__.c.task_id == task_id
                    )
                ).mappings().one()
            )
            fees_after = session.execute(
                select(UnitFeeDecisionRow.__table__)
            ).mappings().all()
            ledgers_after = session.execute(
                select(CreditLogRow.__table__)
            ).mappings().all()

        assert task_after == task_before
        assert unit_after == unit_before
        assert fees_after == fees_before == []
        assert ledgers_after == ledgers_before == []
    finally:
        engine_a.dispose()
        engine_b.dispose()


def test_chat_cancellation_ignores_dormant_registry_factory_through_ack_and_replay(
    tmp_path, monkeypatch
) -> None:
    engine, admission_app, token, capabilities, _crypto_state = _build_app(
        tmp_path, monkeypatch, cost_per_call=3, wallet_credits=500
    )
    opened_sessions: list[Session] = []
    effective_factory = _effective_session_factory(engine, opened_sessions)
    registry_factory_calls: list[str] = []
    legacy_factory_calls: list[str] = []

    def dormant_registry_factory() -> Session:
        registry_factory_calls.append("called")
        raise AssertionError("cancellation must not open the dormant registry factory")

    def reject_legacy_factory():
        legacy_factory_calls.append("called")
        raise AssertionError("registered reviews must not use the legacy writer")

    try:
        admitted = asyncio.run(_post_review(admission_app, token))
        assert admitted.status_code == 200, admitted.text
        task_id = admitted.json()["task_id"]

        cancellation_capabilities = replace(
            capabilities, read_session_factory=dormant_registry_factory
        )
        authentication_runtime = AuthenticationRuntime(
            session_factory=effective_factory,
            secret_key=_JWT_SECRET,
            token_codec=JoseTokenCodec(_JWT_SECRET),
        )
        acknowledgement_losses, attempts = _install_cancel_ack_loss_proxy(
            monkeypatch,
            engine=engine,
            opened_sessions=opened_sessions,
        )
        cancellation_app = create_app(
            session_factory=effective_factory,
            authentication_runtime=authentication_runtime,
            task_cancellation_connection_factory=reject_legacy_factory,
            chat_text_generation_configuration=ChatTextGenerationConfiguration(
                runtime=None,
                server_id_factory=lambda: "cancel-chat-server",
                clock=capabilities.db_clock,
                frame_review_admission=FrameReviewAdmissionConfiguration(
                    cancellation_capabilities
                ),
            ),
        )
        assert opened_sessions == []
        assert registry_factory_calls == []
        assert legacy_factory_calls == []

        cancelled = asyncio.run(_post_cancel(cancellation_app, token, task_id))
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json() == {"task_id": task_id, "status": "cancelling"}
        assert len(acknowledgement_losses) == 1
        assert len(attempts) == 1 and attempts[0][0] == "settled"
        confirmation_sessions = attempts[0][1]
        assert len(confirmation_sessions) == 2
        assert confirmation_sessions[0] is not confirmation_sessions[1]
        assert acknowledgement_losses[0] is confirmation_sessions[0]
        assert all(session in opened_sessions for session in confirmation_sessions)
        assert all(session.get_bind() is engine for session in confirmation_sessions)
        assert registry_factory_calls == []
        assert legacy_factory_calls == []

        sessions = sessionmaker(engine, expire_on_commit=False)
        with sessions() as session:
            task = session.execute(
                select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
            ).mappings().one()
            fees = session.execute(
                select(UnitFeeDecisionRow.__table__)
            ).mappings().all()
            ledgers = session.execute(
                select(CreditLogRow.__table__)
            ).mappings().all()
        assert task["status"] == "failed"
        assert task["cancellation_reason"].startswith("user_cancelled:v1:")
        assert len(fees) == 1 and fees[0]["resolution"] == "waived_cancelled"
        assert ledgers == []

        replayed = asyncio.run(_post_cancel(cancellation_app, token, task_id))
        assert replayed.status_code == 200, replayed.text
        assert replayed.json() == {"task_id": task_id, "status": "cancelling"}
        assert len(attempts) == 2 and attempts[1][0] == "retained"
        assert len(attempts[1][1]) == 1
        with sessions() as session:
            replayed_task = session.execute(
                select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
            ).mappings().one()
            replayed_fees = session.execute(
                select(UnitFeeDecisionRow.__table__)
            ).mappings().all()
        assert replayed_task["cancellation_reason"] == task["cancellation_reason"]
        assert replayed_fees == fees
        assert registry_factory_calls == []
        assert legacy_factory_calls == []
    finally:
        engine.dispose()


def test_worker_cancellation_accepts_shared_effective_factory_and_confirms_ack(
    tmp_path, monkeypatch
) -> None:
    engine, admission_app, token, capabilities, _crypto_state = _build_app(
        tmp_path, monkeypatch, cost_per_call=3, wallet_credits=500
    )
    opened_sessions: list[Session] = []
    effective_factory = _effective_session_factory(engine, opened_sessions)
    registry_factory_calls: list[str] = []
    legacy_factory_calls: list[str] = []

    def dormant_registry_factory() -> Session:
        registry_factory_calls.append("called")
        raise AssertionError("worker cancellation must not open the registry factory")

    def reject_legacy_factory():
        legacy_factory_calls.append("called")
        raise AssertionError("registered reviews must not use the legacy writer")

    try:
        admitted = asyncio.run(_post_review(admission_app, token))
        assert admitted.status_code == 200, admitted.text
        task_id = admitted.json()["task_id"]

        worker_capabilities = replace(
            capabilities, read_session_factory=dormant_registry_factory
        )
        runtime = _worker_runtime(effective_factory, capabilities)
        authentication_runtime = AuthenticationRuntime(
            session_factory=effective_factory,
            secret_key=_JWT_SECRET,
            token_codec=JoseTokenCodec(_JWT_SECRET),
        )
        acknowledgement_losses, attempts = _install_cancel_ack_loss_proxy(
            monkeypatch,
            engine=engine,
            opened_sessions=opened_sessions,
        )
        cancellation_app = create_app(
            session_factory=effective_factory,
            authentication_runtime=authentication_runtime,
            task_cancellation_connection_factory=reject_legacy_factory,
            frame_review_worker_configuration=FrameReviewWorkerConfiguration(
                runtime=runtime,
                registry_capabilities=worker_capabilities,
            ),
        )
        assert runtime.session_factory is effective_factory
        assert opened_sessions == []
        assert registry_factory_calls == []
        assert legacy_factory_calls == []

        cancelled = asyncio.run(_post_cancel(cancellation_app, token, task_id))
        assert cancelled.status_code == 200, cancelled.text
        assert len(acknowledgement_losses) == 1
        assert len(attempts) == 1 and attempts[0][0] == "settled"
        confirmation_sessions = attempts[0][1]
        assert len(confirmation_sessions) == 2
        assert confirmation_sessions[0] is not confirmation_sessions[1]
        assert acknowledgement_losses[0] is confirmation_sessions[0]
        assert all(session in opened_sessions for session in confirmation_sessions)
        assert all(session.get_bind() is engine for session in confirmation_sessions)
        assert registry_factory_calls == []
        assert legacy_factory_calls == []

        replayed = asyncio.run(_post_cancel(cancellation_app, token, task_id))
        assert replayed.status_code == 200, replayed.text
        assert len(attempts) == 2 and attempts[1][0] == "retained"
        assert len(attempts[1][1]) == 1
        assert registry_factory_calls == []
        assert legacy_factory_calls == []
    finally:
        engine.dispose()
