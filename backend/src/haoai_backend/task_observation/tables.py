"""Minimal SQLAlchemy Core projections for task-observation reads."""

from sqlalchemy import Boolean, Column, DateTime, Integer, JSON, MetaData, String, Table, Text

metadata = MetaData()

ai_tasks = Table(
    "ai_tasks",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), nullable=False),
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
    Column("billing_status", String(32), nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)

billing_units = Table(
    "billing_units",
    metadata,
    Column("task_id", String(36), nullable=False),
    Column("quoted_amount", Integer, nullable=False),
    Column("billing_status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

task_submissions = Table(
    "task_submissions",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), nullable=False),
    Column("operation", String(64), nullable=False),
    Column("idempotency_key", String(255), nullable=False),
    Column("task_ids", JSON, nullable=False),
)

users = Table(
    "users",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("is_superuser", Boolean, nullable=False),
)

team_members = Table(
    "team_members",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("team_id", String(36), nullable=False),
    Column("user_id", String(36), nullable=False),
    Column("role", String(20), nullable=False),
    Column("permissions", Text, nullable=True),
)

chat_messages = Table(
    "chat_messages",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("chapter_id", String(36), nullable=False),
    Column("frame_index", Integer, nullable=True),
    Column("asset_type", String(20), nullable=True),
    Column("asset_id", String(36), nullable=True),
)

chapters = Table(
    "chapters",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("title", String(255), nullable=False),
)

characters = Table(
    "characters",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("name", String(255), nullable=False),
)

scenes = Table(
    "scenes",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("title", String(255), nullable=False),
)

props = Table(
    "props",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("name", String(255), nullable=False),
)

storyboard_assets = Table(
    "storyboard_assets",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("name", String(255), nullable=False),
)

asset_tables = {
    "character": (characters, "name"),
    "scene": (scenes, "title"),
    "prop": (props, "name"),
    "storyboard": (storyboard_assets, "name"),
}
