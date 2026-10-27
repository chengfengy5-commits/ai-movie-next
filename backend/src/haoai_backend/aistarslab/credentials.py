"""Credential and multiplier lookups over the existing model/configuration ports."""

from __future__ import annotations

from typing import Any

from haoai_backend.admin_system_models.ports import Cipher
from haoai_backend.admin_system_config.ports import UnitOfWorkFactory as SystemConfigUnitOfWorkFactory
from haoai_backend.model_configs.ports import ModelConfigsUnitOfWorkFactory

from .domain import DEFAULT_BASE_URL
from .ports import AistarslabLegacyReaderFactory


def get_credentials(
    unit_of_work_factory: ModelConfigsUnitOfWorkFactory | None,
    legacy_reader_factory: AistarslabLegacyReaderFactory,
    cipher: Cipher,
    user_id: str,
) -> tuple[str | None, str | None]:
    """Resolve the first decryptable active system key, then the user's BYOK key."""
    for model in legacy_reader_factory().list_active_system_credentials():
        if not model.api_key:
            continue
        try:
            return cipher.decrypt(model.api_key), (
                model.base_url or DEFAULT_BASE_URL
            )
        except Exception:
            continue

    if unit_of_work_factory is None:
        return None, None

    unit_of_work = unit_of_work_factory()
    try:
        for model in unit_of_work.list_personal_models(user_id, category=None):
            if model.provider != "aistarslab" or not model.api_key:
                continue
            try:
                return cipher.decrypt(model.api_key), (
                    model.base_url or DEFAULT_BASE_URL
                )
            except Exception:
                continue
        return None, None
    finally:
        unit_of_work.close()


def get_credit_multiplier(
    unit_of_work_factory: SystemConfigUnitOfWorkFactory | None,
) -> float:
    """Read the legacy multiplier, defaulting to 1.0 for absent or invalid values."""
    if unit_of_work_factory is None:
        return 1.0

    unit_of_work = unit_of_work_factory()
    try:
        config = unit_of_work.first_config()
        value = getattr(config, "aistarslab_credit_multiplier", None)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
        return 1.0
    finally:
        unit_of_work.close()
