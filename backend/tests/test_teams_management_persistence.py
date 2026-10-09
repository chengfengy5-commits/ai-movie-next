from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import insert

from haoai_backend.teams import create_team_uow_factory
from haoai_backend.teams import tables
from haoai_backend.teams.management.persistence import (
    count_rows,
    load_invite,
    load_invite_by_code,
    load_member_by_id,
    release_team_locks,
)
from chapter_asset_replacement_support import OWNER_TABLES, create_owner_database
from teams_support import OWNER_METADATA as TEAM_OWNER_METADATA, system_configs, team_invites


@pytest.fixture
def owner_database(tmp_path):
    database = create_owner_database(tmp_path / "management-persistence.sqlite")
    TEAM_OWNER_METADATA.create_all(database.engine, checkfirst=True)
    with database.engine.begin() as connection:
        connection.execute(insert(system_configs).values(id="team-config"))
    try:
        yield database
    finally:
        database.close()


def test_query_helpers_use_shared_uow_and_scoped_keys(owner_database) -> None:
    uow = create_team_uow_factory(owner_database.session_factory)()
    try:
        assert count_rows(uow, tables.team_members, tables.team_members.c.team_id == "team-a") == 2
        member = load_member_by_id(uow, "team-a", "user-b")
        assert member is not None and member["role"] == "member"
        assert load_member_by_id(uow, "missing-team", "user-b") is None
        assert load_invite_by_code(uow, "missing") is None
    finally:
        uow.close()


def test_invite_lookup_requires_both_invite_and_team(owner_database) -> None:
    now = datetime(2026, 10, 9, 12)
    with owner_database.engine.begin() as connection:
        connection.execute(
            insert(team_invites).values(
                id="invite-one",
                team_id="team-a",
                code="LOOKUP01",
                created_by="user-a",
                created_at=now,
                expires_at=now + timedelta(days=1),
                used_by=None,
                used_at=None,
                status="active",
            )
        )

    uow = create_team_uow_factory(owner_database.session_factory)()
    try:
        assert load_invite(uow, "invite-one", "team-a")["code"] == "LOOKUP01"
        assert load_invite(uow, "invite-one", "other-team") is None
        assert load_invite_by_code(uow, "LOOKUP01")["id"] == "invite-one"
    finally:
        uow.close()


def test_lock_release_is_limited_to_requested_user(owner_database) -> None:
    now = datetime(2026, 10, 9, 12)
    with owner_database.engine.begin() as connection:
        connection.execute(
            insert(OWNER_TABLES["chapter_locks"]).values(
                id="owner-lock",
                chapter_id="neighbor-a",
                user_id="user-a",
                username="owner-a",
                acquired_at=now,
                last_active_at=now,
                expires_at=now + timedelta(minutes=15),
            )
        )

    uow = create_team_uow_factory(owner_database.session_factory)()
    try:
        release_team_locks(uow, "team-a", "user-b")
        uow.commit()
    finally:
        uow.close()

    locks = owner_database.snapshot(("chapter_locks",))["chapter_locks"]
    assert {row["id"] for row in locks} == {"owner-lock"}
