from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.redemption_codes import application
from haoai_backend.redemption_codes.domain import (
    GenerateRedemptionCodesCommand,
    RedemptionCodesActor,
    RedeemRedemptionCodeCommand,
)
from haoai_backend.redemption_codes.persistence import (
    Base,
    CreditLogRow,
    RedemptionCodeRow,
    UserCreditRow,
    UserRow,
    billing_units_reference,
    metadata,
    redemption_codes_unit_of_work_factory,
)

NOW = datetime(2026, 10, 13, 12, 0)
ADMIN = RedemptionCodesActor("admin", True, "free", None)
USER = RedemptionCodesActor("user", False, "free", None)


class CountingSession(Session):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.commit_count = 0
        self.rollback_count = 0
        self.refresh_count = 0
        self.closed = False

    def commit(self):
        self.commit_count += 1
        return super().commit()

    def rollback(self):
        self.rollback_count += 1
        return super().rollback()

    def refresh(self, instance, *args, **kwargs):
        self.refresh_count += 1
        return super().refresh(instance, *args, **kwargs)

    def close(self):
        self.closed = True
        return super().close()


def build_database(path: Path, *, autoflush: bool = False, expire_on_commit: bool = True):
    engine = create_engine(f"sqlite:///{path}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    # The billing table belongs to another module; only its referenced key is needed here.
    billing_units_reference.create(engine, checkfirst=True)
    owned_tables = [
        UserRow.__table__,
        UserCreditRow.__table__,
        CreditLogRow.__table__,
        RedemptionCodeRow.__table__,
    ]
    Base.metadata.create_all(engine, tables=owned_tables)
    maker = sessionmaker(
        engine,
        class_=CountingSession,
        autoflush=autoflush,
        expire_on_commit=expire_on_commit,
        future=True,
    )
    sessions: list[CountingSession] = []

    def session_factory():
        session = maker()
        sessions.append(session)
        return session

    return engine, redemption_codes_unit_of_work_factory(session_factory), sessions


def insert_user(connection, user_id: str, *, is_superuser: bool = False):
    connection.execute(
        insert(UserRow.__table__).values(
            id=user_id,
            username=user_id,
            email=f"{user_id}@example.test",
            hashed_password="unused",
            is_superuser=is_superuser,
            membership_type="free",
            membership_expires_at=None,
            avatar_url=None,
            bio=None,
            created_at=NOW,
            password_updated_at=None,
        )
    )


def insert_code(
    connection,
    *,
    code: str,
    code_type: str,
    value: int,
    created_by: str = "admin",
    expires_at: datetime | None = None,
):
    connection.execute(
        insert(RedemptionCodeRow.__table__).values(
            id=f"id-{code}",
            code=code,
            type=code_type,
            value=value,
            is_used=False,
            used_by=None,
            used_at=None,
            created_by=created_by,
            expires_at=expires_at,
            created_at=NOW,
        )
    )


def test_private_mapping_keeps_complete_columns_constraints_fks_and_user_backrefs():
    assert [column.name for column in UserRow.__table__.columns] == [
        "id", "username", "email", "hashed_password", "is_superuser",
        "membership_type", "membership_expires_at", "avatar_url", "bio",
        "created_at", "password_updated_at",
    ]
    assert [column.name for column in UserCreditRow.__table__.columns] == [
        "id", "user_id", "credits", "created_at", "updated_at",
    ]
    assert [column.name for column in CreditLogRow.__table__.columns] == [
        "id", "user_id", "amount", "balance_after", "type", "description",
        "task_id", "business_key", "billing_unit_id", "related_debit_id", "created_at",
    ]
    assert [column.name for column in RedemptionCodeRow.__table__.columns] == [
        "id", "code", "type", "value", "is_used", "used_by", "used_at",
        "created_by", "expires_at", "created_at",
    ]
    assert len(UserRow.__table__.columns) == 11
    assert len(UserCreditRow.__table__.columns) == 5
    assert len(CreditLogRow.__table__.columns) == 11
    assert len(RedemptionCodeRow.__table__.columns) == 10
    assert UserCreditRow.user.property.backref == "credit"
    assert CreditLogRow.user.property.backref == "credit_logs"
    assert RedemptionCodeRow.creator.property.backref == "created_redemption_codes"
    assert RedemptionCodeRow.user.property.backref == "redeemed_redemption_codes"
    assert CreditLogRow.__table__.constraints
    assert any(
        constraint.name == "uq_credit_logs_business_key"
        for constraint in CreditLogRow.__table__.constraints
    )
    assert billing_units_reference.info == {"external_reference_only": True}
    assert {
        foreign_key.target_fullname
        for foreign_key in CreditLogRow.billing_unit_id.foreign_keys
    } == {"billing_units.id"}
    assert {
        foreign_key.target_fullname
        for foreign_key in CreditLogRow.related_debit_id.foreign_keys
    } == {"credit_logs.id"}
    assert metadata is Base.metadata


def test_generate_collision_matches_sqlite_naive_datetime_round_trip(tmp_path: Path):
    engine, factory, sessions = build_database(tmp_path / "generate.sqlite")
    try:
        with engine.begin() as connection:
            insert_user(connection, "admin", is_superuser=True)
            insert_code(connection, code="HAOAI-TAKEN-0001", code_type="credits", value=1)

        generated = iter(["HAOAI-TAKEN-0001", "HAOAI-FRESH-0001"])
        response = application.generate_redemption_codes(
            factory,
            ADMIN,
            GenerateRedemptionCodesCommand(
                "credits",
                9,
                1,
                "2029-02-03T04:05:06+05:00",
            ),
            code_generator=lambda: next(generated),
        )

        assert response["count"] == 1
        assert response["codes"][0]["code"] == "HAOAI-FRESH-0001"
        assert response["codes"][0]["expires_at"] == "2029-02-03T04:05:06Z"
        assert sessions[0].autoflush is False
        assert sessions[0].commit_count == 1 and sessions[0].refresh_count == 1
        assert sessions[0].rollback_count == 0 and sessions[0].closed
        with engine.connect() as connection:
            raw_expiry = connection.exec_driver_sql(
                "SELECT expires_at FROM redemption_codes WHERE code = ?",
                ("HAOAI-FRESH-0001",),
            ).scalar_one()
        assert raw_expiry == "2029-02-03 04:05:06.000000"

        with Session(engine) as verify:
            row = verify.query(RedemptionCodeRow).filter_by(code="HAOAI-FRESH-0001").one()
            assert row.created_by == "admin" and row.is_used is False
            assert row.expires_at == datetime(2029, 2, 3, 4, 5, 6)
            assert row.expires_at.tzinfo is None
    finally:
        engine.dispose()


def test_generate_with_autoflush_false_keeps_pending_duplicate_constraint_failure(tmp_path: Path):
    engine, factory, sessions = build_database(
        tmp_path / "pending-duplicate.sqlite",
        autoflush=False,
        expire_on_commit=True,
    )
    try:
        with engine.begin() as connection:
            insert_user(connection, "admin", is_superuser=True)

        with pytest.raises(IntegrityError):
            application.generate_redemption_codes(
                factory,
                ADMIN,
                GenerateRedemptionCodesCommand("credits", 1, 2, None),
                code_generator=lambda: "HAOAI-PENDING-0001",
            )

        assert sessions[0].autoflush is False
        assert sessions[0].commit_count == 1
        assert sessions[0].rollback_count >= 1
        assert sessions[0].refresh_count == 0 and sessions[0].closed
        with Session(engine) as verify:
            assert verify.query(RedemptionCodeRow).count() == 0
    finally:
        engine.dispose()


def test_credit_redemption_keeps_identity_and_reads_expired_wallet_after_commit(tmp_path: Path):
    engine, factory, sessions = build_database(
        tmp_path / "credit-expire.sqlite",
        autoflush=False,
        expire_on_commit=True,
    )
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    try:
        with engine.begin() as connection:
            insert_user(connection, "admin", is_superuser=True)
            insert_user(connection, "user")
            insert_code(connection, code="HAOAI-CREDITS-0001", code_type="credits", value=8)
            connection.execute(
                insert(UserCreditRow.__table__).values(
                    id="credit-user",
                    user_id="user",
                    credits=13,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )

        result = application.redeem_redemption_code(
            factory,
            USER,
            RedeemRedemptionCodeCommand(" haoai-credits-0001 "),
            clock=lambda: NOW,
        )

        assert result["detail"] == "当前积分余额：21"
        assert sessions[0].autoflush is False
        assert sessions[0].commit_count == 1 and sessions[0].refresh_count == 0
        assert sessions[0].rollback_count == 0 and sessions[0].closed
        select_statements = [
            statement for statement in statements
            if statement.lstrip().upper().startswith("SELECT")
        ]
        assert len(select_statements) == 3
        assert "FROM redemption_codes" in select_statements[0]
        assert "FROM user_credits" in select_statements[1]
        assert "FROM user_credits" in select_statements[2]
        with Session(engine) as verify:
            credit = verify.query(UserCreditRow).filter_by(user_id="user").one()
            code = verify.query(RedemptionCodeRow).filter_by(code="HAOAI-CREDITS-0001").one()
            logs = verify.query(CreditLogRow).filter_by(user_id="user").all()
            assert credit.credits == 21
            assert code.is_used is True and code.used_by == "user"
            assert len(logs) == 1
            assert (logs[0].amount, logs[0].balance_after, logs[0].type) == (8, 21, "charge")
            assert logs[0].description == "兑换码兑换：HAOAI-CREDITS-0001"
            assert logs[0].business_key is None
            assert logs[0].billing_unit_id is None and logs[0].related_debit_id is None
    finally:
        event.remove(engine, "before_cursor_execute", capture)
        engine.dispose()


def test_missing_wallet_flushes_with_code_before_log_and_keeps_fk_valid(tmp_path: Path):
    engine, factory, sessions = build_database(
        tmp_path / "new-wallet.sqlite",
        autoflush=False,
        expire_on_commit=True,
    )
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    try:
        with engine.begin() as connection:
            insert_user(connection, "admin", is_superuser=True)
            insert_user(connection, "user")
            insert_code(connection, code="HAOAI-NEW-WALLET-0001", code_type="credits", value=6)

        result = application.redeem_redemption_code(
            factory,
            USER,
            RedeemRedemptionCodeCommand("HAOAI-NEW-WALLET-0001"),
            clock=lambda: NOW,
        )

        assert result["detail"] == "当前积分余额：6"
        assert sessions[0].commit_count == 1 and sessions[0].refresh_count == 0
        assert sessions[0].closed
        dml = [
            statement
            for statement in statements
            if statement.lstrip().upper().startswith(("UPDATE", "INSERT"))
        ]
        assert any(
            "user_credits" in statement
            and statement.lstrip().upper().startswith("INSERT")
            for statement in dml
        )
        assert any(
            "credit_logs" in statement
            and statement.lstrip().upper().startswith("INSERT")
            for statement in dml
        )
        with Session(engine) as verify:
            assert verify.query(UserCreditRow).filter_by(user_id="user").one().credits == 6
            assert verify.query(CreditLogRow).filter_by(user_id="user").one().balance_after == 6
    finally:
        event.remove(engine, "before_cursor_execute", capture)
        engine.dispose()
