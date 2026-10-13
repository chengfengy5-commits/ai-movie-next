"""Private declarative ORM model matching legacy users and model_configs."""

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
    """All fixed legacy user columns required by the model-config relationship."""

    __tablename__ = "users"

    id = Column(String(36), primary_key=True, index=True)
    username = Column(String(100), unique=True, index=True, nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    is_superuser = Column(Boolean, default=False, nullable=False)
    membership_type = Column(
        String(20), default="free", nullable=False, comment="free=普通用户 premium=会员"
    )
    membership_expires_at = Column(
        DateTime, nullable=True, comment="会员过期时间，null=永久有效"
    )
    avatar_url = Column(
        String(500), nullable=True, comment="用户头像URL，为空时前端显示默认头像"
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
    """Private mapping of the fixed legacy ModelConfig model."""

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
    params = Column(Text, nullable=True, comment="模型参数配置JSON，如尺寸/分辨率/时长选项")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    user = relationship("UserRow", backref="model_configs")
