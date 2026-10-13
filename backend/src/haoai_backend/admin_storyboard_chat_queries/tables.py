"""Private legacy-compatible ORM mappings for storyboard and chat reads."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, MetaData, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class _Base(DeclarativeBase):
    metadata = MetaData()


class SeriesRow(_Base):
    __tablename__ = "series"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, index=True)
    storyboard_assets: Mapped[list[StoryboardAssetRow]] = relationship(
        back_populates="series"
    )


class ChapterRow(_Base):
    __tablename__ = "chapters"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, index=True)
    chat_messages: Mapped[list[ChatMessageRow]] = relationship(
        back_populates="chapter"
    )


class StoryboardAssetRow(_Base):
    __tablename__ = "storyboard_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, index=True)
    series_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("series.id"), nullable=False, index=True
    )
    chapter_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("chapters.id"), nullable=False, index=True
    )
    frame_index: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    series: Mapped[SeriesRow] = relationship(back_populates="storyboard_assets")


class ChatMessageRow(_Base):
    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, index=True)
    chapter_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("chapters.id"), nullable=False, index=True
    )
    frame_index: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    asset_type: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    chat_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, index=True, default="chat"
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    model_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    chapter: Mapped[ChapterRow] = relationship(back_populates="chat_messages")


metadata = _Base.metadata
registry = _Base.registry
