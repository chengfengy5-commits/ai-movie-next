from __future__ import annotations

from datetime import timedelta

import test_video_single_claims as video_fixtures
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from test_admission_staging import _joint_caller, _joint_prepare_and_stage

from haoai_backend.generation_core.admission_receipts import AdmissionStaged
from haoai_backend.generation_core.base_schema import (
    BillingUnitRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
)
from haoai_backend.generation_core.haoai_group.claims import (
    SqlAlchemyHaoaiGroupClaims,
)
from haoai_backend.generation_core.haoai_group.intents import (
    SqlAlchemyHaoaiGroupIntents,
    haoai_group_submission_key,
)
from haoai_backend.generation_contracts.types import ExternalOperationRef
from haoai_backend.generation_core.schema import ExecutionAttemptGroupRow
from haoai_backend.generation_runtime.registry import HAOAI_ATTEMPT_GROUP


def _admit(environment, key: str) -> str:
    caller = _joint_caller(environment.engine)
    try:
        staged = _joint_prepare_and_stage(
            environment.binding,
            caller,
            operation="video.single",
            raw_key=key,
            request=video_fixtures._video_request("haoai"),
        )
        assert isinstance(staged, AdmissionStaged)
        caller.commit()
        return staged.ordered_tasks[0].task_id
    finally:
        if caller.in_transaction():
            caller.rollback()
        caller.close()


def _claim_and_intents(environment, task_id: str, clock):
    claims = SqlAlchemyHaoaiGroupClaims(
        session_factory=lambda: Session(environment.engine),
        db_clock=lambda: clock[0],
        id_factory=lambda: "haoai-group-intent-claim",
    )
    claim = claims.claim_next("video-single", "haoai-group-intent-worker")
    assert claim is not None and claim.scope.task_id == task_id
    crypto = environment.capabilities.crypto
    proof_authority = environment.capabilities.proof_authority
    assert crypto is not None and proof_authority is not None
    intents = SqlAlchemyHaoaiGroupIntents(
        session_factory=lambda: Session(environment.engine),
        db_clock=lambda: clock[0],
        id_factory=lambda: "haoai-group-submission-1",
        registry=environment.binding._producer._registry,
        crypto=crypto,
        proof_authority=proof_authority,
    )
    return claim, intents


def test_haoai_group_intent_commits_once_with_shared_deadline_and_safe_snapshot(
    tmp_path,
    monkeypatch,
):
    environment = video_fixtures._video_environment(
        monkeypatch,
        tmp_path,
        provider="haoai",
    )
    try:
        task_id = _admit(environment, "haoai-group-intent-commit-001")
        clock = [video_fixtures._NOW]
        claim, intents = _claim_and_intents(environment, task_id, clock)

        prepared = intents.prepare(claim)

        assert prepared.disposition == "committed"
        assert prepared.intent is not None
        assert prepared.verified_input is not None
        intent = prepared.intent
        assert intent.provider == "haoai"
        assert intent.step_id == prepared.verified_input.step_id
        assert intent.attempt_id == intent.step_id
        assert intent.provider_idempotency_key == haoai_group_submission_key(
            prepared.verified_input.unit_id, intent.step_id
        )
        assert intent.deadline_at - intent.first_intent_at == timedelta(hours=2)
        assert intent.committed_at == intent.first_intent_at == video_fixtures._NOW
        with Session(environment.engine) as reader:
            unit = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.id == prepared.verified_input.unit_id
                )
            ).mappings().one()
            step = reader.execute(
                select(ExecutionStepRow.__table__).where(
                    ExecutionStepRow.id == intent.step_id
                )
            ).mappings().one()
            group = reader.execute(
                select(ExecutionAttemptGroupRow.__table__).where(
                    ExecutionAttemptGroupRow.unit_id == prepared.verified_input.unit_id
                )
            ).mappings().one()
            submissions = reader.execute(
                select(ExternalSubmissionRow.__table__).where(
                    ExternalSubmissionRow.step_id == intent.step_id
                )
            ).mappings().all()
        assert unit["first_intent_at"] == intent.first_intent_at.replace(tzinfo=None)
        assert unit["deadline_at"] == intent.deadline_at.replace(tzinfo=None)
        assert step["execution_status"] == "submitting"
        assert group["status"] == "active"
        assert len(submissions) == 1
        row = submissions[0]
        assert row["submission_status"] == "submitting"
        assert row["external_task_id"] is None
        assert row["submission_key"] == intent.provider_idempotency_key
        assert row["deadline_at"] == intent.deadline_at.replace(tzinfo=None)
        assert "joint-fixture-provider-secret" not in str(row["request_snapshot"])
        assert "A quiet room at sunset" not in str(row["request_snapshot"])
    finally:
        environment.engine.dispose()


