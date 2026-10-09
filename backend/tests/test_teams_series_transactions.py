from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, event, insert, select, text, update
from sqlalchemy.orm import Session

from chapter_asset_replacement_support import (
    TrackedSession,
    chapter_locks as owner_chapter_locks,
    chapters as owner_chapters,
    create_owner_database,
    series as owner_series,
    team_members,
    teams,
)
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.teams.errors import StaleTeamWrite
from haoai_backend.teams.series.application import (
    create_team_series,
    share_series_to_team,
)
from haoai_backend.teams.series.persistence import (
    SeriesSqlAlchemyUnitOfWork,
    create_series_uow_factory,
)
from haoai_backend.teams import ShareSeriesRequest, TeamSeriesCreate


NOW = datetime(2026, 10, 9, 12, 0, 0)


@pytest.fixture
def owner_database(tmp_path):
    database = create_owner_database(tmp_path / "teams-series-transactions.sqlite")
    try:
        database.assert_foreign_keys_enabled()
        yield database
    finally:
        database.close()


def seed_second_team_and_non_target_lock(owner_database):
    with owner_database.engine.begin() as connection:
        connection.execute(
            insert(teams).values(
                id="team-b",
                name="另一个团队",
                owner_id="user-a",
                created_at=NOW,
            )
        )
        connection.execute(
            insert(team_members).values(
                id="membership-a-b",
                team_id="team-b",
                user_id="user-a",
                role="member",
                permissions=None,
                joined_at=NOW,
            )
        )
        connection.execute(
            insert(owner_chapter_locks).values(
                id="non-target-lock-b",
                chapter_id="chapter-b",
                user_id="user-b",
                username="成员 B",
                acquired_at=NOW,
                last_active_at=NOW,
                expires_at=NOW + timedelta(hours=1),
            )
        )


def track_rollback(session):
    calls: list[bool] = []
    rollback = session.rollback

    def recorded_rollback():
        calls.append(True)
        return rollback()

    session.rollback = recorded_rollback
    return calls


def test_same_team_share_keeps_claim_clears_lock_and_reads_team_after_commit(owner_database):
    with owner_database.engine.begin() as connection:
        connection.execute(
            update(owner_series)
            .where(owner_series.c.id == "series-a")
            .values(claimed_by="user-b", claimed_at=NOW)
        )
    before = owner_database.snapshot()
    statements: list[str] = []
    commit_positions: list[int] = []
    clock_calls: list[datetime] = []

    def record_statement(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.lstrip().upper())

    def after_commit(_session):
        commit_positions.append(len(statements))

    event.listen(owner_database.engine, "before_cursor_execute", record_statement)
    event.listen(Session, "after_commit", after_commit)
    try:
        result = share_series_to_team(
            create_series_uow_factory(
                owner_database.session_factory,
                utc_clock=lambda: clock_calls.append(NOW) or NOW,
            ),
            TrustedActor("user-a"),
            "series-a",
            ShareSeriesRequest(team_id="team-a", claim=False),
        )
    finally:
        event.remove(Session, "after_commit", after_commit)
        event.remove(owner_database.engine, "before_cursor_execute", record_statement)

    after = owner_database.snapshot()
    assert result == {
        "message": "已分享到团队「测试团队」",
        "team_id": "team-a",
        "team_name": "测试团队",
    }
    assert after["series"] == before["series"]
    assert after["chapter_locks"] == []
    for table_name in before:
        if table_name not in {"series", "chapter_locks"}:
            assert after[table_name] == before[table_name], table_name

    assert len(commit_positions) == 1
    assert not any(
        statement.startswith("UPDATE SERIES") for statement in statements
    )
    assert clock_calls == []
    post_commit_statements = statements[commit_positions[0] :]
    assert any(
        statement.startswith("SELECT") and "FROM TEAMS" in statement
        for statement in post_commit_statements
    )


