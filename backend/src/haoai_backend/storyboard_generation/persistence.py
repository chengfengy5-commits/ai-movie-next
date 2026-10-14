"""SQLAlchemy Core adapter for scoped model, prompt, task and chapter access."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_catalog.tables import model_configs, prompt_configs, system_models, system_prompts
from haoai_backend.generation_core.base_schema import AITaskRow
from haoai_backend.series_access.application import require_series_access as apply_series_access
from haoai_backend.series_access.domain import ActorIdentity
from haoai_backend.series_access.errors import SeriesAccessDenied, SeriesNotFound
from haoai_backend.series_access.persistence import SqlAlchemySeriesAccessReader
from haoai_backend.series_data.domain import parse_chapter_frames
from haoai_backend.series_data.persistence import SqlAlchemySeriesDataUnitOfWork
from haoai_backend.shared.identity import TrustedActor

from .domain import StoryboardPrompt, StoryboardServerModel
from .errors import (
    StoryboardGenerationBadRequest,
    StoryboardGenerationForbidden,
    StoryboardGenerationNotFound,
    StoryboardGenerationTaskConflict,
    StoryboardGenerationUnavailable,
)

_LEGACY_FERNET_PREFIX = "gAAAAA"
_TERMINAL_TASK_STATUSES = ("completed", "failed", "cancelled")


class SqlAlchemyStoryboardGenerationUnitOfWork:
    def __init__(
        self,
        session: Session,
        *,
        decrypt_api_key: Callable[[str], str] | None = None,
        now: Callable[[], datetime] | None = None,
        new_id: Callable[[], str] | None = None,
    ) -> None:
        self._session = session
        self._decrypt_api_key = decrypt_api_key or (lambda value: value)
        self._now = now or (lambda: datetime.now(timezone.utc).replace(tzinfo=None))
        self._new_id = new_id or (lambda: str(uuid4()))

    def resolve_model(self, actor: TrustedActor, model_id: str) -> StoryboardServerModel:
        row = self._session.execute(
            select(
                system_models.c.id,
                system_models.c.name,
                system_models.c.provider,
                system_models.c.api_key,
                system_models.c.model,
                system_models.c.base_url,
                system_models.c.params,
            ).where(
                system_models.c.id == model_id,
                system_models.c.is_active.is_(True),
            )
        ).mappings().first()
        if row is None:
            row = self._session.execute(
                select(
                    model_configs.c.id,
                    model_configs.c.name,
                    model_configs.c.provider,
                    model_configs.c.api_key,
                    model_configs.c.model,
                    model_configs.c.base_url,
                    model_configs.c.params,
                ).where(
                    model_configs.c.id == model_id,
                    model_configs.c.user_id == actor.user_id,
                )
            ).mappings().first()
        if row is None:
            raise StoryboardGenerationNotFound("模型不存在或已禁用")

        api_key = row["api_key"]
        if isinstance(api_key, str) and api_key.startswith(_LEGACY_FERNET_PREFIX):
            try:
                api_key = self._decrypt_api_key(api_key)
            except Exception as exc:
                raise StoryboardGenerationUnavailable("模型凭据不可用，请检查服务配置") from exc
        provider = row["provider"]
        if not isinstance(api_key, str) or not api_key.strip() or not isinstance(provider, str) or not provider.strip():
            raise StoryboardGenerationUnavailable("模型执行配置不完整，请检查服务配置")
        model = row["model"]
        if model is not None and not isinstance(model, str):
            raise StoryboardGenerationUnavailable("模型执行配置不完整，请检查服务配置")
        return StoryboardServerModel(
            id=row["id"],
            name=row["name"],
            provider=provider,
            api_key=api_key,
            model=model,
            base_url=row["base_url"],
            params=row["params"],
        )

    def resolve_prompt(self, actor: TrustedActor, prompt_id: str | None) -> StoryboardPrompt:
        if not prompt_id:
            return StoryboardPrompt("", "")
        row = self._session.execute(
            select(prompt_configs.c.system_prompt, prompt_configs.c.user_prompt).where(
                prompt_configs.c.id == prompt_id,
                prompt_configs.c.user_id == actor.user_id,
            )
        ).mappings().first()
        if row is None:
            row = self._session.execute(
                select(system_prompts.c.system_prompt, system_prompts.c.user_prompt).where(
                    system_prompts.c.id == prompt_id,
                    system_prompts.c.is_active.is_(True),
                )
            ).mappings().first()
        if row is None:
            raise StoryboardGenerationBadRequest(
                "所选提示词不存在、已停用或无权引用，请重新选择后重试"
            )
        return StoryboardPrompt(
            system_prompt=row["system_prompt"] or "",
            user_prompt=row["user_prompt"] or "",
        )

    def require_series_access(self, actor: TrustedActor, series_id: str) -> None:
        try:
            apply_series_access(
                SqlAlchemySeriesAccessReader(self._session),
                ActorIdentity(actor.user_id),
                series_id,
            )
        except SeriesNotFound as exc:
            raise StoryboardGenerationNotFound("剧集不存在") from exc
        except SeriesAccessDenied as exc:
            raise StoryboardGenerationForbidden(exc.detail) from exc

    def validate_task(self, actor: TrustedActor, task_id: str | None) -> None:
        if not task_id:
            return
        row = self._session.execute(
            select(AITaskRow.id, AITaskRow.status, AITaskRow.user_cancelled_at).where(
                AITaskRow.id == task_id,
                AITaskRow.user_id == actor.user_id,
            )
        ).mappings().first()
        if row is None or row["status"] in _TERMINAL_TASK_STATUSES or row["user_cancelled_at"] is not None:
            raise StoryboardGenerationTaskConflict()

    def write_task_result(
        self,
        actor: TrustedActor,
        task_id: str,
        *,
        status: str,
        result: str | None,
    ) -> bool:
        if status not in {"completed", "failed"}:
            raise ValueError("task status must be terminal")
        changed = self._session.execute(
            update(AITaskRow)
            .where(
                AITaskRow.id == task_id,
                AITaskRow.user_id == actor.user_id,
                AITaskRow.status.not_in(_TERMINAL_TASK_STATUSES),
                AITaskRow.user_cancelled_at.is_(None),
            )
            .values(
                status=status,
                result=result,
                progress=100,
                progress_message="分镜生成完成" if status == "completed" else "分镜生成失败",
                updated_at=self._now(),
            )
        )
        return changed.rowcount == 1

    def create_chapter(
        self,
        actor: TrustedActor,
        *,
        series_id: str,
        title: str,
        order: int,
        frames: Sequence[Mapping[str, object]],
    ) -> tuple[str, list[dict[str, object]]]:
        self.require_series_access(actor, series_id)
        prepared_frames = [
            {
                "original_text": str(frame.get("text") or ""),
                "characters": str(frame.get("character") or ""),
                "scene_description": str(frame.get("scene") or ""),
                "dialogue": [],
            }
            for frame in frames
        ]
        series_uow = SqlAlchemySeriesDataUnitOfWork(
            self._session,
            now=self._now,
            new_id=self._new_id,
        )
        chapter = series_uow.create_chapter(series_id, title, prepared_frames, order)
        synced = series_uow.sync_storyboard_assets(chapter.id, series_id)
        final_chapter = synced or chapter
        decoded = parse_chapter_frames(final_chapter.content)
        saved_frames = decoded if isinstance(decoded, list) else prepared_frames
        return chapter.id, saved_frames

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()

    def close(self) -> None:
        self._session.close()


def storyboard_generation_uow_factory(
    session_factory: Callable[[], Session] | None,
    *,
    decrypt_api_key: Callable[[str], str] | None = None,
) -> Callable[[], SqlAlchemyStoryboardGenerationUnitOfWork] | None:
    if session_factory is None:
        return None

    def create() -> SqlAlchemyStoryboardGenerationUnitOfWork:
        return SqlAlchemyStoryboardGenerationUnitOfWork(
            session_factory(),
            decrypt_api_key=decrypt_api_key,
        )

    return create
