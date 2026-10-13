"""Private declarative ORM mapping matching the legacy purchase_packages table."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Integer, MetaData, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class _Base(DeclarativeBase):
    metadata = MetaData()


class PurchasePackageRow(_Base):
    __tablename__ = "purchase_packages"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, index=True, default=lambda: str(uuid4())
    )
    category: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
        comment="credits=积分 / membership=会员",
    )
    name: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="套餐名称（如：1000积分、月卡）"
    )
    price: Mapped[int] = mapped_column(Integer, nullable=False, comment="价格（元）")
    credits: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="积分数额（仅积分套餐使用）")
    days: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="会员天数（仅会员套餐使用）")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="排序")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, comment="是否启用")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
