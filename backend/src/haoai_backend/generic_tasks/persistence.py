"""SQLAlchemy adapters for generic task transactions and legacy ledger reads."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from .domain import (
    CreditLedgerEntry,
    CreditWallet,
    GenericTaskDraft,
    GenericTaskRecord,
    TaskSubmissionRecord,
)
from .ports import GenericTaskUnitOfWork, GenericTaskUnitOfWorkFactory
from .tables import AITask, BillingUnit, CreditLog, TaskSubmission, UserCredit

SessionFactory = Callable[[], Session]


def _task_record(task: AITask) -> GenericTaskRecord:
    return GenericTaskRecord(
        id=task.id,
        user_id=task.user_id,
        type=task.type,
        status=task.status,
        credit_cost=task.credit_cost,
        message_id=task.message_id,
        request_data=task.request_data,
        result=task.result,
        progress=task.progress,
        progress_message=task.progress_message,
        external_task_id=task.external_task_id,
        external_provider=task.external_provider,
        model_name=task.model_name,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


class SqlAlchemyGenericTaskUnitOfWork(GenericTaskUnitOfWork):
    """Wrap one supplied autoflush-disabled Session without creating an engine."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._tasks: dict[str, AITask] = {}
        self._wallets_by_user: dict[str, UserCredit] = {}

    def stage_processing_task(self, task: GenericTaskDraft) -> None:
        entity = AITask(
            id=task.id,
            user_id=task.user_id,
            type=task.type,
            message_id=task.message_id,
            status=task.status,
            credit_cost=task.credit_cost,
            model_name=task.model_name,
            request_data=task.request_data,
            created_at=task.created_at,
            updated_at=task.updated_at,
        )
        self._tasks[task.id] = entity
        self._session.add(entity)

    def load_owned_task_for_update(
        self, actor_id: str, task_id: str
    ) -> GenericTaskRecord | None:
        entity = (
            self._session.query(AITask)
            .filter(AITask.id == task_id, AITask.user_id == actor_id)
            .with_for_update()
            .first()
        )
        if entity is None:
            return None
        self._tasks[task_id] = entity
        return _task_record(entity)

    def load_owned_task_first(
        self, actor_id: str, task_id: str
    ) -> GenericTaskRecord | None:
        entity = (
            self._session.query(AITask)
            .filter(AITask.id == task_id, AITask.user_id == actor_id)
            .first()
        )
        if entity is None:
            return None
        self._tasks[task_id] = entity
        return _task_record(entity)

    def has_billing_unit(self, task_id: str) -> bool:
        return (
            self._session.query(BillingUnit.id)
            .filter(BillingUnit.task_id == task_id)
            .first()
            is not None
        )

    def list_submissions_for_actor(
        self, actor_id: str
    ) -> Sequence[TaskSubmissionRecord]:
        rows = (
            self._session.query(
                TaskSubmission.id,
                TaskSubmission.user_id,
                TaskSubmission.task_ids,
            )
            .filter(TaskSubmission.user_id == actor_id)
            .all()
        )
        return [
            TaskSubmissionRecord(id=row.id, user_id=row.user_id, task_ids=row.task_ids)
            for row in rows
        ]

    def lock_wallet(self, user_id: str) -> CreditWallet | None:
        wallet = (
            self._session.query(UserCredit)
            .filter(UserCredit.user_id == user_id)
            .with_for_update()
            .first()
        )
        if wallet is None:
            return None
        self._wallets_by_user[user_id] = wallet
        return CreditWallet(id=wallet.id, user_id=wallet.user_id, credits=wallet.credits)

    def update_wallet_balance(
        self, wallet_id: str, credits: Any, updated_at: datetime
    ) -> None:
        wallet = next(
            (
                entity
                for entity in self._wallets_by_user.values()
                if entity.id == wallet_id
            ),
            None,
        )
        if wallet is None:
            raise LookupError(f"wallet {wallet_id!r} was not locked in this unit of work")
        wallet.credits = credits
        wallet.updated_at = updated_at

    def lock_matching_usage_debits(
        self, *, user_id: str, task_id: str, amount: Any
    ) -> Sequence[CreditLedgerEntry]:
        rows = (
            self._session.query(CreditLog)
            .filter(
                CreditLog.user_id == user_id,
                CreditLog.task_id == task_id,
                CreditLog.type == "usage",
                CreditLog.amount == amount,
            )
            .with_for_update()
            .all()
        )
        return [
            CreditLedgerEntry(
                id=row.id,
                user_id=row.user_id,
                task_id=row.task_id or "",
                type=row.type,
                amount=row.amount,
                business_key=row.business_key or "",
                related_debit_id=row.related_debit_id,
            )
            for row in rows
        ]

    def has_refund_for_debit(self, debit_id: str) -> bool:
        return (
            self._session.query(CreditLog.id)
            .filter(
                CreditLog.related_debit_id == debit_id,
                CreditLog.type == "refund",
            )
            .with_for_update()
            .first()
            is not None
        )

    def stage_credit_ledger_entry(self, entry: CreditLedgerEntry) -> None:
        task = self._tasks.get(entry.task_id)
        wallet = self._wallets_by_user.get(entry.user_id)
        if task is None:
            raise LookupError(f"task {entry.task_id!r} is not retained in this unit of work")
        if wallet is None:
            raise LookupError(f"wallet for {entry.user_id!r} was not locked in this unit of work")

        if entry.type == "usage":
            description = f"创建任务 [{task.type}] 消耗 {task.credit_cost} 积分"
        elif entry.type == "refund":
            description = f"任务 [{task.type}] 失败退回 {task.credit_cost} 积分"
        else:
            raise ValueError(f"unsupported generic task ledger type: {entry.type!r}")

        self._session.add(
            CreditLog(
                id=entry.id,
                user_id=entry.user_id,
                task_id=entry.task_id,
                business_key=entry.business_key,
                amount=entry.amount,
                balance_after=wallet.credits,
                type=entry.type,
                description=description,
                related_debit_id=entry.related_debit_id,
            )
        )

    def stage_task_status(
        self,
        task_id: str,
        *,
        status: str,
        result: Any | None,
        updated_at: datetime,
    ) -> None:
        task = self._require_task(task_id)
        task.status = status
        if result is not None:
            task.result = result
        task.updated_at = updated_at

    def stage_task_progress(
        self,
        task_id: str,
        *,
        progress: int,
        progress_message: str | None,
        updated_at: datetime,
    ) -> None:
        task = self._require_task(task_id)
        task.progress = progress
        if progress_message is not None:
            task.progress_message = progress_message
        task.updated_at = updated_at

    def read_task_after_commit(self, task_id: str) -> GenericTaskRecord:
        task = self._require_task(task_id)
        self._session.expire(task)
        refreshed = self._session.get(AITask, task_id)
        if refreshed is None:
            raise LookupError(f"committed task {task_id!r} could not be read back")
        self._tasks[task_id] = refreshed
        return _task_record(refreshed)

    def refresh_created_task(self, task_id: str) -> GenericTaskRecord:
        task = self._require_task(task_id)
        self._session.refresh(task)
        return _task_record(task)

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()

    def close(self) -> None:
        self._session.close()

    def _require_task(self, task_id: str) -> AITask:
        task = self._tasks.get(task_id)
        if task is None:
            raise LookupError(f"task {task_id!r} is not retained in this unit of work")
        return task


def create_generic_task_uow_factory(
    session_factory: SessionFactory | None,
) -> GenericTaskUnitOfWorkFactory | None:
    """Bind generic task operations to the application's supplied Session factory."""

    if session_factory is None:
        return None

    def create_unit_of_work() -> GenericTaskUnitOfWork:
        return SqlAlchemyGenericTaskUnitOfWork(session_factory())

    return create_unit_of_work
