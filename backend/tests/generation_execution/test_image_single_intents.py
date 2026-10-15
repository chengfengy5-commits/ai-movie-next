from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

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
from haoai_backend.generation_core.claims import SqlAlchemyImageSingleClaims
from haoai_backend.generation_core.image_single_intents import (
    SqlAlchemyImageSingleIntents,
)
from test_admission_staging import (
    _joint_caller,
    _joint_environment,
    _joint_prepare_and_stage,
)
from test_execution_snapshot_reader import _authenticated_environment


def _claimed_image_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw_key: str
):
    environment = _authenticated_environment(monkeypatch, tmp_path)
    caller = _joint_caller(environment.engine)
    staged = _joint_prepare_and_stage(
        environment.binding,
        caller,
        operation="image.single",
        raw_key=raw_key,
        request={
            "model_id": "model-1",
            "prompt": "Intent test",
            "target": {"kind": "standalone"},
            "size": None,
            "reference_images": [],
        },
    )
    assert isinstance(staged, AdmissionStaged)
    task_id = staged.ordered_tasks[0].task_id
    caller.commit()
    caller.close()

    claims = SqlAlchemyImageSingleClaims(
        lambda: Session(environment.engine),
        lambda: datetime(2026, 10, 16, 2, 0, tzinfo=timezone.utc),
        lambda: f"claim-{raw_key}",
    )
    claim = claims.claim_next("image-single", "worker-image-intent")
    assert claim is not None
    assert claim.scope.task_id == task_id
    return environment, claim


def _intents(
    environment,
    session_factory: Callable[[], Session] | None = None,
    id_factory: Callable[[], str] | None = None,
    db_clock: Callable[[], datetime] | None = None,
):
    crypto = environment.capabilities.crypto
    proof_authority = environment.capabilities.proof_authority
    assert crypto is not None and proof_authority is not None
    return SqlAlchemyImageSingleIntents(
        session_factory or (lambda: Session(environment.engine)),
        db_clock or (lambda: datetime(2026, 10, 16, 2, 0, tzinfo=timezone.utc)),
        id_factory or (lambda: "intent-submission-id"),
        environment.binding._producer._registry,
        crypto,
        proof_authority,
    )


def test_intent_commit_returns_only_after_durable_graph_readback(
    tmp_path: Path, monkeypatch
) -> None:
    environment, claim = _claimed_image_task(
        tmp_path, monkeypatch, "intent-durable-readback"
    )
    intents = _intents(environment)

    prepared = intents.prepare(claim)

    assert prepared.disposition == "committed"
    assert prepared.verified_input is not None
    assert prepared.intent is not None
    assert prepared.intent.step_id
    assert prepared.intent.deadline_at - prepared.intent.first_intent_at == timedelta(
        hours=2
    )
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
        submission = reader.execute(
            select(ExternalSubmissionRow.__table__).where(
                ExternalSubmissionRow.__table__.c.id
                == prepared.intent.external_submission_id
            )
        ).mappings().one()
    assert task["status"] == "processing"
    assert unit["first_intent_at"] == prepared.intent.first_intent_at.replace(
        tzinfo=None
    )
    assert unit["deadline_at"] == prepared.intent.deadline_at.replace(tzinfo=None)
    assert step["execution_status"] == "submitting"
    assert submission["submission_status"] == "submitting"
    assert submission["external_task_id"] is None
    environment.engine.dispose()


def test_unknown_intent_commit_ack_is_confirmed_in_a_fresh_session(
    tmp_path: Path, monkeypatch
) -> None:
    environment, claim = _claimed_image_task(
        tmp_path, monkeypatch, "intent-unknown-commit"
    )
    factory_calls = 0

    class CommitThenRaise:
        def __init__(self) -> None:
            self.session = Session(environment.engine)

        def __enter__(self):
            return self.session

        def __exit__(self, exc_type, exc, traceback):
            self.session.close()
            raise RuntimeError("intent commit acknowledgement lost")

    def session_factory():
        nonlocal factory_calls
        factory_calls += 1
        return CommitThenRaise() if factory_calls == 1 else Session(environment.engine)

    prepared = _intents(environment, session_factory).prepare(claim)

    assert factory_calls == 2
    assert prepared.disposition == "committed"
    assert prepared.intent is not None
    with Session(environment.engine) as reader:
        rows = reader.execute(
            select(ExternalSubmissionRow.__table__).where(
                ExternalSubmissionRow.__table__.c.step_id == prepared.intent.step_id
            )
        ).mappings().all()
    assert len(rows) == 1
    unit_id = rows[0]["request_snapshot"]["unit_id"]
    assert rows[0]["submission_key"] == (
        f"image-single-create-v1:{unit_id}:{prepared.intent.step_id}"
    )
    environment.engine.dispose()


