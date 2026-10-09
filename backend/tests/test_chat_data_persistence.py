from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    delete,
    event,
    insert,
    select,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy import create_engine

from haoai_backend.chat_data import application
from haoai_backend.chat_data.errors import ChatDataNotFound
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.chat_data.persistence import SqlAlchemyChatDataUnitOfWork
from haoai_backend.chat_data.tables import (
    chapter_locks,
    chat_messages,
    chapters,
    metadata as chat_metadata,
    system_configs,
)
from haoai_backend.series_access.tables import (
    metadata as access_metadata,
    series,
    users,
)


history_metadata = MetaData()
full_ai_tasks = Table(
    "ai_tasks",
    history_metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey(users.c.id), nullable=False),
    Column("type", String(20), nullable=False),
    Column("message_id", String(36), nullable=False),
    Column("status", String(20), nullable=False),
    Column("credit_cost", Integer, nullable=False, server_default="0"),
    Column("result", Text, nullable=True),
    Column("request_data", Text, nullable=True),
    Column("model_name", String(255), nullable=True),
    Column("progress", Integer, nullable=False, server_default="0"),
    Column("progress_message", Text, nullable=True),
    Column("external_task_id", String(255), nullable=True),
    Column("external_provider", String(50), nullable=True),
    Column("claimed_by", String(64), nullable=True),
    Column("lease_until", DateTime, nullable=True),
    Column("execution_generation", Integer, nullable=False, server_default="0"),
    Column("claim_token", String(64), nullable=True),
    Column("recovery_status", String(32), nullable=False, default="ready", server_default="ready"),
    Column("billing_status", String(32), nullable=False, default="unbilled", server_default="unbilled"),
    Column("user_cancelled_at", DateTime, nullable=True),
    Column("cancellation_reason", String(64), nullable=True),
    Column("created_at", DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_at", DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    CheckConstraint(
        "billing_status IN ('legacy_unreconciled', 'unbilled', 'charged', "
        "'partially_refunded', 'refunded', 'no_charge')",
        name="ck_ai_tasks_billing_status",
    ),
)


task_quotes = Table(
    "task_quotes",
    history_metadata,
    Column("id", String(36), primary_key=True),
)

