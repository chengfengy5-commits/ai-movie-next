"""Request-attached SSE execution with cross-chunk credential redaction."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any

from haoai_backend.storyboard_generation.execution import run_owned_awaitable, run_owned_in_threadpool

from .application import PreparedAssetGeneration
from .domain import pad_appearances, parse_appearances, parse_extracted
from .ports import StoryboardAssetRuntime, StoryboardAssetRuntimeCall


async def stream_asset_events(
    runtime: StoryboardAssetRuntime,
    prepared: PreparedAssetGeneration,
) -> AsyncIterator[str]:
    label = _label(prepared.kind)
    opening = (
        f"开始提取{label}..."
        if prepared.action == "extract"
        else f"开始判断{label}出场..."
    )
    yield _sse({"type": "start", "message": opening})
    yield _sse({"type": "status", "message": "正在加载模型配置..."})
    yield _sse({"type": "status", "message": f"使用模型: {prepared.model.name}"})
    if prepared.action == "judge" and not prepared.skip_merge:
        yield _sse({"type": "status", "message": f"正在合并已有{label}..."})
        yield _sse({"type": "status", "message": f"合并完成，共 {len(prepared.candidates or [])} 个{label}"})
        yield _sse({"type": "merged", **_merged_payload(prepared.kind, prepared.candidates or [])})
    yield _sse({"type": "status", "message": "正在调用AI模型..."})

    call = StoryboardAssetRuntimeCall(
        model=prepared.model,
        system_prompt=prepared.system_prompt,
        user_prompt=prepared.user_prompt,
        raw_content=prepared.raw_content,
        enable_thinking=prepared.enable_thinking,
        operation=f"{prepared.action}_{prepared.kind}",
    )
    outcome = _StreamOutcome()
    events = _consume_runtime(runtime, call, outcome)
    try:
        async for event in events:
            yield event
    finally:
        await events.aclose()
    if outcome.failure is not None:
        yield _sse({"type": "error", "message": outcome.failure})
        return

    full_text = outcome.text
    yield _sse({"type": "status", "message": f"正在解析{label}{'出场' if prepared.action == 'judge' else '数据'}..."})
    if prepared.action == "extract":
        rows = parse_extracted(prepared.kind, full_text)
        output_key, count_key = _extract_output_keys(prepared.kind)
        yield _sse({
            "type": "complete",
            output_key: rows,
            "raw_response": full_text,
            count_key: len(rows),
        })
        yield _sse({"type": "done"})
        return

    texts = prepared.storyboard_texts or []
    rows, parsed_ok = parse_appearances(prepared.kind, full_text)
    appearances, missing = pad_appearances(
        rows,
        len(texts),
        prepared.frame_numbers,
        parsed_ok=parsed_ok,
    )
    if missing:
        number_text = "、".join(str(number) for number in missing)
        yield _sse({
            "type": "warning",
            "message": (
                f"AI 少返回了 {len(missing)} 个分镜的{label}判定（分镜 {number_text}），"
                "这些分镜会保留原有绑定，可在确认弹窗里手工补"
            ),
        })
    output_key = _judge_candidate_key(prepared.kind)
    yield _sse({
        "type": "complete",
        "appearances": appearances,
        "missing_frames": missing,
        output_key: prepared.candidates or [],
        "raw_response": full_text,
    })
    yield _sse({"type": "done"})


@dataclass(slots=True)
class _StreamOutcome:
    text: str = ""
    failure: str | None = None


async def _consume_runtime(
    runtime: StoryboardAssetRuntime,
    call: StoryboardAssetRuntimeCall,
    outcome: _StreamOutcome,
) -> AsyncIterator[str]:
    full_text = ""
    pending = ""
    try:
        source = runtime.stream(call)
        try:
            async for chunk in source:
                if isinstance(chunk, str):
                    chunk_type = "content"
                    value: Any = chunk
                elif isinstance(chunk, Mapping):
                    chunk_type = chunk.get("type")
                    value = chunk.get("text") if chunk_type == "content" else chunk.get("message")
                else:
                    outcome.failure = "分镜资产运行端返回了无效内容"
                    return

                if chunk_type == "warning":
                    yield _sse({"type": "warning", "message": _safe_detail(value, call.model.api_key)})
                    continue
                if chunk_type == "error":
                    outcome.failure = _safe_detail(value, call.model.api_key)
                    return
                if chunk_type != "content" or not isinstance(value, str):
                    if isinstance(chunk, Mapping) and chunk_type in {"status", "info"}:
                        continue
                    outcome.failure = "分镜资产运行端返回了无效内容"
                    return

                safe_chunk, pending = _redact(pending + value, call.model.api_key)
                if safe_chunk:
                    full_text += safe_chunk
                    yield _sse({"type": "content", "text": safe_chunk, "full_text": full_text})
        finally:
            close = getattr(source, "aclose", None)
            if callable(close):
                await run_owned_awaitable(close())
        final_text, _pending = _redact(pending, call.model.api_key, final=True)
        if final_text:
            full_text += final_text
            yield _sse({"type": "content", "text": final_text, "full_text": full_text})
        outcome.text = full_text
    except Exception as error:
        outcome.failure = _safe_detail(getattr(error, "detail", None) or str(error), call.model.api_key)


def _redact(
    pending: str,
    secret: str,
    *,
    final: bool = False,
) -> tuple[str, str]:
    if not secret:
        return pending, ""
    output: list[str] = []
    remaining = pending
    while remaining:
        match_at = remaining.find(secret)
        if match_at >= 0:
            output.extend((remaining[:match_at], "[已隐藏]"))
            remaining = remaining[match_at + len(secret):]
            continue
        if final:
            output.append(remaining)
            return "".join(output), ""
        partial_at = len(remaining)
        for length in range(min(len(secret) - 1, len(remaining)), 0, -1):
            if remaining.endswith(secret[:length]):
                partial_at = len(remaining) - length
                break
        output.append(remaining[:partial_at])
        return "".join(output), remaining[partial_at:]
    return "".join(output), ""


def _safe_detail(value: Any, secret: str) -> str:
    detail = str(value or "生成失败").strip()
    if secret:
        detail = detail.replace(secret, "[已隐藏]")
    return detail[:2000] or "生成失败"


def _sse(payload: Mapping[str, Any]) -> str:
    return f"data: {json.dumps(dict(payload), ensure_ascii=False)}\n\n"


def _label(kind: str) -> str:
    return {"character": "角色", "scene": "场景", "prop": "道具"}[kind]


def _extract_output_keys(kind: str) -> tuple[str, str]:
    return {
        "character": ("characters", "character_count"),
        "scene": ("scenes", "scene_count"),
        "prop": ("props", "prop_count"),
    }[kind]


def _merged_payload(kind: str, candidates: list[Any]) -> dict[str, Any]:
    key = {
        "character": "merged_characters",
        "scene": "merged_scenes",
        "prop": "merged_props",
    }[kind]
    return {key: candidates}


def _judge_candidate_key(kind: str) -> str:
    return {"character": "merged_characters", "scene": "scenes", "prop": "props"}[kind]
