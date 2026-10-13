"""Persistence ports for administrator prompt-config use cases."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol, TypeAlias

from .domain import PromptConfigRecord


class AdminPromptConfigsUnitOfWork(Protocol):
    def list_prompt_configs(
        self,
        *,
        user_id: str | None,
        sort_field: str,
        sort_order: str,
        skip: int,
        limit: int,
    ) -> tuple[Sequence[PromptConfigRecord], int]: ...

    def get_prompt_config(self, prompt_id: str) -> PromptConfigRecord | None: ...

    def add_prompt_config(
        self, prompt_id: str, user_id: str, values: Mapping[str, Any]
    ) -> None: ...

    def update_prompt_config(
        self, prompt_id: str, changes: Mapping[str, Any]
    ) -> bool: ...

    def delete_prompt_config(self, prompt_id: str) -> bool: ...

    def commit(self) -> None: ...

    def refresh_prompt_config(self, prompt_id: str) -> PromptConfigRecord | None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


AdminPromptConfigsUnitOfWorkFactory: TypeAlias = Callable[
    [], AdminPromptConfigsUnitOfWork
]
