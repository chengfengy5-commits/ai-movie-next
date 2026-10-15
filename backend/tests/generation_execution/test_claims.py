from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event, Thread

import pytest
from sqlalchemy import event, insert, select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_catalog.domain import ModelSource
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    ExecutionStepRow,
    UserCreditRow,
)
from haoai_backend.authentication.tables import users as auth_users
from haoai_backend.generation_core.claims import SqlAlchemyImageSingleClaims
from haoai_backend.generation_core.admission_receipts import AdmissionStaged
from test_admission_staging import _joint_caller, _joint_environment, _joint_prepare_and_stage


def _admit_image_task(tmp_path: Path, raw_key: str = "claim-test-key"):
    environment = _joint_environment(
        tmp_path,
        model_source=ModelSource.SYSTEM,
        operation="image.single",
        wallet_credits=20,
    )
    caller = _joint_caller(environment.engine)
    staged = _joint_prepare_and_stage(
        environment.binding,
        caller,
        operation="image.single",
        raw_key=raw_key,
        request={
            "model_id": "model-1",
            "prompt": "Claim graph",
            "target": {"kind": "standalone"},
            "size": None,
            "reference_images": [],
        },
    )
    assert isinstance(staged, AdmissionStaged)
    task_id = staged.ordered_tasks[0].task_id
    caller.commit()
    caller.close()
    return environment, task_id


def _admit_image_tasks(tmp_path: Path, count: int):
    environment = _joint_environment(
        tmp_path,
        model_source=ModelSource.SYSTEM,
        operation="image.single",
        wallet_credits=1000,
    )
    caller = _joint_caller(environment.engine)
    task_ids: list[str] = []
    for index in range(count):
        staged = _joint_prepare_and_stage(
            environment.binding,
            caller,
            operation="image.single",
            raw_key=f"claim-page-{index}",
            request={
                "model_id": "model-1",
                "prompt": f"Claim graph {index}",
                "target": {"kind": "standalone"},
                "size": None,
                "reference_images": [],
            },
        )
        assert isinstance(staged, AdmissionStaged)
        task_ids.extend(task.task_id for task in staged.ordered_tasks)
    caller.commit()
    caller.close()
    return environment, task_ids


def _financial_snapshot(engine):
    with Session(engine) as reader:
        credits = tuple(
            (row.user_id, row.credits)
            for row in reader.execute(
                select(UserCreditRow.user_id, UserCreditRow.credits).order_by(
                    UserCreditRow.user_id
                )
            )
        )
        logs = reader.execute(
            select(CreditLogRow.__table__).order_by(CreditLogRow.id)
        ).mappings().all()
        units = reader.execute(
            select(
                BillingUnitRow.__table__.c.id,
                BillingUnitRow.__table__.c.debited_amount,
                BillingUnitRow.__table__.c.refunded_amount,
                BillingUnitRow.__table__.c.billing_status,
            ).order_by(BillingUnitRow.id)
        ).all()
    log_rows = tuple(
        tuple(row[column.name] for column in CreditLogRow.__table__.columns)
        for row in logs
    )
    return credits, log_rows, tuple(tuple(row) for row in units)