def test_intent_readback_refuses_a_claim_cancelled_after_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment, claim = _claimed_image_task(
        tmp_path, monkeypatch, "intent-cancel-after-commit"
    )
    factory_calls = 0

    class CommitThenCancel:
        def __init__(self) -> None:
            self.session = Session(environment.engine)

        def __enter__(self):
            return self.session

        def __exit__(self, exc_type, exc, traceback):
            self.session.close()
            with Session(environment.engine) as mutator:
                mutator.execute(
                    update(AITaskRow.__table__)
                    .where(AITaskRow.id == claim.scope.task_id)
                    .values(
                        status="cancelling",
                        user_cancelled_at=datetime(2026, 10, 16, 2, 1),
                        cancellation_reason="user_request",
                    )
                )
                mutator.commit()

    def session_factory():
        nonlocal factory_calls
        factory_calls += 1
        return CommitThenCancel() if factory_calls == 1 else Session(environment.engine)

    prepared = _intents(environment, session_factory).prepare(claim)

    assert factory_calls == 2
    assert prepared.disposition in {"ownership_lost", "unresolved", "existing"}
    assert prepared.intent is None
    assert prepared.verified_input is None
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == claim.scope.task_id)
        ).mappings().one()
        rows = reader.execute(select(ExternalSubmissionRow.__table__)).mappings().all()
    assert task["status"] == "cancelling"
    assert len(rows) == 1
    assert rows[0]["external_task_id"] is None
    environment.engine.dispose()


def test_existing_intent_does_not_issue_a_second_create_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment, claim = _claimed_image_task(
        tmp_path, monkeypatch, "intent-existing-no-repost"
    )
    created_ids: list[str] = []
    intents = _intents(
        environment,
        id_factory=lambda: created_ids.append("intent-id") or "intent-id",
    )

    first = intents.prepare(claim)
    second = intents.prepare(claim)

    assert first.disposition == "committed"
    assert first.intent is not None
    assert second.disposition == "unresolved"
    assert second.intent is None
    assert second.verified_input is None
    assert created_ids == ["intent-id"]
    with Session(environment.engine) as reader:
        rows = reader.execute(select(ExternalSubmissionRow.__table__)).mappings().all()
        task = reader.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == claim.scope.task_id)
        ).mappings().one()
    assert len(rows) == 1
    assert rows[0]["id"] == first.intent.external_submission_id
    assert task["status"] == "reconciling"
    environment.engine.dispose()


def test_intent_stage_rechecks_the_database_lease_after_snapshot_reopen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment, claim = _claimed_image_task(
        tmp_path, monkeypatch, "intent-lease-expires-during-read"
    )
    times = iter(
        (
            datetime(2026, 10, 16, 2, 0, tzinfo=timezone.utc),
            datetime(2026, 10, 16, 2, 2, tzinfo=timezone.utc),
        )
    )

    prepared = _intents(environment, db_clock=lambda: next(times)).prepare(claim)

    assert prepared.disposition == "unresolved"
    assert prepared.intent is None
    assert prepared.verified_input is None
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
                ExecutionStepRow.__table__.c.billing_unit_id == unit["id"]
            )
        ).mappings().one()
        submissions = reader.execute(
            select(ExternalSubmissionRow.__table__)
        ).mappings().all()
    assert task["status"] == "processing"
    assert unit["first_intent_at"] is None
    assert step["execution_status"] == "prepared"
    assert submissions == []
    environment.engine.dispose()



