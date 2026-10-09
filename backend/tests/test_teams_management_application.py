from __future__ import annotations

import json
from datetime import datetime, timedelta
from itertools import count

import pytest
from sqlalchemy import event, insert, update
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.shared.identity import TrustedActor
from haoai_backend.teams import (
    InviteCreate,
    JoinRequest,
    PermissionsUpdate,
    RoleUpdate,
    TeamCreate,
    TeamUpdate,
    create_team_uow_factory,
)
from haoai_backend.teams import tables
from haoai_backend.teams.management.application import (
    create_invites,
    create_team,
    delete_team,
    join_team,
    leave_team,
    list_invites,
    my_teams,
    remove_member,
    revoke_invite,
    set_member_role,
    team_detail,
    update_member_permissions,
    update_team,
)
from chapter_asset_replacement_support import OWNER_TABLES, create_owner_database
from teams_support import OWNER_METADATA as TEAM_OWNER_METADATA, system_configs, team_invites
from haoai_backend.teams.management.persistence import create_management_uow_factory


@pytest.fixture
def owner_database(tmp_path):
    database = create_owner_database(tmp_path / "management-application.sqlite")
    TEAM_OWNER_METADATA.create_all(database.engine, checkfirst=True)
    with database.engine.begin() as connection:
        connection.execute(insert(system_configs).values(id="team-config"))
    try:
        yield database
    finally:
        database.close()


def make_factory(database, *, now=None, code_source=None, prefix="generated"):
    ids = count()

    def new_id():
        return f"{prefix}-{next(ids)}"

    shared_factory = create_team_uow_factory(
        database.session_factory,
        utc_clock=lambda: now or datetime(2026, 10, 9, 12, 0),
        id_source=new_id,
        invite_code_source=code_source or (lambda: "ABCDEFGH"),
    )
    return create_management_uow_factory(shared_factory)




def table_rows(database, table):
    from sqlalchemy import select

    with database.engine.connect() as connection:
        return [dict(row) for row in connection.execute(select(table).order_by(*table.primary_key)).mappings()]


def full_owner_snapshot(database):
    snapshot = database.snapshot()
    snapshot["team_invites"] = table_rows(database, team_invites)
    snapshot["system_configs"] = table_rows(database, system_configs)
    return snapshot


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


def add_invite(database, *, invite_id: str, code: str, expires_at: datetime) -> None:
    now = datetime(2026, 10, 9, 11)
    with database.engine.begin() as connection:
        connection.execute(
            insert(team_invites).values(
                id=invite_id,
                team_id="team-a",
                code=code,
                created_by="user-a",
                created_at=now,
                expires_at=expires_at,
                used_by=None,
                used_at=None,
                status="active",
            )
        )


