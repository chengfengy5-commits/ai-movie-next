"""Explicit server-owned model runtime and configuration contracts."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy.orm import Session


class SessionFactory(Protocol):
    def __call__(self) -> Session: ...


@dataclass(frozen=True, slots=True)
class ChatTextModel:
    id: str
    name: str
    provider: str
    api_key: str
    model: str | None
    base_url: str | None
    cost_per_call: int
    is_system: bool


@dataclass(frozen=True, slots=True)
class ChatTextRuntimeCall:
    model: ChatTextModel
    messages: tuple[Mapping[str, str], ...]
    enable_thinking: bool


class ChatTextGenerationRuntime(Protocol):
    def stream(
        self,
        call: ChatTextRuntimeCall,
    ) -> AsyncIterator[str | Mapping[str, object]]: ...


@dataclass(frozen=True, slots=True)
class ChatTextGenerationConfiguration:
    runtime: ChatTextGenerationRuntime | None
    max_user_concurrency: int = 0
    server_id_factory: Callable[[], str] | None = None
    clock: Callable[[], datetime] | None = None


ApiKeyDecryptor = Callable[[str], str]
