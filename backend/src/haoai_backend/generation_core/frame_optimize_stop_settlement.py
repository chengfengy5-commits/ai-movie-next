"""Persist the N2 stop decision for siblings that were never started."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.records import UnitFeeDecision
from haoai_backend.generation_core.base_schema import AITaskRow
from haoai_backend.generation_core.frame_optimize_execution import LockedFrameOptimizeBatch
from haoai_backend.generation_core.frame_optimize_progress import FrameOptimizeProgress
from haoai_backend.generation_core.persistence import GenerationRecordRepository
from haoai_backend.generation_core.schema import LocalContinuationWorkRow, UnitFeeDecisionRow


def stage_unstarted_insufficient_siblings(
    session: Session,
    graph: LockedFrameOptimizeBatch,
    progress: FrameOptimizeProgress,
    *,
    trigger_unit_id: str,
    resolved_at: datetime,
) -> tuple[str, ...]:
    """Add sibling stop decisions to the live insufficient-fee transaction."""

    if (
        not isinstance(session, Session)
        or not isinstance(graph, LockedFrameOptimizeBatch)
        or not isinstance(progress, FrameOptimizeProgress)
        or not isinstance(trigger_unit_id, str)
        or not isinstance(resolved_at, datetime)
        or resolved_at.tzinfo is None
        or resolved_at.utcoffset() is None
    ):
        raise ValueError("insufficient stop requires a locked optimize graph and DB time")

    owner_id = graph.task.get("user_id")
    task_id = graph.task.get("id")
    task = session.execute(
        select(AITaskRow.__table__)
        .where(
            AITaskRow.__table__.c.id == task_id,
            AITaskRow.__table__.c.user_id == owner_id,
            AITaskRow.__table__.c.type == "batch-optimize",
        )
        .with_for_update()
    ).mappings().one_or_none()
    trigger_unit = graph.units_by_id.get(trigger_unit_id)
    trigger_work = session.get(LocalContinuationWorkRow, progress.works_by_unit.get(trigger_unit_id, {}).get("work_id"))
    trigger_fee = session.get(UnitFeeDecisionRow, trigger_unit_id)
    trigger_receipt = progress.receipts_by_unit.get(trigger_unit_id)
    if (
        task is None
        or task.get("recovery_status") != "stopped_insufficient"
        or task.get("user_cancelled_at") is not None
        or trigger_unit is None
        or type(trigger_unit.get("quoted_amount")) is not int
        or trigger_unit.get("quoted_amount") <= 0
        or trigger_receipt is None
        or trigger_fee is None
        or trigger_fee.charge_phase != "pre_apply"
        or trigger_fee.resolution != "stopped_insufficient"
        or trigger_fee.ledger_ref is not None
        or trigger_fee.generation_result_ref != trigger_receipt.get("result_ref")
        or trigger_fee.application_ref is not None
        or trigger_work is None
        or trigger_work.phase != "stopped_insufficient"
        or trigger_work.phase_version != 1
        or trigger_work.fee_decision_ref != trigger_unit_id
        or trigger_work.terminal_reason != "insufficient_credits"
        or any(unit.get("user_cancelled_at") is not None for unit in graph.units)
    ):
        raise ValueError("the insufficient decision is not a live positive-price stop anchor")

    repository = GenerationRecordRepository(session)
    staged: list[str] = []
    normalized_at = resolved_at.astimezone(timezone.utc)
    for unit_id, unit in graph.units_by_id.items():
        if unit_id == trigger_unit_id:
            continue
        if (
            unit_id in progress.receipts_by_unit
            or unit_id in progress.works_by_unit
            or unit_id in progress.fees_by_unit
            or unit_id in progress.ledgers_by_unit
            or unit_id in progress.applications_by_unit
        ):
            continue
        step = graph.steps_by_unit.get(unit_id)
        if (
            step is None
            or unit.get("result_status") != "pending"
            or unit.get("billing_status") != "unbilled"
            or unit.get("debited_amount") != 0
            or unit.get("refunded_amount") != 0
            or unit.get("first_intent_at") is not None
            or unit.get("deadline_at") is not None
            or unit.get("success_confirmed_at") is not None
            or unit.get("user_cancelled_at") is not None
            or step.get("execution_status") != "prepared"
            or graph.external_by_step.get(str(step.get("id"))) is not None
        ):
            continue
        record = UnitFeeDecision(
            unit_id=unit_id,
            charge_phase="pre_apply",
            resolution="stopped_insufficient",
            ledger_ref=None,
            generation_result_ref=None,
            application_ref=None,
            resolved_at=normalized_at,
        )
        if repository.stage_record(record).status != "created":
            raise ValueError("unstarted sibling stop decision could not be staged atomically")
        staged.append(unit_id)
    return tuple(staged)


__all__ = ["stage_unstarted_insufficient_siblings"]
