from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from haoai_backend.chat_data.tables import chat_messages
from haoai_backend.generation_catalog.domain import ModelSource
from haoai_backend.generation_core.admission_receipts import AdmissionStaged
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
    UserCreditRow,
)
from haoai_backend.generation_core.image_batch_claims import SqlAlchemyImageBatchClaims
from haoai_backend.generation_core.image_batch_intents import SqlAlchemyImageBatchIntents
from test_batch_execution_snapshot_reader import (
    _authenticated_batch_environment,
    _binding_for_task,
)


def _batch_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    environment, staged = _authenticated_batch_environment(
        monkeypatch,
        tmp_path,
        model_source=ModelSource.SYSTEM,
        item_count=2,
    )
    assert isinstance(staged, AdmissionStaged)
    return environment, staged


def _claims(environment):
    return SqlAlchemyImageBatchClaims(
        lambda: Session(environment.engine),
        lambda: datetime(2026, 10, 16, 2, 0, tzinfo=timezone.utc),
        lambda: "batch-intent-claim-token",
    )


def _intents(environment, session_factory=None, id_factory=None):
    crypto = environment.capabilities.crypto
    proof_authority = environment.capabilities.proof_authority
    assert crypto is not None and proof_authority is not None
    return SqlAlchemyImageBatchIntents(
        session_factory or (lambda: Session(environment.engine)),
        lambda: datetime(2026, 10, 16, 2, 0, tzinfo=timezone.utc),
        id_factory or (lambda: "batch-intent-submission-id"),
        environment.binding._producer._registry,
        crypto,
        proof_authority,
    )


def _mark_first_sibling_failed(environment, staged: AdmissionStaged) -> tuple[object, ...]:
    first_task_id = staged.ordered_tasks[0].task_id
    with Session(environment.engine) as session:
        task = session.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == first_task_id)
        ).mappings().one()
        unit = session.execute(
            select(BillingUnitRow.__table__).where(
                BillingUnitRow.__table__.c.task_id == first_task_id
            )
        ).mappings().one()
        step = session.execute(
            select(ExecutionStepRow.__table__).where(
                ExecutionStepRow.__table__.c.billing_unit_id == unit["id"]
            )
        ).mappings().one()
        session.execute(
            update(AITaskRow.__table__)
            .where(AITaskRow.__table__.c.id == first_task_id)
            .values(status="failed", result="synthetic sibling result")
        )
        session.execute(
            update(BillingUnitRow.__table__)
            .where(BillingUnitRow.__table__.c.id == unit["id"])
            .values(result_status="failed")
        )
        session.execute(
            update(ExecutionStepRow.__table__)
            .where(ExecutionStepRow.__table__.c.id == step["id"])
            .values(execution_status="failed")
        )
        session.execute(
            update(chat_messages)
            .where(chat_messages.c.id == task["message_id"])
            .values(content="synthetic sibling result")
        )
        session.commit()
        unit_id = unit["id"]
        step_id = step["id"]
        message_id = task["message_id"]
    return _read_sibling_state(
        environment, first_task_id, unit_id, step_id, message_id
    )


def _read_sibling_state(
    environment, task_id: str, unit_id: str, step_id: str, message_id: str
) -> tuple[object, ...]:
    with Session(environment.engine) as session:
        return (
            task_id,
            unit_id,
            step_id,
            message_id,
            session.execute(
                select(AITaskRow.__table__.c.status).where(
                    AITaskRow.__table__.c.id == task_id
                )
            ).scalar_one(),
            session.execute(
                select(BillingUnitRow.__table__.c.result_status).where(
                    BillingUnitRow.__table__.c.id == unit_id
                )
            ).scalar_one(),
            session.execute(
                select(ExecutionStepRow.__table__.c.execution_status).where(
                    ExecutionStepRow.__table__.c.id == step_id
                )
            ).scalar_one(),
            session.execute(
                select(chat_messages.c.content).where(
                    chat_messages.c.id == message_id
                )
            ).scalar_one(),
        )


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


