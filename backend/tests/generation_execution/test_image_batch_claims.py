from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from threading import Event, Thread

import pytest
from sqlalchemy import event, insert, select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_catalog.domain import ModelSource
from haoai_backend.authentication.tables import users as auth_users
from haoai_backend.generation_core.admission_receipts import (
    AdmissionConfirmed,
    AdmissionStaged,
    lookup_staged_admission,
)
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    ExecutionStepRow,
    TaskSubmissionRow,
    UserCreditRow,
)
import haoai_backend.generation_core.image_batch_claims as image_batch_claims_module
from haoai_backend.generation_core.image_batch_claims import SqlAlchemyImageBatchClaims
from generation_core.test_admission_staging import (
    _joint_caller,
    _joint_environment,
    _joint_prepare_and_stage,
    _joint_request,
)


_NOW = datetime(2026, 10, 16, 2, 0, tzinfo=timezone.utc)


def _stage_image_batch(environment, raw_key: str) -> tuple[tuple[str, ...], str]:
    caller = _joint_caller(environment.engine)
    staged = _joint_prepare_and_stage(
        environment.binding,
        caller,
        operation="image.batch",
        raw_key=raw_key,
        request=_joint_request("image.batch"),
    )
    assert isinstance(staged, AdmissionStaged)
    task_ids = tuple(task.task_id for task in staged.ordered_tasks)
    caller.commit()
    caller.close()

    with Session(environment.engine) as reader:
        confirmed = lookup_staged_admission(reader, staged)
        assert isinstance(confirmed, AdmissionConfirmed)
        submission = reader.execute(
            select(TaskSubmissionRow.__table__).where(
                TaskSubmissionRow.id == staged.submission_id
            )
        ).mappings().one()
        assert tuple(submission["task_ids"]) == task_ids
    return task_ids, staged.submission_id


def _admit_real_image_batch(tmp_path, raw_key: str = "image-batch-claim-real-producer"):
    environment = _joint_environment(
        tmp_path,
        model_source=ModelSource.SYSTEM,
        operation="image.batch",
        wallet_credits=500,
    )
    task_ids, submission_id = _stage_image_batch(environment, raw_key)
    return environment, task_ids, submission_id


def _billing_snapshot(engine):
    with Session(engine) as reader:
        balances = tuple(
            reader.execute(
                select(UserCreditRow.user_id, UserCreditRow.credits).order_by(
                    UserCreditRow.user_id
                )
            ).all()
        )
        logs = reader.execute(
            select(CreditLogRow.__table__).order_by(CreditLogRow.id)
        ).mappings().all()
        units = reader.execute(
            select(
                BillingUnitRow.id,
                BillingUnitRow.debited_amount,
                BillingUnitRow.refunded_amount,
                BillingUnitRow.billing_status,
            ).order_by(BillingUnitRow.id)
        ).all()
    return balances, tuple(tuple(row.values()) for row in logs), tuple(units)


def _claim_adapter(engine, clock, token: str) -> SqlAlchemyImageBatchClaims:
    return SqlAlchemyImageBatchClaims(
        session_factory=lambda: Session(engine),
        db_clock=lambda: clock[0],
        id_factory=lambda: token,
    )


def test_claim_next_claims_one_real_batch_item_with_shared_parent_identity(tmp_path):
    environment, task_ids, submission_id = _admit_real_image_batch(tmp_path)
    tokens = iter(("batch-claim-first", "batch-claim-second"))
    claims = SqlAlchemyImageBatchClaims(
        session_factory=lambda: Session(environment.engine),
        db_clock=lambda: _NOW,
        id_factory=lambda: next(tokens),
    )
    try:
        claim = claims.claim_next("batch-image", "batch-worker-1")

        assert claim is not None
        assert claim.scope.operation == "image.batch"
        assert claim.scope.task_id in task_ids
        assert claim.worker_instance == "batch-worker-1"
        assert claim.execution_generation == 1
        assert claim.token == "batch-claim-first"
        assert "batch-claim-first" not in repr(claim)
        with Session(environment.engine) as reader:
            task = reader.execute(
                select(AITaskRow.__table__).where(
                    AITaskRow.id == claim.scope.task_id
                )
            ).mappings().one()
            parent = reader.execute(
                select(TaskSubmissionRow.__table__).where(
                    TaskSubmissionRow.id == submission_id
                )
            ).mappings().one()
        assert task["status"] == "processing"
        assert task["claim_token"] == claim.token
        assert task["execution_generation"] == claim.execution_generation
        assert task["request_data"]
        assert tuple(parent["task_ids"]) == task_ids
    finally:
        environment.engine.dispose()