def test_claim_next_commits_a_fenced_lease_and_renewal_uses_fresh_db_time(
    tmp_path: Path,
) -> None:
    environment, task_id = _admit_image_task(tmp_path)
    engine = environment.engine
    now = [datetime(2026, 10, 16, 2, 0, tzinfo=timezone.utc)]
    ids = iter(("claim-token-1", "claim-token-2"))
    claims = SqlAlchemyImageSingleClaims(
        session_factory=lambda: Session(engine),
        db_clock=lambda: now[0],
        id_factory=lambda: next(ids),
    )
    try:
        claim = claims.claim_next("image-single", "worker-1")
        assert claim is not None
        assert claim.scope.task_id == task_id
        assert claim.scope.operation == "image.single"
        assert claim.worker_instance == "worker-1"
        assert claim.execution_generation == 1
        assert claim.lease_until == now[0] + timedelta(seconds=60)
        assert not claim.local_lost_signal.is_set()
        assert "claim-token-1" not in repr(claim)

        with Session(engine) as reader:
            task = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == task_id)
            ).mappings().one()
            assert task["status"] == "processing"
            assert task["claimed_by"] == "worker-1"
            assert task["claim_token"] == "claim-token-1"
            assert task["execution_generation"] == 1
            assert task["lease_until"] == (now[0] + timedelta(seconds=60)).replace(tzinfo=None)

        now[0] += timedelta(seconds=20)
        assert claims.renew_or_revoke(claim) == "renewed"
        with Session(engine) as reader:
            renewed = reader.execute(
                select(AITaskRow.__table__.c.lease_until).where(
                    AITaskRow.id == task_id
                )
            ).scalar_one()
            assert renewed == (now[0] + timedelta(seconds=60)).replace(tzinfo=None)

        competing = SqlAlchemyImageSingleClaims(
            session_factory=lambda: Session(engine),
            db_clock=lambda: now[0],
            id_factory=lambda: "claim-token-competing",
        )
        assert competing.claim_next("image-single", "worker-2") is None

        now[0] += timedelta(seconds=61)
        reclaimed = competing.claim_next("image-single", "worker-2")
        assert reclaimed is not None
        assert reclaimed.execution_generation == 2
        assert reclaimed.token == "claim-token-competing"
        assert claims.renew_or_revoke(claim) == "ownership_lost"
        assert claim.local_lost_signal.is_set()
    finally:
        engine.dispose()


def test_unregistered_task_type_is_rejected_before_factories_or_claim_writes(
    tmp_path: Path,
) -> None:
    environment, task_id = _admit_image_task(tmp_path)
    calls = {"session": 0, "clock": 0, "id": 0}

    def session_factory():
        calls["session"] += 1
        return Session(environment.engine)

    def db_clock():
        calls["clock"] += 1
        return datetime(2026, 10, 16, tzinfo=timezone.utc)

    def id_factory():
        calls["id"] += 1
        return "should-not-be-created"

    claims = SqlAlchemyImageSingleClaims(session_factory, db_clock, id_factory)
    try:
        assert claims.claim_next("batch-image", "worker-1") is None
        assert calls == {"session": 0, "clock": 0, "id": 0}
        with Session(environment.engine) as reader:
            task = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == task_id)
            ).mappings().one()
            assert task["status"] == "queued"
            assert task["claim_token"] is None
    finally:
        environment.engine.dispose()


def test_claim_rejects_an_incomplete_registered_graph_before_claiming(
    tmp_path: Path,
) -> None:
    environment, task_id = _admit_image_task(tmp_path, "invalid-claim-graph")
    engine = environment.engine
    try:
        with Session(engine) as mutator:
            step_table = ExecutionStepRow.__table__
            unit_id = mutator.execute(
                select(BillingUnitRow.id).where(BillingUnitRow.task_id == task_id)
            ).scalar_one()
            step_id = mutator.execute(
                select(step_table.c.id).where(step_table.c.billing_unit_id == unit_id)
            ).scalar_one()
            mutator.execute(
                update(step_table)
                .where(step_table.c.id == step_id)
                .values(step_key="unsupported-step")
            )
            mutator.commit()

        calls = {"clock": 0}

        def db_clock():
            calls["clock"] += 1
            return datetime(2026, 10, 16, tzinfo=timezone.utc)

        claims = SqlAlchemyImageSingleClaims(
            lambda: Session(engine), db_clock, lambda: "invalid-graph-token"
        )
        assert claims.claim_next("image-single", "worker-1") is None
        with Session(engine) as reader:
            task = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == task_id)
            ).mappings().one()
            assert task["status"] == "queued"
            assert task["claim_token"] is None
        assert calls["clock"] == 1
    finally:
        engine.dispose()