def _financial_snapshot(environment, owner_id: str):
    with Session(environment.engine) as reader:
        wallet = reader.execute(
            select(UserCreditRow.__table__.c.credits).where(
                UserCreditRow.__table__.c.user_id == owner_id
            )
        ).scalar_one()
        ledger = reader.execute(
            select(
                CreditLogRow.__table__.c.id,
                CreditLogRow.__table__.c.amount,
                CreditLogRow.__table__.c.business_key,
            )
            .where(CreditLogRow.__table__.c.user_id == owner_id)
            .order_by(CreditLogRow.__table__.c.id)
        ).all()
    return wallet, tuple(ledger)


@pytest.mark.parametrize("blocked_state", ["cancelled", "refunded", "failed", "succeeded"])
def test_intent_requires_an_eligible_unsettled_unit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, blocked_state: str
) -> None:
    environment, claim = _claimed_image_task(
        tmp_path, monkeypatch, f"intent-unit-ineligible-{blocked_state}"
    )
    with Session(environment.engine) as mutator:
        unit = mutator.execute(
            select(BillingUnitRow.__table__).where(
                BillingUnitRow.__table__.c.task_id == claim.scope.task_id
            )
        ).mappings().one()
        values = {
            "user_cancelled_at": None,
            "billing_status": unit["billing_status"],
            "result_status": unit["result_status"],
            "success_confirmed_at": None,
        }
        if blocked_state == "cancelled":
            values["user_cancelled_at"] = datetime(2026, 10, 16, 2, 0)
        elif blocked_state == "refunded":
            values["billing_status"] = "refunded"
        elif blocked_state == "failed":
            values["result_status"] = "failed"
        else:
            values["result_status"] = "succeeded"
            values["success_confirmed_at"] = datetime(2026, 10, 16, 2, 0)
        mutator.execute(
            update(BillingUnitRow.__table__)
            .where(BillingUnitRow.__table__.c.id == unit["id"])
            .values(**values)
        )
        mutator.commit()

    before_financials = _financial_snapshot(environment, claim.scope.user_id)
    prepared = _intents(environment).prepare(claim)

    assert prepared.disposition == "unresolved"
    assert prepared.intent is None
    assert prepared.verified_input is None
    assert _financial_snapshot(environment, claim.scope.user_id) == before_financials
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow.__table__).where(
                AITaskRow.__table__.c.id == claim.scope.task_id
            )
        ).mappings().one()
        unit = reader.execute(
            select(BillingUnitRow.__table__).where(
                BillingUnitRow.__table__.c.task_id == claim.scope.task_id
            )
        ).mappings().one()
        step = reader.execute(
            select(ExecutionStepRow.__table__).where(
                ExecutionStepRow.__table__.c.billing_unit_id == unit["id"]
            )
        ).mappings().one()
        submissions = reader.execute(
            select(ExternalSubmissionRow.__table__)
        ).mappings().all()
    assert task["status"] == "processing"
    assert task["claim_token"] == claim.token
    assert unit["first_intent_at"] is None
    assert unit["deadline_at"] is None
    assert step["execution_status"] == "prepared"
    assert submissions == []
    environment.engine.dispose()


def test_unit_changed_by_another_session_after_snapshot_cannot_receive_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment, claim = _claimed_image_task(
        tmp_path, monkeypatch, "intent-unit-changed-after-snapshot"
    )
    intents = _intents(environment)

    def snapshot_reader(session, binding):
        verified = intents.snapshot_reader(
            session,
            binding,
            registry=intents.registry,
            crypto=intents.crypto,
            proof_authority=intents.proof_authority,
        )
        assert verified is not None
        with Session(environment.engine) as mutator:
            mutator.execute(
                update(BillingUnitRow.__table__)
                .where(BillingUnitRow.__table__.c.id == binding.unit_id)
                .values(result_status="failed")
            )
            mutator.commit()
        return verified

    prepared = SqlAlchemyImageSingleIntents(
        intents.session_factory,
        intents.db_clock,
        intents.id_factory,
        intents.registry,
        intents.crypto,
        intents.proof_authority,
        snapshot_reader=snapshot_reader,
    ).prepare(claim)

    assert prepared.disposition == "unresolved"
    assert prepared.intent is None
    assert prepared.verified_input is None
    with Session(environment.engine) as reader:
        unit = reader.execute(
            select(BillingUnitRow.__table__).where(
                BillingUnitRow.__table__.c.task_id == claim.scope.task_id
            )
        ).mappings().one()
        step = reader.execute(
            select(ExecutionStepRow.__table__).where(
                ExecutionStepRow.__table__.c.billing_unit_id == unit["id"]
            )
        ).mappings().one()
        submissions = reader.execute(
            select(ExternalSubmissionRow.__table__)
        ).mappings().all()
    assert unit["result_status"] == "failed"
    assert unit["first_intent_at"] is None
    assert unit["deadline_at"] is None
    assert step["execution_status"] == "prepared"
    assert submissions == []
    environment.engine.dispose()


