"""Shared fixtures and real SQLite helpers for generic-task tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import event, insert, select, update

from chapter_asset_replacement_support import (
    CONSERVATION_TABLES,
    OWNER_METADATA,
    OWNER_TABLES,
    OwnerDatabase,
    create_owner_database,
)
from haoai_backend.generic_tasks.persistence import create_generic_task_uow_factory
from haoai_backend.generic_tasks.ports import GenericTaskRuntime
from haoai_backend.shared.identity import TrustedActor

ACTOR = TrustedActor(user_id="user-a")
OTHER_ACTOR = TrustedActor(user_id="user-b")
NOW = datetime(2026, 10, 10, 3, 0, 0)


@dataclass
class IdSequence:
    values: tuple[str, ...]
    calls: int = 0

    def __call__(self) -> str:
        if self.calls >= len(self.values):
            raise AssertionError("test id sequence exhausted")
        value = self.values[self.calls]
        self.calls += 1
        return value


@dataclass
class SqlObservation:
    statements: list[str]
    commits: int = 0
    rollbacks: int = 0


@pytest.fixture
def owner_database(tmp_path: Path):
    database = create_owner_database(tmp_path / "generic-tasks.sqlite")
    try:
        database.assert_foreign_keys_enabled()
        assert len(OWNER_METADATA.sorted_tables) == 24
        yield database
    finally:
        database.close()


def generic_uow_factory(database: OwnerDatabase):
    factory = create_generic_task_uow_factory(database.session_factory)
    assert factory is not None
    return factory


def make_runtime(
    ids: tuple[str, ...] = ("task-created", "ledger-created"),
    *,
    provider_status: Any = None,
    clock: datetime = NOW,
) -> tuple[GenericTaskRuntime, IdSequence]:
    sequence = IdSequence(ids)
    return (
        GenericTaskRuntime(
            clock=lambda: clock,
            id_factory=sequence,
            provider_status=provider_status,
        ),
        sequence,
    )


def observe_sql(database: OwnerDatabase) -> tuple[SqlObservation, Any]:
    observation = SqlObservation(statements=[])

    def before_cursor_execute(_conn, _cursor, statement, _parameters, _context, _many):
        observation.statements.append(statement)

    def on_commit(_connection):
        observation.commits += 1

    def on_rollback(_connection):
        observation.rollbacks += 1

    event.listen(database.engine, "before_cursor_execute", before_cursor_execute)
    event.listen(database.engine, "commit", on_commit)
    event.listen(database.engine, "rollback", on_rollback)

    def stop() -> None:
        event.remove(database.engine, "before_cursor_execute", before_cursor_execute)
        event.remove(database.engine, "commit", on_commit)
        event.remove(database.engine, "rollback", on_rollback)

    return observation, stop


def seed_task(
    database: OwnerDatabase,
    task_id: str,
    *,
    user_id: str = "user-a",
    task_type: str = "image",
    status: str = "processing",
    credit_cost: int = 0,
    result: str | None = None,
    request_data: str | None = "{}",
    external_task_id: str | None = None,
    external_provider: str | None = None,
    created_at: datetime = NOW,
) -> None:
    with database.engine.begin() as connection:
        connection.execute(
            insert(OWNER_TABLES["ai_tasks"]).values(
                id=task_id,
                user_id=user_id,
                type=task_type,
                message_id="",
                status=status,
                credit_cost=credit_cost,
                result=result,
                request_data=request_data,
                model_name=None,
                progress=0,
                progress_message=None,
                external_task_id=external_task_id,
                external_provider=external_provider,
                claimed_by=None,
                lease_until=None,
                execution_generation=0,
                claim_token=None,
                recovery_status="ready",
                billing_status="unbilled",
                user_cancelled_at=None,
                cancellation_reason=None,
                created_at=created_at,
                updated_at=created_at,
            )
        )


def seed_submission(
    database: OwnerDatabase,
    submission_id: str,
    task_ids: Any,
    *,
    user_id: str = "user-a",
) -> None:
    with database.engine.begin() as connection:
        connection.execute(
            insert(OWNER_TABLES["task_submissions"]).values(
                id=submission_id,
                user_id=user_id,
                operation="generic.guard.test",
                idempotency_key=submission_id,
                request_digest="test-digest",
                task_ids=task_ids,
                task_quote_id=None,
                status="accepted",
                created_at=NOW,
            )
        )


def seed_usage_debit(
    database: OwnerDatabase,
    task_id: str,
    *,
    debit_id: str = "generic-debit",
    user_id: str = "user-a",
    task_type: str = "image",
    credit_cost: int = 10,
    number: int = 0,
) -> None:
    seed_task(
        database,
        task_id,
        user_id=user_id,
        task_type=task_type,
        credit_cost=credit_cost,
    )
    if number == 0:
        with database.engine.begin() as connection:
            connection.execute(
                update(OWNER_TABLES["user_credits"])
                .where(OWNER_TABLES["user_credits"].c.user_id == user_id)
                .values(credits=221)
            )
    with database.engine.begin() as connection:
        connection.execute(
            insert(OWNER_TABLES["credit_logs"]).values(
                id=debit_id,
                user_id=user_id,
                amount=-credit_cost,
                balance_after=221,
                type="usage",
                description=f"seed debit {debit_id}",
                task_id=task_id,
                business_key=f"seed-business-{debit_id}",
                billing_unit_id=None,
                related_debit_id=None,
                created_at=NOW,
            )
        )


def seed_billing_unit(database: OwnerDatabase, task_id: str, *, billing_id: str | None = None) -> None:
    unit_id = billing_id or f"billing-{task_id}"
    with database.engine.begin() as connection:
        connection.execute(
            insert(OWNER_TABLES["billing_units"]).values(
                id=unit_id,
                task_id=task_id,
                item_key=f"item-{task_id}",
                operation="generic.guard.test",
                step_graph_version="test-v1",
                input_digest="test-input",
                quoted_amount=0,
                debited_amount=0,
                refunded_amount=0,
                billing_status="unbilled",
                result_status="pending",
                first_intent_at=None,
                deadline_at=None,
                success_confirmed_at=None,
                user_cancelled_at=None,
                cancellation_reason=None,
                quote_id=None,
                quote_unit_key=None,
                candidate_digest=None,
                created_at=NOW,
                updated_at=NOW,
            )
        )


def seed_debit_row(
    database: OwnerDatabase,
    task_id: str,
    *,
    debit_id: str,
    user_id: str = "user-a",
    amount: int = -10,
    balance_after: int = 221,
) -> None:
    with database.engine.begin() as connection:
        connection.execute(
            insert(OWNER_TABLES["credit_logs"]).values(
                id=debit_id,
                user_id=user_id,
                amount=amount,
                balance_after=balance_after,
                type="usage",
                description=f"seed debit {debit_id}",
                task_id=task_id,
                business_key=f"seed-business-{debit_id}",
                billing_unit_id=None,
                related_debit_id=None,
                created_at=NOW,
            )
        )


def seed_refund_row(
    database: OwnerDatabase,
    debit_id: str,
    *,
    refund_id: str = "generic-seed-refund",
    user_id: str = "user-a",
    task_id: str = "g-refund",
    amount: int = 10,
) -> None:
    with database.engine.begin() as connection:
        connection.execute(
            insert(OWNER_TABLES["credit_logs"]).values(
                id=refund_id,
                user_id=user_id,
                amount=amount,
                balance_after=231,
                type="refund",
                description="seed existing refund",
                task_id=task_id,
                business_key=f"seed-business-{refund_id}",
                billing_unit_id=None,
                related_debit_id=debit_id,
                created_at=NOW,
            )
        )


def rows(database: OwnerDatabase, table_name: str) -> list[dict[str, Any]]:
    with database.engine.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                select(OWNER_TABLES[table_name]).order_by(
                    *OWNER_TABLES[table_name].primary_key
                )
            ).mappings()
        ]


def read_row(database: OwnerDatabase, table_name: str, key: str) -> dict[str, Any] | None:
    table = OWNER_TABLES[table_name]
    with database.engine.connect() as connection:
        row = connection.execute(
            select(table).where(table.c.id == key)
        ).mappings().one_or_none()
        return dict(row) if row is not None else None


def unchanged_except(
    before: dict[str, list[dict[str, object]]],
    after: dict[str, list[dict[str, object]]],
    allowed_tables: set[str],
) -> None:
    assert set(before) == set(CONSERVATION_TABLES)
    assert set(after) == set(CONSERVATION_TABLES)
    for table_name in CONSERVATION_TABLES:
        if table_name not in allowed_tables:
            assert after[table_name] == before[table_name], table_name



class CountingUnitOfWork:
    """Count transaction boundaries while still exercising the real SQLite adapter."""

    def __init__(
        self,
        inner: Any,
        *,
        fail_after_commit: bool = False,
        fail_readback: bool = False,
        fail_refresh: bool = False,
    ) -> None:
        self.inner = inner
        self.fail_after_commit = fail_after_commit
        self.fail_readback = fail_readback
        self.fail_refresh = fail_refresh
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def commit(self) -> None:
        self.commit_calls += 1
        self.inner.commit()
        if self.fail_after_commit:
            raise RuntimeError("simulated lost commit acknowledgement")

    def rollback(self) -> None:
        self.rollback_calls += 1
        self.inner.rollback()

    def read_task_after_commit(self, task_id: str):
        if self.fail_readback:
            raise RuntimeError("simulated committed readback failure")
        return self.inner.read_task_after_commit(task_id)

    def refresh_created_task(self, task_id: str):
        if self.fail_refresh:
            raise RuntimeError("simulated create refresh failure")
        return self.inner.refresh_created_task(task_id)

    def close(self) -> None:
        self.close_calls += 1
        self.inner.close()


def counting_factory(
    factory: Any,
    *,
    fail_after_commit: bool = False,
    fail_readback: bool = False,
    fail_refresh: bool = False,
) -> tuple[Any, list[CountingUnitOfWork]]:
    units: list[CountingUnitOfWork] = []

    def create() -> CountingUnitOfWork:
        unit = CountingUnitOfWork(
            factory(),
            fail_after_commit=fail_after_commit,
            fail_readback=fail_readback,
            fail_refresh=fail_refresh,
        )
        units.append(unit)
        return unit

    return create, units
