"""Static server operation metadata for generation contracts.

The registry contains policy values only. It never imports or constructs an
adapter, provider, database session, HTTP client, or worker runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Mapping

from haoai_backend.generation_contracts.types import OperationPolicy
from haoai_backend.generation_contracts.task_policy import (
    IN_FLIGHT_STATES,
    SOURCE_WORKER_TYPES,
    task_type_for_operation,
)


@dataclass(frozen=True, slots=True)
class AttemptGroupPolicy:
    registry_version: str
    group_key: str
    policy_version: str
    stop_policy_version: str
    attempt_step_ids: tuple[str, str, str]
    max_retries: int
    max_polls: int
    poll_interval_seconds: int
    retry_delay_seconds: int
    original_deadline_seconds: int


LOCAL_CONTINUATION_POLICY: Final[str] = "local_continuation_v1"
STREAM_FEE_POLICY: Final[str] = "stream_debit_receipt_v1"
HAOAI_POLL_POLICY: Final[str] = "haoai_poll_reservation_v1"
HAOAI_AUTH12_POLICY: Final[str] = "haoai_auth12_v1"

_OPERATION_POLICIES: dict[str, OperationPolicy] = {
    "image.single": OperationPolicy(
        operation="image.single",
        task_type=task_type_for_operation("image.single"),
        graph_version="image-single-v1",
        execution_mode="claimed_worker",
        charge_phase="single_admission",
        cancellation_policy_version="legacy_signal_only",
    ),
    "video.single": OperationPolicy(
        operation="video.single",
        task_type=task_type_for_operation("video.single"),
        graph_version="video-single-v1",
        execution_mode="claimed_worker",
        charge_phase="single_admission",
        cancellation_policy_version="legacy_signal_only",
        query_budget_policy=HAOAI_POLL_POLICY,
    ),
    "image.batch": OperationPolicy(
        operation="image.batch",
        task_type=task_type_for_operation("image.batch"),
        graph_version="image-batch-v1",
        execution_mode="claimed_worker",
        charge_phase="batch_admission",
        cancellation_policy_version="lifecycle_v1_for_new_registered_graphs",
    ),
    "frame.review": OperationPolicy(
        operation="frame.review",
        task_type=task_type_for_operation("frame.review"),
        graph_version="frame-review-v1",
        execution_mode="claimed_worker",
        charge_phase="post_apply",
        cancellation_policy_version="lifecycle_v1_for_new_registered_graphs",
        local_continuation_policy=LOCAL_CONTINUATION_POLICY,
    ),
    "frame.optimize": OperationPolicy(
        operation="frame.optimize",
        task_type=task_type_for_operation("frame.optimize"),
        graph_version="frame-optimize-v1",
        execution_mode="claimed_worker",
        charge_phase="pre_apply",
        cancellation_policy_version="lifecycle_v1_for_new_registered_graphs",
        local_continuation_policy=LOCAL_CONTINUATION_POLICY,
    ),
    "text.stream": OperationPolicy(
        operation="text.stream",
        task_type=None,
        graph_version=None,
        execution_mode="web_stream",
        charge_phase="stream_pre_task",
        cancellation_policy_version="legacy_signal_only",
        stream_fee_policy=STREAM_FEE_POLICY,
    ),
}

OPERATION_POLICIES: Final[Mapping[str, OperationPolicy]] = MappingProxyType(
    _OPERATION_POLICIES
)

HAOAI_ATTEMPT_GROUP: Final[AttemptGroupPolicy] = AttemptGroupPolicy(
    registry_version="video-haoai-attempt-group-v1",
    group_key="video-haoai-attempts",
    policy_version=HAOAI_POLL_POLICY,
    stop_policy_version=HAOAI_AUTH12_POLICY,
    attempt_step_ids=(
        "generate.attempt0",
        "generate.attempt1",
        "generate.attempt2",
    ),
    max_retries=2,
    max_polls=720,
    poll_interval_seconds=5,
    retry_delay_seconds=3,
    original_deadline_seconds=7200,
)


def operation_policy(operation: str) -> OperationPolicy | None:
    """Return immutable server metadata without performing runtime dispatch."""

    return OPERATION_POLICIES.get(operation)


__all__ = (
    "AttemptGroupPolicy",
    "SOURCE_WORKER_TYPES",
    "IN_FLIGHT_STATES",
    "LOCAL_CONTINUATION_POLICY",
    "STREAM_FEE_POLICY",
    "HAOAI_POLL_POLICY",
    "HAOAI_AUTH12_POLICY",
    "OPERATION_POLICIES",
    "HAOAI_ATTEMPT_GROUP",
    "operation_policy",
)
