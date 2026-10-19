"""End-to-end SQL evidence for frame.review cancellation control settlement."""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from itertools import count
from threading import Event

import pytest
from sqlalchemy import event, insert, select, update
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.app import create_app
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.tables import user_sessions, users
from haoai_backend.authentication.tokens import JoseTokenCodec
from haoai_backend.chat_text_generation import ChatTextGenerationConfiguration
from haoai_backend.chat_text_generation.ports import FrameReviewAdmissionConfiguration
from haoai_backend.generation_contracts.types import OwnerTaskScope, TextResult
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
    ResultEvidenceRow,
    TaskSubmissionRow,
    UserCreditRow,
)
from haoai_backend.generation_core.frame_review_cancellation import (
    SqlAlchemyFrameReviewCancellation,
)
from haoai_backend.generation_core.frame_review_results import (
    SqlAlchemyFrameReviewResults,
    decode_inline_text_result,
)
from haoai_backend.generation_core.schema import (
    ApplicationRecordRow,
    GenerationResultReceiptRow,
    LocalContinuationWorkRow,
    UnitFeeDecisionRow,
)
from haoai_backend.generation_catalog.domain import ModelSource
from haoai_backend.generation_catalog.tables import model_configs, storyboard_assets
from haoai_backend.generation_credentials.persistence import prepared_payloads
from haoai_backend.prompts.tables import system_prompts
import generation_core.test_admission_staging as admission_fixtures
from generation_core.test_admission_staging import (
    _G2_JOINT_ACTOR_ID,
    _G2_JOINT_MODEL_ID,
    _G2_JOINT_NOW,
    _joint_database,
    _joint_registered_capabilities,
)
from generation_execution.test_app_frame_review_cancel import _post_cancel
from generation_execution.test_frame_review_admission import (
    _CHAPTER_CONTENT,
    _build_app,
    _post_review,
    _review_body,
)
from generation_execution.test_frame_review_results import _claimed_review

_JWT_SECRET = "frame-review-cancel-control-test-only"
_SESSION_ID = "frame-review-cancel-control-session"
_TOKEN_ID = "frame-review-cancel-control-jti"


def _build_byok_app(
    tmp_path, monkeypatch, *, legacy_connection_factory=None
):
    monkeypatch.setattr(admission_fixtures, "_G2_JOINT_PROVIDER", "openai")
    engine = _joint_database(
        tmp_path,
        model_source=ModelSource.USER,
        wallet_credits=None,
    )
    stamp = _G2_JOINT_NOW.replace(tzinfo=None)
    with engine.begin() as connection:
        connection.execute(
            update(admission_fixtures.catalog_chapters)
            .where(admission_fixtures.catalog_chapters.c.id == "joint-chapter-1")
            .values(content=json.dumps(_CHAPTER_CONTENT, ensure_ascii=False))
        )
        connection.execute(
            update(model_configs)
            .where(model_configs.c.id == _G2_JOINT_MODEL_ID)
            .values(
                category="text",
                provider="openai",
                model="review-byok-wire-model",
                params=json.dumps({"temperature": 0.2, "max_tokens": 800}),
            )
        )
        for index, storyboard_id in enumerate(
            ("review-storyboard-1", "review-storyboard-2")
        ):
            connection.execute(
                insert(storyboard_assets).values(
                    id=storyboard_id,
                    series_id="joint-series-1",
                    chapter_id="joint-chapter-1",
                    frame_index=index,
                    name=f"Review frame {index}",
                    description=None,
                    image_url=None,
                    updated_at=stamp,
                )
            )
        connection.execute(
            insert(system_prompts).values(
                id="review-prompt-1",
                category="ai-review",
                name="Review prompt",
                system_prompt="请审阅分镜文本。",
                user_prompt="请保持人物和场景连续。",
                sort_order=0,
                is_active=True,
                created_at=stamp,
                updated_at=stamp,
            )
        )
        connection.execute(
            insert(user_sessions).values(
                id=_SESSION_ID,
                user_id=_G2_JOINT_ACTOR_ID,
                jti=_TOKEN_ID,
                user_agent="frame review local byok integration",
                ip_address="127.0.0.1",
                device_name="synthetic fixture",
                is_active=True,
                created_at=stamp,
                last_seen_at=stamp,
                revoked_at=None,
            )
        )

    capabilities, _crypto_state = _joint_registered_capabilities(
        engine, model_source=ModelSource.USER
    )
    sessions = sessionmaker(engine, expire_on_commit=False)
    token_codec = JoseTokenCodec(_JWT_SECRET)
    server_ids = count(1)
    app = create_app(
        session_factory=sessions,
        authentication_runtime=AuthenticationRuntime(
            session_factory=sessions,
            secret_key=_JWT_SECRET,
            token_codec=token_codec,
        ),
        task_cancellation_connection_factory=legacy_connection_factory,
        chat_text_generation_configuration=ChatTextGenerationConfiguration(
            runtime=None,
            max_user_concurrency=0,
            server_id_factory=lambda: f"byok-review-{next(server_ids)}",
            clock=lambda: _G2_JOINT_NOW,
            frame_review_admission=FrameReviewAdmissionConfiguration(capabilities),
        ),
    )
    token = token_codec.encode(
        {
            "sub": _G2_JOINT_ACTOR_ID,
            "jti": _TOKEN_ID,
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        }
    )
    return engine, app, token


def _cancellation(sessions, now, capabilities, prefix: str):
    identifiers = iter((f"{prefix}-cancel", f"{prefix}-retry"))
    return SqlAlchemyFrameReviewCancellation(
        session_factory=sessions,
        db_clock=lambda: now[0],
        id_factory=lambda: next(identifiers),
        crypto=capabilities.crypto,
        proof_authority=capabilities.proof_authority,
    )


