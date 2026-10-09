"""Complete owner-schema fixtures for the shared teams adapter tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

import pytest
from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, MetaData,
    String, Table, Text, UniqueConstraint, create_engine, event, insert,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

OWNER_METADATA = MetaData()

users = Table(
    "users", OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("username", String(100), nullable=False),
    Column("email", String(255), nullable=False),
    Column("hashed_password", String(255), nullable=False),
    Column("is_superuser", Boolean, nullable=False, default=False),
    Column("membership_type", String(20), nullable=False, default="free"),
    Column("membership_expires_at", DateTime),
    Column("avatar_url", String(500)),
    Column("bio", Text),
    Column("created_at", DateTime, nullable=False),
    Column("password_updated_at", DateTime),
)
Index("ix_users_username", users.c.username, unique=True)
Index("ix_users_email", users.c.email, unique=True)

teams = Table(
    "teams", OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("name", String(255), nullable=False),
    Column("owner_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("created_at", DateTime, nullable=False),
)
Index("ix_teams_owner_id", teams.c.owner_id)

team_members = Table(
    "team_members", OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("team_id", String(36), ForeignKey("teams.id"), nullable=False),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("role", String(20), nullable=False, default="member"),
    Column("permissions", Text),
    Column("joined_at", DateTime, nullable=False),
    UniqueConstraint("team_id", "user_id", name="uq_team_member"),
)
Index("ix_team_members_team_id", team_members.c.team_id)
Index("ix_team_members_user_id", team_members.c.user_id)

team_invites = Table(
    "team_invites", OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("team_id", String(36), ForeignKey("teams.id"), nullable=False),
    Column("code", String(20), nullable=False),
    Column("created_by", String(36), ForeignKey("users.id"), nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("expires_at", DateTime, nullable=False),
    Column("used_by", String(36)),
    Column("used_at", DateTime),
    Column("status", String(20), nullable=False, default="active"),
)
Index("ix_team_invites_team_id", team_invites.c.team_id)
Index("ix_team_invites_code", team_invites.c.code)

chapter_locks = Table(
    "chapter_locks", OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("chapter_id", String(36), ForeignKey("chapters.id"), nullable=False),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("username", String(100), nullable=False),
    Column("acquired_at", DateTime, nullable=False),
    Column("last_active_at", DateTime, nullable=False),
    Column("expires_at", DateTime, nullable=False),
)
Index("ix_chapter_locks_chapter_id", chapter_locks.c.chapter_id, unique=True)

system_configs = Table(
    "system_configs", OWNER_METADATA,
    Column("id", String(36), primary_key=True),
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

series = Table(
    "series", OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("name", String(255), nullable=False),
    Column("description", Text),
    Column("image_url", String(500)),
    Column("style_prompt_id", String(36)),
    Column("team_id", String(36), ForeignKey("teams.id")),
    Column("claimed_by", String(36)),
    Column("claimed_at", DateTime),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)
Index("ix_series_user_id", series.c.user_id)
Index("ix_series_name", series.c.name)
Index("ix_series_team_id", series.c.team_id)
Index("ix_series_claimed_by", series.c.claimed_by)

chapters = Table(
    "chapters", OWNER_METADATA,
    Column("id", String(36), primary_key=True),
    Column("series_id", String(36), ForeignKey("series.id"), nullable=False),
    Column("title", String(255), nullable=False),
    Column("content", Text),
    Column("order", Integer, nullable=False, default=0),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)
Index("ix_chapters_series_id", chapters.c.series_id)


@dataclass(slots=True)
class TeamsDatabase:
    engine: Engine
    session_factory: sessionmaker[Session]

    def dispose(self) -> None:
        self.engine.dispose()


def _config_row(config_id: str) -> dict[str, object]:
    return {
        "id": config_id,
        "muse_token": "",
        "site_title": "Hao AI - 无限可能",
        "site_tagline": "无限可能",
        "storyboard_cost_multiplier": 2,
        "membership_benefits": "",
        "payments_enabled": 1,
        "tutorial_url": "",
        "purchase_qr_code_url": "",
        "purchase_contact_info": "",
        "oss_access_key_id": "",
        "oss_access_key_secret": "",
        "oss_bucket_name": "",
        "oss_endpoint": "",
        "fc_transfer_url": "",
        "fc_transfer_url_cn": "",
        "oss_haoai_match_domain": "",
        "oss_haoai_replace_domain": "",
        "team_created_limit": 3,
        "team_joined_limit": 3,
        "team_member_limit": 20,
        "invite_code_ttl_hours": 24,
        "chapter_lock_idle_minutes": 15,
        "aistarslab_credit_multiplier": 1.0,
        "zhangyuge_billing_pat": "",
        "frontend_url": "",
        "allowed_origins": "",
        "upload_dir": "",
        "worker_max_concurrency": "",
    }


def _seed(database: TeamsDatabase) -> None:
    now = datetime(2026, 1, 1, 12, 0, 0)
    with database.engine.begin() as connection:
        connection.execute(
            insert(users),
            [
                {
                    "id": "user-owner",
                    "username": "owner",
                    "email": "owner@example.test",
                    "hashed_password": "not-used",
                    "is_superuser": False,
                    "membership_type": "free",
                    "membership_expires_at": None,
                    "avatar_url": None,
                    "bio": None,
                    "created_at": now,
                    "password_updated_at": now,
                },
                {
                    "id": "user-member",
                    "username": "member",
                    "email": "member@example.test",
                    "hashed_password": "not-used",
                    "is_superuser": False,
                    "membership_type": "premium",
                    "membership_expires_at": None,
                    "avatar_url": "https://example.test/avatar.png",
                    "bio": "member bio",
                    "created_at": now,
                    "password_updated_at": now,
                },
            ],
        )
        connection.execute(
            insert(teams),
            {
                "id": "team-one",
                "name": "Team One",
                "owner_id": "user-owner",
                "created_at": now,
            },
        )
        connection.execute(
            insert(team_members),
            {
                "id": "membership-one",
                "team_id": "team-one",
                "user_id": "user-member",
                "role": "admin",
                "permissions": '["view_tasks","manage_invites","view_tasks"]',
                "joined_at": now,
            },
        )
        connection.execute(
            insert(series),
            {
                "id": "series-one",
                "user_id": "user-owner",
                "name": "Series One",
                "description": None,
                "image_url": None,
                "style_prompt_id": None,
                "team_id": "team-one",
                "claimed_by": None,
                "claimed_at": None,
                "created_at": now,
                "updated_at": now,
            },
        )
        connection.execute(
            insert(chapters),
            {
                "id": "chapter-one",
                "series_id": "series-one",
                "title": "Chapter One",
                "content": None,
                "order": 1,
                "created_at": now,
                "updated_at": now,
            },
        )
        connection.execute(insert(system_configs), _config_row("config-one"))


@pytest.fixture
def teams_database(tmp_path: Path) -> Iterator[TeamsDatabase]:
    path = tmp_path / "teams-owner.sqlite"
    engine = create_engine(f"sqlite:///{path}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    OWNER_METADATA.create_all(engine)
    database = TeamsDatabase(
        engine=engine,
        session_factory=sessionmaker(
            bind=engine,
            class_=Session,
            expire_on_commit=False,
            autoflush=False,
        ),
    )
    _seed(database)
    try:
        yield database
    finally:
        assert database.engine.pool.checkedout() == 0
        database.dispose()
