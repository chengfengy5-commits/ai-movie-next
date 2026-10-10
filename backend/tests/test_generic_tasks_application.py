from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from haoai_backend.generic_tasks.application import (
    create_task,
    get_external_task_status,
    update_task,
    update_task_progress,
)
from haoai_backend.generic_tasks.errors import GenericTaskError
from generic_tasks_support import (
    ACTOR,
    NOW,
    make_runtime,
    observe_sql,
    owner_database,
    read_row,
    rows,
    seed_debit_row,
    seed_task,
    seed_usage_debit,
    unchanged_except,
    generic_uow_factory,
)


def test_positive_create_stages_task_before_wallet_query_and_commits_exact_ledger(
    owner_database,
) -> None:
    before = owner_database.snapshot()
    observation, stop = observe_sql(owner_database)
    runtime, ids = make_runtime(("g-created-charge", "g-charge-ledger"))
    started_at = datetime.utcnow()
    try:
        result = create_task(
            generic_uow_factory(owner_database),
            ACTOR,
            "image",
            10,
            '{"model":"","model_name":"chosen-model","modelId":"fallback"}',
            runtime,
        )
    finally:
        stop()

    assert result == {"id": "g-created-charge", "status": "processing", "credit_cost": 10}
    assert ids.calls == 2
    statements = [statement.lower() for statement in observation.statements]
    wallet_read = next(i for i, sql in enumerate(statements) if "from user_credits" in sql)
    task_insert = next(
        i for i, sql in enumerate(statements)
        if sql.startswith("insert into ai_tasks")
    )
    assert wallet_read < task_insert
    assert observation.commits == 1

    task = read_row(owner_database, "ai_tasks", "g-created-charge")
    assert task is not None
    assert task["type"] == "image"
    assert task["status"] == "processing"
    assert task["model_name"] == "chosen-model"
    assert task["request_data"] == '{"model":"","model_name":"chosen-model","modelId":"fallback"}'
    assert task["created_at"] == NOW
    assert task["updated_at"] == NOW
    assert task["claimed_by"] is None
    assert task["billing_status"] == "unbilled"

    wallet = read_row(owner_database, "user_credits", "credits-a")
    assert wallet is not None
    assert (wallet["credits"], wallet["updated_at"]) == (221, NOW)
    finished_at = datetime.utcnow()
    ledger = next(row for row in rows(owner_database, "credit_logs") if row["id"] == "g-charge-ledger")
    assert ledger == {
        "id": "g-charge-ledger",
        "user_id": "user-a",
        "amount": -10,
        "balance_after": 221,
        "type": "usage",
        "description": "创建任务 [image] 消耗 10 积分",
        "task_id": "g-created-charge",
        "business_key": "generic-task-charge:g-created-charge",
        "billing_unit_id": None,
        "related_debit_id": None,
        "created_at": ledger["created_at"],
    }
    assert started_at <= ledger["created_at"] <= finished_at
    assert ledger["created_at"].tzinfo is None
    after = owner_database.snapshot()
    unchanged_except(before, after, {"ai_tasks", "user_credits", "credit_logs"})


@pytest.mark.parametrize("credit_cost", [0, -4])
def test_zero_and_negative_cost_create_without_wallet_or_ledger_access(
    owner_database, credit_cost: int,
) -> None:
    observation, stop = observe_sql(owner_database)
    runtime, _ = make_runtime((f"g-free-{credit_cost}", "unused-ledger"))
    try:
        result = create_task(
            generic_uow_factory(owner_database),
            ACTOR,
            "image",
            credit_cost,
            "not-json",
            runtime,
        )
    finally:
        stop()

    assert result["id"] == f"g-free-{credit_cost}"
    statements = [statement.lower() for statement in observation.statements]
    assert not any("from user_credits" in sql for sql in statements)
    assert not any(sql.startswith("update user_credits") for sql in statements)
    assert not any(
        row["task_id"] == f"g-free-{credit_cost}"
        for row in rows(owner_database, "credit_logs")
    )
    task = read_row(owner_database, "ai_tasks", f"g-free-{credit_cost}")
    assert task is not None
    assert task["credit_cost"] == credit_cost
    assert task["request_data"] == "not-json"
    assert task["model_name"] is None


def test_insufficient_credit_rolls_back_staged_task_without_ledger(owner_database) -> None:
    with owner_database.engine.begin() as connection:
        connection.exec_driver_sql("UPDATE user_credits SET credits=3 WHERE user_id='user-a'")
    observation, stop = observe_sql(owner_database)
    runtime, _ = make_runtime(("g-insufficient", "unused-ledger"))
    try:
        with pytest.raises(GenericTaskError) as error:
            create_task(
                generic_uow_factory(owner_database),
                ACTOR,
                "image",
                4,
                "{}",
                runtime,
            )
    finally:
        stop()

    assert error.value.status_code == 402
    assert error.value.detail == "积分不足，需要 4 积分，当前剩余 3 积分"
    assert read_row(owner_database, "ai_tasks", "g-insufficient") is None
    assert not any(row["task_id"] == "g-insufficient" for row in rows(owner_database, "credit_logs"))
    assert read_row(owner_database, "user_credits", "credits-a")["credits"] == 3
    assert observation.commits == 0
    assert observation.rollbacks == 1


