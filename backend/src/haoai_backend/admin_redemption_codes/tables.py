"""Private ORM mapping for the complete legacy users and redemption_codes tables."""

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
    Text,
)
from sqlalchemy.orm import declarative_base, relationship

metadata = MetaData()
Base = declarative_base(metadata=metadata)


class UserRow(Base):
    """All eleven fixed legacy User columns needed by both relationships."""

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


class RedemptionCodeRow(Base):
    """Complete fixed legacy RedemptionCode mapping and its original relationships."""

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
    used_by = Column(
        String(36),
        ForeignKey("users.id"),
        nullable=True,
        index=True,
    )
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
