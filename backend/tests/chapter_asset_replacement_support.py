"""Complete test-only owner schema and SQLite fixtures for chapter replacement."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

import pytest
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    insert,
    select,
)
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session, sessionmaker

OWNER_METADATA = MetaData()

users = Table(
    "users",
    OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("username", String(100), nullable=False),
    Column("email", String(255), nullable=False),
    Column("hashed_password", String(255), nullable=False),
    Column("is_superuser", Boolean, nullable=False),
    Column("membership_type", String(20), nullable=False),
    Column("membership_expires_at", DateTime, nullable=True),
    Column("avatar_url", String(500), nullable=True),
    Column("bio", Text, nullable=True),
    Column("created_at", DateTime, nullable=False),
    Column("password_updated_at", DateTime, nullable=True),
    UniqueConstraint("username", name="uq_users_username"),
    UniqueConstraint("email", name="uq_users_email"),
)
Index("ix_users_username", users.c.username)
Index("ix_users_email", users.c.email)

user_sessions = Table(
    "user_sessions",
    OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("jti", String(128), nullable=False, unique=True),
    Column("user_agent", Text, nullable=True),
    Column("ip_address", String(64), nullable=True),
    Column("device_name", String(200), nullable=True),
    Column("is_active", Boolean, nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("last_seen_at", DateTime, nullable=False),
    Column("revoked_at", DateTime, nullable=True),
)
Index("ix_user_sessions_user_id", user_sessions.c.user_id)
Index("ix_user_sessions_jti", user_sessions.c.jti)

teams = Table(
    "teams",
    OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("name", String(255), nullable=False),
    Column("owner_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("created_at", DateTime, nullable=False),
)
Index("ix_teams_owner_id", teams.c.owner_id)

team_members = Table(
    "team_members",
    OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("team_id", String(36), ForeignKey("teams.id"), nullable=False),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("role", String(20), nullable=False),
    Column("permissions", Text, nullable=True),
    Column("joined_at", DateTime, nullable=False),
    UniqueConstraint("team_id", "user_id", name="uq_team_member"),
)
Index("ix_team_members_team_id", team_members.c.team_id)
Index("ix_team_members_user_id", team_members.c.user_id)

series = Table(
    "series",
    OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("name", String(255), nullable=False),
    Column("description", Text, nullable=True),
    Column("image_url", String(500), nullable=True),
    Column("style_prompt_id", String(36), nullable=True),
    Column("team_id", String(36), ForeignKey("teams.id"), nullable=True),
    Column("claimed_by", String(36), nullable=True),
    Column("claimed_at", DateTime, nullable=True),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)
Index("ix_series_user_id", series.c.user_id)
Index("ix_series_name", series.c.name)
Index("ix_series_team_id", series.c.team_id)
Index("ix_series_claimed_by", series.c.claimed_by)

chapters = Table(
    "chapters",
    OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("series_id", String(36), ForeignKey("series.id"), nullable=False),
    Column("title", String(255), nullable=False),
    Column("content", Text, nullable=True),
    Column("order", Integer, nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)
Index("ix_chapters_series_id", chapters.c.series_id)

def _asset_naming_columns() -> tuple[Column[Any], Column[Any]]:
    return (
        Column("aliases", Text, nullable=True),
        Column("canonical_key", String(255), nullable=True),
    )

characters = Table(
    "characters",
    OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("series_id", String(36), ForeignKey("series.id"), nullable=False),
    Column("name", String(255), nullable=False),
    Column("gender", String(20), nullable=True),
    Column("age", String(100), nullable=True),
    Column("role", String(50), nullable=True),
    Column("appearance", Text, nullable=True),
    Column("description", Text, nullable=True),
    Column("image_url", String(500), nullable=True),
    Column("audio_url", String(500), nullable=True),
    Column("voice_ref", Text, nullable=True),
    *_asset_naming_columns(),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)
Index("ix_characters_series_id", characters.c.series_id)
Index("ix_characters_canonical_key", characters.c.canonical_key)

scenes = Table(
    "scenes",
    OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("series_id", String(36), ForeignKey("series.id"), nullable=False),
    Column("title", String(255), nullable=False),
    Column("description", Text, nullable=True),
    Column("image_url", String(500), nullable=True),
    *_asset_naming_columns(),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)
Index("ix_scenes_series_id", scenes.c.series_id)
Index("ix_scenes_canonical_key", scenes.c.canonical_key)

props = Table(
    "props",
    OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("series_id", String(36), ForeignKey("series.id"), nullable=False),
    Column("name", String(255), nullable=False),
    Column("description", Text, nullable=True),
    Column("image_url", String(500), nullable=True),
    *_asset_naming_columns(),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)
Index("ix_props_series_id", props.c.series_id)
Index("ix_props_canonical_key", props.c.canonical_key)

storyboard_assets = Table(
    "storyboard_assets",
    OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("series_id", String(36), ForeignKey("series.id"), nullable=False),
    Column("chapter_id", String(36), ForeignKey("chapters.id"), nullable=False),
    Column("frame_index", Integer, nullable=False),
    Column("name", String(255), nullable=False),
    Column("description", Text, nullable=True),
    Column("image_url", String(500), nullable=True),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)
Index("ix_storyboard_assets_series_id", storyboard_assets.c.series_id)
Index("ix_storyboard_assets_chapter_id", storyboard_assets.c.chapter_id)

chapter_locks = Table(
    "chapter_locks",
    OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("chapter_id", String(36), ForeignKey("chapters.id"), nullable=False),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("username", String(100), nullable=False),
    Column("acquired_at", DateTime, nullable=False),
    Column("last_active_at", DateTime, nullable=False),
    Column("expires_at", DateTime, nullable=False),
    UniqueConstraint("chapter_id", name="uq_chapter_lock"),
)
Index("ix_chapter_locks_chapter_id", chapter_locks.c.chapter_id)

storyboard_media_states = Table(
    "storyboard_media_states",
    OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("chapter_id", String(36), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False),
    Column("storyboard_asset_id", String(36), nullable=False),
    Column("media_revision", Integer, nullable=False, server_default="1"),
    Column("asset_image_digest", String(64), nullable=True),
    Column("preview_digest", String(64), nullable=True),
    Column("source_valid", Boolean, nullable=False, server_default="true"),
    Column("created_at", DateTime, nullable=False, server_default="CURRENT_TIMESTAMP"),
    Column("updated_at", DateTime, nullable=False, server_default="CURRENT_TIMESTAMP"),
    UniqueConstraint("chapter_id", "storyboard_asset_id", name="uq_storyboard_media_states_chapter_asset"),
    CheckConstraint("media_revision > 0", name="ck_storyboard_media_states_revision_positive"),
)
Index("ix_storyboard_media_states_chapter_id", storyboard_media_states.c.chapter_id)

personal_production_notes = Table(
    "personal_production_notes",
    OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("chapter_id", String(36), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False),
    Column("user_id", String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("revision", Integer, nullable=False, server_default="1"),
    Column("frame_notes", JSON, nullable=False, server_default="'{}'"),
    Column("resume_frame_id", String(36), nullable=True),
    Column("created_at", DateTime, nullable=False, server_default="CURRENT_TIMESTAMP"),
    Column("updated_at", DateTime, nullable=False, server_default="CURRENT_TIMESTAMP"),
    UniqueConstraint("chapter_id", "user_id", name="uq_personal_production_notes_chapter_user"),
    CheckConstraint("revision > 0", name="ck_personal_production_notes_revision_positive"),
)
Index("ix_personal_production_notes_chapter_id", personal_production_notes.c.chapter_id)
Index("ix_personal_production_notes_user_id", personal_production_notes.c.user_id)

rough_cut_drafts = Table(
    "rough_cut_drafts",
    OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("chapter_id", String(36), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False),
    Column("user_id", String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("frames", JSON, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    UniqueConstraint("chapter_id", "user_id", name="uq_rough_cut_drafts_chapter_user"),
    CheckConstraint("revision > 0", name="ck_rough_cut_drafts_revision_positive"),
)
Index("ix_rough_cut_drafts_chapter_id", rough_cut_drafts.c.chapter_id)
Index("ix_rough_cut_drafts_user_id", rough_cut_drafts.c.user_id)

chat_messages = Table(
    "chat_messages",
    OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("chapter_id", String(36), ForeignKey("chapters.id"), nullable=False),
    Column("frame_index", Integer, nullable=True),
    Column("asset_type", String(20), nullable=True),
    Column("asset_id", String(36), nullable=True),
    Column("chat_mode", String(20), nullable=False),
    Column("role", String(20), nullable=False),
    Column("content", Text, nullable=False),
    Column("model_name", String(255), nullable=True),
    Column("created_at", DateTime, nullable=False),
)
Index("ix_chat_messages_chapter_id", chat_messages.c.chapter_id)
Index("ix_chat_messages_frame_index", chat_messages.c.frame_index)
Index("ix_chat_messages_asset_type", chat_messages.c.asset_type)
Index("ix_chat_messages_asset_id", chat_messages.c.asset_id)
Index("ix_chat_messages_chat_mode", chat_messages.c.chat_mode)

ai_tasks = Table(
    "ai_tasks",
    OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("type", String(20), nullable=False),
    Column("message_id", String(36), nullable=False),
    Column("status", String(20), nullable=False),
    Column("credit_cost", Integer, nullable=False),
    Column("result", Text, nullable=True),
    Column("request_data", Text, nullable=True),
    Column("model_name", String(255), nullable=True),
    Column("progress", Integer, nullable=False),
    Column("progress_message", Text, nullable=True),
    Column("external_task_id", String(255), nullable=True),
    Column("external_provider", String(50), nullable=True),
    Column("claimed_by", String(64), nullable=True),
    Column("lease_until", DateTime, nullable=True),
    Column("execution_generation", Integer, nullable=False, server_default="0"),
    Column("claim_token", String(64), nullable=True),
    Column("recovery_status", String(32), nullable=False, server_default="ready"),
    Column("billing_status", String(32), nullable=False, server_default="unbilled"),
    Column("user_cancelled_at", DateTime(timezone=True), nullable=True),
    Column("cancellation_reason", String(64), nullable=True),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    CheckConstraint(
        "billing_status IN ('legacy_unreconciled', 'unbilled', 'charged', "
        "'partially_refunded', 'refunded', 'no_charge')",
        name="ck_ai_tasks_billing_status",
    ),
)
Index("ix_ai_tasks_user_id", ai_tasks.c.user_id)
Index("ix_ai_tasks_type", ai_tasks.c.type)
Index("ix_ai_tasks_message_id", ai_tasks.c.message_id)
Index("ix_ai_tasks_external_task_id", ai_tasks.c.external_task_id)

user_credits = Table(
    "user_credits",
    OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("credits", Integer, nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    UniqueConstraint("user_id", name="uq_user_credits_user_id"),
)
Index("ix_user_credits_user_id", user_credits.c.user_id)

# Declare task quotes before task submissions and billing rows so the owner FKs
# are represented without relying on any production projection metadata.
task_quotes = Table(
    "task_quotes",
    OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("operation", String(64), nullable=False),
    Column("idempotency_key", String(255), nullable=False),
    Column("model_source", String(32), nullable=False),
    Column("model_ref", String(255), nullable=True),
    Column("model_name", String(255), nullable=True),
    Column("unit_price", Integer, nullable=False),
    Column("unit_count", Integer, nullable=False),
    Column("total_amount", Integer, nullable=False),
    Column("input_digest", String(128), nullable=False),
    Column("candidate_digest", String(128), nullable=False),
    Column("unit_manifest", JSON, nullable=False),
    Column("status", String(24), nullable=False, server_default="quoted"),
    Column("confirmation_key", String(255), nullable=True),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("confirmed_at", DateTime(timezone=True), nullable=True),
    Column("consumed_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default="CURRENT_TIMESTAMP"),
    UniqueConstraint("user_id", "operation", "idempotency_key", name="uq_task_quotes_user_operation_key"),
    UniqueConstraint("user_id", "confirmation_key", name="uq_task_quotes_user_confirmation_key"),
    CheckConstraint("unit_price >= 0", name="ck_task_quotes_unit_price_nonnegative"),
    CheckConstraint("unit_count >= 0", name="ck_task_quotes_unit_count_nonnegative"),
    CheckConstraint("total_amount >= 0", name="ck_task_quotes_total_nonnegative"),
    CheckConstraint("total_amount = unit_price * unit_count", name="ck_task_quotes_total_matches_units"),
)
Index("ix_task_quotes_user_id", task_quotes.c.user_id)

task_submissions = Table(
    "task_submissions",
    OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("operation", String(64), nullable=False),
    Column("idempotency_key", String(255), nullable=False),
    Column("request_digest", String(128), nullable=False),
    Column("task_ids", JSON, nullable=False),
    Column("task_quote_id", String(36), ForeignKey("task_quotes.id"), nullable=True),
    Column("status", String(24), nullable=False, server_default="accepted"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default="CURRENT_TIMESTAMP"),
    UniqueConstraint("user_id", "operation", "idempotency_key", name="uq_task_submissions_user_operation_key"),
    UniqueConstraint("task_quote_id", name="uq_task_submissions_task_quote_id"),
    CheckConstraint("length(operation) > 0", name="ck_task_submissions_operation_nonempty"),
)
Index("ix_task_submissions_user_id", task_submissions.c.user_id)

billing_units = Table(
    "billing_units",
    OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("task_id", String(36), ForeignKey("ai_tasks.id"), nullable=False),
    Column("item_key", String(255), nullable=False),
    Column("operation", String(64), nullable=False),
    Column("step_graph_version", String(64), nullable=False),
    Column("input_digest", String(128), nullable=False),
    Column("quoted_amount", Integer, nullable=False),
    Column("debited_amount", Integer, nullable=False, server_default="0"),
    Column("refunded_amount", Integer, nullable=False, server_default="0"),
    Column("billing_status", String(32), nullable=False, server_default="unbilled"),
    Column("result_status", String(32), nullable=False, server_default="pending"),
    Column("first_intent_at", DateTime(timezone=True), nullable=True),
    Column("deadline_at", DateTime(timezone=True), nullable=True),
    Column("success_confirmed_at", DateTime(timezone=True), nullable=True),
    Column("user_cancelled_at", DateTime(timezone=True), nullable=True),
    Column("cancellation_reason", String(64), nullable=True),
    Column("quote_id", String(36), ForeignKey("task_quotes.id"), nullable=True),
    Column("quote_unit_key", String(255), nullable=True),
    Column("candidate_digest", String(128), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default="CURRENT_TIMESTAMP"),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default="CURRENT_TIMESTAMP"),
    UniqueConstraint("task_id", "item_key", name="uq_billing_units_task_item"),
    UniqueConstraint("quote_id", "quote_unit_key", name="uq_billing_units_quote_item"),
    CheckConstraint("quoted_amount >= 0", name="ck_billing_units_quoted_nonnegative"),
    CheckConstraint("debited_amount >= 0", name="ck_billing_units_debited_nonnegative"),
    CheckConstraint("refunded_amount >= 0", name="ck_billing_units_refunded_nonnegative"),
    CheckConstraint("refunded_amount <= debited_amount", name="ck_billing_units_refund_not_over_debit"),
    CheckConstraint("billing_status IN ('unbilled', 'charged', 'refunded', 'no_charge')", name="ck_billing_units_billing_status"),
    CheckConstraint("result_status IN ('pending', 'partial', 'succeeded', 'failed', 'unknown')", name="ck_billing_units_result_status"),
)
Index("ix_billing_units_task_id", billing_units.c.task_id)
Index("ix_billing_units_quote_id", billing_units.c.quote_id)

credit_logs = Table(
    "credit_logs",
    OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("amount", Integer, nullable=False),
    Column("balance_after", Integer, nullable=False),
    Column("type", String(20), nullable=False),
    Column("description", String(500), nullable=True),
    Column("task_id", String(36), nullable=True),
    Column("business_key", String(255), nullable=True),
    Column("billing_unit_id", String(36), ForeignKey("billing_units.id", name="fk_credit_logs_billing_unit_id_billing_units"), nullable=True),
    Column("related_debit_id", String(36), ForeignKey("credit_logs.id", name="fk_credit_logs_related_debit_id_credit_logs"), nullable=True),
    Column("created_at", DateTime, nullable=False),
    UniqueConstraint("business_key", name="uq_credit_logs_business_key"),
)
Index("ix_credit_logs_user_id", credit_logs.c.user_id)
Index("ix_credit_logs_type", credit_logs.c.type)
Index("ix_credit_logs_task_id", credit_logs.c.task_id)
Index("ix_credit_logs_billing_unit_id", credit_logs.c.billing_unit_id)
Index("ix_credit_logs_related_debit_id", credit_logs.c.related_debit_id)

execution_steps = Table(
    "execution_steps",
    OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("billing_unit_id", String(36), ForeignKey("billing_units.id"), nullable=False),
    Column("step_key", String(128), nullable=False),
    Column("dependencies", JSON, nullable=False, server_default="'[]'"),
    Column("input_digest", String(128), nullable=False),
    Column("input_snapshot", JSON, nullable=True),
    Column("required", Boolean, nullable=False, server_default="true"),
    Column("step_order", Integer, nullable=False, server_default="0"),
    Column("execution_status", String(32), nullable=False, server_default="prepared"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default="CURRENT_TIMESTAMP"),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default="CURRENT_TIMESTAMP"),
    UniqueConstraint("billing_unit_id", "step_key", name="uq_execution_steps_unit_key"),
    CheckConstraint("step_order >= 0", name="ck_execution_steps_order_nonnegative"),
    CheckConstraint(
        "execution_status IN ('prepared', 'submitting', 'submitted', 'result_observed', "
        "'applied', 'failed', 'reconciling', 'deterministic_empty')",
        name="ck_execution_steps_status",
    ),
)
Index("ix_execution_steps_billing_unit_id", execution_steps.c.billing_unit_id)

external_submissions = Table(
    "external_submissions",
    OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("step_id", String(36), ForeignKey("execution_steps.id"), nullable=False),
    Column("submission_key", String(255), nullable=False),
    Column("provider", String(64), nullable=False),
    Column("model_ref", String(255), nullable=True),
    Column("request_digest", String(128), nullable=False),
    Column("request_snapshot", JSON, nullable=False),
    Column("intent_at", DateTime(timezone=True), nullable=False),
    Column("submitted_at", DateTime(timezone=True), nullable=True),
    Column("external_task_id", String(255), nullable=True),
    Column("submission_status", String(32), nullable=False, server_default="submitting"),
    Column("deadline_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default="CURRENT_TIMESTAMP"),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default="CURRENT_TIMESTAMP"),
    UniqueConstraint("step_id", name="uq_external_submissions_step_id"),
    UniqueConstraint("submission_key", name="uq_external_submissions_submission_key"),
)
Index("ix_external_submissions_external_task_id", external_submissions.c.external_task_id)

result_evidence = Table(
    "result_evidence",
    OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("step_id", String(36), ForeignKey("execution_steps.id"), nullable=False),
    Column("submission_id", String(36), ForeignKey("external_submissions.id"), nullable=True),
    Column("evidence_key", String(255), nullable=False),
    Column("source", String(64), nullable=False, server_default="provider"),
    Column("evidence_kind", String(64), nullable=False),
    Column("payload_digest", String(128), nullable=False),
    Column("result_location", Text, nullable=True),
    Column("result_digest", String(128), nullable=True),
    Column("received_at", DateTime(timezone=True), nullable=False, server_default="CURRENT_TIMESTAMP"),
    Column("validity", String(24), nullable=False, server_default="unknown"),
    Column("application_status", String(24), nullable=False, server_default="pending"),
    Column("applied_at", DateTime(timezone=True), nullable=True),
    UniqueConstraint("evidence_key", name="uq_result_evidence_evidence_key"),
    CheckConstraint("submission_id IS NOT NULL OR evidence_kind = 'deterministic_empty'", name="ck_result_evidence_submission_or_empty"),
    CheckConstraint("validity IN ('valid', 'invalid', 'unknown')", name="ck_result_evidence_validity"),
    CheckConstraint("application_status IN ('pending', 'applied', 'rejected')", name="ck_result_evidence_application_status"),
)
Index("ix_result_evidence_step_id", result_evidence.c.step_id)
Index("ix_result_evidence_submission_id", result_evidence.c.submission_id)

OWNER_TABLES: Mapping[str, Table] = {
    table.name: table
    for table in OWNER_METADATA.sorted_tables
}
CONSERVATION_TABLES = (
    "users",
    "user_sessions",
    "teams",
    "team_members",
    "series",
    "chapters",
    "characters",
    "scenes",
    "props",
    "storyboard_assets",
    "chapter_locks",
    "storyboard_media_states",
    "personal_production_notes",
    "rough_cut_drafts",
    "chat_messages",
    "ai_tasks",
    "user_credits",
    "credit_logs",
    "task_quotes",
    "task_submissions",
    "billing_units",
    "execution_steps",
    "external_submissions",
    "result_evidence",
)

class TrackedSession(Session):
    """A Session that exposes whether its owner closed it exactly once."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        super().close()


