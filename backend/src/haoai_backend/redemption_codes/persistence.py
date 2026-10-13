"""Private SQLAlchemy mappings and persistence adapter for legacy tables."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    update,
)
from sqlalchemy.orm import Session, declarative_base, relationship
from sqlalchemy.orm.exc import StaleDataError

from .domain import (
    NewCreditLog,
    NewRedemptionCode,
    NewUserCredit,
    RedemptionCode,
    UserCredit,
)
from .ports import RedemptionCodesUnitOfWork

metadata = MetaData()

# Preserve the foreign key without claiming ownership of the billing table.
billing_units_reference = Table(
    "billing_units",
    metadata,
    Column("id", String(36), primary_key=True),
    info={"external_reference_only": True},
)

Base = declarative_base(metadata=metadata)


class UserRow(Base):
    """Complete fixed legacy users mapping, including relationship targets."""

    __tablename__ = "users"

    id = Column(String(36), primary_key=True, index=True)
    username = Column(String(100), unique=True, index=True, nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    is_superuser = Column(Boolean, default=False, nullable=False)
    membership_type = Column(
        String(20),
        default="free",
        nullable=False,
        comment="free=普通用户 premium=会员",
    )
    membership_expires_at = Column(
        DateTime,
        nullable=True,
        comment="会员过期时间，null=永久有效",
    )
    avatar_url = Column(
        String(500),
        nullable=True,
        comment="用户头像URL，为空时前端显示默认头像",
    )
    bio = Column(Text, nullable=True, comment="个人简介，用于用户信息卡展示")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    password_updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        nullable=True,
        comment="密码最后修改时间，旧会话需要在此时间之后重新登录",
    )


class UserCreditRow(Base):
    """Complete fixed legacy user_credits mapping."""

    __tablename__ = "user_credits"

    id = Column(String(36), primary_key=True, index=True)
    user_id = Column(
        String(36),
        ForeignKey("users.id"),
        unique=True,
        nullable=False,
        index=True,
    )
    credits = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    user = relationship("UserRow", backref="credit")


class CreditLogRow(Base):
    """Complete fixed legacy credit_logs mapping and constraints."""

    __tablename__ = "credit_logs"
    __table_args__ = (
        UniqueConstraint("business_key", name="uq_credit_logs_business_key"),
    )

    id = Column(String(36), primary_key=True, index=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    amount = Column(Integer, nullable=False, comment="正数=收入，负数=支出")
    balance_after = Column(Integer, nullable=False, comment="变动后余额")
    type = Column(
        String(20),
        nullable=False,
        index=True,
        comment="charge(充值) / usage(消耗) / refund(退款)",
    )
    description = Column(String(500), nullable=True)
    task_id = Column(String(36), nullable=True, index=True, comment="关联 AI 任务")
    business_key = Column(String(255), nullable=True)
    billing_unit_id = Column(
        String(36),
        ForeignKey(
            "billing_units.id",
            name="fk_credit_logs_billing_unit_id_billing_units",
        ),
        nullable=True,
        index=True,
    )
    related_debit_id = Column(
        String(36),
        ForeignKey(
            "credit_logs.id",
            name="fk_credit_logs_related_debit_id_credit_logs",
        ),
        nullable=True,
        index=True,
    )
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("UserRow", backref="credit_logs")


class RedemptionCodeRow(Base):
    """Complete fixed legacy redemption_codes mapping and relationships."""

    __tablename__ = "redemption_codes"

    id = Column(String(36), primary_key=True, index=True)
    code = Column(
        String(32),
        unique=True,
        nullable=False,
        index=True,
        comment="兑换码字符串",
    )
    type = Column(
        String(20),
        nullable=False,
        comment="membership=会员时长, credits=积分",
    )
    value = Column(Integer, nullable=False, comment="会员天数 or 积分数额")
    is_used = Column(Boolean, default=False, nullable=False)
    used_by = Column(String(36), ForeignKey("users.id"), nullable=True, index=True)
    used_at = Column(DateTime, nullable=True)
    created_by = Column(String(36), ForeignKey("users.id"), nullable=False)
    expires_at = Column(
        DateTime,
        nullable=True,
        comment="过期时间，null=永不过期",
    )
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    creator = relationship(
        "UserRow",
        foreign_keys=[created_by],
        backref="created_redemption_codes",
    )
    user = relationship(
        "UserRow",
        foreign_keys=[used_by],
        backref="redeemed_redemption_codes",
    )


def _redemption_code(row: RedemptionCodeRow) -> RedemptionCode:
    return RedemptionCode(
        id=row.id,
        code=row.code,
        type=row.type,
        value=row.value,
        is_used=row.is_used,
        used_by=row.used_by,
        used_at=row.used_at,
        created_by=row.created_by,
        expires_at=row.expires_at,
        created_at=row.created_at,
    )


class SqlAlchemyRedemptionCodesUnitOfWork(RedemptionCodesUnitOfWork):
    def __init__(self, session: Session) -> None:
        self._session = session
        self._generated_rows: dict[str, RedemptionCodeRow] = {}
        self._loaded_codes: dict[str, RedemptionCodeRow] = {}
        self._credits_by_user_id: dict[str, UserCreditRow] = {}

    def code_exists(self, code: str) -> bool:
        return (
            self._session.query(RedemptionCodeRow)
            .filter(RedemptionCodeRow.code == code)
            .first()
            is not None
        )

    def add_generated_code(self, code: NewRedemptionCode) -> None:
        row = RedemptionCodeRow(
            id=code.id,
            code=code.code,
            type=code.type,
            value=code.value,
            is_used=False,
            created_by=code.created_by,
            expires_at=code.expires_at,
        )
        self._generated_rows[code.id] = row
        self._session.add(row)

    def refresh_generated_code(self, code_id: str) -> RedemptionCode | None:
        row = self._generated_rows.get(code_id)
        if row is None:
            return None
        self._session.refresh(row)
        return _redemption_code(row)

    def load_code(self, code: str) -> RedemptionCode | None:
        row = (
            self._session.query(RedemptionCodeRow)
            .filter(RedemptionCodeRow.code == code)
            .first()
        )
        if row is None:
            return None
        self._loaded_codes[row.id] = row
        return _redemption_code(row)

    def mark_code_used(
        self,
        code_id: str,
        *,
        user_id: str,
        used_at: datetime,
    ) -> None:
        row = self._loaded_codes[code_id]
        row.is_used = True
        row.used_by = user_id
        row.used_at = used_at

    def set_membership(
        self,
        user_id: str,
        *,
        membership_type: str,
        membership_expires_at: datetime,
    ) -> None:
        result = self._session.execute(
            update(UserRow)
            .where(UserRow.id == user_id)
            .values(
                membership_type=membership_type,
                membership_expires_at=membership_expires_at,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise StaleDataError("redemption user changed during membership update")

    def load_user_credit(self, user_id: str) -> UserCredit | None:
        row = (
            self._session.query(UserCreditRow)
            .filter(UserCreditRow.user_id == user_id)
            .first()
        )
        if row is None:
            return None
        self._credits_by_user_id[user_id] = row
        return UserCredit(id=row.id, user_id=row.user_id, credits=row.credits)

    def add_user_credit(self, credit: NewUserCredit) -> None:
        row = UserCreditRow(
            id=credit.id,
            user_id=credit.user_id,
            credits=credit.credits,
        )
        self._credits_by_user_id[credit.user_id] = row
        self._session.add(row)

    def flush(self) -> None:
        self._session.flush()

    def set_user_credit_balance(self, user_id: str, credits: int) -> None:
        self._credits_by_user_id[user_id].credits = credits

    def add_credit_log(self, log: NewCreditLog) -> None:
        self._session.add(
            CreditLogRow(
                id=log.id,
                user_id=log.user_id,
                amount=log.amount,
                balance_after=log.balance_after,
                type=log.type,
                description=log.description,
            )
        )

    def user_credit_balance(self, user_id: str) -> int:
        # Read the same ORM identity after commit so expire_on_commit remains observable.
        return self._credits_by_user_id[user_id].credits

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()

    def close(self) -> None:
        self._session.close()


def redemption_codes_unit_of_work_factory(
    session_factory: Callable[[], Session],
) -> Callable[[], SqlAlchemyRedemptionCodesUnitOfWork]:
    def create() -> SqlAlchemyRedemptionCodesUnitOfWork:
        return SqlAlchemyRedemptionCodesUnitOfWork(session_factory())

    return create
