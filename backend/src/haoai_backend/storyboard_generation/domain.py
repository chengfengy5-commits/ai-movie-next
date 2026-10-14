"""Typed values and pure legacy storyboard parsing helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class StoryboardServerModel:
    id: str
    name: str
    provider: str
    api_key: str = field(repr=False)
    model: str | None = None
    base_url: str | None = None
    params: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class StoryboardPrompt:
    system_prompt: str
    user_prompt: str


@dataclass(frozen=True, slots=True)
class StoryboardRuntimeCall:
    model: StoryboardServerModel = field(repr=False)
    system_prompt: str
    user_prompt: str
    raw_content: str
    temperature: float
    enable_thinking: bool = False
    operation: str = "storyboard"


@dataclass(frozen=True, slots=True)
class StoryboardRuntimeResult:
    raw_response: str


def parse_storyboard(content: str) -> list[dict[str, str]]:
    """Parse only the fixed OUTPUT marker format used by the old endpoint."""
    start = "_::~OUTPUT_START::~_"
    end = "_::~OUTPUT_END::~_"
    separator = "_::~RECORD::~_"
    if start not in content or end not in content:
        return []
    body = content.split(start, 1)[1].split(end, 1)[0].strip()
    if not body:
        return []
    return [
        {"text": record.strip(), "character": "", "scene": "", "prop": ""}
        for record in body.split(separator)
        if record.strip()
    ]


def parse_scene_segments(content: str) -> list[dict[str, Any]]:
    """Keep the old record separator parsing and original segment indexes."""
    result: list[dict[str, Any]] = []
    for index, part in enumerate(content.split("_::~RECORD::~_")):
        value = part.strip()
        if not value:
            continue
        for prefix in ("```json", "```"):
            if value.startswith(prefix):
                value = value[len(prefix):].strip()
                break
        if value.endswith("```"):
            value = value[:-3].strip()
        if value:
            result.append({"index": index, "text": value})
    return result


def parse_all_frames(
    content: str,
    segments: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Pair legacy frame output with its input segments; missing frames fall back."""
    parts = content.split("_::~RECORD::~_")
    frames: list[dict[str, object]] = []
    for index, segment in enumerate(segments):
        generated = parts[index].strip() if index < len(parts) else ""
        text = generated or str(segment.get("text", ""))
        frames.append({"text": text, "duration": 5})
    return frames
