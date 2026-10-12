from __future__ import annotations

import hashlib
import importlib

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
    category: str = "image",
    quote: int = 3,
    asset_type: str | None = None,
    asset_id: str | None = None,
) -> FrozenExecutionSnapshot:
    return FrozenExecutionSnapshot(
        model_ref=ModelReference(owner_id="owner-1", provider="provider-x", model_id="model-1"),
        model_category=category,
        model_name="Display model",
        model_params={"quality": "high"},
        prompt_ref=None,
        effective_prompt="frozen prompt",
        effective_context="frozen context",
        server_quote=quote,
        reference_snapshot_ref="reference-snapshot-1",
        target_snapshot=TargetSnapshot(
            kind="frame_text" if category == "text" else "none",
            asset_type=asset_type,
            asset_id=asset_id,
        ),
        credential_binding_ref="credential-binding-ref-1",
        grant_snapshot=FrozenGrantSnapshot(actor_id="actor-1"),
        adapter_config_versions=(("provider-x", "adapter-v1"),),
        endpoint_policy=EndpointPolicy(provider_origin="https://provider.invalid"),
    )


def _graph(
    operation: str,
    count: int = 1,
    *,
    category: str = "image",
    quote: int | None = None,
) -> object:
    api = importlib.import_module("haoai_backend.generation_core.graph_planning")
    graph_type = api.GraphItemInput
    items = []
    for index in range(count):
        item_id = f"item-{index}"
        items.append(
            graph_type(
                item_id=item_id,
                target_id=f"target-{index}",
                task_id="optimize-task" if operation == "frame.optimize" else f"task-{index}",
                unit_id=f"unit-{index}",
                step_ids=(f"step-{index}",),
                request_digest=_digest(f"request:{index}"),
                rule_digest=_digest(f"rule:{index}"),
                snapshot=_snapshot(
                    category=category,
                    quote=index + 1 if quote is None else quote,
                ),
            )
        )
    versions = {
        "image.single": "image-single-v1",
        "video.single": "video-single-v1",
        "image.batch": "image-batch-v1",
        "frame.review": "frame-review-v1",
        "frame.optimize": "frame-optimize-v1",
    }
    return api.build_operation_graph_plan(operation, graph_version=versions[operation], items=items)


def test_single_and_batch_quote_lines_preserve_server_quotes_and_order() -> None:
    fee_api = importlib.import_module("haoai_backend.generation_core.fee_phases")
    single = fee_api.build_fee_phase_plan("image.single", graph=_graph("image.single"))
    assert single.charge_phase == "single_admission"
    assert single.debit_timing == "before_task_commit"
    assert [line.quoted_amount for line in single.quote_lines] == [1]
    assert single.taskless is False

    graph_api = importlib.import_module("haoai_backend.generation_core.graph_planning")
    batch_items = [
        graph_api.GraphItemInput(
            item_id=f"batch-{index}",
            target_id=f"target-{index}",
            task_id=f"task-{index}",
            unit_id=f"unit-{index}",
            step_ids=(f"step-{index}",),
            request_digest=_digest(f"request:{index}"),
            rule_digest=_digest(f"rule:{index}"),
            snapshot=_snapshot(quote=4 + index, asset_type="character", asset_id=f"asset-{index}"),
            message_id=f"message-{index}",
        )
        for index in range(3)
    ]
    batch_graph = graph_api.build_operation_graph_plan(
        "image.batch", graph_version="image-batch-v1", items=batch_items
    )
    batch = fee_api.build_fee_phase_plan("image.batch", graph=batch_graph)
    assert batch.charge_phase == "batch_admission"
    assert batch.debit_timing == "after_all_items_validate_before_commit"
    assert [line.item_id for line in batch.quote_lines] == ["batch-0", "batch-1", "batch-2"]
    assert [line.quoted_amount for line in batch.quote_lines] == [4, 5, 6]
    assert batch.quoted_total == 15


def test_review_and_optimize_keep_their_distinct_continuation_orders() -> None:
    fee_api = importlib.import_module("haoai_backend.generation_core.fee_phases")
    review = fee_api.build_fee_phase_plan(
        "frame.review", graph=_graph("frame.review", category="text")
    )
    optimize = fee_api.build_fee_phase_plan(
        "frame.optimize", graph=_graph("frame.optimize", count=2, category="text")
    )
    assert review.charge_phase == "post_apply"
    assert review.debit_timing == "after_application_commit"
    assert review.local_continuation_policy == "local_continuation_v1"
    assert review.continuation_order == (
        "delivery_pending",
        "fee_pending",
        "finalize_pending",
        "done",
    )
    assert optimize.charge_phase == "pre_apply"
    assert optimize.debit_timing == "after_generation_before_apply"
    assert optimize.local_continuation_policy == "local_continuation_v1"
    assert optimize.continuation_order == (
        "fee_pending",
        "delivery_pending",
        "finalize_pending",
        "done",
    )
    assert [line.quoted_amount for line in optimize.quote_lines] == [1, 2]
    assert not hasattr(review, "billing_outcome")
    assert not hasattr(optimize, "committed_receipt")