def test_json_scalar_attribute_error_is_not_normalized_or_persisted(owner_database) -> None:
    runtime, _ = make_runtime(("g-scalar",))
    with pytest.raises(AttributeError, match="get"):
        create_task(
            generic_uow_factory(owner_database),
            ACTOR,
            "image",
            0,
            "[]",
            runtime,
        )
    assert read_row(owner_database, "ai_tasks", "g-scalar") is None


def test_failed_status_refunds_only_one_exact_matching_debit(owner_database) -> None:
    seed_usage_debit(
        owner_database,
        "g-refund",
        debit_id="g-refund-debit",
        credit_cost=10,
    )
    with owner_database.engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE ai_tasks SET created_at='2026-10-09 03:00:00', updated_at='2026-10-09 03:00:00' WHERE id='g-refund'"
        )
    before = owner_database.snapshot()
    observation, stop = observe_sql(owner_database)
    runtime, ids = make_runtime(("g-refund-entry",))
    started_at = datetime.utcnow()
    try:
        result = update_task(
            generic_uow_factory(owner_database),
            ACTOR,
            "g-refund",
            "failed",
            runtime=runtime,
        )
    finally:
        stop()

    assert result == {"id": "g-refund", "status": "failed"}
    assert ids.calls == 1
    task = read_row(owner_database, "ai_tasks", "g-refund")
    assert task is not None
    assert (task["status"], task["result"], task["updated_at"]) == ("failed", None, NOW)
    wallet = read_row(owner_database, "user_credits", "credits-a")
    assert wallet is not None and (wallet["credits"], wallet["updated_at"]) == (231, NOW)
    refund = read_row(owner_database, "credit_logs", "g-refund-entry")
    assert refund is not None
    assert refund["amount"] == 10
    assert refund["balance_after"] == 231
    assert refund["type"] == "refund"
    assert refund["description"] == "任务 [image] 失败退回 10 积分"
    assert refund["business_key"] == "generic-task-refund:g-refund-debit"
    assert refund["related_debit_id"] == "g-refund-debit"
    assert refund["billing_unit_id"] is None
    assert started_at <= refund["created_at"] <= datetime.utcnow()
    assert refund["created_at"].tzinfo is None
    queries = [sql.lower() for sql in observation.statements if "from credit_logs" in sql.lower()]
    related_refund = next(
        sql for sql in queries
        if "related_debit_id" in sql and "task_id" not in sql
    )
    refund_filter = related_refund.split("where", 1)[1]
    assert "user_id" not in refund_filter
    after = owner_database.snapshot()
    unchanged_except(before, after, {"ai_tasks", "user_credits", "credit_logs"})


def test_failed_status_does_not_refund_missing_or_ambiguous_usage(owner_database) -> None:
    seed_task(owner_database, "g-missing-debit", credit_cost=10)
    seed_task(owner_database, "g-ambiguous-debit", credit_cost=10)
    seed_debit_row(owner_database, "g-ambiguous-debit", debit_id="g-ambiguous-1")
    seed_debit_row(owner_database, "g-ambiguous-debit", debit_id="g-ambiguous-2")
    runtime, ids = make_runtime(("unused-refund",))
    for task_id in ("g-missing-debit", "g-ambiguous-debit"):
        result = update_task(
            generic_uow_factory(owner_database),
            ACTOR,
            task_id,
            "failed",
            runtime=runtime,
        )
        assert result == {"id": task_id, "status": "failed"}
    assert ids.calls == 0
    assert read_row(owner_database, "user_credits", "credits-a")["credits"] == 231
    assert not any(
        row["type"] == "refund" and row["task_id"] in {"g-missing-debit", "g-ambiguous-debit"}
        for row in rows(owner_database, "credit_logs")
    )