def test_batch_intent_is_item_scoped_and_uses_fresh_verified_parent_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment, staged = _batch_environment(tmp_path, monkeypatch)
    sibling_before = _mark_first_sibling_failed(environment, staged)
    target = _binding_for_task(environment, staged, 1)
    claims = _claims(environment)
    claim = claims.claim_next("batch-image", "worker-image-batch-intent")
    assert claim is not None
    assert claim.scope.task_id == target.task_id
    assert claim.scope.operation == "image.batch"
    before_financials = _financial_state(environment, claim.scope.user_id)

    prepared = _intents(environment).prepare(claim)

    assert prepared.disposition == "committed"
    assert prepared.verified_input is not None
    assert prepared.intent is not None
    assert prepared.verified_input.item_index == 1
    assert prepared.verified_input.ordered_count == 2
    assert prepared.verified_input.task_id == target.task_id
    assert prepared.verified_input.unit_id == target.unit_id
    assert prepared.verified_input.step_id == target.step_id
    assert prepared.verified_input.submission_id == staged.submission_id
    assert prepared.intent.step_id == target.step_id
    assert prepared.intent.attempt_id == prepared.verified_input.server_attempt_id
    assert prepared.intent.deadline_at - prepared.intent.first_intent_at == timedelta(
        hours=2
    )
    assert _financial_state(environment, claim.scope.user_id) == before_financials
    assert _read_sibling_state(environment, *sibling_before[:4]) == sibling_before
    with Session(environment.engine) as session:
        rows = session.execute(
            select(ExternalSubmissionRow.__table__)
            .where(ExternalSubmissionRow.__table__.c.step_id == target.step_id)
        ).mappings().all()
        all_rows = session.execute(select(ExternalSubmissionRow.__table__)).mappings().all()
        assert len(rows) == 1
        assert len(all_rows) == 1
        assert rows[0]["submission_status"] == "submitting"
        assert rows[0]["external_task_id"] is None
        assert rows[0]["deadline_at"] == prepared.intent.deadline_at.replace(tzinfo=None)
    environment.engine.dispose()


def test_unknown_intent_commit_is_confirmed_once_and_retry_does_not_restage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment, staged = _batch_environment(tmp_path, monkeypatch)
    target = _binding_for_task(environment, staged, 0)
    claim = _claims(environment).claim_next("batch-image", "worker-image-batch-intent")
    assert claim is not None
    assert claim.scope.task_id == target.task_id
    factory_calls = 0
    created_ids: list[str] = []

    class CommitThenLoseAcknowledgement:
        def __init__(self) -> None:
            self.session = Session(environment.engine)

        def __enter__(self):
            return self.session

        def __exit__(self, exc_type, exc, traceback):
            self.session.close()
            raise RuntimeError("synthetic lost commit acknowledgement")

    def session_factory():
        nonlocal factory_calls
        factory_calls += 1
        return (
            CommitThenLoseAcknowledgement()
            if factory_calls == 1
            else Session(environment.engine)
        )

    intents = _intents(
        environment,
        session_factory,
        lambda: created_ids.append("batch-intent-id") or "batch-intent-id",
    )

    first = intents.prepare(claim)
    second = intents.prepare(claim)

    assert factory_calls == 3
    assert created_ids == ["batch-intent-id"]
    assert first.disposition == "committed"
    assert first.intent is not None
    assert first.verified_input is not None
    assert first.verified_input.item_index == 0
    assert second.disposition == "unresolved"
    assert second.intent is None
    assert second.verified_input is None
    with Session(environment.engine) as session:
        rows = session.execute(
            select(ExternalSubmissionRow.__table__).where(
                ExternalSubmissionRow.__table__.c.step_id == target.step_id
            )
        ).mappings().all()
        assert len(rows) == 1
        assert rows[0]["id"] == first.intent.external_submission_id
        assert rows[0]["submission_status"] == "submitting"
        assert rows[0]["external_task_id"] is None
    environment.engine.dispose()