def test_stream_fee_plan_stays_taskless_for_debit_skip_byok_and_no_message() -> None:
    fee_api = importlib.import_module("haoai_backend.generation_core.fee_phases")
    stream_input = fee_api.StreamFeePlanInput

    debit = fee_api.build_fee_phase_plan(
        "text.stream",
        stream=stream_input(
            server_attempt_id="attempt-debit",
            server_quote=9,
            disposition="debit",
            message_id="message-1",
        ),
    )
    debit_without_message = fee_api.build_fee_phase_plan(
        "text.stream",
        stream=stream_input(
            server_attempt_id="attempt-debit-without-message",
            server_quote=9,
            disposition="debit",
            message_id=None,
        ),
    )
    skipped = fee_api.build_fee_phase_plan(
        "text.stream",
        stream=stream_input(
            server_attempt_id="attempt-skip",
            server_quote=9,
            disposition="skipped",
            message_id="message-skip",
        ),
    )
    zero = fee_api.build_fee_phase_plan(
        "text.stream",
        stream=stream_input(
            server_attempt_id="attempt-zero",
            server_quote=0,
            disposition="zero",
            message_id=None,
        ),
    )
    byok = fee_api.build_fee_phase_plan(
        "text.stream",
        stream=stream_input(
            server_attempt_id="attempt-byok",
            server_quote=0,
            disposition="byok",
            message_id=None,
        ),
    )
    plans = (debit, debit_without_message, skipped, byok, zero)
    assert all(plan.taskless is True for plan in plans)
    assert all(
        line.task_id is None and line.unit_id is None
        for plan in plans
        for line in plan.quote_lines
    )
    assert debit.debit_timing == "before_optional_task_binding"
    assert debit_without_message.optional_message_id is None
    assert skipped.debit_timing == "balance_check_only"
    assert skipped.quote_lines[0].quoted_amount == 9
    assert byok.debit_timing == "none"
    assert byok.quote_lines[0].quoted_amount == 0
    assert zero.debit_timing == "none"
    assert zero.quote_lines[0].quoted_amount == 0


def test_stream_phase_rejects_inconsistent_quote_and_graphs_reject_operation_mismatch() -> None:
    fee_api = importlib.import_module("haoai_backend.generation_core.fee_phases")
    with pytest.raises(GenerationError):
        fee_api.build_fee_phase_plan(
            "text.stream",
            stream=fee_api.StreamFeePlanInput(
                server_attempt_id="attempt-bad",
                server_quote=1,
                disposition="byok",
            ),
        )
    with pytest.raises(GenerationError):
        fee_api.build_fee_phase_plan(
            "text.stream",
            stream=fee_api.StreamFeePlanInput(
                server_attempt_id="attempt-bad",
                server_quote=True,
                disposition="debit",
            ),
        )
    with pytest.raises(GenerationError):
        fee_api.build_fee_phase_plan("video.single", graph=_graph("image.single"))
    with pytest.raises(GenerationError):
        fee_api.build_fee_phase_plan("image.single", graph=None)


def test_zero_quotes_keep_review_and_optimize_timing_without_billing_proof() -> None:
    fee_api = importlib.import_module("haoai_backend.generation_core.fee_phases")
    review = fee_api.build_fee_phase_plan(
        "frame.review",
        graph=_graph("frame.review", category="text", quote=0),
    )
    optimize = fee_api.build_fee_phase_plan(
        "frame.optimize",
        graph=_graph("frame.optimize", count=2, category="text", quote=0),
    )

    assert review.charge_phase == "post_apply"
    assert review.quote_lines[0].quoted_amount == 0
    assert review.continuation_order[-1] == "done"
    assert optimize.charge_phase == "pre_apply"
    assert [line.quoted_amount for line in optimize.quote_lines] == [0, 0]
    assert optimize.quoted_total == 0
    assert not hasattr(review, "billing_status")
    assert not hasattr(optimize, "unit_fee_decision")


def test_attempt_group_fee_plan_has_one_unit_quote_not_three_attempt_charges() -> None:
    graph_api = importlib.import_module("haoai_backend.generation_core.graph_planning")
    fee_api = importlib.import_module("haoai_backend.generation_core.fee_phases")
    item = graph_api.GraphItemInput(
        item_id="haoai-item",
        target_id="video-target",
        task_id="video-task",
        unit_id="video-unit",
        step_ids=("step-0", "step-1", "step-2"),
        request_digest=_digest("haoai-request"),
        rule_digest=_digest("haoai-rule"),
        snapshot=_snapshot(category="video", quote=21),
    )
    graph = graph_api.build_operation_graph_plan(
        "video.single",
        graph_version="video-haoai-attempt-group-v1",
        items=[item],
        attempt_group_id="group-1",
    )
    fee_plan = fee_api.build_fee_phase_plan("video.single", graph=graph)

    assert len(graph.units) == 1
    assert len(graph.steps) == 3
    assert len(fee_plan.quote_lines) == 1
    assert fee_plan.quoted_total == 21