def test_unit_changed_after_intent_commit_is_rejected_by_fresh_ack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment, claim = _claimed_image_task(
        tmp_path, monkeypatch, "intent-unit-changed-before-ack"
    )
    factory_calls = 0

    class CommitThenRefundUnit:
        def __init__(self) -> None:
            self.session = Session(environment.engine)

        def __enter__(self):
            return self.session

        def __exit__(self, exc_type, exc, traceback):
            self.session.close()
            if exc_type is not None:
                return False
            with Session(environment.engine) as mutator:
                mutator.execute(
                    update(BillingUnitRow.__table__)
                    .where(BillingUnitRow.__table__.c.task_id == claim.scope.task_id)
                    .values(billing_status="refunded")
                )
                mutator.commit()
            return False

    def session_factory():
        nonlocal factory_calls
        factory_calls += 1
        return (
            CommitThenRefundUnit()
            if factory_calls == 1
            else Session(environment.engine)
        )

    prepared = _intents(environment, session_factory=session_factory).prepare(claim)

    assert factory_calls == 2
    assert prepared.intent is None
    assert prepared.verified_input is None
    with Session(environment.engine) as reader:
        unit = reader.execute(
            select(BillingUnitRow.__table__).where(
                BillingUnitRow.__table__.c.task_id == claim.scope.task_id
            )
        ).mappings().one()
        submissions = reader.execute(
            select(ExternalSubmissionRow.__table__)
        ).mappings().all()
    assert unit["billing_status"] == "refunded"
    assert unit["first_intent_at"] is not None
    assert unit["deadline_at"] - unit["first_intent_at"] == timedelta(hours=2)
    assert len(submissions) == 1
    environment.engine.dispose()


@pytest.mark.parametrize(
    ("unit_state", "step_state"),
    [
        (unit_state, step_state)
        for unit_state in ("succeeded", "failed", "refunded")
        for step_state in ("result_observed", "applied", "failed")
    ],
)
def test_existing_intent_preserves_terminal_unit_and_step_facts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unit_state: str,
    step_state: str,
) -> None:
    environment, claim = _claimed_image_task(
        tmp_path, monkeypatch, f"intent-preserve-{unit_state}-{step_state}"
    )
    intents = _intents(environment)
    first = intents.prepare(claim)
    assert first.intent is not None

    with Session(environment.engine) as mutator:
        unit = mutator.execute(
            select(BillingUnitRow.__table__).where(
                BillingUnitRow.__table__.c.task_id == claim.scope.task_id
            )
        ).mappings().one()
        unit_values = {
            "billing_status": "refunded" if unit_state == "refunded" else "charged",
            "result_status": "succeeded" if unit_state == "succeeded" else "failed",
            "success_confirmed_at": (
                datetime(2026, 10, 16, 2, 0)
                if unit_state == "succeeded"
                else None
            ),
            "refunded_amount": (
                unit["debited_amount"] if unit_state == "refunded" else 0
            ),
        }
        mutator.execute(
            update(BillingUnitRow.__table__)
            .where(BillingUnitRow.__table__.c.id == unit["id"])
            .values(**unit_values)
        )
        mutator.execute(
            update(ExecutionStepRow.__table__)
            .where(ExecutionStepRow.__table__.c.billing_unit_id == unit["id"])
            .values(execution_status=step_state)
        )
        mutator.commit()

    before_financials = _financial_snapshot(environment, claim.scope.user_id)
    second = intents.prepare(claim)

    assert second.intent is None
    assert second.verified_input is None
    assert _financial_snapshot(environment, claim.scope.user_id) == before_financials
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow.__table__).where(
                AITaskRow.__table__.c.id == claim.scope.task_id
            )
        ).mappings().one()
        unit = reader.execute(
            select(BillingUnitRow.__table__).where(
                BillingUnitRow.__table__.c.task_id == claim.scope.task_id
            )
        ).mappings().one()
        step = reader.execute(
            select(ExecutionStepRow.__table__).where(
                ExecutionStepRow.__table__.c.billing_unit_id == unit["id"]
            )
        ).mappings().one()
        submissions = reader.execute(
            select(ExternalSubmissionRow.__table__)
        ).mappings().all()
    assert task["status"] == "reconciling"
    assert unit["billing_status"] == unit_values["billing_status"]
    assert unit["result_status"] == unit_values["result_status"]
    assert unit["refunded_amount"] == unit_values["refunded_amount"]
    assert step["execution_status"] == step_state
    assert unit["first_intent_at"].replace(tzinfo=timezone.utc) == first.intent.first_intent_at
    assert unit["deadline_at"].replace(tzinfo=timezone.utc) == first.intent.deadline_at
    assert len(submissions) == 1
    environment.engine.dispose()