billing_units = Table(
    "billing_units",
    history_metadata,
    Column("id", String(36), primary_key=True),
    Column("task_id", String(36), ForeignKey("ai_tasks.id"), nullable=False),
    Column("item_key", String(255), nullable=False),
    Column("operation", String(64), nullable=False),
    Column("step_graph_version", String(64), nullable=False),
    Column("input_digest", String(128), nullable=False),
    Column("quoted_amount", Integer, nullable=False),
    Column("debited_amount", Integer, nullable=False, server_default="0"),
    Column("refunded_amount", Integer, nullable=False, server_default="0"),
    Column("billing_status", String(32), nullable=False, server_default="unbilled"),
    Column("result_status", String(32), nullable=False, server_default="pending"),
    Column("first_intent_at", DateTime(timezone=True), nullable=True),
    Column("deadline_at", DateTime(timezone=True), nullable=True),
    Column("success_confirmed_at", DateTime(timezone=True), nullable=True),
    Column("user_cancelled_at", DateTime(timezone=True), nullable=True),
    Column("cancellation_reason", String(64), nullable=True),
    Column("quote_id", String(36), ForeignKey("task_quotes.id"), nullable=True),
    Column("quote_unit_key", String(255), nullable=True),
    Column("candidate_digest", String(128), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    UniqueConstraint("task_id", "item_key", name="uq_billing_units_task_item"),
    UniqueConstraint("quote_id", "quote_unit_key", name="uq_billing_units_quote_item"),
    CheckConstraint("quoted_amount >= 0", name="ck_billing_units_quoted_nonnegative"),
    CheckConstraint("debited_amount >= 0", name="ck_billing_units_debited_nonnegative"),
    CheckConstraint("refunded_amount >= 0", name="ck_billing_units_refunded_nonnegative"),
    CheckConstraint("refunded_amount <= debited_amount", name="ck_billing_units_refund_not_over_debit"),
    CheckConstraint(
        "billing_status IN ('unbilled', 'charged', 'refunded', 'no_charge')",
        name="ck_billing_units_billing_status",
    ),
    CheckConstraint(
        "result_status IN ('pending', 'partial', 'succeeded', 'failed', 'unknown')",
        name="ck_billing_units_result_status",
    ),
)

execution_steps = Table(
    "execution_steps",
    history_metadata,
    Column("id", String(36), primary_key=True),
    Column("billing_unit_id", String(36), ForeignKey("billing_units.id"), nullable=False),
    Column("step_key", String(128), nullable=False),
    Column("dependencies", JSON, nullable=False, server_default=text("'[]'")),
    Column("input_digest", String(128), nullable=False),
    Column("input_snapshot", JSON, nullable=True),
    Column("required", Boolean, nullable=False, server_default="true"),
    Column("step_order", Integer, nullable=False, server_default="0"),
    Column("execution_status", String(32), nullable=False, server_default="prepared"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    UniqueConstraint("billing_unit_id", "step_key", name="uq_execution_steps_unit_key"),
    CheckConstraint("step_order >= 0", name="ck_execution_steps_order_nonnegative"),
    CheckConstraint(
        "execution_status IN ('prepared', 'submitting', 'submitted', 'result_observed', "
        "'applied', 'failed', 'reconciling', 'deterministic_empty')",
        name="ck_execution_steps_status",
    ),
)

credit_logs = Table(
    "credit_logs",
    history_metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey(users.c.id), nullable=False),
    Column("amount", Integer, nullable=False),
    Column("balance_after", Integer, nullable=False),
    Column("type", String(20), nullable=False),
    Column("description", String(500), nullable=True),
    Column("task_id", String(36), nullable=True),
    Column("business_key", String(255), nullable=True),
    Column("billing_unit_id", String(36), ForeignKey("billing_units.id"), nullable=True),
    Column("related_debit_id", String(36), ForeignKey("credit_logs.id"), nullable=True),
    Column("created_at", DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    UniqueConstraint("business_key", name="uq_credit_logs_business_key"),
)

HISTORY_TABLES = (full_ai_tasks, billing_units, execution_steps, credit_logs)


def _seed_task_history(
    database: "ChatDatabase",
    task_id: str,
    message_id: str,
    *,
    user_id: str = "user-a",
) -> None:
    now = datetime(2026, 10, 9, 12, 0)
    unit_id = f"unit-{task_id}"
    step_id = f"step-{task_id}"
    debit_id = f"debit-{task_id}"

    with database.engine.begin() as connection:
        connection.execute(
            insert(full_ai_tasks).values(
                id=task_id,
                user_id=user_id,
                type="chat",
                message_id=message_id,
                status="completed",
                credit_cost=5,
                result=f"result:{task_id}",
                request_data=f'{{"task":"{task_id}"}}',
                model_name="model-a",
                progress=100,
                progress_message="complete",
                external_task_id=f"external-{task_id}",
                external_provider="provider-a",
                claimed_by="worker-a",
                lease_until=now + timedelta(minutes=1),
                execution_generation=2,
                claim_token=f"claim-{task_id}",
                recovery_status="ready",
                billing_status="charged",
                created_at=now,
                updated_at=now,
            )
        )
        connection.execute(
            insert(billing_units).values(
                id=unit_id,
                task_id=task_id,
                item_key=f"item-{task_id}",
                operation="chat",
                step_graph_version="v1",
                input_digest=f"input-{task_id}",
                quoted_amount=5,
                debited_amount=5,
                refunded_amount=2,
                billing_status="charged",
                result_status="succeeded",
                first_intent_at=now,
                deadline_at=now + timedelta(minutes=5),
                success_confirmed_at=now + timedelta(seconds=1),
                user_cancelled_at=None,
                cancellation_reason=None,
                quote_id=None,
                quote_unit_key=None,
                candidate_digest=f"candidate-{task_id}",
                created_at=now,
                updated_at=now,
            )
        )
        connection.execute(
            insert(execution_steps).values(
                id=step_id,
                billing_unit_id=unit_id,
                step_key="generate",
                dependencies=["prepare"],
                input_digest=f"step-input-{task_id}",
                input_snapshot={"message_id": message_id},
                required=True,
                step_order=1,
                execution_status="applied",
                created_at=now,
                updated_at=now,
            )
        )
        connection.execute(
            insert(credit_logs).values(
                id=debit_id,
                user_id=user_id,
                amount=-5,
                balance_after=95,
                type="usage",
                description=f"usage:{task_id}",
                task_id=task_id,
                business_key=f"usage:{task_id}",
                billing_unit_id=unit_id,
                related_debit_id=None,
                created_at=now,
            )
        )
        connection.execute(
            insert(credit_logs).values(
                id=f"refund-{task_id}",
                user_id=user_id,
                amount=2,
                balance_after=97,
                type="refund",
                description=f"refund:{task_id}",
                task_id=task_id,
                business_key=f"refund:{task_id}",
                billing_unit_id=unit_id,
                related_debit_id=debit_id,
                created_at=now + timedelta(seconds=2),
            )
        )


def _history_snapshot(session: Session) -> dict[str, list[dict[str, Any]]]:
    return {
        table.name: [
            dict(row)
            for row in session.execute(
                select(table).order_by(table.c.id)
            ).mappings().all()
        ]
        for table in HISTORY_TABLES
    }


@dataclass
class ChatDatabase:
    engine: Engine
    session_factory: sessionmaker[Session]

    def seed_chapter(self, chapter_id: str, series_id: str = "series-a") -> None:
        with self.engine.begin() as connection:
            connection.execute(
                insert(chapters).values(id=chapter_id, series_id=series_id)
            )

    def seed_message(
        self,
        message_id: str,
        chapter_id: str = "chapter-a",
        *,
        frame_index: int | None = None,
        asset_type: str | None = None,
        asset_id: str | None = None,
        chat_mode: str = "chat",
        role: str = "assistant",
        content: str = "content",
        model_name: str | None = None,
        created_at: datetime | None = None,
    ) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                insert(chat_messages).values(
                    id=message_id,
                    chapter_id=chapter_id,
                    frame_index=frame_index,
                    asset_type=asset_type,
                    asset_id=asset_id,
                    chat_mode=chat_mode,
                    role=role,
                    content=content,
                    model_name=model_name,
                    created_at=created_at or datetime(2026, 10, 9, 12, 0),
                )
            )


def create_database(path: Path, *, foreign_keys: bool = True) -> ChatDatabase:
    engine = create_engine(f"sqlite:///{path}", future=True)
    if foreign_keys:
        @event.listens_for(engine, "connect")
        def enable_foreign_keys(connection: Any, record: Any) -> None:
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    access_metadata.create_all(engine)
    chat_tables = [table for table in chat_metadata.sorted_tables if table.name != "ai_tasks"]
    chat_metadata.create_all(engine, tables=chat_tables)
    history_metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False, future=True)
    with engine.begin() as connection:
        connection.execute(
            insert(users),
            [
                {"id": "user-a", "username": "Alice"},
                {"id": "user-b", "username": "Bob"},
            ],
        )
        connection.execute(
            insert(series),
            [
                {"id": "series-a", "user_id": "user-a", "team_id": None, "claimed_by": None},
                {"id": "series-b", "user_id": "user-b", "team_id": None, "claimed_by": None},
            ],
        )
        connection.execute(
            insert(chapters).values(id="chapter-a", series_id="series-a")
        )
        connection.execute(
            insert(chapters).values(id="chapter-b", series_id="series-b")
        )
    return ChatDatabase(engine=engine, session_factory=factory)


