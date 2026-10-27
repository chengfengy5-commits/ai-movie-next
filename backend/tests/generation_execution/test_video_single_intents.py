from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
import test_video_single_claims as video_fixtures
from sqlalchemy import select
from sqlalchemy.orm import Session

from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
)
from haoai_backend.generation_core.video_single_intents import (
    SqlAlchemyVideoSingleIntents,
)


def _claimed_video_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    raw_key: str,
):
    environment = video_fixtures._video_environment(
        monkeypatch,
        tmp_path,
        provider="aistarslab",
    )
    request.addfinalizer(environment.engine.dispose)
    task_id, _ = video_fixtures._admit_video_task(environment, key=raw_key)
    clock = [video_fixtures._NOW]
    claims = video_fixtures._claim_adapter(
        environment.engine,
        clock,
        f"video-claim-{raw_key}",
    )
    claim = claims.claim_next("video-single", "video-worker-intent")
    assert claim is not None
    assert claim.scope.task_id == task_id
    return environment, claim


def _intents(
    environment,
    *,
    session_factory=None,
    id_factory=None,
):
    crypto = environment.capabilities.crypto
    proof_authority = environment.capabilities.proof_authority
    assert crypto is not None and proof_authority is not None
    return SqlAlchemyVideoSingleIntents(
        session_factory or (lambda: Session(environment.engine)),
        lambda: video_fixtures._NOW,
        id_factory or (lambda: "video-intent-submission-id"),
        environment.binding._producer._registry,
        crypto,
        proof_authority,
    )


def test_video_intent_commits_once_and_returns_only_after_fresh_readback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    environment, claim = _claimed_video_task(
        tmp_path,
        monkeypatch,
        request,
        "video-intent-durable-readback",
    )
    before_financial = video_fixtures._financial_snapshot(environment.engine)

    prepared = _intents(environment).prepare(claim)

    assert prepared.disposition == "committed"
    assert prepared.verified_input is not None
    assert prepared.intent is not None
    assert prepared.intent.step_id
    assert prepared.intent.provider == "aistarslab"
    assert prepared.intent.deadline_at - prepared.intent.first_intent_at == timedelta(
        hours=2
    )
    verified = prepared.verified_input
    assert verified.server_attempt_id
    assert verified.snapshot.model_ref.provider == "aistarslab"
    assert len(verified.reference_images) == 3
    assert len(verified.reference_audios) == 3
    assert len(verified.reference_videos) == 3
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == claim.scope.task_id)
        ).mappings().one()
        unit = reader.execute(
            select(BillingUnitRow.__table__).where(
                BillingUnitRow.task_id == claim.scope.task_id
            )
        ).mappings().one()
        step = reader.execute(
            select(ExecutionStepRow.__table__).where(
                ExecutionStepRow.__table__.c.id == prepared.intent.step_id
            )
        ).mappings().one()
        submissions = reader.execute(
            select(ExternalSubmissionRow.__table__).where(
                ExternalSubmissionRow.__table__.c.step_id == prepared.intent.step_id
            )
        ).mappings().all()
    assert task["status"] == "processing"
    assert task["claim_token"] == claim.token
    assert unit["first_intent_at"] == prepared.intent.first_intent_at.replace(
        tzinfo=None
    )
    assert unit["deadline_at"] == prepared.intent.deadline_at.replace(tzinfo=None)
    assert unit["billing_status"] in {"charged", "no_charge"}
    assert step["execution_status"] == "submitting"
    assert len(submissions) == 1
    assert submissions[0]["submission_status"] == "submitting"
    assert submissions[0]["external_task_id"] is None
    assert "joint-fixture-provider-secret" not in str(
        submissions[0]["request_snapshot"]
    )
    assert video_fixtures._financial_snapshot(environment.engine) == before_financial


def test_unknown_video_intent_commit_is_confirmed_without_reposting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    environment, claim = _claimed_video_task(
        tmp_path,
        monkeypatch,
        request,
        "video-intent-unknown-commit",
    )
    before_financial = video_fixtures._financial_snapshot(environment.engine)
    factory_calls = 0
    created_ids: list[str] = []

    class CommitThenRaise:
        def __init__(self) -> None:
            self.session = Session(environment.engine)

        def __enter__(self):
            return self.session

        def __exit__(self, exc_type, exc, traceback):
            self.session.close()
            raise RuntimeError("video intent commit acknowledgement lost")

    def session_factory():
        nonlocal factory_calls
        factory_calls += 1
        return CommitThenRaise() if factory_calls == 2 else Session(environment.engine)

    def id_factory() -> str:
        created_ids.append("video-intent-unknown-id")
        return "video-intent-unknown-id"

    prepared = _intents(
        environment,
        session_factory=session_factory,
        id_factory=id_factory,
    ).prepare(claim)

    assert factory_calls == 3
    assert created_ids == ["video-intent-unknown-id"]
    assert prepared.disposition == "committed"
    assert prepared.intent is not None
    assert prepared.verified_input is not None
    with Session(environment.engine) as reader:
        submissions = reader.execute(
            select(ExternalSubmissionRow.__table__).where(
                ExternalSubmissionRow.__table__.c.step_id == prepared.intent.step_id
            )
        ).mappings().all()
        unit = reader.execute(
            select(BillingUnitRow.__table__).where(
                BillingUnitRow.task_id == claim.scope.task_id
            )
        ).mappings().one()
    assert len(submissions) == 1
    assert submissions[0]["id"] == prepared.intent.external_submission_id
    assert submissions[0]["external_task_id"] is None
    assert unit["deadline_at"] - unit["first_intent_at"] == timedelta(hours=2)
    assert video_fixtures._financial_snapshot(environment.engine) == before_financial
