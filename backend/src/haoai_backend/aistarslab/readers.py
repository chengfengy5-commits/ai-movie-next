"""Narrow SQL reads that retain the legacy AIStarsLab query semantics."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .ports import (
    AistarslabCredentialRecord,
    AistarslabExistingModelRecord,
    AistarslabLegacyReaderFactory,
)


class SqlAlchemyAistarslabLegacyReader:
    """Read only the unordered rows needed by AIStarsLab compatibility code."""

    def __init__(self, session_factory: Callable[[], Any]) -> None:
        self._session_factory = session_factory

    def list_active_system_credentials(self) -> list[AistarslabCredentialRecord]:
        from haoai_backend.model_configs.tables import SystemModelRow

        with self._session_factory() as session:
            rows = (
                session.query(SystemModelRow)
                .filter(
                    SystemModelRow.provider == "aistarslab",
                    SystemModelRow.is_active.is_(True),
                )
                .all()
            )
            return [
                AistarslabCredentialRecord(
                    api_key=row.api_key,
                    base_url=row.base_url,
                )
                for row in rows
            ]

    def list_existing_system_models(
        self,
        provider: str,
    ) -> list[AistarslabExistingModelRecord]:
        from haoai_backend.model_configs.tables import SystemModelRow

        with self._session_factory() as session:
            rows = (
                session.query(SystemModelRow)
                .filter(SystemModelRow.provider == provider)
                .all()
            )
            return [
                AistarslabExistingModelRecord(id=row.id, model=row.model)
                for row in rows
            ]


def build_sqlalchemy_aistarslab_legacy_reader_factory(
    session_factory: Callable[[], Any],
) -> AistarslabLegacyReaderFactory:
    """Build a lazy reader factory without importing SQL mappings at composition."""

    return lambda: SqlAlchemyAistarslabLegacyReader(session_factory)