def test_cancellation_settles_owner_only_then_confirms_unknown_commit_ack(
    tmp_path, monkeypatch
) -> None:
    engine, app, token, capabilities, _crypto_state = _build_app(
        tmp_path, monkeypatch, cost_per_call=3, wallet_credits=500
    )
    sessions = sessionmaker(engine, expire_on_commit=False)
    now = [_G2_JOINT_NOW]
    cancellation = _cancellation(sessions, now, capabilities, "cancel-owner")
    try:
        admitted = asyncio.run(_post_review(app, token))
        assert admitted.status_code == 200, admitted.text
        task_id = admitted.json()["task_id"]
        with sessions() as session:
            owner_id = session.execute(
                select(AITaskRow.__table__.c.user_id).where(
                    AITaskRow.__table__.c.id == task_id
                )
            ).scalar_one()

        scope = OwnerTaskScope(owner_id, task_id, "frame.review")
        assert cancellation.consume_persisted(scope) == "not_selected"
        stopped = Event()
        stopped.set()
        assert cancellation.cancel_owned(owner_id, task_id, stop_signal=stopped) == "pending"
        assert cancellation.cancel_owned("foreign-owner", task_id) == "not_found"
        with sessions() as session:
            before_ack_task = session.execute(
                select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
            ).mappings().one()
            before_ack_fees = session.execute(
                select(UnitFeeDecisionRow.__table__)
            ).mappings().all()
        assert before_ack_task["status"] == "queued"
        assert before_ack_task["cancellation_reason"] is None
        assert before_ack_fees == []

        acknowledgement_lost = [False]

        def lose_first_commit_ack(_session):
            if not acknowledgement_lost[0]:
                acknowledgement_lost[0] = True
                raise RuntimeError("synthetic cancellation commit acknowledgement loss")

        event.listen(Session, "after_commit", lose_first_commit_ack)
        try:
            outcome = cancellation.cancel_owned(owner_id, task_id)
        finally:
            event.remove(Session, "after_commit", lose_first_commit_ack)

        assert acknowledgement_lost[0] is True
        assert outcome == "settled"
        with sessions() as session:
            task = session.execute(
                select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
            ).mappings().one()
            unit = session.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == task_id
                )
            ).mappings().one()
            fees = session.execute(select(UnitFeeDecisionRow.__table__)).mappings().all()
            ledgers = session.execute(select(CreditLogRow.__table__)).mappings().all()
            wallet = session.execute(
                select(UserCreditRow.__table__.c.credits).where(
                    UserCreditRow.__table__.c.user_id == owner_id
                )
            ).scalar_one()

        assert task["status"] == "failed"
        assert task["cancellation_reason"].startswith("user_cancelled:v1:")
        assert task["user_cancelled_at"] == unit["user_cancelled_at"]
        assert unit["cancellation_reason"] == task["cancellation_reason"]
        assert unit["billing_status"] == "unbilled"
        assert len(fees) == 1 and fees[0]["resolution"] == "waived_cancelled"
        assert ledgers == []
        assert wallet == 500
    finally:
        engine.dispose()


def test_cancellation_preserves_real_result_and_rejects_late_result_write(
    tmp_path, monkeypatch
) -> None:
    engine, sessions, now, claim, capabilities, verified, intent = _claimed_review(
        tmp_path, monkeypatch
    )
    result_ids = iter(
        ("cancel-result-ref", "cancel-evidence-id", "cancel-work-id")
    )
    results = SqlAlchemyFrameReviewResults(
        session_factory=sessions,
        db_clock=lambda: now[0],
        id_factory=lambda: next(result_ids),
        crypto=capabilities.crypto,
        proof_authority=capabilities.proof_authority,
    )
    output = TextResult("已持久化的完整分镜复核正文")
    try:
        receipt = results.persist_success(claim, intent, verified, output)
        assert receipt not in {"pending", "ownership_lost"}
        cancellation = _cancellation(sessions, now, capabilities, "cancel-result")

        outcome = cancellation.cancel_owned(
            claim.scope.user_id, claim.scope.task_id
        )
        assert outcome == "settled"

        late_write = results.persist_success(
            claim, intent, verified, TextResult("不得覆盖的迟到正文")
        )
        assert late_write == "ownership_lost"

        with sessions() as session:
            task = session.execute(
                select(AITaskRow.__table__).where(
                    AITaskRow.__table__.c.id == claim.scope.task_id
                )
            ).mappings().one()
            unit = session.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.id == verified.unit_id
                )
            ).mappings().one()
            receipts = session.execute(
                select(GenerationResultReceiptRow.__table__)
            ).mappings().all()
            evidence = session.execute(
                select(ResultEvidenceRow.__table__)
            ).mappings().all()
            work = session.execute(
                select(LocalContinuationWorkRow.__table__)
            ).mappings().one()
            fee = session.execute(
                select(UnitFeeDecisionRow.__table__)
            ).mappings().one()
            ledgers = session.execute(select(CreditLogRow.__table__)).mappings().all()
            wallet = session.execute(
                select(UserCreditRow.__table__.c.credits).where(
                    UserCreditRow.__table__.c.user_id == claim.scope.user_id
                )
            ).scalar_one()

        assert task["status"] == "failed"
        assert task["claim_token"] is None and task["claimed_by"] is None
        assert unit["result_status"] == "succeeded"
        assert unit["cancellation_reason"] == task["cancellation_reason"]
        assert len(receipts) == len(evidence) == 1
        assert receipts[0]["result_ref"] == receipt.result_ref
        assert decode_inline_text_result(
            receipts[0]["recoverable_locator"], receipts[0]["content_digest"]
        ) == output.text
        assert work["phase"] == "stopped_cancelled"
        assert work["phase_version"] == 1
        assert work["terminal_reason"] == "user_cancelled"
        assert fee["resolution"] == "waived_cancelled"
        assert fee["generation_result_ref"] == receipt.result_ref
        assert ledgers == []
        assert wallet == 500
    finally:
        engine.dispose()


