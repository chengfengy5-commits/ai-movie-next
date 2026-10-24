from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

import pytest

from haoai_backend.shared.identity import TrustedActor
from haoai_backend.storyboard_asset_generation.application import PreparedAssetGeneration
from haoai_backend.storyboard_asset_generation.execution import stream_asset_events
from haoai_backend.storyboard_asset_generation.ports import StoryboardAssetRuntimeCall
from haoai_backend.storyboard_asset_generation.schemas import JudgeRequest
from haoai_backend.storyboard_generation.domain import StoryboardServerModel


WIRE_FIXTURE = json.loads(
    (Path(__file__).resolve().parents[2] / "frontend/src/testing/fixtures/storyboardAssetWire.json")
    .read_text(encoding="utf-8")
)


@pytest.mark.parametrize("contract", WIRE_FIXTURE["judge_requests"], ids=lambda item: item["kind"])
def test_shared_judge_request_fixture_is_accepted_by_the_actual_backend_parser(contract: Mapping[str, Any]) -> None:
    kind = contract["kind"]
    wire = contract["wire"]

    parsed = JudgeRequest.parse(wire, kind)

    key = f"{kind}s"
    assert parsed.chapter_id == wire["chapter_id"]
    assert parsed.model_id == wire["model_id"]
    assert parsed.prompt_id == wire["prompt_id"]
    assert parsed.enable_thinking is wire["enable_thinking"]
    assert parsed.skip_merge is wire["skip_merge"]
    assert parsed.candidates == wire[key]
    assert parsed.storyboard_texts == wire["storyboard_texts"]
    assert parsed.frame_numbers == wire["frame_numbers"]


class FixtureRuntime:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[StoryboardAssetRuntimeCall] = []

    async def stream(self, call: StoryboardAssetRuntimeCall) -> AsyncIterator[str]:
        self.calls.append(call)
        yield self.response


def _events(runtime: FixtureRuntime, prepared: PreparedAssetGeneration) -> list[dict[str, Any]]:
    async def collect() -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        async for raw_event in stream_asset_events(runtime, prepared):
            assert raw_event.startswith("data: ")
            result.append(json.loads(raw_event[6:]))
        return result

    return asyncio.run(collect())


@pytest.mark.parametrize("kind", ["character", "scene", "prop"])
@pytest.mark.parametrize("action", ["extract", "judge"])
def test_actual_generation_producer_matches_shared_frontend_wire_fixture(kind: str, action: str) -> None:
    runtime_text = WIRE_FIXTURE["producer"]["runtime"][action][kind]
    expected_complete = WIRE_FIXTURE["producer"][f"{action}_complete"][kind]
    candidate_key = {
        "character": "merged_characters",
        "scene": "scenes",
        "prop": "props",
    }[kind]
    prepared = PreparedAssetGeneration(
        actor=TrustedActor("wire-fixture-actor"),
        kind=kind,
        action=action,
        model=StoryboardServerModel(
            id="model-fixture", name="Fixture model", provider="local", api_key="synthetic-only-key"
        ),
        system_prompt="",
        user_prompt="",
        raw_content="合成分镜文字",
        enable_thinking=False,
        candidates=expected_complete.get(candidate_key) if action == "judge" else None,
        storyboard_texts=["她走进房间"],
        frame_numbers=[2],
        skip_merge=True,
    )
    runtime = FixtureRuntime(runtime_text)

    events = _events(runtime, prepared)

    assert events[0]["type"] == "start"
    assert events[-1] == {"type": "done"}
    complete = next(event for event in events if event["type"] == "complete")
    assert complete == expected_complete
    content = next(event for event in events if event["type"] == "content")
    assert content["full_text"] == runtime_text
    assert runtime.calls[0].operation == f"{action}_{kind}"
    assert "synthetic-only-key" not in json.dumps(events, ensure_ascii=False)


def test_actual_judge_producer_emits_the_legacy_merged_event_field() -> None:
    kind = "prop"
    expected_complete = WIRE_FIXTURE["producer"]["judge_complete"][kind]
    candidate_key = "merged_props"
    complete_candidate_key = "props"
    prepared = PreparedAssetGeneration(
        actor=TrustedActor("wire-fixture-actor"),
        kind=kind,
        action="judge",
        model=StoryboardServerModel(
            id="model-fixture", name="Fixture model", provider="local", api_key="synthetic-only-key"
        ),
        system_prompt="",
        user_prompt="",
        raw_content="",
        enable_thinking=False,
        candidates=expected_complete[complete_candidate_key],
        storyboard_texts=["她拾起钥匙"],
        frame_numbers=[1],
        skip_merge=False,
    )
    runtime = FixtureRuntime(WIRE_FIXTURE["producer"]["runtime"]["judge"][kind])

    events = _events(runtime, prepared)

    merged = next(event for event in events if event["type"] == "merged")
    assert merged == {"type": "merged", candidate_key: expected_complete[complete_candidate_key]}
