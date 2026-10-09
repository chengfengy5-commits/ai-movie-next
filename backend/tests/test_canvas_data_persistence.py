from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import ClassVar

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
    create_engine,
    event,
    insert,
    select,
    text,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.canvas_data.errors import CanvasDataForbidden
from haoai_backend.canvas_data.persistence import SqlAlchemyCanvasDataUnitOfWork
from haoai_backend.canvas_data.tables import canvas_documents, metadata as canvas_metadata
from haoai_backend.shared.identity import TrustedActor

OWNER_METADATA = MetaData()
users = Table(
    "users", OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("username", String(100), nullable=False),
)
series = Table(
    "series", OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("team_id", String(36), nullable=True),
    Column("claimed_by", String(36), nullable=True),
)
chapters = Table(
    "chapters", OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("series_id", String(36), ForeignKey("series.id"), nullable=False),
    Column("title", String(255), nullable=False),
    Column("content", Text, nullable=True),
    Column("order", Integer, nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)
team_members = Table(
    "team_members", OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("team_id", String(36), nullable=False),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("role", String(20), nullable=False),
    Column("permissions", Text, nullable=True),
)
chapter_locks = Table(
    "chapter_locks", OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("chapter_id", String(36), ForeignKey("chapters.id"), nullable=False),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("username", String(100), nullable=False),
    Column("acquired_at", DateTime, nullable=False),
    Column("last_active_at", DateTime, nullable=False),
    Column("expires_at", DateTime, nullable=False),
)
system_configs = Table(
    "system_configs", OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("chapter_lock_idle_minutes", Integer, nullable=False),
)
storyboard_assets = Table(
    "storyboard_assets", OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("series_id", String(36), ForeignKey("series.id"), nullable=False),
    Column("chapter_id", String(36), ForeignKey("chapters.id"), nullable=False),
    Column("frame_index", Integer, nullable=False),
    Column("name", String(255), nullable=False),
    Column("description", Text, nullable=True),
    Column("image_url", String(500), nullable=True),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)
storyboard_media_states = Table(
    "storyboard_media_states", OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("chapter_id", String(36), ForeignKey("chapters.id"), nullable=False),
    Column("storyboard_asset_id", String(36), nullable=False),
    Column("media_revision", Integer, nullable=False),
    Column("asset_image_digest", String(64), nullable=True),
    Column("preview_digest", String(64), nullable=True),
    Column("source_valid", Boolean, nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)
personal_production_notes = Table(
    "personal_production_notes", OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("chapter_id", String(36), ForeignKey("chapters.id"), nullable=False),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("frame_notes", JSON, nullable=False),
    Column("resume_frame_id", String(36), nullable=True),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)
chat_messages = Table(
    "chat_messages",
    OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("chapter_id", String(36), ForeignKey("chapters.id"), nullable=False),
    Column("frame_index", Integer, nullable=True),
    Column("asset_type", String(20), nullable=True),
    Column("asset_id", String(36), nullable=True),
    Column("chat_mode", String(20), nullable=False, default="chat"),
    Column("role", String(20), nullable=False),
    Column("content", Text, nullable=False),
    Column("model_name", String(255), nullable=True),
    Column("created_at", DateTime, nullable=False),
)
ai_tasks = Table(
    "ai_tasks",
    OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("type", String(20), nullable=False),
    Column("message_id", String(36), nullable=False),
    Column("status", String(20), nullable=False, default="processing"),
    Column("credit_cost", Integer, nullable=False, default=0),
    Column("result", Text, nullable=True),
    Column("request_data", Text, nullable=True),
    Column("model_name", String(255), nullable=True),
    Column("progress", Integer, nullable=False, default=0),
    Column("progress_message", Text, nullable=True),
    Column("external_task_id", String(255), nullable=True),
    Column("external_provider", String(50), nullable=True),
    Column("claimed_by", String(64), nullable=True),
    Column("lease_until", DateTime, nullable=True),
    Column("execution_generation", Integer, nullable=False, default=0, server_default="0"),
    Column("claim_token", String(64), nullable=True),
    Column("recovery_status", String(32), nullable=False, default="ready", server_default="ready"),
    Column("billing_status", String(32), nullable=False, default="unbilled", server_default="unbilled"),
    Column("user_cancelled_at", DateTime(timezone=True), nullable=True),
    Column("cancellation_reason", String(64), nullable=True),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    CheckConstraint(
        "billing_status IN ('legacy_unreconciled', 'unbilled', 'charged', "
        "'partially_refunded', 'refunded', 'no_charge')",
        name="ck_ai_tasks_billing_status",
    ),
)
task_quotes = Table(
    "task_quotes",
    OWNER_METADATA,
    Column("id", String(36), primary_key=True),
)
billing_units = Table(
    "billing_units",
    OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("task_id", String(36), ForeignKey("ai_tasks.id"), nullable=False),
    Column("item_key", String(255), nullable=False),
    Column("operation", String(64), nullable=False),
    Column("step_graph_version", String(64), nullable=False),
    Column("input_digest", String(128), nullable=False),
    Column("quoted_amount", Integer, nullable=False),
    Column("debited_amount", Integer, nullable=False, default=0, server_default="0"),
    Column("refunded_amount", Integer, nullable=False, default=0, server_default="0"),
    Column("billing_status", String(32), nullable=False, default="unbilled", server_default="unbilled"),
    Column("result_status", String(32), nullable=False, default="pending", server_default="pending"),
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
    OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("billing_unit_id", String(36), ForeignKey("billing_units.id"), nullable=False),
    Column("step_key", String(128), nullable=False),
    Column("dependencies", JSON, nullable=False, default=list, server_default=text("'[]'")),
    Column("input_digest", String(128), nullable=False),
    Column("input_snapshot", JSON, nullable=True),
    Column("required", Boolean, nullable=False, default=True, server_default="true"),
    Column("step_order", Integer, nullable=False, default=0, server_default="0"),
    Column("execution_status", String(32), nullable=False, default="prepared", server_default="prepared"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    UniqueConstraint("billing_unit_id", "step_key", name="uq_execution_steps_unit_key"),
    CheckConstraint("step_order >= 0", name="ck_execution_steps_order_nonnegative"),
    CheckConstraint(
        "execution_status IN ('prepared', 'submitting', 'submitted', "
        "'result_observed', 'applied', 'failed', 'reconciling', 'deterministic_empty')",
        name="ck_execution_steps_status",
    ),
)
credit_logs = Table(
    "credit_logs",
    OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("amount", Integer, nullable=False),
    Column("balance_after", Integer, nullable=False),
    Column("type", String(20), nullable=False),
    Column("description", String(500), nullable=True),
    Column("task_id", String(36), nullable=True),
    Column("business_key", String(255), nullable=True),
    Column("billing_unit_id", String(36), ForeignKey("billing_units.id"), nullable=True),
    Column("related_debit_id", String(36), ForeignKey("credit_logs.id"), nullable=True),
    Column("created_at", DateTime, nullable=False),
    UniqueConstraint("business_key", name="uq_credit_logs_business_key"),
)
PRESERVED_TABLES = (
    chapters,
    storyboard_assets,
    storyboard_media_states,
    personal_production_notes,
    chat_messages,
    ai_tasks,
    billing_units,
    execution_steps,
    credit_logs,
)


@dataclass
class CanvasDatabase:
    __test__: ClassVar[bool] = False
    engine: Engine
    session_factory: sessionmaker[Session]

    def seed(self) -> None:
        now = datetime(2026, 1, 1)
        future = datetime(2026, 1, 2)
        with self.engine.begin() as connection:
            connection.execute(insert(users), [{"id": "user-a", "username": "A"}, {"id": "user-b", "username": "B"}])
            connection.execute(
                insert(series).values(id="series-a", user_id="user-a", team_id="team-a", claimed_by=None)
            )
            connection.execute(
                insert(team_members).values(
                    id="member-b", team_id="team-a", user_id="user-b", role="member", permissions="[]"
                )
            )
            connection.execute(
                insert(chapters).values(
                    id="chapter-a", series_id="series-a", title="A", content="[]", order=1,
                    created_at=now, updated_at=now,
                )
            )
            connection.execute(insert(system_configs).values(id="config-a", chapter_lock_idle_minutes=15))
            connection.execute(
                insert(storyboard_assets).values(
                    id="asset-a", series_id="series-a", chapter_id="chapter-a", frame_index=0,
                    name="frame", description="kept", image_url="https://media.invalid/a.jpg",
                    created_at=now, updated_at=now,
                )
            )
            connection.execute(
                insert(storyboard_media_states).values(
                    id="media-a", chapter_id="chapter-a", storyboard_asset_id="asset-a",
                    media_revision=3, asset_image_digest="a" * 64, preview_digest="b" * 64,
                    source_valid=True, created_at=now, updated_at=now,
                )
            )
            connection.execute(
                insert(personal_production_notes).values(
                    id="notes-a", chapter_id="chapter-a", user_id="user-a", revision=4,
                    frame_notes={"frame-a": {"text": "preserved"}}, resume_frame_id="frame-a",
                    created_at=now, updated_at=now,
                )
            )
            connection.execute(
                insert(personal_production_notes).values(
                    id="notes-b", chapter_id="chapter-a", user_id="user-b", revision=2,
                    frame_notes={"frame-a": {"text": "other user"}}, resume_frame_id=None,
                    created_at=now, updated_at=now,
                )
            )
            connection.execute(
                insert(chat_messages).values(
                    id="message-a", chapter_id="chapter-a", frame_index=0, asset_type="storyboard",
                    asset_id="asset-a", chat_mode="chat", role="assistant",
                    content="preserved chat", model_name="model-preserved", created_at=now,
                )
            )
            connection.execute(
                insert(ai_tasks).values(
                    id="task-a", user_id="user-a", type="chat", message_id="message-a",
                    status="completed", credit_cost=2, result="result-preserved",
                    request_data='{"prompt":"preserved"}', model_name="model-preserved",
                    progress=100, progress_message="finished", external_task_id="external-task-a",
                    external_provider="provider-a", claimed_by="worker-a", lease_until=future,
                    execution_generation=4, claim_token="claim-a", recovery_status="ready",
                    billing_status="charged", user_cancelled_at=None, cancellation_reason=None,
                    created_at=now, updated_at=future,
                )
            )
            connection.execute(
                insert(billing_units).values(
                    id="billing-a", task_id="task-a", item_key="chat:message-a", operation="chat",
                    step_graph_version="graph-v1", input_digest="input-digest-a",
                    quoted_amount=3, debited_amount=2, refunded_amount=1,
                    billing_status="charged", result_status="succeeded",
                    first_intent_at=now, deadline_at=future, success_confirmed_at=future,
                    user_cancelled_at=None, cancellation_reason=None, quote_id=None,
                    quote_unit_key=None, candidate_digest="candidate-a", created_at=now, updated_at=future,
                )
            )
            connection.execute(
                insert(execution_steps).values(
                    id="step-a", billing_unit_id="billing-a", step_key="render",
                    dependencies=["prepare"], input_digest="step-input-a",
                    input_snapshot={"source": "preserved"}, required=True, step_order=1,
                    execution_status="applied", created_at=now, updated_at=future,
                )
            )
            connection.execute(
                insert(credit_logs).values(
                    id="credit-debit-a", user_id="user-a", amount=-2, balance_after=8,
                    type="usage", description="chat debit", task_id="task-a",
                    business_key="canvas-debit-a", billing_unit_id="billing-a",
                    related_debit_id=None, created_at=now,
                )
            )
            connection.execute(
                insert(credit_logs).values(
                    id="credit-refund-a", user_id="user-a", amount=1, balance_after=9,
                    type="refund", description="partial refund", task_id="task-a",
                    business_key="canvas-refund-a", billing_unit_id="billing-a",
                    related_debit_id="credit-debit-a", created_at=future,
                )
            )

    def snapshot_preserved_rows(self) -> dict[str, list[dict[str, object]]]:
        with self.session_factory() as session:
            return {
                table.name: [dict(row) for row in session.execute(select(table).order_by(table.c.id)).mappings()]
                for table in PRESERVED_TABLES
            }


@pytest.fixture
def canvas_db(tmp_path: Path) -> CanvasDatabase:
    path = tmp_path / "canvas-owner.sqlite"
    engine = create_engine(f"sqlite:///{path}", future=True)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
    OWNER_METADATA.create_all(engine)
    canvas_metadata.create_all(engine)
    db = CanvasDatabase(engine, sessionmaker(engine, expire_on_commit=False, future=True))
    db.seed()
    yield db
    engine.dispose()


def test_canvas_owner_table_has_full_fields_unique_chapter_and_foreign_keys(canvas_db: CanvasDatabase) -> None:
    assert list(canvas_documents.c.keys()) == [
        "id", "series_id", "chapter_id", "version", "document_json", "created_by",
        "updated_by", "created_at", "updated_at",
    ]
    assert isinstance(canvas_documents.c.document_json.type, Text)
    assert any(constraint.name == "uq_canvas_documents_chapter_id" for constraint in canvas_documents.constraints)
    assert {foreign_key.target_fullname for foreign_key in canvas_documents.foreign_keys} == {
        "series.id", "chapters.id", "users.id"
    }

    with canvas_db.engine.begin() as connection:
        values = {
            "id": "doc-a", "series_id": "series-a", "chapter_id": "chapter-a", "version": 1,
            "document_json": "{}", "created_by": "user-a", "updated_by": "user-a",
            "created_at": datetime(2026, 1, 1), "updated_at": datetime(2026, 1, 1),
        }
        connection.execute(insert(canvas_documents).values(**values))
        with pytest.raises(IntegrityError):
            connection.execute(insert(canvas_documents).values(**{**values, "id": "doc-b"}))
        with pytest.raises(IntegrityError):
            connection.execute(
                insert(canvas_documents).values(**{**values, "id": "doc-c", "chapter_id": "missing"})
            )


def test_access_uses_existing_series_reader_and_owner_membership(canvas_db: CanvasDatabase) -> None:
    with canvas_db.session_factory() as session:
        uow = SqlAlchemyCanvasDataUnitOfWork(session)
        uow.ensure_clean()
        assert uow.load_chapter("chapter-a").series_id == "series-a"
        uow.require_series_access(TrustedActor("user-a"), "series-a")
        uow.require_series_access(TrustedActor("user-b"), "series-a")
        with pytest.raises(CanvasDataForbidden):
            uow.require_series_access(TrustedActor("outsider"), "series-a")
        uow.close()


def test_owned_expired_lock_refreshes_but_foreign_lock_is_untouched(canvas_db: CanvasDatabase) -> None:
    now = datetime(2026, 2, 1, 12)
    old = now - timedelta(hours=1)
    with canvas_db.engine.begin() as connection:
        connection.execute(
            insert(chapter_locks).values(
                id="lock-a", chapter_id="chapter-a", user_id="user-a", username="A",
                acquired_at=old, last_active_at=old, expires_at=old,
            )
        )
    with canvas_db.session_factory() as session:
        uow = SqlAlchemyCanvasDataUnitOfWork(session, now=lambda: now)
        uow.refresh_chapter_lock("chapter-a", "user-b")
        uow.commit()
        uow.refresh_chapter_lock("chapter-a", "user-a")
        uow.commit()
        row = session.execute(select(chapter_locks).where(chapter_locks.c.id == "lock-a")).mappings().one()
        assert row["last_active_at"] == now
        assert row["expires_at"] == now + timedelta(minutes=15)
        uow.close()


def test_lock_refresh_uses_falsey_fallback_and_preserves_negative_config(canvas_db: CanvasDatabase) -> None:
    before = datetime(2026, 1, 1)
    first_now = datetime(2026, 6, 1, 12)
    second_now = datetime(2026, 6, 1, 13)
    with canvas_db.engine.begin() as connection:
        connection.execute(
            insert(chapter_locks).values(
                id="lock-a", chapter_id="chapter-a", user_id="user-a", username="A",
                acquired_at=before, last_active_at=before, expires_at=before,
            )
        )
        connection.execute(
            update(system_configs).where(system_configs.c.id == "config-a").values(chapter_lock_idle_minutes=0)
        )

    with canvas_db.session_factory() as session:
        uow = SqlAlchemyCanvasDataUnitOfWork(session, now=lambda: first_now)
        uow.refresh_chapter_lock("chapter-a", "user-a")
        uow.commit()
        row = session.execute(select(chapter_locks)).mappings().one()
        assert row["expires_at"] == first_now + timedelta(minutes=15)
        uow.close()

    with canvas_db.session_factory() as session:
        session.execute(
            update(system_configs).where(system_configs.c.id == "config-a").values(chapter_lock_idle_minutes=-4)
        )
        session.commit()

    with canvas_db.session_factory() as session:
        uow = SqlAlchemyCanvasDataUnitOfWork(session, now=lambda: second_now)
        uow.refresh_chapter_lock("chapter-a", "user-a")
        uow.commit()
        row = session.execute(select(chapter_locks)).mappings().one()
        assert row["last_active_at"] == second_now
        assert row["expires_at"] == second_now - timedelta(minutes=4)
        uow.close()
