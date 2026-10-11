from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from haoai_backend.generation_runtime.registry import (
    HAOAI_ATTEMPT_GROUP,
    HAOAI_AUTH12_POLICY,
    HAOAI_POLL_POLICY,
    IN_FLIGHT_STATES,
    LOCAL_CONTINUATION_POLICY,
    OPERATION_POLICIES,
    SOURCE_WORKER_TYPES,
    STREAM_FEE_POLICY,
    operation_policy,
)


def test_operation_registry_has_the_six_frozen_operation_mappings() -> None:
    assert set(OPERATION_POLICIES) == {
        "image.single",
        "video.single",
        "image.batch",
        "frame.review",
        "frame.optimize",
        "text.stream",
    }
    assert OPERATION_POLICIES["image.single"].graph_version == "image-single-v1"
    assert OPERATION_POLICIES["video.single"].graph_version == "video-single-v1"
    assert OPERATION_POLICIES["image.batch"].graph_version == "image-batch-v1"
    assert OPERATION_POLICIES["frame.review"].graph_version == "frame-review-v1"
    assert OPERATION_POLICIES["frame.optimize"].graph_version == "frame-optimize-v1"


def test_graphless_stream_and_old_graphs_keep_their_eligibility_boundary() -> None:
    stream = operation_policy("text.stream")
    assert stream is not None
    assert stream.task_type is None
    assert stream.graph_version is None
    assert stream.execution_mode == "web_stream"
    assert stream.stream_fee_policy == STREAM_FEE_POLICY

    assert OPERATION_POLICIES["image.single"].cancellation_policy_version == "legacy_signal_only"
    assert OPERATION_POLICIES["video.single"].cancellation_policy_version == "legacy_signal_only"
    assert OPERATION_POLICIES["frame.review"].local_continuation_policy == LOCAL_CONTINUATION_POLICY
    assert (
        OPERATION_POLICIES["frame.optimize"].local_continuation_policy
        == LOCAL_CONTINUATION_POLICY
    )


def test_worker_types_and_haoai_attempt_group_keep_fixed_budgets() -> None:
    assert SOURCE_WORKER_TYPES == (
        "batch-optimize",
        "batch-image",
        "image-single",
        "video-single",
        "ai-review",
    )
    assert IN_FLIGHT_STATES == (
        "queued",
        "processing",
        "cancelling",
        "reconciling",
    )
    assert HAOAI_ATTEMPT_GROUP.registry_version == "video-haoai-attempt-group-v1"
    assert HAOAI_ATTEMPT_GROUP.attempt_step_ids == (
        "generate.attempt0",
        "generate.attempt1",
        "generate.attempt2",
    )
    assert HAOAI_ATTEMPT_GROUP.policy_version == HAOAI_POLL_POLICY
    assert HAOAI_ATTEMPT_GROUP.stop_policy_version == HAOAI_AUTH12_POLICY
    assert HAOAI_ATTEMPT_GROUP.max_retries == 2
    assert HAOAI_ATTEMPT_GROUP.max_polls == 720
    assert HAOAI_ATTEMPT_GROUP.poll_interval_seconds == 5
    assert HAOAI_ATTEMPT_GROUP.retry_delay_seconds == 3
    assert HAOAI_ATTEMPT_GROUP.original_deadline_seconds == 7200


def test_static_registry_is_immutable_and_never_dispatches_unknown_names() -> None:
    with pytest.raises(TypeError):
        OPERATION_POLICIES["unexpected"] = OPERATION_POLICIES["image.single"]  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        HAOAI_ATTEMPT_GROUP.max_retries = 99  # type: ignore[misc]
    assert operation_policy("future.operation") is None
