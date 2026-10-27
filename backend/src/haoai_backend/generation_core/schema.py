"""ORM declarations and structural catalog checks for generation persistence."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Protocol, Sequence

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    inspect,
    text,
)
from sqlalchemy.engine import Connection
from sqlalchemy.orm import declarative_base
from sqlalchemy.types import TypeEngine

from haoai_backend.generation_contracts.records import (
    ApplicationRecord,
    ExecutionAttemptGroup,
    GenerationResultReceipt,
    LocalContinuationWork,
    PolicyStopEvent,
    PollReservation,
    StreamFeeRecord,
    UnitFeeDecision,
)
from .check_expressions import canonical_check_expression
from .base_schema import (
    AITaskRow,
    Base as FoundationBase,
    BillingUnitRow,
    CreditLogRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
    FOUNDATION_TABLE_NAMES,
    FOUNDATION_TABLES,
    ResultEvidenceRow,
    TaskQuoteRow,
    TaskSubmissionRow,
    UserCreditRow,
    UserRow,
)


GENERATION_SCHEMA_VERSION = "0001_generation_shared_records"
GENERATION_SCHEMA_CHECKSUM = "4a5555181658fae91d035235a021d06fdbf3dde4ad0271375498fa9e57736bfa"
CANVAS_RESULT_TARGETS_SCHEMA_VERSION = "0002_canvas_result_targets"
CANVAS_RESULT_TARGETS_SCHEMA_CHECKSUM = "1d9482fc951fc4e13d9c0c3fe5c74fb3e605045a0427306d25906872cfda1f67"
FUSED_GENERATION_SUBMISSIONS_SCHEMA_VERSION = "0003_fused_generation_submissions"
FUSED_GENERATION_SUBMISSIONS_SCHEMA_CHECKSUM = "4cefd8e428911da76e89297517ed3e6cb2dc7222d873f21b4c510d3a4e7c0680"
GENERATION_CURRENT_SCHEMA_VERSION = FUSED_GENERATION_SUBMISSIONS_SCHEMA_VERSION
GENERATION_CURRENT_SCHEMA_CHECKSUM = FUSED_GENERATION_SUBMISSIONS_SCHEMA_CHECKSUM
GENERATION_MIGRATION_HISTORY_TABLE = "generation_schema_migrations"
GENERATION_SUPPORTED_VERSIONS = (
    GENERATION_SCHEMA_VERSION,
    CANVAS_RESULT_TARGETS_SCHEMA_VERSION,
    FUSED_GENERATION_SUBMISSIONS_SCHEMA_VERSION,
)
CANVAS_RESULT_TARGETS_TABLE_NAME = "generation_result_targets"
FUSED_GENERATION_SUBMISSIONS_TABLE_NAME = "fused_generation_submissions"


class UnitFeeDecisionRow(FoundationBase):
    __tablename__ = "unit_fee_decisions"

    unit_id = Column(String(36), ForeignKey("billing_units.id"), primary_key=True)
    charge_phase = Column(String(32), nullable=False)
    resolution = Column(String(32), nullable=False)
    ledger_ref = Column(String(36), ForeignKey("credit_logs.id"), nullable=True)
    generation_result_ref = Column(
        String(36), ForeignKey("generation_result_receipts.result_ref"), nullable=True
    )
    application_ref = Column(
        String(36), ForeignKey("application_records.application_ref"), nullable=True
    )
    resolved_at = Column(DateTime(timezone=False), nullable=True)
    __table_args__ = (
        CheckConstraint(
            "charge_phase IN ('stream_pre_task', 'single_admission', 'batch_admission', "
            "'post_apply', 'pre_apply')",
            name="ck_unit_fee_decisions_charge_phase",
        ),
        CheckConstraint(
            "resolution IN ('pending', 'charged', 'no_charge', 'waived_insufficient', "
            "'waived_charge_failed', 'waived_cancelled', 'waived_delivery_failed', "
            "'stopped_insufficient', 'stopped_late_generation')",
            name="ck_unit_fee_decisions_resolution",
        ),
    )


class GenerationResultReceiptRow(FoundationBase):
    __tablename__ = "generation_result_receipts"

    result_ref = Column(String(36), primary_key=True)
    owner_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    task_id = Column(String(36), ForeignKey("ai_tasks.id"), nullable=False)
    unit_id = Column(String(36), ForeignKey("billing_units.id"), nullable=False)
    operation = Column(String(64), nullable=False)
    step_id = Column(String(36), ForeignKey("execution_steps.id"), nullable=False)
    submission_id = Column(String(36), ForeignKey("external_submissions.id"), nullable=False)
    evidence_id = Column(String(36), ForeignKey("result_evidence.id"), nullable=False)
    selected_attempt_id = Column(String(255), nullable=False)
    recoverable_locator = Column(Text, nullable=False)
    content_digest = Column(String(128), nullable=False)
    validation_version = Column(String(128), nullable=False)
    validated_at = Column(DateTime(timezone=False), nullable=False)
    generation_fulfilled_at = Column(DateTime(timezone=False), nullable=False)
    late_disposition = Column(String(64), nullable=True)
    policy_stop_disposition = Column(String(64), nullable=True)
    automatic_application_allowed = Column(Boolean, nullable=False)


GenerationAuxiliaryBase = declarative_base()


class GenerationResultTargetRow(GenerationAuxiliaryBase):
    """Search projection for a canonical result's immutable original target."""

    __tablename__ = CANVAS_RESULT_TARGETS_TABLE_NAME
    __table_args__ = (
        CheckConstraint(
            "target_kind IN ('canvas_frame_preview', 'canvas_asset_image')",
            name="ck_generation_result_targets_kind",
        ),
        Index(
            "ix_generation_result_targets_page",
            "owner_id",
            "chapter_id",
            "target_kind",
            "target_identity",
            "generation_fulfilled_at",
            "result_ref",
        ),
    )

    result_ref = Column(String(36), primary_key=True)
    owner_id = Column(String(36), nullable=False)
    task_id = Column(String(36), nullable=False)
    series_id = Column(String(36), nullable=False)
    chapter_id = Column(String(36), nullable=False)
    target_kind = Column(String(64), nullable=False)
    target_identity = Column(String(255), nullable=False)
    target_snapshot_digest = Column(String(64), nullable=False)
    generation_fulfilled_at = Column(DateTime(timezone=False), nullable=False)


