"""Persistence ports for administrator system-prompt use cases."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

from .domain import SystemPromptRecord


class AdminSystemPromptUnitOfWork(Protocol):
    def list_prompts(
        self,
        *,
        category: str | None,
        is_active: bool | None,
        skip: int,
        limit: int,
    ) -> tuple[Sequence[SystemPromptRecord], int]: ...

    def get_prompt(self, prompt_id: str) -> SystemPromptRecord | None: ...

    def add_prompt(self, prompt_id: str, values: Mapping[str, Any]) -> None: ...

    def update_prompt(self, prompt_id: str, changes: Mapping[str, Any]) -> bool: ...

    def delete_prompt(self, prompt_id: str) -> bool: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


UnitOfWorkFactory = Callable[[], AdminSystemPromptUnitOfWork]
