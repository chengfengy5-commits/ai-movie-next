"""SQLAlchemy adapter for the fixed legacy system_configs table."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from sqlalchemy.orm import Session

from .domain import SystemConfigRecord
from .ports import AdminSystemConfigUnitOfWork
from .tables import SystemConfigRow


class SqlAlchemyAdminSystemConfigUnitOfWork(AdminSystemConfigUnitOfWork):
    def __init__(self, session: Session) -> None:
        self._session = session

    def first_config(self) -> SystemConfigRecord | None:
        row = self._session.query(SystemConfigRow).first()
        return _record(row) if row is not None else None

    def get_config(self, config_id: str) -> SystemConfigRecord | None:
        row = (
            self._session.query(SystemConfigRow)
            .filter(SystemConfigRow.id == config_id)
            .first()
        )
        return _record(row) if row is not None else None

    def load_config_for_update(self, config_id: str) -> SystemConfigRow | None:
        return (
            self._session.query(SystemConfigRow)
            .filter(SystemConfigRow.id == config_id)
            .first()
        )

    def set_config_value(self, config: object, field: str, value: Any) -> None:
        setattr(config, field, value)

    def refresh_config(self, config: object) -> SystemConfigRecord:
        self._session.refresh(config)
        return _record(config)

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()

    def close(self) -> None:
        self._session.close()


def _record(row: Any) -> SystemConfigRecord:
    fields = tuple(system_configs_columns())
    if isinstance(row, Mapping):
        values = {field: row[field] for field in fields}
    else:
        values = {field: getattr(row, field) for field in fields}
    return SystemConfigRecord(**values)


def system_configs_columns() -> tuple[str, ...]:
    """Return the fixed legacy column order without exposing mapper internals."""

    return (
        "id",
        "muse_token",
        "site_title",
        "site_tagline",
        "storyboard_cost_multiplier",
        "membership_benefits",
        "payments_enabled",
        "tutorial_url",
        "purchase_qr_code_url",
        "purchase_contact_info",
        "oss_access_key_id",
        "oss_access_key_secret",
        "oss_bucket_name",
        "oss_endpoint",
        "fc_transfer_url",
        "fc_transfer_url_cn",
        "oss_haoai_match_domain",
        "oss_haoai_replace_domain",
        "team_created_limit",
        "team_joined_limit",
        "team_member_limit",
        "invite_code_ttl_hours",
        "chapter_lock_idle_minutes",
        "aistarslab_credit_multiplier",
        "zhangyuge_billing_pat",
        "frontend_url",
        "allowed_origins",
        "upload_dir",
        "worker_max_concurrency",
    )


def admin_system_config_uow_factory(
    session_factory: Callable[[], Session],
) -> Callable[[], SqlAlchemyAdminSystemConfigUnitOfWork]:
    """Create business sessions only when the returned factory runs."""

    def create() -> SqlAlchemyAdminSystemConfigUnitOfWork:
        return SqlAlchemyAdminSystemConfigUnitOfWork(session_factory())

    return create