@pytest.fixture
def database(tmp_path: Path):
    database = create_database(tmp_path / "chat-data.sqlite")
    yield database
    database.engine.dispose()


def test_core_lists_preserve_filters_asset_rows_and_created_order(database: ChatDatabase) -> None:
    database.seed_message(
        "message-later",
        frame_index=1,
        created_at=datetime(2026, 10, 9, 12, 2),
    )
    database.seed_message(
        "message-asset",
        frame_index=-1,
        asset_type="",
        asset_id="asset-a",
        created_at=datetime(2026, 10, 9, 12, 1),
    )
    database.seed_message(
        "message-image",
        chat_mode="image",
        created_at=datetime(2026, 10, 9, 12, 0),
    )

    with database.session_factory() as session:
        uow = SqlAlchemyChatDataUnitOfWork(session)
        ordinary = uow.list_chat_messages("chapter-a", "chat", None)
        negative_frame = uow.list_chat_messages("chapter-a", "chat", -1)
        asset = uow.list_asset_chat_messages("chapter-a", "", "asset-a", "chat")
        uow.close()

    assert [row.id for row in ordinary] == ["message-asset", "message-later"]
    assert [row.id for row in negative_frame] == ["message-asset"]
    assert [row.id for row in asset] == ["message-asset"]
    assert ordinary[0].asset_type == ""


