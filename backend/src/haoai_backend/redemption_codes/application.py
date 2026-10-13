"""Legacy-compatible redemption use cases and transaction ownership."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TypeVar
from uuid import uuid4

from .code_generator import generate_code_string
from .domain import (
    GenerateRedemptionCodesCommand,
    NewCreditLog,
    NewRedemptionCode,
    NewUserCredit,
    RedemptionCodesActor,
    RedeemRedemptionCodeCommand,
    redemption_code_payload,
)
from .errors import (
    RedemptionCodeAlreadyUsed,
    RedemptionCodeExpired,
    RedemptionCodeNotFound,
    RedemptionCodeTypeUnknown,
    RedemptionCodesBadRequest,
    RedemptionCodesForbidden,
    RedemptionCodesUnavailable,
)
from .ports import (
    Clock,
    CodeGenerator,
    RedemptionCodesUnitOfWork,
    UnitOfWorkFactory,
)

_Result = TypeVar("_Result")


def _with_unit_of_work(
    factory: UnitOfWorkFactory | None,
    operation: Callable[[RedemptionCodesUnitOfWork], _Result],
) -> _Result:
    if factory is None:
        raise RedemptionCodesUnavailable()

    unit_of_work = factory()
    try:
        result = operation(unit_of_work)
    except BaseException:
        try:
            unit_of_work.rollback()
        except BaseException:
            pass
        try:
            unit_of_work.close()
        except BaseException:
            pass
        raise

    unit_of_work.close()
    return result


def _require_administrator(actor: RedemptionCodesActor) -> None:
    if not actor.is_superuser:
        raise RedemptionCodesForbidden()


def generate_redemption_codes(
    factory: UnitOfWorkFactory | None,
    actor: RedemptionCodesActor,
    command: GenerateRedemptionCodesCommand,
    *,
    code_generator: CodeGenerator | None = None,
) -> dict[str, object]:
    _require_administrator(actor)
    generator = generate_code_string if code_generator is None else code_generator

    def generate_with(unit_of_work: RedemptionCodesUnitOfWork) -> dict[str, object]:
        expires_at = None
        if command.expires_at:
            try:
                expires_at = datetime.fromisoformat(command.expires_at)
            except ValueError:
                raise RedemptionCodesBadRequest(
                    "expires_at 格式不正确，请使用 ISO 格式"
                ) from None

        codes: list[NewRedemptionCode] = []
        for _ in range(command.count):
            while True:
                code = generator()
                if not unit_of_work.code_exists(code):
                    break

            record = NewRedemptionCode(
                id=str(uuid4()),
                code=code,
                type=command.type,
                value=command.value,
                created_by=actor.user_id,
                expires_at=expires_at,
            )
            unit_of_work.add_generated_code(record)
            codes.append(record)

        unit_of_work.commit()
        refreshed = []
        for code in codes:
            persisted = unit_of_work.refresh_generated_code(code.id)
            if persisted is None:
                raise RuntimeError("committed redemption code could not be refreshed")
            refreshed.append(redemption_code_payload(persisted))

        return {
            "codes": refreshed,
            "count": len(refreshed),
            "type": command.type,
            "value": command.value,
        }

    return _with_unit_of_work(factory, generate_with)


def redeem_redemption_code(
    factory: UnitOfWorkFactory | None,
    actor: RedemptionCodesActor,
    command: RedeemRedemptionCodeCommand,
    *,
    clock: Clock | None = None,
) -> dict[str, object]:
    now = datetime.utcnow if clock is None else clock

    def redeem_with(unit_of_work: RedemptionCodesUnitOfWork) -> dict[str, object]:
        code = unit_of_work.load_code(command.code.strip().upper())
        if code is None:
            raise RedemptionCodeNotFound()

        if code.is_used:
            raise RedemptionCodeAlreadyUsed()

        if code.expires_at and now() > code.expires_at:
            raise RedemptionCodeExpired()

        unit_of_work.mark_code_used(
            code.id,
            user_id=actor.user_id,
            used_at=now(),
        )

        if code.type == "membership":
            current_time = now()
            days = code.value
            if actor.membership_type == "premium" and actor.membership_expires_at:
                new_expiry = actor.membership_expires_at + timedelta(days=days)
            else:
                new_expiry = current_time + timedelta(days=days)

            unit_of_work.set_membership(
                actor.user_id,
                membership_type="premium",
                membership_expires_at=new_expiry,
            )
            unit_of_work.commit()
            return {
                "message": f"兑换成功！会员时长增加 {days} 天",
                "type": "membership",
                "value": days,
                "detail": f"会员有效期至：{new_expiry.strftime('%Y-%m-%d %H:%M')}",
            }

        if code.type == "credits":
            amount = code.value
            credit = unit_of_work.load_user_credit(actor.user_id)
            if credit is None:
                unit_of_work.add_user_credit(
                    NewUserCredit(
                        id=str(uuid4()),
                        user_id=actor.user_id,
                        credits=0,
                    )
                )
                unit_of_work.flush()
                old_balance = 0
            else:
                old_balance = credit.credits

            balance_after = old_balance + amount
            unit_of_work.set_user_credit_balance(actor.user_id, balance_after)
            unit_of_work.add_credit_log(
                NewCreditLog(
                    id=str(uuid4()),
                    user_id=actor.user_id,
                    amount=amount,
                    balance_after=balance_after,
                    type="charge",
                    description=f"兑换码兑换：{code.code}",
                )
            )
            unit_of_work.commit()
            refreshed_balance = unit_of_work.user_credit_balance(actor.user_id)
            return {
                "message": f"兑换成功！获得 {amount} 积分",
                "type": "credits",
                "value": amount,
                "detail": f"当前积分余额：{refreshed_balance}",
            }

        raise RedemptionCodeTypeUnknown()

    return _with_unit_of_work(factory, redeem_with)
