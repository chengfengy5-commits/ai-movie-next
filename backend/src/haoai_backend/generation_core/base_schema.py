"""Explicit mappings for the legacy tables required by generation records.

These mappings support caller-owned reads and writes and isolated test fixtures.
They are not a production bootstrap schema; explicit migrations create only the
new generation records after the existing tables pass structural preflight.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    JSON,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import declarative_base


Base = declarative_base(metadata=MetaData())


class UserRow(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True)


class AITaskRow(Base):
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
    status = Column(String(20), nullable=False)
    claimed_by = Column(String(64), nullable=True)
    lease_until = Column(DateTime, nullable=True)
    execution_generation = Column(Integer, nullable=False, default=0, server_default="0")
    claim_token = Column(String(64), nullable=True)
    recovery_status = Column(String(32), nullable=False, default="ready", server_default="ready")
    billing_status = Column(String(32), nullable=False, default="unbilled", server_default="unbilled")
    user_cancelled_at = Column(DateTime(timezone=True), nullable=True)
    cancellation_reason = Column(String(64), nullable=True)
    message_id = Column(String(36), nullable=False, default="")
    credit_cost = Column(Integer, nullable=False, default=0, server_default="0")
    result = Column(Text, nullable=True)
    request_data = Column(Text, nullable=True)
    model_name = Column(String(255), nullable=True)
    progress = Column(Integer, nullable=False, default=0, server_default="0")
    progress_message = Column(Text, nullable=True)
    external_task_id = Column(String(255), nullable=True)
    external_provider = Column(String(50), nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class TaskQuoteRow(Base):
    __tablename__ = "task_quotes"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "operation", "idempotency_key",
            name="uq_task_quotes_user_operation_key",
        ),
        UniqueConstraint(
            "user_id", "confirmation_key",
            name="uq_task_quotes_user_confirmation_key",
        ),
        CheckConstraint("unit_price >= 0", name="ck_task_quotes_unit_price_nonnegative"),
        CheckConstraint("unit_count >= 0", name="ck_task_quotes_unit_count_nonnegative"),
        CheckConstraint("total_amount >= 0", name="ck_task_quotes_total_nonnegative"),
        CheckConstraint(
            "total_amount = unit_price * unit_count",
            name="ck_task_quotes_total_matches_units",
        ),
    )

    id = Column(String(36), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    operation = Column(String(64), nullable=False)
    idempotency_key = Column(String(255), nullable=False)
    model_source = Column(String(32), nullable=False, default="system")
    model_ref = Column(String(255), nullable=True)
    model_name = Column(String(255), nullable=True)
    unit_price = Column(Integer, nullable=False)
    unit_count = Column(Integer, nullable=False)
    total_amount = Column(Integer, nullable=False)
    input_digest = Column(String(128), nullable=False)
    candidate_digest = Column(String(128), nullable=False)
    unit_manifest = Column(JSON, nullable=False, default=list)
    status = Column(String(24), nullable=False, default="quoted", server_default="quoted")
    confirmation_key = Column(String(255), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    consumed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class TaskSubmissionRow(Base):
    __tablename__ = "task_submissions"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "operation", "idempotency_key",
            name="uq_task_submissions_user_operation_key",
        ),
        UniqueConstraint("task_quote_id", name="uq_task_submissions_task_quote_id"),
        CheckConstraint("length(operation) > 0", name="ck_task_submissions_operation_nonempty"),
    )

    id = Column(String(36), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    operation = Column(String(64), nullable=False)
    idempotency_key = Column(String(255), nullable=False)
    request_digest = Column(String(128), nullable=False)
    task_ids = Column(JSON, nullable=False, default=list)
    task_quote_id = Column(String(36), ForeignKey("task_quotes.id"), nullable=True)
    status = Column(String(24), nullable=False, default="accepted", server_default="accepted")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class BillingUnitRow(Base):
    __tablename__ = "billing_units"
    __table_args__ = (
        UniqueConstraint("task_id", "item_key", name="uq_billing_units_task_item"),
        UniqueConstraint("quote_id", "quote_unit_key", name="uq_billing_units_quote_item"),
        CheckConstraint("quoted_amount >= 0", name="ck_billing_units_quoted_nonnegative"),
        CheckConstraint("debited_amount >= 0", name="ck_billing_units_debited_nonnegative"),
        CheckConstraint("refunded_amount >= 0", name="ck_billing_units_refunded_nonnegative"),
        CheckConstraint(
            "refunded_amount <= debited_amount",
            name="ck_billing_units_refund_not_over_debit",
        ),
        CheckConstraint(
            "billing_status IN ('unbilled', 'charged', 'refunded', 'no_charge')",
            name="ck_billing_units_billing_status",
        ),
        CheckConstraint(
            "result_status IN ('pending', 'partial', 'succeeded', 'failed', 'unknown')",
            name="ck_billing_units_result_status",
        ),
    )

    id = Column(String(36), primary_key=True)
    task_id = Column(String(36), ForeignKey("ai_tasks.id"), nullable=False)
    item_key = Column(String(255), nullable=False)
    operation = Column(String(64), nullable=False)
    step_graph_version = Column(String(64), nullable=False)
    input_digest = Column(String(128), nullable=False)
    quoted_amount = Column(Integer, nullable=False)
    debited_amount = Column(Integer, nullable=False, default=0, server_default="0")
    refunded_amount = Column(Integer, nullable=False, default=0, server_default="0")
    billing_status = Column(String(32), nullable=False, default="unbilled", server_default="unbilled")
    result_status = Column(String(32), nullable=False, default="pending", server_default="pending")
    first_intent_at = Column(DateTime(timezone=True), nullable=True)
    deadline_at = Column(DateTime(timezone=True), nullable=True)
    success_confirmed_at = Column(DateTime(timezone=True), nullable=True)
    user_cancelled_at = Column(DateTime(timezone=True), nullable=True)
    cancellation_reason = Column(String(64), nullable=True)
    quote_id = Column(String(36), ForeignKey("task_quotes.id"), nullable=True)
    quote_unit_key = Column(String(255), nullable=True)
    candidate_digest = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class ExecutionStepRow(Base):
    __tablename__ = "execution_steps"
    __table_args__ = (
        UniqueConstraint("billing_unit_id", "step_key", name="uq_execution_steps_unit_key"),
        CheckConstraint("step_order >= 0", name="ck_execution_steps_order_nonnegative"),
        CheckConstraint(
            "execution_status IN ('prepared', 'submitting', 'submitted', 'result_observed', "
            "'applied', 'failed', 'reconciling', 'deterministic_empty')",
            name="ck_execution_steps_status",
        ),
    )

    id = Column(String(36), primary_key=True)
    billing_unit_id = Column(String(36), ForeignKey("billing_units.id"), nullable=False)
    step_key = Column(String(128), nullable=False)
    dependencies = Column(JSON, nullable=False, default=list, server_default=text("'[]'"))
    input_digest = Column(String(128), nullable=False)
    input_snapshot = Column(JSON, nullable=True)
    required = Column(Boolean, nullable=False, default=True, server_default="true")
    step_order = Column(Integer, nullable=False, default=0, server_default="0")
    execution_status = Column(String(32), nullable=False, default="prepared", server_default="prepared")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class ExternalSubmissionRow(Base):
    __tablename__ = "external_submissions"
    __table_args__ = (
        UniqueConstraint("step_id", name="uq_external_submissions_step_id"),
        UniqueConstraint("submission_key", name="uq_external_submissions_submission_key"),
    )

    id = Column(String(36), primary_key=True)
    step_id = Column(String(36), ForeignKey("execution_steps.id"), nullable=False)
    submission_key = Column(String(255), nullable=False)
    provider = Column(String(64), nullable=False)
    model_ref = Column(String(255), nullable=True)
    request_digest = Column(String(128), nullable=False)
    request_snapshot = Column(JSON, nullable=False, default=dict)
    intent_at = Column(DateTime(timezone=True), nullable=False)
    submitted_at = Column(DateTime(timezone=True), nullable=True)
    external_task_id = Column(String(255), nullable=True)
    submission_status = Column(String(32), nullable=False, default="submitting", server_default="submitting")
    deadline_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class ResultEvidenceRow(Base):
    __tablename__ = "result_evidence"
    __table_args__ = (
        UniqueConstraint("evidence_key", name="uq_result_evidence_evidence_key"),
        CheckConstraint(
            "submission_id IS NOT NULL OR evidence_kind = 'deterministic_empty'",
            name="ck_result_evidence_submission_or_empty",
        ),
        CheckConstraint(
            "validity IN ('valid', 'invalid', 'unknown')",
            name="ck_result_evidence_validity",
        ),
        CheckConstraint(
            "application_status IN ('pending', 'applied', 'rejected')",
            name="ck_result_evidence_application_status",
        ),
    )

    id = Column(String(36), primary_key=True)
    step_id = Column(String(36), ForeignKey("execution_steps.id"), nullable=False)
    submission_id = Column(String(36), ForeignKey("external_submissions.id"), nullable=True)
    evidence_key = Column(String(255), nullable=False)
    source = Column(String(64), nullable=False, default="provider", server_default="provider")
    evidence_kind = Column(String(64), nullable=False)
    payload_digest = Column(String(128), nullable=False)
    result_location = Column(Text, nullable=True)
    result_digest = Column(String(128), nullable=True)
    received_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    validity = Column(String(24), nullable=False, default="unknown", server_default="unknown")
    application_status = Column(String(24), nullable=False, default="pending", server_default="pending")
    applied_at = Column(DateTime(timezone=True), nullable=True)


class UserCreditRow(Base):
    __tablename__ = "user_credits"

    id = Column(String(36), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, unique=True)
    credits = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class CreditLogRow(Base):
    __tablename__ = "credit_logs"
    __table_args__ = (
        UniqueConstraint("business_key", name="uq_credit_logs_business_key"),
        ForeignKeyConstraint(
            ["billing_unit_id"], ["billing_units.id"],
            name="fk_credit_logs_billing_unit_id_billing_units",
        ),
        ForeignKeyConstraint(
            ["related_debit_id"], ["credit_logs.id"],
            name="fk_credit_logs_related_debit_id_credit_logs",
        ),
    )

    id = Column(String(36), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    amount = Column(Integer, nullable=False)
    balance_after = Column(Integer, nullable=False)
    type = Column(String(20), nullable=False)
    description = Column(String(500), nullable=True)
    task_id = Column(String(36), nullable=True)
    business_key = Column(String(255), nullable=True)
    billing_unit_id = Column(String(36), nullable=True)
    related_debit_id = Column(String(36), nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))


FOUNDATION_TABLE_NAMES = (
    "users",
    "ai_tasks",
    "task_submissions",
    "task_quotes",
    "billing_units",
    "execution_steps",
    "external_submissions",
    "result_evidence",
    "user_credits",
    "credit_logs",
)
FOUNDATION_TABLES = tuple(Base.metadata.tables[name] for name in FOUNDATION_TABLE_NAMES)

__all__ = (
    "Base",
    "UserRow",
    "AITaskRow",
    "TaskSubmissionRow",
    "TaskQuoteRow",
    "BillingUnitRow",
    "ExecutionStepRow",
    "ExternalSubmissionRow",
    "ResultEvidenceRow",
    "UserCreditRow",
    "CreditLogRow",
    "FOUNDATION_TABLE_NAMES",
    "FOUNDATION_TABLES",
)