class FusedGenerationSubmissionRow(GenerationAuxiliaryBase):
    """Durable idempotency, fee, snapshot, dispatch, and result facts for fused generation."""

    __tablename__ = FUSED_GENERATION_SUBMISSIONS_TABLE_NAME
    __table_args__ = (
        UniqueConstraint("owner_id", "idempotency_key", name="uq_fused_submissions_owner_key"),
        UniqueConstraint("task_id", name="uq_fused_submissions_task_id"),
        Index("ix_fused_submissions_recovery", "stage", "updated_at", "submission_id"),
        CheckConstraint("operation = 'fused.image'", name="ck_fused_submissions_operation"),
        CheckConstraint(
            "stage IN ('reserved', 'fee_committed', 'snapshot_ready', 'accepted', "
            "'rejected_after_charge', 'charged_incomplete', 'dispatching', "
            "'provider_result_unknown', 'result_known', 'media_ack_unknown', "
            "'media_started', 'completed', 'failed')",
            name="ck_fused_submissions_stage",
        ),
        CheckConstraint(
            "fee_disposition IN ('debit', 'byok', 'zero')",
            name="ck_fused_submissions_fee_disposition",
        ),
        CheckConstraint(
            "fee_amount >= 0 AND ((fee_disposition = 'debit' AND fee_amount > 0 "
            "AND debit_log_id IS NOT NULL) OR (fee_disposition IN ('byok', 'zero') "
            "AND fee_amount = 0 AND debit_log_id IS NULL))",
            name="ck_fused_submissions_fee_facts",
        ),
        CheckConstraint(
            "length(request_digest) = 64",
            name="ck_fused_submissions_request_digest",
        ),
    )

    submission_id = Column(String(36), primary_key=True)
    owner_id = Column(String(36), nullable=False)
    operation = Column(String(32), nullable=False)
    idempotency_key = Column(String(255), nullable=False)
    request_digest = Column(String(64), nullable=False)
    stage = Column(String(32), nullable=False)
    fee_disposition = Column(String(16), nullable=False)
    fee_amount = Column(Integer, nullable=False)
    debit_log_id = Column(String(36), nullable=True)
    task_id = Column(String(36), nullable=True)
    message_id = Column(String(36), nullable=True)
    encrypted_request = Column(Text, nullable=True)
    request_nonce = Column(String(128), nullable=True)
    encrypted_snapshot = Column(Text, nullable=True)
    snapshot_nonce = Column(String(128), nullable=True)
    encrypted_result = Column(Text, nullable=True)
    result_nonce = Column(String(128), nullable=True)
    result_kind = Column(String(16), nullable=True)
    result_digest = Column(String(64), nullable=True)
    seal_key_id = Column(String(128), nullable=True)
    crypto_algorithm = Column(String(64), nullable=True)
    model_source = Column(String(16), nullable=True)
    model_row_id = Column(String(36), nullable=True)
    provider = Column(String(64), nullable=True)
    principal_id = Column(String(36), nullable=True)
    binding_id = Column(String(36), nullable=True)
    binding_version = Column(String(16), nullable=True)
    endpoint_origin = Column(String(512), nullable=True)
    allowed_content_paths = Column(Text, nullable=True)
    rotation_provenance = Column(String(255), nullable=True)
    snapshot_proof_key_id = Column(String(128), nullable=True)
    snapshot_proof = Column(String(128), nullable=True)
    response_status = Column(Integer, nullable=True)
    response_json = Column(Text, nullable=True)
    dispatch_nonce = Column(String(36), nullable=True)
    dispatch_owner = Column(String(64), nullable=True)
    dispatch_generation = Column(Integer, nullable=True)
    wire_digest = Column(String(64), nullable=True)
    asset_id = Column(String(36), nullable=True)
    created_at = Column(DateTime(timezone=False), nullable=False)
    updated_at = Column(DateTime(timezone=False), nullable=False)


class ApplicationRecordRow(FoundationBase):
    __tablename__ = "application_records"
    __table_args__ = (
        UniqueConstraint("result_ref", "target_scope", name="uq_application_result_target"),
        CheckConstraint(
            "delivery_status IN ('pending', 'applied', 'target_conflict', 'target_deleted', 'failed')",
            name="ck_application_records_delivery_status",
        ),
    )

    application_ref = Column(String(36), primary_key=True)
    result_ref = Column(
        String(36), ForeignKey("generation_result_receipts.result_ref"), nullable=False
    )
    owner_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    task_id = Column(String(36), ForeignKey("ai_tasks.id"), nullable=False)
    unit_id = Column(String(36), ForeignKey("billing_units.id"), nullable=False)
    target_scope = Column(String(255), nullable=False)
    result_digest = Column(String(128), nullable=False)
    delivery_status = Column(String(32), nullable=False)
    before_target_digest = Column(String(128), nullable=True)
    after_target_digest = Column(String(128), nullable=True)
    media_revision_before = Column(Integer, nullable=True)
    media_revision_after = Column(Integer, nullable=True)
    notes_revision_before = Column(Integer, nullable=True)
    notes_revision_after = Column(Integer, nullable=True)
    committed_at = Column(DateTime(timezone=False), nullable=True)


class ExecutionAttemptGroupRow(FoundationBase):
    __tablename__ = "execution_attempt_groups"
    __table_args__ = (
        CheckConstraint("max_retries = 2", name="ck_attempt_groups_max_retries"),
        CheckConstraint("poll_used >= 0", name="ck_attempt_groups_poll_used_nonnegative"),
        CheckConstraint(
            "auth_unavailable_streak >= 0",
            name="ck_attempt_groups_auth_streak_nonnegative",
        ),
        CheckConstraint("poll_sequence >= 0", name="ck_attempt_groups_poll_sequence_nonnegative"),
        CheckConstraint(
            "status IN ('prepared', 'active', 'pending_verification', 'succeeded', 'failed', "
            "'cancelled', 'policy_stopped', 'succeeded_late')",
            name="ck_attempt_groups_status",
        ),
    )

    unit_id = Column(String(36), ForeignKey("billing_units.id"), primary_key=True)
    group_key = Column(String(128), primary_key=True)
    registry_version = Column(String(128), nullable=False)
    attempt_step_ids = Column(JSON, nullable=False)
    original_payload_digest = Column(String(128), nullable=False)
    max_retries = Column(Integer, nullable=False)
    active_attempt = Column(Integer, nullable=False)
    selected_evidence_id = Column(String(36), ForeignKey("result_evidence.id"), nullable=True)
    status = Column(String(32), nullable=False)
    poll_used = Column(Integer, nullable=False, default=0, server_default="0")
    auth_unavailable_streak = Column(Integer, nullable=False, default=0, server_default="0")
    terminal_reason = Column(String(128), nullable=True)
    poll_sequence = Column(Integer, nullable=False, default=0, server_default="0")
    next_poll_at = Column(DateTime(timezone=False), nullable=True)
    outstanding_reservation_ref = Column(String(36), nullable=True)
    sticky_policy_stop_event_ref = Column(String(36), nullable=True)
    active_eligibility_version = Column(String(128), nullable=False)


class StreamFeeRecordRow(FoundationBase):
    __tablename__ = "stream_fee_records"
    __table_args__ = (
        CheckConstraint("operation = 'text.stream'", name="ck_stream_fee_records_operation"),
        CheckConstraint("quoted_amount >= 0", name="ck_stream_fee_records_quote_nonnegative"),
        CheckConstraint(
            "actual_debited_amount >= 0 AND actual_debited_amount <= quoted_amount",
            name="ck_stream_fee_records_debit_bounds",
        ),
        CheckConstraint(
            "disposition IN ('debit', 'skipped', 'byok', 'zero')",
            name="ck_stream_fee_records_disposition",
        ),
        CheckConstraint(
            "settlement_state IN ('committed', 'success_retained', 'settlement_pending', "
            "'refunded', 'no_debit_terminal')",
            name="ck_stream_fee_records_settlement_state",
        ),
        CheckConstraint(
            "start_state IN ('ready_to_start', 'start_reserved', 'start_exposed', 'revoked')",
            name="ck_stream_fee_records_start_state",
        ),
        CheckConstraint(
            "(actual_debited_amount = 0 AND original_debit_log_id IS NULL "
            "AND original_business_key IS NULL) OR "
            "(actual_debited_amount > 0 AND original_debit_log_id IS NOT NULL "
            "AND original_business_key IS NOT NULL)",
            name="ck_stream_fee_records_original_debit_scope",
        ),
    )

    server_attempt_id = Column(String(36), primary_key=True)
    owner_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    operation = Column(String(32), nullable=False)
    frozen_model_provenance = Column(Text, nullable=False)
    quoted_amount = Column(Integer, nullable=False)
    disposition = Column(String(16), nullable=False)
    actual_debited_amount = Column(Integer, nullable=False)
    original_debit_log_id = Column(String(36), ForeignKey("credit_logs.id"), nullable=True)
    original_business_key = Column(String(255), nullable=True)
    settlement_state = Column(String(32), nullable=False)
    start_state = Column(String(24), nullable=False)
    start_token = Column(String(128), nullable=True)
    task_id = Column(String(36), ForeignKey("ai_tasks.id"), nullable=True)
    message_id = Column(String(36), nullable=True)
    created_at = Column(DateTime(timezone=False), nullable=False)
    updated_at = Column(DateTime(timezone=False), nullable=False)