def test_cancellation_stops_after_applied_result_without_refunding_charge(
    tmp_path, monkeypatch
) -> None:
    from generation_execution.test_frame_review_continuation_transactions import (
        _claim_next_phase,
        _make_fee_pending,
        _persist_result,
        _scenario,
    )
    from haoai_backend.generation_core.base_schema import UserCreditRow
    from haoai_backend.series_data.tables import chapters

    scenario = _scenario(tmp_path, monkeypatch, cost_per_call=3, wallet_credits=500)
    engine = scenario["engine"]
    sessions = scenario["sessions"]
    now = scenario["clock"]
    capabilities = scenario["capabilities"]
    try:
        _persist_result(scenario, output="已经应用的章节正文")
        _make_fee_pending(scenario)
        fee_claim = _claim_next_phase(scenario)
        assert fee_claim is not None
        assert scenario["consumer"].consume(fee_claim) == "fee_resolved"
        with sessions() as session:
            wallet_before_cancel = session.execute(
                select(UserCreditRow.__table__.c.credits).where(
                    UserCreditRow.__table__.c.user_id == fee_claim.owner_scope.user_id
                )
            ).scalar_one()
        assert wallet_before_cancel == 497

        cancellation_sessions: list[Session] = []

        def open_cancellation_session() -> Session:
            session = sessions()
            cancellation_sessions.append(session)
            return session

        cancellation = _cancellation(
            open_cancellation_session, now, capabilities, "cancel-charged-result"
        )
        acknowledgement_lost: list[Session] = []

        def lose_charged_cancel_commit_ack(session):
            if not acknowledgement_lost and session.get_bind() is engine:
                acknowledgement_lost.append(session)
                raise RuntimeError("synthetic charged cancellation commit ACK loss")

        event.listen(Session, "after_commit", lose_charged_cancel_commit_ack)
        try:
            cancellation_outcome = cancellation.cancel_owned(
                fee_claim.owner_scope.user_id, fee_claim.owner_scope.task_id
            )
        finally:
            event.remove(Session, "after_commit", lose_charged_cancel_commit_ack)
        assert len(acknowledgement_lost) == 1
        assert cancellation_outcome == "settled"
        assert len(cancellation_sessions) == 2
        assert cancellation_sessions[0] is not cancellation_sessions[1]
        assert acknowledgement_lost[0] is cancellation_sessions[0]

        with sessions() as session:
            task = session.execute(
                select(AITaskRow.__table__).where(
                    AITaskRow.__table__.c.id == fee_claim.owner_scope.task_id
                )
            ).mappings().one()
            unit = session.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == fee_claim.owner_scope.task_id
                )
            ).mappings().one()
            fee = session.execute(
                select(UnitFeeDecisionRow.__table__).where(
                    UnitFeeDecisionRow.__table__.c.unit_id == unit["id"]
                )
            ).mappings().one()
            receipt = session.execute(
                select(GenerationResultReceiptRow.__table__).where(
                    GenerationResultReceiptRow.__table__.c.task_id
                    == fee_claim.owner_scope.task_id
                )
            ).mappings().one()
            application = session.execute(
                select(ApplicationRecordRow.__table__).where(
                    ApplicationRecordRow.__table__.c.task_id
                    == fee_claim.owner_scope.task_id
                )
            ).mappings().one()
            work = session.execute(
                select(LocalContinuationWorkRow.__table__).where(
                    LocalContinuationWorkRow.__table__.c.task_id
                    == fee_claim.owner_scope.task_id
                )
            ).mappings().one()
            ledgers = session.execute(
                select(CreditLogRow.__table__).where(
                    CreditLogRow.__table__.c.billing_unit_id == unit["id"]
                )
            ).mappings().all()
            wallet_after_cancel = session.execute(
                select(UserCreditRow.__table__.c.credits).where(
                    UserCreditRow.__table__.c.user_id == fee_claim.owner_scope.user_id
                )
            ).scalar_one()
            chapter_content = session.execute(
                select(chapters.c.content).where(
                    chapters.c.id == "joint-chapter-1"
                )
            ).scalar_one()

        assert task["status"] == "failed"
        assert task["billing_status"] == "charged" and task["credit_cost"] == 3
        assert task["cancellation_reason"].startswith("user_cancelled:v1:")
        assert unit["billing_status"] == "charged"
        assert unit["debited_amount"] == 3 and unit["refunded_amount"] == 0
        assert fee["resolution"] == "charged" and fee["ledger_ref"] == ledgers[0]["id"]
        assert fee["generation_result_ref"] == receipt["result_ref"]
        assert fee["application_ref"] == application["application_ref"]
        assert application["result_ref"] == receipt["result_ref"]
        assert application["delivery_status"] == "applied"
        assert len(ledgers) == 1 and ledgers[0]["amount"] == -3
        assert ledgers[0]["task_id"] == task["id"]
        assert ledgers[0]["billing_unit_id"] == unit["id"]
        assert ledgers[0]["related_debit_id"] is None
        assert unit["cancellation_reason"] == task["cancellation_reason"]
        assert wallet_after_cancel == wallet_before_cancel == 497
        assert work["phase"] == "stopped_cancelled"
        assert work["phase_version"] == 3
        assert work["terminal_reason"] == "user_cancelled"
        assert json.loads(chapter_content)[0]["text"] == "已经应用的章节正文"

        retained_snapshot = {
            "task": dict(task),
            "unit": dict(unit),
            "fee": dict(fee),
            "receipt": dict(receipt),
            "application": dict(application),
            "work": dict(work),
            "ledgers": [dict(row) for row in ledgers],
            "wallet": wallet_after_cancel,
            "chapter_content": chapter_content,
        }
        assert cancellation.cancel_owned(
            fee_claim.owner_scope.user_id, fee_claim.owner_scope.task_id
        ) == "retained"
        assert len(cancellation_sessions) == 3
        with sessions() as session:
            replayed_task = session.execute(
                select(AITaskRow.__table__).where(
                    AITaskRow.__table__.c.id == fee_claim.owner_scope.task_id
                )
            ).mappings().one()
            replayed_unit = session.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id
                    == fee_claim.owner_scope.task_id
                )
            ).mappings().one()
            replayed_fee = session.execute(
                select(UnitFeeDecisionRow.__table__).where(
                    UnitFeeDecisionRow.__table__.c.unit_id == unit["id"]
                )
            ).mappings().one()
            replayed_receipt = session.execute(
                select(GenerationResultReceiptRow.__table__).where(
                    GenerationResultReceiptRow.__table__.c.task_id
                    == fee_claim.owner_scope.task_id
                )
            ).mappings().one()
            replayed_application = session.execute(
                select(ApplicationRecordRow.__table__).where(
                    ApplicationRecordRow.__table__.c.task_id
                    == fee_claim.owner_scope.task_id
                )
            ).mappings().one()
            replayed_work = session.execute(
                select(LocalContinuationWorkRow.__table__).where(
                    LocalContinuationWorkRow.__table__.c.task_id
                    == fee_claim.owner_scope.task_id
                )
            ).mappings().one()
            replayed_ledgers = session.execute(
                select(CreditLogRow.__table__).where(
                    CreditLogRow.__table__.c.billing_unit_id == unit["id"]
                )
            ).mappings().all()
            replayed_wallet = session.execute(
                select(UserCreditRow.__table__.c.credits).where(
                    UserCreditRow.__table__.c.user_id
                    == fee_claim.owner_scope.user_id
                )
            ).scalar_one()
            replayed_chapter = session.execute(
                select(chapters.c.content).where(
                    chapters.c.id == "joint-chapter-1"
                )
            ).scalar_one()
        assert dict(replayed_task) == retained_snapshot["task"]
        assert dict(replayed_unit) == retained_snapshot["unit"]
        assert dict(replayed_fee) == retained_snapshot["fee"]
        assert dict(replayed_receipt) == retained_snapshot["receipt"]
        assert dict(replayed_application) == retained_snapshot["application"]
        assert dict(replayed_work) == retained_snapshot["work"]
        assert [dict(row) for row in replayed_ledgers] == retained_snapshot["ledgers"]
        assert replayed_wallet == retained_snapshot["wallet"]
        assert replayed_chapter == retained_snapshot["chapter_content"]
    finally:
        engine.dispose()


