from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, insert
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.admin_redemption_codes import application
from haoai_backend.admin_redemption_codes.domain import AdminRedemptionCodesActor
from haoai_backend.admin_redemption_codes.errors import AdminRedemptionCodeBadRequest
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
        connection.execute(
            insert(RedemptionCodeRow.__table__).values(
                id="existing-id",
                code="HAOAI-EXIST-0001",
                type="credits",
                value=1,
                is_used=False,
                used_by=None,
                used_at=None,
                created_by="admin",
                expires_at=None,
                created_at=STAMP,
            )
        )
    return engine, admin_redemption_codes_uow_factory(session_factory), sessions


def test_create_preserves_legacy_coercion_identity_collision_and_first_refresh(tmp_path: Path):
    engine, factory, sessions = build_database(tmp_path / "application.sqlite")
    generated = iter(
        ["HAOAI-EXIST-0001", "HAOAI-AAAA-0001", "HAOAI-BBBB-0002"]
    )
    try:
        payload = application.create_redemption_codes(
            factory,
            ADMIN,
            {
                "id": "caller-id",
                "code": "caller-code",
                "type": "credits",
                "value": "7",
                "count": "2",
                "is_used": True,
                "used_by": "attacker",
                "created_by": "attacker",
                "expires_at": "2031-01-02T03:04:05Z",
            },
            code_generator=lambda: next(generated),
        )

        assert set(payload) == {
            "id",
            "code",
            "type",
            "value",
            "is_used",
            "used_by",
            "used_at",
            "created_by",
            "expires_at",
            "created_at",
            "_generated_count",
        }
        assert payload["id"] != "caller-id"
        assert payload["code"] == "HAOAI-AAAA-0001"
        assert payload["type"] == "credits" and payload["value"] == 7
        assert payload["is_used"] is False
        assert payload["used_by"] is None and payload["used_at"] is None
        assert payload["created_by"] == "admin"
        assert payload["expires_at"] == "2031-01-02T03:04:05Z"
        assert payload["_generated_count"] == 2

        assert sessions[0].commit_attempts == 1
        assert sessions[0].refresh_attempts == 1
        assert sessions[0].closed
        with Session(engine) as verify:
            records = (
                verify.query(RedemptionCodeRow)
                .filter(RedemptionCodeRow.code.like("HAOAI-%"))
                .order_by(RedemptionCodeRow.code)
                .all()
            )
        assert [record.code for record in records] == [
            "HAOAI-AAAA-0001",
            "HAOAI-BBBB-0002",
            "HAOAI-EXIST-0001",
        ]
        assert all(record.created_by == "admin" for record in records)
    finally:
        engine.dispose()


def test_validation_and_primary_failure_cleanup_keep_the_original_exception(tmp_path: Path):
    engine, factory, sessions = build_database(tmp_path / "validation.sqlite")
    try:
        with pytest.raises(AdminRedemptionCodeBadRequest) as invalid_type:
            application.create_redemption_codes(
                factory, ADMIN, {"type": None, "value": 1}
            )
        assert invalid_type.value.detail == "type 必须是 membership 或 credits"
        assert sessions == []

        with pytest.raises(TypeError):
            application.create_redemption_codes(
                factory, ADMIN, {"type": "credits", "value": None}
            )
        assert sessions == []
    finally:
        engine.dispose()

    original_error = OSError("commit acknowledgement lost")

    class FailingUnitOfWork:
        rollback_attempts = 0
        close_attempts = 0

        def code_exists(self, code):
            return False

        def add_code(self, code):
            self.added = code

        def commit(self):
            raise original_error

        def rollback(self):
            self.rollback_attempts += 1
            raise RuntimeError("rollback also failed")

        def close(self):
            self.close_attempts += 1
            raise RuntimeError("close also failed")

    failing = FailingUnitOfWork()
    with pytest.raises(OSError) as raised:
        application.create_redemption_codes(
            lambda: failing,
            ADMIN,
            {"type": "credits", "value": 1},
            code_generator=lambda: "HAOAI-FAIL-0001",
        )
    assert raised.value is original_error
    assert failing.rollback_attempts == failing.close_attempts == 1