def test_batch_claims_are_per_item_and_release_preserves_sibling_lease(tmp_path):
    environment, task_ids, _ = _admit_real_image_batch(tmp_path)
    clock = [_NOW]
    first_worker = _claim_adapter(environment.engine, clock, "batch-worker-a-token")
    second_worker = _claim_adapter(environment.engine, clock, "batch-worker-b-token")
    before_billing = _billing_snapshot(environment.engine)
    try:
        first = first_worker.claim_next("batch-image", "batch-worker-a")
        assert first is not None
        assert first.scope.task_id in task_ids

        second = second_worker.claim_next("batch-image", "batch-worker-b")
        assert second is not None
        assert second.scope.task_id in task_ids
        assert second.scope.task_id != first.scope.task_id
        assert {first.scope.task_id, second.scope.task_id} == set(task_ids)
        assert first.token != second.token

        with Session(environment.engine) as reader:
            rows = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.id.in_(task_ids))
            ).mappings().all()
        by_id = {row["id"]: row for row in rows}
        assert by_id[first.scope.task_id]["claim_token"] == first.token
        assert by_id[second.scope.task_id]["claim_token"] == second.token
        assert all(row["status"] == "processing" for row in rows)

        assert first_worker.release_claim(first) is True
        with Session(environment.engine) as reader:
            released = reader.execute(
                select(AITaskRow.__table__).where(
                    AITaskRow.id == first.scope.task_id
                )
            ).mappings().one()
            sibling = reader.execute(
                select(AITaskRow.__table__).where(
                    AITaskRow.id == second.scope.task_id
                )
            ).mappings().one()
        assert released["status"] == "queued"
        assert released["claim_token"] is None
        assert sibling["status"] == "processing"
        assert sibling["claim_token"] == second.token
        assert second_worker.release_claim(second) is True
        assert _billing_snapshot(environment.engine) == before_billing
    finally:
        environment.engine.dispose()


def test_terminal_sibling_does_not_block_claiming_remaining_batch_item(tmp_path):
    environment, task_ids, _ = _admit_real_image_batch(tmp_path)
    clock = [_NOW]
    tokens = iter(("batch-terminal-first", "batch-terminal-second"))
    claims = SqlAlchemyImageBatchClaims(
        session_factory=lambda: Session(environment.engine),
        db_clock=lambda: clock[0],
        id_factory=lambda: next(tokens),
    )
    try:
        first = claims.claim_next("batch-image", "batch-worker-a")
        assert first is not None
        with Session(environment.engine) as mutator:
            mutator.execute(
                update(AITaskRow.__table__)
                .where(AITaskRow.id == first.scope.task_id)
                .values(status="completed", result="fixture terminal result")
            )
            unit_id = mutator.execute(
                select(BillingUnitRow.id).where(
                    BillingUnitRow.task_id == first.scope.task_id
                )
            ).scalar_one()
            mutator.execute(
                update(BillingUnitRow.__table__)
                .where(BillingUnitRow.id == unit_id)
                .values(result_status="succeeded")
            )
            mutator.execute(
                update(ExecutionStepRow.__table__)
                .where(ExecutionStepRow.billing_unit_id == unit_id)
                .values(execution_status="applied")
            )
            mutator.commit()

        remaining = claims.claim_next("batch-image", "batch-worker-b")
        assert remaining is not None
        assert remaining.scope.task_id in task_ids
        assert remaining.scope.task_id != first.scope.task_id
        assert claims.release_claim(remaining) is True
    finally:
        environment.engine.dispose()