def test_cancellation_after_application_before_fee_preserves_applied_result(
    tmp_path, monkeypatch
) -> None:
    from generation_execution.test_frame_review_continuation_transactions import (
        _claim_next_phase,
        _persist_result,
        _scenario,
    )
    from haoai_backend.series_data.tables import chapters

    scenario = _persist_result(_scenario(tmp_path, monkeypatch))
    sessions = scenario["sessions"]
    try:
        application_claim = _claim_next_phase(scenario)
        assert application_claim is not None
        assert scenario["consumer"].consume(application_claim) == "delivered"

        with sessions() as session:
            task_before = dict(
                session.execute(
                    select(AITaskRow.__table__).where(
                        AITaskRow.__table__.c.id
                        == application_claim.owner_scope.task_id
                    )
                ).mappings().one()
            )
            unit_before = dict(
                session.execute(
                    select(BillingUnitRow.__table__).where(
                        BillingUnitRow.__table__.c.id == application_claim.unit_id
                    )
                ).mappings().one()
            )
            application_before = dict(
                session.execute(
                    select(ApplicationRecordRow.__table__).where(
                        ApplicationRecordRow.__table__.c.task_id
                        == application_claim.owner_scope.task_id
                    )
                ).mappings().one()
            )
            receipt_before = dict(
                session.execute(
                    select(GenerationResultReceiptRow.__table__).where(
                        GenerationResultReceiptRow.__table__.c.task_id
                        == application_claim.owner_scope.task_id
                    )
                ).mappings().one()
            )
            work_before = dict(
                session.execute(
                    select(LocalContinuationWorkRow.__table__).where(
                        LocalContinuationWorkRow.__table__.c.task_id
                        == application_claim.owner_scope.task_id
                    )
                ).mappings().one()
            )
            chapter_before = session.execute(
                select(chapters.c.content).where(
                    chapters.c.id == "joint-chapter-1"
                )
            ).scalar_one()
            assert session.execute(
                select(UnitFeeDecisionRow.__table__).where(
                    UnitFeeDecisionRow.__table__.c.unit_id
                    == application_claim.unit_id
                )
            ).all() == []

        assert work_before["phase"] == "fee_pending"
        cancellation = _cancellation(
            sessions, scenario["clock"], scenario["capabilities"], "cancel-applied-before-fee"
        )
        assert cancellation.cancel_owned(
            application_claim.owner_scope.user_id,
            application_claim.owner_scope.task_id,
        ) == "settled"

        with sessions() as session:
            task = session.execute(
                select(AITaskRow.__table__).where(
                    AITaskRow.__table__.c.id == application_claim.owner_scope.task_id
                )
            ).mappings().one()
            unit = session.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.id == application_claim.unit_id
                )
            ).mappings().one()
            application = session.execute(
                select(ApplicationRecordRow.__table__).where(
                    ApplicationRecordRow.__table__.c.task_id
                    == application_claim.owner_scope.task_id
                )
            ).mappings().one()
            receipt = session.execute(
                select(GenerationResultReceiptRow.__table__).where(
                    GenerationResultReceiptRow.__table__.c.task_id
                    == application_claim.owner_scope.task_id
                )
            ).mappings().one()
            work = session.execute(
                select(LocalContinuationWorkRow.__table__).where(
                    LocalContinuationWorkRow.__table__.c.task_id
                    == application_claim.owner_scope.task_id
                )
            ).mappings().one()
            fee = session.execute(
                select(UnitFeeDecisionRow.__table__).where(
                    UnitFeeDecisionRow.__table__.c.unit_id
                    == application_claim.unit_id
                )
            ).mappings().one()
            ledgers = session.execute(
                select(CreditLogRow.__table__).where(
                    CreditLogRow.__table__.c.billing_unit_id
                    == application_claim.unit_id
                )
            ).mappings().all()
            wallet = session.execute(
                select(UserCreditRow.__table__.c.credits).where(
                    UserCreditRow.__table__.c.user_id
                    == application_claim.owner_scope.user_id
                )
            ).scalar_one()
            chapter_after = session.execute(
                select(chapters.c.content).where(
                    chapters.c.id == "joint-chapter-1"
                )
            ).scalar_one()

        assert task["status"] == "failed"
        assert task["cancellation_reason"].startswith("user_cancelled:v1:")
        assert unit["result_status"] == "succeeded"
        assert unit["billing_status"] == "unbilled"
        assert unit["debited_amount"] == unit["refunded_amount"] == 0
        assert dict(application) == application_before
        assert dict(receipt) == receipt_before
        assert work["phase"] == "stopped_cancelled"
        assert work["application_ref"] == application_before["application_ref"]
        assert work["phase_version"] == work_before["phase_version"] + 1
        assert fee["resolution"] == "waived_cancelled"
        assert fee["generation_result_ref"] == receipt_before["result_ref"]
        assert fee["application_ref"] == application_before["application_ref"]
        assert ledgers == []
        assert wallet == 500
        assert json.loads(chapter_after)[0]["text"] == "优化后的分镜正文"
        assert chapter_after == chapter_before
    finally:
        scenario["engine"].dispose()


def test_byok_cancellation_records_zero_charge_without_creating_wallet(
    tmp_path, monkeypatch
) -> None:
    engine, app, token = _build_byok_app(tmp_path, monkeypatch)
    try:
        request_body = _review_body()
        request_body.update(
            {
                "model_owner_id": "attacker-selected-owner",
                "owner_id": "attacker-selected-owner",
                "source": "system",
                "is_system": True,
            }
        )
        admitted = asyncio.run(_post_review(app, token, request_body))
        assert admitted.status_code == 200, admitted.text
        task_id = admitted.json()["task_id"]
        with engine.connect() as connection:
            task = connection.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == task_id)
            ).mappings().one()
            unit = connection.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == task_id
                )
            ).mappings().one()
            wallet_before = connection.execute(
                select(UserCreditRow.__table__)
            ).mappings().all()
        request = json.loads(task["request_data"])
        with engine.connect() as connection:
            prepared_payload = connection.execute(
                select(
                    prepared_payloads.c.source,
                    prepared_payloads.c.owner_or_system,
                ).where(
                    prepared_payloads.c.prepared_payload_id
                    == request["prepared_payload_id"]
                )
            ).mappings().one()
        assert request["model_source"] == "user"
        assert request["model_owner_id"] == task["user_id"]
        assert prepared_payload["source"] == "user"
        assert prepared_payload["owner_or_system"] == task["user_id"]
        assert unit["quoted_amount"] == 0
        assert wallet_before == []

        cancelled = asyncio.run(_post_cancel(app, token, task_id))
        assert cancelled.status_code == 200, cancelled.text

        with engine.connect() as connection:
            cancelled_task = connection.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == task_id)
            ).mappings().one()
            cancelled_unit = connection.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == task_id
                )
            ).mappings().one()
            fees = connection.execute(
                select(UnitFeeDecisionRow.__table__).where(
                    UnitFeeDecisionRow.__table__.c.unit_id == unit["id"]
                )
            ).mappings().all()
            ledgers = connection.execute(select(CreditLogRow.__table__)).mappings().all()
            wallet_after = connection.execute(
                select(UserCreditRow.__table__)
            ).mappings().all()

        assert cancelled_task["status"] == "failed"
        assert cancelled_task["billing_status"] == "no_charge"
        assert cancelled_task["credit_cost"] == 0
        assert cancelled_unit["billing_status"] == "no_charge"
        assert cancelled_unit["debited_amount"] == cancelled_unit["refunded_amount"] == 0
        assert len(fees) == 1 and fees[0]["resolution"] == "no_charge"
        assert fees[0]["ledger_ref"] is None
        assert ledgers == []
        assert wallet_after == []

        marker = cancelled_task["cancellation_reason"]
        replayed = asyncio.run(_post_cancel(app, token, task_id))
        assert replayed.status_code == 200, replayed.text
        with engine.connect() as connection:
            replayed_task = connection.execute(
                select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
            ).mappings().one()
            replayed_fees = connection.execute(
                select(UnitFeeDecisionRow.__table__).where(
                    UnitFeeDecisionRow.__table__.c.unit_id == unit["id"]
                )
            ).mappings().all()
            replayed_ledgers = connection.execute(
                select(CreditLogRow.__table__)
            ).mappings().all()
            replayed_wallets = connection.execute(
                select(UserCreditRow.__table__)
            ).mappings().all()
        assert replayed_task["cancellation_reason"] == marker
        assert replayed_fees == fees
        assert replayed_ledgers == []
        assert replayed_wallets == []
    finally:
        engine.dispose()


