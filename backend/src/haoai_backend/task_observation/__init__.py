"""Framework-free contracts for observing existing AI task state."""

from importlib import import_module
from typing import Any

from .authentication import resolve_account_actor, resolve_active_actor
from .domain import BillingUnitRecord, TaskRecord, TaskSubmissionRecord, TeamMembershipRecord
from .errors import (
    TaskObservationBadRequest,
    TaskObservationConflict,
    TaskObservationForbidden,
    TaskObservationIntegrityError,
    TaskObservationNotFound,
    TaskObservationUnavailable,
)
from .payload import (
    build_ai_review_counts,
    build_ai_review_status,
    build_batch_optimize_status,
    build_running_batch_optimize,
    build_task_list_payload,
    build_task_request,
    decode_request_data,
    find_running_batch_optimize,
    fold_long_strings,
)
from .ports import (
    CancellationConnectionFactory,
    CancellationSignal,
    CancellationSignalRegistry,
    TaskCancellationWriter,
    TaskObservationReader,
    TaskObservationUnitOfWork,
    TaskReceiptReader,
    TaskRequestAccessReader,
    UnitOfWorkFactory,
    GenerationResultReader,
)
from .receipts import build_submission_receipt, build_task_receipt

__all__ = [
    "BillingUnitRecord",
    "GenerationResultApplicationFacade",
    "GenerationResultReader",
    "CancellationConnectionFactory",
    "CancellationSignal",
    "CancellationSignalRegistry",
    "TaskCancellationWriter",
    "TaskObservationBadRequest",
    "TaskObservationConflict",
    "TaskObservationForbidden",
    "TaskObservationIntegrityError",
    "TaskObservationNotFound",
    "TaskObservationReader",
    "TaskObservationUnitOfWork",
    "TaskObservationUnavailable",
    "TaskReceiptReader",
    "TaskRecord",
    "TaskRequestAccessReader",
    "TaskSubmissionRecord",
    "TeamMembershipRecord",
    "UnitOfWorkFactory",
    "application_key_for",
    "confirm_worker_fence_after_target_lock",
    "application_ref_for",
    "current_target_application",
    "expected_target_digest",
    "find_application_record",
    "load_owned_completed_result",
    "load_validated_worker_result",
    "stage_application_record",
    "target_matches_receipt",
    "target_scope_for_snapshot",
    "build_ai_review_counts",
    "build_ai_review_status",
    "build_batch_optimize_status",
    "build_running_batch_optimize",
    "build_submission_receipt",
    "build_task_list_payload",
    "build_task_receipt",
    "build_task_request",
    "decode_request_data",
    "find_running_batch_optimize",
    "fold_long_strings",
    "resolve_account_actor",
    "resolve_active_actor",
]

_GENERATION_EXPORTS = frozenset(
    {
        "GenerationResultApplicationFacade",
        "application_key_for",
        "application_ref_for",
        "current_target_application",
        "confirm_worker_fence_after_target_lock",
        "expected_target_digest",
        "find_application_record",
        "load_owned_completed_result",
        "load_validated_worker_result",
        "stage_application_record",
        "target_matches_receipt",
        "target_scope_for_snapshot",
    }
)


def __getattr__(name: str) -> Any:
    if name not in _GENERATION_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(".generation", __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