@pytest.mark.parametrize(
    "corruption",
    (
        "parent_operation",
        "parent_owner_mismatch",
        "duplicate_parent_id",
        "missing_parent_task",
        "swapped_item_request_data",
        "wrong_item_unit_mapping",
        "wrong_graph_version",
        "multiple_units",
        "multiple_steps",
        "different_sibling_manifest",
        "wrong_sibling_task_type",
    ),
)
def test_claim_rejects_incomplete_or_misordered_batch_identity(tmp_path, corruption):
    environment, task_ids, submission_id = _admit_real_image_batch(
        tmp_path, f"image-batch-invalid-{corruption}"
    )
    with Session(environment.engine) as mutator:
        if corruption == "parent_operation":
            mutator.execute(
                update(TaskSubmissionRow.__table__)
                .where(TaskSubmissionRow.id == submission_id)
                .values(operation="image.single")
            )
        elif corruption == "parent_owner_mismatch":
            mutator.execute(
                insert(auth_users).values(
                    id="foreign-batch-owner",
                    username="foreign-batch-owner",
                    email="foreign-batch-owner@example.test",
                    hashed_password="fixture-only",
                    is_superuser=False,
                    membership_type="free",
                    created_at=_NOW.replace(tzinfo=None),
                )
            )
            mutator.execute(
                update(TaskSubmissionRow.__table__)
                .where(TaskSubmissionRow.id == submission_id)
                .values(user_id="foreign-batch-owner")
            )
        elif corruption == "duplicate_parent_id":
            mutator.execute(
                update(TaskSubmissionRow.__table__)
                .where(TaskSubmissionRow.id == submission_id)
                .values(task_ids=[task_ids[0], task_ids[0]])
            )
        elif corruption == "missing_parent_task":
            mutator.execute(
                update(TaskSubmissionRow.__table__)
                .where(TaskSubmissionRow.id == submission_id)
                .values(task_ids=[task_ids[0], "missing-batch-child"])
            )
        elif corruption == "swapped_item_request_data":
            request_data = dict(
                mutator.execute(
                    select(AITaskRow.id, AITaskRow.request_data).where(
                        AITaskRow.id.in_(task_ids)
                    )
                ).all()
            )
            mutator.execute(
                update(AITaskRow.__table__)
                .where(AITaskRow.id == task_ids[0])
                .values(request_data=request_data[task_ids[1]])
            )
            mutator.execute(
                update(AITaskRow.__table__)
                .where(AITaskRow.id == task_ids[1])
                .values(request_data=request_data[task_ids[0]])
            )
        elif corruption == "wrong_item_unit_mapping":
            wrong_unit_id = mutator.execute(
                select(BillingUnitRow.id).where(
                    BillingUnitRow.task_id == task_ids[1]
                )
            ).scalar_one()
            request_data = json.loads(
                mutator.execute(
                    select(AITaskRow.request_data).where(
                        AITaskRow.id == task_ids[0]
                    )
                ).scalar_one()
            )
            request_data["billing_unit_id"] = wrong_unit_id
            mutator.execute(
                update(AITaskRow.__table__)
                .where(AITaskRow.id == task_ids[0])
                .values(request_data=json.dumps(request_data, sort_keys=True))
            )
        elif corruption == "wrong_graph_version":
            mutator.execute(
                update(BillingUnitRow.__table__)
                .where(BillingUnitRow.task_id == task_ids[0])
                .values(step_graph_version="image-single-v1")
            )
        elif corruption == "multiple_units":
            unit = dict(
                mutator.execute(
                    select(BillingUnitRow.__table__).where(
                        BillingUnitRow.task_id == task_ids[0]
                    )
                ).mappings().one()
            )
            unit.update(id="extra-batch-unit", item_key=f"{unit['item_key']}-extra")
            mutator.execute(insert(BillingUnitRow.__table__).values(unit))
        elif corruption == "multiple_steps":
            step = dict(
                mutator.execute(
                    select(ExecutionStepRow.__table__).where(
                        ExecutionStepRow.billing_unit_id.in_(
                            select(BillingUnitRow.id).where(
                                BillingUnitRow.task_id == task_ids[0]
                            )
                        )
                    )
                ).mappings().one()
            )
            step.update(id="extra-batch-step", step_key="extra-generate", step_order=1)
            mutator.execute(insert(ExecutionStepRow.__table__).values(step))
        elif corruption == "different_sibling_manifest":
            unit_id = mutator.execute(
                select(BillingUnitRow.id).where(
                    BillingUnitRow.task_id == task_ids[1]
                )
            ).scalar_one()
            step = dict(
                mutator.execute(
                    select(ExecutionStepRow.__table__).where(
                        ExecutionStepRow.billing_unit_id == unit_id
                    )
                ).mappings().one()
            )
            snapshot = dict(step["input_snapshot"])
            snapshot["ordered_manifest_digest"] = "f" * 64
            mutator.execute(
                update(ExecutionStepRow.__table__)
                .where(ExecutionStepRow.__table__.c.id == step["id"])
                .values(input_snapshot=snapshot)
            )
        else:
            mutator.execute(
                update(AITaskRow.__table__)
                .where(AITaskRow.id == task_ids[1])
                .values(type="image-batch")
            )
        mutator.commit()

    claims = _claim_adapter(environment.engine, [_NOW], f"invalid-{corruption}-token")
    try:
        assert claims.claim_next("batch-image", "batch-invalid-worker") is None
        with Session(environment.engine) as reader:
            rows = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.id.in_(task_ids))
            ).mappings().all()
        assert all(row["claim_token"] is None for row in rows)
    finally:
        environment.engine.dispose()


