from __future__ import annotations

import time
from datetime import datetime

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, event, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.admin_system_prompts import application
from haoai_backend.admin_system_prompts.domain import AdminSystemPromptActor
from haoai_backend.admin_system_prompts.persistence import SqlAlchemyAdminSystemPromptUnitOfWork
from haoai_backend.admin_system_prompts.tables import metadata, system_prompts

ADMIN = AdminSystemPromptActor(user_id="admin", is_superuser=True)


class CountingSession(Session):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.commit_attempts = 0
        self.rollback_attempts = 0
        self.closed = False

    def commit(self) -> None:
        self.commit_attempts += 1
        super().commit()

    def rollback(self) -> None:
        self.rollback_attempts += 1
        super().rollback()

    def close(self) -> None:
        self.closed = True
        super().close()


@pytest.fixture
def database():
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    order_metadata = MetaData()
    user_prompt_orders = Table(
        "user_prompt_orders",
        order_metadata,
        Column("id", String(36), primary_key=True),
        Column("user_id", String(36), nullable=False),
        Column("prompt_id", String(36), nullable=False),
        Column("category", String(50), nullable=False),
        Column("sort_order", Integer, nullable=False),
    )
    order_metadata.create_all(engine)
    maker = sessionmaker(
        engine,
        class_=CountingSession,
        expire_on_commit=False,
        autoflush=False,
    )
    sessions: list[CountingSession] = []

    def factory() -> SqlAlchemyAdminSystemPromptUnitOfWork:
        session = maker()
        sessions.append(session)
        return SqlAlchemyAdminSystemPromptUnitOfWork(session)

    try:
        yield engine, factory, sessions, user_prompt_orders
    finally:
        engine.dispose()


def seed(engine, prompt_id="p1", **values) -> None:
    row = {
        "id": prompt_id,
        "category": "text",
        "name": "Prompt",
        "system_prompt": "system",
        "user_prompt": "user",
        "sort_order": 1,
        "is_active": True,
    }
    row.update(values)
    with engine.begin() as connection:
        connection.execute(insert(system_prompts).values(**row))


def test_insert_defaults_null_values_and_postcommit_projection(database) -> None:
    engine, factory, sessions, _orders = database

    payload = application.create_system_prompt(
        factory,
        ADMIN,
        {
            "category": "text",
            "name": None,
            "system_prompt": None,
            "user_prompt": None,
            "sort_order": None,
            "is_active": None,
            "id": "client-id",
            "created_at": datetime(2000, 1, 1),
            "user_id": "client-user",
            "is_system": True,
        },
    )

    assert payload["id"] != "client-id"
    assert payload["category"] == "text"
    assert payload["name"] == "系统提示词"
    assert payload["system_prompt"] is None
    assert payload["user_prompt"] is None
    assert payload["sort_order"] == 0
    assert payload["is_active"] is True
    assert payload["created_at"] is not None
    assert payload["updated_at"] is not None
    assert set(payload) == {
        "id", "category", "name", "system_prompt", "user_prompt",
        "sort_order", "is_active", "created_at", "updated_at",
    }
    assert sessions[0].commit_attempts == 1
    assert sessions[0].closed

    with pytest.raises(IntegrityError):
        application.create_system_prompt(factory, ADMIN, {"category": None})
    assert sessions[1].commit_attempts == 1
    assert sessions[1].rollback_attempts >= 1
    assert sessions[1].closed


def test_list_uses_legacy_filters_count_and_default_page(database) -> None:
    engine, factory, _sessions, _orders = database
    for number in range(11):
        seed(
            engine,
            f"p{number:02}",
            category="text" if number < 10 else "other",
            sort_order=number,
            is_active=number % 2 == 0,
        )

    page = application.list_system_prompts(
        factory, ADMIN, category=None, is_active=None, skip=0, limit=10
    )
    assert page["total"] == 11
    assert len(page["data"]) == 10
    assert [item["sort_order"] for item in page["data"]] == list(range(10))

    inactive = application.list_system_prompts(
        factory, ADMIN, category="", is_active=False, skip=0, limit=10
    )
    assert inactive["total"] == 5
    assert all(item["is_active"] is False for item in inactive["data"])

    whitespace = application.list_system_prompts(
        factory, ADMIN, category=" ", is_active=None, skip=0, limit=10
    )
    assert whitespace == {"data": [], "total": 0}


