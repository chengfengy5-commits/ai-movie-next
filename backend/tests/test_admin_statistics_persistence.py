from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, event, insert
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.admin_statistics.persistence import SqlAlchemyAdminStatisticsUnitOfWork
from haoai_backend.admin_statistics.tables import (
    ai_tasks, chapters, characters, credit_logs, metadata, props, scenes,
    series, system_configs, user_credits,
)
from haoai_backend.authentication.tables import metadata as auth_metadata, users as auth_users


NOW = datetime(2026, 10, 13, 12, 0, 0)
MONTH_START = datetime(2026, 10, 1)


def seed(engine):
    with engine.begin() as connection:
        connection.execute(insert(auth_users), [
            {"id": "active", "username": "active", "email": "active@example.test",
             "hashed_password": "x", "is_superuser": False, "membership_type": "premium",
             "membership_expires_at": None, "avatar_url": None, "bio": None,
             "created_at": datetime(2026, 10, 2), "password_updated_at": None},
            {"id": "expired", "username": "expired", "email": "expired@example.test",
             "hashed_password": "x", "is_superuser": False, "membership_type": "premium",
             "membership_expires_at": datetime(2026, 10, 1), "avatar_url": None, "bio": None,
             "created_at": datetime(2026, 10, 3), "password_updated_at": None},
            {"id": "free", "username": "free", "email": "free@example.test",
             "hashed_password": "x", "is_superuser": False, "membership_type": "free",
             "membership_expires_at": None, "avatar_url": None, "bio": None,
             "created_at": datetime(2026, 9, 30), "password_updated_at": None},
        ])
        connection.execute(insert(user_credits), [
            {"id": "credit-1", "user_id": "active", "credits": 40},
            {"id": "credit-2", "user_id": "expired", "credits": 48},
        ])
        connection.execute(insert(credit_logs), [
            {"id": "log-use", "user_id": "active", "amount": -15, "created_at": datetime(2026, 10, 5)},
            {"id": "log-charge", "user_id": "active", "amount": 9, "created_at": datetime(2026, 10, 6)},
            {"id": "log-old", "user_id": "free", "amount": -50, "created_at": datetime(2026, 9, 30)},
        ])
        connection.execute(insert(ai_tasks), [
            {"id": "task-ok", "status": "completed", "created_at": datetime(2026, 10, 7)},
            {"id": "task-fail", "status": "failed", "created_at": datetime(2026, 10, 8)},
            {"id": "task-old", "status": "completed", "created_at": datetime(2026, 9, 30)},
        ])
        for table, count in ((series, 2), (chapters, 3), (characters, 4), (scenes, 5), (props, 6)):
            connection.execute(insert(table), [{"id": f"{table.name}-{i}"} for i in range(count)])
        connection.execute(insert(system_configs).values(id="config", muse_token="fake-muse-token"))


def test_sixteen_aggregates_run_in_fixed_order_and_use_legacy_filters(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'stats.sqlite'}")
    auth_metadata.create_all(engine)
    metadata.create_all(engine)
    seed(engine)
    statements = []
    def record(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(" ".join(statement.lower().split()))
    event.listen(engine, "before_cursor_execute", record)
    session = sessionmaker(engine, expire_on_commit=False)()
    uow = SqlAlchemyAdminStatisticsUnitOfWork(session)
    try:
        result = uow.aggregate_statistics(NOW, MONTH_START)
        assert result == {
            "total_users": 3, "premium_users": 2, "active_members": 1,
            "new_users_month": 2, "total_credits": 88,
            "credits_used_month": -15, "credits_charged_month": 9,
            "total_tasks": 3, "tasks_month": 2, "tasks_completed": 2,
            "tasks_failed": 1, "total_series": 2, "total_chapters": 3,
            "total_characters": 4, "total_scenes": 5, "total_props": 6,
        }
        assert len(statements) == 16
        expected_tables = [
            "users", "users", "users", "users", "user_credits",
            "credit_logs", "credit_logs", "ai_tasks", "ai_tasks",
            "ai_tasks", "ai_tasks", "series", "chapters", "characters",
            "scenes", "props",
        ]
        assert all(f"from {table}" in statement for table, statement in zip(expected_tables, statements))
        assert uow.get_muse_token() == "fake-muse-token"
    finally:
        session.close()
        engine.dispose()


def test_early_sql_error_propagates_without_partial_result(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'stats-error.sqlite'}")
    auth_metadata.create_all(engine)
    metadata.create_all(engine)
    session = sessionmaker(engine)()
    def fail_first(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            raise RuntimeError("first aggregate failed")
    event.listen(engine, "before_cursor_execute", fail_first)
    uow = SqlAlchemyAdminStatisticsUnitOfWork(session)
    try:
        try:
            uow.aggregate_statistics(NOW, MONTH_START)
        except RuntimeError as error:
            assert str(error) == "first aggregate failed"
        else:
            raise AssertionError("query failure should propagate")
    finally:
        session.close()
        engine.dispose()