def test_dirty_share_clears_locks_before_delayed_updated_at_and_keeps_claim(
    owner_database,
):
    seed_second_team_and_non_target_lock(owner_database)
    with owner_database.engine.begin() as connection:
        connection.execute(
            update(owner_series)
            .where(owner_series.c.id == "series-a")
            .values(claimed_by="user-b", claimed_at=NOW)
        )

    before = owner_database.snapshot()
    events: list[str] = []
    updates: list[str] = []

    def clock() -> datetime:
        events.append("clock")
        return NOW + timedelta(seconds=1)

    def record_statement(_connection, _cursor, statement, _parameters, _context, _many):
        normalized = statement.lstrip().upper()
        if normalized.startswith("DELETE FROM CHAPTER_LOCKS"):
            events.append("lock-delete")
        elif normalized.startswith("UPDATE SERIES"):
            events.append("series-update")
            updates.append(normalized)

    event.listen(owner_database.engine, "before_cursor_execute", record_statement)
    try:
        result = share_series_to_team(
            create_series_uow_factory(
                owner_database.session_factory,
                utc_clock=clock,
            ),
            TrustedActor("user-a"),
            "series-a",
            ShareSeriesRequest(team_id="team-b", claim=False),
        )
    finally:
        event.remove(owner_database.engine, "before_cursor_execute", record_statement)

    after = owner_database.snapshot()
    assert result == {
        "message": "已分享到团队「另一个团队」",
        "team_id": "team-b",
        "team_name": "另一个团队",
    }
    saved = next(row for row in after["series"] if row["id"] == "series-a")
    assert saved["team_id"] == "team-b"
    assert saved["claimed_by"] == "user-b"
    assert saved["claimed_at"] == NOW
    assert saved["updated_at"] == NOW + timedelta(seconds=1)
    assert before["chapter_locks"]
    assert [row["id"] for row in after["chapter_locks"]] == ["non-target-lock-b"]
    for table_name in before:
        if table_name not in {"series", "chapter_locks"}:
            assert after[table_name] == before[table_name], table_name

    assert events == ["lock-delete", "clock", "series-update"]
    assert len(updates) == 1
    assert "UPDATED_AT" in updates[0]


def test_commit_failure_rolls_back_series_insert_and_closes_session(owner_database):
    sessions = []

    def factory():
        session = owner_database.session_factory()
        sessions.append(session)

        def fail_before_commit(_session):
            raise RuntimeError("simulated commit failure")

        event.listen(session, "before_commit", fail_before_commit)
        return SeriesSqlAlchemyUnitOfWork(
            session,
            utc_clock=lambda: NOW,
            id_source=lambda: "series-failed",
        )

    with pytest.raises(RuntimeError, match="simulated commit failure"):
        create_team_series(
            factory,
            TrustedActor("user-b"),
            "team-a",
            TeamSeriesCreate(name="失败事务"),
        )

    assert len(sessions) == 1
    assert sessions[0].close_calls == 1
    with owner_database.engine.connect() as connection:
        assert connection.execute(
            select(owner_series.c.id).where(owner_series.c.id == "series-failed")
        ).first() is None


