from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from haoai_backend.chat_data.tables import chat_messages
from haoai_backend.generation_contracts.types import StoredMedia
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
from haoai_backend.generation_core.image_batch_results import (
    SqlAlchemyImageBatchResults,
)
from haoai_backend.generation_core.schema import GenerationResultReceiptRow
from generation_core.test_image_batch_intents import (
    _batch_environment,
    _claims,
    _intents,
    _read_sibling_state,
)


_INTENT_NOW = datetime(2026, 10, 16, 2, 0, tzinfo=timezone.utc)


def _item_state(environment, task_id: str) -> tuple[object, ...]:
    with Session(environment.engine) as session:
        task = session.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == task_id)
        ).mappings().one()
        unit = session.execute(
            select(BillingUnitRow.__table__).where(
                BillingUnitRow.__table__.c.task_id == task_id
            )
        ).mappings().one()
        step = session.execute(
            select(ExecutionStepRow.__table__).where(
                ExecutionStepRow.__table__.c.billing_unit_id == unit["id"]
            )
        ).mappings().one()
        submission = session.execute(
            select(ExternalSubmissionRow.__table__).where(
                ExternalSubmissionRow.__table__.c.step_id == step["id"]
            )
        ).mappings().all()
        message = session.execute(
            select(chat_messages.c.content).where(
                chat_messages.c.id == task["message_id"]
            )
        ).scalar_one()
        return (
            task["status"],
            task["result"],
            task["billing_status"],
            unit["result_status"],
            unit["billing_status"],
            unit["refunded_amount"],
            step["execution_status"],
            tuple(
                (
                    row["id"],
                    row["submission_status"],
                    row["submitted_at"],
                )
                for row in submission
            ),
            message,
        )


def _financial_state(environment, owner_id: str) -> tuple[object, ...]:
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
    return balance, tuple(tuple(row) for row in ledger)


def _prepare_one_item(environment):
    claim = _claims(environment).claim_next(
        "batch-image", "worker-image-batch-results"
    )
    assert claim is not None
    prepared = _intents(environment).prepare(claim)
    assert prepared.disposition == "committed"
    assert prepared.verified_input is not None
    assert prepared.intent is not None
    return claim, prepared.intent, prepared.verified_input


def test_complete_persists_only_the_selected_child_of_real_two_item_batch(
    tmp_path, monkeypatch
) -> None:
    environment, staged = _batch_environment(tmp_path, monkeypatch)
    claim, intent, verified = _prepare_one_item(environment)
    sibling_task_id = next(
        task.task_id
        for task in staged.ordered_tasks
        if task.task_id != claim.scope.task_id
    )
    with Session(environment.engine) as session:
        sibling_unit = session.execute(
            select(BillingUnitRow.__table__).where(
                BillingUnitRow.__table__.c.task_id == sibling_task_id
            )
        ).mappings().one()
        sibling_step = session.execute(
            select(ExecutionStepRow.__table__).where(
                ExecutionStepRow.__table__.c.billing_unit_id == sibling_unit["id"]
            )
        ).mappings().one()
        sibling_message_id = session.execute(
            select(AITaskRow.__table__.c.message_id).where(
                AITaskRow.__table__.c.id == sibling_task_id
            )
        ).scalar_one()
    sibling_before = _read_sibling_state(
        environment,
        sibling_task_id,
        sibling_unit["id"],
        sibling_step["id"],
        sibling_message_id,
    )
    financial_before = _financial_state(environment, claim.scope.user_id)
    parent_order_before: tuple[str, ...]
    with Session(environment.engine) as session:
        parent_order_before = tuple(
            session.execute(
                select(TaskSubmissionRow.__table__.c.task_ids).where(
                    TaskSubmissionRow.__table__.c.id == staged.submission_id
                )
            ).scalar_one()
        )

    result_ids = iter(("batch-result-ref", "batch-result-evidence"))
    results = SqlAlchemyImageBatchResults(
        lambda: Session(environment.engine),
        lambda: _INTENT_NOW,
        lambda: next(result_ids),
    )
    receipt = results.complete(
        claim,
        intent,
        verified,
        StoredMedia(
            locator="https://media.example.test/batch/selected.png",
            content_digest="a" * 64,
            media_type="image/png",
            size_bytes=128,
        ),
    )

    assert receipt is not None
    assert receipt.operation == "image.batch"
    assert receipt.task_id == claim.scope.task_id
    assert receipt.unit_id == verified.unit_id
    assert receipt.step_id == intent.step_id
    assert receipt.submission_id == intent.external_submission_id
    assert _read_sibling_state(environment, *sibling_before[:4]) == sibling_before
    assert _financial_state(environment, claim.scope.user_id) == financial_before
    with Session(environment.engine) as session:
        parent_order_after = tuple(
            session.execute(
                select(TaskSubmissionRow.__table__.c.task_ids).where(
                    TaskSubmissionRow.__table__.c.id == staged.submission_id
                )
            ).scalar_one()
        )
        saved = GenerationResultReceiptRow.__table__
        saved_receipt = session.execute(
            select(saved).where(saved.c.result_ref == receipt.result_ref)
        ).mappings().one()
        evidence = session.execute(
            select(ExternalSubmissionRow.__table__.c.submission_status).where(
                ExternalSubmissionRow.__table__.c.id == intent.external_submission_id
            )
        ).scalar_one()
    assert parent_order_after == parent_order_before
    assert saved_receipt["task_id"] == claim.scope.task_id
    assert saved_receipt["unit_id"] == verified.unit_id
    assert evidence == "completed"
    selected = _item_state(environment, claim.scope.task_id)
    assert selected[0] == "completed"
    assert selected[3] == "succeeded"
    assert selected[6] == "result_observed"
    assert selected[8] == "https://media.example.test/batch/selected.png"
    environment.engine.dispose()