def test_haoai_group_intent_replay_of_unknown_create_does_not_create_another_row(
    tmp_path,
    monkeypatch,
):
    environment = video_fixtures._video_environment(
        monkeypatch,
        tmp_path,
        provider="haoai",
    )
    try:
        task_id = _admit(environment, "haoai-group-intent-unknown-001")
        clock = [video_fixtures._NOW]
        claim, intents = _claim_and_intents(environment, task_id, clock)
        first = intents.prepare(claim)
        assert first.disposition == "committed"

        second = intents.prepare(claim)

        assert second.disposition == "unresolved"
        with Session(environment.engine) as reader:
            rows = reader.execute(
                select(ExternalSubmissionRow.__table__)
                .where(ExternalSubmissionRow.__table__.c.step_id == first.intent.step_id)
            ).mappings().all()
        assert len(rows) == 1
        assert rows[0]["id"] == first.intent.external_submission_id
        assert rows[0]["external_task_id"] is None
    finally:
        environment.engine.dispose()


def test_haoai_group_intent_resumes_known_id_as_read_only_query(
    tmp_path,
    monkeypatch,
):
    environment = video_fixtures._video_environment(
        monkeypatch,
        tmp_path,
        provider="haoai",
    )
    try:
        task_id = _admit(environment, "haoai-group-intent-resume-001")
        clock = [video_fixtures._NOW]
        claim, intents = _claim_and_intents(environment, task_id, clock)
        first = intents.prepare(claim)
        assert first.disposition == "committed"

        with environment.engine.begin() as connection:
            connection.execute(
                update(ExternalSubmissionRow.__table__)
                .where(
                    ExternalSubmissionRow.__table__.c.id
                    == first.intent.external_submission_id
                )
                .values(
                    submission_status="submitted",
                    external_task_id="haoai-known-original-123",
                    submitted_at=video_fixtures._NOW.replace(tzinfo=None),
                )
            )
            connection.execute(
                update(ExecutionStepRow.__table__)
                .where(ExecutionStepRow.__table__.c.id == first.intent.step_id)
                .values(execution_status="submitted")
            )

        resumed = intents.prepare(claim)

        assert resumed.disposition == "resume_query"
        assert resumed.external_ref is not None
        assert resumed.external_ref.external_id == "haoai-known-original-123"
        assert resumed.external_ref.intent_id == first.intent.external_submission_id
        assert resumed.external_ref.attempt_id == first.intent.step_id
        with Session(environment.engine) as reader:
            submissions = reader.execute(
                select(ExternalSubmissionRow.__table__)
                .where(ExternalSubmissionRow.__table__.c.step_id == first.intent.step_id)
            ).mappings().all()
        assert len(submissions) == 1
        assert submissions[0]["external_task_id"] == "haoai-known-original-123"
    finally:
        environment.engine.dispose()


def test_haoai_group_create_submission_persists_original_id_and_never_reposts(
    tmp_path,
    monkeypatch,
):
    environment = video_fixtures._video_environment(
        monkeypatch,
        tmp_path,
        provider="haoai",
    )
    try:
        task_id = _admit(environment, "haoai-group-create-known-id-001")
        clock = [video_fixtures._NOW]
        claim, intents = _claim_and_intents(environment, task_id, clock)
        prepared = intents.prepare(claim)
        assert prepared.disposition == "committed"
        external_ref = ExternalOperationRef(
            intent_id=prepared.intent.external_submission_id,
            attempt_id=prepared.intent.attempt_id,
            provider="haoai",
            external_id="haoai-known-create-id-456",
            create_outcome="submitted",
        )

        assert (
            intents.record_submission(
                claim, prepared.intent, prepared.verified_input, external_ref
            )
            == "recorded"
        )
        resumed = intents.prepare(claim)

        assert resumed.disposition == "resume_query"
        assert resumed.external_ref.external_id == external_ref.external_id
        assert resumed.intent.external_submission_id == prepared.intent.external_submission_id
        with Session(environment.engine) as reader:
            row = reader.execute(
                select(ExternalSubmissionRow.__table__).where(
                    ExternalSubmissionRow.__table__.c.step_id == prepared.intent.step_id
                )
            ).mappings().one()
            step = reader.execute(
                select(ExecutionStepRow.__table__).where(
                    ExecutionStepRow.__table__.c.id == prepared.intent.step_id
                )
            ).mappings().one()
        assert row["submission_status"] == "submitted"
        assert row["external_task_id"] == external_ref.external_id
        assert step["execution_status"] == "submitted"
    finally:
        environment.engine.dispose()