def test_unknown_claim_commit_is_confirmed_by_independent_readback(tmp_path: Path) -> None:
    environment, task_id = _admit_image_task(tmp_path, "unknown-claim-commit")
    engine = environment.engine
    factory_calls = 0

    class CommitThenRaise:
        def __init__(self) -> None:
            self.session = Session(engine)

        def __enter__(self):
            return self.session

        def __exit__(self, exc_type, exc, traceback):
            self.session.close()
            raise RuntimeError("commit acknowledgement lost")

    def session_factory():
        nonlocal factory_calls
        factory_calls += 1
        return CommitThenRaise() if factory_calls == 2 else Session(engine)

    claims = SqlAlchemyImageSingleClaims(
        session_factory,
        lambda: datetime(2026, 10, 16, tzinfo=timezone.utc),
        lambda: "confirmed-after-unknown-commit",
    )
    try:
        claim = claims.claim_next("image-single", "worker-unknown-ack")
        assert claim is not None
        with Session(engine) as reader:
            task = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == task_id)
            ).mappings().one()
            assert task["claim_token"] == "confirmed-after-unknown-commit"
            assert task["execution_generation"] == claim.execution_generation
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "changed_field",
    ["owner", "task_type", "token", "generation", "lease", "status"],
)
def test_unknown_claim_ack_rejects_changed_expected_binding(
    tmp_path: Path, changed_field: str
) -> None:
    environment, task_id = _admit_image_task(
        tmp_path, f"unknown-claim-tamper-{changed_field}"
    )
    engine = environment.engine
    factory_calls = 0

    class CommitThenTamper:
        def __init__(self) -> None:
            self.session = Session(engine)

        def __enter__(self):
            return self.session

        def __exit__(self, exc_type, exc, traceback):
            self.session.close()
            with Session(engine) as mutator:
                values = {}
                if changed_field == "owner":
                    mutator.execute(
                        insert(auth_users).values(
                            id="tampered-owner",
                            username="tampered-owner",
                            email="tampered-owner@example.test",
                            hashed_password="fixture-only",
                            is_superuser=False,
                            membership_type="free",
                            created_at=datetime(2026, 10, 16),
                        )
                    )
                    values["user_id"] = "tampered-owner"
                elif changed_field == "task_type":
                    values["type"] = "image-batch"
                elif changed_field == "token":
                    values["claim_token"] = "different-token"
                elif changed_field == "generation":
                    values["execution_generation"] = 2
                elif changed_field == "lease":
                    values["lease_until"] = datetime(2026, 10, 16, 3, 1)
                else:
                    values["status"] = "cancelling"
                mutator.execute(
                    update(AITaskRow.__table__)
                    .where(AITaskRow.id == task_id)
                    .values(**values)
                )
                mutator.commit()
            raise RuntimeError("claim commit acknowledgement lost after row changed")

    def session_factory():
        nonlocal factory_calls
        factory_calls += 1
        return CommitThenTamper() if factory_calls == 2 else Session(engine)

    claims = SqlAlchemyImageSingleClaims(
        session_factory,
        lambda: datetime(2026, 10, 16, 2, 0, tzinfo=timezone.utc),
        lambda: "tamper-ack-token",
    )
    before_financial = _financial_snapshot(engine)
    try:
        claim = claims.claim_next("image-single", "worker-tamper-ack")
        assert factory_calls == 3
        assert claim is None
        assert _financial_snapshot(engine) == before_financial
    finally:
        engine.dispose()


