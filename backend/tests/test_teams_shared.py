from __future__ import annotations

import json
from datetime import datetime

import pytest
from sqlalchemy import event, insert, select, update
from sqlalchemy.exc import IntegrityError, SAWarning

from haoai_backend.teams import (
    TeamConfig,
    TeamSqlAlchemyUnitOfWork,
    assignment_plan,
    create_team_uow_factory,
    default_permissions_for_role,
    has_team_permission,
    member_permissions,
    permissions_for_update,
)
from haoai_backend.teams import tables
from haoai_backend.teams.errors import StaleTeamWrite, UnitOfWorkClosed
from haoai_backend.teams.persistence import INVITE_CODE_ALPHABET, new_invite_code
from haoai_backend.teams.schemas import (
    InviteCreate,
    JoinRequest,
    PermissionsUpdate,
    RoleUpdate,
    ShareSeriesRequest,
    TeamCreate,
    TeamSeriesCreate,
    TeamUpdate,
    TransferRequest,
)
from teams_support import team_invites, teams_database, teams


def test_config_reader_keeps_falsey_defaults_and_negative_values(teams_database):
    assert TeamConfig.from_row(None) == TeamConfig()
    assert TeamConfig.from_row(
        {
            "team_created_limit": 0,
            "team_joined_limit": -2,
            "team_member_limit": None,
            "invite_code_ttl_hours": 0,
            "chapter_lock_idle_minutes": -1,
        }
    ) == TeamConfig(
        team_created_limit=3,
        team_joined_limit=-2,
        team_member_limit=20,
        invite_code_ttl_hours=24,
        chapter_lock_idle_minutes=-1,
    )

    uow = TeamSqlAlchemyUnitOfWork(teams_database.session_factory())
    try:
        assert uow.config_reader.load_first_config() == TeamConfig()
    finally:
        uow.close()


def test_policy_readers_use_full_owner_rows_and_preserve_permission_storage(teams_database):
    uow = TeamSqlAlchemyUnitOfWork(teams_database.session_factory())
    try:
        team = uow.team_reader.load_team("team-one")
        member = uow.team_reader.load_membership("team-one", "user-member")
        users = uow.team_reader.load_users(["user-owner", "user-member"])

        assert team is not None
        assert (team.id, team.name, team.owner_id) == ("team-one", "Team One", "user-owner")
        assert member is not None
        assert member.permissions == '["view_tasks","manage_invites","view_tasks"]'
        assert users["user-owner"].username == "owner"
        assert users["user-member"].avatar_url == "https://example.test/avatar.png"
        assert uow.config_reader.load_first_config() == TeamConfig()
    finally:
        uow.close()


def test_first_snapshot_assignments_merge_and_noop_without_sql(teams_database):
    session = teams_database.session_factory()
    uow = TeamSqlAlchemyUnitOfWork(session)
    statements: list[str] = []

    def capture_updates(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("UPDATE TEAMS"):
            statements.append(statement)

    event.listen(teams_database.engine, "before_cursor_execute", capture_updates)
    try:
        first = uow.load_row(tables.teams, {"id": "team-one"})
        assert first is not None

        uow.stage_assignment(
            assignment_plan(tables.teams, {"id": "team-one"}, first, {"name": "Changed"})
        )
        uow.stage_assignment(
            assignment_plan(tables.teams, {"id": "team-one"}, first, {"name": first["name"]})
        )
        uow.commit()

        assert statements == []
        assert uow.refresh_row(tables.teams, {"id": "team-one"})["name"] == "Team One"
    finally:
        event.remove(teams_database.engine, "before_cursor_execute", capture_updates)
        uow.close()


def test_snapshot_is_stable_until_explicit_refresh(teams_database):
    uow = TeamSqlAlchemyUnitOfWork(teams_database.session_factory())
    try:
        first = uow.load_row(tables.teams, {"id": "team-one"})
        assert first is not None
        uow.execute_immediate(
            update(tables.teams)
            .where(tables.teams.c.id == "team-one")
            .values(name="Changed in this transaction")
        )

        assert uow.load_row(tables.teams, {"id": "team-one"})["name"] == "Team One"
        assert uow.refresh_row(tables.teams, {"id": "team-one"})["name"] == "Changed in this transaction"
        uow.rollback()
    finally:
        uow.close()


def test_staged_insert_is_invisible_until_flush_and_rollback_discards_it(teams_database):
    uow = TeamSqlAlchemyUnitOfWork(
        teams_database.session_factory(),
        utc_clock=lambda: datetime(2026, 1, 2),
        invite_code_source=lambda: "ABCDEFGH",
    )
    invite = {
        "id": "invite-staged",
        "team_id": "team-one",
        "code": "ABCDEFGH",
        "created_by": "user-owner",
        "created_at": uow.now_utc_naive(),
        "expires_at": datetime(2026, 1, 3),
        "used_by": None,
        "used_at": None,
        "status": "active",
    }
    try:
        uow.stage_insert(tables.team_invites, invite)
        assert not uow.invite_code_exists("ABCDEFGH")
        uow.flush()
        assert uow.invite_code_exists("ABCDEFGH")
        uow.rollback()
    finally:
        uow.close()

    with teams_database.engine.connect() as connection:
        assert connection.execute(
            select(team_invites.c.id).where(team_invites.c.id == "invite-staged")
        ).first() is None


def test_dirty_update_zero_row_is_real_sql_failure_and_rolls_back(teams_database):
    with teams_database.engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TRIGGER ignore_team_update "
            "BEFORE UPDATE ON teams BEGIN SELECT RAISE(IGNORE); END"
        )

    uow = TeamSqlAlchemyUnitOfWork(teams_database.session_factory())
    try:
        first = uow.load_row(tables.teams, {"id": "team-one"})
        assert first is not None
        uow.stage_assignment(
            assignment_plan(tables.teams, {"id": "team-one"}, first, {"name": "must not save"})
        )
        with pytest.raises(StaleTeamWrite):
            uow.commit()
        uow.rollback()
    finally:
        uow.close()
        with teams_database.engine.begin() as connection:
            connection.exec_driver_sql("DROP TRIGGER ignore_team_update")

    with teams_database.engine.connect() as connection:
        assert connection.execute(
            select(teams.c.name).where(teams.c.id == "team-one")
        ).scalar_one() == "Team One"


