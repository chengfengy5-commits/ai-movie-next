"""Framework-free contracts for observing existing AI task state."""

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
)
from .receipts import build_submission_receipt, build_task_receipt

__all__ = [
    "BillingUnitRecord",
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
