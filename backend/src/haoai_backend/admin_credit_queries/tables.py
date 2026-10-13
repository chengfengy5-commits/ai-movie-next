"""Private ORM mappings for the legacy user credit and credit log tables."""

from __future__ import annotations

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
)
from sqlalchemy.orm import declarative_base, relationship

metadata = MetaData()

# This table resolves the legacy foreign key only. It is not an ORM mapping and
# is excluded from module-owned schema creation and query paths.
billing_units_reference = Table(
    "billing_units",
    metadata,
    Column("id", String(36), primary_key=True),
    info={"external_reference_only": True},
)

Base = declarative_base(metadata=metadata)


class UserRow(Base):
    """Complete private projection of the fixed legacy users row."""

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
    """Complete private projection of the fixed legacy user_credits row."""

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
    """Complete private projection of the fixed legacy credit_logs row."""

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
    description = Column(String(500), nullable=True, comment="变动说明")
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