class PollReservationRow(FoundationBase):
    __tablename__ = "poll_reservations"
    __table_args__ = (
        UniqueConstraint("unit_id", "group_key", "sequence", name="uq_poll_reservations_group_sequence"),
        ForeignKeyConstraint(
            ["unit_id", "group_key"],
            ["execution_attempt_groups.unit_id", "execution_attempt_groups.group_key"],
            name="fk_poll_reservations_attempt_group",
        ),
        CheckConstraint("sequence > 0", name="ck_poll_reservations_sequence_positive"),
        CheckConstraint("committed_poll_count > 0", name="ck_poll_reservations_count_positive"),
        CheckConstraint(
            "state IN ('reserved', 'accepted', 'closed_unknown')",
            name="ck_poll_reservations_state",
        ),
    )

    reservation_id = Column(String(36), primary_key=True)
    unit_id = Column(String(36), nullable=False)
    group_key = Column(String(128), nullable=False)
    sequence = Column(Integer, nullable=False)
    attempt_id = Column(String(36), nullable=False)
    step_id = Column(String(36), ForeignKey("execution_steps.id"), nullable=False)
    submission_id = Column(String(36), ForeignKey("external_submissions.id"), nullable=False)
    external_id = Column(String(255), nullable=False)
    payload_digest = Column(String(128), nullable=False)
    committed_poll_count = Column(Integer, nullable=False)
    reserved_at = Column(DateTime(timezone=False), nullable=False)
    timeout_at = Column(DateTime(timezone=False), nullable=False)
    next_poll_at = Column(DateTime(timezone=False), nullable=False)
    token = Column(String(128), nullable=False)
    state = Column(String(24), nullable=False)
    observation_ref = Column(String(36), nullable=True)


class PolicyStopEventRow(FoundationBase):
    __tablename__ = "policy_stop_events"
    __table_args__ = (
        UniqueConstraint(
            "unit_id", "group_key", "policy_version", "reason",
            name="uq_policy_stop_events_group_policy_reason",
        ),
        ForeignKeyConstraint(
            ["unit_id", "group_key"],
            ["execution_attempt_groups.unit_id", "execution_attempt_groups.group_key"],
            name="fk_policy_stop_events_attempt_group",
        ),
        CheckConstraint("reason = 'auth_unavailable_12'", name="ck_policy_stop_events_reason"),
        CheckConstraint(
            "financial_disposition IN ('pending', 'refunded', 'retained_by_started_cancel', 'no_debit')",
            name="ck_policy_stop_events_financial_disposition",
        ),
        CheckConstraint("amount >= 0", name="ck_policy_stop_events_amount_nonnegative"),
    )

    event_id = Column(String(36), primary_key=True)
    unit_id = Column(String(36), nullable=False)
    group_key = Column(String(128), nullable=False)
    policy_version = Column(String(128), nullable=False)
    reason = Column(String(64), nullable=False)
    reservation_refs = Column(JSON, nullable=False)
    observation_refs = Column(JSON, nullable=False)
    active_step_id = Column(String(36), ForeignKey("execution_steps.id"), nullable=False)
    active_submission_id = Column(String(36), ForeignKey("external_submissions.id"), nullable=False)
    external_id = Column(String(255), nullable=False)
    stopped_at = Column(DateTime(timezone=False), nullable=False)
    financial_disposition = Column(String(32), nullable=False)
    original_debit_ref = Column(String(36), ForeignKey("credit_logs.id"), nullable=True)
    refund_ref = Column(String(36), ForeignKey("credit_logs.id"), nullable=True)
    amount = Column(Integer, nullable=False)
    owner_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    task_id = Column(String(36), ForeignKey("ai_tasks.id"), nullable=False)


class LocalContinuationWorkRow(FoundationBase):
    __tablename__ = "local_continuation_work"
    __table_args__ = (
        UniqueConstraint("task_id", "unit_id", "policy_version", name="uq_continuation_work_task_unit_policy"),
        CheckConstraint(
            "phase IN ('delivery_pending', 'fee_pending', 'finalize_pending', 'done', "
            "'stopped_cancelled', 'stopped_insufficient', 'stopped_delivery_failed', "
            "'stopped_late_generation')",
            name="ck_continuation_work_phase",
        ),
        CheckConstraint("phase_version >= 0", name="ck_continuation_work_phase_version_nonnegative"),
        CheckConstraint(
            "last_execution_generation IS NULL OR last_execution_generation >= 0",
            name="ck_continuation_work_generation_nonnegative",
        ),
    )

    work_id = Column(String(36), primary_key=True)
    owner_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    task_id = Column(String(36), ForeignKey("ai_tasks.id"), nullable=False)
    unit_id = Column(String(36), ForeignKey("billing_units.id"), nullable=False)
    operation = Column(String(64), nullable=False)
    policy_version = Column(String(128), nullable=False)
    result_ref = Column(String(36), ForeignKey("generation_result_receipts.result_ref"), nullable=False)
    content_digest = Column(String(128), nullable=False)
    generation_fulfilled_at = Column(DateTime(timezone=False), nullable=False)
    original_first_intent_at = Column(DateTime(timezone=False), nullable=False)
    original_deadline_at = Column(DateTime(timezone=False), nullable=False)
    phase_order = Column(JSON, nullable=False)
    phase = Column(String(32), nullable=False)
    phase_version = Column(Integer, nullable=False)
    fee_decision_ref = Column(String(36), ForeignKey("unit_fee_decisions.unit_id"), nullable=True)
    application_ref = Column(String(36), ForeignKey("application_records.application_ref"), nullable=True)
    terminal_reason = Column(String(128), nullable=True)
    last_execution_generation = Column(Integer, nullable=True)


@dataclass(frozen=True, slots=True)
class ColumnRequirement:
    kind: str
    nullable: bool
    length: int | None = None


@dataclass(frozen=True, slots=True)
class ForeignKeyRequirement:
    columns: tuple[str, ...]
    referred_table: str
    referred_columns: tuple[str, ...]
    name: str | None = None


@dataclass(frozen=True, slots=True)
class UniqueRequirement:
    columns: tuple[str, ...]
    name: str | None = None


@dataclass(frozen=True, slots=True)
class CheckRequirement:
    name: str
    expression: str


@dataclass(frozen=True, slots=True)
class IndexRequirement:
    columns: tuple[str, ...]
    unique: bool = False
    name: str | None = None


@dataclass(frozen=True, slots=True)
class TableRequirement:
    columns: Mapping[str, ColumnRequirement]
    primary_key: tuple[str, ...]
    foreign_keys: tuple[ForeignKeyRequirement, ...] = ()
    unique_constraints: tuple[UniqueRequirement, ...] = ()
    checks: tuple[CheckRequirement, ...] = ()
    indexes: tuple[IndexRequirement, ...] = ()


