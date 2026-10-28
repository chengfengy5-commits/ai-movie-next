from __future__ import annotations

from datetime import timedelta

import test_video_single_claims as video_fixtures
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from test_admission_staging import _joint_caller, _joint_prepare_and_stage

from haoai_backend.generation_core.admission_receipts import AdmissionStaged
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    ExecutionStepRow,
)
from haoai_backend.generation_core.haoai_group.claims import (
    SqlAlchemyHaoaiGroupClaims,
)


def _admit(environment, provider: str, key: str) -> str:
    caller = _joint_caller(environment.engine)
    try:
        staged = _joint_prepare_and_stage(
            environment.binding,
            caller,
            operation="video.single",
            raw_key=key,
            request=video_fixtures._video_request(provider),
        )
        assert isinstance(staged, AdmissionStaged)
        caller.commit()
        return staged.ordered_tasks[0].task_id
    finally:
        if caller.in_transaction():
            caller.rollback()
        caller.close()


def _claims(environment, clock):
    return SqlAlchemyHaoaiGroupClaims(
        session_factory=lambda: Session(environment.engine),
        db_clock=lambda: clock[0],
        id_factory=lambda: "haoai-group-claim-token",
        lease_seconds=60,
    )


def test_haoai_group_claims_claim_renew_and_release_the_whole_group(
    tmp_path,
    monkeypatch,
):
    environment = video_fixtures._video_environment(
        monkeypatch,
        tmp_path,
        provider="haoai",
    )
    try:
        task_id = _admit(environment, "haoai", "haoai-group-claim-001")
        clock = [video_fixtures._NOW]
        claims = _claims(environment, clock)

        claim = claims.claim_next("video-single", "haoai-worker")

        assert claim is not None
        assert claim.scope.task_id == task_id
        assert claim.scope.operation == "video.single"
        clock[0] += timedelta(seconds=30)
        assert claims.renew_or_revoke(claim) == "renewed"
        with Session(environment.engine) as reader:
            task = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == task_id)
            ).mappings().one()
        assert task["status"] == "processing"
        assert task["claimed_by"] == "haoai-worker"
        assert task["claim_token"] == claim.token
        assert claims.release_claim(claim) is True
        assert claim.local_lost_signal.is_set()
        with Session(environment.engine) as reader:
            task = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == task_id)
            ).mappings().one()
        assert task["status"] == "queued"
        assert task["claim_token"] is None
    finally:
        environment.engine.dispose()


def test_haoai_group_claims_skip_ordinary_video_graphs(
    tmp_path,
    monkeypatch,
):
    environment = video_fixtures._video_environment(
        monkeypatch,
        tmp_path,
        provider="xai",
    )
    try:
        task_id = _admit(environment, "xai", "haoai-group-claim-ordinary-001")
        claims = _claims(environment, [video_fixtures._NOW])

        assert claims.claim_next("video-single", "haoai-worker") is None
        with Session(environment.engine) as reader:
            task = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == task_id)
            ).mappings().one()
        assert task["status"] == "queued"
        assert task["claim_token"] is None
    finally:
        environment.engine.dispose()


def test_haoai_group_claims_refuse_a_mutated_step_graph_without_leasing_task(
    tmp_path,
    monkeypatch,
):
    environment = video_fixtures._video_environment(
        monkeypatch,
        tmp_path,
        provider="haoai",
    )
    try:
        task_id = _admit(environment, "haoai", "haoai-group-claim-mutated-001")
        with Session(environment.engine) as reader:
            unit_id = reader.execute(
                select(BillingUnitRow.id).where(BillingUnitRow.task_id == task_id)
            ).scalar_one()
        with environment.engine.begin() as connection:
            connection.execute(
                update(ExecutionStepRow.__table__)
                .where(
                    ExecutionStepRow.__table__.c.billing_unit_id
                    == unit_id
                )
                .values(required=True)
            )
        claims = _claims(environment, [video_fixtures._NOW])

        assert claims.claim_next("video-single", "haoai-worker") is None
        with Session(environment.engine) as reader:
            task = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == task_id)
            ).mappings().one()
        assert task["status"] == "queued"
        assert task["claim_token"] is None
    finally:
        environment.engine.dispose()
