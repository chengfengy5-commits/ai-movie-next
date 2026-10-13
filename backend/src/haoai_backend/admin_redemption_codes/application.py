"""Redemption-code use cases with explicit transaction ownership."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import TypeVar
from uuid import uuid4

from .code_generator import _generate_code_string
from .domain import (
    AdminRedemptionCodesActor,
    NewRedemptionCode,
    RedemptionCode,
    redemption_code_payload,
)
from .errors import (
    AdminRedemptionCodeBadRequest,
    AdminRedemptionCodeNotFound,
    AdminRedemptionCodesForbidden,
    AdminRedemptionCodesUnavailable,
)
from .ports import CodeGenerator, UnitOfWorkFactory

_Result = TypeVar("_Result")


def _with_unit_of_work(
    factory: UnitOfWorkFactory | None,
    operation: Callable[[object], _Result],
) -> _Result:
    if factory is None:
        raise AdminRedemptionCodesUnavailable()

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


def _require_admin(actor: AdminRedemptionCodesActor) -> None:
    if not actor.is_superuser:
        raise AdminRedemptionCodesForbidden()


def list_redemption_codes(
    factory: UnitOfWorkFactory | None,
    actor: AdminRedemptionCodesActor,
    *,
    skip: int = 0,
    limit: int = 10,
    sort_field: str = "created_at",
    sort_order: str = "desc",
    code: str | None = None,
    code_type: str | None = None,
    is_used: str | None = None,
    value: int | None = None,
) -> dict[str, object]:
    _require_admin(actor)

    def list_with(unit_of_work):
        rows, total = unit_of_work.list_codes(
            code=code,
            code_type=code_type,
            is_used=is_used,
            value=value,
            sort_field=sort_field,
            sort_order=sort_order,
            skip=skip,
            limit=limit,
        )
        return {
            "data": [redemption_code_payload(row) for row in rows],
            "total": total,
        }

    return _with_unit_of_work(factory, list_with)


def get_redemption_code(
    factory: UnitOfWorkFactory | None,
    actor: AdminRedemptionCodesActor,
    code_id: str,
) -> dict[str, object]:
    _require_admin(actor)

    def get_with(unit_of_work):
        row = unit_of_work.load_code(code_id)
        if row is None:
            raise AdminRedemptionCodeNotFound()
        return redemption_code_payload(row)

    return _with_unit_of_work(factory, get_with)


def create_redemption_codes(
    factory: UnitOfWorkFactory | None,
    actor: AdminRedemptionCodesActor,
    data: Mapping[str, object],
    *,
    code_generator: CodeGenerator | None = None,
) -> dict[str, object]:
    _require_admin(actor)

    code_type = data.get("type", "membership")
    if code_type not in ("membership", "credits"):
        raise AdminRedemptionCodeBadRequest("type 必须是 membership 或 credits")

    value = int(data.get("value", 0))
    if value <= 0:
        raise AdminRedemptionCodeBadRequest("value 必须大于 0")

    count = int(data.get("count", 1))
    if count < 1 or count > 1000:
        raise AdminRedemptionCodeBadRequest("count 范围 1-1000")

    expires_at = None
    if data.get("expires_at"):
        try:
            expires_at = datetime.fromisoformat(
                data["expires_at"].replace("Z", "+00:00")
            )
        except (ValueError, AttributeError):
            raise AdminRedemptionCodeBadRequest(
                "expires_at 格式不正确"
            ) from None

    generator = code_generator or _generate_code_string

    def create_with(unit_of_work):
        code_ids: list[str] = []
        for _ in range(count):
            while True:
                code = generator()
                if not unit_of_work.code_exists(code):
                    break

            code_id = str(uuid4())
            unit_of_work.add_code(
                NewRedemptionCode(
                    id=code_id,
                    code=code,
                    type=code_type,
                    value=value,
                    created_by=actor.user_id,
                    expires_at=expires_at,
                )
            )
            code_ids.append(code_id)

        unit_of_work.commit()
        first = unit_of_work.refresh_code(code_ids[0])
        if first is None:
            raise RuntimeError("committed redemption code could not be refreshed")

        payload = redemption_code_payload(first)
        payload["_generated_count"] = count
        return payload

    return _with_unit_of_work(factory, create_with)


def delete_redemption_code(
    factory: UnitOfWorkFactory | None,
    actor: AdminRedemptionCodesActor,
    code_id: str,
) -> dict[str, str]:
    _require_admin(actor)

    def delete_with(unit_of_work):
        if not unit_of_work.delete_code(code_id):
            raise AdminRedemptionCodeNotFound()
        unit_of_work.commit()
        return {"message": "删除成功"}

    return _with_unit_of_work(factory, delete_with)
