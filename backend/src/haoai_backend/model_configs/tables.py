"""Private SQLAlchemy mappings for the fixed legacy model-configuration tables."""

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
    """Private projection of all fixed legacy user columns."""

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


class ModelConfigRow(Base):
    """Private mapping of the fixed legacy personal model configuration."""

    __tablename__ = "model_configs"

    id = Column(String(36), primary_key=True, index=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    category = Column(String(20), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    provider = Column(String(50), nullable=False)
    api_key = Column(String(500), nullable=False)
    model = Column(String(255), nullable=True)
    base_url = Column(String(500), nullable=True)
    sort_order = Column(Integer, default=0, nullable=False)
    params = Column(
        Text,
        nullable=True,
        comment="模型参数配置JSON，如尺寸/分辨率/时长选项",
    )
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    user = relationship("UserRow", backref="model_configs")


class SystemModelRow(Base):
    """Private mapping of the fixed legacy administrator model configuration."""

    __tablename__ = "system_models"

    id = Column(String(36), primary_key=True, index=True)
    category = Column(String(20), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    provider = Column(String(50), nullable=False)
    api_key = Column(String(500), nullable=False)
    model = Column(String(255), nullable=True)
    base_url = Column(String(500), nullable=True)
    cost_per_call = Column(Integer, default=0, nullable=False)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    sort_order = Column(Integer, default=0, nullable=False)
    params = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class UserModelOrderRow(Base):
    """Private mapping of the fixed legacy per-user model ordering table."""

    __tablename__ = "user_model_orders"

    id = Column(String(36), primary_key=True, index=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    model_id = Column(
        String(36),
        nullable=False,
        comment="系统模型或用户模型的 ID",
    )
    category = Column(
        String(20),
        nullable=False,
        comment="text/image/video",
    )
    sort_order = Column(Integer, nullable=False, comment="排序序号，越小越靠前")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )
