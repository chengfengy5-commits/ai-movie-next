"""Private mappings for the complete legacy Series graph used by these routes."""

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
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, relationship

metadata = MetaData()
Base = declarative_base(metadata=metadata)


class UserRow(Base):
    """The complete eleven-column legacy User table used by Series.user."""

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
        comment="会员到期时间，null=永久有效",
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
        comment="密码最后修改时间",
    )


class TeamReferenceRow(Base):
    """Only the teams.id FK target is referenced by the fixed Series mapping."""

    __tablename__ = "teams"

    id = Column(String(36), primary_key=True, index=True)


class SeriesRow(Base):
    """All eleven fixed legacy Series columns and its eight direct relationships."""

    __tablename__ = "series"

    id = Column(String(36), primary_key=True, index=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(255), index=True, nullable=False)
    description = Column(Text, nullable=True)
    image_url = Column(String(500), nullable=True)
    style_prompt_id = Column(String(36), nullable=True)
    team_id = Column(
        String(36),
        ForeignKey("teams.id"),
        nullable=True,
        index=True,
        comment="所属团队，为空=个人剧集",
    )
    claimed_by = Column(
        String(36),
        nullable=True,
        index=True,
        comment="认领人（制作负责人），为空=未认领；1 人制",
    )
    claimed_at = Column(DateTime, nullable=True, comment="认领时间")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    user = relationship("UserRow", backref="series")
    chapters = relationship(
        "ChapterRow",
        back_populates="series",
        cascade="all, delete-orphan",
    )
    characters = relationship(
        "CharacterRow",
        back_populates="series",
        cascade="all, delete-orphan",
    )
    scenes = relationship(
        "SceneRow",
        back_populates="series",
        cascade="all, delete-orphan",
    )
    props = relationship(
        "PropRow",
        back_populates="series",
        cascade="all, delete-orphan",
    )
    storyboard_assets = relationship(
        "StoryboardAssetRow",
        back_populates="series",
        cascade="all, delete-orphan",
    )
    fused_assets = relationship(
        "FusedAssetRow",
        back_populates="series",
        cascade="all, delete-orphan",
    )
    duplicate_exclusions = relationship(
        "AssetDuplicateExclusionRow",
        back_populates="series",
        cascade="all, delete-orphan",
    )


class ChapterRow(Base):
    __tablename__ = "chapters"

    id = Column(String(36), primary_key=True, index=True)
    series_id = Column(String(36), ForeignKey("series.id"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=True)
    order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    series = relationship("SeriesRow", back_populates="chapters")
    chat_messages = relationship(
        "ChatMessageRow",
        back_populates="chapter",
        cascade="all, delete-orphan",
    )


class AssetNamingMixin:
    """Keep the hidden alias and canonical-key columns on legacy asset tables."""

    aliases = Column(Text, nullable=True, comment="别名集合（JSON 数组字符串）")
    canonical_key = Column(
        String(255),
        nullable=True,
        index=True,
        comment="规范名键，用于确定性复用匹配",
    )


class CharacterRow(AssetNamingMixin, Base):
    __tablename__ = "characters"

    id = Column(String(36), primary_key=True, index=True)
    series_id = Column(String(36), ForeignKey("series.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    gender = Column(String(20), nullable=True)
    age = Column(String(100), nullable=True)
    role = Column(String(50), nullable=True)
    appearance = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    image_url = Column(String(500), nullable=True)
    audio_url = Column(String(500), nullable=True)
    voice_ref = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    series = relationship("SeriesRow", back_populates="characters")


class SceneRow(AssetNamingMixin, Base):
    __tablename__ = "scenes"

    id = Column(String(36), primary_key=True, index=True)
    series_id = Column(String(36), ForeignKey("series.id"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    image_url = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    series = relationship("SeriesRow", back_populates="scenes")


class PropRow(AssetNamingMixin, Base):
    __tablename__ = "props"

    id = Column(String(36), primary_key=True, index=True)
    series_id = Column(String(36), ForeignKey("series.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    image_url = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    series = relationship("SeriesRow", back_populates="props")


class StoryboardAssetRow(Base):
    __tablename__ = "storyboard_assets"

    id = Column(String(36), primary_key=True, index=True)
    series_id = Column(String(36), ForeignKey("series.id"), nullable=False, index=True)
    chapter_id = Column(
        String(36),
        ForeignKey("chapters.id"),
        nullable=False,
        index=True,
    )
    frame_index = Column(Integer, nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    image_url = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    series = relationship("SeriesRow", back_populates="storyboard_assets")


class FusedAssetRow(Base):
    __tablename__ = "fused_assets"

    id = Column(String(36), primary_key=True, index=True)
    series_id = Column(String(36), ForeignKey("series.id"), nullable=False, index=True)
    member_type = Column(String(20), nullable=False, default="prop", index=True)
    members = Column(Text, nullable=True)
    name = Column(String(255), nullable=False)
    image_url = Column(String(500), nullable=True)
    size = Column(String(50), nullable=True)
    status = Column(String(20), nullable=False, default="completed", index=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    series = relationship("SeriesRow", back_populates="fused_assets")


class ChatMessageRow(Base):
    __tablename__ = "chat_messages"

    id = Column(String(36), primary_key=True, index=True)
    chapter_id = Column(
        String(36),
        ForeignKey("chapters.id"),
        nullable=False,
        index=True,
    )
    frame_index = Column(Integer, nullable=True, index=True)
    asset_type = Column(String(20), nullable=True, index=True)
    asset_id = Column(String(36), nullable=True, index=True)
    chat_mode = Column(String(20), nullable=False, index=True, default="chat")
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    model_name = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    chapter = relationship("ChapterRow", back_populates="chat_messages")


class AssetDuplicateExclusionRow(Base):
    __tablename__ = "asset_duplicate_exclusions"
    __table_args__ = (
        UniqueConstraint(
            "series_id",
            "asset_type",
            "asset_id_a",
            "asset_id_b",
            name="uq_asset_dup_exclusion",
        ),
    )

    id = Column(String(36), primary_key=True, index=True)
    series_id = Column(String(36), ForeignKey("series.id"), nullable=False, index=True)
    asset_type = Column(String(20), nullable=False, index=True)
    asset_id_a = Column(String(36), nullable=False, index=True)
    asset_id_b = Column(String(36), nullable=False, index=True)
    created_by = Column(String(36), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    series = relationship("SeriesRow", back_populates="duplicate_exclusions")
