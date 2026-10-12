"""Stage the original unit precharge inside the caller-owned transaction.

The fee plan supplies server quotes and ordering. This module performs the
conditional wallet update and ledger writes; it never commits or owns a
Session lifecycle.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.errors import GenerationError
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    UserCreditRow,
)
from haoai_backend.generation_core.fee_phases import FeePhasePlan, build_fee_phase_plan
from haoai_backend.generation_core.graph_planning import OperationGraphPlan


@dataclass(frozen=True, slots=True)
class UnitFeeStaged:
    unit_id: str
    task_id: str
    quoted_amount: int
    debited_amount: int
    billing_status: Literal["charged", "no_charge"]
    ledger_id: str | None
    business_key: str | None
    balance_after: int | None


@dataclass(frozen=True, slots=True)
class UnitFeeStagingResult:
    phase_plan: FeePhasePlan
    total_debited: int
    wallet_balance_after: int | None
    units: tuple[UnitFeeStaged, ...]


class InsufficientAdmissionCredits(Exception):
    """The persisted wallet could not cover the complete admitted quote."""

    def __init__(self) -> None:
        super().__init__("积分不足，无法提交生成任务")


def stage_unit_fees(
    session: Session,
    *,
    actor_id: str,
    graph: OperationGraphPlan,
    id_factory: Callable[[], str],
    now: datetime,
) -> UnitFeeStagingResult:
    """Stage every unit's financial state and positive debit ledger entries.

    The operation graph is checked against the frozen G1 fee plan before any
    wallet or ledger effect. Positive units share one conditional wallet debit
    for the complete batch, then receive ordered per-unit ledger rows whose
    balances match the actual post-update wallet value. A zero-only request
    never creates or updates a wallet and writes no synthetic ledger row.
    """

    if not actor_id or actor_id != actor_id.strip():
        raise _invalid("trusted account identity is required for unit fees")
    if not callable(id_factory):
        raise _invalid("server ID factory is unavailable")
    if not isinstance(now, datetime) or now.tzinfo is not None:
        raise _invalid("fee staging time must be a naive UTC timestamp")

    phase_plan = build_fee_phase_plan(graph.operation, graph=graph)
    ordered_units = tuple(graph.units)
    if not ordered_units or len(phase_plan.quote_lines) != len(ordered_units):
        raise _invalid("fee plan does not cover every billing unit")

    for position, (line, unit) in enumerate(
        zip(phase_plan.quote_lines, ordered_units, strict=True)
    ):
        if (
            line.order != position
            or line.unit_id != unit.unit_id
            or line.task_id != unit.task_id
            or line.item_id != unit.item_id
            or type(line.quoted_amount) is not int
            or line.quoted_amount != unit.quoted_amount
            or type(unit.quoted_amount) is not int
            or unit.quoted_amount < 0
        ):
            raise _invalid("fee plan and ordered billing units do not match")

    positive_units = tuple(unit for unit in ordered_units if unit.quoted_amount > 0)
    total_debited = sum(unit.quoted_amount for unit in positive_units)
    if total_debited != phase_plan.quoted_total:
        raise _invalid("total fee differs from the frozen fee plan")

    unit_table = BillingUnitRow.__table__
    task_table = AITaskRow.__table__
    wallet_table = UserCreditRow.__table__
    log_table = CreditLogRow.__table__

    ledger_ids = {unit.unit_id: _server_id(id_factory) for unit in positive_units}
    if len(set(ledger_ids.values())) != len(ledger_ids):
        raise _invalid("server ID factory returned duplicate ledger identifiers")

    with session.no_autoflush:
        balance_after_by_unit: dict[str, int] = {}
        wallet_balance_after: int | None = None

        if positive_units:
            business_keys = tuple(f"debit:{unit.unit_id}" for unit in positive_units)
            existing = session.execute(
                select(log_table.c.business_key).where(
                    log_table.c.business_key.in_(business_keys)
                )
            ).scalars().all()
            if existing:
                raise GenerationError(
                    "inflight_conflict",
                    "生成扣款键已占用，不能重复创建任务",
                )

            wallet_balance_after = session.execute(
                update(wallet_table)
                .where(
                    wallet_table.c.user_id == actor_id,
                    wallet_table.c.credits >= total_debited,
                )
                .values(credits=wallet_table.c.credits - total_debited)
                .returning(wallet_table.c.credits)
            ).scalar_one_or_none()
            if wallet_balance_after is None:
                raise InsufficientAdmissionCredits()

            remaining = total_debited
            for unit in positive_units:
                remaining -= unit.quoted_amount
                balance_after_by_unit[unit.unit_id] = wallet_balance_after + remaining

        inserts: list[dict[str, object]] = []
        for unit in ordered_units:
            amount = unit.quoted_amount
            if amount == 0:
                billing_status: Literal["charged", "no_charge"] = "no_charge"
                debited_amount = 0
            else:
                billing_status = "charged"
                debited_amount = amount

            unit_update = session.execute(
                update(unit_table)
                .where(unit_table.c.id == unit.unit_id)
                .values(
                    debited_amount=debited_amount,
                    refunded_amount=0,
                    billing_status=billing_status,
                    updated_at=now,
                )
            )
            if unit_update.rowcount != 1:
                raise _invalid("billing unit disappeared while staging its fee")

            task_update = session.execute(
                update(task_table)
                .where(
                    task_table.c.id == unit.task_id,
                    task_table.c.user_id == actor_id,
                )
                .values(billing_status=billing_status, updated_at=now)
            )
            if task_update.rowcount != 1:
                raise _invalid("task disappeared while staging its fee")

            ledger_id = ledger_ids.get(unit.unit_id)
            business_key = f"debit:{unit.unit_id}" if amount > 0 else None
            balance_after = balance_after_by_unit.get(unit.unit_id)
            if amount > 0:
                inserts.append(
                    {
                        "id": ledger_id,
                        "user_id": actor_id,
                        "amount": -amount,
                        "balance_after": balance_after,
                        "type": "usage",
                        "description": f"AI task {graph.policy.task_type} billing unit debit",
                        "task_id": unit.task_id,
                        "business_key": business_key,
                        "billing_unit_id": unit.unit_id,
                        "related_debit_id": None,
                        "created_at": now,
                    }
                )

        if inserts:
            session.execute(insert(log_table), inserts)

    staged_units = tuple(
        UnitFeeStaged(
            unit_id=unit.unit_id,
            task_id=unit.task_id,
            quoted_amount=unit.quoted_amount,
            debited_amount=unit.quoted_amount,
            billing_status="charged" if unit.quoted_amount else "no_charge",
            ledger_id=ledger_ids.get(unit.unit_id),
            business_key=(f"debit:{unit.unit_id}" if unit.quoted_amount else None),
            balance_after=balance_after_by_unit.get(unit.unit_id),
        )
        for unit in ordered_units
    )
    return UnitFeeStagingResult(
        phase_plan=phase_plan,
        total_debited=total_debited,
        wallet_balance_after=wallet_balance_after,
        units=staged_units,
    )


def _server_id(id_factory: Callable[[], str]) -> str:
    value = id_factory()
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 36
        or any(ord(character) < 32 for character in value)
    ):
        raise _invalid("server ID factory returned an invalid identifier")
    return value


def _invalid(detail: str) -> GenerationError:
    return GenerationError("invalid_generation_request", detail)


__all__ = (
    "InsufficientAdmissionCredits",
    "UnitFeeStaged",
    "UnitFeeStagingResult",
    "stage_unit_fees",
)