def test_foreign_user_model_id_is_rejected_without_admission_writes(
    tmp_path, monkeypatch
) -> None:
    engine, app, token = _build_byok_app(tmp_path, monkeypatch)
    foreign_owner_id = "frame-review-foreign-owner"
    stamp = _G2_JOINT_NOW.replace(tzinfo=None)
    try:
        with engine.begin() as connection:
            connection.execute(
                insert(users).values(
                    id=foreign_owner_id,
                    username="frame-review-foreign-owner",
                    email="frame-review-foreign-owner@example.test",
                    hashed_password="fixture-only-hash",
                    is_superuser=False,
                    membership_type="premium",
                    membership_expires_at=None,
                    created_at=stamp,
                )
            )
            connection.execute(
                update(model_configs)
                .where(model_configs.c.id == _G2_JOINT_MODEL_ID)
                .values(user_id=foreign_owner_id)
            )

        response = asyncio.run(_post_review(app, token))
        assert response.status_code == 422, response.text

        with engine.connect() as connection:
            tasks = connection.execute(select(AITaskRow.__table__)).mappings().all()
            submissions = connection.execute(
                select(TaskSubmissionRow.__table__)
            ).mappings().all()
            units = connection.execute(
                select(BillingUnitRow.__table__)
            ).mappings().all()
            payloads = connection.execute(
                select(prepared_payloads)
            ).mappings().all()
            fees = connection.execute(
                select(UnitFeeDecisionRow.__table__)
            ).mappings().all()
            ledgers = connection.execute(
                select(CreditLogRow.__table__)
            ).mappings().all()
            wallets = connection.execute(
                select(UserCreditRow.__table__)
            ).mappings().all()

        assert tasks == []
        assert submissions == []
        assert units == []
        assert payloads == []
        assert fees == []
        assert ledgers == []
        assert wallets == []
    finally:
        engine.dispose()


def test_registered_corrupt_graph_fails_closed_without_legacy_fallback(
    tmp_path, monkeypatch
) -> None:
    legacy_factory_calls: list[str] = []

    @contextmanager
    def legacy_connection_factory():
        legacy_factory_calls.append("opened")
        raise AssertionError("registered review cancellation reached legacy writer")
        yield None

    engine, app, token = _build_byok_app(
        tmp_path,
        monkeypatch,
        legacy_connection_factory=legacy_connection_factory,
    )
    try:
        admitted = asyncio.run(_post_review(app, token))
        assert admitted.status_code == 200, admitted.text
        task_id = admitted.json()["task_id"]
        with engine.begin() as connection:
            task_row = connection.execute(
                select(AITaskRow.__table__.c.request_data).where(
                    AITaskRow.__table__.c.id == task_id
                )
            ).scalar_one()
            request_data = json.loads(task_row)
            request_data["model_owner_id"] = "forged-other-owner"
            connection.execute(
                update(AITaskRow.__table__)
                .where(AITaskRow.__table__.c.id == task_id)
                .values(request_data=json.dumps(request_data))
            )

        cancelled = asyncio.run(_post_cancel(app, token, task_id))
        assert cancelled.status_code == 503, cancelled.text
        assert legacy_factory_calls == []

        with engine.connect() as connection:
            task = connection.execute(
                select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == task_id)
            ).mappings().one()
            fees = connection.execute(
                select(UnitFeeDecisionRow.__table__)
            ).mappings().all()
        assert task["status"] == "queued"
        assert task["cancellation_reason"] is None
        assert task["user_cancelled_at"] is None
        assert fees == []
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("table", "column", "row_id"),
    (
        (GenerationResultReceiptRow.__table__, "validated_at", "receipt"),
        (ExternalSubmissionRow.__table__, "submitted_at", "external"),
        (ResultEvidenceRow.__table__, "received_at", "evidence"),
    ),
)
def test_cancellation_rejects_a_single_result_timestamp_mismatch(
    tmp_path, monkeypatch, table, column, row_id
) -> None:
    engine, sessions, now, claim, capabilities, verified, intent = _claimed_review(
        tmp_path, monkeypatch
    )
    result_ids = iter(("cancel-time-result", "cancel-time-evidence", "cancel-time-work"))
    results = SqlAlchemyFrameReviewResults(
        session_factory=sessions,
        db_clock=lambda: now[0],
        id_factory=lambda: next(result_ids),
        crypto=capabilities.crypto,
        proof_authority=capabilities.proof_authority,
    )
    try:
        receipt = results.persist_success(
            claim, intent, verified, TextResult("真实生产链结果时间")
        )
        assert receipt not in {"pending", "ownership_lost"}
        target_id = {
            "receipt": receipt.result_ref,
            "external": intent.external_submission_id,
            "evidence": receipt.evidence_id,
        }[row_id]
        target_column = "result_ref" if row_id == "receipt" else "id"
        with sessions() as session:
            with session.begin():
                session.execute(
                    update(table)
                    .where(table.c[target_column] == target_id)
                    .values({column: now[0] + timedelta(seconds=1)})
                )

        cancellation = _cancellation(sessions, now, capabilities, "cancel-time-mismatch")
        assert cancellation.cancel_owned(
            claim.scope.user_id, claim.scope.task_id
        ) == "pending"

        with sessions() as session:
            task = session.execute(
                select(AITaskRow.__table__).where(
                    AITaskRow.__table__.c.id == claim.scope.task_id
                )
            ).mappings().one()
            fees = session.execute(select(UnitFeeDecisionRow.__table__)).mappings().all()
            work = session.execute(
                select(LocalContinuationWorkRow.__table__)
            ).mappings().one()
        assert task["cancellation_reason"] is None
        assert fees == []
        assert work["phase"] == "delivery_pending"
        assert work["phase_version"] == 0
    finally:
        engine.dispose()


