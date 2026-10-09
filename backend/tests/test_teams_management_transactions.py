from __future__ import annotations

from datetime import datetime, timedelta
from itertools import count

import pytest
from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.shared.identity import TrustedActor
from haoai_backend.teams import InviteCreate, JoinRequest, create_team_uow_factory
from haoai_backend.teams import tables
from haoai_backend.teams.management.application import join_team, create_invites
from haoai_backend.teams.errors import StaleTeamWrite
from haoai_backend.teams import TeamCreate, TeamUpdate
from haoai_backend.teams.management.application import update_team
from haoai_backend.teams.management.persistence import create_management_uow_factory
from chapter_asset_replacement_support import OWNER_TABLES, create_owner_database
from teams_support import OWNER_METADATA as TEAM_OWNER_METADATA, system_configs, team_invites


@pytest.fixture
def owner_database(tmp_path):
    database = create_owner_database(tmp_path / "management-transactions.sqlite")
    TEAM_OWNER_METADATA.create_all(database.engine, checkfirst=True)
    with database.engine.begin() as connection:
        connection.execute(insert(system_configs).values(id="team-config"))
    try:
        yield database
    finally:
        database.close()


def table_rows(database, table):
    from sqlalchemy import select

    with database.engine.connect() as connection:
        return [dict(row) for row in connection.execute(select(table).order_by(*table.primary_key)).mappings()]


def add_user(database, user_id: str) -> None:
    now = datetime(2026, 10, 9, 12)
    with database.engine.begin() as connection:
        connection.execute(
            insert(OWNER_TABLES["users"]).values(
                id=user_id,
                username=user_id,
                email=f"{user_id}@example.test",
                hashed_password="unused",
                is_superuser=False,
                membership_type="premium",
                membership_expires_at=None,
                avatar_url=None,
                bio=None,
                created_at=now,
                password_updated_at=now,
            )
        )


def add_invite(database, invite_id: str, code: str) -> None:
    now = datetime(2026, 10, 9, 12)
    with database.engine.begin() as connection:
        connection.execute(
            insert(team_invites).values(
                id=invite_id,
                team_id="team-a",
                code=code,
                created_by="user-a",
                created_at=now,
                expires_at=now + timedelta(days=1),
                used_by=None,
                used_at=None,
                status="active",
            )
        )


def test_two_uows_both_read_unjoined_then_unique_member_insert_wins_once(owner_database) -> None:
    add_user(owner_database, "user-racer")
    session_a = owner_database.session_factory()
    session_b = owner_database.session_factory()
    factory_a = create_team_uow_factory(lambda: session_a, id_source=lambda: "race-a")
    factory_b = create_team_uow_factory(lambda: session_b, id_source=lambda: "race-b")
    uow_a = factory_a()
    uow_b = factory_b()
    try:
        assert session_a.connection().connection.dbapi_connection is not (
            session_b.connection().connection.dbapi_connection
        )
        assert uow_a.load_membership("team-a", "user-racer") is None
        assert uow_b.load_membership("team-a", "user-racer") is None
        uow_a.stage_insert(
            tables.team_members,
            {
                "id": "race-a",
                "team_id": "team-a",
                "user_id": "user-racer",
                "role": "member",
                "permissions": None,
                "joined_at": datetime(2026, 10, 9, 12),
            },
        )
        uow_b.stage_insert(
            tables.team_members,
            {
                "id": "race-b",
                "team_id": "team-a",
                "user_id": "user-racer",
                "role": "member",
                "permissions": None,
                "joined_at": datetime(2026, 10, 9, 12),
            },
        )
        uow_a.commit()
        with pytest.raises(IntegrityError):
            uow_b.commit()
        uow_b.rollback()
    finally:
        uow_b.close()
        uow_a.close()

    winners = [
        row
        for row in owner_database.snapshot(("team_members",))["team_members"]
        if row["user_id"] == "user-racer"
    ]
    assert len(winners) == 1
    assert winners[0]["id"] == "race-a"


def test_late_unique_failure_rolls_back_join_invite_assignment(owner_database) -> None:
    add_user(owner_database, "user-racer")
    add_invite(owner_database, "racer-invite", "RACECODE")
    before = owner_database.snapshot()
    before["team_invites"] = table_rows(owner_database, team_invites)
    with owner_database.engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TRIGGER reject_racer_membership "
            "BEFORE INSERT ON team_members WHEN NEW.user_id='user-racer' "
            "BEGIN SELECT RAISE(ABORT, 'forced member conflict'); END"
        )
    factory = create_management_uow_factory(
        create_team_uow_factory(
            owner_database.session_factory,
            utc_clock=lambda: datetime(2026, 10, 9, 12),
        )
    )
    try:
        with pytest.raises(IntegrityError):
            join_team(factory, TrustedActor("user-racer"), JoinRequest(code="RACECODE"))
    finally:
        with owner_database.engine.begin() as connection:
            connection.exec_driver_sql("DROP TRIGGER reject_racer_membership")
    after = owner_database.snapshot()
    after["team_invites"] = table_rows(owner_database, team_invites)
    assert after == before


def test_dirty_team_update_zero_row_is_real_sql_failure_and_rolls_back(owner_database) -> None:
    with owner_database.engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TRIGGER ignore_team_update "
            "BEFORE UPDATE ON teams BEGIN SELECT RAISE(IGNORE); END"
        )
    factory = create_management_uow_factory(
        create_team_uow_factory(owner_database.session_factory)
    )
    try:
        with pytest.raises(StaleTeamWrite):
            update_team(
                factory,
                TrustedActor("user-a"),
                "team-a",
                TeamUpdate(name="must not persist"),
            )
    finally:
        with owner_database.engine.begin() as connection:
            connection.exec_driver_sql("DROP TRIGGER ignore_team_update")
    team = next(
        row for row in owner_database.snapshot(("teams",))["teams"]
        if row["id"] == "team-a"
    )
    assert team["name"] == "测试团队"


class CommitThenFailSession(Session):
    def commit(self) -> None:
        super().commit()
        raise RuntimeError("commit acknowledgement lost")


def test_commit_ack_failure_is_unknown_durable_and_not_replayed(owner_database) -> None:
    created_ids = []
    sequence = count()

    def new_id():
        value = f"durable-invite-{next(sequence)}"
        created_ids.append(value)
        return value

    factory_session = sessionmaker(
        bind=owner_database.engine,
        class_=CommitThenFailSession,
        autoflush=False,
        expire_on_commit=True,
    )
    factory = create_management_uow_factory(
        create_team_uow_factory(
            factory_session,
            utc_clock=lambda: datetime(2026, 10, 9, 12),
            id_source=new_id,
            invite_code_source=lambda: "ONCECODE",
        )
    )
    with pytest.raises(RuntimeError, match="acknowledgement lost"):
        create_invites(
            factory,
            TrustedActor("user-a"),
            "team-a",
            InviteCreate(count=1),
        )

    saved = table_rows(owner_database, team_invites)
    assert [row["id"] for row in saved if row["id"].startswith("durable-invite-")] == [
        "durable-invite-0"
    ]
    assert created_ids == ["durable-invite-0"]
