"""Runtime and unit-of-work ports for storyboard asset generation."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from haoai_backend.shared.identity import TrustedActor
from haoai_backend.storyboard_generation.domain import StoryboardServerModel


@dataclass(frozen=True, slots=True)
class StoryboardAssetRuntimeCall:
    model: StoryboardServerModel = field(repr=False)
    system_prompt: str
    user_prompt: str
    raw_content: str
    temperature: float = 0.7
    enable_thinking: bool = False
    operation: str = "storyboard_asset"


class StoryboardAssetRuntime(Protocol):
    def stream(
        self, call: StoryboardAssetRuntimeCall
    ) -> AsyncIterator[str | Mapping[str, Any]]: ...


class StoryboardAssetUnitOfWork(Protocol):
    def get_chapter(self, chapter_id: str) -> Mapping[str, Any] | None: ...
    def require_series_access(self, actor: TrustedActor, series_id: str) -> None: ...
    def resolve_model(self, actor: TrustedActor, model_id: str) -> StoryboardServerModel: ...
    def resolve_prompt(self, actor: TrustedActor, prompt_id: str) -> tuple[str, str]: ...
    def merge_candidates(
        self, actor: TrustedActor, *, kind: str, series_id: str, candidates: list[Any]
    ) -> list[dict[str, Any]]: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...


StoryboardAssetUnitOfWorkFactory = Callable[[], StoryboardAssetUnitOfWork]
