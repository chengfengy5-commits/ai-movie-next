"""Execution and persistence ports for fused image generation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Protocol

from haoai_backend.shared.identity import TrustedActor

from .domain import (
    FusedGenerationInput,
    FusedGenerationOutput,
    FusedPromptText,
    FusedServerImageModel,
    FusedTaskClaim,
    PreparedFusedGeneration,
)


class FusedGenerationRuntime(Protocol):
    """Configured server-side driver; receives only the selected model identity."""

    async def generate(self, request: FusedGenerationInput) -> FusedGenerationOutput: ...


class FusedGenerationStorage(Protocol):
    """Optional transfer adapter; returning None means keep the provider URL."""

    async def store_generated_image(self, provider_url: str) -> str | None: ...


class FusedGenerationUnitOfWork(Protocol):
    def current_time(self) -> datetime: ...
    def new_id(self) -> str: ...
    def require_series_access(self, actor: TrustedActor, series_id: str) -> None: ...
    def load_member_asset(
        self, member_type: str, series_id: str, asset_id: str
    ) -> Mapping[str, object] | None: ...
    def resolve_and_charge_model(self, user_id: str, model_id: str) -> FusedServerImageModel: ...
    def resolve_image_prompt(self, user_id: str, prompt_id: str) -> FusedPromptText | None: ...
    def load_chapter(self, chapter_id: str) -> Mapping[str, object] | None: ...
    def stage_request(self, prepared: PreparedFusedGeneration, user_id: str) -> None: ...
    def claim_task(self, task_id: str, claim_token: str) -> FusedTaskClaim | None: ...
    def finish_task(
        self,
        prepared: PreparedFusedGeneration,
        claim_token: str,
        *,
        image_url: str | None,
        error_message: str | None,
    ) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...


FusedGenerationUnitOfWorkFactory = Callable[[], FusedGenerationUnitOfWork]