def test_committed_missing_pk_readback_is_durable_and_does_not_retry(owner_database):
    before = owner_database.snapshot()
    sessions = []
    rollback_calls: list[list[bool]] = []
    commits: list[bool] = []
    durable_rows: list[dict[str, object]] = []
    readbacks: list[str] = []
    state = {"commit_callback_done": False}
    insert_statements: list[str] = []

    def after_commit(session):
        if not sessions or session is not sessions[0]:
            return
        commits.append(True)
        with owner_database.engine.begin() as connection:
            row = connection.execute(
                select(owner_series).where(owner_series.c.id == "series-durable")
            ).mappings().one()
            durable_rows.append(dict(row))
            connection.execute(
                delete(owner_series).where(owner_series.c.id == "series-durable")
            )
        state["commit_callback_done"] = True

    def record_statement(_connection, _cursor, statement, _parameters, _context, _many):
        normalized = statement.lstrip().upper()
        if normalized.startswith("INSERT INTO SERIES"):
            insert_statements.append(normalized)
        if (
            state["commit_callback_done"]
            and normalized.startswith("SELECT")
            and "FROM SERIES" in normalized
        ):
            readbacks.append(normalized)

    event.listen(Session, "after_commit", after_commit)
    event.listen(owner_database.engine, "before_cursor_execute", record_statement)
    try:
        def factory():
            session = owner_database.session_factory()
            sessions.append(session)
            rollback_calls.append(track_rollback(session))
            return SeriesSqlAlchemyUnitOfWork(
                session,
                utc_clock=lambda: NOW,
                id_source=lambda: "series-durable",
            )

        with pytest.raises(RuntimeError, match="could not be read back"):
            create_team_series(
                factory,
                TrustedActor("user-b"),
                "team-a",
                TeamSeriesCreate(name="提交后真实读回缺行"),
            )
    finally:
        event.remove(owner_database.engine, "before_cursor_execute", record_statement)
        event.remove(Session, "after_commit", after_commit)

    assert len(commits) == 1
    assert len(durable_rows) == 1
    assert durable_rows[0]["name"] == "提交后真实读回缺行"
    assert len(readbacks) == 1
    assert len(insert_statements) == 1
    assert rollback_calls == [[True]]
    assert len(sessions) == 1
    assert sessions[0].close_calls == 1
    assert owner_database.snapshot() == before


def test_commit_acknowledgement_failure_keeps_durable_insert_without_replay(
    owner_database,
):
    sessions = []
    commits: list[bool] = []
    rollback_calls: list[list[bool]] = []
    post_commit_series_reads: list[str] = []
    insert_statements: list[str] = []
    committed = False

    def after_commit(session):
        nonlocal committed
        if sessions and session is sessions[0]:
            commits.append(True)
            committed = True

    def record_statement(_connection, _cursor, statement, _parameters, _context, _many):
        normalized = statement.lstrip().upper()
        if normalized.startswith("INSERT INTO SERIES"):
            insert_statements.append(normalized)
        if committed and normalized.startswith("SELECT") and "FROM SERIES" in normalized:
            post_commit_series_reads.append(normalized)

    event.listen(Session, "after_commit", after_commit)
    event.listen(owner_database.engine, "before_cursor_execute", record_statement)
    try:
        def factory():
            session = owner_database.session_factory()
            sessions.append(session)
            rollback_calls.append(track_rollback(session))
            commit = session.commit

            def commit_then_lose_acknowledgement():
                commit()
                raise RuntimeError("simulated lost commit acknowledgement")

            session.commit = commit_then_lose_acknowledgement
            return SeriesSqlAlchemyUnitOfWork(
                session,
                utc_clock=lambda: NOW,
                id_source=lambda: "series-ack-unknown",
            )

        with pytest.raises(RuntimeError, match="lost commit acknowledgement"):
            create_team_series(
                factory,
                TrustedActor("user-b"),
                "team-a",
                TeamSeriesCreate(name="提交确认未知"),
            )
    finally:
        event.remove(owner_database.engine, "before_cursor_execute", record_statement)
        event.remove(Session, "after_commit", after_commit)

    assert len(commits) == 1
    assert len(insert_statements) == 1
    assert post_commit_series_reads == []
    assert rollback_calls == [[True]]
    assert len(sessions) == 1
    assert sessions[0].close_calls == 1
    with owner_database.engine.connect() as connection:
        row = connection.execute(
            select(owner_series).where(owner_series.c.id == "series-ack-unknown")
        ).mappings().one()
    assert row["name"] == "提交确认未知"