def test_cancellation_rejects_timely_flags_with_fulfillment_after_deadline(
    tmp_path, monkeypatch
) -> None:
    engine, sessions, now, claim, capabilities, verified, intent = _claimed_review(
        tmp_path, monkeypatch, lease_seconds=8000
    )
    result_ids = iter(("cancel-late-result", "cancel-late-evidence", "cancel-late-work"))
    results = SqlAlchemyFrameReviewResults(
        session_factory=sessions,
        db_clock=lambda: now[0],
        id_factory=lambda: next(result_ids),
        crypto=capabilities.crypto,
        proof_authority=capabilities.proof_authority,
    )
    try:
        receipt = results.persist_success(
            claim, intent, verified, TextResult("真实及时结果后被伪改为超时")
        )
        assert receipt not in {"pending", "ownership_lost"}
        late_at = intent.deadline_at + timedelta(seconds=1)
        with sessions() as session:
            with session.begin():
                session.execute(
                    update(GenerationResultReceiptRow.__table__)
                    .where(
                        GenerationResultReceiptRow.__table__.c.result_ref
                        == receipt.result_ref
                    )
                    .values(validated_at=late_at, generation_fulfilled_at=late_at)
                )
                session.execute(
                    update(BillingUnitRow.__table__)
                    .where(BillingUnitRow.__table__.c.id == verified.unit_id)
                    .values(success_confirmed_at=late_at)
                )
                session.execute(
                    update(ResultEvidenceRow.__table__)
                    .where(ResultEvidenceRow.__table__.c.id == receipt.evidence_id)
                    .values(received_at=late_at)
                )
                session.execute(
                    update(ExternalSubmissionRow.__table__)
                    .where(ExternalSubmissionRow.__table__.c.id == intent.external_submission_id)
                    .values(submitted_at=late_at)
                )
                session.execute(
                    update(LocalContinuationWorkRow.__table__)
                    .where(
                        LocalContinuationWorkRow.__table__.c.work_id
                        == "cancel-late-work"
                    )
                    .values(generation_fulfilled_at=late_at)
                )

        cancellation = _cancellation(sessions, now, capabilities, "cancel-late-mismatch")
        assert cancellation.cancel_owned(
            claim.scope.user_id, claim.scope.task_id
        ) == "pending"

        with sessions() as session:
            task = session.execute(
                select(AITaskRow.__table__).where(
                    AITaskRow.__table__.c.id == claim.scope.task_id
                )
            ).mappings().one()
            fee_rows = session.execute(select(UnitFeeDecisionRow.__table__)).mappings().all()
            receipt_row = session.execute(
                select(GenerationResultReceiptRow.__table__).where(
                        GenerationResultReceiptRow.__table__.c.result_ref
                        == receipt.result_ref
                )
            ).mappings().one()
        assert task["cancellation_reason"] is None
        assert fee_rows == []
        assert receipt_row["automatic_application_allowed"] is True
        assert receipt_row["late_disposition"] is None
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("cost_per_call", "expected_resolution", "expected_billing_status"),
    ((3, "waived_cancelled", "unbilled"), (0, "no_charge", "no_charge")),
)
def test_cancellation_confirms_and_replays_real_late_generation_after_commit_ack_loss(
    tmp_path, monkeypatch, cost_per_call, expected_resolution, expected_billing_status
) -> None:
    from generation_execution.test_frame_review_continuation_transactions import (
        _persist_result,
        _scenario,
    )

    scenario = _scenario(
        tmp_path,
        monkeypatch,
        cost_per_call=cost_per_call,
        wallet_credits=500,
    )
    scenario["generation_claims"].lease_seconds = 8000
    persist_success = SqlAlchemyFrameReviewResults.persist_success

    def persist_after_original_deadline(self, claim, intent, verified, output):
        previous_time = scenario["clock"][0]
        scenario["clock"][0] = intent.deadline_at + timedelta(seconds=1)
        try:
            return persist_success(self, claim, intent, verified, output)
        finally:
            scenario["clock"][0] = previous_time

    with monkeypatch.context() as local_patch:
        local_patch.setattr(
            SqlAlchemyFrameReviewResults,
            "persist_success",
            persist_after_original_deadline,
        )
        scenario = _persist_result(scenario, output="真实迟到结果正文")

    engine = scenario["engine"]
    sessions = scenario["sessions"]
    capabilities = scenario["capabilities"]
    now = scenario["clock"]
    now[0] = scenario["intent"].deadline_at + timedelta(seconds=1)
    try:
        assert scenario["receipt"].automatic_application_allowed is False
        assert scenario["receipt"].late_disposition == "stopped_late_generation"
        opened_sessions: list[Session] = []

        def open_session() -> Session:
            session = sessions()
            opened_sessions.append(session)
            return session

        cancellation = _cancellation(
            open_session, now, capabilities, "cancel-real-late-terminal"
        )
        acknowledgement_lost: list[Session] = []

        def lose_commit_ack(session):
            if not acknowledgement_lost and session.get_bind() is engine:
                acknowledgement_lost.append(session)
                raise RuntimeError("synthetic late cancellation commit ACK loss")

        event.listen(Session, "after_commit", lose_commit_ack)
        try:
            outcome = cancellation.cancel_owned(
                scenario["generation_claim"].scope.user_id,
                scenario["generation_claim"].scope.task_id,
            )
        finally:
            event.remove(Session, "after_commit", lose_commit_ack)

        assert len(acknowledgement_lost) == 1
        assert outcome == "settled"
        assert len(opened_sessions) == 2
        assert opened_sessions[0] is not opened_sessions[1]
        assert acknowledgement_lost[0] is opened_sessions[0]

        def snapshot():
            with sessions() as session:
                task = session.execute(
                    select(AITaskRow.__table__).where(
                        AITaskRow.__table__.c.id
                        == scenario["generation_claim"].scope.task_id
                    )
                ).mappings().one()
                unit = session.execute(
                    select(BillingUnitRow.__table__).where(
                        BillingUnitRow.__table__.c.id
                        == scenario["verified"].unit_id
                    )
                ).mappings().one()
                step = session.execute(
                    select(ExecutionStepRow.__table__).where(
                        ExecutionStepRow.__table__.c.billing_unit_id == unit["id"]
                    )
                ).mappings().one()
                external = session.execute(
                    select(ExternalSubmissionRow.__table__).where(
                        ExternalSubmissionRow.__table__.c.step_id == step["id"]
                    )
                ).mappings().one()
                receipt = session.execute(
                    select(GenerationResultReceiptRow.__table__).where(
                        GenerationResultReceiptRow.__table__.c.result_ref
                        == scenario["receipt"].result_ref
                    )
                ).mappings().one()
                evidence = session.execute(
                    select(ResultEvidenceRow.__table__).where(
                        ResultEvidenceRow.__table__.c.id == receipt["evidence_id"]
                    )
                ).mappings().one()
                work = session.execute(
                    select(LocalContinuationWorkRow.__table__).where(
                        LocalContinuationWorkRow.__table__.c.unit_id == unit["id"]
                    )
                ).mappings().one()
                applications = session.execute(
                    select(ApplicationRecordRow.__table__).where(
                        ApplicationRecordRow.__table__.c.unit_id == unit["id"]
                    )
                ).mappings().all()
                fees = session.execute(
                    select(UnitFeeDecisionRow.__table__).where(
                        UnitFeeDecisionRow.__table__.c.unit_id == unit["id"]
                    )
                ).mappings().all()
                ledgers = session.execute(
                    select(CreditLogRow.__table__).where(
                        CreditLogRow.__table__.c.billing_unit_id == unit["id"]
                    )
                ).mappings().all()
                wallet = session.execute(
                    select(UserCreditRow.__table__.c.credits).where(
                        UserCreditRow.__table__.c.user_id == task["user_id"]
                    )
                ).scalar_one()
            return {
                "task": dict(task),
                "unit": dict(unit),
                "step": dict(step),
                "external": dict(external),
                "receipt": dict(receipt),
                "evidence": dict(evidence),
                "work": dict(work),
                "applications": [dict(row) for row in applications],
                "fees": [dict(row) for row in fees],
                "ledgers": [dict(row) for row in ledgers],
                "wallet": wallet,
            }

        settled = snapshot()
        task, unit = settled["task"], settled["unit"]
        receipt, evidence, work = settled["receipt"], settled["evidence"], settled["work"]
        fees = settled["fees"]
        applications, ledgers = settled["applications"], settled["ledgers"]

        assert task["status"] == "failed"
        assert task["cancellation_reason"].startswith("user_cancelled:v1:")
        assert receipt["result_ref"] == scenario["receipt"].result_ref
        assert receipt["automatic_application_allowed"] is False
        assert receipt["late_disposition"] == "stopped_late_generation"
        assert evidence["id"] == receipt["evidence_id"]
        assert work["phase"] == "stopped_late_generation"
        assert work["phase_version"] == 1
        assert work["fee_decision_ref"] is None
        assert work["terminal_reason"] == "original_generation_deadline_elapsed"
        assert len(fees) == 1 and fees[0]["resolution"] == expected_resolution
        assert unit["billing_status"] == expected_billing_status
        assert fees[0]["unit_id"] == unit["id"]
        assert fees[0]["generation_result_ref"] == scenario["receipt"].result_ref
        assert fees[0]["application_ref"] is None and fees[0]["ledger_ref"] is None
        assert applications == [] and ledgers == []
        assert settled["wallet"] == 500

        assert cancellation.cancel_owned(
            scenario["generation_claim"].scope.user_id,
            scenario["generation_claim"].scope.task_id,
        ) == "retained"
        replayed = snapshot()
        assert replayed == settled
    finally:
        engine.dispose()


