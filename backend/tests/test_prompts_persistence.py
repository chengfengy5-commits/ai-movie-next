from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.prompts.persistence import SqlAlchemyPromptsUnitOfWork
from haoai_backend.prompts.tables import (
    PromptConfigRow,
    metadata,
    prompt_configs,
    system_prompts,
    user_prompt_orders,
    users,
)

STAMP = datetime(2026, 10, 13, 9, 30)


@pytest.fixture
def database(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'prompts-persistence.sqlite'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    metadata.create_all(engine)
    session_factory = sessionmaker(
        engine,
        autoflush=False,
        expire_on_commit=False,
        future=True,
    )
    with engine.begin() as connection:
        connection.execute(insert(users), [{"id": "user-a"}, {"id": "user-b"}])
    yield engine, session_factory
    engine.dispose()


def add_personal(engine: Engine, prompt_id: str, *, name: str = "before") -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(prompt_configs).values(
                id=prompt_id,
                user_id="user-a",
                category="story",
                name=name,
                system_prompt="system",
                user_prompt="user",
                sort_order=0,
                created_at=STAMP,
                updated_at=STAMP,
            )
        )


def test_personal_active_is_transient_and_not_a_database_column(database) -> None:
    engine, session_factory = database
    add_personal(engine, "personal")
    unit_of_work = SqlAlchemyPromptsUnitOfWork(session_factory())

    updated = unit_of_work.update_personal_prompt(
        "personal",
        "user-a",
        {"is_active": False},
    )
    unit_of_work.commit()
    readback = unit_of_work.refresh_personal_prompt("personal", "user-a")
    unit_of_work.close()

    assert updated is not None and updated.is_active is False
    assert readback is not None and readback.is_active is None
    assert "is_active" not in prompt_configs.c
    with session_factory() as session:
        fresh = session.query(PromptConfigRow).filter_by(id="personal").one()
        assert getattr(fresh, "is_active", None) is None


def test_refresh_readback_uses_persisted_values(database) -> None:
    engine, session_factory = database
    add_personal(engine, "personal")
    unit_of_work = SqlAlchemyPromptsUnitOfWork(session_factory())
    unit_of_work.update_personal_prompt("personal", "user-a", {"name": "local"})
    unit_of_work.commit()
    with engine.begin() as connection:
        connection.execute(
            prompt_configs.update()
            .where(prompt_configs.c.id == "personal")
            .values(name="concurrent")
        )
    saved = unit_of_work.refresh_personal_prompt("personal", "user-a")
    unit_of_work.close()

    assert saved is not None and saved.name == "concurrent"


def test_order_rows_allow_repeated_prompt_ids_with_independent_record_ids(database) -> None:
    engine, session_factory = database
    unit_of_work = SqlAlchemyPromptsUnitOfWork(session_factory())
    unit_of_work.add_prompt_order(
        _order("order-one", "same-prompt", 0),
        user_id="user-a",
        category="story",
    )
    unit_of_work.add_prompt_order(
        _order("order-two", "same-prompt", 1),
        user_id="user-a",
        category="story",
    )
    unit_of_work.commit()
    rows = unit_of_work.list_prompt_orders("user-a", "story")
    unit_of_work.close()

    assert [(row.id, row.prompt_id, row.sort_order) for row in rows] == [
        ("order-one", "same-prompt", 0),
        ("order-two", "same-prompt", 1),
    ]
    with engine.connect() as connection:
        ids = connection.execute(select(user_prompt_orders.c.id).order_by(user_prompt_orders.c.id)).all()
    assert ids == [("order-one",), ("order-two",)]


def _order(order_id: str, prompt_id: str, sort_order: int):
    from haoai_backend.prompts.domain import PromptOrderRecord

    return PromptOrderRecord(order_id, prompt_id, sort_order)
