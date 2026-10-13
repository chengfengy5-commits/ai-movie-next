from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.admin_redemption_codes import application
from haoai_backend.admin_redemption_codes.domain import AdminRedemptionCodesActor
from haoai_backend.admin_redemption_codes.persistence import (
    SqlAlchemyAdminRedemptionCodesUnitOfWork,
    admin_redemption_codes_uow_factory,
)
from haoai_backend.admin_redemption_codes.tables import (
    RedemptionCodeRow,
    UserRow,
    metadata,
)

ADMIN = AdminRedemptionCodesActor(user_id="admin", is_superuser=True)
STAMP = datetime(2026, 10, 13, 9, 30)


class CountingSession(Session):
    def __init__(self, *args, **kwargs):
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


def build_database(path: Path):
    engine = create_engine(f"sqlite:///{path}")
    metadata.create_all(engine)
    maker = sessionmaker(
        engine,
        class_=CountingSession,
        expire_on_commit=False,
        autoflush=False,
        future=True,
    )
    sessions: list[CountingSession] = []

    def session_factory():
        session = maker()
        sessions.append(session)
        return session

    with engine.begin() as connection:
        connection.execute(
            insert(UserRow.__table__).values(
                id="admin",
                username="admin",
                email="admin@example.test",
                hashed_password="unused",
                is_superuser=True,
                membership_type="free",
                membership_expires_at=None,
                avatar_url=None,
                bio=None,
                created_at=STAMP,
                password_updated_at=None,
            )
        )
        for code_id, code, code_type, value, is_used, created in (
            ("a", "HAOAI-AAA-0001", "credits", 0, False, STAMP),
            ("b", "HAOAI-BBB-0002", "credits", 0, True, STAMP + timedelta(seconds=1)),
            ("c", "OTHER-CCC-0003", "membership", 3, False, STAMP + timedelta(seconds=2)),
        ):
            connection.execute(
                insert(RedemptionCodeRow.__table__).values(
                    id=code_id,
                    code=code,
                    type=code_type,
                    value=value,
                    is_used=is_used,
                    used_by="admin" if is_used else None,
                    used_at=created if is_used else None,
                    created_by="admin",
                    expires_at=None,
                    created_at=created,
                )
            )
    return engine, admin_redemption_codes_uow_factory(session_factory), sessions


def test_filtered_count_precedes_page_query_and_raw_sort_fallback(tmp_path: Path):
    engine, factory, sessions = build_database(tmp_path / "filter-sort.sqlite")
    statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        page = application.list_redemption_codes(
            factory,
            ADMIN,
            code="HAOAI-%",
            code_type="credits",
            is_used=" YES ",
            value=0,
            sort_field="created_at",
            sort_order="desc",
            skip=0,
            limit=10,
        )
        assert page["total"] == 1
        assert [row["id"] for row in page["data"]] == ["a"]
        selects = [
            statement
            for statement in statements
            if statement.lstrip().upper().startswith("SELECT")
        ]
        assert len(selects) == 2
        assert "count(" in selects[0].lower()
        assert "FROM redemption_codes" in selects[0]
        assert "FROM redemption_codes" in selects[1]
        assert sessions[-1].commit_attempts == sessions[-1].refresh_attempts == 0
        assert sessions[-1].closed

        fallback = application.list_redemption_codes(
            factory,
            ADMIN,
            sort_field="missing_attribute",
            sort_order="DESC",
        )
        assert fallback["total"] == 3
        assert [row["id"] for row in fallback["data"]] == ["a", "b", "c"]
        with pytest.raises(SQLAlchemyError):
            application.list_redemption_codes(
                factory, ADMIN, sort_field="metadata", sort_order="desc"
            )
        assert all(session.closed for session in sessions)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
        engine.dispose()


def test_private_mapping_has_both_user_relationships_and_autoflush_false_duplicate_fails(tmp_path: Path):
    assert len(UserRow.__table__.columns) == 11
    assert len(RedemptionCodeRow.__table__.columns) == 10
    assert RedemptionCodeRow.creator.property.backref == "created_redemption_codes"
    assert RedemptionCodeRow.user.property.backref == "redeemed_redemption_codes"
    assert {
        foreign_key.target_fullname
        for column in (RedemptionCodeRow.used_by, RedemptionCodeRow.created_by)
        for foreign_key in column.foreign_keys
    } == {"users.id"}

    engine, factory, sessions = build_database(tmp_path / "pending-duplicate.sqlite")
    try:
        with pytest.raises(IntegrityError):
            application.create_redemption_codes(
                factory,
                ADMIN,
                {"type": "credits", "value": 1, "count": 2},
                code_generator=lambda: "HAOAI-PENDING-0001",
            )
        assert sessions[0].commit_attempts == 1
        assert sessions[0].rollback_attempts >= 1
        assert sessions[0].refresh_attempts == 0
        assert sessions[0].closed
        with Session(engine) as verify:
            assert (
                verify.query(RedemptionCodeRow)
                .filter(RedemptionCodeRow.code == "HAOAI-PENDING-0001")
                .count()
                == 0
            )
    finally:
        engine.dispose()
