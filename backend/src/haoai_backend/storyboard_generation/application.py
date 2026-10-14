"""Use cases for configuring, invoking and persisting storyboard generation."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from haoai_backend.shared.identity import TrustedActor

from .domain import (
    StoryboardPrompt,
    StoryboardRuntimeCall,
    StoryboardRuntimeResult,
    StoryboardServerModel,
    parse_storyboard,
)
from .errors import StoryboardGenerationBadRequest, StoryboardGenerationUnavailable
from .ports import (
    StoryboardGenerationRuntime,
    StoryboardGenerationUnitOfWork,
    StoryboardGenerationUnitOfWorkFactory,
)
from .schemas import CreateChapterWithStoryboardRequest, GenerateStoryboardRequest, TwoPassStoryboardRequest


@dataclass(frozen=True, slots=True)
class PreparedStoryboard:
    actor: TrustedActor
    model: StoryboardServerModel
    prompt: StoryboardPrompt
    raw_content: str
    temperature: float
    task_id: str | None


@dataclass(frozen=True, slots=True)
class PreparedTwoPassStoryboard:
    actor: TrustedActor
    model: StoryboardServerModel
    scene_prompt: StoryboardPrompt
    frame_prompt: StoryboardPrompt
    raw_content: str
    temperature: float
    enable_thinking: bool
    task_id: str | None
    skip_frame_generation: bool


@dataclass(frozen=True, slots=True)
class PreparedChapterStoryboard:
    actor: TrustedActor
    model: StoryboardServerModel
    prompt: StoryboardPrompt
    series_id: str
    title: str
    raw_content: str
    order: int


def prepare_storyboard(
    factory: StoryboardGenerationUnitOfWorkFactory | None,
    actor: TrustedActor,
    request: GenerateStoryboardRequest,
) -> PreparedStoryboard:
    raw_content, model_id, prompt_id = _validate_base(
        request.raw_content, request.model_id, request.prompt_id
    )
    with _open(factory) as uow:
        uow.validate_task(actor, request.task_id)
        return PreparedStoryboard(
            actor=actor,
            model=uow.resolve_model(actor, model_id),
            prompt=uow.resolve_prompt(actor, prompt_id),
            raw_content=raw_content,
            temperature=request.temperature,
            task_id=request.task_id,
        )


def prepare_two_pass_storyboard(
    factory: StoryboardGenerationUnitOfWorkFactory | None,
    actor: TrustedActor,
    request: TwoPassStoryboardRequest,
) -> PreparedTwoPassStoryboard:
    raw_content, model_id, _ = _validate_base(
        request.raw_content, request.model_id, None
    )
    scene_prompt_id = _clean_optional(request.scene_prompt_id)
    frame_prompt_id = _clean_optional(request.frame_prompt_id)
    with _open(factory) as uow:
        uow.validate_task(actor, request.task_id)
        model = uow.resolve_model(actor, model_id)
        # Resolve both selected prompts before runtime work can start.
        scene_prompt = uow.resolve_prompt(actor, scene_prompt_id)
        frame_prompt = uow.resolve_prompt(actor, frame_prompt_id)
        return PreparedTwoPassStoryboard(
            actor, model, scene_prompt, frame_prompt, raw_content,
            request.temperature, request.enable_thinking, request.task_id,
            request.skip_frame_generation,
        )


def prepare_chapter_storyboard(
    factory: StoryboardGenerationUnitOfWorkFactory | None,
    actor: TrustedActor,
    request: CreateChapterWithStoryboardRequest,
) -> PreparedChapterStoryboard:
    series_id = request.series_id.strip()
    title = request.chapter_title.strip()
    raw_content, model_id, prompt_id = _validate_base(
        request.raw_content, request.model_id, request.prompt_id
    )
    if not series_id:
        raise StoryboardGenerationBadRequest("剧集ID不能为空")
    if not title:
        raise StoryboardGenerationBadRequest("章节标题不能为空")
    with _open(factory) as uow:
        uow.require_series_access(actor, series_id)
        return PreparedChapterStoryboard(
            actor, uow.resolve_model(actor, model_id), uow.resolve_prompt(actor, prompt_id),
            series_id, title, raw_content, request.order,
        )


def standard_runtime_call(
    prepared: PreparedStoryboard | PreparedChapterStoryboard,
) -> StoryboardRuntimeCall:
    return StoryboardRuntimeCall(
        model=prepared.model,
        system_prompt=prepared.prompt.system_prompt,
        user_prompt=prepared.prompt.user_prompt,
        raw_content=prepared.raw_content,
        temperature=0.7,
        operation="storyboard",
    )


async def generate_storyboard(
    runtime: StoryboardGenerationRuntime,
    call: StoryboardRuntimeCall,
) -> tuple[list[dict[str, str]], str]:
    result = await runtime.generate(call)
    raw_response = safe_raw_response(_runtime_text(result), call.model)
    return parse_storyboard(raw_response), raw_response


def save_generated_chapter(
    factory: StoryboardGenerationUnitOfWorkFactory | None,
    prepared: PreparedChapterStoryboard,
    frames: Sequence[Mapping[str, object]],
) -> tuple[str, list[dict[str, object]]]:
    with _open(factory) as uow:
        # Recheck series authorization in the fresh write Session after provider work.
        chapter_id, saved_frames = uow.create_chapter(
            prepared.actor,
            series_id=prepared.series_id,
            title=prepared.title,
            order=prepared.order,
            frames=frames,
        )
        uow.commit()
        return chapter_id, saved_frames


def save_task_result(
    factory: StoryboardGenerationUnitOfWorkFactory | None,
    actor: TrustedActor,
    task_id: str | None,
    *,
    status: str,
    result: object | None,
) -> bool:
    if task_id is None:
        return True
    encoded = json.dumps(result, ensure_ascii=False) if result is not None else None
    with _open(factory) as uow:
        changed = uow.write_task_result(actor, task_id, status=status, result=encoded)
        if changed:
            uow.commit()
        else:
            uow.rollback()
        return changed


def ensure_runtime_available(runtime: object | None) -> None:
    if runtime is None or any(
        not callable(getattr(runtime, name, None))
        for name in ("generate", "stream")
    ):
        raise StoryboardGenerationUnavailable()



def safe_raw_response(raw_response: str, model: StoryboardServerModel) -> str:
    return raw_response.replace(model.api_key, "[已隐藏]") if model.api_key else raw_response


def safe_provider_detail(error: BaseException, model: StoryboardServerModel) -> str:
    detail = getattr(error, "detail", None)
    text = detail if isinstance(detail, str) and detail.strip() else str(error)
    if model.api_key:
        text = text.replace(model.api_key, "[已隐藏]")
    return text.strip()[:2000] or "分镜生成失败"


def _runtime_text(result: Any) -> str:
    if isinstance(result, StoryboardRuntimeResult):
        raw_response = result.raw_response
    elif isinstance(result, Mapping):
        raw_response = result.get("raw_response")
    else:
        raw_response = None
    if not isinstance(raw_response, str):
        raise StoryboardGenerationUnavailable("分镜运行端返回格式无效")
    return raw_response


def _validate_base(
    raw_content: str,
    model_id: str,
    prompt_id: str | None,
) -> tuple[str, str, str | None]:
    content = raw_content.strip()
    selected_model = model_id.strip()
    if not content:
        raise StoryboardGenerationBadRequest("剧本内容不能为空")
    if not selected_model:
        raise StoryboardGenerationBadRequest("模型ID不能为空")
    return content, selected_model, _clean_optional(prompt_id)


def _clean_optional(value: str | None) -> str | None:
    cleaned = value.strip() if isinstance(value, str) else ""
    return cleaned or None


@contextmanager
def _open(
    factory: StoryboardGenerationUnitOfWorkFactory | None,
) -> Iterator[StoryboardGenerationUnitOfWork]:
    if factory is None:
        raise StoryboardGenerationUnavailable("分镜数据库服务尚未接线")
    uow = factory()
    try:
        yield uow
    except BaseException:
        try:
            uow.rollback()
        except BaseException:
            pass
        raise
    finally:
        uow.close()