def test_statistics_use_natural_joins_and_preserve_grouped_rows(database: ChatDatabase) -> None:
    database.seed_message("chapter-message", "chapter-a", model_name="unused")
    database.seed_chapter("chapter-c", "series-a")
    database.seed_message("series-message", "chapter-c", model_name="unused")
    with database.engine.begin() as connection:
        connection.execute(
            insert(full_ai_tasks),
            [
                {
                    "id": "task-a1",
                    "user_id": "user-a",
                    "type": "chat",
                    "message_id": "chapter-message",
                    "status": "completed",
                    "credit_cost": -2,
                    "model_name": None,
                },
                {
                    "id": "task-a2",
                    "user_id": "user-a",
                    "type": "chat",
                    "message_id": "chapter-message",
                    "status": "failed",
                    "credit_cost": 99,
                    "model_name": "",
                },
                {
                    "id": "task-a3",
                    "user_id": "user-a",
                    "type": "chat",
                    "message_id": "chapter-message",
                    "status": "queued",
                    "credit_cost": 100,
                    "model_name": "Ignored",
                },
                {
                    "id": "task-b1",
                    "user_id": "user-a",
                    "type": "chat",
                    "message_id": "series-message",
                    "status": "completed",
                    "credit_cost": 7,
                    "model_name": "Beta",
                },
                {
                    "id": "task-b2",
                    "user_id": "user-b",
                    "type": "video",
                    "message_id": "series-message",
                    "status": "completed",
                    "credit_cost": 5,
                    "model_name": "Beta",
                },
                {
                    "id": "task-orphan",
                    "user_id": "user-a",
                    "type": "chat",
                    "message_id": "missing-message",
                    "status": "completed",
                    "credit_cost": 80,
                    "model_name": "Orphan",
                },
            ],
        )

    with database.session_factory() as session:
        uow = SqlAlchemyChatDataUnitOfWork(session)
        chapter_rows = uow.chapter_statistics("chapter-a")
        series_rows = uow.series_statistics("series-a")
        uow.close()

    assert [(row["model_name"], row["status"], row["calls"], row["credits"]) for row in chapter_rows] == [
        (None, "completed", 1, -2),
        ("", "failed", 1, 99),
    ]
    assert {(row["model_name"], row["status"], row["calls"]) for row in series_rows} == {
        (None, "completed", 1),
        ("", "failed", 1),
        ("Beta", "completed", 2),
    }
    assert not any(row["model_name"] == "Orphan" for row in series_rows)
    assert not any(row["model_name"] == "Ignored" for row in series_rows)


