from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import event, insert

from chapter_asset_replacement_support import (
    CONSERVATION_TABLES,
    create_owner_database,
    series as owner_series,
    users,
)
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.teams.series.application import list_team_series
from haoai_backend.teams.series.persistence import (
    SeriesSqlAlchemyUnitOfWork,
    create_series_uow_factory,
)


NOW = datetime(2026, 10, 9, 12, 0, 0)


@pytest.fixture
def owner_database(tmp_path):
    database = create_owner_database(tmp_path / "teams-series-persistence.sqlite")
    try:
        database.assert_foreign_keys_enabled()
        yield database
    finally:
        database.close()


def test_page_clamps_sorts_counts_avatars_and_reads_without_writes(owner_database):
    with owner_database.engine.begin() as connection:
        connection.execute(
            users.update()
            .where(users.c.id == "user-a")
            .values(email="123456@qq.com")
        )
        connection.execute(
            users.update()
            .where(users.c.id == "user-b")
            .values(email="654321@qq.com")
        )
        connection.execute(
            owner_series.update()
            .where(owner_series.c.id == "series-a")
            .values(claimed_by="user-a", claimed_at=NOW)
        )
        connection.execute(
            insert(owner_series).values(
                id="series-c",
                user_id="user-b",
                name="较新的剧集",
                description=None,
                image_url=None,
                style_prompt_id=None,
                team_id="team-a",
                claimed_by="user-a",
                claimed_at=NOW,
                created_at=NOW,
                updated_at=datetime(2026, 10, 9, 13, 0, 0),
            )
        )

    before = owner_database.snapshot()
    assert all(before[name] for name in CONSERVATION_TABLES)

    statements: list[str] = []

    def record_statement(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.lstrip().upper())

    event.listen(owner_database.engine, "before_cursor_execute", record_statement)
    try:
        factory = create_series_uow_factory(
            owner_database.session_factory,
            utc_clock=lambda: NOW,
        )
        first_page = list_team_series(
            factory,
            TrustedActor("user-b"),
            "team-a",
            page=0,
            page_size=500,
        )
        second_page = list_team_series(
            factory,
            TrustedActor("user-b"),
            "team-a",
            page=2,
            page_size=0,
        )
    finally:
        event.remove(owner_database.engine, "before_cursor_execute", record_statement)

    assert first_page["total"] == 2
    assert first_page["page"] == 1
    assert first_page["page_size"] == 50
    assert first_page["total_pages"] == 1
    assert first_page["items"][0]["id"] == "series-c"
    assert first_page["items"][0]["claimed_by"] == "user-a"
    assert first_page["items"][0]["owner_avatar_url"] == (
        "https://q1.qlogo.cn/g?b=qq&nk=654321&s=100"
    )
    assert first_page["items"][0]["claimed_by_avatar_url"] == (
        "https://q1.qlogo.cn/g?b=qq&nk=123456&s=100"
    )
    assert first_page["items"][1]["chapter_count"] == 2
    assert first_page["items"][1]["asset_counts"] == {
        "characters": 3,
        "scenes": 2,
        "props": 2,
        "storyboard": 1,
    }
    assert second_page["total_pages"] == 2
    assert second_page["page"] == 2
    assert second_page["page_size"] == 1
    assert [item["id"] for item in second_page["items"]] == ["series-a"]
    assert statements
    assert all(statement.startswith("SELECT") for statement in statements)
    assert owner_database.snapshot() == before