def test_cancellation_retains_real_delivery_failure_terminal_graph(
    tmp_path, monkeypatch
) -> None:
    from generation_execution.test_frame_review_continuation_transactions import (
        _claim_next_phase,
        _persist_result,
        _scenario,
    )
    from haoai_backend.series_data.tables import chapters

    scenario = _persist_result(
        _scenario(tmp_path, monkeypatch, cost_per_call=3, wallet_credits=500),
        output="真实交付失败前的原始结果",
    )
    engine = scenario["engine"]
    sessions = scenario["sessions"]
    capabilities = scenario["capabilities"]
    now = scenario["clock"]
    try:
        with engine.begin() as connection:
            chapter = connection.execute(
                select(chapters).where(chapters.c.id == "joint-chapter-1")
            ).mappings().one()
            frames = json.loads(chapter["content"])
            frames[0]["text"] = "并发修改导致真实目标写入冲突"
            connection.execute(
                update(chapters)
                .where(chapters.c.id == "joint-chapter-1")
                .values(content=json.dumps(frames, ensure_ascii=False))
            )

        continuation_claim = _claim_next_phase(scenario)
        assert continuation_claim is not None
        assert scenario["consumer"].consume(continuation_claim) == "finalized"
        assert scenario["continuation_claims"].release_continuation(
            continuation_claim
        )
        cancellation = _cancellation(
            sessions, now, capabilities, "cancel-real-delivery-failure"
        )
        outcome = cancellation.cancel_owned(
            continuation_claim.owner_scope.user_id,
            continuation_claim.owner_scope.task_id,
        )
        if outcome != "retained":
            with sessions() as session:
                _status, graph = cancellation._lock_graph(
                    session,
                    continuation_claim.owner_scope.user_id,
                    continuation_claim.owner_scope.task_id,
                )
                diagnostic = None if graph is None else {
                    "failed_projection": cancellation._failed_projection_matches(graph),
                        "base": cancellation._base_binding_matches(graph),
                    "external": cancellation._external_matches(graph),
                    "fee": cancellation._fee_graph_matches(graph),
                        "task_billing": (
                            graph.task.get("credit_cost"),
                            graph.task.get("billing_status"),
                            graph.task.get("claimed_by"),
                            graph.task.get("claim_token"),
                            graph.task.get("lease_until"),
                        ),
                        "unit_billing": (
                            graph.unit.get("billing_status"),
                            graph.unit.get("debited_amount"),
                            graph.unit.get("refunded_amount"),
                        ),
                        "graph_counts": (
                            len(graph.receipts),
                            len(graph.works),
                            len(graph.applications),
                            graph.fee is not None,
                            len(graph.ledgers),
                        ),
                    "task_status": graph.task.get("status"),
                    "work_phase": graph.works[0].get("phase") if graph.works else None,
                    "result": (
                        cancellation._result_graph_matches(
                            graph,
                            graph.receipts[0],
                            graph.works[0],
                            graph.applications[0] if graph.applications else None,
                        )
                        if graph.receipts and graph.works
                        else None
                    ),
                }
            raise AssertionError(f"valid delivery failure was not retained: {outcome}, {diagnostic}")

        with sessions() as session:
            task = session.execute(
                select(AITaskRow.__table__).where(
                    AITaskRow.__table__.c.id
                    == continuation_claim.owner_scope.task_id
                )
            ).mappings().one()
            receipt = session.execute(
                select(GenerationResultReceiptRow.__table__).where(
                    GenerationResultReceiptRow.__table__.c.result_ref
                    == scenario["receipt"].result_ref
                )
            ).mappings().one()
            evidence = session.execute(
                select(ResultEvidenceRow.__table__)
            ).mappings().one()
            work = session.execute(
                select(LocalContinuationWorkRow.__table__).where(
                    LocalContinuationWorkRow.__table__.c.work_id
                    == continuation_claim.work_id
                )
            ).mappings().one()
            fees = session.execute(select(UnitFeeDecisionRow.__table__)).mappings().all()
            ledgers = session.execute(select(CreditLogRow.__table__)).mappings().all()

        assert task["status"] == "failed"
        assert task["cancellation_reason"] is None
        assert receipt["result_ref"] == scenario["receipt"].result_ref
        assert receipt["automatic_application_allowed"] is True
        assert evidence["application_status"] == "pending"
        assert work["phase"] == "stopped_delivery_failed"
        assert work["phase_version"] == 1
        assert work["terminal_reason"] == "frame_review_target_delivery_failed"
        assert fees == [] and ledgers == []
    finally:
        engine.dispose()


def test_cancellation_stop_after_task_write_rolls_back_without_confirmation_session(
    tmp_path, monkeypatch
) -> None:
    engine, sessions, now, claim, capabilities, verified, _intent = _claimed_review(
        tmp_path, monkeypatch
    )
    factory_calls: list[None] = []

    def tracked_session_factory():
        factory_calls.append(None)
        return sessions()

    cancellation = SqlAlchemyFrameReviewCancellation(
        session_factory=tracked_session_factory,
        db_clock=lambda: now[0],
        id_factory=iter(("stop-after-task-cancel", "must-not-confirm")).__next__,
        crypto=capabilities.crypto,
        proof_authority=capabilities.proof_authority,
    )
    stopped = Event()

    def stop_after_task_update(_connection, _cursor, statement, _params, _context, _many):
        if statement.lstrip().lower().startswith("update ai_tasks"):
            stopped.set()

    event.listen(engine, "after_cursor_execute", stop_after_task_update)
    try:
        assert cancellation.cancel_owned(
            claim.scope.user_id, claim.scope.task_id, stop_signal=stopped
        ) == "pending"
        assert stopped.is_set()
        assert len(factory_calls) == 1

        with sessions() as session:
            task = session.execute(
                select(AITaskRow.__table__).where(
                    AITaskRow.__table__.c.id == claim.scope.task_id
                )
            ).mappings().one()
            unit = session.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.id == verified.unit_id
                )
            ).mappings().one()
            fees = session.execute(select(UnitFeeDecisionRow.__table__)).mappings().all()
            work = session.execute(
                select(LocalContinuationWorkRow.__table__)
            ).mappings().all()
        assert task["cancellation_reason"] is None
        assert task["user_cancelled_at"] is None
        assert unit["cancellation_reason"] is None
        assert unit["user_cancelled_at"] is None
        assert fees == [] and work == []
    finally:
        event.remove(engine, "after_cursor_execute", stop_after_task_update)
        engine.dispose()