def test_haoai_group_unknown_create_is_sticky_read_only_without_id_or_new_intent(
    tmp_path,
    monkeypatch,
):
    environment = video_fixtures._video_environment(
        monkeypatch,
        tmp_path,
        provider="haoai",
    )
    try:
        task_id = _admit(environment, "haoai-group-create-unknown-001")
        clock = [video_fixtures._NOW]
        claim, intents = _claim_and_intents(environment, task_id, clock)
        prepared = intents.prepare(claim)
        assert prepared.disposition == "committed"
        before_financial = video_fixtures._financial_snapshot(environment.engine)
        external_ref = ExternalOperationRef(
            intent_id=prepared.intent.external_submission_id,
            attempt_id=prepared.intent.attempt_id,
            provider="haoai",
            external_id=None,
            create_outcome="unknown",
            last_safe_provider_state="create_outcome_unknown",
        )

        assert (
            intents.record_submission(
                claim, prepared.intent, prepared.verified_input, external_ref
            )
            == "recorded"
        )
        replay = intents.prepare(claim)

        assert replay.disposition == "unresolved"
        assert video_fixtures._financial_snapshot(environment.engine) == before_financial
        with Session(environment.engine) as reader:
            row = reader.execute(
                select(ExternalSubmissionRow.__table__).where(
                    ExternalSubmissionRow.__table__.c.step_id == prepared.intent.step_id
                )
            ).mappings().one()
            step = reader.execute(
                select(ExecutionStepRow.__table__).where(
                    ExecutionStepRow.__table__.c.id == prepared.intent.step_id
                )
            ).mappings().one()
            unit = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.id == prepared.verified_input.unit_id
                )
            ).mappings().one()
            group = reader.execute(
                select(ExecutionAttemptGroupRow.__table__).where(
                    ExecutionAttemptGroupRow.unit_id == prepared.verified_input.unit_id
                )
            ).mappings().one()
        assert row["submission_status"] == "unknown"
        assert row["external_task_id"] is None
        assert step["execution_status"] == "reconciling"
        assert unit["result_status"] == "unknown"
        assert group["status"] == "pending_verification"
    finally:
        environment.engine.dispose()


def test_haoai_group_create_refuses_original_secret_as_provider_id(
    tmp_path,
    monkeypatch,
):
    environment = video_fixtures._video_environment(
        monkeypatch,
        tmp_path,
        provider="haoai",
    )
    try:
        task_id = _admit(environment, "haoai-group-create-secret-echo-001")
        clock = [video_fixtures._NOW]
        claim, intents = _claim_and_intents(environment, task_id, clock)
        prepared = intents.prepare(claim)
        assert prepared.disposition == "committed"
        secret = prepared.verified_input.provider_credential.secret
        external_ref = ExternalOperationRef(
            intent_id=prepared.intent.external_submission_id,
            attempt_id=prepared.intent.attempt_id,
            provider="haoai",
            external_id=secret,
            create_outcome="submitted",
        )

        assert (
            intents.record_submission(
                claim, prepared.intent, prepared.verified_input, external_ref
            )
            == "pending"
        )
        with Session(environment.engine) as reader:
            row = reader.execute(
                select(ExternalSubmissionRow.__table__).where(
                    ExternalSubmissionRow.__table__.c.step_id == prepared.intent.step_id
                )
            ).mappings().one()
        assert row["submission_status"] == "submitting"
        assert row["external_task_id"] is None
        assert secret not in str(row)
    finally:
        environment.engine.dispose()
