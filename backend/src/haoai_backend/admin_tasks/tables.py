from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
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
mapper_registry = Base.registry


class AdminTaskUser(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True)


class AdminTask(Base):
    """AI 任务记录 - 跟踪 AI 处理状态"""

    __tablename__ = "ai_tasks"
    __table_args__ = (
        CheckConstraint(
            "billing_status IN ('legacy_unreconciled', 'unbilled', 'charged', "
            "'partially_refunded', 'refunded', 'no_charge')",
            name="ck_ai_tasks_billing_status",
        ),
    )

    id = Column(String(36), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    type = Column(String(20), nullable=False)
    message_id = Column(String(36), nullable=False)
    status = Column(String(20), nullable=False)
    credit_cost = Column(Integer, nullable=False)
    result = Column(Text, nullable=True)
    request_data = Column(Text, nullable=True)
    model_name = Column(String(255), nullable=True)
    progress = Column(Integer, nullable=False)
    progress_message = Column(Text, nullable=True)
    external_task_id = Column(String(255), nullable=True)
    external_provider = Column(String(50), nullable=True)
    claimed_by = Column(String(64), nullable=True)
    lease_until = Column(DateTime, nullable=True)
    execution_generation = Column(Integer, nullable=False)
    claim_token = Column(String(64), nullable=True)
    recovery_status = Column(String(32), nullable=False)
    billing_status = Column(String(32), nullable=False)
    user_cancelled_at = Column(DateTime(timezone=True), nullable=True)
    cancellation_reason = Column(String(64), nullable=True)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)

    user = relationship(AdminTaskUser)


users = AdminTaskUser.__table__
ai_tasks = AdminTask.__table__