def test_unregistered_task_family_is_rejected_before_factories(tmp_path):
    environment, _, _ = _admit_real_image_batch(tmp_path)
    calls = {"session": 0, "clock": 0, "id": 0}

    def session_factory():
        calls["session"] += 1
        return Session(environment.engine)

    def db_clock():
        calls["clock"] += 1
        return _NOW

    def id_factory():
        calls["id"] += 1
        return "should-not-exist"

    claims = SqlAlchemyImageBatchClaims(session_factory, db_clock, id_factory)
    try:
        assert claims.claim_next("image-single", "batch-worker") is None
        assert calls == {"session": 0, "clock": 0, "id": 0}
    finally:
        environment.engine.dispose()


@pytest.mark.parametrize(
    "blocked_state",
    (
        "cancelled_task",
        "terminal_task",
        "cancelled_unit",
        "intent_started",
        "deadline_present",
        "step_started",
    ),
)
def test_cancelled_terminal_or_started_item_is_not_claimed(tmp_path, blocked_state):
    environment, task_ids, _ = _admit_real_image_batch(
        tmp_path, f"image-batch-blocked-{blocked_state}"
    )
    target_task_id = task_ids[0]
    cancellation_time = _NOW.replace(tzinfo=None)
    with Session(environment.engine) as mutator:
        mutator.execute(
            update(AITaskRow.__table__)
            .where(AITaskRow.id == target_task_id)
            .values(created_at=(_NOW - timedelta(seconds=1)).replace(tzinfo=None))
        )
        unit_id = mutator.execute(
            select(BillingUnitRow.id).where(
                BillingUnitRow.task_id == target_task_id
            )
        ).scalar_one()
        if blocked_state == "cancelled_task":
            mutator.execute(
                update(AITaskRow.__table__)
                .where(AITaskRow.id == target_task_id)
                .values(
                    status="cancelling",
                    user_cancelled_at=cancellation_time,
                    cancellation_reason="user_request",
                )
            )
        elif blocked_state == "terminal_task":
            mutator.execute(
                update(AITaskRow.__table__)
                .where(AITaskRow.id == target_task_id)
                .values(status="failed", result="terminal fixture")
            )
        elif blocked_state == "cancelled_unit":
            mutator.execute(
                update(BillingUnitRow.__table__)
                .where(BillingUnitRow.id == unit_id)
                .values(user_cancelled_at=cancellation_time, cancellation_reason="user_request")
            )
        elif blocked_state == "intent_started":
            mutator.execute(
                update(BillingUnitRow.__table__)
                .where(BillingUnitRow.id == unit_id)
                .values(first_intent_at=cancellation_time, deadline_at=cancellation_time + timedelta(minutes=1))
            )
        elif blocked_state == "deadline_present":
            mutator.execute(
                update(BillingUnitRow.__table__)
                .where(BillingUnitRow.id == unit_id)
                .values(deadline_at=cancellation_time - timedelta(seconds=1))
            )
        else:
            mutator.execute(
                update(ExecutionStepRow.__table__)
                .where(ExecutionStepRow.billing_unit_id == unit_id)
                .values(execution_status="submitting")
            )
        mutator.commit()

    claims = _claim_adapter(environment.engine, [_NOW], f"blocked-{blocked_state}-token")
    try:
        claim = claims.claim_next("batch-image", "batch-worker")
        assert claim is not None
        assert claim.scope.task_id != target_task_id
        with Session(environment.engine) as reader:
            target = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == target_task_id)
            ).mappings().one()
        assert target["claim_token"] is None
    finally:
        environment.engine.dispose()


def test_expired_lease_reclaim_fences_old_token_from_renew_and_release(tmp_path):
    environment, task_ids, _ = _admit_real_image_batch(tmp_path)
    clock = [_NOW]
    original_adapter = _claim_adapter(environment.engine, clock, "batch-expired-old-token")
    try:
        original = original_adapter.claim_next("batch-image", "batch-worker-old")
        assert original is not None
        clock[0] += timedelta(seconds=61)
        new_adapter = _claim_adapter(environment.engine, clock, "batch-expired-new-token")
        replacement = new_adapter.claim_next("batch-image", "batch-worker-new")
        assert replacement is not None
        assert replacement.scope.task_id == original.scope.task_id
        assert replacement.scope.task_id in task_ids
        assert replacement.execution_generation == original.execution_generation + 1

        assert original_adapter.renew_or_revoke(original) == "ownership_lost"
        assert original_adapter.release_claim(original) is False
        with Session(environment.engine) as reader:
            row = reader.execute(
                select(AITaskRow.__table__).where(
                    AITaskRow.id == replacement.scope.task_id
                )
            ).mappings().one()
        assert row["claim_token"] == replacement.token
        assert row["execution_generation"] == replacement.execution_generation
        assert row["claimed_by"] == replacement.worker_instance
    finally:
        environment.engine.dispose()


