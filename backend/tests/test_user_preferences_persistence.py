from __future__ import annotations

from dataclasses import dataclass
import json
from datetime import datetime
from pathlib import Path
from typing import ClassVar

import pytest
from sqlalchemy import event, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy import create_engine

from haoai_backend.shared.identity import TrustedActor
from haoai_backend.user_preferences.application import (
    get_user_preferences,
    update_user_preferences,
)
from haoai_backend.user_preferences.persistence import (
    SqlAlchemyUserPreferencesUnitOfWork,
)
from haoai_backend.user_preferences.tables import (
    UserPreference,
    metadata,
    user_preferences,
    users,
)


@dataclass
class PreferenceDatabase:
    __test__: ClassVar[bool] = False

    engine: Engine
    session_factory: sessionmaker[Session]

    def seed_users(self, *user_ids: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(insert(users), [{"id": user_id} for user_id in user_ids])


@pytest.fixture
def database(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'user-preferences.sqlite'}")

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
    database = PreferenceDatabase(engine, session_factory)
    database.seed_users("user-a", "user-b")
    yield database
    engine.dispose()


def test_duplicate_rows_keep_iteration_and_first_row_update_behavior(
    database: PreferenceDatabase,
) -> None:
    timestamp = datetime(2026, 1, 1)
    with database.engine.begin() as connection:
        connection.execute(
            insert(user_preferences),
            [
                {
                    "id": "preference-one",
                    "user_id": "user-a",
                    "key": "theme",
                    "value": '"first"',
                    "updated_at": timestamp,
                },
                {
                    "id": "preference-two",
                    "user_id": "user-a",
                    "key": "theme",
                    "value": '"second"',
                    "updated_at": timestamp,
                },
            ],
        )

    reader = SqlAlchemyUserPreferencesUnitOfWork(database.session_factory())
    rows = reader.list_preferences("user-a")
    expected_get_value = json.loads(rows[-1].value)
    first_id = reader.find_preference("user-a", "theme").id
    reader.close()

    assert get_user_preferences(
        lambda: SqlAlchemyUserPreferencesUnitOfWork(database.session_factory()),
        TrustedActor("user-a"),
    ) == {"theme": expected_get_value}

    result = update_user_preferences(
        lambda: SqlAlchemyUserPreferencesUnitOfWork(database.session_factory()),
        TrustedActor("user-a"),
        {"theme": "replacement"},
        clock=lambda: datetime(2026, 2, 1),
        id_factory=lambda: "unused",
    )

    assert result == {"ok": True, "updated_keys": ["theme"]}
    with database.engine.connect() as connection:
        stored = {
            row["id"]: row["value"]
            for row in connection.execute(
                select(user_preferences.c.id, user_preferences.c.value)
                .where(user_preferences.c.user_id == "user-a")
            ).mappings()
        }
    assert stored[first_id] == '"replacement"'
    assert sum(value == '"replacement"' for value in stored.values()) == 1


def test_autoflush_disabled_keeps_pending_rows_until_commit(
    database: PreferenceDatabase,
) -> None:
    with database.engine.begin() as connection:
        connection.execute(
            insert(user_preferences).values(
                id="existing",
                user_id="user-a",
                key="existing",
                value='"stored"',
                updated_at=datetime(2026, 1, 1),
            )
        )

    session = database.session_factory()
    unit_of_work = SqlAlchemyUserPreferencesUnitOfWork(session)
    pending = UserPreference()
    pending.id = "pending"
    pending.user_id = "user-a"
    pending.key = "pending"
    pending.value = '"not flushed by lookup"'
    pending.updated_at = datetime(2026, 1, 1)
    session.add(pending)
    flushes: list[bool] = []

    def record_flush(_session, _flush_context, _instances) -> None:
        flushes.append(True)

    event.listen(session, "before_flush", record_flush)
    try:
        found = unit_of_work.find_preference("user-a", "existing")
        assert found is not None
        assert flushes == []
        assert pending in session.new

        unit_of_work.commit()
        assert flushes == [True]
    finally:
        event.remove(session, "before_flush", record_flush)
        unit_of_work.close()

    with database.engine.connect() as connection:
        assert connection.execute(
            select(user_preferences.c.id).where(user_preferences.c.id == "pending")
        ).scalar_one() == "pending"


def test_same_value_assignment_does_not_overwrite_a_concurrent_change(
    database: PreferenceDatabase,
) -> None:
    initial_timestamp = datetime(2026, 1, 1)
    with database.engine.begin() as connection:
        connection.execute(
            insert(user_preferences).values(
                id="same-value",
                user_id="user-a",
                key="theme",
                value='"same"',
                updated_at=initial_timestamp,
            )
        )

    session = database.session_factory()
    unit_of_work = SqlAlchemyUserPreferencesUnitOfWork(session)
    row = unit_of_work.find_preference("user-a", "theme")
    assert row is not None

    with database.session_factory() as competing_session:
        competing_session.execute(
            update(user_preferences)
            .where(user_preferences.c.id == "same-value")
            .values(value='"concurrent"')
        )
        competing_session.commit()

    row.value = '"same"'
    row.updated_at = datetime(2026, 2, 1)
    unit_of_work.commit()
    unit_of_work.close()

    with database.engine.connect() as connection:
        stored_value = connection.execute(
            select(user_preferences.c.value).where(
                user_preferences.c.id == "same-value"
            )
        ).scalar_one()
    assert stored_value == '"concurrent"'
