"""Private Core declarations for the fixed statistics source tables."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)
from sqlalchemy.orm import registry

metadata = MetaData()

users = Table(
    "users",
    metadata,
    Column("id", String(36), primary_key=True, index=True),
    Column("membership_type", String(20), nullable=False, default="free"),
    Column("membership_expires_at", DateTime, nullable=True),
    Column("created_at", DateTime, nullable=False),
)

user_credits = Table(
    "user_credits",
    metadata,
    Column("id", String(36), primary_key=True, index=True),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False, unique=True, index=True),
    Column("credits", Integer, nullable=False, default=0),
    Column("created_at", DateTime, nullable=False, default=datetime.utcnow),
    Column("updated_at", DateTime, nullable=False, default=datetime.utcnow),
)

credit_logs = Table(
    "credit_logs",
    metadata,
    Column("id", String(36), primary_key=True, index=True),
    Column("user_id", String(36), nullable=False, index=True),
    Column("amount", Integer, nullable=False),
    Column("created_at", DateTime, nullable=False, default=datetime.utcnow, index=True),
)

ai_tasks = Table(
    "ai_tasks",
    metadata,
    Column("id", String(36), primary_key=True, index=True),
    Column("status", String(20), nullable=False, default="pending", index=True),
    Column("created_at", DateTime, nullable=False, default=datetime.utcnow, index=True),
)

series = Table(
    "series",
    metadata,
    Column("id", String(36), primary_key=True, index=True),
)

chapters = Table(
    "chapters",
    metadata,
    Column("id", String(36), primary_key=True, index=True),
)

characters = Table(
    "characters",
    metadata,
    Column("id", String(36), primary_key=True, index=True),
)

scenes = Table(
    "scenes",
    metadata,
    Column("id", String(36), primary_key=True, index=True),
)

props = Table(
    "props",
    metadata,
    Column("id", String(36), primary_key=True, index=True),
)

system_configs = Table(
    "system_configs",
    metadata,
    Column("id", String(36), primary_key=True, index=True),
    Column("muse_token", String(500), nullable=False, default=""),
    Column("site_title", String(200), nullable=False, default="Hao AI - 无限可能"),
    Column("site_tagline", String(500), nullable=False, default="无限可能"),
    Column("storyboard_cost_multiplier", Integer, nullable=False, default=2),
    Column("membership_benefits", Text, nullable=False, default=""),
    Column("payments_enabled", Integer, nullable=False, default=1),
    Column("tutorial_url", String(500), nullable=False, default=""),
    Column("purchase_qr_code_url", String(500), nullable=False, default=""),
    Column("purchase_contact_info", Text, nullable=False, default=""),
    Column("oss_access_key_id", String(200), nullable=False, default=""),
    Column("oss_access_key_secret", String(200), nullable=False, default=""),
    Column("oss_bucket_name", String(100), nullable=False, default=""),
    Column("oss_endpoint", String(200), nullable=False, default=""),
    Column("fc_transfer_url", String(500), nullable=False, default=""),
    Column("fc_transfer_url_cn", String(500), nullable=False, default=""),
    Column("oss_haoai_match_domain", String(200), nullable=False, default=""),
    Column("oss_haoai_replace_domain", String(200), nullable=False, default=""),
    Column("team_created_limit", Integer, nullable=False, default=3),
    Column("team_joined_limit", Integer, nullable=False, default=3),
    Column("team_member_limit", Integer, nullable=False, default=20),
    Column("invite_code_ttl_hours", Integer, nullable=False, default=24),
    Column("chapter_lock_idle_minutes", Integer, nullable=False, default=15),
    Column("aistarslab_credit_multiplier", Float, nullable=False, default=1.0),
    Column("zhangyuge_billing_pat", Text, nullable=False, default=""),
    Column("frontend_url", String(500), nullable=False, default=""),
    Column("allowed_origins", Text, nullable=False, default=""),
    Column("upload_dir", String(500), nullable=False, default=""),
    Column("worker_max_concurrency", String(10), nullable=False, default=""),
)

_mapper_registry = registry(metadata=metadata)


class SystemConfigRow:
    """Full fixed SystemConfig projection used by the original first-row query."""

    pass


_mapper_registry.map_imperatively(SystemConfigRow, system_configs)