def test_status_and_progress_preserve_none_but_allow_empty_values(owner_database) -> None:
    seed_task(owner_database, "g-update", result="old result")
    with owner_database.engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE ai_tasks SET claimed_by='worker-g', lease_until='2026-10-10 04:00:00', "
            "execution_generation=7, claim_token='claim-g', recovery_status='processing', "
            "billing_status='charged' WHERE id='g-update'"
        )
    seed_task(owner_database, "g-progress")
    with owner_database.engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE ai_tasks SET progress_message='preserve this' WHERE id='g-progress'"
        )
    status_runtime, _ = make_runtime(())
    update_task(
        generic_uow_factory(owner_database),
        ACTOR,
        "g-update",
        "completed",
        None,
        status_runtime,
    )
    updated_status = update_task(
        generic_uow_factory(owner_database),
        ACTOR,
        "g-update",
        "completed",
        "",
        status_runtime,
    )
    assert updated_status == {"id": "g-update", "status": "completed"}
    task = read_row(owner_database, "ai_tasks", "g-update")
    assert task is not None and task["result"] == ""
    assert (task["claimed_by"], task["execution_generation"], task["claim_token"]) == (
        "worker-g", 7, "claim-g"
    )
    assert (task["recovery_status"], task["billing_status"]) == ("processing", "charged")

    update_task_progress(
        generic_uow_factory(owner_database),
        ACTOR,
        "g-progress",
        120,
        None,
        status_runtime,
    )
    task = read_row(owner_database, "ai_tasks", "g-progress")
    assert task is not None and task["progress_message"] == "preserve this"
    progress = update_task_progress(
        generic_uow_factory(owner_database),
        ACTOR,
        "g-progress",
        -8,
        "",
        status_runtime,
    )
    assert progress == {"id": "g-progress", "progress": 0, "progress_message": ""}
    task = read_row(owner_database, "ai_tasks", "g-progress")
    assert task is not None
    assert task["progress"] == 0
    assert task["progress_message"] == ""



def test_failed_status_commits_without_second_refund_when_one_already_exists(owner_database) -> None:
    from generic_tasks_support import seed_refund_row

    seed_usage_debit(
        owner_database,
        "g-already-refunded",
        debit_id="g-already-debit",
        credit_cost=10,
    )
    seed_refund_row(
        owner_database,
        "g-already-debit",
        refund_id="g-existing-refund",
        task_id="g-already-refunded",
    )
    runtime, ids = make_runtime(("unused-refund",))
    result = update_task(
        generic_uow_factory(owner_database),
        ACTOR,
        "g-already-refunded",
        "failed",
        runtime=runtime,
    )

    assert result == {"id": "g-already-refunded", "status": "failed"}
    assert ids.calls == 0
    wallet = read_row(owner_database, "user_credits", "credits-a")
    assert wallet is not None and wallet["credits"] == 221
    refunds = [
        row for row in rows(owner_database, "credit_logs")
        if row["type"] == "refund" and row["related_debit_id"] == "g-already-debit"
    ]
    assert [row["id"] for row in refunds] == ["g-existing-refund"]


def test_create_model_alias_truthiness_and_falsey_request_default(owner_database) -> None:
    cases = (
        ("", "{}", None),
        ('{"model":"first","model_name":"second","modelId":"third"}',
         '{"model":"first","model_name":"second","modelId":"third"}', "first"),
        ('{"model":"","model_name":"","modelId":"third"}',
         '{"model":"","model_name":"","modelId":"third"}', "third"),
    )
    for index, (request_data, stored_request, expected_model) in enumerate(cases):
        task_id = f"g-model-alias-{index}"
        runtime, _ = make_runtime((task_id,))
        create_task(
            generic_uow_factory(owner_database),
            ACTOR,
            "image",
            0,
            request_data,
            runtime,
        )
        task = read_row(owner_database, "ai_tasks", task_id)
        assert task is not None
        assert task["request_data"] == stored_request
        assert task["model_name"] == expected_model


class FakePrepared:
    def __init__(self, response, *, result="https://media.invalid/result.png") -> None:
        self.response = response
        self.result = result
        self.poll_ids: list[str] = []
        self.extract_calls: list[object] = []

    async def poll_once(self, external_task_id: str):
        self.poll_ids.append(external_task_id)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def extract_result(self, response):
        self.extract_calls.append(response)
        return self.result


class FakeProviderPort:
    def __init__(self, prepared, *, prepare_error=None) -> None:
        self.prepared = prepared
        self.prepare_error = prepare_error
        self.providers: list[str] = []

    def prepare(self, provider: str):
        self.providers.append(provider)
        if self.prepare_error is not None:
            raise self.prepare_error
        return self.prepared


def test_external_status_prepares_before_catch_polls_once_and_never_mutates(
    owner_database,
) -> None:
    seed_task(
        owner_database,
        "g-external",
        external_task_id="external-42",
        external_provider="test-provider",
    )
    before = owner_database.snapshot()
    prepared = FakePrepared(("completed", {"payload": 1}))
    port = FakeProviderPort(prepared)
    runtime, _ = make_runtime((), provider_status=port)
    result = asyncio.run(
        get_external_task_status(
            generic_uow_factory(owner_database),
            ACTOR,
            "g-external",
            runtime,
        )
    )

    assert result == {"status": "completed", "result": "https://media.invalid/result.png"}
    assert port.providers == ["test-provider"]
    assert prepared.poll_ids == ["external-42"]
    assert prepared.extract_calls == [{"payload": 1}]
    assert owner_database.snapshot() == before