def test_pre_stage_failure_cannot_confirm_an_existing_token_row(tmp_path: Path) -> None:
    environment, task_ids = _admit_image_tasks(tmp_path, 2)
    engine = environment.engine
    with Session(engine) as reader:
        ordered_ids = list(
            reader.execute(
                select(AITaskRow.id).order_by(
                    AITaskRow.created_at, AITaskRow.id
                )
            ).scalars()
        )
    existing_claims = SqlAlchemyImageSingleClaims(
        lambda: Session(engine),
        lambda: datetime(2026, 10, 16, 2, 0, tzinfo=timezone.utc),
        lambda: "reused-claim-token",
    )
    existing_claim = existing_claims.claim_next("image-single", "worker-original")
    assert existing_claim is not None
    existing_task_id = existing_claim.scope.task_id
    assert existing_task_id in ordered_ids

    factory_calls = 0

    def failing_session_factory():
        nonlocal factory_calls
        factory_calls += 1
        if factory_calls == 2:
            raise RuntimeError("writer failed before staging")
        return Session(engine)

    retry = SqlAlchemyImageSingleClaims(
        failing_session_factory,
        lambda: datetime(2026, 10, 16, 2, 0, tzinfo=timezone.utc),
        lambda: "reused-claim-token",
    )
    try:
        assert retry.claim_next("image-single", "worker-retry") is None
        assert factory_calls == 2
        with Session(engine) as reader:
            rows = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.id.in_(task_ids))
            ).mappings().all()
            original = next(row for row in rows if row["id"] == existing_task_id)
            assert original["claim_token"] == "reused-claim-token"
            assert original["claimed_by"] == "worker-original"
            other = next(row for row in rows if row["id"] != existing_task_id)
            assert other["status"] == "queued"
            assert other["claim_token"] is None
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("status", "cancelled"),
    [
        ("cancelling", False),
        ("completed", False),
        ("failed", False),
        ("processing", True),
    ],
)
def test_renewal_rejects_cancelled_or_terminal_tasks_without_settlement(
    tmp_path: Path, status: str, cancelled: bool
) -> None:
    environment, task_id = _admit_image_task(
        tmp_path, f"renew-invalid-{status}-{cancelled}"
    )
    engine = environment.engine
    now = datetime(2026, 10, 16, 2, 0, tzinfo=timezone.utc)
    claims = SqlAlchemyImageSingleClaims(
        lambda: Session(engine), lambda: now, lambda: "renew-invalid-token"
    )
    claim = claims.claim_next("image-single", "worker-renew-invalid")
    assert claim is not None
    cancellation_stamp = datetime(2026, 10, 16, 2, 0) if cancelled else None
    with Session(engine) as mutator:
        mutator.execute(
            update(AITaskRow.__table__)
            .where(AITaskRow.id == task_id)
            .values(
                status=status,
                user_cancelled_at=cancellation_stamp,
                cancellation_reason="user_request" if cancelled else None,
                result="existing-terminal-result" if status in {"completed", "failed"} else None,
            )
        )
        mutator.commit()

    before_financial = _financial_snapshot(engine)
    with Session(engine) as reader:
        before = reader.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == task_id)
        ).mappings().one()
    now += timedelta(seconds=10)
    assert claims.renew_or_revoke(claim) == "ownership_lost"
    assert claim.local_lost_signal.is_set()
    with Session(engine) as reader:
        after = reader.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == task_id)
        ).mappings().one()
    assert after["status"] == status
    assert after["lease_until"] == before["lease_until"]
    assert after["user_cancelled_at"] == before["user_cancelled_at"]
    assert after["cancellation_reason"] == before["cancellation_reason"]
    assert after["result"] == before["result"]
    assert _financial_snapshot(engine) == before_financial
    engine.dispose()


def test_renewal_prewrite_failure_does_not_ack_the_still_live_old_lease(
    tmp_path: Path,
) -> None:
    environment, task_id = _admit_image_task(tmp_path, "renew-prewrite-failure")
    engine = environment.engine
    now = [datetime(2026, 10, 16, 2, 0, tzinfo=timezone.utc)]
    initial = SqlAlchemyImageSingleClaims(
        lambda: Session(engine), lambda: now[0], lambda: "renew-prewrite-token"
    )
    claim = initial.claim_next("image-single", "worker-renew-prewrite")
    assert claim is not None
    old_lease = claim.lease_until
    now[0] += timedelta(seconds=10)

    class FailBeforeOpen:
        def __call__(self):
            raise RuntimeError("renew writer failed before update")

    # The same adapter owns the local lost-signal fence; replace only its session factory.
    initial.session_factory = FailBeforeOpen()
    before_financial = _financial_snapshot(engine)
    assert initial.renew_or_revoke(claim) == "ownership_lost"
    assert claim.local_lost_signal.is_set()
    with Session(engine) as reader:
        stored = reader.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == task_id)
        ).mappings().one()
    assert stored["lease_until"] == old_lease.replace(tzinfo=None)
    assert _financial_snapshot(engine) == before_financial
    engine.dispose()