@pytest.mark.parametrize("leading_state", ("live", "invalid"))
def test_claim_scans_past_first_page_to_later_batch_candidate(
    monkeypatch, tmp_path, leading_state
):
    environment = _joint_environment(
        tmp_path,
        model_source=ModelSource.SYSTEM,
        operation="image.batch",
        wallet_credits=100,
    )
    first_ids, _ = _stage_image_batch(environment, f"image-batch-first-{leading_state}")
    later_ids, _ = _stage_image_batch(environment, f"image-batch-later-{leading_state}")
    with Session(environment.engine) as mutator:
        mutator.execute(
            update(AITaskRow.__table__)
            .where(AITaskRow.id.in_(first_ids))
            .values(created_at=(_NOW - timedelta(minutes=1)).replace(tzinfo=None))
        )
        mutator.execute(
            update(AITaskRow.__table__)
            .where(AITaskRow.id.in_(later_ids))
            .values(created_at=_NOW.replace(tzinfo=None))
        )
        if leading_state == "live":
            mutator.execute(
                update(AITaskRow.__table__)
                .where(AITaskRow.id.in_(first_ids))
                .values(
                    status="processing",
                    claimed_by="another-worker",
                    claim_token="existing-live-batch-claim",
                    execution_generation=1,
                    lease_until=(_NOW + timedelta(minutes=1)).replace(tzinfo=None),
                )
            )
        else:
            unit_id = mutator.execute(
                select(BillingUnitRow.id).where(
                    BillingUnitRow.task_id == first_ids[0]
                )
            ).scalar_one()
            mutator.execute(
                update(ExecutionStepRow.__table__)
                .where(ExecutionStepRow.billing_unit_id == unit_id)
                .values(step_key="unsupported-legacy-step")
            )
        mutator.commit()

    monkeypatch.setattr(image_batch_claims_module, "_CANDIDATE_PAGE_SIZE", 1)
    claims = _claim_adapter(environment.engine, [_NOW], f"after-first-page-{leading_state}")
    try:
        claim = claims.claim_next("batch-image", "batch-worker-later")
        assert claim is not None
        assert claim.scope.task_id in later_ids
    finally:
        environment.engine.dispose()


@pytest.mark.parametrize("change", ("cancel", "terminal", "request_data", "token_generation"))
def test_sqlite_two_connection_claim_cas_preserves_concurrent_task_change(
    tmp_path, change
):
    environment, task_ids, _ = _admit_real_image_batch(
        tmp_path, f"image-batch-sqlite-cas-{change}"
    )
    engine = environment.engine
    target_task_id = task_ids[0]
    with Session(engine) as mutator:
        mutator.execute(
            update(AITaskRow.__table__)
            .where(AITaskRow.id == target_task_id)
            .values(created_at=(_NOW - timedelta(seconds=1)).replace(tzinfo=None))
        )
        mutator.commit()

    entered_update = Event()
    resume_update = Event()
    result = []
    errors = []
    claims = _claim_adapter(engine, [_NOW], f"sqlite-batch-cas-{change}")

    def pause_before_update(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("UPDATE AI_TASKS SET") and not entered_update.is_set():
            entered_update.set()
            if not resume_update.wait(10):
                raise TimeoutError("test did not release gated batch claim update")

    def run_claim():
        try:
            result.append(claims.claim_next("batch-image", "batch-cas-worker"))
        except BaseException as exc:
            errors.append(exc)

    event.listen(engine, "before_cursor_execute", pause_before_update)
    worker = Thread(target=run_claim, daemon=True)
    worker.start()
    try:
        assert entered_update.wait(10)
        with Session(engine) as mutator:
            if change == "cancel":
                mutator.execute(
                    update(AITaskRow.__table__)
                    .where(AITaskRow.id == target_task_id)
                    .values(
                        status="cancelling",
                        user_cancelled_at=_NOW.replace(tzinfo=None),
                        cancellation_reason="concurrent-cancel",
                    )
                )
            elif change == "terminal":
                mutator.execute(
                    update(AITaskRow.__table__)
                    .where(AITaskRow.id == target_task_id)
                    .values(status="completed", result="concurrent-result")
                )
            elif change == "request_data":
                current = json.loads(
                    mutator.execute(
                        select(AITaskRow.request_data).where(
                            AITaskRow.id == target_task_id
                        )
                    ).scalar_one()
                )
                current["test_concurrent_change"] = True
                mutator.execute(
                    update(AITaskRow.__table__)
                    .where(AITaskRow.id == target_task_id)
                    .values(request_data=json.dumps(current, sort_keys=True))
                )
            else:
                mutator.execute(
                    update(AITaskRow.__table__)
                    .where(AITaskRow.id == target_task_id)
                    .values(
                        claimed_by="concurrent-worker",
                        claim_token="concurrent-token",
                        execution_generation=1,
                        lease_until=(_NOW + timedelta(minutes=1)).replace(tzinfo=None),
                    )
                )
            mutator.commit()
        resume_update.set()
        worker.join(10)
        assert not worker.is_alive()
        assert errors == []
        claim = result[0]
        assert claim is None or claim.scope.task_id != target_task_id, (
            f"concurrent {change} on {target_task_id} must fence that item; "
            f"observed claim={claim.scope.task_id if claim is not None else None}"
        )
        with Session(engine) as reader:
            stored = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == target_task_id)
            ).mappings().one()
        if change == "cancel":
            assert stored["status"] == "cancelling"
            assert stored["cancellation_reason"] == "concurrent-cancel"
        elif change == "terminal":
            assert stored["status"] == "completed"
            assert stored["result"] == "concurrent-result"
        elif change == "request_data":
            assert json.loads(stored["request_data"])["test_concurrent_change"] is True
        else:
            assert stored["claim_token"] == "concurrent-token"
            assert stored["execution_generation"] == 1
    finally:
        resume_update.set()
        worker.join(10)
        event.remove(engine, "before_cursor_execute", pause_before_update)
        engine.dispose()


