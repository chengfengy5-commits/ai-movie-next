from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import Request
from fastapi.routing import APIRoute

from haoai_backend.shared.identity import TrustedActor
from haoai_backend.storyboard_generation.domain import StoryboardServerModel
from haoai_backend.storyboard_asset_generation.application import PreparedAssetGeneration
from haoai_backend.storyboard_asset_generation.execution import stream_asset_events
from haoai_backend.storyboard_asset_generation.http import build_storyboard_asset_generation_router


class ChunkRuntime:
    def __init__(self, chunks: list[Any]) -> None:
        self.chunks = chunks
        self.closed = False

    async def stream(self, call: Any) -> AsyncIterator[Any]:
        del call
        try:
            for chunk in self.chunks:
                yield chunk
        finally:
            self.closed = True


def _prepared(*, kind: str = "character", action: str = "extract") -> PreparedAssetGeneration:
    return PreparedAssetGeneration(
        actor=TrustedActor("actor"),
        kind=kind,
        action=action,
        model=StoryboardServerModel(
            id="model", name="local", provider="local", api_key="abab", model=None
        ),
        system_prompt="system",
        user_prompt="user",
        raw_content="剧本文本",
        enable_thinking=False,
        candidates=None,
    )


def test_content_is_redacted_across_provider_chunks_before_sse_and_raw_response() -> None:
    runtime = ChunkRuntime(["before ab", "ab", " after", {"type": "warning", "message": "提示"}, "[]"])

    async def collect() -> list[dict[str, Any]]:
        events = []
        async for value in stream_asset_events(runtime, _prepared()):
            if value.startswith("data: "):
                events.append(json.loads(value[6:]))
        return events

    events = asyncio.run(collect())
    serialized = json.dumps(events, ensure_ascii=False)
    assert "abab" not in serialized
    assert "[已隐藏]" in serialized
    assert next(event for event in events if event["type"] == "complete")["raw_response"] == "before [已隐藏] after[]"
    assert any(event == {"type": "warning", "message": "提示"} for event in events)
    assert events[-1] == {"type": "done"}


def test_closing_request_stream_closes_the_provider_without_fake_terminal_events() -> None:
    runtime = ChunkRuntime(["partial"])

    async def close_after_content() -> list[dict[str, Any]]:
        events = stream_asset_events(runtime, _prepared())
        seen = []
        async for value in events:
            if value.startswith("data: "):
                event = json.loads(value[6:])
                seen.append(event)
                if event.get("type") == "content":
                    await events.aclose()
                    break
        return seen

    emitted = asyncio.run(close_after_content())
    assert runtime.closed is True
    assert emitted[-1]["type"] == "content"
    assert not any(event["type"] in {"complete", "done", "error"} for event in emitted)


class _RouterUnitOfWork:
    def get_chapter(self, chapter_id: str):
        assert chapter_id == "chapter"
        return {"series_id": "series", "content": [{"text": "分镜描述"}]}

    def require_series_access(self, actor: TrustedActor, series_id: str) -> None:
        assert actor.user_id == "member"
        assert series_id == "series"

    def resolve_model(self, actor: TrustedActor, model_id: str) -> StoryboardServerModel:
        assert actor.user_id == "member"
        assert model_id == "model"
        return StoryboardServerModel(
            id="model", name="local", provider="local", api_key="", model=None
        )

    def resolve_prompt(self, actor: TrustedActor, prompt_id: str) -> tuple[str, str]:
        raise AssertionError("request has no selected prompt")

    def merge_candidates(self, actor: TrustedActor, *, kind: str, series_id: str, candidates: list[Any]):
        raise AssertionError("extract endpoint does not merge candidates")

    def commit(self) -> None:
        raise AssertionError("extract endpoint does not write")

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None


class _GatedCloseRuntime:
    def __init__(self) -> None:
        self.close_started = asyncio.Event()
        self.release_close = asyncio.Event()
        self.provider_closed = asyncio.Event()
        self.source: AsyncIterator[str] | None = None

    def stream(self, call: Any) -> AsyncIterator[str]:
        del call

        async def chunks() -> AsyncIterator[str]:
            try:
                yield "[]"
            finally:
                self.close_started.set()
                await self.release_close.wait()
                self.provider_closed.set()

        self.source = chunks()
        return self.source


def _json_request(path: str, body: dict[str, Any]) -> Request:
    payload = json.dumps(body).encode("utf-8")
    messages = [{"type": "http.request", "body": payload, "more_body": False}]

    async def receive() -> dict[str, Any]:
        if messages:
            return messages.pop(0)
        return {"type": "http.disconnect"}

    return Request({
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 43123),
        "server": ("127.0.0.1", 80),
        "root_path": "",
    }, receive)


@pytest.mark.parametrize("cancel_count", [0, 2], ids=["aclose", "repeated-cancel"])
def test_actual_router_response_close_drains_provider_cleanup(cancel_count: int) -> None:
    runtime = _GatedCloseRuntime()
    path = "/api/storyboard/extract-characters-stream"
    router = build_storyboard_asset_generation_router(
        uow_factory=_RouterUnitOfWork,
        resolve_actor=lambda request: TrustedActor("member"),
        runtime=runtime,
    )

    async def run() -> None:
        route = next(
            route for route in router.routes
            if isinstance(route, APIRoute) and route.path == path
        )
        response = await route.endpoint(
            _json_request(path, {"chapter_id": "chapter", "model_id": "model"}),
            actor=TrustedActor("member"),
        )
        iterator = response.body_iterator
        seen: list[dict[str, Any]] = []
        close_task: asyncio.Task[None] | None = None
        try:
            while True:
                value = await asyncio.wait_for(iterator.__anext__(), timeout=2)
                event = json.loads(value.removeprefix("data: ").strip())
                seen.append(event)
                if event.get("type") == "content":
                    break

            close_task = asyncio.create_task(iterator.aclose())
            await asyncio.wait_for(runtime.close_started.wait(), timeout=2)
            if cancel_count:
                for _ in range(cancel_count):
                    close_task.cancel()
                    await asyncio.sleep(0)
                assert not close_task.done()
            else:
                assert not close_task.done()
            assert not runtime.provider_closed.is_set()

            runtime.release_close.set()
            if cancel_count:
                with pytest.raises(asyncio.CancelledError):
                    await close_task
            else:
                await asyncio.wait_for(close_task, timeout=2)
            await asyncio.wait_for(runtime.provider_closed.wait(), timeout=2)
            assert seen[-1]["type"] == "content"
            assert not any(event.get("type") in {"complete", "done", "error"} for event in seen)
        finally:
            runtime.release_close.set()
            if close_task is not None and not close_task.done():
                try:
                    await asyncio.wait_for(close_task, timeout=2)
                except BaseException:
                    pass
            if runtime.source is not None and not runtime.provider_closed.is_set():
                try:
                    await asyncio.wait_for(runtime.source.aclose(), timeout=2)
                except BaseException:
                    pass
            if not runtime.provider_closed.is_set():
                try:
                    await asyncio.wait_for(runtime.provider_closed.wait(), timeout=2)
                except BaseException:
                    pass

    asyncio.run(run())
