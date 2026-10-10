"""Framework-free persistence and application ports for generic task operations."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, TypeAlias

from haoai_backend.provider_status import ProviderStatusPort

from .domain import (
    CreditLedgerEntry,
    CreditWallet,
    GenericTaskDraft,
    GenericTaskRecord,
    TaskSubmissionRecord,
)

TaskClock: TypeAlias = Callable[[], datetime]
TaskIdFactory: TypeAlias = Callable[[], str]


@dataclass(frozen=True, slots=True)
class GenericTaskRuntime:
    """Explicit use-case dependencies; the provider port may be unconfigured."""

    clock: TaskClock
    id_factory: TaskIdFactory
    provider_status: ProviderStatusPort | None = None


class GenericTaskUnitOfWork(Protocol):
    """One supplied ``autoflush=False`` ORM Session with explicit transactions."""

    def stage_processing_task(self, task: GenericTaskDraft) -> None:
        """Add the mapped task without flushing or issuing an early INSERT."""
        ...

    def load_owned_task_for_update(
        self, actor_id: str, task_id: str
    ) -> GenericTaskRecord | None: ...

    def load_owned_task_first(
        self, actor_id: str, task_id: str
    ) -> GenericTaskRecord | None: ...

    def has_billing_unit(self, task_id: str) -> bool: ...

    def list_submissions_for_actor(
        self, actor_id: str
    ) -> Sequence[TaskSubmissionRecord]:
        """Return raw task_ids values so the caller can require a Python list."""
        ...

    def lock_wallet(self, user_id: str) -> CreditWallet | None: ...

    def update_wallet_balance(
        self, wallet_id: str, credits: Any, updated_at: datetime
    ) -> None: ...

    def lock_matching_usage_debits(
        self, *, user_id: str, task_id: str, amount: Any
    ) -> Sequence[CreditLedgerEntry]: ...

    def has_refund_for_debit(self, debit_id: str) -> bool: ...

    def stage_credit_ledger_entry(self, entry: CreditLedgerEntry) -> None: ...

    def stage_task_status(
        self,
        task_id: str,
        *,
        status: str,
        result: Any | None,
        updated_at: datetime,
    ) -> None:
        """A None result preserves the existing value; empty strings clear it."""
        ...

    def stage_task_progress(
        self,
        task_id: str,
        *,
        progress: int,
        progress_message: str | None,
        updated_at: datetime,
    ) -> None: ...

    def read_task_after_commit(self, task_id: str) -> GenericTaskRecord:
        """Read the retained ORM entity by primary key after commit.

        Use the same UoW without rechecking ownership; propagate readback errors.
        """
        ...

    def refresh_created_task(self, task_id: str) -> GenericTaskRecord:
        """Refresh the staged ORM entity by primary key after its commit."""
        ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


GenericTaskUnitOfWorkFactory: TypeAlias = Callable[[], GenericTaskUnitOfWork]