class TrackingSessionFactory:
    def __init__(self, engine: Engine) -> None:
        self.created: list[TrackedSession] = []
        self._factory = sessionmaker(
            bind=engine,
            class_=TrackedSession,
            autoflush=False,
            expire_on_commit=True,
            future=True,
        )

    def __call__(self) -> TrackedSession:
        session = self._factory()
        self.created.append(session)
        return session


@dataclass
class OwnerDatabase:
    engine: Engine
    session_factory: TrackingSessionFactory
    path: Path
    _foreign_key_listener: Any

    def snapshot(
        self,
        table_names: Sequence[str] = CONSERVATION_TABLES,
    ) -> dict[str, list[dict[str, object]]]:
        with self.engine.connect() as connection:
            return {
                name: [
                    dict(row)
                    for row in connection.execute(
                        select(OWNER_TABLES[name]).order_by(*OWNER_TABLES[name].primary_key)
                    ).mappings()
                ]
                for name in table_names
            }

    def assert_foreign_keys_enabled(self) -> None:
        with self.engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1

    def seed_replacement_assets(self) -> dict[str, tuple[str, str]]:
        """Ensure old/new owner rows exist for all three supported categories."""
        now = datetime(2026, 10, 9, 12, 0, 0)
        asset_rows: dict[str, tuple[Table, tuple[dict[str, object], ...]]] = {
            "character": (
                characters,
                tuple(
                    {
                        "id": asset_id,
                        "series_id": "series-a",
                        "name": name,
                        "gender": "未指定",
                        "age": None,
                        "role": None,
                        "appearance": None,
                        "description": f"完整角色行 {asset_id}",
                        "image_url": None,
                        "audio_url": None,
                        "voice_ref": None,
                        "aliases": "[]",
                        "canonical_key": None,
                        "created_at": now,
                        "updated_at": now,
                    }
                    for asset_id, name in (
                        ("old-character", "旧角色"),
                        ("new-character", "新角色"),
                        ("other-character", "无关角色"),
                    )
                ),
            ),
            "scene": (
                scenes,
                tuple(
                    {
                        "id": asset_id,
                        "series_id": "series-a",
                        "title": title,
                        "description": f"完整场景行 {asset_id}",
                        "image_url": None,
                        "aliases": "[]",
                        "canonical_key": None,
                        "created_at": now,
                        "updated_at": now,
                    }
                    for asset_id, title in (("old-scene", "旧场景"), ("new-scene", "新场景"))
                ),
            ),
            "prop": (
                props,
                tuple(
                    {
                        "id": asset_id,
                        "series_id": "series-a",
                        "name": name,
                        "description": f"完整道具行 {asset_id}",
                        "image_url": None,
                        "aliases": "[]",
                        "canonical_key": None,
                        "created_at": now,
                        "updated_at": now,
                    }
                    for asset_id, name in (("old-prop", "旧道具"), ("new-prop", "新道具"))
                ),
            ),
        }
        result: dict[str, tuple[str, str]] = {}
        with self.engine.begin() as connection:
            for asset_type, (table, rows) in asset_rows.items():
                for row in rows:
                    exists = connection.execute(
                        select(table.c.id).where(table.c.id == row["id"])
                    ).first()
                    if exists is None:
                        connection.execute(insert(table).values(**row))
                ids = tuple(str(row["id"]) for row in rows if not str(row["id"]).startswith("other-"))
                result[asset_type] = (ids[0], ids[1])
        return result

    def close(self, *, remove_files: bool = True) -> None:
        if getattr(self, "_closed", False):
            return
        unclosed = [
            session for session in self.session_factory.created
            if session.close_calls != 1
        ]
        for session in self.session_factory.created:
            if session.close_calls == 0:
                session.close()
        try:
            self.engine.dispose()
        finally:
            event.remove(self.engine, "connect", self._foreign_key_listener)
            if remove_files:
                for suffix in ("", "-journal", "-wal", "-shm"):
                    Path(f"{self.path}{suffix}").unlink(missing_ok=True)
            self._closed = True
        assert not unclosed, "all fixture-owned Sessions must be closed exactly once"

    def seed_complete_fixture(self) -> None:
        now = datetime(2026, 10, 9, 12, 0, 0)
        image_url = "https://media.invalid/storyboard-a.png"
        preview_url = "https://media.invalid/preview-a.png"
        image_digest = hashlib.sha256(image_url.encode("utf-8")).hexdigest()
        preview_digest = hashlib.sha256(preview_url.encode("utf-8")).hexdigest()
        chapter_content = [
            {
                "storyboard": ["storyboard-a", "ignored-secondary-id"],
                "preview": preview_url,
                "text": "待替换镜头",
                "character": ["old-character", "old-character"],
                "scene": ["old-scene"],
                "prop": ["old-prop"],
                "unknown_field": {"preserve": True, "label": "保留"},
            }
        ]
        with self.engine.begin() as connection:
            connection.execute(
                insert(users),
                [
                    {
                        "id": "user-a",
                        "username": "owner-a",
                        "email": "owner-a@example.test",
                        "hashed_password": "test-hash-a",
                        "is_superuser": False,
                        "membership_type": "premium",
                        "membership_expires_at": None,
                        "avatar_url": None,
                        "bio": "owner fixture A",
                        "created_at": now,
                        "password_updated_at": now,
                    },
                    {
                        "id": "user-b",
                        "username": "member-b",
                        "email": "member-b@example.test",
                        "hashed_password": "test-hash-b",
                        "is_superuser": False,
                        "membership_type": "premium",
                        "membership_expires_at": None,
                        "avatar_url": None,
                        "bio": "owner fixture B",
                        "created_at": now,
                        "password_updated_at": now,
                    },
                ],
            )
            connection.execute(
                insert(user_sessions),
                [
                    {
                        "id": "session-a",
                        "user_id": "user-a",
                        "jti": "test-jti-a",
                        "user_agent": "chapter-replacement fixture",
                        "ip_address": "127.0.0.1",
                        "device_name": "fixture device A",
                        "is_active": True,
                        "created_at": now,
                        "last_seen_at": now,
                        "revoked_at": None,
                    },
                    {
                        "id": "session-b",
                        "user_id": "user-b",
                        "jti": "test-jti-b",
                        "user_agent": "chapter-replacement fixture",
                        "ip_address": "127.0.0.1",
                        "device_name": "fixture device B",
                        "is_active": True,
                        "created_at": now,
                        "last_seen_at": now,
                        "revoked_at": None,
                    },
                ],
            )
            connection.execute(
                insert(teams).values(
                    id="team-a", name="测试团队", owner_id="user-a", created_at=now
                )
            )
            connection.execute(
                insert(team_members),
                [
                    {
                        "id": "team-member-a",
                        "team_id": "team-a",
                        "user_id": "user-a",
                        "role": "owner",
                        "permissions": None,
                        "joined_at": now,
                    },
                    {
                        "id": "team-member-b",
                        "team_id": "team-a",
                        "user_id": "user-b",
                        "role": "member",
                        "permissions": None,
                        "joined_at": now,
                    },
                ],
            )
            connection.execute(
                insert(series),
                [
                    {
                        "id": "series-a",
                        "user_id": "user-a",
                        "name": "测试剧集 A",
                        "description": "完整 owner fixture",
                        "image_url": None,
                        "style_prompt_id": None,
                        "team_id": "team-a",
                        "claimed_by": None,
                        "claimed_at": None,
                        "created_at": now,
                        "updated_at": now,
                    },
                    {
                        "id": "series-b",
                        "user_id": "user-b",
                        "name": "测试剧集 B",
                        "description": "跨剧引用边界",
                        "image_url": None,
                        "style_prompt_id": None,
                        "team_id": None,
                        "claimed_by": None,
                        "claimed_at": None,
                        "created_at": now,
                        "updated_at": now,
                    },
                ],
            )
            connection.execute(
                insert(chapters),
                [
                    {
                        "id": "chapter-a",
                        "series_id": "series-a",
                        "title": "待替换章节",
                        "content": json.dumps(chapter_content, ensure_ascii=False),
                        "order": 1,
                        "created_at": now,
                        "updated_at": now,
                    },
                    {
                        "id": "neighbor-a",
                        "series_id": "series-a",
                        "title": "同剧邻章",
                        "content": json.dumps(
                            [{"storyboard": [], "character": ["other-character"]}],
                            ensure_ascii=False,
                        ),
                        "order": 2,
                        "created_at": now,
                        "updated_at": now,
                    },
                    {
                        "id": "chapter-b",
                        "series_id": "series-b",
                        "title": "跨剧邻章",
                        "content": json.dumps(
                            [{"storyboard": [], "character": ["old-character"]}],
                            ensure_ascii=False,
                        ),
                        "order": 1,
                        "created_at": now,
                        "updated_at": now,
                    },
                ],
            )
            connection.execute(
                insert(storyboard_assets).values(
                    id="storyboard-a",
                    series_id="series-a",
                    chapter_id="chapter-a",
                    frame_index=0,
                    name="镜头 A",
                    description="不可因类别素材替换而改写",
                    image_url=image_url,
                    created_at=now,
                    updated_at=now,
                )
            )
            connection.execute(
                insert(chapter_locks).values(
                    id="foreign-lock-a",
                    chapter_id="chapter-a",
                    user_id="user-b",
                    username="另一个编辑者",
                    acquired_at=now - timedelta(hours=2),
                    last_active_at=now - timedelta(hours=1),
                    expires_at=now - timedelta(minutes=30),
                )
            )
            connection.execute(
                insert(storyboard_media_states).values(
                    id="media-state-a",
                    chapter_id="chapter-a",
                    storyboard_asset_id="storyboard-a",
                    media_revision=1,
                    asset_image_digest=image_digest,
                    preview_digest=preview_digest,
                    source_valid=True,
                    created_at=now,
                    updated_at=now,
                )
            )
            connection.execute(
                insert(personal_production_notes),
                [
                    {
                        "id": "notes-a",
                        "chapter_id": "chapter-a",
                        "user_id": "user-a",
                        "revision": 7,
                        "frame_notes": {
                            "storyboard-a": {
                                "status": "approved",
                                "note": "已确认",
                                "approved_media_revision": 1,
                                "needs_reconfirmation": False,
                                "future_field": "完整保留",
                            },
                            "legacy-unknown-a": {"status": "unknown", "note": "旧版状态"},
                        },
                        "resume_frame_id": "storyboard-a",
                        "created_at": now,
                        "updated_at": now,
                    },
                    {
                        "id": "notes-b",
                        "chapter_id": "chapter-a",
                        "user_id": "user-b",
                        "revision": 11,
                        "frame_notes": {
                            "storyboard-a": {
                                "status": "unknown",
                                "note": "待确认",
                                "opaque_value": {"keep": [1, 2, 3]},
                            },
                            "legacy-unknown-b": {"status": "unknown", "note": "保留"},
                        },
                        "resume_frame_id": "storyboard-a",
                        "created_at": now,
                        "updated_at": now,
                    },
                ],
            )
            connection.execute(
                insert(rough_cut_drafts),
                [
                    {
                        "id": f"rough-cut-{user_id}",
                        "chapter_id": "chapter-a",
                        "user_id": user_id,
                        "revision": revision,
                        "frames": ["storyboard-a"],
                        "updated_at": now,
                    }
                    for user_id, revision in (("user-a", 3), ("user-b", 4))
                ],
            )
            connection.execute(
                insert(chat_messages).values(
                    id="chat-a",
                    chapter_id="chapter-a",
                    frame_index=0,
                    asset_type="character",
                    asset_id="old-character",
                    chat_mode="chat",
                    role="assistant",
                    content="请保留这条聊天历史",
                    model_name="test-model",
                    created_at=now,
                )
            )
            connection.execute(
                insert(ai_tasks).values(
                    id="task-a",
                    user_id="user-a",
                    type="image",
                    message_id="chat-a",
                    status="processing",
                    credit_cost=9,
                    result=None,
                    request_data='{"source":"preservation-fixture"}',
                    model_name="test-model",
                    progress=63,
                    progress_message="任务历史保留",
                    external_task_id="external-task-a",
                    external_provider="test-provider",
                    claimed_by="worker-a",
                    lease_until=now + timedelta(minutes=5),
                    execution_generation=4,
                    claim_token="claim-token-a",
                    recovery_status="ready",
                    billing_status="charged",
                    user_cancelled_at=None,
                    cancellation_reason=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            connection.execute(
                insert(user_credits),
                [
                    {"id": "credits-a", "user_id": "user-a", "credits": 231, "created_at": now, "updated_at": now},
                    {"id": "credits-b", "user_id": "user-b", "credits": 178, "created_at": now, "updated_at": now},
                ],
            )
            connection.execute(
                insert(task_quotes).values(
                    id="quote-a",
                    user_id="user-a",
                    operation="image.generate",
                    idempotency_key="quote-key-a",
                    model_source="user",
                    model_ref="model-a",
                    model_name="test-model",
                    unit_price=9,
                    unit_count=1,
                    total_amount=9,
                    input_digest="input-digest-a",
                    candidate_digest="candidate-digest-a",
                    unit_manifest=[{"item": "frame-a", "amount": 9}],
                    status="consumed",
                    confirmation_key="confirm-a",
                    expires_at=now + timedelta(days=1),
                    confirmed_at=now,
                    consumed_at=now,
                    created_at=now,
                )
            )
            connection.execute(
                insert(billing_units).values(
                    id="billing-a",
                    task_id="task-a",
                    item_key="frame-a",
                    operation="image.generate",
                    step_graph_version="v1",
                    input_digest="input-digest-a",
                    quoted_amount=9,
                    debited_amount=9,
                    refunded_amount=0,
                    billing_status="charged",
                    result_status="partial",
                    first_intent_at=now,
                    deadline_at=now + timedelta(hours=1),
                    success_confirmed_at=None,
                    user_cancelled_at=None,
                    cancellation_reason=None,
                    quote_id="quote-a",
                    quote_unit_key="frame-a",
                    candidate_digest="candidate-digest-a",
                    created_at=now,
                    updated_at=now,
                )
            )
            connection.execute(
                insert(task_submissions).values(
                    id="submission-batch-a",
                    user_id="user-a",
                    operation="image.generate",
                    idempotency_key="submission-key-a",
                    request_digest="request-digest-a",
                    task_ids=["task-a"],
                    task_quote_id="quote-a",
                    status="accepted",
                    created_at=now,
                )
            )
            connection.execute(
                insert(execution_steps).values(
                    id="step-a",
                    billing_unit_id="billing-a",
                    step_key="provider-submit",
                    dependencies=[],
                    input_digest="input-digest-a",
                    input_snapshot={"frame": "frame-a"},
                    required=True,
                    step_order=0,
                    execution_status="submitted",
                    created_at=now,
                    updated_at=now,
                )
            )
            connection.execute(
                insert(external_submissions).values(
                    id="external-submission-a",
                    step_id="step-a",
                    submission_key="external-submit-key-a",
                    provider="test-provider",
                    model_ref="model-a",
                    request_digest="request-digest-a",
                    request_snapshot={"task_id": "task-a"},
                    intent_at=now,
                    submitted_at=now,
                    external_task_id="external-task-a",
                    submission_status="submitted",
                    deadline_at=now + timedelta(hours=1),
                    created_at=now,
                    updated_at=now,
                )
            )
            connection.execute(
                insert(result_evidence).values(
                    id="result-evidence-a",
                    step_id="step-a",
                    submission_id="external-submission-a",
                    evidence_key="evidence-key-a",
                    source="provider",
                    evidence_kind="image-result",
                    payload_digest="payload-digest-a",
                    result_location="https://media.invalid/result-a.png",
                    result_digest="result-digest-a",
                    received_at=now,
                    validity="unknown",
                    application_status="pending",
                    applied_at=None,
                )
            )
            connection.execute(
                insert(credit_logs),
                [
                    {
                        "id": "credit-log-a",
                        "user_id": "user-a",
                        "amount": -9,
                        "balance_after": 231,
                        "type": "usage",
                        "description": "保留账务记录",
                        "task_id": "task-a",
                        "business_key": "fixture-credit-log-a",
                        "billing_unit_id": "billing-a",
                        "related_debit_id": None,
                        "created_at": now,
                    }
                ],
            )
        self.seed_replacement_assets()

    @contextmanager
    def physical_connection_pair(self) -> Iterator[tuple[Connection, Connection]]:
        first = self.engine.connect()
        second = self.engine.connect()
        try:
            first_dbapi = first.connection.dbapi_connection
            second_dbapi = second.connection.dbapi_connection
            assert first_dbapi is not second_dbapi
            yield first, second
        finally:
            second.close()
            first.close()


def _enable_foreign_keys(connection: Any, _record: Any) -> None:
    cursor = connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def create_owner_database(path: Path, *, seed: bool = True) -> OwnerDatabase:
    """Create a file-backed full-owner SQLite database for tests or local acceptance."""
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path}", future=True)
    event.listen(engine, "connect", _enable_foreign_keys)
    try:
        OWNER_METADATA.create_all(engine)
        database = OwnerDatabase(
            engine=engine,
            session_factory=TrackingSessionFactory(engine),
            path=path,
            _foreign_key_listener=_enable_foreign_keys,
        )
        database.assert_foreign_keys_enabled()
        if seed:
            database.seed_complete_fixture()
        return database
    except Exception:
        engine.dispose()
        event.remove(engine, "connect", _enable_foreign_keys)
        raise


@pytest.fixture
def owner_database(tmp_path: Path) -> Iterator[OwnerDatabase]:
    database = create_owner_database(tmp_path / "chapter-asset-replacement.sqlite")
    try:
        yield database
    finally:
        database.close()
