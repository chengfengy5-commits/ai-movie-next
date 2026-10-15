from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import select

from haoai_backend.authentication.tables import user_credits
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
)
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.storyboard_generation.two_pass_billing import (
    TwoPassBillingService,
    TwoPassQuoteRequest,
)
from haoai_backend.generation_core.base_schema import AITaskRow, CreditLogRow
from haoai_backend.authentication.tables import user_credits
from haoai_backend.storyboard_generation.two_pass_billing import OPERATION

from two_pass_test_support import build_two_pass_system


def test_concurrent_same_key_admission_creates_one_task_and_one_debit(tmp_path) -> None:
    system = build_two_pass_system(tmp_path / "two-pass-admission-race.sqlite", multiplier=2)
    actor = TrustedActor("member")
    billing = TwoPassBillingService(system.session_factory, now=lambda: system.now)
    quote = billing.create_quote(
        actor,
        TwoPassQuoteRequest(
            series_id="series-member",
            raw_content="synthetic source",
            model_id="system-text-model",
            scene_prompt_id="scene-prompt",
            frame_prompt_id="frame-prompt",
        ),
    )
    barrier = Barrier(2)

    def admit():
        barrier.wait(timeout=5)
        return billing.admit(actor, quote["quote_id"], "same-key-concurrent")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(admit)
        second = pool.submit(admit)
        admissions = [first.result(timeout=10), second.result(timeout=10)]

    assert sorted(admission.kind for admission in admissions) == ["new", "pending"]
    assert admissions[0].receipt["task_id"] == admissions[1].receipt["task_id"]
    with system.session_factory() as session:
        tasks = session.execute(select(AITaskRow)).scalars().all()
        debits = session.execute(
            select(CreditLogRow).where(CreditLogRow.type == "usage")
        ).scalars().all()
        balance = session.execute(
            select(user_credits.c.credits).where(user_credits.c.user_id == actor.user_id)
        ).scalar_one()
    assert len(tasks) == 1
    assert len(debits) == 1
    assert balance == 44
    system.engine.dispose()


def test_concurrent_success_and_failure_only_commit_one_terminal_outcome(tmp_path) -> None:
    system = build_two_pass_system(tmp_path / "two-pass-settlement-race.sqlite", multiplier=2)
    actor = TrustedActor("member")
    billing = TwoPassBillingService(system.session_factory, now=lambda: system.now)
    quote = billing.create_quote(
        actor,
        TwoPassQuoteRequest(
            series_id="series-member",
            raw_content="synthetic source",
            model_id="system-text-model",
            scene_prompt_id="scene-prompt",
            frame_prompt_id="frame-prompt",
        ),
    )
    admission = billing.admit(actor, quote["quote_id"], "race-settlement")
    assert admission.execution is not None
    execution = admission.execution
    assert quote["quoted_amount"] == 6

    barrier = Barrier(2)

    def complete() -> bool:
        barrier.wait(timeout=5)
        return billing.mark_completed(
            execution,
            {
                "frames": [{"text": "synthetic frame"}],
                "segments": [{"index": 0, "text": "synthetic scene"}],
            },
        )

    def fail() -> bool:
        barrier.wait(timeout=5)
        return billing.mark_failed(execution, "synthetic failure")

    with ThreadPoolExecutor(max_workers=2) as pool:
        completed = pool.submit(complete)
        failed = pool.submit(fail)
        outcomes = (completed.result(timeout=10), failed.result(timeout=10))

    assert sum(outcomes) == 1

    with system.session_factory() as session:
        task = session.execute(
            select(AITaskRow).where(AITaskRow.id == execution.task_id)
        ).scalar_one()
        unit = session.execute(
            select(BillingUnitRow).where(BillingUnitRow.id == execution.billing_unit_id)
        ).scalar_one()
        balance = session.execute(
            select(user_credits.c.credits).where(user_credits.c.user_id == actor.user_id)
        ).scalar_one()
        refunds = session.execute(
            select(CreditLogRow).where(
                CreditLogRow.billing_unit_id == execution.billing_unit_id,
                CreditLogRow.type == "refund",
            )
        ).scalars().all()
        submissions = session.execute(
            select(CreditLogRow).where(
                CreditLogRow.billing_unit_id == execution.billing_unit_id,
                CreditLogRow.type == "usage",
            )
        ).scalars().all()

    assert len(submissions) == 1
    assert task.status in {"completed", "failed"}
    assert unit.result_status == ("succeeded" if task.status == "completed" else "failed")
    assert task.billing_status == unit.billing_status
    assert unit.operation == OPERATION
    if task.status == "completed":
        assert task.billing_status == "charged"
        assert unit.refunded_amount == 0
        assert refunds == []
        assert balance == 44
    else:
        assert task.billing_status == "refunded"
        assert unit.refunded_amount == 6
        assert len(refunds) == 1
        assert balance == 50
