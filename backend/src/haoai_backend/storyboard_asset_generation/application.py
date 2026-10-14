"""Prepare authorized model inputs and construct the legacy prompt payloads."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

from haoai_backend.shared.identity import TrustedActor
from haoai_backend.storyboard_generation.domain import StoryboardServerModel

from .domain import format_storyboard_texts
from .errors import StoryboardAssetBadRequest, StoryboardAssetNotFound, StoryboardAssetUnavailable
from .ports import StoryboardAssetUnitOfWork, StoryboardAssetUnitOfWorkFactory
from .prompts import CHARACTER_SYSTEM, CHARACTER_USER, PROP_SYSTEM, PROP_USER, SCENE_SYSTEM, SCENE_USER
from .schemas import ExtractRequest, JudgeRequest


@dataclass(frozen=True, slots=True)
class PreparedAssetGeneration:
    actor: TrustedActor
    kind: str
    action: str
    model: StoryboardServerModel
    system_prompt: str
    user_prompt: str
    raw_content: str
    enable_thinking: bool
    candidates: list[Any] | None = None
    storyboard_texts: list[str] | None = None
    frame_numbers: list[int] | None = None
    skip_merge: bool = False


def prepare_extract(
    factory: StoryboardAssetUnitOfWorkFactory | None,
    actor: TrustedActor,
    *,
    kind: str,
    request: ExtractRequest,
) -> PreparedAssetGeneration:
    with _open(factory) as uow:
        chapter = uow.get_chapter(request.chapter_id)
        if chapter is None:
            raise StoryboardAssetNotFound("章节不存在")
        series_id = str(chapter["series_id"])
        uow.require_series_access(actor, series_id)
        model = uow.resolve_model(actor, request.model_id)
        prompt_id = request.prompt_id
        system_prompt, user_prompt = uow.resolve_prompt(actor, prompt_id) if prompt_id else ("", "")
        all_texts = _chapter_texts(chapter.get("content"))
        if not all_texts:
            raise StoryboardAssetBadRequest("章节没有分镜数据")
        if not any(all_texts):
            raise StoryboardAssetBadRequest("章节分镜中没有文本内容")
        if request.frame_indices is None:
            selected = all_texts
        else:
            selected = [all_texts[index] for index in request.frame_indices if 0 <= index < len(all_texts)]
            if not selected:
                raise StoryboardAssetBadRequest("没有选中任何分镜")
        return PreparedAssetGeneration(
            actor=actor,
            kind=kind,
            action="extract",
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            raw_content="\n\n".join(selected),
            enable_thinking=request.enable_thinking,
        )


def prepare_judge(
    factory: StoryboardAssetUnitOfWorkFactory | None,
    actor: TrustedActor,
    *,
    kind: str,
    request: JudgeRequest,
) -> PreparedAssetGeneration:
    with _open(factory) as uow:
        chapter = uow.get_chapter(request.chapter_id)
        if chapter is None:
            raise StoryboardAssetNotFound("章节不存在")
        series_id = str(chapter["series_id"])
        uow.require_series_access(actor, series_id)
        model = uow.resolve_model(actor, request.model_id)
        if request.prompt_id:
            system_prompt, user_template = uow.resolve_prompt(actor, request.prompt_id)
        else:
            system_prompt, user_template = _default_judge_prompt(kind)
        if request.skip_merge:
            merged = _pass_through_candidates(kind, request.candidates)
        else:
            merged = uow.merge_candidates(
                actor, kind=kind, series_id=series_id, candidates=request.candidates
            )
        user_prompt = _compose_judge_prompt(
            kind,
            user_template,
            merged,
            request.storyboard_texts,
            request.frame_numbers,
        )
        return PreparedAssetGeneration(
            actor=actor,
            kind=kind,
            action="judge",
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            raw_content="",
            enable_thinking=request.enable_thinking,
            candidates=merged,
            storyboard_texts=list(request.storyboard_texts),
            frame_numbers=request.frame_numbers,
            skip_merge=request.skip_merge,
        )


@contextmanager
def _open(
    factory: StoryboardAssetUnitOfWorkFactory | None,
) -> Iterator[StoryboardAssetUnitOfWork]:
    if factory is None:
        raise StoryboardAssetUnavailable("数据库服务尚未接线")
    uow = factory()
    try:
        yield uow
    except Exception:
        try:
            uow.rollback()
        except Exception:
            pass
        raise
    finally:
        uow.close()


def _chapter_texts(content: Any) -> list[str]:
    value = content
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return []
    if not isinstance(value, list):
        return []
    texts: list[str] = []
    for frame in value:
        if not isinstance(frame, Mapping):
            texts.append("")
            continue
        text = frame.get("text")
        if not isinstance(text, str):
            text = frame.get("original_text")
        texts.append(text if isinstance(text, str) else "")
    return texts


def _default_judge_prompt(kind: str) -> tuple[str, str]:
    if kind == "character":
        return CHARACTER_SYSTEM, CHARACTER_USER
    if kind == "scene":
        return SCENE_SYSTEM, SCENE_USER
    return PROP_SYSTEM, PROP_USER


def _pass_through_candidates(kind: str, candidates: Sequence[Any]) -> list[Any]:
    return [dict(candidate) if isinstance(candidate, Mapping) else candidate for candidate in candidates]


def _compose_judge_prompt(
    kind: str,
    template: str,
    candidates: Sequence[Mapping[str, Any]],
    storyboard_texts: Sequence[str],
    frame_numbers: Sequence[int] | None,
) -> str:
    storyboard = format_storyboard_texts(storyboard_texts, frame_numbers)
    if kind == "character":
        normalized = [_candidate_mapping(kind, candidate) for candidate in candidates]
        existing = [candidate for candidate in normalized if candidate.get("is_existing")]
        new = [candidate for candidate in normalized if not candidate.get("is_existing")]
        return (
            template.replace("{existing_characters}", _format_characters(existing, existing=True))
            .replace("{new_characters}", _format_characters(new, existing=False))
            .replace("{storyboard_texts}", storyboard)
        )

    normalized = [_candidate_mapping(kind, candidate) for candidate in candidates]
    field = "title" if kind == "scene" else "name"
    label = "场景" if kind == "scene" else "道具"
    all_lines = "\n".join(
        f"- {candidate.get(field, '')}：{_clip_description(candidate.get('description'))}"
        for candidate in normalized
    )
    existing_rows = [candidate for candidate in normalized if candidate.get("is_existing")]
    new_rows = [candidate for candidate in normalized if not candidate.get("is_existing")]
    existing_lines = ""
    new_lines = ""
    if existing_rows:
        existing_lines = (
            f"【已有{label}】（库中已有，可跨章节复用；按贴合度选择，不因它已存在就优先选它）\n"
            + "\n".join(
                f"- {candidate.get(field, '')}：{_clip_description(candidate.get('description'))}"
                for candidate in existing_rows
            )
        )
    if new_rows:
        same_asset = "同一个场景" if kind == "scene" else "同一种物品"
        new_lines = (
            f"\n【本章新提取{label}】（与【已有{label}】地位相同，按贴合度选择；若与某条已有{label}其实是{same_asset}，请输出已有{label}的名称）\n"
            + "\n".join(
                f"- {candidate.get(field, '')}：{_clip_description(candidate.get('description'))}"
                for candidate in new_rows
            )
        )
    return (
        template.replace("{scenes_list}" if kind == "scene" else "{props_list}", all_lines)
        .replace("{existing_scenes}" if kind == "scene" else "{existing_props}", existing_lines)
        .replace("{new_scenes}" if kind == "scene" else "{new_props}", new_lines)
        .replace("{storyboard_texts}", storyboard)
    )


def _candidate_mapping(kind: str, candidate: Any) -> dict[str, Any]:
    if isinstance(candidate, Mapping):
        value = dict(candidate)
    elif isinstance(candidate, str):
        value = {"title" if kind == "scene" else "name": candidate}
    else:
        value = {}
    field = "title" if kind == "scene" else "name"
    if field not in value and isinstance(value.get("name"), str):
        value[field] = value["name"]
    return value


def _format_characters(rows: Sequence[Mapping[str, Any]], *, existing: bool) -> str:
    if not rows:
        return ""
    label = "已有角色" if existing else "新提取角色"
    note = "优先选择这些角色，除非角色形象有重大差异" if existing else "仅在已有角色无法匹配时选择"
    prefix = "" if existing else "\n"
    result = f"{prefix}【{label}】（{note}）\n"
    for row in rows:
        line = f"- {row.get('name', '')}"
        gender, age, role = row.get("gender"), row.get("age"), row.get("role")
        if gender:
            line += f"（{gender}"
        if age:
            line += f"，{age}"
        if role:
            line += f"，{role}"
        line += "）"
        if row.get("appearance"):
            line += f" - {row['appearance']}"
        result += line + "\n"
    return result


def _clip_description(value: Any) -> str:
    text = str(value or "").strip().replace("\n", " ")
    return text if len(text) <= 60 else text[:60] + "…"
