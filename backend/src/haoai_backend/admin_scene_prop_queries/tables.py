"""Private ORM mappings for the legacy series, scenes, and props tables."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class SeriesRow(Base):
    """Minimal related mapping required by the legacy asset relationship."""

    __tablename__ = "series"

    id = Column(String(36), primary_key=True, index=True)

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


class SceneRow(Base):
    """Private mapping containing the complete legacy scenes table."""

    __tablename__ = "scenes"

    aliases = Column(Text, nullable=True, comment="别名集合（JSON 数组字符串）")
    canonical_key = Column(
        String(255),
        nullable=True,
        index=True,
        comment="规范名键，用于确定性复用匹配",
    )
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


class PropRow(Base):
    """Private mapping containing the complete legacy props table."""

    __tablename__ = "props"

    aliases = Column(Text, nullable=True, comment="别名集合（JSON 数组字符串）")
    canonical_key = Column(
        String(255),
        nullable=True,
        index=True,
        comment="规范名键，用于确定性复用匹配",
    )
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
