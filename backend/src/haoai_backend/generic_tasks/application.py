"""Use cases for the four legacy generic-task routes."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

from haoai_backend.provider_status import ProviderStatusPort
from haoai_backend.shared.identity import TrustedActor

from .domain import CreditLedgerEntry, GenericTaskDraft, GenericTaskRecord
from .errors import GenericTaskError
from .guard import CONTROLLED_TASK_TYPES, reject_controlled_task_update
from .ports import GenericTaskRuntime, GenericTaskUnitOfWork, GenericTaskUnitOfWorkFactory


def _runtime(runtime: GenericTaskRuntime | None) -> GenericTaskRuntime:
    if runtime is not None:
        return runtime
    return GenericTaskRuntime(
        clock=datetime.utcnow,
        id_factory=lambda: str(uuid.uuid4()),
        provider_status=None,
    )


def _open_uow(
    factory: GenericTaskUnitOfWorkFactory | None,
) -> GenericTaskUnitOfWork:
    if factory is None:
        raise GenericTaskError(503, "通用任务服务暂不可用")
    return factory()


def _rollback_once(unit_of_work: GenericTaskUnitOfWork, state: dict[str, bool]) -> None:
    if not state["rollback_attempted"]:
        state["rollback_attempted"] = True
        unit_of_work.rollback()


def create_task(
    factory: GenericTaskUnitOfWorkFactory | None,
    actor: TrustedActor,
    task_type: str,
    credit_cost: int = 0,
    request_data: str | None = None,
    runtime: GenericTaskRuntime | None = None,
) -> dict[str, Any]:
    """Create a generic task and atomically debit its positive charge."""

    configured = _runtime(runtime)
    unit_of_work = _open_uow(factory)
    state = {"commit_attempted": False, "rollback_attempted": False}
    try:
        if task_type in CONTROLLED_TASK_TYPES:
            raise GenericTaskError(422, "该任务类型由受控准入接口创建")

        model_name = None
        if request_data:
            try:
                parsed = json.loads(request_data)
                model_name = parsed.get("model") or parsed.get("model_name") or parsed.get("modelId")
            except (json.JSONDecodeError, TypeError):
                pass

        now = configured.clock()
        task = GenericTaskDraft(
            id=configured.id_factory(),
            user_id=actor.user_id,
            type=task_type,
            status="processing",
            credit_cost=credit_cost,
            message_id="",
            request_data=request_data or "{}",
            model_name=model_name,
            created_at=now,
            updated_at=now,
        )
        unit_of_work.stage_processing_task(task)

        if credit_cost > 0:
            wallet = unit_of_work.lock_wallet(actor.user_id)
            if wallet is None or wallet.credits < credit_cost:
                _rollback_once(unit_of_work, state)
                available = wallet.credits if wallet is not None else 0
                raise GenericTaskError(
                    402,
                    f"积分不足，需要 {credit_cost} 积分，当前剩余 {available} 积分",
                )

            remaining = wallet.credits - credit_cost
            unit_of_work.update_wallet_balance(wallet.id, remaining, now)
            unit_of_work.stage_credit_ledger_entry(
                CreditLedgerEntry(
                    id=configured.id_factory(),
                    user_id=actor.user_id,
                    task_id=task.id,
                    type="usage",
                    amount=-credit_cost,
                    business_key=f"generic-task-charge:{task.id}",
                )
            )

        state["commit_attempted"] = True
        unit_of_work.commit()
        created = unit_of_work.refresh_created_task(task.id)
        return {"id": created.id, "status": created.status, "credit_cost": created.credit_cost}
    except Exception:
        if not state["commit_attempted"]:
            _rollback_once(unit_of_work, state)
        raise
    finally:
        unit_of_work.close()


def _owned_task_for_update(
    unit_of_work: GenericTaskUnitOfWork,
    actor: TrustedActor,
    task_id: str,
    *,
    progress: bool,
) -> GenericTaskRecord:
    if progress:
        task = unit_of_work.load_owned_task_first(actor.user_id, task_id)
    else:
        task = unit_of_work.load_owned_task_for_update(actor.user_id, task_id)
    if task is None:
        raise GenericTaskError(404, "任务不存在")
    reject_controlled_task_update(unit_of_work, task, actor.user_id)
    return task


def update_task(
    factory: GenericTaskUnitOfWorkFactory | None,
    actor: TrustedActor,
    task_id: str,
    status: str,
    result: str | None = None,
    runtime: GenericTaskRuntime | None = None,
) -> dict[str, Any]:
    """Update one owned generic task and refund only its proven debit."""

    configured = _runtime(runtime)
    unit_of_work = _open_uow(factory)
    state = {"commit_attempted": False, "rollback_attempted": False}
    try:
        task = _owned_task_for_update(unit_of_work, actor, task_id, progress=False)
        now = configured.clock()
        unit_of_work.stage_task_status(
            task.id,
            status=status,
            result=result,
            updated_at=now,
        )

        if status == "failed" and task.credit_cost and task.credit_cost > 0:
            wallet = unit_of_work.lock_wallet(actor.user_id)
            if wallet is not None:
                debits = unit_of_work.lock_matching_usage_debits(
                    user_id=actor.user_id,
                    task_id=task.id,
                    amount=-task.credit_cost,
                )
                if len(debits) == 1:
                    debit = debits[0]
                    if not unit_of_work.has_refund_for_debit(debit.id):
                        unit_of_work.update_wallet_balance(
                            wallet.id,
                            wallet.credits + task.credit_cost,
                            now,
                        )
                        unit_of_work.stage_credit_ledger_entry(
                            CreditLedgerEntry(
                                id=configured.id_factory(),
                                user_id=actor.user_id,
                                task_id=task.id,
                                type="refund",
                                amount=task.credit_cost,
                                business_key=f"generic-task-refund:{debit.id}",
                                related_debit_id=debit.id,
                            )
                        )

        state["commit_attempted"] = True
        unit_of_work.commit()
        updated = unit_of_work.read_task_after_commit(task.id)
        return {"id": updated.id, "status": updated.status}
    except Exception:
        if not state["commit_attempted"]:
            _rollback_once(unit_of_work, state)
        raise
    finally:
        unit_of_work.close()


def update_task_progress(
    factory: GenericTaskUnitOfWorkFactory | None,
    actor: TrustedActor,
    task_id: str,
    progress: int,
    progress_message: str | None = None,
    runtime: GenericTaskRuntime | None = None,
) -> dict[str, Any]:
    """Clamp and update progress after owned-task and controlled-task checks."""

    configured = _runtime(runtime)
    unit_of_work = _open_uow(factory)
    state = {"commit_attempted": False, "rollback_attempted": False}
    try:
        task = _owned_task_for_update(unit_of_work, actor, task_id, progress=True)
        unit_of_work.stage_task_progress(
            task.id,
            progress=max(0, min(100, progress)),
            progress_message=progress_message,
            updated_at=configured.clock(),
        )
        state["commit_attempted"] = True
        unit_of_work.commit()
        updated = unit_of_work.read_task_after_commit(task.id)
        return {
            "id": updated.id,
            "progress": updated.progress,
            "progress_message": updated.progress_message,
        }
    except Exception:
        if not state["commit_attempted"]:
            _rollback_once(unit_of_work, state)
        raise
    finally:
        unit_of_work.close()


async def get_external_task_status(
    factory: GenericTaskUnitOfWorkFactory | None,
    actor: TrustedActor,
    task_id: str,
    runtime: GenericTaskRuntime | None = None,
) -> dict[str, Any]:
    """Poll one provider once without mutating task state or dispatching work."""

    configured = _runtime(runtime)
    unit_of_work = _open_uow(factory)
    try:
        task = unit_of_work.load_owned_task_first(actor.user_id, task_id)
        if task is None:
            raise GenericTaskError(404, "任务不存在")
        if not task.external_task_id or not task.external_provider:
            return {"status": "unknown", "detail": "该任务没有关联的外部任务"}

        provider_status: ProviderStatusPort | None = configured.provider_status
        if provider_status is None:
            raise GenericTaskError(503, "外部状态查询服务暂不可用")

        prepared = provider_status.prepare(task.external_provider)
        if prepared is None:
            return {"status": "unknown", "detail": "无法为该任务创建查询器"}

        try:
            status, data = await prepared.poll_once(task.external_task_id)
            if status == "completed":
                return {"status": "completed", "result": prepared.extract_result(data)}
            if status == "failed":
                error_message = (
                    data.get("error", {}).get("message", "")
                    or data.get("fail_reason", "未知错误")
                )
                return {"status": "failed", "error": error_message}
            return {"status": "processing"}
        except Exception as exc:
            return {
                "status": "unknown",
                "detail": f"查询外部任务状态异常: {str(exc)}",
            }
    finally:
        unit_of_work.close()