def test_zero_row_dirty_update_is_generic_failure_and_rolls_back_prior_lock_delete(
    owner_database,
):
    seed_second_team_and_non_target_lock(owner_database)
    before = owner_database.snapshot()
    statements: list[tuple[str, int | None]] = []
    commits: list[bool] = []
    sessions = []
    rollback_calls: list[list[bool]] = []

    def after_cursor(
        _connection, _cursor, statement, _parameters, context, _many
    ):
        normalized = statement.lstrip().upper()
        if normalized.startswith("DELETE FROM CHAPTER_LOCKS") or normalized.startswith(
            "UPDATE SERIES"
        ):
            statements.append((normalized, context.rowcount))

    def after_commit(_session):
        commits.append(True)

    event.listen(owner_database.engine, "after_cursor_execute", after_cursor)
    event.listen(Session, "after_commit", after_commit)
    try:
        def factory():
            session = owner_database.session_factory()
            sessions.append(session)
            rollback_calls.append(track_rollback(session))
            return SeriesSqlAlchemyUnitOfWork(
                session,
                utc_clock=lambda: NOW + timedelta(seconds=1),
            )

        with owner_database.engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TRIGGER ignore_series_a_update "
                "BEFORE UPDATE ON series WHEN OLD.id = 'series-a' "
                "BEGIN SELECT RAISE(IGNORE); END"
            )

        with pytest.raises(StaleTeamWrite) as caught:
            share_series_to_team(
                factory,
                TrustedActor("user-a"),
                "series-a",
                ShareSeriesRequest(team_id="team-b", claim=False),
            )
    finally:
        event.remove(Session, "after_commit", after_commit)
        event.remove(owner_database.engine, "after_cursor_execute", after_cursor)

    assert not hasattr(caught.value, "status_code")
    assert len(commits) == 0
    assert rollback_calls == [[True]]
    assert len(sessions) == 1
    assert sessions[0].close_calls == 1
    delete_rows = [
        rowcount
        for statement, rowcount in statements
        if statement.startswith("DELETE FROM CHAPTER_LOCKS")
    ]
    update_rows = [
        rowcount
        for statement, rowcount in statements
        if statement.startswith("UPDATE SERIES")
    ]
    assert delete_rows == [1]
    assert update_rows == [0]
    assert owner_database.snapshot() == before


def test_zero_row_bulk_lock_delete_is_legal_and_other_rows_are_preserved(owner_database):
    seed_second_team_and_non_target_lock(owner_database)
    with owner_database.engine.begin() as connection:
        connection.execute(
            delete(owner_chapter_locks).where(
                owner_chapter_locks.c.chapter_id == "chapter-a"
            )
        )
    before = owner_database.snapshot()
    delete_rowcounts: list[int] = []

    def after_cursor(
        _connection, _cursor, statement, _parameters, context, _many
    ):
        if statement.lstrip().upper().startswith("DELETE FROM CHAPTER_LOCKS"):
            delete_rowcounts.append(context.rowcount)

    event.listen(owner_database.engine, "after_cursor_execute", after_cursor)
    try:
        result = share_series_to_team(
            create_series_uow_factory(
                owner_database.session_factory,
                utc_clock=lambda: NOW + timedelta(seconds=1),
            ),
            TrustedActor("user-a"),
            "series-a",
            ShareSeriesRequest(team_id="team-b", claim=False),
        )
    finally:
        event.remove(owner_database.engine, "after_cursor_execute", after_cursor)

    after = owner_database.snapshot()
    assert result["team_id"] == "team-b"
    assert delete_rowcounts == [0]
    saved = next(row for row in after["series"] if row["id"] == "series-a")
    original = next(row for row in before["series"] if row["id"] == "series-a")
    assert saved["team_id"] == "team-b"
    assert saved["updated_at"] == NOW + timedelta(seconds=1)
    for table_name in before:
        if table_name != "series":
            assert after[table_name] == before[table_name], table_name
    assert all(
        saved[key] == original[key]
        for key in original
        if key not in {"team_id", "updated_at"}
    )


