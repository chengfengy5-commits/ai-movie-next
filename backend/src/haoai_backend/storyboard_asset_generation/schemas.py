"""Narrow wire parsing for the six legacy storyboard asset endpoints."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError


class StoryboardRequestError(ValueError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code


class _ExtractCoercion(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enable_thinking: bool = False
    frame_indices: list[int] | None = None


@dataclass(frozen=True, slots=True)
class ExtractRequest:
    chapter_id: str
    model_id: str
    prompt_id: str | None
    enable_thinking: bool
    frame_indices: list[int] | None

    @classmethod
    def parse(cls, body: Mapping[str, Any]) -> "ExtractRequest":
        try:
            coerced = _ExtractCoercion.model_validate(body)
        except ValidationError as error:
            raise StoryboardRequestError(422, "提取参数格式不正确") from error
        return cls(
            _required_string(body, "chapter_id", "章节ID", missing_status=422),
            _required_string(body, "model_id", "模型ID", missing_status=422),
            _optional_string(body.get("prompt_id")),
            coerced.enable_thinking,
            coerced.frame_indices,
        )


@dataclass(frozen=True, slots=True)
class JudgeRequest:
    chapter_id: str
    model_id: str
    prompt_id: str | None
    enable_thinking: bool
    frame_numbers: list[int] | None
    skip_merge: bool
    candidates: list[Any]
    storyboard_texts: list[str]

    @classmethod
    def parse(cls, body: Mapping[str, Any], kind: str) -> "JudgeRequest":
        candidates = body.get(f"{kind}s")
        texts = body.get("storyboard_texts")
        if candidates is None or candidates == []:
            raise ValueError(f"缺少{_KIND_LABEL[kind]}列表")
        if not isinstance(candidates, list):
            raise StoryboardRequestError(422, f"{_KIND_LABEL[kind]}列表格式不正确")
        if texts is None or texts == []:
            raise ValueError("缺少分镜文本列表")
        if not isinstance(texts, list):
            raise StoryboardRequestError(422, "分镜文本列表格式不正确")
        if any(not isinstance(item, str) for item in texts):
            raise StoryboardRequestError(422, "分镜文本列表格式不正确")
        return cls(
            _required_string(body, "chapter_id", "章节ID", missing_status=400),
            _required_string(body, "model_id", "模型ID", missing_status=400),
            _optional_string(body.get("prompt_id")),
            bool(body.get("enable_thinking", False)),
            _int_list(body.get("frame_numbers")),
            bool(body.get("skip_merge", False)),
            candidates,
            texts,
        )


_KIND_LABEL = {"character": "角色", "scene": "场景", "prop": "道具"}


def _required_string(
    body: Mapping[str, Any],
    key: str,
    label: str,
    *,
    missing_status: int,
) -> str:
    value = body.get(key)
    if value is None:
        if missing_status == 400:
            raise StoryboardRequestError(400, f"缺少{label}")
        raise StoryboardRequestError(422, f"{label}不能为空")
    if not isinstance(value, str):
        raise StoryboardRequestError(422, f"{label}格式不正确")
    if not value.strip():
        raise StoryboardRequestError(400, f"{label}不能为空")
    return value.strip()


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("提示词ID格式不正确")
    normalized = value.strip()
    return normalized or None


def _int_list(value: Any) -> list[int] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("分镜序号列表格式不正确")
    return [item for item in value if isinstance(item, int) and not isinstance(item, bool)]
