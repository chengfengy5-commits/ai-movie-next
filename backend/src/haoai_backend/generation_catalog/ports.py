"""Read-only ports consumed by the pure catalog application layer."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from .domain import CatalogModel, CatalogPrompt, UserModelOrder


class CatalogReader(Protocol):
    """Narrow query interface; implementations own transaction and locking."""

    def active_system_model(
        self, model_id: str, category: str
    ) -> CatalogModel | None:
        """Return only the active system row matching both exact values."""

    def owned_model(
        self, user_id: str, model_id: str, category: str
    ) -> CatalogModel | None:
        """Return only a row owned by this user with this exact identity."""

    def active_system_text_models(self) -> Sequence[CatalogModel]:
        """List active system text models ordered by legacy sort_order."""

    def owned_text_models(self, user_id: str) -> Sequence[CatalogModel]:
        """List this user's text models ordered by legacy sort_order."""

    def user_model_orders(
        self, user_id: str, category: str
    ) -> Sequence[UserModelOrder]:
        """Return this user's ordered model preferences for one category."""

    def active_system_prompt(self, prompt_id: str) -> CatalogPrompt | None:
        """Return only an active system prompt with this exact ID."""

    def owned_prompt(
        self, user_id: str, prompt_id: str
    ) -> CatalogPrompt | None:
        """Return only a prompt owned by this user with this exact ID."""