def test_settle_due_refunds_only_selected_batch_item_once(tmp_path, monkeypatch) -> None:
    environment, staged = _batch_environment(tmp_path, monkeypatch)
    claim, _intent, verified = _prepare_one_item(environment)
    sibling_task_id = next(
        task.task_id
        for task in staged.ordered_tasks
        if task.task_id != claim.scope.task_id
    )
    with Session(environment.engine) as session:
        sibling_unit = session.execute(
            select(BillingUnitRow.__table__).where(
                BillingUnitRow.__table__.c.task_id == sibling_task_id
            )
        ).mappings().one()
        sibling_step = session.execute(
            select(ExecutionStepRow.__table__).where(
                ExecutionStepRow.__table__.c.billing_unit_id == sibling_unit["id"]
            )
        ).mappings().one()
        sibling_message_id = session.execute(
            select(AITaskRow.__table__.c.message_id).where(
                AITaskRow.__table__.c.id == sibling_task_id
            )
        ).scalar_one()
        selected_unit = session.execute(
            select(BillingUnitRow.__table__).where(
                BillingUnitRow.__table__.c.task_id == claim.scope.task_id
            )
        ).mappings().one()
    sibling_before = _read_sibling_state(
        environment,
        sibling_task_id,
        sibling_unit["id"],
        sibling_step["id"],
        sibling_message_id,
    )
    financial_before = _financial_state(environment, claim.scope.user_id)
    before_balance = financial_before[0]
    unit_id = verified.unit_id
    amount = selected_unit["debited_amount"]
    assert type(amount) is int and amount > 0
    refund_ids = iter(("batch-due-refund-id",))
    results = SqlAlchemyImageBatchResults(
        lambda: Session(environment.engine),
        lambda: datetime(2026, 10, 16, 4, 0, tzinfo=timezone.utc),
        lambda: next(refund_ids),
    )

    assert results.settle_due(claim.scope) == "settled"
    after_first = _financial_state(environment, claim.scope.user_id)
    assert results.settle_due(claim.scope) == "settled"
    after_second = _financial_state(environment, claim.scope.user_id)

    assert after_first == after_second
    assert after_second[0] == before_balance + amount
    refund_rows = [row for row in after_second[1] if row[2] == f"refund:{unit_id}"]
    assert len(refund_rows) == 1
    assert refund_rows[0][1] == amount
    assert _read_sibling_state(environment, *sibling_before[:4]) == sibling_before
    assert _item_state(environment, claim.scope.task_id)[0] == "failed"
    with Session(environment.engine) as session:
        parent_order = tuple(
            session.execute(
                select(TaskSubmissionRow.__table__.c.task_ids).where(
                    TaskSubmissionRow.__table__.c.id == staged.submission_id
                )
            ).scalar_one()
        )
    assert parent_order == tuple(task.task_id for task in staged.ordered_tasks)
    environment.engine.dispose()


def test_unknown_child_stays_unresolved_without_early_refund(
    tmp_path, monkeypatch
) -> None:
    environment, _staged = _batch_environment(tmp_path, monkeypatch)
    claim, intent, verified = _prepare_one_item(environment)
    financial_before = _financial_state(environment, claim.scope.user_id)
    ids = iter(("batch-observation-evidence",))
    results = SqlAlchemyImageBatchResults(
        lambda: Session(environment.engine),
        lambda: _INTENT_NOW,
        lambda: next(ids),
    )

    observed = results.record_unknown_observation(
        claim,
        intent,
        verified,
        payload_digest="b" * 64,
        result_location="https://provider.example.test/unknown/result.png",
        evidence_kind="provider_image_locator",
    )
    assert observed == "recorded"
    assert results.mark_unknown(claim, intent, verified) == "pending"
    assert results.settle_due(claim.scope) == "pending"
    assert _financial_state(environment, claim.scope.user_id) == financial_before
    child_state = _item_state(environment, claim.scope.task_id)
    assert child_state[0] == "reconciling"
    assert child_state[3] == "unknown"
    assert child_state[6] == "reconciling"
    assert child_state[7][0][1] == "unknown"
    environment.engine.dispose()


def test_unknown_observation_survives_local_claim_loss_without_state_changes(
    tmp_path, monkeypatch
) -> None:
    environment, _staged = _batch_environment(tmp_path, monkeypatch)
    claim, intent, verified = _prepare_one_item(environment)
    state_before = _item_state(environment, claim.scope.task_id)
    financial_before = _financial_state(environment, claim.scope.user_id)
    ids = iter(("lost-claim-observation-evidence",))
    results = SqlAlchemyImageBatchResults(
        lambda: Session(environment.engine),
        lambda: _INTENT_NOW,
        lambda: next(ids),
    )
    claim.local_lost_signal.set()

    result = results.record_unknown_observation(
        claim,
        intent,
        verified,
        payload_digest="c" * 64,
        result_location="https://provider.example.test/lost-claim/result.png",
        evidence_kind="provider_image_locator",
    )

    assert result == "recorded"
    assert _item_state(environment, claim.scope.task_id) == state_before
    assert _financial_state(environment, claim.scope.user_id) == financial_before
    with Session(environment.engine) as session:
        observations = session.execute(
            select(ResultEvidenceRow.__table__).where(
                ResultEvidenceRow.step_id == intent.step_id,
                ResultEvidenceRow.submission_id == intent.external_submission_id,
                ResultEvidenceRow.evidence_key
                == f"image-batch-observation-v1:{intent.external_submission_id}",
            )
        ).mappings().all()
    assert len(observations) == 1
    assert observations[0]["validity"] == "unknown"
    assert observations[0]["application_status"] == "pending"
    environment.engine.dispose()
