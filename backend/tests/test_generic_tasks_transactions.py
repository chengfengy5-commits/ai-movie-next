from __future__ import annotations

import pytest

from haoai_backend.generic_tasks.application import create_task, update_task, update_task_progress
from generic_tasks_support import (
    ACTOR,
    counting_factory,
    generic_uow_factory,
    make_runtime,
    owner_database,
    read_row,
    seed_task,
)


def test_commit_acknowledgement_failure_does_not_rollback_or_replay_create(
    owner_database,
) -> None:
    factory, units = counting_factory(
        generic_uow_factory(owner_database),
        fail_after_commit=True,
    )
    runtime, ids = make_runtime(("g-commit-ambiguous",))
    with pytest.raises(RuntimeError, match="lost commit acknowledgement"):
        create_task(factory, ACTOR, "image", 0, "{}", runtime)

    assert ids.calls == 1
    assert len(units) == 1
    assert (units[0].commit_calls, units[0].rollback_calls, units[0].close_calls) == (1, 0, 1)
    task = read_row(owner_database, "ai_tasks", "g-commit-ambiguous")
    assert task is not None and task["status"] == "processing"


def test_create_refresh_failure_after_commit_is_not_replayed(owner_database) -> None:
    factory, units = counting_factory(
        generic_uow_factory(owner_database),
        fail_refresh=True,
    )
    runtime, _ = make_runtime(("g-refresh-failed",))
    with pytest.raises(RuntimeError, match="create refresh failure"):
        create_task(factory, ACTOR, "image", 0, "{}", runtime)

    assert len(units) == 1
    assert (units[0].commit_calls, units[0].rollback_calls, units[0].close_calls) == (1, 0, 1)
    assert read_row(owner_database, "ai_tasks", "g-refresh-failed") is not None


def test_unknown_status_commit_ack_is_not_replayed_or_rolled_back(owner_database) -> None:
    task_id = "g-unknown-status-commit"
    seed_task(owner_database, task_id, status="processing")
    factory, units = counting_factory(
        generic_uow_factory(owner_database),
        fail_after_commit=True,
    )
    runtime, _ = make_runtime(())
    with pytest.raises(RuntimeError, match="lost commit acknowledgement"):
        update_task(factory, ACTOR, task_id, "completed", "committed", runtime)

    assert len(units) == 1
    assert (units[0].commit_calls, units[0].rollback_calls, units[0].close_calls) == (1, 0, 1)
    task = read_row(owner_database, "ai_tasks", task_id)
    assert task is not None and (task["status"], task["result"]) == ("completed", "committed")

@pytest.mark.parametrize("method", ["status", "progress"])

def test_postcommit_readback_failure_is_not_replayed(owner_database, method: str) -> None:
    task_id = f"g-readback-{method}"
    seed_task(owner_database, task_id, result="old")
    factory, units = counting_factory(
        generic_uow_factory(owner_database),
        fail_readback=True,
    )
    runtime, _ = make_runtime(())
    with pytest.raises(RuntimeError, match="committed readback failure"):
        if method == "status":
            update_task(factory, ACTOR, task_id, "completed", "new", runtime)
        else:
            update_task_progress(factory, ACTOR, task_id, 75, "running", runtime)

    assert len(units) == 1
    assert (units[0].commit_calls, units[0].rollback_calls, units[0].close_calls) == (1, 0, 1)
    task = read_row(owner_database, "ai_tasks", task_id)
    assert task is not None
    assert task["status"] == ("completed" if method == "status" else "processing")
    assert task["result"] == ("new" if method == "status" else "old")
    assert task["progress"] == (0 if method == "status" else 75)
