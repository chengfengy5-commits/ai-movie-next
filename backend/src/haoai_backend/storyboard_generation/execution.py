"""Owned asynchronous SSE workers for storyboard and two-pass generation."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Mapping, Sequence
from typing import Any, Callable, TypeVar

from starlette.concurrency import run_in_threadpool

from haoai_backend.shared.identity import TrustedActor

from .application import (
    PreparedStoryboard,
    PreparedTwoPassStoryboard,
    safe_provider_detail,
    safe_raw_response,
    save_task_result,
)
from .domain import StoryboardRuntimeCall, parse_all_frames, parse_scene_segments, parse_storyboard
from .errors import StoryboardGenerationBadRequest, StoryboardProviderFailure
from .ports import StoryboardGenerationRuntime, StoryboardGenerationUnitOfWorkFactory


T = TypeVar("T")


class StoryboardGenerationExecution:
    def __init__(
        self,
        runtime: StoryboardGenerationRuntime,
        uow_factory: StoryboardGenerationUnitOfWorkFactory | None,
    ) -> None:
        self.runtime = runtime
        self.uow_factory = uow_factory
        self._workers: set[asyncio.Task[None]] = set()

    def start_storyboard_stream(self, prepared: PreparedStoryboard) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        worker = asyncio.create_task(self._run_storyboard_stream(prepared, queue))
        self._retain(worker)
        return queue

    def start_two_pass_stream(self, prepared: PreparedTwoPassStoryboard) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        worker = asyncio.create_task(self._run_two_pass(prepared, queue))
        self._retain(worker)
        return queue

    async def _run_storyboard_stream(
        self, prepared: PreparedStoryboard, queue: asyncio.Queue[dict[str, Any]]
    ) -> None:
        raw_response = ""
        try:
            await queue.put({"type": "status", "message": "正在加载模型配置..."})
            await queue.put({"type": "status", "message": f"使用模型: {prepared.model.name}"})
            call = StoryboardRuntimeCall(
                prepared.model,
                prepared.prompt.system_prompt,
                prepared.prompt.user_prompt,
                prepared.raw_content,
                prepared.temperature,
                operation="storyboard",
            )
            await queue.put({"type": "status", "message": "正在调用AI模型..."})
            await queue.put({
                "type": "payload",
                "data": json.dumps(_legacy_payload(call), ensure_ascii=False, indent=2),
            })
            raw_response = await self._stream_content(call, queue)
            await queue.put({"type": "status", "message": "正在解析分镜..."})
            frames = parse_storyboard(raw_response)
            await self._save_task(
                prepared.actor, prepared.task_id, "completed",
                {"frames": frames, "raw_response": raw_response},
            )
            await queue.put({
                "type": "complete",
                "frames": frames,
                "raw_response": raw_response,
                "frame_count": len(frames),
            })
        except asyncio.CancelledError:
            await self._save_task(prepared.actor, prepared.task_id, "failed", {"error": "任务被取消"})
            raise
        except Exception as error:
            detail = safe_provider_detail(error, prepared.model)
            await self._save_task(prepared.actor, prepared.task_id, "failed", {"error": detail})
            await queue.put({"type": "error", "message": detail})

    async def _run_two_pass(
        self, prepared: PreparedTwoPassStoryboard, queue: asyncio.Queue[dict[str, Any]]
    ) -> None:
        try:
            await queue.put({"type": "status", "message": "Step 1/2: 正在分析文章结构..."})
            scene_user = _scene_user_prompt(prepared.scene_prompt.user_prompt, prepared.raw_content)
            call = StoryboardRuntimeCall(
                prepared.model,
                prepared.scene_prompt.system_prompt,
                scene_user,
                "",
                prepared.temperature,
                prepared.enable_thinking,
                "scene_segmentation",
            )
            await queue.put({
                "type": "payload",
                "data": json.dumps(_legacy_payload(call), ensure_ascii=False, indent=2),
            })
            raw = await self._stream_content(call, queue)
            segments = parse_scene_segments(raw)
            if not segments:
                raise StoryboardGenerationBadRequest("AI 返回的分段格式无法解析")
            segments = [
                {
                    **segment,
                    "text": safe_raw_response(str(segment["text"]), prepared.model),
                }
                for segment in segments
            ]
            await queue.put({"type": "segments", "segments": segments, "count": len(segments)})
            await queue.put({"type": "segment_complete", "count": len(segments)})

            if prepared.skip_frame_generation:
                await queue.put({
                    "type": "status",
                    "message": "跳过 AI 分镜生成，直接使用拆分结果作为分镜...",
                })
                frames = [{"text": str(item["text"]), "duration": 5} for item in segments]
            else:
                await queue.put({"type": "status", "message": "Step 2/2: 正在为所有片段生成分镜..."})
                frame_user = _frame_user_prompt(prepared.frame_prompt.user_prompt, segments)
                frame_call = StoryboardRuntimeCall(
                    prepared.model,
                    prepared.frame_prompt.system_prompt,
                    frame_user,
                    "",
                    prepared.temperature,
                    prepared.enable_thinking,
                    "frame_generation",
                )
                raw_frames = await self._stream_content(
                    frame_call,
                    queue,
                    event_type="step2_output",
                )
                frames = parse_all_frames(raw_frames, segments)

            for index, (segment, frame) in enumerate(zip(segments, frames, strict=True)):
                text = str(segment["text"])
                await queue.put({
                    "type": "frame_progress",
                    "current": index + 1,
                    "total": len(segments),
                    "segment_summary": text[:30] + "..." if len(text) > 30 else text,
                })
                await queue.put({"type": "frame", "index": index, "frame": frame})
            result = {"frames": frames, "segments": segments}
            await self._save_task(prepared.actor, prepared.task_id, "completed", result)
            await queue.put({
                "type": "complete",
                "frames": frames,
                "segments": segments,
                "frame_count": len(frames),
            })
        except asyncio.CancelledError:
            await self._save_task(prepared.actor, prepared.task_id, "failed", {"error": "任务被取消"})
            raise
        except Exception as error:
            detail = safe_provider_detail(error, prepared.model)
            await self._save_task(prepared.actor, prepared.task_id, "failed", {"error": detail})
            await queue.put({"type": "error", "message": detail})

    async def _stream_content(
        self,
        call: StoryboardRuntimeCall,
        queue: asyncio.Queue[dict[str, Any]],
        *,
        event_type: str = "content",
    ) -> str:
        pending = ""
        full_text = ""
        async for chunk in self.runtime.stream(call):
            if not isinstance(chunk, str):
                raise StoryboardProviderFailure("分镜运行端返回了无效内容")
            safe_chunk, pending = _redact_stream_chunk(
                pending + chunk,
                call.model.api_key,
            )
            if safe_chunk:
                full_text += safe_chunk
                event = {"type": event_type, "text": safe_chunk}
                if event_type == "content":
                    event["full_text"] = full_text
                await queue.put(event)
        final_chunk, _ = _redact_stream_chunk(
            pending,
            call.model.api_key,
            final=True,
        )
        if final_chunk:
            full_text += final_chunk
            event = {"type": event_type, "text": final_chunk}
            if event_type == "content":
                event["full_text"] = full_text
            await queue.put(event)
        return full_text

    async def _save_task(
        self,
        actor: TrustedActor,
        task_id: str | None,
        status: str,
        result: object,
    ) -> None:
        if task_id is None or self.uow_factory is None:
            return
        try:
            await run_owned_in_threadpool(
                save_task_result,
                self.uow_factory,
                actor,
                task_id,
                status=status,
                result=result,
            )
        except Exception:
            # A lost/failed task-status write is not retried, which avoids
            # replaying provider work after an unknown commit result.
            return

    def _retain(self, worker: asyncio.Task[None]) -> None:
        self._workers.add(worker)
        worker.add_done_callback(self._workers.discard)


async def run_owned_awaitable(awaitable: Awaitable[T]) -> T:
    """Keep async resolver work alive until its awaited resource cleanup finishes."""
    async def invoke() -> T:
        return await awaitable

    worker = asyncio.create_task(invoke())
    return await _drain_owned_worker(worker)


async def run_owned_in_threadpool(
    function: Callable[..., T],
    *args: Any,
    **kwargs: Any,
) -> T:
    """Run synchronous work to completion before propagating request cancellation."""
    worker = asyncio.create_task(run_in_threadpool(function, *args, **kwargs))
    return await _drain_owned_worker(worker)


async def _drain_owned_worker(worker: asyncio.Task[T]) -> T:
    cancellation_requested = False
    while not worker.done():
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError:
            cancellation_requested = True
        except Exception:
            break

    if worker.cancelled():
        raise asyncio.CancelledError

    try:
        result = worker.result()
    except Exception:
        if cancellation_requested:
            raise asyncio.CancelledError
        raise

    if cancellation_requested:
        raise asyncio.CancelledError
    return result


def _legacy_payload(call: StoryboardRuntimeCall) -> dict[str, Any]:
    user_text = call.user_prompt
    if call.raw_content:
        user_text += "\n\n" + call.raw_content
    payload: dict[str, Any] = {
        "model": call.model.model,
        "messages": [
            {"role": "system", "content": call.system_prompt},
            {"role": "user", "content": user_text},
        ],
        "temperature": call.temperature,
        "stream": True,
    }
    if call.enable_thinking:
        payload["thinking"] = {"type": "enabled"}
        payload["reasoning_effort"] = "high"
    elif call.operation == "scene_segmentation":
        payload["thinking"] = {"type": "disabled"}
    return payload


def _scene_user_prompt(template: str, raw_content: str) -> str:
    value = template.replace("{content}", "").strip()
    if value:
        value += "\n\n"
    return value + raw_content


def _frame_user_prompt(
    template: str, segments: Sequence[Mapping[str, object]]
) -> str:
    combined_text = "_::~RECORD::~_".join(str(segment["text"]) for segment in segments)
    return f"{template}\n\n{combined_text}" if template else combined_text



def _redact_stream_chunk(
    pending: str,
    secret: str,
    *,
    final: bool = False,
) -> tuple[str, str]:
    if not secret:
        return pending, ""

    safe_parts: list[str] = []
    remaining = pending
    while remaining:
        match_at = remaining.find(secret)
        if match_at >= 0:
            safe_parts.extend((remaining[:match_at], "[已隐藏]"))
            remaining = remaining[match_at + len(secret):]
            continue

        if final:
            safe_parts.append(remaining)
            return "".join(safe_parts), ""

        partial_at = len(remaining)
        for length in range(min(len(secret) - 1, len(remaining)), 0, -1):
            if remaining.endswith(secret[:length]):
                partial_at = len(remaining) - length
                break
        safe_parts.append(remaining[:partial_at])
        return "".join(safe_parts), remaining[partial_at:]

    return "".join(safe_parts), ""