class MinimalManagementPort:
    def __init__(self) -> None:
        self.closed = False

    def ensure_clean(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def memberships_for_user(self, user_id: str):
        assert user_id == "user-a"
        return []


def test_application_uses_business_port_without_a_session_escape_hatch() -> None:
    uow = MinimalManagementPort()

    assert my_teams(lambda: uow, TrustedActor("user-a")) == []
    assert uow.closed
    assert not hasattr(uow, "session")


def test_create_team_flushes_before_owner_membership_and_preserves_blank_trim(
    owner_database,
) -> None:
    statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        normalized = " ".join(statement.lower().split())
        if normalized.startswith("insert into teams") or normalized.startswith(
            "insert into team_members"
        ):
            statements.append(normalized)

    event.listen(owner_database.engine, "before_cursor_execute", capture)
    try:
        result = create_team(
            make_factory(owner_database, prefix="new"),
            TrustedActor("user-a"),
            TeamCreate(name="  "),
        )
    finally:
        event.remove(owner_database.engine, "before_cursor_execute", capture)

    assert statements[0].startswith("insert into teams")
    assert statements[1].startswith("insert into team_members")
    assert result["name"] == ""
    after = owner_database.snapshot(("teams", "team_members"))
    saved = next(row for row in after["teams"] if row["id"] == result["id"])
    membership = next(
        row for row in after["team_members"] if row["team_id"] == result["id"]
    )
    assert saved["owner_id"] == membership["user_id"] == "user-a"
    assert membership["role"] == "owner"


def test_team_lists_and_detail_keep_source_membership_and_avatar_projection(
    owner_database,
) -> None:
    mine = my_teams(make_factory(owner_database), TrustedActor("user-b"))
    assert [item["id"] for item in mine] == ["team-a"]
    assert mine[0]["member_count"] == 2
    assert mine[0]["my_role"] == "member"

    detail = team_detail(
        make_factory(owner_database),
        TrustedActor("user-a"),
        "team-a",
    )
    assert detail["my_role"] == "owner"
    assert detail["my_permissions"] == []
    member = next(row for row in detail["members"] if row["user_id"] == "user-b")
    assert member["username"] == "member-b"
    assert member["permissions"] == []


def test_update_team_trim_and_role_noop_preserve_commit_semantics(owner_database) -> None:
    factory = make_factory(owner_database)
    renamed = update_team(
        factory,
        TrustedActor("user-a"),
        "team-a",
        TeamUpdate(name="  "),
    )
    assert renamed == {"id": "team-a", "name": ""}

    updates: list[str] = []
    commits = 0

    def after_commit(_session):
        nonlocal commits
        commits += 1

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().lower().startswith("update team_members"):
            updates.append(statement)

    event.listen(Session, "after_commit", after_commit)
    event.listen(owner_database.engine, "before_cursor_execute", capture)
    try:
        unchanged = set_member_role(
            factory,
            TrustedActor("user-a"),
            "team-a",
            "user-b",
            RoleUpdate(role=" MEMBER "),
        )
        assert unchanged == {
            "message": "角色未变化",
            "role": "member",
            "permissions": [],
        }
        changed = set_member_role(
            factory,
            TrustedActor("user-a"),
            "team-a",
            "user-b",
            RoleUpdate(role=" ADMIN "),
        )
        assert changed == {
            "message": "角色已更新",
            "role": "admin",
            "permissions": ["view_tasks", "manage_invites"],
        }
    finally:
        event.remove(owner_database.engine, "before_cursor_execute", capture)
        event.remove(Session, "after_commit", after_commit)

    assert commits == 1
    assert len(updates) == 1
    saved = next(
        row for row in owner_database.snapshot(("team_members",))["team_members"]
        if row["user_id"] == "user-b"
    )
    assert json.loads(saved["permissions"]) == ["view_tasks", "manage_invites"]


def test_permission_update_filters_deduplicates_and_keeps_unhashable_failure(
    owner_database,
) -> None:
    factory = make_factory(owner_database)
    set_member_role(
        factory,
        TrustedActor("user-a"),
        "team-a",
        "user-b",
        RoleUpdate(role="admin"),
    )
    post_commit_selects: list[str] = []
    committed = False

    def after_commit(_session):
        nonlocal committed
        committed = True

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        normalized = " ".join(statement.lower().split())
        if committed and normalized.startswith("select"):
            post_commit_selects.append(normalized)

    event.listen(Session, "after_commit", after_commit)
    event.listen(owner_database.engine, "before_cursor_execute", capture)
    try:
        result = update_member_permissions(
            factory,
            TrustedActor("user-a"),
            "team-a",
            "user-b",
            PermissionsUpdate(permissions=["view_tasks", "invalid", "view_tasks"]),
        )
    finally:
        event.remove(owner_database.engine, "before_cursor_execute", capture)
        event.remove(Session, "after_commit", after_commit)

    assert result == {"message": "权限已更新", "permissions": ["view_tasks"]}
    assert committed
    assert post_commit_selects == []

    with pytest.raises(TypeError):
        update_member_permissions(
            factory,
            TrustedActor("user-a"),
            "team-a",
            "user-b",
            PermissionsUpdate(permissions=[{"unhashable": True}]),
        )


def test_invite_batch_checks_only_committed_codes_and_get_expires_active_rows(
    owner_database,
) -> None:
    now = datetime(2026, 10, 9, 12)
    factory = make_factory(owner_database, now=now)
    created = create_invites(
        factory,
        TrustedActor("user-a"),
        "team-a",
        InviteCreate(count=2),
    )
    assert [invite["code"] for invite in created["invites"]] == [
        "ABCDEFGH",
        "ABCDEFGH",
    ]
    assert created["ttl_hours"] == 24

    add_invite(
        owner_database,
        invite_id="expired-invite",
        code="EXPIRED1",
        expires_at=now - timedelta(seconds=1),
    )
    before_commits = table_rows(owner_database, team_invites)
    rows = list_invites(factory, TrustedActor("user-a"), "team-a")
    assert next(row for row in rows if row["id"] == "expired-invite")["status"] == "expired"
    after = table_rows(owner_database, team_invites)
    assert len(after) == len(before_commits)
    assert next(row for row in after if row["id"] == "expired-invite")["status"] == "expired"



def test_create_invites_returns_precommit_rows_without_postcommit_invite_query(
    owner_database,
) -> None:
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    committed = False
    postcommit_invite_selects: list[str] = []

    def after_commit(_session):
        nonlocal committed
        committed = True

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        normalized = " ".join(statement.lower().split())
        if committed and normalized.startswith("select") and "team_invites" in normalized:
            postcommit_invite_selects.append(normalized)

    event.listen(Session, "after_commit", after_commit)
    event.listen(owner_database.engine, "before_cursor_execute", capture)
    try:
        result = create_invites(
            make_factory(owner_database),
            TrustedActor("user-a"),
            "team-a",
            InviteCreate(count=2),
        )
    finally:
        event.remove(owner_database.engine, "before_cursor_execute", capture)
        event.remove(Session, "after_commit", after_commit)

    assert len(result["invites"]) == 2
    assert result["ttl_hours"] == 24
    assert postcommit_invite_selects == []

def test_join_commits_invite_consumption_and_expiry_before_returning_error(
    owner_database,
) -> None:
    add_user(owner_database, "user-c")
    now = datetime(2026, 10, 9, 12)
    add_invite(
        owner_database,
        invite_id="join-invite",
        code="JOINCODE",
        expires_at=now + timedelta(hours=1),
    )
    result = join_team(
        make_factory(owner_database, now=now),
        TrustedActor("user-c"),
        JoinRequest(code=" joincode "),
    )
    assert result == {
        "message": "已成功加入团队「测试团队」",
        "team_id": "team-a",
        "team_name": "测试团队",
    }
    snapshot = owner_database.snapshot(("team_members",))
    snapshot["team_invites"] = table_rows(owner_database, team_invites)
    assert any(row["user_id"] == "user-c" for row in snapshot["team_members"])
    invite = next(row for row in snapshot["team_invites"] if row["id"] == "join-invite")
    assert (invite["status"], invite["used_by"]) == ("used", "user-c")

    add_user(owner_database, "user-d")
    add_invite(
        owner_database,
        invite_id="expired-join",
        code="EXPIRE01",
        expires_at=now - timedelta(seconds=1),
    )
    with pytest.raises(Exception, match="邀请码已过期"):
        join_team(
            make_factory(owner_database, now=now),
            TrustedActor("user-d"),
            JoinRequest(code="EXPIRE01"),
        )
    expired = next(
        row
        for row in table_rows(owner_database, team_invites)
        if row["id"] == "expired-join"
    )
    assert expired["status"] == "expired"


def test_team_delete_keeps_source_lock_lookup_after_detaching_series(owner_database) -> None:
    now = datetime(2026, 10, 9, 12)
    with owner_database.engine.begin() as connection:
        connection.execute(
            insert(OWNER_TABLES["teams"]).values(
                id="team-b",
                name="另一团队",
                owner_id="user-b",
                created_at=now,
            )
        )
        connection.execute(
            insert(OWNER_TABLES["team_members"]).values(
                id="team-b-member-a",
                team_id="team-b",
                user_id="user-a",
                role="member",
                permissions='["view_tasks"]',
                joined_at=now,
            )
        )
        connection.execute(
            insert(team_invites),
            [
                {
                    "id": "team-a-invite",
                    "team_id": "team-a",
                    "code": "TEAMAINVITE",
                    "created_by": "user-a",
                    "created_at": now,
                    "expires_at": now + timedelta(days=1),
                    "used_by": None,
                    "used_at": None,
                    "status": "active",
                },
                {
                    "id": "team-b-invite",
                    "team_id": "team-b",
                    "code": "TEAMBINVITE",
                    "created_by": "user-b",
                    "created_at": now,
                    "expires_at": now + timedelta(days=1),
                    "used_by": None,
                    "used_at": None,
                    "status": "active",
                },
            ],
        )
        connection.execute(
            update(OWNER_TABLES["series"])
            .where(OWNER_TABLES["series"].c.id == "series-a")
            .values(claimed_by="user-b", claimed_at=now)
        )
        connection.execute(
            update(OWNER_TABLES["series"])
            .where(OWNER_TABLES["series"].c.id == "series-b")
            .values(team_id="team-b", claimed_by="user-a", claimed_at=now)
        )
        connection.execute(
            insert(OWNER_TABLES["chapter_locks"]).values(
                id="team-b-lock",
                chapter_id="chapter-b",
                user_id="user-a",
                username="team-b editor",
                acquired_at=now,
                last_active_at=now,
                expires_at=now + timedelta(minutes=15),
            )
        )
    before = full_owner_snapshot(owner_database)
    assert {row["id"] for row in before["teams"]} == {"team-a", "team-b"}
    assert {row["id"] for row in before["team_invites"]} == {
        "team-a-invite",
        "team-b-invite",
    }
    assert any(row["team_id"] == "team-b" for row in before["team_members"])
    assert any(row["id"] == "team-b-lock" for row in before["chapter_locks"])

    result = delete_team(
        make_factory(owner_database, now=now),
        TrustedActor("user-a"),
        "team-a",
    )

    assert result == {"message": "团队已解散"}
    expected = {name: list(rows) for name, rows in before.items()}
    expected["teams"] = [row for row in before["teams"] if row["id"] != "team-a"]
    expected["team_members"] = [
        row for row in before["team_members"] if row["team_id"] != "team-a"
    ]
    expected["team_invites"] = [
        row for row in before["team_invites"] if row["team_id"] != "team-a"
    ]
    expected["series"] = [
        {**row, "team_id": None, "updated_at": now}
        if row["team_id"] == "team-a"
        else row
        for row in before["series"]
    ]
    assert full_owner_snapshot(owner_database) == expected
    assert [row["id"] for row in expected["team_invites"]] == ["team-b-invite"]
    assert [row["id"] for row in expected["team_members"] if row["team_id"] == "team-b"] == ["team-b-member-a"]
    assert [row["id"] for row in expected["chapter_locks"] if row["chapter_id"] == "chapter-b"] == ["team-b-lock"]
    series_a = next(row for row in expected["series"] if row["id"] == "series-a")
    series_b = next(row for row in expected["series"] if row["id"] == "series-b")
    assert (series_a["team_id"], series_a["claimed_by"], series_a["claimed_at"]) == (None, "user-b", now)
    assert (series_b["team_id"], series_b["claimed_by"], series_b["claimed_at"]) == ("team-b", "user-a", now)


def test_leave_releases_only_actor_locks_and_keeps_claim_state(owner_database) -> None:
    now = datetime(2026, 10, 9, 12)
    with owner_database.engine.begin() as connection:
        connection.execute(
            insert(OWNER_TABLES["teams"]).values(
                id="team-b",
                name="另一团队",
                owner_id="user-a",
                created_at=now,
            )
        )
        connection.execute(
            insert(OWNER_TABLES["team_members"]).values(
                id="team-b-member-b",
                team_id="team-b",
                user_id="user-b",
                role="member",
                permissions='["view_tasks"]',
                joined_at=now,
            )
        )
        connection.execute(
            update(OWNER_TABLES["series"])
            .where(OWNER_TABLES["series"].c.id == "series-b")
            .values(team_id="team-b")
        )
        connection.execute(
            insert(OWNER_TABLES["chapter_locks"]).values(
                id="owner-neighbor-lock",
                chapter_id="neighbor-a",
                user_id="user-a",
                username="owner-a",
                acquired_at=now,
                last_active_at=now,
                expires_at=now + timedelta(minutes=15),
            )
        )
        connection.execute(
            insert(OWNER_TABLES["chapter_locks"]).values(
                id="member-cross-team-lock",
                chapter_id="chapter-b",
                user_id="user-b",
                username="member-b",
                acquired_at=now,
                last_active_at=now,
                expires_at=now + timedelta(minutes=15),
            )
        )
        connection.execute(
            update(OWNER_TABLES["series"])
            .where(OWNER_TABLES["series"].c.id == "series-a")
            .values(claimed_by="user-b", claimed_at=now)
        )
    before = full_owner_snapshot(owner_database)
    assert next(row for row in before["series"] if row["id"] == "series-b")["team_id"] == "team-b"
    assert any(
        row["team_id"] == "team-b" and row["user_id"] == "user-b"
        for row in before["team_members"]
    )
    assert any(row["id"] == "member-cross-team-lock" for row in before["chapter_locks"])
    team_series_ids = {
        row["id"] for row in before["series"] if row["team_id"] == "team-a"
    }
    team_chapter_ids = {
        row["id"] for row in before["chapters"] if row["series_id"] in team_series_ids
    }

    result = leave_team(
        make_factory(owner_database, now=now),
        TrustedActor("user-b"),
        "team-a",
    )

    assert result == {"message": "已退出团队"}
    expected = {name: list(rows) for name, rows in before.items()}
    expected["team_members"] = [
        row
        for row in before["team_members"]
        if not (row["team_id"] == "team-a" and row["user_id"] == "user-b")
    ]
    expected["chapter_locks"] = [
        row
        for row in before["chapter_locks"]
        if not (
            row["chapter_id"] in team_chapter_ids
            and row["user_id"] == "user-b"
        )
    ]
    assert full_owner_snapshot(owner_database) == expected
    assert any(
        row["team_id"] == "team-b" and row["user_id"] == "user-b"
        for row in expected["team_members"]
    )
    assert any(row["id"] == "member-cross-team-lock" for row in expected["chapter_locks"])
    series = next(row for row in expected["series"] if row["id"] == "series-a")
    assert (series["claimed_by"], series["claimed_at"]) == ("user-b", now)





def test_team_update_noop_assignments_commit_without_sql_update(owner_database) -> None:
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    commits = 0
    last_readback_commit = 0
    team_updates: list[str] = []
    postcommit_team_reads: list[str] = []

    def after_commit(_session):
        nonlocal commits
        commits += 1

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        nonlocal last_readback_commit
        normalized = " ".join(statement.lower().split())
        if normalized.startswith("update teams"):
            team_updates.append(normalized)
        if (
            commits > last_readback_commit
            and normalized.startswith("select")
            and " from teams " in normalized
        ):
            postcommit_team_reads.append(normalized)
            last_readback_commit = commits

    event.listen(Session, "after_commit", after_commit)
    event.listen(owner_database.engine, "before_cursor_execute", capture)
    try:
        update_team(
            make_factory(owner_database),
            TrustedActor("user-a"),
            "team-a",
            TeamUpdate(),
        )
        update_team(
            make_factory(owner_database),
            TrustedActor("user-a"),
            "team-a",
            TeamUpdate(name=None),
        )
        update_team(
            make_factory(owner_database),
            TrustedActor("user-a"),
            "team-a",
            TeamUpdate(name=" 测试团队 "),
        )
    finally:
        event.remove(owner_database.engine, "before_cursor_execute", capture)
        event.remove(Session, "after_commit", after_commit)

    assert commits == 3
    assert team_updates == []
    assert len(postcommit_team_reads) == 3


def test_invite_revoke_preserves_status_guards_and_team_scope(owner_database) -> None:
    from haoai_backend.teams import TeamBadRequest, TeamNotFound
    from sqlalchemy import update as sql_update

    add_invite(
        owner_database,
        invite_id="active-invite",
        code="ACTIVE01",
        expires_at=datetime(2026, 10, 10, 12),
    )
    add_invite(
        owner_database,
        invite_id="used-invite",
        code="USED0001",
        expires_at=datetime(2026, 10, 10, 12),
    )
    add_invite(
        owner_database,
        invite_id="expired-invite",
        code="OLD00001",
        expires_at=datetime(2026, 10, 8, 12),
    )
    with owner_database.engine.begin() as connection:
        connection.execute(
            sql_update(team_invites)
            .where(team_invites.c.id == "used-invite")
            .values(status="used", used_by="user-b", used_at=datetime(2026, 10, 9, 11))
        )
        connection.execute(
            sql_update(team_invites)
            .where(team_invites.c.id == "expired-invite")
            .values(status="expired")
        )
        connection.execute(insert(OWNER_TABLES["teams"]).values(
            id="team-b",
            name="另一个团队",
            owner_id="user-a",
            created_at=datetime(2026, 10, 9, 10),
        ))
        connection.execute(
            insert(team_invites).values(
                id="other-team-invite",
                team_id="team-b",
                code="OTHER001",
                created_by="user-a",
                created_at=datetime(2026, 10, 9, 11),
                expires_at=datetime(2026, 10, 10, 11),
                used_by=None,
                used_at=None,
                status="active",
            )
        )

    before = full_owner_snapshot(owner_database)
    factory = make_factory(owner_database)
    commits = 0
    invite_updates: list[str] = []

    def after_commit(_session):
        nonlocal commits
        commits += 1

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        normalized = " ".join(statement.lower().split())
        if normalized.startswith("update team_invites"):
            invite_updates.append(normalized)

    event.listen(Session, "after_commit", after_commit)
    event.listen(owner_database.engine, "before_cursor_execute", capture)
    try:
        with pytest.raises(TeamNotFound, match="邀请码不存在"):
            revoke_invite(factory, TrustedActor("user-a"), "team-a", "other-team-invite")
        with pytest.raises(TeamBadRequest, match="已被使用"):
            revoke_invite(factory, TrustedActor("user-a"), "team-a", "used-invite")
        assert full_owner_snapshot(owner_database) == before

        assert revoke_invite(
            factory,
            TrustedActor("user-a"),
            "team-a",
            "active-invite",
        ) == {"message": "邀请码已作废"}
        assert revoke_invite(
            factory,
            TrustedActor("user-a"),
            "team-a",
            "expired-invite",
        ) == {"message": "邀请码已作废"}
    finally:
        event.remove(owner_database.engine, "before_cursor_execute", capture)
        event.remove(Session, "after_commit", after_commit)

    assert commits == 2
    assert len(invite_updates) == 1
    after = full_owner_snapshot(owner_database)
    assert next(row for row in after["team_invites"] if row["id"] == "active-invite")["status"] == "expired"
    assert next(row for row in after["team_invites"] if row["id"] == "used-invite")["status"] == "used"
    assert next(row for row in after["team_invites"] if row["id"] == "other-team-invite")["status"] == "active"


def test_invite_list_expires_only_overdue_active_rows_in_newest_order(owner_database) -> None:
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    now = datetime(2026, 10, 9, 12)
    rows = [
        ("old-expired", datetime(2026, 10, 9, 8), datetime(2026, 10, 9, 7), "expired"),
        ("overdue-active", datetime(2026, 10, 9, 9), datetime(2026, 10, 9, 11), "active"),
        ("used-future", datetime(2026, 10, 9, 10), datetime(2026, 10, 10, 10), "used"),
        ("future-active", datetime(2026, 10, 9, 11), datetime(2026, 10, 10, 11), "active"),
    ]
    with owner_database.engine.begin() as connection:
        connection.execute(
            insert(OWNER_TABLES["teams"]).values(
                id="team-b",
                name="另一个团队",
                owner_id="user-a",
                created_at=now,
            )
        )
        for invite_id, created_at, expires_at, status in rows:
            connection.execute(
                insert(team_invites).values(
                    id=invite_id,
                    team_id="team-a",
                    code=invite_id.upper()[:8],
                    created_by="user-a",
                    created_at=created_at,
                    expires_at=expires_at,
                    used_by="user-b" if status == "used" else None,
                    used_at=created_at if status == "used" else None,
                    status=status,
                )
            )
        connection.execute(
            insert(team_invites).values(
                id="cross-team",
                team_id="team-b",
                code="CROSS001",
                created_by="user-a",
                created_at=now,
                expires_at=now + timedelta(days=1),
                used_by=None,
                used_at=None,
                status="active",
            )
        )

    before_snapshot = full_owner_snapshot(owner_database)
    commits = 0
    updates: list[str] = []

    def after_commit(_session):
        nonlocal commits
        commits += 1

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        normalized = " ".join(statement.lower().split())
        if normalized.startswith("update team_invites"):
            updates.append(normalized)

    event.listen(Session, "after_commit", after_commit)
    event.listen(owner_database.engine, "before_cursor_execute", capture)
    try:
        first = list_invites(make_factory(owner_database, now=now), TrustedActor("user-a"), "team-a")
        commits_after_first = commits
        second = list_invites(make_factory(owner_database, now=now), TrustedActor("user-a"), "team-a")
    finally:
        event.remove(owner_database.engine, "before_cursor_execute", capture)
        event.remove(Session, "after_commit", after_commit)

    expected_ids = ["future-active", "used-future", "overdue-active", "old-expired"]
    assert [row["id"] for row in first] == expected_ids
    assert [row["id"] for row in second] == expected_ids
    assert next(row for row in first if row["id"] == "overdue-active")["status"] == "expired"
    assert commits_after_first == 1
    assert commits == 1
    assert len(updates) == 1
    assert "cross-team" not in {row["id"] for row in first}

    expected_snapshot = {name: list(rows) for name, rows in before_snapshot.items()}
    expected_snapshot["team_invites"] = [
        {**row, "status": "expired"}
        if row["id"] == "overdue-active"
        else row
        for row in expected_snapshot["team_invites"]
    ]
    assert full_owner_snapshot(owner_database) == expected_snapshot


def test_management_role_removal_priority_and_full_row_conservation(owner_database) -> None:
    from haoai_backend.teams import TeamBadRequest, TeamForbidden

    add_user(owner_database, "user-c")
    add_user(owner_database, "user-d")
    now = datetime(2026, 10, 9, 12)
    with owner_database.engine.begin() as connection:
        connection.execute(
            insert(OWNER_TABLES["team_members"]).values(
                id="member-c",
                team_id="team-a",
                user_id="user-c",
                role="member",
                permissions=None,
                joined_at=now,
            )
        )
        connection.execute(
            insert(OWNER_TABLES["team_members"]).values(
                id="member-d",
                team_id="team-a",
                user_id="user-d",
                role="admin",
                permissions=None,
                joined_at=now,
            )
        )
        connection.execute(
            insert(OWNER_TABLES["chapter_locks"]).values(
                id="user-c-lock",
                chapter_id="neighbor-a",
                user_id="user-c",
                username="user-c",
                acquired_at=now,
                last_active_at=now,
                expires_at=now + timedelta(minutes=15),
            )
        )
        connection.execute(
            update(OWNER_TABLES["teams"])
            .where(OWNER_TABLES["teams"].c.id == "team-a")
            .values(owner_id="user-d")
        )
        connection.execute(
            update(OWNER_TABLES["users"])
            .where(OWNER_TABLES["users"].c.id == "user-b")
            .values(is_superuser=True)
        )

    owner = TrustedActor("user-a")
    factory = make_factory(owner_database)
    with pytest.raises(TeamForbidden):
        remove_member(factory, TrustedActor("user-b"), "team-a", "user-c")
    set_member_role(factory, owner, "team-a", "user-b", RoleUpdate(role="admin"))
    update_member_permissions(
        factory,
        owner,
        "team-a",
        "user-b",
        PermissionsUpdate(permissions=["remove_members"]),
    )
    before_denials = full_owner_snapshot(owner_database)
    with pytest.raises(TeamBadRequest, match="不能移除队长"):
        remove_member(factory, TrustedActor("user-b"), "team-a", "user-a")
    with pytest.raises(TeamForbidden, match="仅队长可移除管理员"):
        remove_member(factory, TrustedActor("user-b"), "team-a", "user-d")
    with pytest.raises(TeamBadRequest, match="不能移除自己"):
        remove_member(factory, TrustedActor("user-b"), "team-a", "user-b")
    assert full_owner_snapshot(owner_database) == before_denials

    before_remove = full_owner_snapshot(owner_database)
    assert remove_member(factory, owner, "team-a", "user-c") == {"message": "成员已移除"}
    expected = {name: list(rows) for name, rows in before_remove.items()}
    expected["team_members"] = [
        row for row in before_remove["team_members"] if row["id"] != "member-c"
    ]
    expected["chapter_locks"] = [
        row for row in before_remove["chapter_locks"] if row["id"] != "user-c-lock"
    ]
    assert full_owner_snapshot(owner_database) == expected
    saved_team = next(row for row in expected["teams"] if row["id"] == "team-a")
    assert saved_team["owner_id"] == "user-d"


def test_postcommit_team_and_member_readback_sql_failures_are_durable_without_replay(
    owner_database,
) -> None:
    def fail_after_commit(table_name: str, detail: str):
        committed = False
        read_attempts: list[str] = []

        def after_commit(_session):
            nonlocal committed
            committed = True

        def before_cursor_execute(
            _connection,
            _cursor,
            statement,
            _parameters,
            _context,
            _many,
        ):
            normalized = " ".join(statement.lower().split())
            if (
                committed
                and normalized.startswith("select")
                and f" from {table_name} " in normalized
            ):
                read_attempts.append(normalized)
                raise RuntimeError(detail)

        event.listen(Session, "after_commit", after_commit)
        event.listen(owner_database.engine, "before_cursor_execute", before_cursor_execute)

        def remove_listeners() -> None:
            event.remove(owner_database.engine, "before_cursor_execute", before_cursor_execute)
            event.remove(Session, "after_commit", after_commit)

        return read_attempts, remove_listeners

    team_reads, remove_team_listeners = fail_after_commit(
        "teams", "team readback SQL failed"
    )
    try:
        with pytest.raises(Exception, match="team readback SQL failed"):
            update_team(
                make_factory(owner_database),
                TrustedActor("user-a"),
                "team-a",
                TeamUpdate(name="持久化后读回失败"),
            )
    finally:
        remove_team_listeners()
    assert len(team_reads) == 1
    saved_team = owner_database.snapshot(("teams",))["teams"][0]
    assert saved_team["name"] == "持久化后读回失败"

    member_reads, remove_member_listeners = fail_after_commit(
        "team_members", "member readback SQL failed"
    )
    try:
        with pytest.raises(Exception, match="member readback SQL failed"):
            set_member_role(
                make_factory(owner_database),
                TrustedActor("user-a"),
                "team-a",
                "user-b",
                RoleUpdate(role="admin"),
            )
    finally:
        remove_member_listeners()
    assert len(member_reads) == 1
    member = next(
        row
        for row in owner_database.snapshot(("team_members",))["team_members"]
        if row["id"] == "team-member-b"
    )
    assert member["role"] == "admin"
