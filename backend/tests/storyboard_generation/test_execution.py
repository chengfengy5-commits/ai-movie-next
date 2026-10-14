from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any

from haoai_backend.shared.identity import TrustedActor
from haoai_backend.storyboard_generation.application import (
    PreparedStoryboard,
    PreparedTwoPassStoryboard,
    standard_runtime_call,
)
from haoai_backend.storyboard_generation.domain import (
    StoryboardPrompt,
    StoryboardRuntimeCall,
    StoryboardRuntimeResult,
    StoryboardServerModel,
)
from haoai_backend.storyboard_generation.execution import StoryboardGenerationExecution
from haoai_backend.storyboard_generation.errors import StoryboardProviderFailure


MARKED = "_::~OUTPUT_START::~_有完整文本。_::~OUTPUT_END::~_"


class Runtime:
    def __init__(self, *, gate: asyncio.Event | None = None) -> None:
        self.gate = gate
        self.started = asyncio.Event()
        self.operations: list[str] = []
        self.calls: list[StoryboardRuntimeCall] = []

    async def generate(self, call: StoryboardRuntimeCall) -> StoryboardRuntimeResult:
        return StoryboardRuntimeResult(MARKED)

    async def stream(self, call: StoryboardRuntimeCall) -> AsyncIterator[str]:
        self.started.set()
        self.operations.append(call.operation)
        self.calls.append(call)
        if self.gate is not None:
            await self.gate.wait()
        if call.operation == "scene_segmentation":
            yield "片段一_::~RECORD::~_片段二"
        elif call.operation == "frame_generation":
            yield "画面一_::~RECORD::~_画面二"
        else:
            yield MARKED


class UnitOfWork:
    def __init__(self, statuses: list[tuple[str, str | None]]) -> None:
        self.statuses = statuses

    def write_task_result(self, actor, task_id, *, status, result):
        self.statuses.append((status, result))
        return True

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def _prepared(task_id: str | None = None) -> PreparedStoryboard:
    return PreparedStoryboard(
        actor=TrustedActor("member"),
        model=StoryboardServerModel("model", "Model", "provider", "synthetic-key"),
        prompt=StoryboardPrompt("", ""),
        raw_content="剧本",
        temperature=0.7,
        task_id=task_id,
    )


def test_two_pass_skip_uses_parsed_segments_and_never_calls_frame_runtime() -> None:
    async def scenario():
        runtime = Runtime()
        execution = StoryboardGenerationExecution(runtime, None)
        prepared = PreparedTwoPassStoryboard(
            TrustedActor("member"),
            StoryboardServerModel("model", "Model", "provider", "synthetic-key"),
            StoryboardPrompt("", ""),
            StoryboardPrompt("", ""),
            "原始剧本",
            0.7,
            False,
            None,
            True,
        )
        queue = execution.start_two_pass_stream(prepared)
        events = []
        while True:
            event = await queue.get()
            events.append(event)
            if event["type"] == "complete":
                break
        complete = events[-1]
        assert complete["segments"] == [
            {"index": 0, "text": "片段一"},
            {"index": 1, "text": "片段二"},
        ]
        assert complete["frames"] == [
            {"text": "片段一", "duration": 5},
            {"text": "片段二", "duration": 5},
        ]
        assert "frame_generation" not in runtime.operations
        assert any(event["type"] == "segment_complete" for event in events)

    asyncio.run(scenario())


def test_stream_worker_finishes_bound_task_after_consumer_stops_reading() -> None:
    async def scenario():
        gate = asyncio.Event()
        runtime = Runtime(gate=gate)
        statuses: list[tuple[str, str | None]] = []
        execution = StoryboardGenerationExecution(runtime, lambda: UnitOfWork(statuses))
        queue = execution.start_storyboard_stream(_prepared("task-owned"))
        await runtime.started.wait()
        # The consumer drops the queue; the retained worker must continue.
        gate.set()
        for _ in range(100):
            if statuses:
                break
            await asyncio.sleep(0.01)
        assert statuses and statuses[0][0] == "completed"
        assert not queue.empty()
        assert runtime.operations == ["storyboard"]

    asyncio.run(scenario())


