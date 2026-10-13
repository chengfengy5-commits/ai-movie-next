"""Private SQLAlchemy adapter for the fixed legacy system_configs table."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import Column, Float, Integer, MetaData, String, Table, Text
from sqlalchemy.orm import Session, registry

from .errors import UploadFilesUnavailable

logger = logging.getLogger(__name__)

metadata = MetaData()
system_configs = Table(
    "system_configs",
    metadata,
    Column("id", String(36), primary_key=True, index=True),
    Column("muse_token", String(500), default="", nullable=False),
    Column("site_title", String(200), default="Hao AI - 无限可能", nullable=False),
    Column("site_tagline", String(500), default="无限可能", nullable=False),
    Column("storyboard_cost_multiplier", Integer, default=2, nullable=False),
    Column("membership_benefits", Text, default="", nullable=False),
    Column("payments_enabled", Integer, default=1, nullable=False),
    Column("tutorial_url", String(500), default="", nullable=False),
    Column("purchase_qr_code_url", String(500), default="", nullable=False),
    Column("purchase_contact_info", Text, default="", nullable=False),
    Column("oss_access_key_id", String(200), default="", nullable=False),
    Column("oss_access_key_secret", String(200), default="", nullable=False),
    Column("oss_bucket_name", String(100), default="", nullable=False),
    Column("oss_endpoint", String(200), default="", nullable=False),
    Column("fc_transfer_url", String(500), default="", nullable=False),
    Column("fc_transfer_url_cn", String(500), default="", nullable=False),
    Column("oss_haoai_match_domain", String(200), default="", nullable=False),
    Column("oss_haoai_replace_domain", String(200), default="", nullable=False),
    Column("team_created_limit", Integer, default=3, nullable=False),
    Column("team_joined_limit", Integer, default=3, nullable=False),
    Column("team_member_limit", Integer, default=20, nullable=False),
    Column("invite_code_ttl_hours", Integer, default=24, nullable=False),
    Column("chapter_lock_idle_minutes", Integer, default=15, nullable=False),
    Column("aistarslab_credit_multiplier", Float, default=1.0, nullable=False),
    Column("zhangyuge_billing_pat", Text, default="", nullable=False),
    Column("frontend_url", String(500), default="", nullable=False),
    Column("allowed_origins", Text, default="", nullable=False),
    Column("upload_dir", String(500), default="", nullable=False),
    Column("worker_max_concurrency", String(10), default="", nullable=False),
)

_mapper_registry = registry(metadata=metadata)


class _SystemConfigRow:
    """Mapped row with the complete fixed legacy column set."""


_mapper_registry.map_imperatively(_SystemConfigRow, system_configs)

SessionFactory = Callable[[], Session]


class SqlAlchemyUploadDirectoryProvider:
    """Read the first system configuration on each request, then use fallback."""

    def __init__(
        self,
        session_factory: SessionFactory | None,
        fallback_directory: str | Path | None,
    ) -> None:
        self._session_factory = session_factory
        self._fallback_directory = fallback_directory

    def get_upload_directory(self) -> str | Path:
        session_factory = self._session_factory
        if session_factory is not None:
            try:
                session = session_factory()
                try:
                    config = session.query(_SystemConfigRow).first()
                    if config is not None and config.upload_dir:
                        return config.upload_dir
                finally:
                    session.close()
            except Exception:
                logger.warning("读取系统配置失败，回退配置的上传目录")

        if self._fallback_directory:
            return self._fallback_directory
        raise UploadFilesUnavailable()


def upload_directory_provider(
    session_factory: SessionFactory | None,
    fallback_directory: str | Path | None,
) -> SqlAlchemyUploadDirectoryProvider:
    """Build a provider without opening a session or inspecting the filesystem."""

    return SqlAlchemyUploadDirectoryProvider(session_factory, fallback_directory)
