"""Narrow, framework-free read and cancellation ports for task observation."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from typing import Any, Protocol, TypeAlias

from .domain import BillingUnitRecord, TaskRecord, TaskSubmissionRecord, TeamMembershipRecord

TaskRow: TypeAlias = Mapping[str, Any]
MessageRows: TypeAlias = Mapping[str, TaskRow]
ChapterTitles: TypeAlias = Mapping[str, str]
AssetNames: TypeAlias = Mapping[str, Mapping[str, str | None]]


class TaskObservationReader(Protocol):
    """Read task and presentation context through one supplied request unit."""

    def load_owned_task(self, actor_id: str, task_id: str) -> TaskRecord | TaskRow | None: ...

    def load_task(self, task_id: str) -> TaskRecord | TaskRow | None: ...

    def find_owned_by_message(
        self, actor_id: str, message_id: str, limit: int = 2
    ) -> Sequence[TaskRecord | TaskRow]: ...

    def count_owned_tasks(self, actor_id: str) -> int: ...

    def list_owned_tasks(
        self, actor_id: str, offset: int, limit: int
    ) -> Sequence[TaskRecord | TaskRow]: ...

    def load_messages(self, message_ids: Sequence[str]) -> MessageRows: ...

    def load_chapter_titles(self, chapter_ids: set[str]) -> ChapterTitles: ...

    def load_asset_names(self, asset_ids_by_kind: Mapping[str, Sequence[str]]) -> AssetNames: ...

    def list_ai_review_count_candidates(
        self, actor_id: str, message_id: str
    ) -> Sequence[TaskRecord | TaskRow]: ...

    def list_running_batch_optimize_candidates(
        self, actor_id: str
    ) -> Sequence[TaskRecord | TaskRow]: ...


class TaskReceiptReader(Protocol):
    """Keep the first-unit and unique-unit receipt reads distinct."""

    def first_billing_unit(self, task_id: str) -> BillingUnitRecord | TaskRow | None: ...

    def find_submission(
        self, actor_id: str, operation: str, raw_key: str
    ) -> TaskSubmissionRecord | TaskRow | None: ...

    def unique_billing_unit(self, task_id: str) -> BillingUnitRecord | TaskRow | None: ...


class TaskRequestAccessReader(Protocol):
    """Read SQL privilege and membership after the task-first existence check."""

    def load_sql_superuser(self, actor_id: str) -> bool: ...

    def load_membership(
        self, team_id: str, user_id: str
    ) -> TeamMembershipRecord | TaskRow | None: ...


class TaskObservationUnitOfWork(
    TaskObservationReader,
    TaskReceiptReader,
    TaskRequestAccessReader,
    Protocol,
):
    """One request Session; callers may roll back and close but never commit."""

    def rollback(self) -> None: ...

    def close(self) -> None: ...


UnitOfWorkFactory: TypeAlias = Callable[[], TaskObservationUnitOfWork]


class CancellationSignal(Protocol):
    def set(self) -> None: ...


class CancellationSignalRegistry(Protocol):
    def get(self, task_id: str) -> CancellationSignal | None: ...


class TaskCancellationWriter(Protocol):
    """Write only the source-compatible status transition in an independent unit."""

    def request_cancel(self, task_id: str) -> bool: ...


CancellationConnectionFactory: TypeAlias = Callable[
    [], AbstractContextManager[object]
]
