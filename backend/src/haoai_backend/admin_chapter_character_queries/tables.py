"""Private SQLAlchemy mappings for the legacy read-only tables."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, MetaData, String, Text
from sqlalchemy.orm import declarative_base, relationship


metadata = MetaData()
Base = declarative_base(metadata=metadata)


class SeriesRow(Base):
    __tablename__ = "series"

    id = Column(String(36), primary_key=True, index=True)

    chapters = relationship("ChapterRow", back_populates="series")
    characters = relationship("CharacterRow", back_populates="series")


class ChapterRow(Base):
    __tablename__ = "chapters"

    id = Column(String(36), primary_key=True, index=True)
    series_id = Column(String(36), ForeignKey("series.id"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=True)
    order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    series = relationship("SeriesRow", back_populates="chapters")
    chat_messages = relationship(
        "ChatMessageRow",
        back_populates="chapter",
        cascade="all, delete-orphan",
    )


class ChatMessageRow(Base):
    __tablename__ = "chat_messages"

    id = Column(String(36), primary_key=True, index=True)
    chapter_id = Column(
        String(36),
        ForeignKey("chapters.id"),
        nullable=False,
        index=True,
    )

    chapter = relationship("ChapterRow", back_populates="chat_messages")


class AssetNamingMixin:
    aliases = Column(Text, nullable=True)
    canonical_key = Column(String(255), nullable=True, index=True)


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
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    series = relationship("SeriesRow", back_populates="characters")