def test_renewal_unknown_commit_requires_exact_new_lease_and_live_processing(
    tmp_path: Path,
) -> None:
    environment, task_id = _admit_image_task(tmp_path, "renew-unknown-ack")
    engine = environment.engine
    now = [datetime(2026, 10, 16, 2, 0, tzinfo=timezone.utc)]
    initial = SqlAlchemyImageSingleClaims(
        lambda: Session(engine), lambda: now[0], lambda: "renew-unknown-token"
    )
    claim = initial.claim_next("image-single", "worker-renew-unknown")
    assert claim is not None
    now[0] += timedelta(seconds=10)
    factory_calls = 0

    class CommitThenRaise:
        def __init__(self) -> None:
            self.session = Session(engine)

        def __enter__(self):
            return self.session

        def __exit__(self, exc_type, exc, traceback):
            self.session.close()
            with Session(engine) as mutator:
                mutator.execute(
                    update(AITaskRow.__table__)
                    .where(AITaskRow.id == task_id)
                    .values(status="cancelling")
                )
                mutator.commit()
            raise RuntimeError("renew commit acknowledgement lost")

    def session_factory():
        nonlocal factory_calls
        factory_calls += 1
        return CommitThenRaise() if factory_calls == 1 else Session(engine)

    initial.session_factory = session_factory
    before_financial = _financial_snapshot(engine)
    assert initial.renew_or_revoke(claim) == "ownership_lost"
    assert claim.local_lost_signal.is_set()
    with Session(engine) as reader:
        stored = reader.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == task_id)
        ).mappings().one()
    assert stored["status"] == "cancelling"
    assert stored["lease_until"] == (now[0] + timedelta(seconds=60)).replace(tzinfo=None)
    assert _financial_snapshot(engine) == before_financial
    engine.dispose()


def test_renewal_unknown_commit_confirms_the_exact_new_lease(tmp_path: Path) -> None:
    environment, task_id = _admit_image_task(tmp_path, "renew-exact-ack")
    engine = environment.engine
    now = [datetime(2026, 10, 16, 2, 0, tzinfo=timezone.utc)]
    claims = SqlAlchemyImageSingleClaims(
        lambda: Session(engine), lambda: now[0], lambda: "renew-exact-token"
    )
    claim = claims.claim_next("image-single", "worker-renew-exact")
    assert claim is not None
    now[0] += timedelta(seconds=10)
    expected_lease = now[0] + timedelta(seconds=60)
    factory_calls = 0

    class CommitThenRaise:
        def __init__(self) -> None:
            self.session = Session(engine)

        def __enter__(self):
            return self.session

        def __exit__(self, exc_type, exc, traceback):
            self.session.close()
            raise RuntimeError("renew commit acknowledgement lost")

    def session_factory():
        nonlocal factory_calls
        factory_calls += 1
        return CommitThenRaise() if factory_calls == 1 else Session(engine)

    claims.session_factory = session_factory
    try:
        assert claims.renew_or_revoke(claim) == "renewed"
        assert factory_calls == 2
        with Session(engine) as reader:
            stored_lease = reader.execute(
                select(AITaskRow.__table__.c.lease_until).where(
                    AITaskRow.id == task_id
                )
            ).scalar_one()
        assert stored_lease == expected_lease.replace(tzinfo=None)
        assert not claim.local_lost_signal.is_set()
    finally:
        engine.dispose()