def test_external_status_preserves_expired_string_error_unknown_behavior(owner_database) -> None:
    seed_task(
        owner_database,
        "g-expired",
        external_task_id="external-expired",
        external_provider="xai",
    )
    prepared = FakePrepared(("failed", {"error": "请求已过期"}))
    runtime, _ = make_runtime((), provider_status=FakeProviderPort(prepared))
    result = asyncio.run(
        get_external_task_status(
            generic_uow_factory(owner_database),
            ACTOR,
            "g-expired",
            runtime,
        )
    )
    assert result == {
        "status": "unknown",
        "detail": "查询外部任务状态异常: 'str' object has no attribute 'get'",
    }
    assert prepared.poll_ids == ["external-expired"]


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            ("failed", {"error": {"message": "provider rejected"}}),
            {"status": "failed", "error": "provider rejected"},
        ),
        (("pending", {"ignored": True}), {"status": "processing"}),
    ],
)
def test_external_status_maps_legacy_failed_and_pending_shapes(
    owner_database, response, expected
) -> None:
    task_id = f"g-provider-{response[0]}"
    seed_task(
        owner_database,
        task_id,
        external_task_id="external-shape",
        external_provider="test-provider",
    )
    prepared = FakePrepared(response)
    runtime, _ = make_runtime((), provider_status=FakeProviderPort(prepared))
    result = asyncio.run(
        get_external_task_status(
            generic_uow_factory(owner_database),
            ACTOR,
            task_id,
            runtime,
        )
    )
    assert result == expected
    assert prepared.poll_ids == ["external-shape"]


def test_external_status_unconfigured_and_missing_external_task_semantics(owner_database) -> None:
    seed_task(
        owner_database,
        "g-no-queryer",
        external_task_id="external-no-queryer",
        external_provider="unsupported",
    )
    seed_task(owner_database, "g-no-external")
    no_queryer, _ = make_runtime((), provider_status=FakeProviderPort(None))
    result = asyncio.run(
        get_external_task_status(
            generic_uow_factory(owner_database),
            ACTOR,
            "g-no-queryer",
            no_queryer,
        )
    )
    assert result == {"status": "unknown", "detail": "无法为该任务创建查询器"}

    unused_port = FakeProviderPort(None)
    no_external, _ = make_runtime((), provider_status=unused_port)
    result = asyncio.run(
        get_external_task_status(
            generic_uow_factory(owner_database),
            ACTOR,
            "g-no-external",
            no_external,
        )
    )
    assert result == {"status": "unknown", "detail": "该任务没有关联的外部任务"}
    assert unused_port.providers == []


def test_external_status_poll_exception_becomes_unknown(owner_database) -> None:
    seed_task(
        owner_database,
        "g-poll-error",
        external_task_id="external-error",
        external_provider="test-provider",
    )
    prepared = FakePrepared(RuntimeError("temporary poll issue"))
    runtime, _ = make_runtime((), provider_status=FakeProviderPort(prepared))
    result = asyncio.run(
        get_external_task_status(
            generic_uow_factory(owner_database),
            ACTOR,
            "g-poll-error",
            runtime,
        )
    )
    assert result == {
        "status": "unknown",
        "detail": "查询外部任务状态异常: temporary poll issue",
    }
    assert prepared.poll_ids == ["external-error"]


def test_external_status_without_provider_runtime_returns_503(owner_database) -> None:
    seed_task(
        owner_database,
        "g-no-provider-runtime",
        external_task_id="external-no-runtime",
        external_provider="xai",
    )
    runtime, _ = make_runtime(())
    with pytest.raises(GenericTaskError) as error:
        asyncio.run(
            get_external_task_status(
                generic_uow_factory(owner_database),
                ACTOR,
                "g-no-provider-runtime",
                runtime,
            )
        )
    assert error.value.status_code == 503

def test_external_status_prepare_errors_escape_poll_boundary(owner_database) -> None:
    seed_task(
        owner_database,
        "g-prepare-error",
        external_task_id="external-prepare-error",
        external_provider="xai",
    )
    port = FakeProviderPort(None, prepare_error=GenericTaskError(503, "缺少 provider runtime"))
    runtime, _ = make_runtime((), provider_status=port)
    with pytest.raises(GenericTaskError) as error:
        asyncio.run(
            get_external_task_status(
                generic_uow_factory(owner_database),
                ACTOR,
                "g-prepare-error",
                runtime,
            )
        )
    assert error.value.status_code == 503
    assert port.providers == ["xai"]
