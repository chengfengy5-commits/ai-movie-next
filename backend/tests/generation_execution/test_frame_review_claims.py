"""SQL claim boundaries for the registered frame.review worker graph."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from sqlalchemy import event, insert, select, update
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.generation_contracts.types import TextResult
from haoai_backend.generation_core.base_schema import AITaskRow
from haoai_backend.generation_core.frame_review_claims import (
    SqlAlchemyFrameReviewClaims,
)
from haoai_backend.generation_core.frame_review_intents import (
    SqlAlchemyFrameReviewIntents,
)
from haoai_backend.generation_core.frame_review_results import (
    SqlAlchemyFrameReviewResults,
)
from haoai_backend.generation_core.base_schema import (
    BillingUnitRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
)
from generation_core.test_admission_staging import _G2_JOINT_NOW
from generation_core.test_admission_staging import _G2_JOINT_ACTOR_ID
from generation_execution.test_frame_review_admission import (
    _build_app,
    _post_review,
)


def test_claim_is_committed_and_fenced_to_one_admitted_review_graph(
    tmp_path, monkeypatch
) -> None:
    engine, app, token, _, _ = _build_app(tmp_path, monkeypatch)
    try:
        response = asyncio.run(_post_review(app, token))
        assert response.status_code == 200, response.text
        task_id = response.json()["task_id"]
        sessions = sessionmaker(engine, expire_on_commit=False)
        now = [_G2_JOINT_NOW]
        claims = SqlAlchemyFrameReviewClaims(
            session_factory=sessions,
            db_clock=lambda: now[0],
            id_factory=lambda: "frame-review-claim-1",
        )

        claim = claims.claim_next("ai-review", "review-worker-1")

        assert claim is not None
        assert claim.scope.task_id == task_id
        assert claim.scope.operation == "frame.review"
        assert claim.worker_instance == "review-worker-1"
        assert claim.execution_generation == 1
        assert claim.token == "frame-review-claim-1"
        with sessions() as session:
            task = session.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == task_id)
            ).mappings().one()
        assert task["status"] == "processing"
        assert task["claimed_by"] == claim.worker_instance
        assert task["claim_token"] == claim.token
        assert task["execution_generation"] == claim.execution_generation
        assert task["lease_until"] is not None

        second = SqlAlchemyFrameReviewClaims(
            session_factory=sessions,
            db_clock=lambda: now[0],
            id_factory=lambda: "frame-review-claim-2",
        )
        assert second.claim_next("ai-review", "review-worker-2") is None
        assert claims.release_claim(claim) is True
    finally:
        engine.dispose()


def test_claim_refuses_other_task_types_without_mutating_review_graph(
    tmp_path, monkeypatch
) -> None:
    engine, app, token, _, _ = _build_app(tmp_path, monkeypatch)
    try:
        response = asyncio.run(_post_review(app, token))
        assert response.status_code == 200, response.text
        task_id = response.json()["task_id"]
        sessions = sessionmaker(engine, expire_on_commit=False)
        claims = SqlAlchemyFrameReviewClaims(
            session_factory=sessions,
            db_clock=lambda: _G2_JOINT_NOW,
            id_factory=lambda: "frame-review-wrong-type",
        )

        assert claims.claim_next("image-single", "wrong-worker") is None
        with sessions() as session:
            task = session.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == task_id)
            ).mappings().one()
        assert task["status"] == "queued"
        assert task["execution_generation"] == 0
        assert task["claim_token"] is None
    finally:
        engine.dispose()


def test_original_intent_is_single_use_and_keeps_the_first_deadline(
    tmp_path, monkeypatch
) -> None:
    engine, app, token, capabilities, _ = _build_app(tmp_path, monkeypatch)
    try:
        response = asyncio.run(_post_review(app, token))
        assert response.status_code == 200, response.text
        sessions = sessionmaker(engine, expire_on_commit=False)
        now = [_G2_JOINT_NOW]
        claims = SqlAlchemyFrameReviewClaims(
            session_factory=sessions,
            db_clock=lambda: now[0],
            id_factory=lambda: "frame-review-intent-claim",
        )
        claim = claims.claim_next("ai-review", "review-worker")
        assert claim is not None
        intents = SqlAlchemyFrameReviewIntents(
            session_factory=sessions,
            db_clock=lambda: now[0],
            id_factory=lambda: "frame-review-original-intent",
            crypto=capabilities.crypto,
            proof_authority=capabilities.proof_authority,
        )

        preparation = intents.prepare(claim)

        assert preparation.disposition == "committed"
        assert preparation.verified_input is not None
        assert preparation.intent is not None
        intent = preparation.intent
        assert intent.committed_at == _G2_JOINT_NOW
        assert intent.first_intent_at == _G2_JOINT_NOW
        assert intent.deadline_at == _G2_JOINT_NOW + timedelta(seconds=7200)
        task_id = claim.scope.task_id
        with sessions() as session:
            unit = session.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == task_id
                )
            ).mappings().one()
            step = session.execute(
                select(ExecutionStepRow.__table__).where(
                    ExecutionStepRow.id == intent.step_id
                )
            ).mappings().one()
            submissions = session.execute(
                select(ExternalSubmissionRow.__table__).where(
                    ExternalSubmissionRow.step_id == intent.step_id
                )
            ).mappings().all()
        assert unit["first_intent_at"] == _G2_JOINT_NOW.replace(tzinfo=None)
        assert unit["deadline_at"] == intent.deadline_at.replace(tzinfo=None)
        assert step["execution_status"] == "submitting"
        assert len(submissions) == 1

        assert claims.release_claim(claim) is True
        now[0] = _G2_JOINT_NOW + timedelta(seconds=300)
        recovered_claim = SqlAlchemyFrameReviewClaims(
            session_factory=sessions,
            db_clock=lambda: now[0],
            id_factory=lambda: "frame-review-recovered-claim",
        ).claim_next("ai-review", "review-worker-2")
        assert recovered_claim is None
        with sessions() as session:
            current_submissions = session.execute(
                select(ExternalSubmissionRow.__table__).where(
                    ExternalSubmissionRow.step_id == intent.step_id
                )
            ).mappings().all()
            current_unit = session.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == task_id
                )
            ).mappings().one()
        assert len(current_submissions) == 1
        assert current_submissions[0]["id"] == intent.external_submission_id
        assert current_unit["first_intent_at"] == _G2_JOINT_NOW.replace(tzinfo=None)
        assert current_unit["deadline_at"] == intent.deadline_at.replace(tzinfo=None)
    finally:
        engine.dispose()


def test_intent_commit_ack_loss_is_confirmed_without_creating_another_intent(
    tmp_path, monkeypatch
) -> None:
    engine, app, token, capabilities, _ = _build_app(tmp_path, monkeypatch)
    try:
        response = asyncio.run(_post_review(app, token))
        assert response.status_code == 200, response.text
        sessions = sessionmaker(engine, expire_on_commit=False)
        claim = SqlAlchemyFrameReviewClaims(
            session_factory=sessions,
            db_clock=lambda: _G2_JOINT_NOW,
            id_factory=lambda: "frame-review-intent-ack-claim",
        ).claim_next("ai-review", "intent-ack-worker")
        assert claim is not None
        intents = SqlAlchemyFrameReviewIntents(
            session_factory=sessions,
            db_clock=lambda: _G2_JOINT_NOW,
            id_factory=lambda: "frame-review-intent-ack-submission",
            crypto=capabilities.crypto,
            proof_authority=capabilities.proof_authority,
        )
        callbacks = [0]

        def lose_ack_once(_session):
            callbacks[0] += 1
            if callbacks[0] == 1:
                raise RuntimeError("synthetic intent acknowledgement loss after commit")

        event.listen(Session, "after_commit", lose_ack_once)
        try:
            prepared = intents.prepare(claim)
        finally:
            event.remove(Session, "after_commit", lose_ack_once)

        assert prepared.disposition == "committed"
        assert prepared.intent is not None
        repeated = intents.prepare(claim)
        assert repeated.disposition == "existing"
        with sessions() as session:
            submissions = session.execute(
                select(ExternalSubmissionRow.__table__).where(
                    ExternalSubmissionRow.step_id == prepared.intent.step_id
                )
            ).mappings().all()
        assert len(submissions) == 1
        assert submissions[0]["id"] == prepared.intent.external_submission_id
        assert submissions[0]["intent_at"] == _G2_JOINT_NOW.replace(tzinfo=None)
        assert submissions[0]["deadline_at"] == (
            _G2_JOINT_NOW + timedelta(seconds=7200)
        ).replace(tzinfo=None)
    finally:
        engine.dispose()


def test_result_with_delivery_work_is_not_reclaimed_as_generation(
    tmp_path, monkeypatch
) -> None:
    engine, app, token, capabilities, _ = _build_app(tmp_path, monkeypatch)
    sessions = sessionmaker(engine, expire_on_commit=False)
    try:
        response = asyncio.run(_post_review(app, token))
        assert response.status_code == 200, response.text
        ids = iter(("review-result-claim", "review-result-intent", "review-result-next"))
        claims = SqlAlchemyFrameReviewClaims(
            session_factory=sessions,
            db_clock=lambda: _G2_JOINT_NOW,
            id_factory=lambda: next(ids),
        )
        claim = claims.claim_next("ai-review", "result-claim-worker")
        assert claim is not None
        prepared = SqlAlchemyFrameReviewIntents(
            session_factory=sessions,
            db_clock=lambda: _G2_JOINT_NOW,
            id_factory=lambda: "review-result-intent",
            crypto=capabilities.crypto,
            proof_authority=capabilities.proof_authority,
        ).prepare(claim)
        assert prepared.disposition == "committed"
        assert prepared.verified_input is not None and prepared.intent is not None
        result = SqlAlchemyFrameReviewResults(
            session_factory=sessions,
            db_clock=lambda: _G2_JOINT_NOW,
            id_factory=iter(
                ("review-result-ref", "review-result-evidence", "review-result-work")
            ).__next__,
            crypto=capabilities.crypto,
            proof_authority=capabilities.proof_authority,
        ).persist_success(
            claim,
            prepared.intent,
            prepared.verified_input,
            TextResult("已持久化的完整结果"),
        )
        assert result not in {"pending", "ownership_lost"}
        assert claims.release_claim(claim) is True

        assert claims.claim_next("ai-review", "result-claim-worker-2") is None
    finally:
        engine.dispose()


def test_existing_intent_is_not_freshly_claimed_after_release_or_lease_expiry(
    tmp_path, monkeypatch
) -> None:
    for lease_state in ("released", "expired"):
        scenario_dir = tmp_path / lease_state
        scenario_dir.mkdir()
        engine, app, token, capabilities, _ = _build_app(scenario_dir, monkeypatch)
        sessions = sessionmaker(engine, expire_on_commit=False)
        now = [_G2_JOINT_NOW]
        try:
            response = asyncio.run(_post_review(app, token))
            assert response.status_code == 200, response.text
            claim_ids = iter(
                (f"intent-{lease_state}-initial", f"intent-{lease_state}-recovery")
            )
            claims = SqlAlchemyFrameReviewClaims(
                session_factory=sessions,
                db_clock=lambda: now[0],
                id_factory=lambda: next(claim_ids),
            )
            claim = claims.claim_next("ai-review", "intent-initial-worker")
            assert claim is not None
            prepared = SqlAlchemyFrameReviewIntents(
                session_factory=sessions,
                db_clock=lambda: now[0],
                id_factory=lambda: f"intent-{lease_state}-submission",
                crypto=capabilities.crypto,
                proof_authority=capabilities.proof_authority,
            ).prepare(claim)
            assert prepared.disposition == "committed"
            assert prepared.intent is not None
            if lease_state == "released":
                assert claims.release_claim(claim) is True
            else:
                now[0] += timedelta(seconds=61)
                with sessions.begin() as session:
                    session.execute(
                        update(AITaskRow.__table__)
                        .where(AITaskRow.id == claim.scope.task_id)
                        .values(lease_until=now[0] - timedelta(seconds=1))
                    )

            recovered = claims.claim_next("ai-review", "intent-recovery-worker")
            assert recovered is None
        finally:
            engine.dispose()


def test_expired_prepared_graph_remains_claimable_and_advances_generation(
    tmp_path, monkeypatch
) -> None:
    engine, app, token, _, _ = _build_app(tmp_path, monkeypatch)
    sessions = sessionmaker(engine, expire_on_commit=False)
    try:
        response = asyncio.run(_post_review(app, token))
        assert response.status_code == 200, response.text
        initial = SqlAlchemyFrameReviewClaims(
            session_factory=sessions,
            db_clock=lambda: _G2_JOINT_NOW,
            id_factory=lambda: "prepared-expired-first",
        ).claim_next("ai-review", "prepared-first-worker")
        assert initial is not None
        with sessions.begin() as session:
            session.execute(
                update(AITaskRow.__table__)
                .where(AITaskRow.id == initial.scope.task_id)
                .values(lease_until=_G2_JOINT_NOW - timedelta(seconds=1))
            )

        recovered = SqlAlchemyFrameReviewClaims(
            session_factory=sessions,
            db_clock=lambda: _G2_JOINT_NOW,
            id_factory=lambda: "prepared-expired-second",
        ).claim_next("ai-review", "prepared-recovery-worker")
        assert recovered is not None
        assert recovered.execution_generation == initial.execution_generation + 1
    finally:
        engine.dispose()


def test_invalid_review_candidates_before_page_do_not_hide_later_valid_graph(
    tmp_path, monkeypatch
) -> None:
    from haoai_backend.generation_core.base_schema import AITaskRow

    engine, app, token, _, _ = _build_app(tmp_path, monkeypatch)
    try:
        response = asyncio.run(_post_review(app, token))
        assert response.status_code == 200, response.text
        task_id = response.json()["task_id"]
        with engine.begin() as connection:
            connection.execute(
                insert(AITaskRow.__table__),
                [
                    {
                        "id": f"invalid-review-orphan-{index:03d}",
                        "user_id": _G2_JOINT_ACTOR_ID,
                        "type": "ai-review",
                        "status": "queued",
                        "message_id": f"orphan-message-{index:03d}",
                        "request_data": "{}",
                        "model_name": "invalid",
                        "credit_cost": 0,
                        "created_at": (_G2_JOINT_NOW - timedelta(days=2)).replace(tzinfo=None),
                        "updated_at": (_G2_JOINT_NOW - timedelta(days=2)).replace(tzinfo=None),
                    }
                    for index in range(130)
                ],
            )
        claims = SqlAlchemyFrameReviewClaims(
            session_factory=sessionmaker(engine, expire_on_commit=False),
            db_clock=lambda: _G2_JOINT_NOW,
            id_factory=lambda: "claim-after-invalid-page",
        )
        candidates = claims._candidate_ids(None)
        assert [candidate[1] for candidate in candidates] == [task_id]
        claim = claims.claim_next("ai-review", "page-worker")
        assert claim is not None
        assert claim.scope.task_id == task_id
    finally:
        engine.dispose()


def test_renewal_commit_ack_loss_confirms_the_exact_live_lease(
    tmp_path, monkeypatch
) -> None:
    engine, app, token, _, _ = _build_app(tmp_path, monkeypatch)
    try:
        response = asyncio.run(_post_review(app, token))
        assert response.status_code == 200, response.text
        sessions = sessionmaker(engine, expire_on_commit=False)
        now = [_G2_JOINT_NOW]
        claims = SqlAlchemyFrameReviewClaims(
            session_factory=sessions,
            db_clock=lambda: now[0],
            id_factory=lambda: "renew-ack-claim",
            lease_seconds=60,
        )
        claim = claims.claim_next("ai-review", "renew-ack-worker")
        assert claim is not None
        callbacks = [0]

        def lose_ack_once(_session):
            callbacks[0] += 1
            if callbacks[0] == 1:
                raise RuntimeError("synthetic renewal acknowledgement loss")

        now[0] += timedelta(seconds=5)
        event.listen(Session, "after_commit", lose_ack_once)
        try:
            outcome = claims.renew_or_revoke(claim)
        finally:
            event.remove(Session, "after_commit", lose_ack_once)

        assert outcome == "renewed"
        with sessions() as session:
            task = session.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == claim.scope.task_id)
            ).mappings().one()
        assert task["claim_token"] == claim.token
        assert task["execution_generation"] == claim.execution_generation
        assert task["lease_until"] == (now[0] + timedelta(seconds=60)).replace(tzinfo=None)
    finally:
        engine.dispose()


def test_release_commit_ack_loss_confirms_the_exact_released_claim(
    tmp_path, monkeypatch
) -> None:
    engine, app, token, _, _ = _build_app(tmp_path, monkeypatch)
    try:
        response = asyncio.run(_post_review(app, token))
        assert response.status_code == 200, response.text
        sessions = sessionmaker(engine, expire_on_commit=False)
        claims = SqlAlchemyFrameReviewClaims(
            session_factory=sessions,
            db_clock=lambda: _G2_JOINT_NOW,
            id_factory=lambda: "release-ack-claim",
        )
        claim = claims.claim_next("ai-review", "release-ack-worker")
        assert claim is not None
        callbacks = [0]

        def lose_ack_once(_session):
            callbacks[0] += 1
            if callbacks[0] == 1:
                raise RuntimeError("synthetic release acknowledgement loss")

        event.listen(Session, "after_commit", lose_ack_once)
        try:
            released = claims.release_claim(claim)
        finally:
            event.remove(Session, "after_commit", lose_ack_once)

        assert released is True
        with sessions() as session:
            task = session.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == claim.scope.task_id)
            ).mappings().one()
        assert task["status"] == "queued"
        assert task["claimed_by"] is None
        assert task["claim_token"] is None
        assert task["lease_until"] is None
        assert task["execution_generation"] == claim.execution_generation
    finally:
        engine.dispose()


def test_renewal_roll_back_does_not_confirm_an_uncommitted_lease(
    tmp_path, monkeypatch
) -> None:
    engine, app, token, _, _ = _build_app(tmp_path, monkeypatch)
    try:
        response = asyncio.run(_post_review(app, token))
        assert response.status_code == 200, response.text
        sessions = sessionmaker(engine, expire_on_commit=False)
        claims = SqlAlchemyFrameReviewClaims(
            session_factory=sessions,
            db_clock=lambda: _G2_JOINT_NOW,
            id_factory=lambda: "renew-rollback-claim",
        )
        claim = claims.claim_next("ai-review", "renew-rollback-worker")
        assert claim is not None

        def roll_back_before_commit(_session):
            raise RuntimeError("synthetic renewal rollback")

        event.listen(Session, "before_commit", roll_back_before_commit)
        try:
            outcome = claims.renew_or_revoke(claim)
        finally:
            event.remove(Session, "before_commit", roll_back_before_commit)

        assert outcome == "ownership_lost"
        with sessions() as session:
            task = session.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == claim.scope.task_id)
            ).mappings().one()
        assert task["claim_token"] == claim.token
        assert task["execution_generation"] == claim.execution_generation
        assert task["lease_until"] == claim.lease_until.replace(tzinfo=None)
    finally:
        engine.dispose()


def test_second_renewal_rollback_does_not_reconfirm_the_previous_lease(
    tmp_path, monkeypatch
) -> None:
    engine, app, token, _, _ = _build_app(tmp_path, monkeypatch)
    try:
        response = asyncio.run(_post_review(app, token))
        assert response.status_code == 200, response.text
        sessions = sessionmaker(engine, expire_on_commit=False)
        now = [_G2_JOINT_NOW]
        claims = SqlAlchemyFrameReviewClaims(
            session_factory=sessions,
            db_clock=lambda: now[0],
            id_factory=lambda: "renew-second-rollback-claim",
        )
        claim = claims.claim_next("ai-review", "renew-second-rollback-worker")
        assert claim is not None
        first_lease = claim.lease_until

        now[0] += timedelta(seconds=10)
        assert claims.renew_or_revoke(claim) == "renewed"
        second_lease = now[0] + timedelta(seconds=60)
        assert second_lease > first_lease
        with sessions() as session:
            task = session.execute(
                select(AITaskRow.__table__).where(
                    AITaskRow.id == claim.scope.task_id
                )
            ).mappings().one()
        assert task["lease_until"] == second_lease.replace(tzinfo=None)

        def roll_back_before_commit(_session):
            raise RuntimeError("synthetic second renewal rollback")

        event.listen(Session, "before_commit", roll_back_before_commit)
        try:
            outcome = claims.renew_or_revoke(claim)
        finally:
            event.remove(Session, "before_commit", roll_back_before_commit)

        assert outcome == "ownership_lost"
        assert claim.local_lost_signal.is_set()
        with sessions() as session:
            task = session.execute(
                select(AITaskRow.__table__).where(
                    AITaskRow.id == claim.scope.task_id
                )
            ).mappings().one()
        assert task["lease_until"] == second_lease.replace(tzinfo=None)
        assert task["claim_token"] == claim.token
        assert task["execution_generation"] == claim.execution_generation
    finally:
        engine.dispose()


def test_renewal_ack_after_fence_change_or_expiry_is_not_confirmed(
    tmp_path, monkeypatch
) -> None:
    for scenario in ("fence_changed", "lease_expired"):
        scenario_dir = tmp_path / scenario
        scenario_dir.mkdir()
        engine, app, token, _, _ = _build_app(scenario_dir, monkeypatch)
        try:
            response = asyncio.run(_post_review(app, token))
            assert response.status_code == 200, response.text
            sessions = sessionmaker(engine, expire_on_commit=False)
            now = [_G2_JOINT_NOW]
            claims = SqlAlchemyFrameReviewClaims(
                session_factory=sessions,
                db_clock=lambda: now[0],
                id_factory=lambda: f"renew-{scenario}-claim",
                lease_seconds=60,
            )
            claim = claims.claim_next("ai-review", f"renew-{scenario}-worker")
            assert claim is not None
            now[0] += timedelta(seconds=5)
            callbacks = [0]

            def change_after_commit(_session):
                callbacks[0] += 1
                if callbacks[0] != 1:
                    return
                if scenario == "lease_expired":
                    now[0] += timedelta(seconds=60)
                else:
                    with sessions.begin() as changed:
                        changed.execute(
                            update(AITaskRow.__table__)
                            .where(AITaskRow.id == claim.scope.task_id)
                            .values(
                                claim_token="superseding-token",
                                execution_generation=claim.execution_generation + 1,
                            )
                        )
                raise RuntimeError("synthetic renewal acknowledgement loss")

            event.listen(Session, "after_commit", change_after_commit)
            try:
                outcome = claims.renew_or_revoke(claim)
            finally:
                event.remove(Session, "after_commit", change_after_commit)

            assert outcome == "ownership_lost"
            assert claim.local_lost_signal.is_set()
            with sessions() as session:
                task = session.execute(
                    select(AITaskRow.__table__).where(
                        AITaskRow.id == claim.scope.task_id
                    )
                ).mappings().one()
            if scenario == "fence_changed":
                assert task["claim_token"] == "superseding-token"
                assert task["execution_generation"] == claim.execution_generation + 1
            else:
                assert task["lease_until"] == now[0].replace(tzinfo=None)
        finally:
            engine.dispose()
