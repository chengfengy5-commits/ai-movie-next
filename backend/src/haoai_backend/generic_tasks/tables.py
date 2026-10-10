"""Private ORM mappings for the legacy generic task and credit rows."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, MetaData, String, Table, Text
from sqlalchemy.orm import declarative_base

Base = declarative_base(metadata=MetaData())

# Resolve owner foreign keys without mapping or creating the full users table.
users = Table(
    "users", Base.metadata, Column("id", String(36), primary_key=True)
)


class AITask(Base):
    """The generic-task columns read or written by this module."""

    __tablename__ = "ai_tasks"

    id = Column(String(36), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    type = Column(String(20), nullable=False)
    message_id = Column(String(36), nullable=False)
    status = Column(String(20), nullable=False, default="processing")
    credit_cost = Column(Integer, nullable=False, default=0)
    result = Column(Text, nullable=True)
    request_data = Column(Text, nullable=True)
    model_name = Column(String(255), nullable=True)
    progress = Column(Integer, nullable=False, default=0)
    progress_message = Column(Text, nullable=True)
    external_task_id = Column(String(255), nullable=True)
    external_provider = Column(String(50), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class UserCredit(Base):
    """The wallet columns used by the legacy task ledger."""

    __tablename__ = "user_credits"

    id = Column(String(36), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, unique=True)
    credits = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class CreditLog(Base):
    """The generic charge/refund ledger columns."""

    __tablename__ = "credit_logs"

    id = Column(String(36), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    amount = Column(Integer, nullable=False)
    balance_after = Column(Integer, nullable=False)
    type = Column(String(20), nullable=False)
    description = Column(String(500), nullable=True)
    task_id = Column(String(36), nullable=True)
    business_key = Column(String(255), nullable=True, unique=True)
    related_debit_id = Column(String(36), ForeignKey("credit_logs.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class BillingUnit(Base):
    """Minimal lookup mapping for worker-owned billing records."""

    __tablename__ = "billing_units"

    id = Column(String(36), primary_key=True)
    task_id = Column(String(36), ForeignKey("ai_tasks.id"), nullable=False)


class TaskSubmission(Base):
    """The raw JSON task-id list used by the controlled-update guard."""

    __tablename__ = "task_submissions"

    id = Column(String(36), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    task_ids = Column(JSON, nullable=False)