def test_release_preserves_cancellation_and_result_without_financial_changes(
    tmp_path: Path,
) -> None:
    environment, task_id = _admit_image_task(tmp_path, "release-preserve-cancel")
    engine = environment.engine
    claims = SqlAlchemyImageSingleClaims(
        lambda: Session(engine),
        lambda: datetime(2026, 10, 16, 2, 0, tzinfo=timezone.utc),
        lambda: "release-preserve-token",
    )
    claim = claims.claim_next("image-single", "worker-release-preserve")
    assert claim is not None
    cancellation_stamp = datetime(2026, 10, 16, 2, 1)
    with Session(engine) as mutator:
        mutator.execute(
            update(AITaskRow.__table__)
            .where(AITaskRow.id == task_id)
            .values(
                status="cancelling",
                user_cancelled_at=cancellation_stamp,
                cancellation_reason="user_request",
                result="result-to-preserve",
            )
        )
        mutator.commit()
    before_financial = _financial_snapshot(engine)

    assert claims.release_claim(claim)
    assert claim.local_lost_signal.is_set()
    with Session(engine) as reader:
        stored = reader.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == task_id)
        ).mappings().one()
    assert stored["status"] == "cancelling"
    assert stored["user_cancelled_at"] == cancellation_stamp
    assert stored["cancellation_reason"] == "user_request"
    assert stored["result"] == "result-to-preserve"
    assert stored["claimed_by"] is None
    assert stored["claim_token"] is None
    assert stored["lease_until"] is None
    assert _financial_snapshot(engine) == before_financial
    engine.dispose()


def test_claim_scans_past_live_and_invalid_first_candidate_page(tmp_path: Path) -> None:
    environment, task_ids = _admit_image_tasks(tmp_path, 129)
    engine = environment.engine
    now = datetime(2026, 10, 16, 2, 0, tzinfo=timezone.utc)
    with Session(engine) as mutator:
        ordered_tasks = mutator.execute(
            select(AITaskRow.__table__.c.id, AITaskRow.__table__.c.created_at).order_by(
                AITaskRow.__table__.c.created_at, AITaskRow.__table__.c.id
            )
        ).all()
        ordered_ids = [row.id for row in ordered_tasks]
        invalid_ids = ordered_ids[:64]
        live_ids = ordered_ids[64:128]
        expected_task_id = ordered_ids[128]
        unit_ids = mutator.execute(
            select(BillingUnitRow.__table__.c.id).where(
                BillingUnitRow.__table__.c.task_id.in_(invalid_ids)
            )
        ).scalars().all()
        mutator.execute(
            update(ExecutionStepRow.__table__)
            .where(ExecutionStepRow.__table__.c.billing_unit_id.in_(unit_ids))
            .values(step_key="unsupported-legacy-step")
        )
        mutator.execute(
            update(AITaskRow.__table__)
            .where(AITaskRow.id.in_(live_ids))
            .values(
                status="processing",
                claimed_by="another-worker",
                claim_token="existing-live-claim",
                execution_generation=1,
                lease_until=datetime(2026, 10, 16, 3, 0),
            )
        )
        mutator.commit()

    before_financial = _financial_snapshot(engine)
    clock_calls = 0

    def db_clock():
        nonlocal clock_calls
        clock_calls += 1
        return now

    claims = SqlAlchemyImageSingleClaims(
        lambda: Session(engine), db_clock, lambda: "after-page-token"
    )
    try:
        claim = claims.claim_next("image-single", "worker-after-page")
        assert claim is not None
        assert claim.scope.task_id == expected_task_id
        assert clock_calls == 129
        with Session(engine) as reader:
            tasks = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.id.in_(task_ids))
            ).mappings().all()
            live = [row for row in tasks if row["id"] in live_ids]
            assert all(row["claimed_by"] == "another-worker" for row in live)
            assert all(row["claim_token"] == "existing-live-claim" for row in live)
            winner = next(row for row in tasks if row["id"] == expected_task_id)
            assert winner["status"] == "processing"
            assert winner["claim_token"] == "after-page-token"
        assert _financial_snapshot(engine) == before_financial
    finally:
        engine.dispose()