def test_cancellation_checks_stop_after_each_returned_lock_query(
    tmp_path, monkeypatch
) -> None:
    engine, sessions, now, claim, capabilities, _verified, _intent = _claimed_review(
        tmp_path, monkeypatch
    )
    cancellation = _cancellation(sessions, now, capabilities, "stop-between-locks")
    stopped = Event()
    lock_queries: list[str] = []

    def stop_after_user_query(_connection, _cursor, statement, _params, _context, _many):
        normalized = " ".join(statement.lower().split())
        if normalized.startswith("select") and " from users" in normalized:
            lock_queries.append(normalized)
            stopped.set()
        elif normalized.startswith("select"):
            lock_queries.append(normalized)

    event.listen(engine, "after_cursor_execute", stop_after_user_query)
    try:
        assert cancellation.cancel_owned(
            claim.scope.user_id, claim.scope.task_id, stop_signal=stopped
        ) == "pending"
        assert stopped.is_set()
        assert len(lock_queries) == 1
        assert "from users" in lock_queries[0]
        with sessions() as session:
            task = session.execute(
                select(AITaskRow.__table__).where(
                    AITaskRow.__table__.c.id == claim.scope.task_id
                )
            ).mappings().one()
        assert task["cancellation_reason"] is None
    finally:
        event.remove(engine, "after_cursor_execute", stop_after_user_query)
        engine.dispose()


@pytest.mark.parametrize(
    "failure",
    ("fee_flush", "unit_cas", "task_cas", "work_cas", "commit"),
)
def test_cancellation_database_rejections_roll_back_the_entire_transaction(
    tmp_path, monkeypatch, failure
) -> None:
    if failure == "work_cas":
        from generation_execution.test_frame_review_continuation_transactions import (
            _persist_result,
            _scenario,
        )

        scenario = _persist_result(_scenario(tmp_path, monkeypatch))
        engine = scenario["engine"]
        sessions = scenario["sessions"]
        now = scenario["clock"]
        capabilities = scenario["capabilities"]
        task_id = scenario["generation_claim"].scope.task_id
        unit_id = scenario["verified"].unit_id
        owner_id = scenario["generation_claim"].scope.user_id
    else:
        engine, sessions, now, claim, capabilities, verified, _intent = _claimed_review(
            tmp_path, monkeypatch
        )
        task_id = claim.scope.task_id
        unit_id = verified.unit_id
        owner_id = claim.scope.user_id
        scenario = None

    cancellation = _cancellation(sessions, now, capabilities, f"reject-{failure}")
    trigger_spec = {
        "fee_flush": ("INSERT", "unit_fee_decisions", "1"),
        "unit_cas": (
            "UPDATE",
            "billing_units",
            "NEW.cancellation_reason IS NOT NULL AND OLD.cancellation_reason IS NULL",
        ),
        "task_cas": (
            "UPDATE",
            "ai_tasks",
            "NEW.cancellation_reason IS NOT NULL AND OLD.cancellation_reason IS NULL",
        ),
        "work_cas": (
            "UPDATE",
            "local_continuation_work",
            "NEW.phase = 'stopped_cancelled'",
        ),
    }
    if failure != "commit":
        operation, table_name, condition = trigger_spec[failure]
        with engine.begin() as connection:
            connection.exec_driver_sql(
                f"CREATE TRIGGER reject_cancel_{failure} "
                f"BEFORE {operation} ON {table_name} "
                f"WHEN {condition} BEGIN "
                "SELECT RAISE(ABORT, 'synthetic cancellation write rejection'); END"
            )

    def read_graph():
        with sessions() as session:
            task = dict(
                session.execute(
                    select(AITaskRow.__table__).where(AITaskRow.id == task_id)
                ).mappings().one()
            )
            unit = dict(
                session.execute(
                    select(BillingUnitRow.__table__).where(
                        BillingUnitRow.__table__.c.id == unit_id
                    )
                ).mappings().one()
            )
            steps = [
                dict(row)
                for row in session.execute(
                    select(ExecutionStepRow.__table__).where(
                        ExecutionStepRow.__table__.c.billing_unit_id == unit_id
                    )
                ).mappings().all()
            ]
            step_ids = tuple(row["id"] for row in steps)
            externals = [
                dict(row)
                for row in session.execute(
                    select(ExternalSubmissionRow.__table__).where(
                        ExternalSubmissionRow.__table__.c.step_id.in_(step_ids)
                    )
                ).mappings().all()
            ]
            evidences = [
                dict(row)
                for row in session.execute(
                    select(ResultEvidenceRow.__table__).where(
                        ResultEvidenceRow.__table__.c.step_id.in_(step_ids)
                    )
                ).mappings().all()
            ]
            receipts = [
                dict(row)
                for row in session.execute(
                    select(GenerationResultReceiptRow.__table__).where(
                        GenerationResultReceiptRow.__table__.c.task_id == task_id
                    )
                ).mappings().all()
            ]
            works = [
                dict(row)
                for row in session.execute(
                    select(LocalContinuationWorkRow.__table__).where(
                        LocalContinuationWorkRow.__table__.c.task_id == task_id
                    )
                ).mappings().all()
            ]
            applications = [
                dict(row)
                for row in session.execute(
                    select(ApplicationRecordRow.__table__).where(
                        ApplicationRecordRow.__table__.c.task_id == task_id
                    )
                ).mappings().all()
            ]
            fees = [
                dict(row)
                for row in session.execute(
                    select(UnitFeeDecisionRow.__table__).where(
                        UnitFeeDecisionRow.__table__.c.unit_id == unit_id
                    )
                ).mappings().all()
            ]
            ledgers = [
                dict(row)
                for row in session.execute(
                    select(CreditLogRow.__table__).where(
                        CreditLogRow.__table__.c.billing_unit_id == unit_id
                    )
                ).mappings().all()
            ]
            wallets = [
                dict(row)
                for row in session.execute(
                    select(UserCreditRow.__table__).where(
                        UserCreditRow.__table__.c.user_id == owner_id
                    )
                ).mappings().all()
            ]
        return {
            "task": task,
            "unit": unit,
            "steps": steps,
            "externals": externals,
            "evidences": evidences,
            "receipts": receipts,
            "works": works,
            "applications": applications,
            "fees": fees,
            "ledgers": ledgers,
            "wallets": wallets,
        }

    before = read_graph()
    rejected_commit = [False]

    def reject_before_commit(session):
        if not rejected_commit[0] and session.get_bind() is engine:
            rejected_commit[0] = True
            raise RuntimeError("synthetic cancellation commit rejection")

    if failure == "commit":
        event.listen(Session, "before_commit", reject_before_commit)
    try:
        outcome = cancellation.cancel_owned(owner_id, task_id)
    finally:
        if failure == "commit":
            event.remove(Session, "before_commit", reject_before_commit)

    try:
        if failure == "commit":
            assert rejected_commit == [True]
        assert outcome == "pending"
        assert read_graph() == before
    finally:
        engine.dispose()
