"""Detached legacy stream workers and SSE serialization."""

from __future__ import annotations

import asyncio
from contextlib import aclosing
import json
from collections.abc import Mapping
from typing import Any

from haoai_backend.storyboard_generation.execution import run_owned_in_threadpool

from .domain import PreparedStream, redact_chunk, redact_text
from .persistence import settle_stream
from .ports import ChatTextGenerationConfiguration, ChatTextGenerationRuntime, ChatTextRuntimeCall, SessionFactory


class ChatTextGenerationExecution:
    def __init__(
        self,
        runtime: ChatTextGenerationRuntime,
        session_factory: SessionFactory | None,
        configuration: ChatTextGenerationConfiguration | None,
    ) -> None:
        self._runtime = runtime
        self._session_factory = session_factory
        self._configuration = configuration
        self._workers: set[asyncio.Task[None]] = set()

    def start(self, prepared: PreparedStream) -> asyncio.Queue[dict[str, Any] | None]:
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        setattr(queue, "consumer_disconnected", False)
        worker = asyncio.create_task(self._run(prepared, queue))
        self._workers.add(worker)
        worker.add_done_callback(self._workers.discard)
        return queue

    async def wait_for_idle(self) -> None:
        while self._workers:
            await asyncio.gather(*tuple(self._workers), return_exceptions=True)

    async def _run(
        self,
        prepared: PreparedStream,
        queue: asyncio.Queue[dict[str, Any] | None],
    ) -> None:
        call = ChatTextRuntimeCall(
            model=prepared.model,
            messages=prepared.messages,
            enable_thinking=prepared.enable_thinking,
        )
        full_text = ""
        pending = ""
        error_detail: str | None = None
        error_event_sent = False
        settlement_started = False
        try:
            async with aclosing(self._runtime.stream(call)) as events:
                async for event in events:
                    if isinstance(event, str):
                        event_type = "content"
                        content: object = event
                        other: Mapping[str, object] = {}
                    elif isinstance(event, Mapping):
                        event_type = str(event.get("type", "content"))
                        content = event.get("text", "")
                        other = event
                    else:
                        raise RuntimeError("模型服务返回了无效响应")

                    if event_type == "error":
                        detail = str(other.get("detail") or other.get("message") or content)
                        error_detail = redact_text(detail, prepared.original_key)
                        await _publish(
                            queue,
                            {
                                "type": "error",
                                "message": error_detail,
                                "detail": error_detail,
                            }
                        )
                        error_event_sent = True
                        break

                    if event_type not in {"content", "warning", "status"}:
                        continue

                    safe_other = _redact_mapping(other, prepared.original_key)
                    if event_type == "content":
                        if not isinstance(content, str):
                            raise RuntimeError("模型服务返回了无效文本")
                        safe_part, pending = redact_chunk(
                            pending + content,
                            prepared.original_key,
                        )
                        if safe_part:
                            full_text += safe_part
                            await _publish(
                                queue,
                                {
                                    "type": "content",
                                    "text": safe_part,
                                    "full_text": full_text,
                                }
                            )
                    else:
                        await _publish(queue, dict(safe_other))

            if error_detail is None:
                tail, pending = redact_chunk(
                    pending,
                    prepared.original_key,
                    final=True,
                )
                full_text += tail
                if tail:
                    await _publish(
                        queue,
                        {"type": "content", "text": tail, "full_text": full_text}
                    )
                if not full_text:
                    error_detail = "AI 返回为空"

            succeeded = error_detail is None
            result = full_text if succeeded else error_detail
            settlement_started = True
            settled = await run_owned_in_threadpool(
                settle_stream,
                self._session_factory,
                prepared,
                succeeded=succeeded,
                result=result,
                clock=self._configuration.clock if self._configuration else None,
            )
            if not settled:
                error_detail = "任务结果暂时无法保存"
                await _publish(queue, {"type": "error", "message": error_detail})
                error_event_sent = True
            elif succeeded:
                await _publish(queue, {"type": "complete", "raw_response": full_text})
            elif error_detail and not error_event_sent:
                await _publish(queue, {"type": "error", "message": error_detail})
                error_event_sent = True
        except asyncio.CancelledError:
            safe_detail = redact_text("任务被取消", prepared.original_key)
            if not settlement_started:
                settlement_started = True
                try:
                    await run_owned_in_threadpool(
                        settle_stream,
                        self._session_factory,
                        prepared,
                        succeeded=False,
                        result=safe_detail,
                        clock=self._configuration.clock if self._configuration else None,
                    )
                except Exception:
                    pass
            if not error_event_sent:
                await _publish(
                    queue,
                    {"type": "error", "message": safe_detail, "detail": safe_detail},
                )
            raise
        except Exception as error:
            safe_detail = redact_text(str(error) or "聊天生成失败", prepared.original_key)
            settled = False
            if not settlement_started:
                settlement_started = True
                try:
                    settled = await run_owned_in_threadpool(
                        settle_stream,
                        self._session_factory,
                        prepared,
                        succeeded=False,
                        result=safe_detail,
                        clock=self._configuration.clock if self._configuration else None,
                    )
                except Exception:
                    settled = False
            if settled and not error_event_sent:
                await _publish(
                    queue,
                    {"type": "error", "message": safe_detail, "detail": safe_detail}
                )
                error_event_sent = True
            elif not settled and not error_event_sent:
                await _publish(
                    queue,
                    {"type": "error", "message": "任务结果暂时无法保存"}
                )
        finally:
            await _publish(queue, None, terminal=True)


async def sse_events(
    queue: asyncio.Queue[dict[str, Any] | None],
):
    try:
        yield _encode({"type": "start"})
        while True:
            event = await queue.get()
            if event is None:
                break
            yield _encode(event)
        yield _encode({"type": "done"})
    finally:
        disconnect_consumer(queue)


def disconnect_consumer(
    queue: asyncio.Queue[dict[str, Any] | None],
) -> None:
    setattr(queue, "consumer_disconnected", True)
    while not queue.empty():
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            break


async def _publish(
    queue: asyncio.Queue[dict[str, Any] | None],
    event: dict[str, Any] | None,
    *,
    terminal: bool = False,
) -> None:
    if not getattr(queue, "consumer_disconnected", False):
        await queue.put(event)


def _encode(event: Mapping[str, Any]) -> str:
    return "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"


def _redact_mapping(
    value: Mapping[str, object],
    secret: str,
) -> Mapping[str, object]:
    return {
        key: _redact_value(item, secret)
        for key, item in value.items()
    }


def _redact_value(value: object, secret: str) -> object:
    if isinstance(value, str):
        return redact_text(value, secret)
    if isinstance(value, Mapping):
        return _redact_mapping(value, secret)
    if isinstance(value, list):
        return [_redact_value(item, secret) for item in value]
    return value