def test_unknown_claim_commit_is_confirmed_only_by_exact_new_batch_lease(tmp_path):
    environment, task_ids, _ = _admit_real_image_batch(tmp_path)
    state = {"lose_ack": True}
    claims = _claim_adapter(environment.engine, [_NOW], "batch-unknown-ack-token")

    def lose_claim_acknowledgement(session):
        if session.bind is environment.engine and state["lose_ack"]:
            state["lose_ack"] = False
            raise RuntimeError("fixture lost committed batch claim acknowledgement")

    event.listen(Session, "after_commit", lose_claim_acknowledgement)
    try:
        claim = claims.claim_next("batch-image", "batch-unknown-worker")
    finally:
        event.remove(Session, "after_commit", lose_claim_acknowledgement)

    try:
        assert not state["lose_ack"]
        assert claim is not None
        assert claim.scope.task_id in task_ids
        with Session(environment.engine) as reader:
            stored = reader.execute(
                select(AITaskRow.__table__).where(
                    AITaskRow.id == claim.scope.task_id
                )
            ).mappings().one()
        assert stored["status"] == "processing"
        assert stored["claim_token"] == claim.token
        assert stored["execution_generation"] == claim.execution_generation
    finally:
        environment.engine.dispose()


@pytest.mark.parametrize("change", ("cancel", "token"))
def test_unknown_claim_ack_rejects_postcommit_changed_owner_lease(tmp_path, change):
    environment, task_ids, _ = _admit_real_image_batch(
        tmp_path, f"image-batch-unknown-after-{change}"
    )
    state = {"mutate": True}
    claims = _claim_adapter(environment.engine, [_NOW], f"batch-unknown-{change}-token")

    def mutate_after_commit(session):
        if session.bind is not environment.engine or not state["mutate"]:
            return
        state["mutate"] = False
        with Session(environment.engine) as mutator:
            if change == "cancel":
                mutator.execute(
                    update(AITaskRow.__table__)
                    .where(AITaskRow.id.in_(task_ids))
                    .values(
                        status="cancelling",
                        user_cancelled_at=_NOW.replace(tzinfo=None),
                        cancellation_reason="postcommit-cancel",
                    )
                )
            else:
                mutator.execute(
                    update(AITaskRow.__table__)
                    .where(AITaskRow.id.in_(task_ids))
                    .values(claim_token="postcommit-replaced-token")
                )
            mutator.commit()
        raise RuntimeError("claim committed but acknowledgement followed mutation")

    event.listen(Session, "after_commit", mutate_after_commit)
    try:
        claim = claims.claim_next("batch-image", "batch-unknown-worker")
    finally:
        event.remove(Session, "after_commit", mutate_after_commit)

    try:
        assert not state["mutate"]
        assert claim is None
        with Session(environment.engine) as reader:
            rows = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.id.in_(task_ids))
            ).mappings().all()
        if change == "cancel":
            assert all(row["status"] == "cancelling" for row in rows)
        else:
            assert all(row["claim_token"] == "postcommit-replaced-token" for row in rows)
    finally:
        environment.engine.dispose()