def test_failed_snapshot_preserves_unit_and_step_committed_by_another_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment, claim = _claimed_image_task(
        tmp_path, monkeypatch, "intent-snapshot-preserves-unit-step"
    )
    intents = _intents(environment)

    def failed_after_unit_and_step_advance(_session, binding):
        with Session(environment.engine) as mutator:
            mutator.execute(
                update(BillingUnitRow.__table__)
                .where(BillingUnitRow.__table__.c.id == binding.unit_id)
                .values(result_status="succeeded", success_confirmed_at=datetime(2026, 10, 16, 2, 0))
            )
            mutator.execute(
                update(ExecutionStepRow.__table__)
                .where(ExecutionStepRow.__table__.c.id == binding.step_id)
                .values(execution_status="applied")
            )
            mutator.commit()
        return None

    preparation = SqlAlchemyImageSingleIntents(
        intents.session_factory,
        intents.db_clock,
        intents.id_factory,
        intents.registry,
        intents.crypto,
        intents.proof_authority,
        snapshot_reader=failed_after_unit_and_step_advance,
    ).prepare(claim)

    assert preparation.intent is None
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow.__table__).where(
                AITaskRow.__table__.c.id == claim.scope.task_id
            )
        ).mappings().one()
        unit = reader.execute(
            select(BillingUnitRow.__table__).where(
                BillingUnitRow.__table__.c.task_id == claim.scope.task_id
            )
        ).mappings().one()
        step = reader.execute(
            select(ExecutionStepRow.__table__).where(
                ExecutionStepRow.__table__.c.billing_unit_id == unit["id"]
            )
        ).mappings().one()
    assert task["status"] == "reconciling"
    assert unit["result_status"] == "succeeded"
    assert unit["success_confirmed_at"] == datetime(2026, 10, 16, 2, 0)
    assert step["execution_status"] == "applied"
    assert unit["first_intent_at"] is None
    assert unit["deadline_at"] is None
    environment.engine.dispose()


