"""System-config read/update use cases and transaction boundary."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Mapping

from .domain import (
    AdminSystemConfigActor,
    convert_legacy_update_value,
    system_config_payload,
)
from .errors import (
    AdminSystemConfigForbidden,
    AdminSystemConfigNotFound,
    AdminSystemConfigUnavailable,
)
from .ports import AdminSystemConfigUnitOfWork, UnitOfWorkFactory

_UPDATE_FIELDS = (
    "muse_token",
    "site_title",
    "site_tagline",
    "storyboard_cost_multiplier",
    "membership_benefits",
    "payments_enabled",
    "tutorial_url",
    "purchase_qr_code_url",
    "purchase_contact_info",
    "team_created_limit",
    "team_joined_limit",
    "team_member_limit",
    "invite_code_ttl_hours",
    "chapter_lock_idle_minutes",
    "aistarslab_credit_multiplier",
    "frontend_url",
    "allowed_origins",
    "upload_dir",
    "worker_max_concurrency",
)


@contextmanager
def _open(
    factory: UnitOfWorkFactory | None,
) -> Iterator[AdminSystemConfigUnitOfWork]:
    if factory is None:
        raise AdminSystemConfigUnavailable()
    unit_of_work = factory()
    try:
        yield unit_of_work
    except BaseException:
        try:
            unit_of_work.rollback()
        except BaseException:
            pass
        try:
            unit_of_work.close()
        except BaseException:
            pass
        raise
    else:
        unit_of_work.close()


def _require_admin(actor: AdminSystemConfigActor) -> None:
    if not actor.is_superuser:
        raise AdminSystemConfigForbidden()


def list_system_config(
    factory: UnitOfWorkFactory | None,
    actor: AdminSystemConfigActor,
) -> dict[str, Any]:
    _require_admin(actor)
    with _open(factory) as unit_of_work:
        config = unit_of_work.first_config()
        if config is None:
            return {"data": [], "total": 0}
        return {"data": [system_config_payload(config)], "total": 1}


def get_system_config(
    factory: UnitOfWorkFactory | None,
    actor: AdminSystemConfigActor,
    config_id: str,
) -> dict[str, Any]:
    _require_admin(actor)
    with _open(factory) as unit_of_work:
        config = unit_of_work.get_config(config_id)
        if config is None:
            raise AdminSystemConfigNotFound()
        return system_config_payload(config)


def update_system_config(
    factory: UnitOfWorkFactory | None,
    actor: AdminSystemConfigActor,
    config_id: str,
    data: Mapping[str, Any],
) -> dict[str, Any]:
    _require_admin(actor)
    with _open(factory) as unit_of_work:
        config = unit_of_work.load_config_for_update(config_id)
        if config is None:
            raise AdminSystemConfigNotFound()
        for field in _UPDATE_FIELDS:
            if field in data:
                unit_of_work.set_config_value(
                    config,
                    field,
                    convert_legacy_update_value(field, data[field]),
                )
        unit_of_work.commit()
        updated = unit_of_work.refresh_config(config)
        return system_config_payload(updated)
