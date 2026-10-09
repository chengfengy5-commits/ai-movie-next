"""Real file-SQLite checks for the read-only series access projection."""

from pathlib import Path

import pytest
from sqlalchemy import create_engine, delete, event, insert, update
from sqlalchemy.orm import Session

from haoai_backend.series_access.persistence import SqlAlchemySeriesAccessReader
from haoai_backend.series_access.tables import metadata, series, team_members, users


@pytest.fixture
def engine(tmp_path: Path):
    database_path = tmp_path / "series-access.sqlite"
    engine = create_engine(f"sqlite:///{database_path}", future=True)
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            insert(users),
            [
                {"id": "claimant-a", "username": "姓名 A"},
                {"id": "claimant-b", "username": "姓名 B"},
            ],
        )
        connection.execute(
            insert(series),
            [
                {
                    "id": "series-a",
                    "user_id": "author-a",
                    "team_id": "team-a",
                    "claimed_by": "claimant-a",
                },
                {
                    "id": "series-b",
                    "user_id": "author-b",
                    "team_id": "team-b",
                    "claimed_by": None,
                },
            ],
        )
        connection.execute(
            insert(team_members),
            [
                {
                    "id": "member-a",
                    "team_id": "team-a",
                    "user_id": "viewer",
                    "role": "member",
                    "permissions": '["enter_claimed_series"]',
                },
                {
                    "id": "member-b",
                    "team_id": "team-b",
                    "user_id": "viewer",
                    "role": "owner",
                    "permissions": None,
                },
                {
                    "id": "member-c",
                    "team_id": "team-a",
                    "user_id": "other",
                    "role": "admin",
                    "permissions": "[]",
                },
            ],
        )
    yield engine
    engine.dispose()


def test_reader_uses_exact_series_team_user_and_claimant_filters(engine) -> None:
    with Session(engine, future=True) as session:
        reader = SqlAlchemySeriesAccessReader(session)
        assert reader.load_series("series-a").team_id == "team-a"
        assert reader.load_series("missing") is None
        assert reader.load_team_membership("team-a", "viewer").role == "member"
        assert reader.load_team_membership("team-b", "viewer").role == "owner"
        assert reader.load_team_membership("team-b", "other") is None
        assert reader.load_username("claimant-a") == "姓名 A"
        assert reader.load_username("missing") is None


def test_reader_does_not_write_or_manage_the_callers_session(engine) -> None:
    statements: list[str] = []
    commits: list[bool] = []
    rollbacks: list[bool] = []

    def observe_sql(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement.strip().lower())

    event.listen(engine, "before_cursor_execute", observe_sql)
    with Session(engine, future=True) as session:
        event.listen(session, "after_commit", lambda _: commits.append(True))
        event.listen(session, "after_rollback", lambda _: rollbacks.append(True))
        reader = SqlAlchemySeriesAccessReader(session)
        reader.load_series("series-a")
        reader.load_team_membership("team-a", "viewer")
        reader.load_username("claimant-a")

        assert session.in_transaction()
        assert session.is_active
        assert commits == []
        assert rollbacks == []
        assert all(statement.startswith("select") for statement in statements)
    event.remove(engine, "before_cursor_execute", observe_sql)


def test_new_reader_observes_membership_changes_on_a_later_request(engine) -> None:
    with Session(engine, future=True) as first_session:
        first_reader = SqlAlchemySeriesAccessReader(first_session)
        assert first_reader.load_team_membership("team-a", "viewer") is not None

    with engine.begin() as connection:
        connection.execute(
            update(team_members)
            .where(team_members.c.id == "member-a")
            .values(permissions="[]")
        )
        connection.execute(delete(team_members).where(team_members.c.id == "member-a"))

    with Session(engine, future=True) as next_session:
        next_reader = SqlAlchemySeriesAccessReader(next_session)
        assert next_reader.load_team_membership("team-a", "viewer") is None