@pytest.mark.parametrize("mutation", ["task_operation", "task_protected_ref", "step_snapshot"])
def test_intent_ack_refuses_a_different_persisted_original_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    environment, claim = _claimed_image_task(
        tmp_path, monkeypatch, f"intent-ack-graph-{mutation}"
    )
    factory_calls = 0

    class CommitThenMutate:
        def __init__(self) -> None:
            self.session = Session(environment.engine)

        def __enter__(self):
            return self.session

        def __exit__(self, exc_type, exc, traceback):
            self.session.close()
            if exc_type is not None:
                return False
            with Session(environment.engine) as mutator:
                if mutation.startswith("task_"):
                    task = mutator.execute(
                        select(AITaskRow.__table__).where(
                            AITaskRow.id == claim.scope.task_id
                        )
                    ).mappings().one()
                    request_data = json.loads(task["request_data"])
                    if mutation == "task_operation":
                        request_data["operation"] = "image.batch"
                    else:
                        request_data["protected_ref"] = "changed-protected-ref"
                    mutator.execute(
                        update(AITaskRow.__table__)
                        .where(AITaskRow.id == claim.scope.task_id)
                        .values(request_data=json.dumps(request_data, sort_keys=True))
                    )
                else:
                    step = mutator.execute(
                        select(ExecutionStepRow.__table__).where(
                            ExecutionStepRow.billing_unit_id.in_(
                                select(BillingUnitRow.id).where(
                                    BillingUnitRow.task_id == claim.scope.task_id
                                )
                            )
                        )
                    ).mappings().one()
                    snapshot = dict(step["input_snapshot"])
                    snapshot["protected_ref"] = "changed-protected-ref"
                    mutator.execute(
                        update(ExecutionStepRow.__table__)
                        .where(ExecutionStepRow.id == step["id"])
                        .values(input_snapshot=snapshot)
                    )
                mutator.commit()
            return False

    def session_factory():
        nonlocal factory_calls
        factory_calls += 1
        return CommitThenMutate() if factory_calls == 1 else Session(environment.engine)

    prepared = _intents(environment, session_factory=session_factory).prepare(claim)

    assert factory_calls == 2
    assert prepared.disposition in {"existing", "unresolved"}
    assert prepared.intent is None
    assert prepared.verified_input is None
    with Session(environment.engine) as reader:
        rows = reader.execute(select(ExternalSubmissionRow.__table__)).mappings().all()
    assert len(rows) == 1
    environment.engine.dispose()


def test_failed_snapshot_reopen_does_not_overwrite_concurrent_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment, claim = _claimed_image_task(
        tmp_path, monkeypatch, "intent-cancel-on-snapshot-reopen"
    )

    def cancelled_snapshot_reader(_session, _binding):
        with Session(environment.engine) as mutator:
            mutator.execute(
                update(AITaskRow.__table__)
                .where(AITaskRow.id == claim.scope.task_id)
                .values(
                    status="cancelling",
                    user_cancelled_at=datetime(2026, 10, 16, 2, 0),
                    cancellation_reason="user_request",
                )
            )
            mutator.commit()
        return None

    intents = _intents(environment)
    intents.snapshot_reader = cancelled_snapshot_reader
    prepared = intents.prepare(claim)

    assert prepared.intent is None
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
                ExecutionStepRow.billing_unit_id == unit["id"]
            )
        ).mappings().one()
    assert task["status"] == "cancelling"
    assert task["cancellation_reason"] == "user_request"
    assert unit["result_status"] == "pending"
    assert step["execution_status"] == "prepared"
    environment.engine.dispose()


@pytest.mark.parametrize(
    ("terminal_status", "cancelled_at", "reason"),
    [("cancelling", datetime(2026, 10, 16, 2, 0), "user_request"), ("completed", None, None)],
)
def test_existing_intent_reconciling_branch_preserves_concurrent_task_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    terminal_status: str,
    cancelled_at: datetime | None,
    reason: str | None,
) -> None:
    environment, claim = _claimed_image_task(
        tmp_path, monkeypatch, f"intent-existing-race-{terminal_status}"
    )
    first = _intents(environment).prepare(claim)
    assert first.disposition == "committed"
    assert first.intent is not None
    mutated = False

    def mutate_then_clock() -> datetime:
        nonlocal mutated
        if not mutated:
            mutated = True
            with Session(environment.engine) as mutator:
                mutator.execute(
                    update(AITaskRow.__table__)
                    .where(AITaskRow.id == claim.scope.task_id)
                    .values(
                        status=terminal_status,
                        user_cancelled_at=cancelled_at,
                        cancellation_reason=reason,
                        result=("preserve-terminal-result" if terminal_status == "completed" else None),
                    )
                )
                mutator.commit()
        return datetime(2026, 10, 16, 2, 0, tzinfo=timezone.utc)

    second = _intents(environment, db_clock=mutate_then_clock).prepare(claim)

    assert second.intent is None
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
                ExecutionStepRow.billing_unit_id == unit["id"]
            )
        ).mappings().one()
    assert task["status"] == terminal_status
    assert task["cancellation_reason"] == reason
    if terminal_status == "completed":
        assert task["result"] == "preserve-terminal-result"
    assert unit["result_status"] == "pending"
    assert step["execution_status"] == "submitting"
    environment.engine.dispose()