def run_interleaved_assignment(
    owner_database,
    *,
    requested_description: str,
    later_description: str,
    expect_update: bool,
):
    before = owner_database.snapshot()
    assert all(before[table_name] for table_name in before)
    clock_calls: list[datetime] = []
    updates: list[tuple[str, int | None]] = []

    with owner_database.physical_connection_pair() as (first, second):
        first_dbapi = first.connection.dbapi_connection
        second_dbapi = second.connection.dbapi_connection
        assert first_dbapi is not second_dbapi

        session = TrackedSession(
            bind=first,
            autoflush=False,
            expire_on_commit=True,
            future=True,
        )
        uow = SeriesSqlAlchemyUnitOfWork(
            session,
            utc_clock=lambda: clock_calls.append(NOW + timedelta(seconds=1))
            or NOW + timedelta(seconds=1),
        )
        try:
            first_snapshot = uow.load_series("series-a")
            assert first_snapshot is not None
            assert first_snapshot["description"] == "完整 owner fixture"
            assert first_dbapi.in_transaction is False
            uow.stage_series_assignment(
                first_snapshot,
                {"description": requested_description},
            )
            assert clock_calls == []

            second.execute(
                update(owner_series)
                .where(owner_series.c.id == "series-a")
                .values(description=later_description)
            )
            second.commit()
            assert first_dbapi.in_transaction is False
            assert uow.load_series("series-a")["description"] == "完整 owner fixture"

            def record_update(
                _connection, _cursor, statement, _parameters, context, _many
            ):
                if statement.lstrip().upper().startswith("UPDATE SERIES"):
                    updates.append((statement.lstrip().upper(), context.rowcount))

            event.listen(owner_database.engine, "after_cursor_execute", record_update)
            try:
                uow.commit()
            finally:
                event.remove(owner_database.engine, "after_cursor_execute", record_update)
        finally:
            uow.close()

        assert session.close_calls == 1

    after = owner_database.snapshot()
    expected_snapshot = {
        table_name: [dict(row) for row in rows]
        for table_name, rows in before.items()
    }
    expected_series = next(
        row for row in expected_snapshot["series"] if row["id"] == "series-a"
    )
    expected_series["description"] = (
        requested_description if expect_update else later_description
    )
    if expect_update:
        expected_series["updated_at"] = NOW + timedelta(seconds=1)

    assert after == expected_snapshot
    assert len(updates) == (1 if expect_update else 0)
    if expect_update:
        statement, rowcount = updates[0]
        set_clause = statement.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
        columns = [part.split("=", 1)[0].strip() for part in set_clause.split(",")]
        assert columns == ["DESCRIPTION", "UPDATED_AT"]
        assert rowcount == 1
        assert clock_calls == [NOW + timedelta(seconds=1)]
    else:
        assert clock_calls == []


def test_two_connections_keep_first_snapshot_assignment_when_later_writer_changes_row(
    owner_database,
):
    run_interleaved_assignment(
        owner_database,
        requested_description="request-after-first-snapshot",
        later_description="request-after-first-snapshot",
        expect_update=True,
    )


def test_two_connections_do_not_expand_noop_assignment_after_later_writer(
    owner_database,
):
    run_interleaved_assignment(
        owner_database,
        requested_description="完整 owner fixture",
        later_description="writer-after-first-snapshot",
        expect_update=False,
    )


def test_reverted_assignment_has_no_net_update_or_updated_at_clock(owner_database):
    before = owner_database.snapshot()
    clock_calls: list[datetime] = []
    updates: list[str] = []
    commits: list[bool] = []
    session = owner_database.session_factory()
    uow = SeriesSqlAlchemyUnitOfWork(
        session,
        utc_clock=lambda: clock_calls.append(NOW + timedelta(seconds=1))
        or NOW + timedelta(seconds=1),
    )
    snapshot = uow.load_series("series-a")
    assert snapshot is not None
    uow.stage_series_assignment(snapshot, {"team_id": "team-b"})
    uow.stage_series_assignment(snapshot, {"team_id": snapshot["team_id"]})

    def record_update(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("UPDATE SERIES"):
            updates.append(statement.lstrip().upper())

    def after_commit(committed_session):
        if committed_session is session:
            commits.append(True)

    event.listen(owner_database.engine, "before_cursor_execute", record_update)
    event.listen(Session, "after_commit", after_commit)
    try:
        uow.commit()
    finally:
        event.remove(Session, "after_commit", after_commit)
        event.remove(owner_database.engine, "before_cursor_execute", record_update)
        uow.close()

    assert commits == [True]
    assert updates == []
    assert clock_calls == []
    assert owner_database.snapshot() == before