@pytest.mark.parametrize("operation", ["claim", "renew", "release"])
def test_sqlite_two_connection_cas_does_not_overwrite_concurrent_task_change(
    tmp_path: Path, operation: str
) -> None:
    environment, task_id = _admit_image_task(tmp_path, f"sqlite-cas-{operation}")
    engine = environment.engine
    now = datetime(2026, 10, 16, 2, 0, tzinfo=timezone.utc)
    calls = 0

    def db_clock():
        nonlocal calls
        calls += 1
        return now + timedelta(seconds=10 * calls)

    claims = SqlAlchemyImageSingleClaims(
        lambda: Session(engine), db_clock, lambda: "sqlite-cas-token"
    )
    existing_claim = None
    if operation != "claim":
        existing_claim = claims.claim_next("image-single", "worker-cas")
        assert existing_claim is not None

    entered_update = Event()
    resume_update = Event()
    result: list[object] = []
    errors: list[BaseException] = []
    financial_snapshot_before = _financial_snapshot(engine)

    def pause_before_update(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("UPDATE AI_TASKS SET") and not entered_update.is_set():
            entered_update.set()
            if not resume_update.wait(10):
                raise TimeoutError("test did not release gated worker update")

    def run_worker():
        try:
            if operation == "claim":
                result.append(claims.claim_next("image-single", "worker-cas"))
            elif operation == "renew":
                result.append(claims.renew_or_revoke(existing_claim))
            else:
                result.append(claims.release_claim(existing_claim))
        except BaseException as exc:
            errors.append(exc)

    event.listen(engine, "before_cursor_execute", pause_before_update)
    worker = Thread(target=run_worker, daemon=True)
    worker.start()
    try:
        assert entered_update.wait(10)
        cancellation_stamp = datetime(2026, 10, 16, 2, 5)
        with Session(engine) as mutator:
            if operation == "release":
                mutator.execute(
                    update(AITaskRow.__table__)
                    .where(AITaskRow.id == task_id)
                    .values(status="completed", result="concurrent-result")
                )
            else:
                mutator.execute(
                    update(AITaskRow.__table__)
                    .where(AITaskRow.id == task_id)
                    .values(
                        status="cancelling",
                        user_cancelled_at=cancellation_stamp,
                        cancellation_reason="concurrent-cancel",
                    )
                )
            mutator.commit()
        resume_update.set()
        worker.join(10)
        assert not worker.is_alive()
        assert errors == []
        if operation == "claim":
            assert result == [None]
        elif operation == "renew":
            assert result == ["ownership_lost"]
            assert existing_claim.local_lost_signal.is_set()
        else:
            assert result == [False]
            assert existing_claim.local_lost_signal.is_set()
        with Session(engine) as reader:
            stored = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == task_id)
            ).mappings().one()
        if operation == "release":
            assert stored["status"] == "completed"
            assert stored["result"] == "concurrent-result"
        else:
            assert stored["status"] == "cancelling"
            assert stored["user_cancelled_at"] == cancellation_stamp
            assert stored["cancellation_reason"] == "concurrent-cancel"
            if operation == "claim":
                assert stored["claim_token"] is None
            else:
                assert stored["claim_token"] == existing_claim.token
        assert _financial_snapshot(engine) == financial_snapshot_before
    finally:
        resume_update.set()
        worker.join(10)
        event.remove(engine, "before_cursor_execute", pause_before_update)
        engine.dispose()


def test_shutdown_release_clears_only_exact_claim_without_cancel_or_settlement(
    tmp_path: Path,
) -> None:
    environment, task_id = _admit_image_task(tmp_path, "release-claim-key")
    engine = environment.engine
    claims = SqlAlchemyImageSingleClaims(
        lambda: Session(engine),
        lambda: datetime(2026, 10, 16, tzinfo=timezone.utc),
        lambda: "release-claim-token",
    )
    try:
        claim = claims.claim_next("image-single", "worker-release")
        assert claim is not None
        assert claims.release_claim(claim)
        assert claim.local_lost_signal.is_set()
        with Session(engine) as reader:
            task = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == task_id)
            ).mappings().one()
            assert task["status"] == "queued"
            assert task["claimed_by"] is None
            assert task["claim_token"] is None
            assert task["lease_until"] is None
            assert task["user_cancelled_at"] is None
            assert task["cancellation_reason"] is None
    finally:
        engine.dispose()