def test_lock_refresh_only_changes_existing_owned_lock_with_legacy_fallback(database: ChatDatabase) -> None:
    now = datetime(2026, 10, 9, 12, 0)
    expired = now - timedelta(days=1)
    with database.engine.begin() as connection:
        connection.execute(
            insert(system_configs).values(
                id="config-zero",
                chapter_lock_idle_minutes=0,
            )
        )
        connection.execute(
            insert(chapter_locks),
            [
                {
                    "id": "own-lock",
                    "chapter_id": "chapter-a",
                    "user_id": "user-a",
                    "username": "Alice",
                    "acquired_at": expired,
                    "last_active_at": expired,
                    "expires_at": expired,
                },
                {
                    "id": "foreign-lock",
                    "chapter_id": "chapter-b",
                    "user_id": "user-b",
                    "username": "Bob",
                    "acquired_at": expired,
                    "last_active_at": expired,
                    "expires_at": expired,
                },
            ],
        )
    with database.session_factory() as session:
        locks_before = {
            row["id"]: dict(row)
            for row in session.execute(select(chapter_locks)).mappings().all()
        }

    with database.session_factory() as session:
        uow = SqlAlchemyChatDataUnitOfWork(session, now=lambda: now)
        uow.refresh_chapter_lock("chapter-a", "user-a")
        uow.refresh_chapter_lock("chapter-b", "user-a")
        uow.commit()
        uow.close()

    with database.session_factory() as session:
        rows = {
            row["id"]: row
            for row in session.execute(select(chapter_locks)).mappings().all()
        }

    assert rows["own-lock"] == {
        **locks_before["own-lock"],
        "last_active_at": now,
        "expires_at": now + timedelta(minutes=15),
    }
    assert rows["foreign-lock"] == locks_before["foreign-lock"]


def test_core_update_and_delete_never_touch_task_history(database: ChatDatabase) -> None:
    with database.engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        credit_foreign_keys = connection.exec_driver_sql(
            "PRAGMA foreign_key_list(credit_logs)"
        ).mappings().all()
        task_foreign_keys = connection.exec_driver_sql(
            "PRAGMA foreign_key_list(ai_tasks)"
        ).mappings().all()

    assert any(
        row["table"] == "credit_logs"
        and row["from"] == "related_debit_id"
        and row["to"] == "id"
        for row in credit_foreign_keys
    )
    assert not any(row["from"] == "message_id" for row in task_foreign_keys)

    database.seed_message("message-a", content="before")
    database.seed_message("image-a", asset_type="image", content="image")
    _seed_task_history(database, "task-a", "message-a")
    _seed_task_history(database, "task-image", "image-a")

    with database.session_factory() as session:
        history_before = _history_snapshot(session)

    with pytest.raises(ChatDataNotFound, match="消息不存在"):
        application.delete_single_chat_message(
            lambda: SqlAlchemyChatDataUnitOfWork(database.session_factory()),
            TrustedActor("user-a"),
            "chapter-a",
            "image-a",
        )
    with database.session_factory() as session:
        assert _history_snapshot(session) == history_before

    with pytest.raises(ChatDataNotFound, match="消息不存在"):
        application.delete_single_chat_message(
            lambda: SqlAlchemyChatDataUnitOfWork(database.session_factory()),
            TrustedActor("user-a"),
            "chapter-a",
            "missing-message",
        )
    with database.session_factory() as session:
        assert _history_snapshot(session) == history_before

    with database.session_factory() as session:
        uow = SqlAlchemyChatDataUnitOfWork(session)
        uow.update_chat_message_content("message-a", "chapter-a", "after")
        uow.commit()
        uow.delete_single_chat_message("message-a", "chapter-a")
        uow.commit()
        uow.close()

    with database.session_factory() as session:
        remaining = session.execute(
            select(chat_messages.c.id).order_by(chat_messages.c.id)
        ).scalars().all()
        assert remaining == ["image-a"]
        assert _history_snapshot(session) == history_before
        assert len(history_before["billing_units"]) == 2
        assert len(history_before["execution_steps"]) == 2
        assert len(history_before["credit_logs"]) == 4


