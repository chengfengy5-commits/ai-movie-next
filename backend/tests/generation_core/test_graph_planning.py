from __future__ import annotations

import hashlib
import importlib
from dataclasses import replace

import pytest

from haoai_backend.generation_contracts.errors import GenerationError
from haoai_backend.generation_contracts.types import (
    EndpointPolicy,
    FrozenExecutionSnapshot,
    FrozenGrantSnapshot,
    ModelReference,
    TargetSnapshot,
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _snapshot(
    *,
    model_id: str = "model-1",
    category: str = "image",
    quote: int = 3,
    target_kind: str = "none",
    asset_type: str | None = None,
    asset_id: str | None = None,
    prompt: str = "frozen prompt",
    params: dict[str, object] | None = None,
) -> FrozenExecutionSnapshot:
    return FrozenExecutionSnapshot(
        model_ref=ModelReference(owner_id="owner-1", provider="provider-x", model_id=model_id),
        model_category=category,
        model_name=f"Display {model_id}",
        model_params=params or {"quality": "high", "nested": {"steps": [1, 2]}},
        prompt_ref="prompt-ref-1",
        effective_prompt=prompt,
        effective_context="frozen context",
        server_quote=quote,
        reference_snapshot_ref="reference-snapshot-1",
        target_snapshot=TargetSnapshot(
            kind=target_kind,
            chapter_id="chapter-1",
            series_id="series-1",
            asset_type=asset_type,
            asset_id=asset_id,
            compat_frame_index=2 if target_kind == "frame_text" else None,
        ),
        credential_binding_ref="credential-binding-ref-1",
        grant_snapshot=FrozenGrantSnapshot(
            actor_id="actor-1",
            series_id="series-1",
            access_provenance=("trusted-fixture",),
        ),
        adapter_config_versions=(("provider-x", "adapter-v1"),),
        endpoint_policy=EndpointPolicy(provider_origin="https://provider.invalid"),
    )


def _item(
    item_id: str,
    *,
    target_id: str | None = None,
    task_id: str | None = None,
    unit_id: str | None = None,
    step_ids: tuple[str, ...] | None = None,
    message_id: str | None = None,
    snapshot: FrozenExecutionSnapshot | None = None,
) -> object:
    api = importlib.import_module("haoai_backend.generation_core.graph_planning")
    return api.GraphItemInput(
        item_id=item_id,
        target_id=target_id or f"target-{item_id}",
        task_id=task_id or f"task-{item_id}",
        unit_id=unit_id or f"unit-{item_id}",
        step_ids=step_ids or (f"step-{item_id}",),
        message_id=message_id,
        request_digest=_digest(f"request:{item_id}"),
        rule_digest=_digest(f"rule:{item_id}"),
        snapshot=snapshot or _snapshot(),
    )


def _build(operation: str, items: list[object], *, version: str) -> object:
    api = importlib.import_module("haoai_backend.generation_core.graph_planning")
    return api.build_operation_graph_plan(
        operation,
        graph_version=version,
        items=items,
    )


def test_batch_keeps_order_and_each_complete_frozen_snapshot() -> None:
    params_a: dict[str, object] = {"quality": "standard", "nested": {"steps": [1, 2]}}
    params_b: dict[str, object] = {"quality": "ultra", "nested": {"steps": [7]}}
    first = _item(
        "item-a",
        message_id="message-a",
        snapshot=_snapshot(
            model_id="owned-model-a",
            quote=3,
            asset_type="character",
            asset_id="asset-a",
            prompt="prompt A",
            params=params_a,
        ),
    )
    second = _item(
        "item-b",
        message_id="message-b",
        snapshot=_snapshot(
            model_id="owned-model-b",
            quote=11,
            asset_type="scene",
            asset_id="asset-b",
            prompt="prompt B",
            params=params_b,
        ),
    )

    plan = _build("image.batch", [first, second], version="image-batch-v1")
    params_a["nested"] = {"steps": [999]}
    reversed_plan = _build("image.batch", [second, first], version="image-batch-v1")

    assert [item.item_id for item in plan.items] == ["item-a", "item-b"]
    assert [item.snapshot.server_quote for item in plan.items] == [3, 11]
    assert [item.snapshot.model_ref.model_id for item in plan.items] == [
        "owned-model-a",
        "owned-model-b",
    ]
    assert [item.snapshot.effective_prompt for item in plan.items] == ["prompt A", "prompt B"]
    assert plan.items[0].snapshot.model_params["nested"]["steps"] == (1, 2)
    assert plan.plan_digest != reversed_plan.plan_digest
    assert [unit.quoted_amount for unit in plan.units] == [3, 11]
    assert [task.task_id for task in plan.tasks] == ["task-item-a", "task-item-b"]
    assert [step.item_id for step in plan.steps] == ["item-a", "item-b"]
    assert [message.message_id for message in plan.messages] == ["message-a", "message-b"]


def test_batch_bounds_and_duplicate_stable_asset_identity_are_rejected() -> None:
    with pytest.raises(GenerationError):
        _build("image.batch", [], version="image-batch-v1")

    max_batch = [
        _item(
            f"max-item-{index}",
            message_id=f"max-message-{index}",
            snapshot=_snapshot(
                asset_type="character",
                asset_id=f"max-asset-{index}",
            ),
        )
        for index in range(64)
    ]
    assert len(_build("image.batch", max_batch, version="image-batch-v1").items) == 64

    too_many = [
        _item(
            f"item-{index}",
            message_id=f"message-{index}",
            snapshot=_snapshot(
                asset_type="character",
                asset_id=f"asset-{index}",
            ),
        )
        for index in range(65)
    ]
    with pytest.raises(GenerationError):
        _build("image.batch", too_many, version="image-batch-v1")

    first = _item(
        "one",
        message_id="message-one",
        snapshot=_snapshot(asset_type="character", asset_id="same-asset"),
    )
    second = _item(
        "two",
        message_id="message-two",
        snapshot=_snapshot(asset_type="character", asset_id="same-asset"),
    )
    with pytest.raises(GenerationError):
        _build("image.batch", [first, second], version="image-batch-v1")

    missing_message = _item(
        "missing-message",
        snapshot=_snapshot(asset_type="character", asset_id="asset-message"),
    )
    with pytest.raises(GenerationError):
        _build("image.batch", [missing_message], version="image-batch-v1")


def test_policy_version_category_and_video_canvas_are_not_guessed() -> None:
    with pytest.raises(GenerationError):
        _build("image.batch", [_item("legacy")], version="batch-image-v1")
    with pytest.raises(GenerationError):
        _build("image.batch", [_item("unknown")], version="future-image-v99")
    with pytest.raises(GenerationError):
        _build("not-a-registered-operation", [_item("unknown")], version="unknown-v1")
    with pytest.raises(GenerationError):
        _build(
            "image.single",
            [_item("one"), _item("two")],
            version="image-single-v1",
        )
    wrong_image_model = _item(
        "wrong-image-category",
        snapshot=_snapshot(category="video"),
    )
    with pytest.raises(GenerationError):
        _build("image.single", [wrong_image_model], version="image-single-v1")

    wrong_video_model = _item(
        "wrong-category",
        snapshot=_snapshot(category="image"),
    )
    with pytest.raises(GenerationError):
        _build("video.single", [wrong_video_model], version="video-single-v1")

    canvas_video = _item(
        "canvas-video",
        snapshot=_snapshot(category="video", target_kind="canvas_frame_preview"),
    )
    with pytest.raises(GenerationError):
        _build("video.single", [canvas_video], version="video-single-v1")

    with pytest.raises(GenerationError):
        _build("text.stream", [_item("stream")], version="stream-v1")


def test_haoai_attempt_group_is_required_with_three_optional_independent_steps() -> None:
    item = _item(
        "haoai",
        step_ids=("stable-step-0", "stable-step-1", "stable-step-2"),
        snapshot=_snapshot(category="video", quote=19),
    )
    api = importlib.import_module("haoai_backend.generation_core.graph_planning")
    plan = api.build_operation_graph_plan(
        "video.single",
        graph_version="video-haoai-attempt-group-v1",
        items=[item],
        attempt_group_id="attempt-group-1",
    )

    assert plan.graph_version == "video-haoai-attempt-group-v1"
    assert len(plan.tasks) == len(plan.units) == 1
    assert plan.attempt_group.required is True
    assert plan.attempt_group.max_retries == 2
    assert plan.attempt_group.step_ids == (
        "stable-step-0",
        "stable-step-1",
        "stable-step-2",
    )
    assert [step.step_key for step in plan.steps] == [
        "generate.attempt0",
        "generate.attempt1",
        "generate.attempt2",
    ]
    assert [step.required for step in plan.steps] == [False, False, False]
    assert [step.order for step in plan.steps] == [0, 1, 2]
    assert all(step.dependencies == () for step in plan.steps)

    wrong_members = _item("bad-group", step_ids=("only-one",), snapshot=_snapshot(category="video"))
    with pytest.raises(GenerationError):
        api.build_operation_graph_plan(
            "video.single",
            graph_version="video-haoai-attempt-group-v1",
            items=[wrong_members],
            attempt_group_id="attempt-group-bad",
        )
    with pytest.raises(GenerationError):
        api.build_operation_graph_plan(
            "video.single",
            graph_version="video-single-v1",
            items=[item],
            attempt_group_id="attempt-group-ordinary",
        )


def test_optimize_uses_one_parent_task_and_one_unit_per_frame_with_limit() -> None:
    frames = [
        _item(
            f"frame-{index}",
            task_id="optimize-task",
            target_id=f"frame-target-{index}",
            unit_id=f"frame-unit-{index}",
            step_ids=(f"frame-step-{index}",),
            snapshot=_snapshot(category="text", quote=index, target_kind="frame_text"),
        )
        for index in range(30)
    ]
    plan = _build("frame.optimize", frames, version="frame-optimize-v1")
    assert len(plan.tasks) == 1
    assert len(plan.units) == len(plan.steps) == 30
    assert [unit.item_id for unit in plan.units] == [f"frame-{i}" for i in range(30)]
    assert [unit.quoted_amount for unit in plan.units] == list(range(30))

    too_many = frames + [
        _item(
            "frame-30",
            task_id="optimize-task",
            target_id="frame-target-30",
            unit_id="frame-unit-30",
            snapshot=_snapshot(category="text", target_kind="frame_text"),
        )
    ]
    with pytest.raises(GenerationError):
        _build("frame.optimize", too_many, version="frame-optimize-v1")


def test_quotes_reject_bool_and_float_before_graph_construction() -> None:
    for bad_quote in (True, 3.5):
        item = _item("bad-quote", snapshot=_snapshot(quote=bad_quote))
        with pytest.raises(GenerationError):
            _build("image.single", [item], version="image-single-v1")

    with pytest.raises(ValueError):
        _snapshot(quote=-1)


def test_plan_uses_only_frozen_snapshot_when_external_catalog_state_changes() -> None:
    class CountingCatalogResolver:
        def __init__(self) -> None:
            self.calls = 0
            self.current_price = 3

        def resolve(self) -> int:
            self.calls += 1
            return self.current_price

    catalog = CountingCatalogResolver()
    frozen_item = _item("frozen", snapshot=_snapshot(quote=3, prompt="original prompt"))
    initial = _build("image.single", [frozen_item], version="image-single-v1")
    catalog.current_price = 9000
    subsequent = _build("image.single", [frozen_item], version="image-single-v1")

    assert subsequent.plan_digest == initial.plan_digest
    assert subsequent.items[0].snapshot.server_quote == 3
    assert subsequent.items[0].snapshot.effective_prompt == "original prompt"
    assert catalog.calls == 0


def test_plan_copies_all_nested_snapshot_sequences_before_digesting() -> None:
    endpoint_paths = ["/trusted/path"]
    access_provenance = ["trusted-fixture"]
    adapter_versions = [["provider-x", "adapter-v1"]]
    original = _snapshot()
    snapshot = replace(
        original,
        endpoint_policy=EndpointPolicy(
            provider_origin="https://provider.invalid",
            allowed_content_paths=endpoint_paths,
        ),
        grant_snapshot=FrozenGrantSnapshot(
            actor_id="actor-1",
            series_id="series-1",
            access_provenance=access_provenance,
        ),
        adapter_config_versions=adapter_versions,
    )

    plan = _build("image.single", [_item("nested-snapshot", snapshot=snapshot)], version="image-single-v1")
    planned = plan.items[0].snapshot
    digest_before_mutation = plan.plan_digest
    item_digest_before_mutation = plan.items[0].snapshot_digest

    endpoint_paths.append("/late/path")
    access_provenance[0] = "changed-source"
    adapter_versions[0][1] = "changed-version"
    adapter_versions.append(["other-provider", "other-version"])

    assert planned.endpoint_policy.allowed_content_paths == ("/trusted/path",)
    assert planned.grant_snapshot.access_provenance == ("trusted-fixture",)
    assert planned.adapter_config_versions == (("provider-x", "adapter-v1"),)
    assert planned.model_params["nested"]["steps"] == (1, 2)
    assert plan.items[0].snapshot_digest == item_digest_before_mutation
    assert plan.plan_digest == digest_before_mutation
    assert plan.items[0].step_ids == ("step-nested-snapshot",)