@dataclass(frozen=True, slots=True)
class CatalogColumn:
    kind: str
    nullable: bool
    length: int | None = None


@dataclass(frozen=True, slots=True)
class CatalogForeignKey:
    columns: tuple[str, ...]
    referred_table: str
    referred_columns: tuple[str, ...]
    name: str | None
    effective: bool | None = True
    validated: bool | None = True


@dataclass(frozen=True, slots=True)
class CatalogUnique:
    columns: tuple[str, ...]
    name: str | None
    effective: bool | None = True
    validated: bool | None = True
    predicate: str | None = None


@dataclass(frozen=True, slots=True)
class CatalogCheck:
    expression: str
    name: str | None
    effective: bool | None = True
    validated: bool | None = True


@dataclass(frozen=True, slots=True)
class CatalogIndex:
    columns: tuple[str, ...]
    name: str | None
    unique: bool
    effective: bool | None = True
    ready: bool | None = True
    live: bool | None = True
    predicate: str | None = None


@dataclass(frozen=True, slots=True)
class CatalogTable:
    columns: Mapping[str, CatalogColumn]
    primary_key: tuple[str, ...]
    foreign_keys: tuple[CatalogForeignKey, ...]
    unique_constraints: tuple[CatalogUnique, ...]
    checks: tuple[CatalogCheck, ...]
    indexes: tuple[CatalogIndex, ...] = ()


class CatalogReader(Protocol):
    def table_names(self) -> set[str]: ...

    def table(self, name: str) -> CatalogTable: ...


@dataclass(frozen=True, slots=True)
class SchemaIssue:
    code: str
    table: str
    detail: str


@dataclass(frozen=True, slots=True)
class SchemaCheck:
    ready: bool
    issues: tuple[SchemaIssue, ...]


