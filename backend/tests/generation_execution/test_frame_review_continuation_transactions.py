"""Transactional continuation, runtime fencing, and source preservation tests."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from itertools import count
from threading import Event, Thread
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select, text, update
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.dml import Update

from haoai_backend.generation_contracts.ports import GenerationRuntimeConfig
from haoai_backend.generation_contracts.types import (
    ContinuationClaimAuthority,
    OwnerTaskScope,
    TextResult,
    ValidatedGenerationResult,
)
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    UserCreditRow,
)
from haoai_backend.generation_core.frame_review_claims import (
    SqlAlchemyFrameReviewClaims,
)
from haoai_backend.generation_core.frame_review_continuation import (
    SqlAlchemyFrameReviewContinuationConsumer,
)
from haoai_backend.generation_core.frame_review_continuation_claims import (
    SqlAlchemyFrameReviewContinuationClaims,
)
from haoai_backend.generation_core.frame_review_intents import (
    SqlAlchemyFrameReviewIntents,
)
from haoai_backend.generation_core.frame_review_results import (
    SqlAlchemyFrameReviewResults,
)
from haoai_backend.generation_core.persistence import GenerationRecordRepository
from haoai_backend.generation_core.schema import (
    ApplicationRecordRow,
    GenerationResultReceiptRow,
    LocalContinuationWorkRow,
    UnitFeeDecisionRow,
)
from haoai_backend.generation_runtime.configuration import RegisteredExecutorFactory
from haoai_backend.generation_runtime.runtime import build_runtime
from haoai_backend.personal_production.notes.tables import metadata as notes_metadata
from haoai_backend.series_data.tables import chapters
import haoai_backend.series_data.generation as series_generation
import generation_core.test_admission_staging as admission_fixtures
import generation_execution.test_frame_review_admission as admission


class _Ids:
    def __init__(self, prefix: str) -> None:
        self._prefix = prefix
        self._counter = count(1)

    def __call__(self) -> str:
        return f"{self._prefix}-{next(self._counter)}"


def _scenario(
    tmp_path,
    monkeypatch,
    *,
    cost_per_call: int = 3,
    wallet_credits: int | None = 500,
    storyboard_asset_ids: tuple[str, ...] = (
        "review-storyboard-1",
        "review-storyboard-2",
    ),
    chapter_content: list[object] | None = None,
):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(admission, "_G2_JOINT_NOW", now)
    monkeypatch.setattr(admission_fixtures, "_G2_JOINT_NOW", now)
    engine, app, token, capabilities, _ = admission._build_app(
        tmp_path,
        monkeypatch,
        cost_per_call=cost_per_call,
        wallet_credits=wallet_credits,
        storyboard_asset_ids=storyboard_asset_ids,
        chapter_content=chapter_content,
    )
    sessions = sessionmaker(engine, expire_on_commit=False)
    clock = [now]
    ids = _Ids("frame-review-329")
    generation_claims = SqlAlchemyFrameReviewClaims(
        session_factory=sessions,
        db_clock=lambda: clock[0],
        id_factory=ids,
    )
    return {
        "engine": engine,
        "app": app,
        "token": token,
        "capabilities": capabilities,
        "sessions": sessions,
        "clock": clock,
        "ids": ids,
        "generation_claims": generation_claims,
        "generation_claim": None,
        "verified": None,
        "intent": None,
        "receipt": None,
        "continuation_claims": None,
        "consumer": None,
        "p5_schema_ready": False,
    }


def _persist_result(scenario, *, output: str = "优化后的分镜正文", body=None):
    engine = scenario["engine"]
    sessions = scenario["sessions"]
    capabilities = scenario["capabilities"]
    clock = scenario["clock"]
    ids = scenario["ids"]
    response = asyncio.run(
        admission._post_review(scenario["app"], scenario["token"], body)
    )
    assert response.status_code == 200, response.text
    claim = scenario["generation_claims"].claim_next(
        "ai-review", "frame-review-329-generation"
    )
    assert claim is not None
    prepared = SqlAlchemyFrameReviewIntents(
        session_factory=sessions,
        db_clock=lambda: clock[0],
        id_factory=ids,
        crypto=capabilities.crypto,
        proof_authority=capabilities.proof_authority,
    ).prepare(claim)
    assert prepared.disposition == "committed"
    assert prepared.verified_input is not None
    assert prepared.intent is not None
    verified = prepared.verified_input
    results = SqlAlchemyFrameReviewResults(
        session_factory=sessions,
        db_clock=lambda: clock[0],
        id_factory=ids,
        crypto=capabilities.crypto,
        proof_authority=capabilities.proof_authority,
    )
    receipt = results.persist_success(
        claim, prepared.intent, verified, TextResult(output)
    )
    assert receipt not in {"pending", "ownership_lost"}
    assert scenario["generation_claims"].release_claim(claim)
    scenario["generation_claim"] = claim
    scenario["verified"] = verified
    scenario["intent"] = prepared.intent
    scenario["receipt"] = receipt
    if not scenario["p5_schema_ready"]:
        _prepare_p5_schema(engine)
        scenario["p5_schema_ready"] = True
    continuation_claims = SqlAlchemyFrameReviewContinuationClaims(
        session_factory=sessions,
        db_clock=lambda: clock[0],
        id_factory=ids,
    )
    consumer = SqlAlchemyFrameReviewContinuationConsumer(
        session_factory=sessions,
        db_clock=lambda: clock[0],
        id_factory=ids,
        crypto=capabilities.crypto,
        proof_authority=capabilities.proof_authority,
        claims=continuation_claims,
    )
    scenario["continuation_claims"] = continuation_claims
    scenario["consumer"] = consumer
    return scenario


def _prepare_p5_schema(engine) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE storyboard_assets ADD COLUMN created_at DATETIME"
        )
    notes_metadata.create_all(engine)


def _claim_next_phase(scenario):
    return scenario["continuation_claims"].claim_continuation(
        "ai-review", "frame-review-329-continuation", lease_seconds=60
    )


def _make_fee_pending(scenario):
    delivery_claim = _claim_next_phase(scenario)
    assert delivery_claim is not None
    assert scenario["consumer"].consume(delivery_claim) == "delivered"
    with scenario["sessions"]() as session:
        with session.begin():
            session.execute(
                update(AITaskRow.__table__)
                .where(AITaskRow.__table__.c.id == delivery_claim.owner_scope.task_id)
                .values(status="queued", claimed_by=None, claim_token=None, lease_until=None)
            )


def _inject_fee_failure(monkeypatch, failure: str) -> None:
    if failure in {"unit_cas", "task_cas", "zero_unit_cas"}:
        original_execute = Session.execute
        target_table = (
            BillingUnitRow.__table__.name
            if failure != "task_cas"
            else AITaskRow.__table__.name
        )

        def fail_one_update(session, statement, *args, **kwargs):
            if (
                isinstance(statement, Update)
                and statement.table.name == target_table
                and not getattr(fail_one_update, "injected", False)
            ):
                fail_one_update.injected = True
                return SimpleNamespace(rowcount=0)
            return original_execute(session, statement, *args, **kwargs)

        monkeypatch.setattr(Session, "execute", fail_one_update)
        return

    original_stage = GenerationRecordRepository.stage_record
    original_advance = GenerationRecordRepository.advance_continuation_work
    if failure == "fee_record":

        def fail_fee_record(repository, record):
            if isinstance(record, UnitFeeDecision):
                return SimpleNamespace(status="conflict")
            return original_stage(repository, record)

        from haoai_backend.generation_contracts.records import UnitFeeDecision

        monkeypatch.setattr(GenerationRecordRepository, "stage_record", fail_fee_record)
    elif failure == "work_advance":

        def fail_finalize_advance(repository, updated, *, expected_phase_version):
            if updated.phase == "finalize_pending":
                return SimpleNamespace(status="conflict")
            return original_advance(
                repository, updated, expected_phase_version=expected_phase_version
            )

        monkeypatch.setattr(
            GenerationRecordRepository,
            "advance_continuation_work",
            fail_finalize_advance,
        )


def _assert_fee_phase_unwritten(scenario, task_id: str, unit_id: str) -> None:
    with scenario["engine"].connect() as connection:
        task = connection.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == task_id)
        ).mappings().one()
        unit = connection.execute(
            select(BillingUnitRow.__table__).where(BillingUnitRow.id == unit_id)
        ).mappings().one()
        work = connection.execute(
            select(LocalContinuationWorkRow.__table__).where(
                LocalContinuationWorkRow.__table__.c.task_id == task_id
            )
        ).mappings().one()
        wallets = connection.execute(
            select(UserCreditRow.__table__).where(
                UserCreditRow.__table__.c.user_id == task["user_id"]
            )
        ).mappings().all()
        ledgers = connection.execute(select(CreditLogRow.__table__)).mappings().all()
        fees = connection.execute(
            select(UnitFeeDecisionRow.__table__).where(
                UnitFeeDecisionRow.__table__.c.unit_id == unit_id
            )
        ).mappings().all()
    assert task["billing_status"] == "unbilled"
    assert unit["billing_status"] == "unbilled"
    assert unit["debited_amount"] == 0
    if unit["quoted_amount"] == 0:
        assert wallets == []
    else:
        assert len(wallets) == 1 and wallets[0]["credits"] == 500
    assert ledgers == []
    assert fees == []
    assert work["phase"] == "fee_pending"
    assert work["phase_version"] == 1


def test_continuation_phase_release_advances_immediately_and_preserves_terminal_state(
    tmp_path, monkeypatch
) -> None:
    scenario = _persist_result(_scenario(tmp_path, monkeypatch))
    try:
        outcomes = ("delivered", "fee_resolved", "finalized")
        released_versions = []
        previous_claim = None
        for expected in outcomes:
            claim = _claim_next_phase(scenario)
            assert claim is not None
            if previous_claim is not None:
                assert not scenario["continuation_claims"].release_continuation(
                    previous_claim
                )
                with scenario["engine"].connect() as connection:
                    active_task = connection.execute(
                        select(AITaskRow.__table__).where(
                            AITaskRow.id == claim.owner_scope.task_id
                        )
                    ).mappings().one()
                assert active_task["claim_token"] == claim.token
            released_versions.append(claim.phase_version)
            if expected == "delivered":
                scenario["clock"][0] += timedelta(seconds=1)
                renewed = scenario["continuation_claims"].renew_continuation(claim)
                assert renewed is not None
                assert renewed.lease_until > claim.lease_until
            assert scenario["consumer"].consume(claim) == expected
            if expected == "delivered":
                lost_ack = {"raised": False}

                def raise_after_release_commit(_session):
                    if not lost_ack["raised"]:
                        lost_ack["raised"] = True
                        raise RuntimeError("synthetic release acknowledgement loss")

                event.listen(Session, "after_commit", raise_after_release_commit)
                try:
                    assert scenario["continuation_claims"].release_continuation(
                        claim
                    )
                finally:
                    event.remove(Session, "after_commit", raise_after_release_commit)
                assert lost_ack["raised"]
            else:
                assert scenario["continuation_claims"].release_continuation(claim)
            with scenario["engine"].connect() as connection:
                task = connection.execute(
                    select(AITaskRow.__table__).where(
                        AITaskRow.id == claim.owner_scope.task_id
                    )
                ).mappings().one()
                work = connection.execute(
                    select(LocalContinuationWorkRow.__table__).where(
                        LocalContinuationWorkRow.__table__.c.task_id
                        == claim.owner_scope.task_id
                    )
                ).mappings().one()
            assert task["claim_token"] is None
            assert task["claimed_by"] is None
            if expected == "finalized":
                assert task["status"] == "completed"
                assert work["phase"] == "done"
            else:
                assert task["status"] == "queued"
                assert work["phase_version"] == claim.phase_version + 1
            previous_claim = claim
        assert released_versions == [0, 1, 2]
    finally:
        scenario["engine"].dispose()


@pytest.mark.parametrize(
    ("failure", "cost_per_call", "wallet_credits"),
    (
        ("unit_cas", 3, 500),
        ("task_cas", 3, 500),
        ("fee_record", 3, 500),
        ("work_advance", 3, 500),
        ("zero_unit_cas", 0, None),
    ),
)
def test_fee_stage_failures_roll_back_every_billing_projection(
    tmp_path, monkeypatch, failure, cost_per_call, wallet_credits
) -> None:
    scenario = _persist_result(
        _scenario(
            tmp_path,
            monkeypatch,
            cost_per_call=cost_per_call,
            wallet_credits=wallet_credits,
        )
    )
    try:
        _make_fee_pending(scenario)
        claim = _claim_next_phase(scenario)
        assert claim is not None
        _inject_fee_failure(monkeypatch, failure)
        assert scenario["consumer"].consume(claim) == "pending"
        _assert_fee_phase_unwritten(
            scenario, claim.owner_scope.task_id, claim.unit_id
        )
    finally:
        scenario["engine"].dispose()


def test_frame_review_delivery_saves_first_text_and_never_replaces_it_on_second_review(
    tmp_path, monkeypatch
) -> None:
    scenario = _persist_result(_scenario(tmp_path, monkeypatch))
    try:
        first_claim = _claim_next_phase(scenario)
        assert first_claim is not None
        assert scenario["consumer"].consume(first_claim) == "delivered"
        receipt = scenario["receipt"]
        verified = scenario["verified"]
        replay_result = ValidatedGenerationResult(
            result_ref=receipt.result_ref,
            observation_ref=receipt.evidence_id,
            validation_version=receipt.validation_version,
            recoverable_locator=receipt.recoverable_locator,
            content_digest=receipt.content_digest,
            validated_at=receipt.validated_at,
            generation_fulfilled_at=receipt.generation_fulfilled_at,
            timely_success=True,
            selected_attempt_id=receipt.selected_attempt_id,
            safe_output=TextResult("优化后的分镜正文"),
            automatic_application_allowed=True,
        )
        with scenario["sessions"]() as session:
            with session.begin():
                replay = series_generation.stage_frame_result(
                    session,
                    first_claim,
                    replay_result,
                    verified.snapshot.target_snapshot,
                )
        assert replay.kind == "already_applied"
        assert scenario["continuation_claims"].release_continuation(first_claim)
        with scenario["engine"].connect() as connection:
            first_chapter = connection.execute(
                select(chapters).where(chapters.c.id == "joint-chapter-1")
            ).mappings().one()
        first_frames = json.loads(first_chapter["content"])
        assert first_frames[0]["text"] == "优化后的分镜正文"
        assert first_frames[0]["original_text"] == "原始分镜正文"

        fee_claim = _claim_next_phase(scenario)
        assert fee_claim is not None and fee_claim.phase_version == 1
        assert scenario["consumer"].consume(fee_claim) == "fee_resolved"
        assert scenario["continuation_claims"].release_continuation(fee_claim)
        finalize_claim = _claim_next_phase(scenario)
        assert finalize_claim is not None and finalize_claim.phase_version == 2
        assert scenario["consumer"].consume(finalize_claim) == "finalized"
        assert scenario["continuation_claims"].release_continuation(finalize_claim)

        second_body = admission._review_body()
        second_body["message_id"] = "source-message-329-second"
        second = _persist_result(
            scenario, output="第二次优化后的正文", body=second_body
        )
        second_claim = _claim_next_phase(second)
        assert second_claim is not None
        assert second["consumer"].consume(second_claim) == "delivered"
        with scenario["engine"].connect() as connection:
            second_chapter = connection.execute(
                select(chapters).where(chapters.c.id == "joint-chapter-1")
            ).mappings().one()
        second_frames = json.loads(second_chapter["content"])
        assert second_frames[0]["text"] == "第二次优化后的正文"
        assert second_frames[0]["original_text"] == "原始分镜正文"
    finally:
        scenario["engine"].dispose()


@pytest.mark.parametrize(
    ("existing_original", "expected_original"),
    (("已有有效原文", "已有有效原文"), (" \t", "原始分镜正文")),
)
def test_frame_review_delivery_preserves_only_valid_existing_original_text(
    tmp_path, monkeypatch, existing_original, expected_original
) -> None:
    scenario = _persist_result(
        _scenario(
            tmp_path,
            monkeypatch,
            chapter_content=[
                {
                    "text": "原始分镜正文",
                    "original_text": existing_original,
                    "storyboard": ["review-storyboard-1"],
                },
                {"text": "非目标帧", "storyboard": ["review-storyboard-2"]},
            ],
        )
    )
    try:
        claim = _claim_next_phase(scenario)
        assert claim is not None
        assert scenario["consumer"].consume(claim) == "delivered"
        with scenario["engine"].connect() as connection:
            chapter = connection.execute(
                select(chapters).where(chapters.c.id == "joint-chapter-1")
            ).mappings().one()
        frame = json.loads(chapter["content"])[0]
        assert frame["text"] == "优化后的分镜正文"
        assert frame["original_text"] == expected_original
    finally:
        scenario["engine"].dispose()


def test_frame_review_delivery_follows_stable_id_after_reorder(
    tmp_path, monkeypatch
) -> None:
    scenario = _persist_result(_scenario(tmp_path, monkeypatch))
    try:
        with scenario["engine"].begin() as connection:
            chapter = connection.execute(
                select(chapters).where(chapters.c.id == "joint-chapter-1")
            ).mappings().one()
            frames = json.loads(chapter["content"])
            frames.reverse()
            connection.execute(
                update(chapters)
                .where(chapters.c.id == "joint-chapter-1")
                .values(content=json.dumps(frames, ensure_ascii=False))
            )
        claim = _claim_next_phase(scenario)
        assert claim is not None
        assert scenario["consumer"].consume(claim) == "delivered"
        with scenario["engine"].connect() as connection:
            chapter = connection.execute(
                select(chapters).where(chapters.c.id == "joint-chapter-1")
            ).mappings().one()
        frames = json.loads(chapter["content"])
        assert frames[1]["storyboard"] == ["review-storyboard-1"]
        assert frames[1]["text"] == "优化后的分镜正文"
        assert frames[1]["original_text"] == "原始分镜正文"
        assert frames[0]["text"] == "非目标帧"
    finally:
        scenario["engine"].dispose()


def test_frame_review_delivery_accepts_no_id_with_original_chapter_snapshot(
    tmp_path, monkeypatch
) -> None:
    scenario = _persist_result(
        _scenario(
            tmp_path,
            monkeypatch,
            storyboard_asset_ids=(),
            chapter_content=[{"text": "无稳定ID的原始帧", "custom": {"keep": True}}],
        )
    )
    try:
        claim = _claim_next_phase(scenario)
        assert claim is not None
        assert scenario["consumer"].consume(claim) == "delivered"
        with scenario["engine"].connect() as connection:
            chapter = connection.execute(
                select(chapters).where(chapters.c.id == "joint-chapter-1")
            ).mappings().one()
        frame = json.loads(chapter["content"])[0]
        assert frame["text"] == "优化后的分镜正文"
        assert frame["original_text"] == "无稳定ID的原始帧"
        assert frame["custom"] == {"keep": True}
    finally:
        scenario["engine"].dispose()


@pytest.mark.parametrize("failure", ("local_stop", "application_record"))
def test_frame_review_application_failure_rolls_back_all_delivery_writes(
    tmp_path, monkeypatch, failure
) -> None:
    scenario = _persist_result(_scenario(tmp_path, monkeypatch))
    claim = _claim_next_phase(scenario)
    assert claim is not None
    local_stop = Event()
    if failure == "local_stop":
        original_reconcile = series_generation.stage_media_reconciliation

        def stop_after_media_stage(*args, **kwargs):
            outcome = original_reconcile(*args, **kwargs)
            local_stop.set()
            return outcome

        monkeypatch.setattr(
            series_generation, "stage_media_reconciliation", stop_after_media_stage
        )
    else:
        def reject_application_record(*_args, **_kwargs):
            raise RuntimeError("synthetic application record conflict")

        monkeypatch.setattr(
            series_generation, "stage_application_record", reject_application_record
        )
    try:
        assert scenario["consumer"].consume(
            claim,
            local_stop_signal=local_stop if failure == "local_stop" else None,
        ) == "pending"
        with scenario["engine"].connect() as connection:
            task = connection.execute(
                select(AITaskRow.__table__).where(
                    AITaskRow.id == claim.owner_scope.task_id
                )
            ).mappings().one()
            unit = connection.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.id == claim.unit_id
                )
            ).mappings().one()
            work = connection.execute(
                select(LocalContinuationWorkRow.__table__).where(
                    LocalContinuationWorkRow.__table__.c.work_id == claim.work_id
                )
            ).mappings().one()
            chapter = connection.execute(
                select(chapters).where(chapters.c.id == "joint-chapter-1")
            ).mappings().one()
            asset = connection.execute(
                text(
                    "SELECT description FROM storyboard_assets "
                    "WHERE id = 'review-storyboard-1'"
                )
            ).mappings().one()
            applications = connection.execute(
                select(ApplicationRecordRow.__table__).where(
                    ApplicationRecordRow.__table__.c.task_id == claim.owner_scope.task_id
                )
            ).mappings().all()
        assert task["status"] == "processing"
        assert task["claim_token"] == claim.token
        assert unit["result_status"] == "succeeded"
        assert work["phase"] == "delivery_pending"
        assert work["phase_version"] == 0
        assert json.loads(chapter["content"])[0]["text"] == "原始分镜正文"
        assert "original_text" not in json.loads(chapter["content"])[0]
        assert asset["description"] is None
        assert applications == []
    finally:
        scenario["continuation_claims"].release_continuation(claim)
        scenario["engine"].dispose()


def test_failed_frame_review_delivery_releases_claim_and_preserves_failed_status(
    tmp_path, monkeypatch
) -> None:
    scenario = _persist_result(_scenario(tmp_path, monkeypatch))
    try:
        with scenario["engine"].begin() as connection:
            chapter = connection.execute(
                select(chapters).where(chapters.c.id == "joint-chapter-1")
            ).mappings().one()
            frames = json.loads(chapter["content"])
            frames[0]["text"] = "外部并发改稿"
            connection.execute(
                update(chapters)
                .where(chapters.c.id == "joint-chapter-1")
                .values(content=json.dumps(frames, ensure_ascii=False))
            )
        claim = _claim_next_phase(scenario)
        assert claim is not None
        assert scenario["consumer"].consume(claim) == "finalized"
        assert scenario["continuation_claims"].release_continuation(claim)
        with scenario["engine"].connect() as connection:
            task = connection.execute(
                select(AITaskRow.__table__).where(
                    AITaskRow.id == claim.owner_scope.task_id
                )
            ).mappings().one()
            work = connection.execute(
                select(LocalContinuationWorkRow.__table__).where(
                    LocalContinuationWorkRow.__table__.c.work_id == claim.work_id
                )
            ).mappings().one()
        assert task["status"] == "failed"
        assert task["claim_token"] is None
        assert task["claimed_by"] is None
        assert work["phase"] == "stopped_delivery_failed"
        assert work["terminal_reason"] == "frame_review_target_delivery_failed"
    finally:
        scenario["engine"].dispose()


def test_continuation_shutdown_releases_claim_and_signals_blocked_executor() -> None:
    class Port:
        lease_seconds = 1

        def __init__(self):
            self.claim = ContinuationClaimAuthority(
                owner_scope=OwnerTaskScope("owner", "task", "frame.review"),
                unit_id="unit",
                work_id="work",
                result_ref="result",
                phase_version=0,
                worker_instance="worker",
                token="token",
                execution_generation=1,
                lease_until=datetime.now(timezone.utc) + timedelta(seconds=10),
            )
            self.renewed = Event()
            self.released: list[ContinuationClaimAuthority] = []

        def claim_next(self, _task_type, _instance):
            return None

        def claim_continuation(self, _task_type, _instance, _lease_seconds):
            return self.claim

        def renew_continuation(self, claim):
            updated = replace(
                claim, lease_until=datetime.now(timezone.utc) + timedelta(seconds=10)
            )
            self.claim = updated
            self.renewed.set()
            return updated

        def release_continuation(self, claim):
            self.released.append(claim)
            return True

    class Executor:
        def __init__(self):
            self.started = Event()
            self.finish = Event()
            self.local_stop = None

        def bind_execution_port(self, _port):
            return None

        def execute_continuation_claim(self, _claim, local_stop_signal):
            self.local_stop = local_stop_signal
            self.started.set()
            self.finish.wait(timeout=2)
            return "pending"

        def execute_claim(self, _claim):
            raise AssertionError("continuations must use their private stop signal")

    port = Port()
    executor = Executor()

    def unused_factory():
        return object()

    configuration = GenerationRuntimeConfig(
        session_factory=unused_factory,
        db_clock=lambda: datetime.now(timezone.utc),
        id_factory=lambda: "runtime",
        http_client_factory=unused_factory,
        credential_resolver=lambda _binding, _purpose: None,
        provider_factories=(),
        media_store_factory=unused_factory,
        local_muse_staging_dir=None,
        public_media_fetcher_factory=unused_factory,
        bound_provider_content_fetcher_factory=unused_factory,
        local_muse_capability_factory=unused_factory,
        r2_storage_factory=unused_factory,
        shutdown_timeout_seconds=0.5,
    )
    runtime = build_runtime(
        configuration,
        (
            RegisteredExecutorFactory(
                "ai-review", lambda: port, lambda: executor
            ),
        ),
    )
    outcomes: list[str] = []
    runner = Thread(
        target=lambda: outcomes.append(runtime.run_claimed_once("ai-review"))
    )
    runner.start()
    try:
        assert executor.started.wait(timeout=1)
        assert port.renewed.wait(timeout=1.5)
        receipt = runtime.shutdown("continuation shutdown test")
        assert receipt.released_resources == ("claim:task",)
        assert receipt.unreleased_resources == ()
        assert executor.local_stop is not None and executor.local_stop.is_set()
    finally:
        executor.finish.set()
        runner.join(timeout=2)
    assert not runner.is_alive()
    assert outcomes == ["pending"]
    assert len(port.released) == 1