def test_provider_error_is_terminal_without_fake_done_and_secret_is_redacted() -> None:
    class FailingRuntime(Runtime):
        async def stream(self, call: StoryboardRuntimeCall) -> AsyncIterator[str]:
            self.started.set()
            raise StoryboardProviderFailure("provider rejected synthetic-key")
            yield ""

    async def scenario():
        statuses: list[tuple[str, str | None]] = []
        runtime = FailingRuntime()
        execution = StoryboardGenerationExecution(runtime, lambda: UnitOfWork(statuses))
        prepared = _prepared("task-error")
        queue = execution.start_storyboard_stream(prepared)
        events = []
        while True:
            event = await queue.get()
            events.append(event)
            if event["type"] in {"error", "complete"}:
                break
        assert events[-1] == {
            "type": "error",
            "message": "provider rejected [已隐藏]",
        }
        assert not any(event["type"] == "done" for event in events)
        assert statuses == [("failed", '{"error": "provider rejected [已隐藏]"}')]

    asyncio.run(scenario())



def test_two_pass_emits_raw_step_two_chunks_and_parses_fixed_duration_frames() -> None:
    async def scenario():
        runtime = Runtime()
        execution = StoryboardGenerationExecution(runtime, None)
        prepared = PreparedTwoPassStoryboard(
            TrustedActor("member"),
            StoryboardServerModel("model", "Model", "provider", "synthetic-key"),
            StoryboardPrompt("scene system", "scene template"),
            StoryboardPrompt("frame system", "frame template"),
            "原始剧本",
            0.7,
            False,
            None,
            False,
        )
        queue = execution.start_two_pass_stream(prepared)
        events = []
        while True:
            event = await queue.get()
            events.append(event)
            if event["type"] == "complete":
                break

        assert runtime.operations == ["scene_segmentation", "frame_generation"]
        assert events[-1]["frames"] == [
            {"text": "画面一", "duration": 5},
            {"text": "画面二", "duration": 5},
        ]
        assert [event["text"] for event in events if event["type"] == "step2_output"] == [
            "画面一_::~RECORD::~_画面二",
        ]

    asyncio.run(scenario())


def test_non_stream_uses_fixed_legacy_temperature_but_stream_keeps_request_value() -> None:
    async def scenario():
        prepared = replace(_prepared(), temperature=0.2)
        assert standard_runtime_call(prepared).temperature == 0.7
        runtime = Runtime()
        execution = StoryboardGenerationExecution(runtime, None)
        queue = execution.start_storyboard_stream(prepared)
        while (await queue.get())["type"] != "complete":
            pass
        assert runtime.calls[0].temperature == 0.2

    asyncio.run(scenario())


def test_two_pass_provider_failure_is_failed_without_fake_complete_or_retry() -> None:
    class FailingFrameRuntime(Runtime):
        async def stream(self, call: StoryboardRuntimeCall) -> AsyncIterator[str]:
            self.operations.append(call.operation)
            self.calls.append(call)
            if call.operation == "scene_segmentation":
                yield "片段一_::~RECORD::~_片段二"
                return
            raise StoryboardProviderFailure("frame provider failed synthetic-key")
            yield ""

    async def scenario() -> None:
        statuses: list[tuple[str, str | None]] = []
        runtime = FailingFrameRuntime()
        execution = StoryboardGenerationExecution(runtime, lambda: UnitOfWork(statuses))
        prepared = PreparedTwoPassStoryboard(
            TrustedActor("member"),
            StoryboardServerModel("model", "Model", "provider", "synthetic-key"),
            StoryboardPrompt("scene system", "scene template"),
            StoryboardPrompt("frame system", "frame template"),
            "原始剧本",
            0.7,
            False,
            "task-step-two-failure",
            False,
        )
        queue = execution.start_two_pass_stream(prepared)
        events: list[dict[str, Any]] = []
        while True:
            event = await queue.get()
            events.append(event)
            if event["type"] in {"error", "complete"}:
                break

        assert runtime.operations == ["scene_segmentation", "frame_generation"]
        assert events[-1] == {
            "type": "error",
            "message": "frame provider failed [已隐藏]",
        }
        assert not any(event["type"] in {"complete", "done"} for event in events)
        assert statuses == [
            ("failed", '{"error": "frame provider failed [已隐藏]"}')
        ]

    asyncio.run(scenario())