def test_foreign_key_rejects_message_for_missing_body_chapter(database: ChatDatabase) -> None:
    from haoai_backend.chat_data.domain import NewChatMessage
    from sqlalchemy.exc import IntegrityError

    with database.session_factory() as session:
        uow = SqlAlchemyChatDataUnitOfWork(
            session,
            new_id=lambda: "message-bad",
        )
        with pytest.raises(IntegrityError):
            uow.create_chat_message(
                NewChatMessage(
                    chapter_id="missing",
                    frame_index=None,
                    asset_type=None,
                    asset_id=None,
                    chat_mode="chat",
                    role="user",
                    content="body chapter",
                    model_name=None,
                )
            )
            uow.commit()
        uow.rollback()
        uow.close()

    with database.session_factory() as session:
        assert session.execute(select(chat_messages)).all() == []



def test_bulk_delete_covers_modes_and_frames_without_touching_task_history(
    database: ChatDatabase,
) -> None:
    database.seed_message("chat-frame-one", frame_index=1, chat_mode="chat")
    database.seed_message("image-frame-one", frame_index=1, chat_mode="image")
    database.seed_message("chat-frame-two", frame_index=2, chat_mode="chat")
    database.seed_message("other-chapter", "chapter-b", frame_index=1, chat_mode="chat")
    _seed_task_history(database, "task-chat", "chat-frame-one")
    _seed_task_history(database, "task-image", "image-frame-one")
    _seed_task_history(database, "task-two", "chat-frame-two")
    _seed_task_history(database, "task-other", "other-chapter", user_id="user-b")

    with database.session_factory() as session:
        history_before = _history_snapshot(session)

    with database.session_factory() as session:
        uow = SqlAlchemyChatDataUnitOfWork(session)
        uow.delete_chat_messages("chapter-a", 1)
        uow.commit()
        uow.delete_chat_messages("chapter-a", None)
        uow.commit()
        uow.delete_chat_messages("chapter-a", 99)
        uow.commit()
        uow.close()

    with database.session_factory() as session:
        remaining = [
            dict(row)
            for row in session.execute(
                select(chat_messages).order_by(chat_messages.c.id)
            ).mappings().all()
        ]
        assert remaining == [
            {
                "id": "other-chapter",
                "chapter_id": "chapter-b",
                "frame_index": 1,
                "asset_type": None,
                "asset_id": None,
                "chat_mode": "chat",
                "role": "assistant",
                "content": "content",
                "model_name": None,
                "created_at": datetime(2026, 10, 9, 12, 0),
            }
        ]
        assert _history_snapshot(session) == history_before

    assert {name: len(rows) for name, rows in history_before.items()} == {
        "ai_tasks": 4,
        "billing_units": 4,
        "execution_steps": 4,
        "credit_logs": 8,
    }


def test_negative_lock_idle_setting_is_preserved(database: ChatDatabase) -> None:
    now = datetime(2026, 10, 9, 12, 0)
    with database.engine.begin() as connection:
        connection.execute(
            insert(system_configs).values(id="config-negative", chapter_lock_idle_minutes=-4)
        )
        connection.execute(
            insert(chapter_locks).values(
                id="negative-lock",
                chapter_id="chapter-a",
                user_id="user-a",
                username="Alice",
                acquired_at=now,
                last_active_at=now,
                expires_at=now,
            )
        )

    with database.session_factory() as session:
        uow = SqlAlchemyChatDataUnitOfWork(session, now=lambda: now)
        uow.refresh_chapter_lock("chapter-a", "user-a")
        uow.refresh_chapter_lock("chapter-a", "another-user")
        uow.refresh_chapter_lock("missing-chapter", "user-a")
        uow.commit()
        uow.close()

    with database.session_factory() as session:
        lock = session.execute(
            select(chapter_locks).where(chapter_locks.c.id == "negative-lock")
        ).mappings().one()
    assert lock["last_active_at"] == now
    assert lock["expires_at"] == now - timedelta(minutes=4)
