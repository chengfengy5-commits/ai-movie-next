from __future__ import annotations

from datetime import datetime, timedelta
import time

import pytest
from sqlalchemy import create_engine, event, insert, select
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.admin_model_configs import application
from haoai_backend.admin_model_configs.domain import AdminModelConfigActor
from haoai_backend.admin_model_configs.persistence import SqlAlchemyAdminModelConfigUnitOfWork
from haoai_backend.admin_model_configs.tables import ModelConfigRow, UserRow, metadata

ADMIN = AdminModelConfigActor(user_id="admin", is_superuser=True)
STAMP = datetime(2026, 10, 13, 9, 30)


class CountingSession(Session):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.commit_attempts = 0
        self.rollback_attempts = 0
        self.refresh_attempts = 0
        self.closed = False

    def commit(self):
        self.commit_attempts += 1
        return super().commit()

    def rollback(self):
        self.rollback_attempts += 1
        return super().rollback()

    def refresh(self, instance, *args, **kwargs):
        self.refresh_attempts += 1
        return super().refresh(instance, *args, **kwargs)

    def close(self):
        self.closed = True
        return super().close()


@pytest.fixture
def database():
    engine = create_engine("sqlite://")
    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
    metadata.create_all(engine)
    maker = sessionmaker(engine, class_=CountingSession, expire_on_commit=False, autoflush=False)
    sessions: list[CountingSession] = []
    def factory():
        session = maker()
        sessions.append(session)
        return SqlAlchemyAdminModelConfigUnitOfWork(session)
    try:
        yield engine, factory, sessions
    finally:
        engine.dispose()


def seed_user(engine, user_id="u1"):
    with engine.begin() as connection:
        connection.execute(
            insert(UserRow.__table__).values(
                id=user_id, username=user_id, email=f"{user_id}@example.test",
                hashed_password="unused", is_superuser=False, membership_type="free",
                membership_expires_at=None, avatar_url=None, bio=None,
                created_at=STAMP, password_updated_at=None,
            )
        )


def seed_config(engine, model_id, *, user_id="u1", order=0, **changes):
    values = {
        "id": model_id, "user_id": user_id, "category": "text", "name": model_id,
        "provider": "provider", "api_key": f"key-{model_id}", "model": "m",
        "base_url": "", "sort_order": order, "params": "{}",
        "created_at": STAMP + timedelta(seconds=order),
        "updated_at": STAMP + timedelta(seconds=order),
    }
    values.update(changes)
    with engine.begin() as connection:
        connection.execute(insert(ModelConfigRow.__table__).values(**values))


def test_real_insert_defaults_explicit_none_and_constraints(database):
    engine, factory, sessions = database
    seed_user(engine, "admin")
    result = application.create_model_config(factory, ADMIN, {})
    assert result["user_id"] == "admin"
    assert result["category"] == "text" and result["sort_order"] == 0
    assert result["name"] == result["provider"] == result["api_key"] == ""
    assert result["model"] == result["base_url"] == ""
    assert result["created_at"] is not None and result["updated_at"] is not None
    assert set(result) == {
        "id", "user_id", "category", "name", "provider", "model", "api_key",
        "base_url", "sort_order", "created_at", "updated_at",
    }
    assert sessions[0].commit_attempts == sessions[0].refresh_attempts == 1
    assert sessions[0].closed

    none_row = application.create_model_config(
        factory, ADMIN, {"category": "image", "name": "n", "provider": "p", "api_key": "k", "sort_order": None, "model": None}
    )
    assert none_row["sort_order"] == 0
    assert none_row["model"] is None
    with pytest.raises(IntegrityError):
        application.create_model_config(factory, ADMIN, {"category": None})
    assert sessions[-1].commit_attempts == 1
    assert sessions[-1].rollback_attempts >= 1
    assert sessions[-1].closed


def test_list_default_page_exact_user_filter_and_raw_sort_semantics(database):
    engine, factory, _sessions = database
    seed_user(engine, "u1")
    seed_user(engine, "u2")
    for number in range(11):
        seed_config(engine, f"m{number:02}", user_id="u1" if number < 9 else "u2", order=number)
    page = application.list_model_configs(
        factory, ADMIN, skip=0, limit=10, sort_field="created_at", sort_order="desc", user_id=None
    )
    assert page["total"] == 11 and len(page["data"]) == 10
    assert page["data"][0]["id"] == "m10"
    filtered = application.list_model_configs(
        factory, ADMIN, skip=0, limit=10, sort_field="sort_order", sort_order="asc", user_id="u1"
    )
    assert filtered["total"] == 9
    assert [row["sort_order"] for row in filtered["data"]] == list(range(9))
    assert application.list_model_configs(
        factory, ADMIN, skip=0, limit=10, sort_field="does_not_exist", sort_order="DESC", user_id=""
    )["total"] == 11
    with pytest.raises(SQLAlchemyError):
        application.list_model_configs(
            factory, ADMIN, skip=0, limit=10, sort_field="metadata", sort_order="desc", user_id=None
        )
    with pytest.raises(SQLAlchemyError):
        application.list_model_configs(
            factory, ADMIN, skip=0, limit=10, sort_field="user", sort_order="desc", user_id=None
        )


def test_detail_update_all_orm_attributes_and_query_first_on_identity_cached_row(database):
    engine, factory, sessions = database
    seed_user(engine)
    seed_config(engine, "m1")
    statements: list[str] = []
    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)
    event.listen(engine, "before_cursor_execute", capture)
    try:
        unit = factory()
        row = unit.get_model_config("m1")
        assert row is not None
        before = len([s for s in statements if "FROM model_configs" in s])
        unit.get_model_config("m1")
        after = len([s for s in statements if "FROM model_configs" in s])
        assert after == before + 1
        unit.close()

        custom_created = datetime(2020, 1, 2)
        custom_updated = datetime(2021, 3, 4)
        payload = application.update_model_config(
            factory, ADMIN, "m1",
            {"id": "other", "user_id": "other", "params": "[1]", "created_at": custom_created,
             "updated_at": custom_updated, "unknown_field": "ignored", "metadata": "replace class metadata",
             "registry": "replace class registry", "__table__": "replace table"},
        )
        assert payload["id"] == "m1" and payload["user_id"] == "u1"
        assert payload["created_at"] == custom_created and payload["updated_at"] == custom_updated
        with Session(engine) as verify:
            stored = verify.query(ModelConfigRow).filter(ModelConfigRow.id == "m1").first()
            assert stored.params == "[1]"
        assert sessions[-1].commit_attempts == sessions[-1].refresh_attempts == 1
        assert sessions[-1].closed
    finally:
        event.remove(engine, "before_cursor_execute", capture)


def test_relationship_assignment_failure_delete_has_no_postcommit_read(database):
    engine, factory, sessions = database
    seed_user(engine)
    seed_config(engine, "m1")
    with pytest.raises(IntegrityError):
        application.update_model_config(factory, ADMIN, "m1", {"user": None})
    assert sessions[-1].commit_attempts == 1
    assert sessions[-1].rollback_attempts >= 1 and sessions[-1].closed

    statements: list[str] = []
    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)
    event.listen(engine, "before_cursor_execute", capture)
    try:
        assert application.delete_model_config(factory, ADMIN, "m1") == {"message": "删除成功"}
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert sessions[-1].commit_attempts == 1 and sessions[-1].closed
    selects = [sql for sql in statements if sql.lstrip().upper().startswith("SELECT")]
    assert len(selects) == 1
    assert "FROM model_configs" in selects[0]
