"""Retained two-pass workers with billing settlement before terminal SSE events."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from starlette.concurrency import run_in_threadpool

from .application import safe_provider_detail
from .domain import StoryboardRuntimeCall, parse_scene_segments
from .errors import StoryboardGenerationBadRequest, StoryboardProviderFailure
from .execution import (
    _frame_user_prompt,
    _legacy_payload,
    _redact_stream_chunk,
    _scene_user_prompt,
)
from .ports import StoryboardGenerationRuntime
from .two_pass_billing import (
    TwoPassAdmission,
    TwoPassBillingService,
    TwoPassExecutionInput,
    parse_generated_frames,
)


class TwoPassStoryboardExecution:
    """Owns each admitted provider run independently of the HTTP reader."""

    def __init__(
        self,
        runtime: StoryboardGenerationRuntime,
        billing: TwoPassBillingService,
    ) -> None:
        self._runtime = runtime
        self._billing = billing
        self._workers: set[asyncio.Task[None]] = set()

    def start(self, execution: TwoPassExecutionInput) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        worker = asyncio.create_task(self._run(execution, queue))
        self._workers.add(worker)
        worker.add_done_callback(self._workers.discard)
        return queue

    async def _run(
        self,
        execution: TwoPassExecutionInput,
        queue: asyncio.Queue[dict[str, Any]],
    ) -> None:
        try:
            await queue.put({"type": "status", "message": "Step 1/2: 正在分析文章结构..."})
            scene_user = _scene_user_prompt(
                execution.scene_prompt.user_prompt,
                execution.raw_content,
            )
            scene_call = StoryboardRuntimeCall(
                execution.model,
                execution.scene_prompt.system_prompt,
                scene_user,
                "",
                execution.temperature,
                execution.enable_thinking,
                "scene_segmentation",
            )
            await queue.put(_payload_event(scene_call))
            scene_output = await self._stream_content(scene_call, queue)
            segments = parse_scene_segments(scene_output)
            if not segments:
                raise StoryboardGenerationBadRequest("AI 返回的分段格式无法解析")
            await queue.put({"type": "segments", "segments": segments, "count": len(segments)})
            await queue.put({"type": "segment_complete", "count": len(segments)})

            if execution.skip_frame_generation:
                frames = [{"text": str(segment["text"]), "duration": 5} for segment in segments]
            else:
                await queue.put({"type": "status", "message": "Step 2/2: 正在为所有片段生成分镜..."})
                frame_user = _frame_user_prompt(
                    execution.frame_prompt.user_prompt,
                    segments,
                )
                frame_call = StoryboardRuntimeCall(
                    execution.model,
                    execution.frame_prompt.system_prompt,
                    frame_user,
                    "",
                    execution.temperature,
                    execution.enable_thinking,
                    "frame_generation",
                )
                await queue.put(_payload_event(frame_call))
                frame_output = await self._stream_content(
                    frame_call,
                    queue,
                    event_type="step2_output",
                )
                try:
                    frames = parse_generated_frames(frame_output, segments)
                except ValueError as error:
                    raise StoryboardGenerationBadRequest(str(error)) from error

            result = {"frames": frames, "segments": segments}
            settled = await run_in_threadpool(
                self._billing.mark_completed,
                execution,
                result,
            )
            if not settled:
                await queue.put({
                    "type": "pending",
                    "message": "生成结果正在核对，请稍后查询任务状态",
                })
                return

            for index, (segment, frame) in enumerate(zip(segments, frames, strict=True)):
                text = str(segment["text"])
                await queue.put({
                    "type": "frame_progress",
                    "current": index + 1,
                    "total": len(segments),
                    "segment_summary": text[:30] + "..." if len(text) > 30 else text,
                })
                await queue.put({"type": "frame", "index": index, "frame": frame})
            await queue.put({
                "type": "complete",
                "frames": frames,
                "segments": segments,
                "frame_count": len(frames),
            })
        except asyncio.CancelledError:
            await self._settle_failure(execution, "分镜生成任务已中断", queue)
            raise
        except Exception as error:
            detail = _safe_detail(error, execution)
            await self._settle_failure(execution, detail, queue)

    async def _stream_content(
        self,
        call: StoryboardRuntimeCall,
        queue: asyncio.Queue[dict[str, Any]],
        *,
        event_type: str = "content",
    ) -> str:
        pending = ""
        full_text = ""
        try:
            async for chunk in self._runtime.stream(call):
                if not isinstance(chunk, str):
                    raise StoryboardProviderFailure("AI 服务返回了无效内容")
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
        except Exception as error:
            raise StoryboardProviderFailure(_safe_runtime_error(error, call.model.api_key)) from None

        final_chunk, _ = _redact_stream_chunk(pending, call.model.api_key, final=True)
        if final_chunk:
            full_text += final_chunk
            event = {"type": event_type, "text": final_chunk}
            if event_type == "content":
                event["full_text"] = full_text
            await queue.put(event)
        return full_text

    async def _settle_failure(
        self,
        execution: TwoPassExecutionInput,
        detail: str,
        queue: asyncio.Queue[dict[str, Any]],
    ) -> None:
        try:
            settled = await run_in_threadpool(
                self._billing.mark_failed,
                execution,
                detail,
            )
        except Exception:
            settled = False
        if not settled:
            await queue.put({
                "type": "pending",
                "message": "任务结算状态正在核对，请稍后查询任务状态",
            })
            return
        await queue.put({"type": "error", "message": detail})


def replay_events(admission: TwoPassAdmission) -> asyncio.Queue[dict[str, Any]]:
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    if admission.kind == "completed":
        result = admission.result or {}
        frames = _valid_saved_frames(result.get("frames"))
        segments = result.get("segments")
        safe_segments = segments if isinstance(segments, list) else []
        queue.put_nowait({
            "type": "complete",
            "frames": frames,
            "segments": safe_segments,
            "frame_count": len(frames),
        })
    elif admission.kind == "failed":
        detail = admission.result.get("error") if admission.result else None
        queue.put_nowait({
            "type": "error",
            "message": detail if isinstance(detail, str) and detail else "分镜生成失败",
        })
    else:
        queue.put_nowait({"type": "pending", "message": "任务仍在处理中，请稍后查询任务状态"})
    return queue


def _payload_event(call: StoryboardRuntimeCall) -> dict[str, Any]:
    return {
        "type": "payload",
        "data": json.dumps(_legacy_payload(call), ensure_ascii=False, indent=2),
    }


def _safe_detail(error: BaseException, execution: TwoPassExecutionInput) -> str:
    detail = safe_provider_detail(error, execution.model)
    if execution.model.base_url:
        detail = detail.replace(execution.model.base_url, "[服务端模型地址]")
    return detail[:1000] or "分镜生成失败"


def _safe_runtime_error(error: BaseException, api_key: str) -> str:
    detail = getattr(error, "detail", None)
    message = detail if isinstance(detail, str) else str(error)
    if api_key:
        message = message.replace(api_key, "[已隐藏]")
    return message[:500] or "AI 服务暂时不可用"


def _valid_saved_frames(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    frames: list[dict[str, object]] = []
    for item in value:
        if isinstance(item, dict) and isinstance(item.get("text"), str) and item["text"].strip():
            frames.append({"text": item["text"], "duration": item.get("duration", 5)})
    return frames


__all__ = ("TwoPassStoryboardExecution", "replay_events")