def test_existing_token_row_is_not_misreported_as_a_new_claim(tmp_path):
    environment, task_ids, _ = _admit_real_image_batch(tmp_path)
    with Session(environment.engine) as mutator:
        mutator.execute(
            update(AITaskRow.__table__)
            .where(AITaskRow.id == task_ids[0])
            .values(
                status="processing",
                claimed_by="old-worker",
                claim_token="preexisting-batch-token",
                execution_generation=3,
                lease_until=(_NOW + timedelta(minutes=1)).replace(tzinfo=None),
            )
        )
        mutator.commit()
    claims = _claim_adapter(environment.engine, [_NOW], "preexisting-batch-token")
    try:
        assert claims.claim_next("batch-image", "batch-new-worker") is None
        with Session(environment.engine) as reader:
            rows = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.id.in_(task_ids))
            ).mappings().all()
        old = next(row for row in rows if row["claim_token"] == "preexisting-batch-token")
        assert old["claimed_by"] == "old-worker"
        assert old["execution_generation"] == 3
        assert all(
            row["claim_token"] in (None, "preexisting-batch-token") for row in rows
        )
    finally:
        environment.engine.dispose()


def test_claim_rejects_parent_order_that_disagrees_with_producer_child_slots(
    tmp_path, request
):
    environment, task_ids, submission_id = _admit_real_image_batch(
        tmp_path, "image-batch-parent-order-reversed"
    )
    request.addfinalizer(environment.engine.dispose)
    with Session(environment.engine) as mutator:
        mutator.execute(
            update(TaskSubmissionRow.__table__)
            .where(TaskSubmissionRow.id == submission_id)
            .values(task_ids=list(reversed(task_ids)))
        )
        mutator.commit()

    claims = _claim_adapter(environment.engine, [_NOW], "reversed-parent-token")
    claim = claims.claim_next("batch-image", "batch-order-worker")
    assert claim is None
    with Session(environment.engine) as reader:
        rows = reader.execute(
            select(AITaskRow.__table__)
            .where(AITaskRow.id.in_(task_ids))
            .order_by(AITaskRow.id)
        ).mappings().all()
    assert all(row["status"] == "queued" for row in rows)
    assert all(row["claim_token"] is None for row in rows)


