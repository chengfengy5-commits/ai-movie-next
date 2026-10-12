"""User preference read and update use cases."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping
from uuid import uuid4

from haoai_backend.shared.identity import TrustedActor

from .domain import decode_preferences, encode_preference
from .errors import UserPreferencesUnavailable
from .ports import Clock, IdFactory, UnitOfWorkFactory, UserPreferencesUnitOfWork


def _open(factory: UnitOfWorkFactory | None) -> UserPreferencesUnitOfWork:
    if factory is None:
        raise UserPreferencesUnavailable()
    return factory()


def get_user_preferences(
    factory: UnitOfWorkFactory | None,
    actor: TrustedActor,
) -> dict[str, Any]:
    unit_of_work = _open(factory)
    try:
        result = decode_preferences(unit_of_work.list_preferences(actor.user_id))
    except BaseException:
        try:
            unit_of_work.close()
        except BaseException:
            pass
        raise

    unit_of_work.close()
    return result


def update_user_preferences(
    factory: UnitOfWorkFactory | None,
    actor: TrustedActor,
    preferences: Mapping[str, Any],
    *,
    clock: Clock | None = None,
    id_factory: IdFactory | None = None,
) -> dict[str, Any]:
    unit_of_work = _open(factory)
    now = datetime.utcnow if clock is None else clock
    new_id = (lambda: str(uuid4())) if id_factory is None else id_factory
    try:
        timestamp = now()
        updated_keys: list[str] = []

        for key, value in preferences.items():
            if not key or len(key) > 100:
                continue

            try:
                serialized = encode_preference(value)
            except (TypeError, ValueError):
                continue

            existing = unit_of_work.find_preference(actor.user_id, key)
            if existing is None:
                unit_of_work.add_preference(
                    preference_id=new_id(),
                    user_id=actor.user_id,
                    key=key,
                    value=serialized,
                    updated_at=timestamp,
                )
            else:
                existing.value = serialized
                existing.updated_at = timestamp
            updated_keys.append(key)

        unit_of_work.commit()
        result = {"ok": True, "updated_keys": updated_keys}
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

    unit_of_work.close()
    return result
