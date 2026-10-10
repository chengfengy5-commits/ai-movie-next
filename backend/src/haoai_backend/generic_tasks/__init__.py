"""Public framework-independent contracts for generic task operations."""

from .domain import (
    CreditLedgerEntry,
    CreditWallet,
    GenericTaskDraft,
    GenericTaskRecord,
    TaskSubmissionRecord,
)
from .errors import GenericTaskError
from .ports import (
    GenericTaskRuntime,
    GenericTaskUnitOfWork,
    GenericTaskUnitOfWorkFactory,
    TaskClock,
    TaskIdFactory,
)

__all__ = [
    "CreditLedgerEntry",
    "CreditWallet",
    "GenericTaskDraft",
    "GenericTaskError",
    "GenericTaskRecord",
    "GenericTaskRuntime",
    "GenericTaskUnitOfWork",
    "GenericTaskUnitOfWorkFactory",
    "TaskClock",
    "TaskIdFactory",
    "TaskSubmissionRecord",
]