class SQLAlchemyCatalog:
    """Normalize SQLAlchemy Inspector output for deterministic preflight checks."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        self._inspector = inspect(connection)
        self._postgresql_enforced_column = False
        if connection.dialect.name == "postgresql":
            self._load_postgresql_constraint_state()

    def _load_postgresql_constraint_state(self) -> None:
        self._postgresql_enforced_column = bool(
            self._connection.execute(
                text(
                    "SELECT EXISTS ("
                    "SELECT 1 FROM pg_catalog.pg_attribute "
                    "WHERE attrelid = 'pg_catalog.pg_constraint'::regclass "
                    "AND attname = 'conenforced' AND NOT attisdropped)"
                )
            ).scalar_one()
        )

    def _constraint_state(self, table_name: str, name: str | None, kind: str):
        if name is None:
            return None
        enforced = "con.conenforced" if self._postgresql_enforced_column else "TRUE"
        row = self._connection.execute(
            text(
                "SELECT con.convalidated, "
                f"{enforced}, idx.relname "
                "FROM pg_catalog.pg_constraint AS con "
                "LEFT JOIN pg_catalog.pg_class AS idx ON idx.oid = con.conindid "
                "WHERE con.conrelid = pg_catalog.to_regclass(:table_name) "
                "AND con.conname = :constraint_name AND con.contype = :constraint_type"
            ),
            {
                "table_name": table_name,
                "constraint_name": name,
                "constraint_type": kind,
            },
        ).one_or_none()
        if row is None:
            return None
        return bool(row[1]), bool(row[0]), row[2]

    def _index_state(self, table_name: str, index_name: str):
        row = self._connection.execute(
            text(
                "SELECT idx.indisvalid, idx.indisready, idx.indislive, "
                "pg_catalog.pg_get_expr(idx.indpred, idx.indrelid) "
                "FROM pg_catalog.pg_index AS idx "
                "JOIN pg_catalog.pg_class AS table_class ON table_class.oid = idx.indrelid "
                "JOIN pg_catalog.pg_class AS index_class ON index_class.oid = idx.indexrelid "
                "WHERE idx.indrelid = pg_catalog.to_regclass(:table_name) "
                "AND index_class.relname = :index_name"
            ),
            {"table_name": table_name, "index_name": index_name},
        ).one_or_none()
        if row is None:
            return False, None
        return bool(row[0] and row[1] and row[2]), row[3]

    def _index_readiness(self, table_name: str, index_name: str):
        row = self._connection.execute(
            text(
                "SELECT idx.indisvalid, idx.indisready, idx.indislive, "
                "pg_catalog.pg_get_expr(idx.indpred, idx.indrelid) "
                "FROM pg_catalog.pg_index AS idx "
                "JOIN pg_catalog.pg_class AS table_class ON table_class.oid = idx.indrelid "
                "JOIN pg_catalog.pg_class AS index_class ON index_class.oid = idx.indexrelid "
                "WHERE idx.indrelid = pg_catalog.to_regclass(:table_name) "
                "AND index_class.relname = :index_name"
            ),
            {"table_name": table_name, "index_name": index_name},
        ).one_or_none()
        if row is None:
            return False, False, False, None
        return bool(row[0]), bool(row[1]), bool(row[2]), row[3]

    def table_names(self) -> set[str]:
        return set(self._inspector.get_table_names())

    def table(self, name: str) -> CatalogTable:
        columns: dict[str, CatalogColumn] = {}
        for column in self._inspector.get_columns(name):
            kind, length = _type_details(column["type"])
            columns[str(column["name"])] = CatalogColumn(
                kind=kind,
                nullable=bool(column["nullable"]),
                length=length,
            )

        primary_key = self._inspector.get_pk_constraint(name).get("constrained_columns") or []
        foreign_keys = []
        for item in self._inspector.get_foreign_keys(name):
            state = self._constraint_state(name, item.get("name"), "f") if self._connection.dialect.name == "postgresql" else None
            foreign_keys.append(
                CatalogForeignKey(
                    tuple(item.get("constrained_columns") or ()),
                    str(item.get("referred_table") or ""),
                    tuple(item.get("referred_columns") or ()),
                    item.get("name"),
                    True if state is None and self._connection.dialect.name == "sqlite" else (None if state is None else state[0]),
                    True if state is None and self._connection.dialect.name == "sqlite" else (None if state is None else state[1]),
                )
            )
        unique_constraints = []
        indexes = []
        for item in self._inspector.get_unique_constraints(name):
            state = self._constraint_state(name, item.get("name"), "u") if self._connection.dialect.name == "postgresql" else None
            index_name = None if state is None else state[2]
            effective, predicate = (
                (True, None)
                if self._connection.dialect.name == "sqlite"
                else self._index_state(name, index_name)
                if self._connection.dialect.name == "postgresql" and index_name is not None
                else (None, None)
            )
            unique_constraints.append(
                CatalogUnique(
                    tuple(item.get("column_names") or ()),
                    item.get("name"),
                    effective,
                    True if state is None and self._connection.dialect.name == "sqlite" else (None if state is None else state[1]),
                    predicate,
                )
            )
        for item in self._inspector.get_indexes(name):
            index_name = str(item.get("name") or "")
            if self._connection.dialect.name == "postgresql":
                effective, ready, live, predicate = self._index_readiness(
                    name, index_name
                )
            elif self._connection.dialect.name == "sqlite":
                dialect_options = item.get("dialect_options") or {}
                predicate_value = dialect_options.get("sqlite_where")
                predicate = None if predicate_value is None else str(predicate_value)
                effective, ready, live = True, True, True
            else:
                effective, ready, live, predicate = None, None, None, None
            indexes.append(
                CatalogIndex(
                    tuple(item.get("column_names") or ()),
                    item.get("name"),
                    bool(item.get("unique")),
                    effective,
                    ready,
                    live,
                    predicate,
                )
            )
            if item.get("unique"):
                index_ready = (
                    None
                    if None in (effective, ready, live)
                    else bool(effective and ready and live)
                )
                unique_constraints.append(
                    CatalogUnique(
                        tuple(item.get("column_names") or ()),
                        item.get("name"),
                        index_ready,
                        index_ready,
                        predicate,
                    )
                )
        checks = (
            list(_sqlite_check_constraints(self._connection, name))
            if self._connection.dialect.name == "sqlite"
            else []
        )
        check_items = (
            ()
            if self._connection.dialect.name == "sqlite"
            else self._inspector.get_check_constraints(name)
        )
        for item in check_items:
            state = self._constraint_state(name, item.get("name"), "c") if self._connection.dialect.name == "postgresql" else None
            checks.append(
                CatalogCheck(
                    str(item.get("sqltext") or ""),
                    item.get("name"),
                    True if state is None and self._connection.dialect.name == "sqlite" else (None if state is None else state[0]),
                    True if state is None and self._connection.dialect.name == "sqlite" else (None if state is None else state[1]),
                )
            )
        return CatalogTable(
            columns=columns,
            primary_key=tuple(primary_key),
            foreign_keys=tuple(foreign_keys),
            unique_constraints=tuple(unique_constraints),
            checks=tuple(checks),
            indexes=tuple(indexes),
        )


def _sqlite_check_constraints(connection: Connection, table_name: str) -> tuple[CatalogCheck, ...]:
    row = connection.execute(
        text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :name"),
        {"name": table_name},
    ).one_or_none()
    if row is None or not row[0]:
        return ()

    ddl = str(row[0])
    checks: list[CatalogCheck] = []
    pattern = re.compile(
        r"CONSTRAINT\s+([A-Za-z0-9_]+)\s+CHECK\s*\(",
        re.IGNORECASE,
    )
    for match in pattern.finditer(ddl):
        cursor = match.end()
        depth = 1
        quote: str | None = None
        start = cursor
        while cursor < len(ddl) and depth:
            character = ddl[cursor]
            if quote is not None:
                closing = "]" if quote == "[" else quote
                if character == closing:
                    if cursor + 1 < len(ddl) and ddl[cursor + 1] == closing:
                        cursor += 2
                        continue
                    quote = None
            elif character in ("'", '"', chr(96), "["):
                quote = character
            elif character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
            cursor += 1
        if depth == 0:
            checks.append(
                CatalogCheck(
                    ddl[start:cursor - 1],
                    match.group(1),
                    True,
                    True,
                )
            )
    return tuple(checks)


def _type_details(column_type: TypeEngine[object]) -> tuple[str, int | None]:
    if isinstance(column_type, Boolean):
        return "boolean", None
    if isinstance(column_type, DateTime):
        return "datetime", None
    if isinstance(column_type, Integer):
        return "integer", None
    if isinstance(column_type, JSON):
        return "json", None
    if isinstance(column_type, Text):
        return "text", None
    if isinstance(column_type, String):
        return "string", column_type.length
    return column_type.__class__.__name__.lower(), None


def _col(kind: str, nullable: bool, length: int | None = None) -> ColumnRequirement:
    return ColumnRequirement(kind, nullable, length)


def _fk(
    columns: str | tuple[str, ...],
    referred_table: str,
    referred_columns: str | tuple[str, ...] = "id",
    name: str | None = None,
) -> ForeignKeyRequirement:
    local = (columns,) if isinstance(columns, str) else columns
    remote = (referred_columns,) if isinstance(referred_columns, str) else referred_columns
    return ForeignKeyRequirement(local, referred_table, remote, name)


def _unique(columns: str | tuple[str, ...], name: str | None = None) -> UniqueRequirement:
    return UniqueRequirement((columns,) if isinstance(columns, str) else columns, name)


def _check(name: str, expression: str) -> CheckRequirement:
    return CheckRequirement(name, expression)


def _columns(**items: ColumnRequirement) -> Mapping[str, ColumnRequirement]:
    return items


FOUNDATION_REQUIREMENTS: Mapping[str, TableRequirement] = {
    "users": TableRequirement(
        _columns(id=_col("string", False, 36)), ("id",)
    ),
    "ai_tasks": TableRequirement(
        _columns(
            id=_col("string", False, 36), user_id=_col("string", False, 36),
            type=_col("string", False, 20), status=_col("string", False, 20),
            claimed_by=_col("string", True, 64), lease_until=_col("datetime", True),
            execution_generation=_col("integer", False), claim_token=_col("string", True, 64),
            recovery_status=_col("string", False, 32), billing_status=_col("string", False, 32),
            user_cancelled_at=_col("datetime", True), cancellation_reason=_col("string", True, 64),
        ),
        ("id",),
        foreign_keys=(_fk("user_id", "users"),),
        checks=(
            _check(
                "ck_ai_tasks_billing_status",
                "billing_status IN ('legacy_unreconciled', 'unbilled', 'charged', 'partially_refunded', 'refunded', 'no_charge')",
            ),
        ),
    ),
    "task_quotes": TableRequirement(
        _columns(
            id=_col("string", False, 36), user_id=_col("string", False, 36),
            operation=_col("string", False, 64), idempotency_key=_col("string", False, 255),
            unit_price=_col("integer", False), unit_count=_col("integer", False),
            total_amount=_col("integer", False), input_digest=_col("string", False, 128),
            candidate_digest=_col("string", False, 128), unit_manifest=_col("json", False),
            confirmation_key=_col("string", True, 255), status=_col("string", False, 24),
            expires_at=_col("datetime", False), confirmed_at=_col("datetime", True),
            consumed_at=_col("datetime", True),
        ),
        ("id",),
        foreign_keys=(_fk("user_id", "users"),),
        unique_constraints=(
            _unique(("user_id", "operation", "idempotency_key"), "uq_task_quotes_user_operation_key"),
            _unique(("user_id", "confirmation_key"), "uq_task_quotes_user_confirmation_key"),
        ),
        checks=(
            _check("ck_task_quotes_unit_price_nonnegative", "unit_price >= 0"),
            _check("ck_task_quotes_unit_count_nonnegative", "unit_count >= 0"),
            _check("ck_task_quotes_total_nonnegative", "total_amount >= 0"),
            _check("ck_task_quotes_total_matches_units", "total_amount = unit_price * unit_count"),
        ),
    ),
    "task_submissions": TableRequirement(
        _columns(
            id=_col("string", False, 36), user_id=_col("string", False, 36),
            operation=_col("string", False, 64), idempotency_key=_col("string", False, 255),
            request_digest=_col("string", False, 128), task_ids=_col("json", False),
            task_quote_id=_col("string", True, 36), status=_col("string", False, 24),
            created_at=_col("datetime", False),
        ),
        ("id",),
        foreign_keys=(_fk("user_id", "users"), _fk("task_quote_id", "task_quotes")),
        unique_constraints=(
            _unique(("user_id", "operation", "idempotency_key"), "uq_task_submissions_user_operation_key"),
            _unique("task_quote_id", "uq_task_submissions_task_quote_id"),
        ),
        checks=(_check("ck_task_submissions_operation_nonempty", "length(operation) > 0"),),
    ),
    "billing_units": TableRequirement(
        _columns(
            id=_col("string", False, 36), task_id=_col("string", False, 36),
            item_key=_col("string", False, 255), operation=_col("string", False, 64),
            step_graph_version=_col("string", False, 64), input_digest=_col("string", False, 128),
            quoted_amount=_col("integer", False), debited_amount=_col("integer", False),
            refunded_amount=_col("integer", False), billing_status=_col("string", False, 32),
            result_status=_col("string", False, 32), first_intent_at=_col("datetime", True),
            deadline_at=_col("datetime", True), success_confirmed_at=_col("datetime", True),
            user_cancelled_at=_col("datetime", True), cancellation_reason=_col("string", True, 64),
            quote_id=_col("string", True, 36), quote_unit_key=_col("string", True, 255),
            candidate_digest=_col("string", True, 128),
        ),
        ("id",),
        foreign_keys=(_fk("task_id", "ai_tasks"), _fk("quote_id", "task_quotes")),
        unique_constraints=(
            _unique(("task_id", "item_key"), "uq_billing_units_task_item"),
            _unique(("quote_id", "quote_unit_key"), "uq_billing_units_quote_item"),
        ),
        checks=(
            _check("ck_billing_units_quoted_nonnegative", "quoted_amount >= 0"),
            _check("ck_billing_units_debited_nonnegative", "debited_amount >= 0"),
            _check("ck_billing_units_refunded_nonnegative", "refunded_amount >= 0"),
            _check("ck_billing_units_refund_not_over_debit", "refunded_amount <= debited_amount"),
            _check("ck_billing_units_billing_status", "billing_status IN ('unbilled', 'charged', 'refunded', 'no_charge')"),
            _check("ck_billing_units_result_status", "result_status IN ('pending', 'partial', 'succeeded', 'failed', 'unknown')"),
        ),
    ),
    "execution_steps": TableRequirement(
        _columns(
            id=_col("string", False, 36), billing_unit_id=_col("string", False, 36),
            step_key=_col("string", False, 128), dependencies=_col("json", False),
            input_digest=_col("string", False, 128), input_snapshot=_col("json", True),
            required=_col("boolean", False), step_order=_col("integer", False),
            execution_status=_col("string", False, 32),
        ),
        ("id",),
        foreign_keys=(_fk("billing_unit_id", "billing_units"),),
        unique_constraints=(_unique(("billing_unit_id", "step_key"), "uq_execution_steps_unit_key"),),
        checks=(
            _check("ck_execution_steps_order_nonnegative", "step_order >= 0"),
            _check("ck_execution_steps_status", "execution_status IN ('prepared', 'submitting', 'submitted', 'result_observed', 'applied', 'failed', 'reconciling', 'deterministic_empty')"),
        ),
    ),
    "external_submissions": TableRequirement(
        _columns(
            id=_col("string", False, 36), step_id=_col("string", False, 36),
            submission_key=_col("string", False, 255), provider=_col("string", False, 64),
            model_ref=_col("string", True, 255), request_digest=_col("string", False, 128),
            request_snapshot=_col("json", False), intent_at=_col("datetime", False),
            submitted_at=_col("datetime", True), external_task_id=_col("string", True, 255),
            submission_status=_col("string", False, 32), deadline_at=_col("datetime", False),
        ),
        ("id",),
        foreign_keys=(_fk("step_id", "execution_steps"),),
        unique_constraints=(
            _unique("step_id", "uq_external_submissions_step_id"),
            _unique("submission_key", "uq_external_submissions_submission_key"),
        ),
    ),
    "result_evidence": TableRequirement(
        _columns(
            id=_col("string", False, 36), step_id=_col("string", False, 36),
            submission_id=_col("string", True, 36), evidence_key=_col("string", False, 255),
            source=_col("string", False, 64), evidence_kind=_col("string", False, 64),
            payload_digest=_col("string", False, 128), result_location=_col("text", True),
            result_digest=_col("string", True, 128), received_at=_col("datetime", False),
            validity=_col("string", False, 24), application_status=_col("string", False, 24),
            applied_at=_col("datetime", True),
        ),
        ("id",),
        foreign_keys=(_fk("step_id", "execution_steps"), _fk("submission_id", "external_submissions")),
        unique_constraints=(_unique("evidence_key", "uq_result_evidence_evidence_key"),),
        checks=(
            _check("ck_result_evidence_submission_or_empty", "submission_id IS NOT NULL OR evidence_kind = 'deterministic_empty'"),
            _check("ck_result_evidence_validity", "validity IN ('valid', 'invalid', 'unknown')"),
            _check("ck_result_evidence_application_status", "application_status IN ('pending', 'applied', 'rejected')"),
        ),
    ),
    "user_credits": TableRequirement(
        _columns(
            id=_col("string", False, 36), user_id=_col("string", False, 36),
            credits=_col("integer", False),
        ),
        ("id",),
        foreign_keys=(_fk("user_id", "users"),),
        unique_constraints=(_unique("user_id"),),
    ),
    "credit_logs": TableRequirement(
        _columns(
            id=_col("string", False, 36), user_id=_col("string", False, 36),
            amount=_col("integer", False), balance_after=_col("integer", False),
            type=_col("string", False, 20), task_id=_col("string", True, 36),
            business_key=_col("string", True, 255), billing_unit_id=_col("string", True, 36),
            related_debit_id=_col("string", True, 36), created_at=_col("datetime", False),
        ),
        ("id",),
        foreign_keys=(
            _fk("user_id", "users"),
            _fk("billing_unit_id", "billing_units", name="fk_credit_logs_billing_unit_id_billing_units"),
            _fk("related_debit_id", "credit_logs", name="fk_credit_logs_related_debit_id_credit_logs"),
        ),
        unique_constraints=(_unique("business_key", "uq_credit_logs_business_key"),),
    ),
}


@dataclass(frozen=True, slots=True)
class _RecordModelSet:
    row_type: type
    record_type: type
    table_name: str


RECORD_MODEL_SETS = (
    _RecordModelSet(UnitFeeDecisionRow, UnitFeeDecision, "unit_fee_decisions"),
    _RecordModelSet(GenerationResultReceiptRow, GenerationResultReceipt, "generation_result_receipts"),
    _RecordModelSet(ApplicationRecordRow, ApplicationRecord, "application_records"),
    _RecordModelSet(ExecutionAttemptGroupRow, ExecutionAttemptGroup, "execution_attempt_groups"),
    _RecordModelSet(LocalContinuationWorkRow, LocalContinuationWork, "local_continuation_work"),
    _RecordModelSet(StreamFeeRecordRow, StreamFeeRecord, "stream_fee_records"),
    _RecordModelSet(PollReservationRow, PollReservation, "poll_reservations"),
    _RecordModelSet(PolicyStopEventRow, PolicyStopEvent, "policy_stop_events"),
)
GENERATION_RECORD_TABLE_NAMES = tuple(item.table_name for item in RECORD_MODEL_SETS)
GENERATION_RECORD_TABLES = tuple(FoundationBase.metadata.tables[name] for name in GENERATION_RECORD_TABLE_NAMES)
FOUNDATION_TABLE_MAP = {table.name: table for table in FOUNDATION_TABLES}


def _table_requirement(table) -> TableRequirement:
    columns: dict[str, ColumnRequirement] = {}
    for column in table.columns:
        kind, length = _type_details(column.type)
        columns[column.name] = ColumnRequirement(kind, bool(column.nullable), length)
    foreign_keys = tuple(
        ForeignKeyRequirement(
            tuple(constraint.column_keys),
            next(iter(constraint.elements)).column.table.name,
            tuple(element.column.name for element in constraint.elements),
            constraint.name,
        )
        for constraint in table.foreign_key_constraints
    )
    unique_constraints = tuple(
        UniqueRequirement(tuple(column.name for column in constraint.columns), constraint.name)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    )
    checks = tuple(
        CheckRequirement(constraint.name or "", str(constraint.sqltext))
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    )
    indexes = tuple(
        IndexRequirement(
            tuple(column.name for column in index.columns),
            bool(index.unique),
            index.name,
        )
        for index in table.indexes
    )
    primary_key = tuple(column.name for column in table.primary_key.columns)
    return TableRequirement(
        columns,
        primary_key,
        foreign_keys,
        unique_constraints,
        checks,
        indexes,
    )


GENERATION_RECORD_REQUIREMENTS = {
    name: _table_requirement(FoundationBase.metadata.tables[name])
    for name in GENERATION_RECORD_TABLE_NAMES
}
_canvas_target_table = GenerationResultTargetRow.__table__
_canvas_target_model_requirement = _table_requirement(_canvas_target_table)
CANVAS_RESULT_TARGET_REQUIREMENTS = {
    CANVAS_RESULT_TARGETS_TABLE_NAME: TableRequirement(
        columns=_canvas_target_model_requirement.columns,
        primary_key=_canvas_target_model_requirement.primary_key,
        foreign_keys=(
            _fk("result_ref", "generation_result_receipts", "result_ref"),
            _fk("owner_id", "users"),
            _fk("task_id", "ai_tasks"),
        ),
        unique_constraints=_canvas_target_model_requirement.unique_constraints,
        checks=_canvas_target_model_requirement.checks,
        indexes=_canvas_target_model_requirement.indexes,
    )
}
CANVAS_RESULT_TARGET_INDEX_REQUIREMENTS = {
    "generation_result_receipts": (
        IndexRequirement(
            ("owner_id", "result_ref"),
            name="ix_generation_result_receipts_owner_result_ref",
        ),
    )
}
_fused_submission_requirement = _table_requirement(
    FusedGenerationSubmissionRow.__table__
)
FUSED_GENERATION_SUBMISSION_REQUIREMENTS = {
    FUSED_GENERATION_SUBMISSIONS_TABLE_NAME: TableRequirement(
        columns=_fused_submission_requirement.columns,
        primary_key=_fused_submission_requirement.primary_key,
        foreign_keys=(
            _fk("owner_id", "users"),
            _fk("debit_log_id", "credit_logs"),
        ),
        unique_constraints=tuple(
            UniqueRequirement(item.columns)
            for item in _fused_submission_requirement.unique_constraints
        ),
        checks=_fused_submission_requirement.checks,
        indexes=_fused_submission_requirement.indexes,
    )
}
MIGRATION_HISTORY_REQUIREMENT = TableRequirement(
    {
        "version": ColumnRequirement("string", False, 64),
        "checksum": ColumnRequirement("string", False, 64),
        "applied_at": ColumnRequirement("datetime", False),
    },
    ("version",),
)


def _check_requirements(
    catalog: CatalogReader,
    requirements: Mapping[str, TableRequirement],
    required_tables: Sequence[str],
    *,
    reject_extra_columns: bool = False,
) -> SchemaCheck:
    issues: list[SchemaIssue] = []
    available_tables = catalog.table_names()
    for table_name in required_tables:
        requirement = requirements[table_name]
        if table_name not in available_tables:
            issues.append(SchemaIssue("missing_table", table_name, "required table is absent"))
            continue
        actual = catalog.table(table_name)
        if reject_extra_columns:
            extra_columns = sorted(set(actual.columns) - set(requirement.columns))
            if extra_columns:
                issues.append(SchemaIssue("unexpected_columns", table_name, ", ".join(extra_columns)))
        for column_name, expected in requirement.columns.items():
            found = actual.columns.get(column_name)
            if found is None:
                issues.append(SchemaIssue("missing_column", table_name, column_name))
                continue
            if found.kind != expected.kind or (expected.length is not None and found.length != expected.length):
                issues.append(SchemaIssue("incompatible_column_type", table_name, column_name))
            if found.nullable != expected.nullable:
                issues.append(SchemaIssue("incompatible_column_nullability", table_name, column_name))
        if actual.primary_key != requirement.primary_key:
            issues.append(SchemaIssue("incompatible_primary_key", table_name, str(actual.primary_key)))
        for expected in requirement.foreign_keys:
            candidates = [
                item for item in actual.foreign_keys
                if item.columns == expected.columns
                and item.referred_table == expected.referred_table
                and item.referred_columns == expected.referred_columns
                and (expected.name is None or item.name == expected.name)
            ]
            if not candidates:
                issues.append(SchemaIssue("missing_or_incompatible_foreign_key", table_name, expected.name or f"{expected.columns}->{expected.referred_table}.{expected.referred_columns}"))
            elif not any(item.effective is True and item.validated is True for item in candidates):
                issues.append(SchemaIssue("foreign_key_not_effective_and_validated", table_name, expected.name or str(expected.columns)))
        for expected in requirement.unique_constraints:
            candidates = [
                item for item in actual.unique_constraints
                if item.columns == expected.columns
                and (expected.name is None or item.name == expected.name)
            ]
            if not candidates:
                issues.append(SchemaIssue("missing_or_incompatible_unique", table_name, expected.name or str(expected.columns)))
            elif not any(
                item.effective is True and item.validated is True and item.predicate is None
                for item in candidates
            ):
                issues.append(SchemaIssue("unique_not_effective_validated_and_global", table_name, expected.name or str(expected.columns)))
        for expected in requirement.indexes:
            candidates = [
                item
                for item in actual.indexes
                if item.columns == expected.columns
                and item.unique is expected.unique
                and (expected.name is None or item.name == expected.name)
            ]
            if not candidates:
                issues.append(
                    SchemaIssue(
                        "missing_or_incompatible_index",
                        table_name,
                        expected.name or str(expected.columns),
                    )
                )
            elif not any(
                item.effective is True
                and item.ready is True
                and item.live is True
                and item.predicate is None
                for item in candidates
            ):
                issues.append(
                    SchemaIssue(
                        "index_not_effective_ready_live_and_global",
                        table_name,
                        expected.name or str(expected.columns),
                    )
                )
        string_columns = frozenset(
            name
            for name, column in requirement.columns.items()
            if column.kind in {"string", "text"}
        )
        for expected in requirement.checks:
            expected_expression = canonical_check_expression(
                expected.expression,
                table_name=table_name,
                string_columns=string_columns,
            )
            candidates = [
                item
                for item in actual.checks
                if item.name == expected.name
                and expected_expression is not None
                and canonical_check_expression(
                    item.expression,
                    table_name=table_name,
                    string_columns=string_columns,
                ) == expected_expression
            ]
            if not candidates:
                issues.append(SchemaIssue("missing_or_incompatible_check", table_name, expected.name))
            elif not any(item.effective is True and item.validated is True for item in candidates):
                issues.append(SchemaIssue("check_not_effective_and_validated", table_name, expected.name))
    return SchemaCheck(not issues, tuple(issues))


def check_foundation_schema(catalog: CatalogReader) -> SchemaCheck:
    """Require the effective, validated legacy column and constraint structures."""

    return _check_requirements(catalog, FOUNDATION_REQUIREMENTS, FOUNDATION_TABLE_NAMES)


def check_generation_record_schema(catalog: CatalogReader) -> SchemaCheck:
    """Require the complete, effective schema of all eight generation records."""

    return _check_requirements(
        catalog,
        GENERATION_RECORD_REQUIREMENTS,
        GENERATION_RECORD_TABLE_NAMES,
        reject_extra_columns=True,
    )


def _check_index_requirements(
    catalog: CatalogReader,
    table_name: str,
    requirements: Sequence[IndexRequirement],
) -> tuple[SchemaIssue, ...]:
    if table_name not in catalog.table_names():
        return (SchemaIssue("missing_table", table_name, "required index table is absent"),)
    actual_indexes = catalog.table(table_name).indexes
    issues: list[SchemaIssue] = []
    for expected in requirements:
        candidates = [
            item
            for item in actual_indexes
            if item.columns == expected.columns
            and item.unique is expected.unique
            and (expected.name is None or item.name == expected.name)
        ]
        if not candidates:
            issues.append(
                SchemaIssue(
                    "missing_or_incompatible_index",
                    table_name,
                    expected.name or str(expected.columns),
                )
            )
        elif not any(
            item.effective is True
            and item.ready is True
            and item.live is True
            and item.predicate is None
            for item in candidates
        ):
            issues.append(
                SchemaIssue(
                    "index_not_effective_ready_live_and_global",
                    table_name,
                    expected.name or str(expected.columns),
                )
            )
    return tuple(issues)


def check_canvas_result_target_schema(catalog: CatalogReader) -> SchemaCheck:
    """Require the projection table and both live, full indexes for v2."""

    table_check = _check_requirements(
        catalog,
        CANVAS_RESULT_TARGET_REQUIREMENTS,
        (CANVAS_RESULT_TARGETS_TABLE_NAME,),
        reject_extra_columns=True,
    )
    index_issues = tuple(
        issue
        for table_name, requirements in CANVAS_RESULT_TARGET_INDEX_REQUIREMENTS.items()
        for issue in _check_index_requirements(catalog, table_name, requirements)
    )
    issues = (*table_check.issues, *index_issues)
    return SchemaCheck(not issues, tuple(issues))


def check_fused_generation_submission_schema(catalog: CatalogReader) -> SchemaCheck:
    """Require the dedicated durable fused-submission ledger table."""

    return _check_requirements(
        catalog,
        FUSED_GENERATION_SUBMISSION_REQUIREMENTS,
        (FUSED_GENERATION_SUBMISSIONS_TABLE_NAME,),
        reject_extra_columns=True,
    )


def check_migration_history_schema(catalog: CatalogReader) -> SchemaCheck:
    """Require the migration history key, typed columns, and nullability."""

    return _check_requirements(
        catalog,
        {GENERATION_MIGRATION_HISTORY_TABLE: MIGRATION_HISTORY_REQUIREMENT},
        (GENERATION_MIGRATION_HISTORY_TABLE,),
        reject_extra_columns=True,
    )


def check_generation_version_set(
    versions: Sequence[str], *, require_current: bool = True
) -> SchemaCheck:
    """Reject duplicate or unsupported applied migration versions."""

    issues: list[SchemaIssue] = []
    if len(versions) != len(set(versions)):
        issues.append(SchemaIssue("duplicate_generation_version", GENERATION_MIGRATION_HISTORY_TABLE, "version rows are not unique"))
    unsupported = sorted(set(versions) - set(GENERATION_SUPPORTED_VERSIONS))
    if unsupported:
        issues.append(SchemaIssue("unsupported_generation_version", GENERATION_MIGRATION_HISTORY_TABLE, ", ".join(unsupported)))
    known = set(versions) & set(GENERATION_SUPPORTED_VERSIONS)
    ordered = tuple(
        version for version in GENERATION_SUPPORTED_VERSIONS if version in known
    )
    expected_prefix = GENERATION_SUPPORTED_VERSIONS[: len(ordered)]
    if ordered != expected_prefix:
        issues.append(
            SchemaIssue(
                "generation_migration_history_gap",
                GENERATION_MIGRATION_HISTORY_TABLE,
                ", ".join(ordered),
            )
        )
    if require_current and ordered != GENERATION_SUPPORTED_VERSIONS:
        issues.append(
            SchemaIssue(
                "generation_migration_not_applied",
                GENERATION_MIGRATION_HISTORY_TABLE,
                GENERATION_CURRENT_SCHEMA_VERSION,
            )
        )
    return SchemaCheck(not issues, tuple(issues))


def sqlalchemy_catalog(connection: Connection) -> SQLAlchemyCatalog:
    return SQLAlchemyCatalog(connection)


__all__ = (
    "FoundationBase",
    "GenerationAuxiliaryBase",
    "GENERATION_SCHEMA_VERSION",
    "GENERATION_SCHEMA_CHECKSUM",
    "CANVAS_RESULT_TARGETS_SCHEMA_VERSION",
    "CANVAS_RESULT_TARGETS_SCHEMA_CHECKSUM",
    "FUSED_GENERATION_SUBMISSIONS_SCHEMA_VERSION",
    "FUSED_GENERATION_SUBMISSIONS_SCHEMA_CHECKSUM",
    "GENERATION_CURRENT_SCHEMA_VERSION",
    "GENERATION_CURRENT_SCHEMA_CHECKSUM",
    "GENERATION_MIGRATION_HISTORY_TABLE",
    "GENERATION_SUPPORTED_VERSIONS",
    "CANVAS_RESULT_TARGETS_TABLE_NAME",
    "FUSED_GENERATION_SUBMISSIONS_TABLE_NAME",
    "UserRow",
    "AITaskRow",
    "TaskQuoteRow",
    "TaskSubmissionRow",
    "BillingUnitRow",
    "ExecutionStepRow",
    "ExternalSubmissionRow",
    "ResultEvidenceRow",
    "UserCreditRow",
    "CreditLogRow",
    "UnitFeeDecisionRow",
    "GenerationResultReceiptRow",
    "GenerationResultTargetRow",
    "FusedGenerationSubmissionRow",
    "ApplicationRecordRow",
    "ExecutionAttemptGroupRow",
    "LocalContinuationWorkRow",
    "StreamFeeRecordRow",
    "PollReservationRow",
    "PolicyStopEventRow",
    "FOUNDATION_TABLE_NAMES",
    "FOUNDATION_TABLES",
    "GENERATION_RECORD_TABLE_NAMES",
    "GENERATION_RECORD_TABLES",
    "GENERATION_RECORD_REQUIREMENTS",
    "CANVAS_RESULT_TARGET_REQUIREMENTS",
    "CANVAS_RESULT_TARGET_INDEX_REQUIREMENTS",
    "FUSED_GENERATION_SUBMISSION_REQUIREMENTS",
    "FOUNDATION_REQUIREMENTS",
    "MIGRATION_HISTORY_REQUIREMENT",
    "CatalogColumn",
    "CatalogForeignKey",
    "CatalogUnique",
    "CatalogCheck",
    "IndexRequirement",
    "CatalogIndex",
    "CatalogTable",
    "CatalogReader",
    "SchemaIssue",
    "SchemaCheck",
    "SQLAlchemyCatalog",
    "check_foundation_schema",
    "check_generation_record_schema",
    "check_canvas_result_target_schema",
    "check_fused_generation_submission_schema",
    "check_migration_history_schema",
    "check_generation_version_set",
    "sqlalchemy_catalog",
)
