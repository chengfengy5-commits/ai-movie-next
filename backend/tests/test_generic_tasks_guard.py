from __future__ import annotations

import pytest

from haoai_backend.generic_tasks.application import update_task, update_task_progress
from haoai_backend.generic_tasks.errors import GenericTaskError
from generic_tasks_support import (
    ACTOR,
    OTHER_ACTOR,
    make_runtime,
    observe_sql,
    owner_database,
    read_row,
    seed_billing_unit,
    seed_submission,
    seed_task,
    generic_uow_factory,
)


def test_owned_lookup_returns_404_before_any_controlled_task_check(owner_database) -> None:
    seed_task(owner_database, "g-private")
    observation, stop = observe_sql(owner_database)
    runtime, _ = make_runtime(())
    try:
        with pytest.raises(GenericTaskError) as error:
            update_task(
                generic_uow_factory(owner_database),
                OTHER_ACTOR,
                "g-private",
                "failed",
                runtime=runtime,
            )
    finally:
        stop()

    assert error.value.status_code == 404
    sql = [statement.lower() for statement in observation.statements]
    assert sum("from ai_tasks" in statement for statement in sql) == 1
    assert not any("billing_units" in statement for statement in sql)
    assert not any("task_submissions" in statement for statement in sql)


def test_controlled_type_short_circuits_billing_and_submission_queries(owner_database) -> None:
    seed_task(owner_database, "g-controlled", task_type="batch-image")
    seed_billing_unit(owner_database, "g-controlled")
    seed_submission(owner_database, "g-controlled-submission", ["g-controlled"])
    observation, stop = observe_sql(owner_database)
    runtime, _ = make_runtime(())
    try:
        with pytest.raises(GenericTaskError) as error:
            update_task(
                generic_uow_factory(owner_database),
                ACTOR,
                "g-controlled",
                "failed",
                runtime=runtime,
            )
    finally:
        stop()

    assert error.value.status_code == 409
    sql = [statement.lower() for statement in observation.statements]
    assert sum("from ai_tasks" in statement for statement in sql) == 1
    assert not any("billing_units" in statement for statement in sql)
    assert not any("task_submissions" in statement for statement in sql)


def test_billing_unit_blocks_before_submission_list(owner_database) -> None:
    seed_task(owner_database, "g-billed")
    seed_billing_unit(owner_database, "g-billed")
    seed_submission(owner_database, "g-billed-submission", ["g-billed"])
    observation, stop = observe_sql(owner_database)
    runtime, _ = make_runtime(())
    try:
        with pytest.raises(GenericTaskError) as error:
            update_task(
                generic_uow_factory(owner_database),
                ACTOR,
                "g-billed",
                "failed",
                runtime=runtime,
            )
    finally:
        stop()

    assert error.value.status_code == 409
    sql = [statement.lower() for statement in observation.statements]
    assert next(i for i, item in enumerate(sql) if "from billing_units" in item) < len(sql)
    assert not any("from task_submissions" in statement for statement in sql)


def test_only_python_list_submission_membership_blocks_update(owner_database) -> None:
    seed_task(owner_database, "g-listed")
    seed_task(owner_database, "g-scalar")
    seed_submission(owner_database, "g-list-submission", ["g-listed"])
    seed_submission(owner_database, "g-scalar-submission", "g-scalar")
    runtime, _ = make_runtime(())

    with pytest.raises(GenericTaskError) as error:
        update_task(
            generic_uow_factory(owner_database),
            ACTOR,
            "g-listed",
            "completed",
            runtime=runtime,
        )
    assert error.value.status_code == 409

    result = update_task(
        generic_uow_factory(owner_database),
        ACTOR,
        "g-scalar",
        "completed",
        runtime=runtime,
    )
    assert result == {"id": "g-scalar", "status": "completed"}


def test_progress_lookup_is_owned_first_and_uses_no_for_update_lock(owner_database) -> None:
    from sqlalchemy.orm import Session

    seed_task(owner_database, "g-progress-lock")
    lock_flags: list[bool] = []

    def observe_statement(state) -> None:
        statement = getattr(state, "statement", None)
        if statement is not None and getattr(statement, "is_select", False):
            lock_flags.append(getattr(statement, "_for_update_arg", None) is not None)

    from sqlalchemy import event
    event.listen(Session, "do_orm_execute", observe_statement)
    runtime, _ = make_runtime(())
    try:
        result = update_task_progress(
            generic_uow_factory(owner_database),
            ACTOR,
            "g-progress-lock",
            101,
            runtime=runtime,
        )
    finally:
        event.remove(Session, "do_orm_execute", observe_statement)

    assert result["progress"] == 100
    assert lock_flags
    assert lock_flags == [False, False, False, False]
    assert read_row(owner_database, "ai_tasks", "g-progress-lock")["progress"] == 100


def test_status_update_uses_for_update_then_postcommit_primary_key_readback(
    owner_database,
) -> None:
    from sqlalchemy.orm import Session
    from sqlalchemy import event

    seed_task(owner_database, "g-status-lock")
    lock_flags: list[bool] = []

    def observe_statement(state) -> None:
        statement = getattr(state, "statement", None)
        if statement is not None and getattr(statement, "is_select", False):
            lock_flags.append(getattr(statement, "_for_update_arg", None) is not None)

    event.listen(Session, "do_orm_execute", observe_statement)
    observation, stop = observe_sql(owner_database)
    runtime, _ = make_runtime(())
    try:
        update_task(
            generic_uow_factory(owner_database),
            ACTOR,
            "g-status-lock",
            "completed",
            runtime=runtime,
        )
    finally:
        event.remove(Session, "do_orm_execute", observe_statement)
        stop()

    assert lock_flags == [True, False, False, False]
    task_reads = [
        statement.lower()
        for statement in observation.statements
        if statement.lower().startswith("select") and "from ai_tasks" in statement.lower()
    ]
    assert len(task_reads) == 2
    assert "user_id" in task_reads[0]
    readback_filter = task_reads[-1].split("where", 1)[1]
    assert "user_id" not in readback_filter
    assert "where ai_tasks.id = ?" in task_reads[-1]