@pytest.mark.parametrize(
    "state",
    ("cancelled", "terminal", "expired_lost_signal", "intent_progressed"),
)
def test_exact_owner_can_renew_or_release_after_lifecycle_state_change(
    tmp_path, state, request
):
    environment, task_ids, _submission_id = _admit_real_image_batch(
        tmp_path, f"image-batch-owner-unwind-{state}"
    )
    request.addfinalizer(environment.engine.dispose)
    clock = [_NOW]
    claims = _claim_adapter(environment.engine, clock, f"batch-unwind-{state}-token")
    claim = claims.claim_next("batch-image", f"batch-unwind-{state}-worker")
    assert claim is not None
    target_task_id = claim.scope.task_id
    sibling_task_id = next(task_id for task_id in task_ids if task_id != target_task_id)

    cancellation_stamp = _NOW.replace(tzinfo=None)
    with Session(environment.engine) as mutator:
        if state == "cancelled":
            mutator.execute(
                update(AITaskRow.__table__)
                .where(AITaskRow.id == target_task_id)
                .values(
                    status="cancelling",
                    user_cancelled_at=cancellation_stamp,
                    cancellation_reason="user_request",
                    result="cancelled-result-preserved",
                )
            )
        elif state == "terminal":
            mutator.execute(
                update(AITaskRow.__table__)
                .where(AITaskRow.id == target_task_id)
                .values(status="failed", result="terminal-result-preserved")
            )
        elif state == "intent_progressed":
            unit_id = mutator.execute(
                select(BillingUnitRow.id).where(
                    BillingUnitRow.task_id == target_task_id
                )
            ).scalar_one()
            mutator.execute(
                update(BillingUnitRow.__table__)
                .where(BillingUnitRow.id == unit_id)
                .values(
                    first_intent_at=cancellation_stamp,
                    deadline_at=cancellation_stamp + timedelta(hours=2),
                )
            )
            mutator.execute(
                update(ExecutionStepRow.__table__)
                .where(ExecutionStepRow.billing_unit_id == unit_id)
                .values(execution_status="submitting")
            )
        mutator.commit()

    before_financial = _billing_snapshot(environment.engine)
    with Session(environment.engine) as reader:
        before_task = reader.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == target_task_id)
        ).mappings().one()
        unit_id = reader.execute(
            select(BillingUnitRow.id).where(BillingUnitRow.task_id == target_task_id)
        ).scalar_one()
        before_unit = reader.execute(
            select(
                BillingUnitRow.__table__.c.first_intent_at,
                BillingUnitRow.__table__.c.deadline_at,
                BillingUnitRow.__table__.c.user_cancelled_at,
                BillingUnitRow.__table__.c.cancellation_reason,
                BillingUnitRow.__table__.c.result_status,
            ).where(BillingUnitRow.id == unit_id)
        ).one()
        before_step_status = reader.execute(
            select(ExecutionStepRow.__table__.c.execution_status).where(
                ExecutionStepRow.__table__.c.billing_unit_id == unit_id
            )
        ).scalar_one()

    if state == "expired_lost_signal":
        clock[0] += timedelta(seconds=61)
        assert claims.renew_or_revoke(claim) == "ownership_lost"
        assert claim.local_lost_signal.is_set()
    elif state in {"cancelled", "terminal"}:
        assert claims.renew_or_revoke(claim) == "ownership_lost"
        assert claim.local_lost_signal.is_set()
    else:
        clock[0] += timedelta(seconds=10)
        assert claims.renew_or_revoke(claim) == "renewed"
        assert not claim.local_lost_signal.is_set()

    assert claims.release_claim(claim) is True
    with Session(environment.engine) as reader:
        after_task = reader.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == target_task_id)
        ).mappings().one()
        after_unit = reader.execute(
            select(
                BillingUnitRow.__table__.c.first_intent_at,
                BillingUnitRow.__table__.c.deadline_at,
                BillingUnitRow.__table__.c.user_cancelled_at,
                BillingUnitRow.__table__.c.cancellation_reason,
                BillingUnitRow.__table__.c.result_status,
            ).where(BillingUnitRow.id == unit_id)
        ).one()
        after_step_status = reader.execute(
            select(ExecutionStepRow.__table__.c.execution_status).where(
                ExecutionStepRow.__table__.c.billing_unit_id == unit_id
            )
        ).scalar_one()
        sibling = reader.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == sibling_task_id)
        ).mappings().one()

    expected_status = "cancelling" if state == "cancelled" else (
        "failed" if state == "terminal" else "queued"
    )
    assert after_task["status"] == expected_status
    assert after_task["claimed_by"] is None
    assert after_task["claim_token"] is None
    assert after_task["lease_until"] is None
    assert after_task["execution_generation"] == claim.execution_generation
    assert after_task["user_cancelled_at"] == before_task["user_cancelled_at"]
    assert after_task["cancellation_reason"] == before_task["cancellation_reason"]
    assert after_task["result"] == before_task["result"]
    assert tuple(after_unit) == tuple(before_unit)
    assert after_step_status == before_step_status
    assert sibling["status"] == "queued"
    assert sibling["claim_token"] is None
    assert _billing_snapshot(environment.engine) == before_financial


def test_ordinary_owned_claim_renewal_succeeds_without_financial_mutation(
    tmp_path, request
):
    environment, task_ids, _submission_id = _admit_real_image_batch(
        tmp_path, "image-batch-ordinary-renewal"
    )
    request.addfinalizer(environment.engine.dispose)
    clock = [_NOW]
    claims = _claim_adapter(environment.engine, clock, "batch-ordinary-renew-token")
    before_financial = _billing_snapshot(environment.engine)
    claim = claims.claim_next("batch-image", "batch-ordinary-renew-worker")
    assert claim is not None
    clock[0] += timedelta(seconds=10)

    assert claims.renew_or_revoke(claim) == "renewed"
    with Session(environment.engine) as reader:
        stored = reader.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == claim.scope.task_id)
        ).mappings().one()
        sibling_task_id = next(
            task_id for task_id in task_ids if task_id != claim.scope.task_id
        )
        sibling = reader.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == sibling_task_id)
        ).mappings().one()
    assert stored["lease_until"] == (clock[0] + timedelta(seconds=60)).replace(
        tzinfo=None
    )
    assert stored["execution_generation"] == claim.execution_generation
    assert not claim.local_lost_signal.is_set()
    assert sibling["status"] == "queued"
    assert sibling["claim_token"] is None
    assert _billing_snapshot(environment.engine) == before_financial