def test_zero_row_orm_delete_warns_but_commit_succeeds(teams_database):
    uow = TeamSqlAlchemyUnitOfWork(teams_database.session_factory())
    try:
        uow.stage_delete(tables.team_invites, {"id": "missing-invite"})
        with pytest.warns(SAWarning, match="DELETE on team_invites matched no row"):
            uow.commit()
    finally:
        uow.close()


def test_owner_permissions_and_source_list_filtering():
    assert has_team_permission("owner", None, "not-a-known-key")
    assert member_permissions('["view_tasks","not-a-known-key","view_tasks",4]') == [
        "view_tasks",
        "view_tasks",
    ]
    assert member_permissions('{"permissions":["view_tasks"]}') == []
    assert member_permissions("[{}]") == []
    assert permissions_for_update(["view_tasks", "view_tasks", "invalid"]) == ["view_tasks"]
    assert json.loads(default_permissions_for_role("admin")) == ["view_tasks", "manage_invites"]
    assert default_permissions_for_role("member") is None
    with pytest.raises(TypeError):
        permissions_for_update([{"unhashable": "write"}])


def test_factory_binds_explicit_sources_to_one_session(teams_database):
    created_sessions = []

    def make_session():
        session = teams_database.session_factory()
        created_sessions.append(session)
        return session

    factory = create_team_uow_factory(
        make_session,
        utc_clock=lambda: datetime(2026, 1, 2),
        epoch_clock=lambda: 123.5,
        id_source=lambda: "deterministic-id",
        invite_code_source=lambda: "ZXCVBNML",
    )
    uow = factory()
    try:
        assert len(created_sessions) == 1
        assert uow.session is created_sessions[0]
        assert uow.now_utc_naive() == datetime(2026, 1, 2)
        assert uow.epoch_seconds() == 123.5
        assert uow.new_id() == "deterministic-id"
        assert uow.new_invite_code() == "ZXCVBNML"
    finally:
        uow.close()


def test_integrity_error_is_not_swallowed_and_closed_readers_fail(teams_database):
    uow = TeamSqlAlchemyUnitOfWork(teams_database.session_factory())
    duplicate_team = {
        "id": "team-one",
        "name": "Duplicate",
        "owner_id": "user-owner",
        "created_at": datetime(2026, 1, 2),
    }
    try:
        uow.stage_insert(tables.teams, duplicate_team)
        with pytest.raises(IntegrityError) as error:
            uow.commit()
        assert uow.is_integrity_error(error.value)
        uow.rollback()
    finally:
        uow.close()

    with pytest.raises(UnitOfWorkClosed):
        uow.load_membership("team-one", "user-member")
    with pytest.raises(UnitOfWorkClosed):
        uow.load_users([])
    with pytest.raises(UnitOfWorkClosed):
        uow.load_first_config()


def test_request_schemas_keep_source_validation_and_ignore_extra_fields():
    assert TeamCreate.model_validate({"name": "A", "unused": True}).name == "A"
    assert TeamUpdate.model_validate({}).name is None
    assert TeamUpdate.model_validate({"name": None}).name is None
    assert InviteCreate.model_validate({}).count == 1
    assert InviteCreate.model_validate({"count": 5}).count == 5
    assert JoinRequest.model_validate({"code": "ABCDEF"}).code == "ABCDEF"
    assert RoleUpdate.model_validate({"role": " OWNER "}).role == " OWNER "
    assert PermissionsUpdate.model_validate({}).permissions == []
    assert TeamSeriesCreate.model_validate({"name": "Series"}).claim is False
    assert ShareSeriesRequest.model_validate({"team_id": "team-one"}).claim is False
    assert TransferRequest.model_validate({"user_id": " arbitrary id "}).user_id == " arbitrary id "

    with pytest.raises(ValueError):
        TeamCreate.model_validate({"name": ""})
    with pytest.raises(ValueError):
        TeamUpdate.model_validate({"name": ""})
    with pytest.raises(ValueError):
        InviteCreate.model_validate({"count": 0})
    with pytest.raises(ValueError):
        InviteCreate.model_validate({"count": 6})
    with pytest.raises(ValueError):
        JoinRequest.model_validate({"code": "short"})
    with pytest.raises(ValueError):
        TransferRequest.model_validate({"user_id": ""})


def test_invite_code_source_uses_eight_allowed_characters():
    code = new_invite_code()
    assert len(code) == 8
    assert set(code) <= set(INVITE_CODE_ALPHABET)