def test_noop_update_commits_once_without_sql_update_and_real_change_uses_onupdate(database) -> None:
    engine, factory, sessions, _orders = database
    seed(engine, name="Stable")
    statements: list[str] = []

    def capture(_conn, _cursor, statement, _parameters, _context, _many) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        original = application.get_system_prompt(
            factory, ADMIN, "p1"
        )["updated_at"]
        statements.clear()

        unchanged = application.update_system_prompt(
            factory, ADMIN, "p1", {"name": "Stable", "client_field": "ignored"}
        )
        assert unchanged["updated_at"] == original
        assert sessions[-1].commit_attempts == 1
        assert not any(statement.lstrip().upper().startswith("UPDATE") for statement in statements)

        statements.clear()
        time.sleep(0.003)
        changed = application.update_system_prompt(
            factory, ADMIN, "p1", {"name": "Changed"}
        )
        assert changed["name"] == "Changed"
        assert changed["updated_at"] > original
        assert sessions[-1].commit_attempts == 1
        assert sum(
            statement.lstrip().upper().startswith("UPDATE") for statement in statements
        ) == 1
    finally:
        event.remove(engine, "before_cursor_execute", capture)


def test_update_null_keeps_database_constraint_and_delete_preserves_order_rows(database) -> None:
    engine, factory, sessions, user_prompt_orders = database
    seed(engine)
    with engine.begin() as connection:
        connection.execute(
            insert(user_prompt_orders),
            [
                {"id": "o1", "user_id": "u1", "prompt_id": "p1", "category": "text", "sort_order": 1},
                {"id": "o2", "user_id": "u2", "prompt_id": "p1", "category": "text", "sort_order": 2},
            ],
        )

    with pytest.raises(IntegrityError):
        application.update_system_prompt(factory, ADMIN, "p1", {"category": None})
    assert sessions[-1].commit_attempts == 1
    assert sessions[-1].rollback_attempts >= 1
    assert sessions[-1].closed

    deleted = application.delete_system_prompt(factory, ADMIN, "p1")
    assert deleted == {"message": "删除成功"}
    assert sessions[-1].commit_attempts == 1
    with engine.connect() as connection:
        assert connection.execute(
            select(user_prompt_orders.c.prompt_id).order_by(user_prompt_orders.c.id)
        ).scalars().all() == ["p1", "p1"]
        assert connection.execute(
            select(system_prompts.c.id).where(system_prompts.c.id == "p1")
        ).first() is None


def test_postcommit_readback_observes_concurrent_database_value(database) -> None:
    engine, normal_factory, sessions, _orders = database
    seed(engine, name="before")
    class InterferingSession(CountingSession):
        inject_external_change = True

        def commit(self) -> None:
            super().commit()
            if self.inject_external_change:
                self.inject_external_change = False
                with Session(engine) as other:
                    other.execute(
                        update(system_prompts)
                        .where(system_prompts.c.id == "p1")
                        .values(name="after concurrent commit")
                    )
                    other.commit()

    maker = sessionmaker(
        engine,
        class_=InterferingSession,
        expire_on_commit=False,
        autoflush=False,
    )
    special_sessions: list[InterferingSession] = []

    def interfering_factory() -> SqlAlchemyAdminSystemPromptUnitOfWork:
        session = maker()
        special_sessions.append(session)
        return SqlAlchemyAdminSystemPromptUnitOfWork(session)

    payload = application.update_system_prompt(
        interfering_factory, ADMIN, "p1", {"name": "our write"}
    )
    assert payload["name"] == "after concurrent commit"
    assert special_sessions[0].commit_attempts == 1
    assert special_sessions[0].closed
