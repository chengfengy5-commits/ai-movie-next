"""Administrator credit query and update use cases."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from .domain import AdminCreditQueriesActor
from .errors import (
    AdminCreditQueriesForbidden,
    AdminCreditQueriesUnavailable,
    UserCreditNotFound,
)
from .ports import AdminCreditQueriesUnitOfWork, UnitOfWorkFactory
from .schemas import (
    AdminCreditUpdateBody,
    CreditLogListPayload,
    UserCreditListPayload,
    UserCreditPayload,
    credit_log_payload,
    user_credit_payload,
)


@contextmanager
def _open(
    factory: UnitOfWorkFactory | None,
) -> Iterator[AdminCreditQueriesUnitOfWork]:
    if factory is None:
        raise AdminCreditQueriesUnavailable()
    unit_of_work = factory()
    try:
        yield unit_of_work
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
    else:
        unit_of_work.close()


def _require_admin(actor: AdminCreditQueriesActor) -> None:
    if not actor.is_superuser:
        raise AdminCreditQueriesForbidden()


def list_user_credits(
    factory: UnitOfWorkFactory | None,
    actor: AdminCreditQueriesActor,
    *,
    skip: int,
    limit: int,
    sort_field: str,
    sort_order: str,
) -> UserCreditListPayload:
    _require_admin(actor)
    with _open(factory) as unit_of_work:
        records, total = unit_of_work.list_user_credits(
            skip=skip,
            limit=limit,
            sort_field=sort_field,
            sort_order=sort_order,
        )
        return {
            "data": [user_credit_payload(record) for record in records],
            "total": total,
        }


def get_user_credit(
    factory: UnitOfWorkFactory | None,
    actor: AdminCreditQueriesActor,
    credit_id: str,
) -> UserCreditPayload:
    _require_admin(actor)
    with _open(factory) as unit_of_work:
        record = unit_of_work.get_user_credit(credit_id)
        if record is None:
            raise UserCreditNotFound()
        return user_credit_payload(record)


def update_user_credit(
    factory: UnitOfWorkFactory | None,
    actor: AdminCreditQueriesActor,
    credit_id: str,
    data: AdminCreditUpdateBody,
) -> UserCreditPayload:
    _require_admin(actor)
    with _open(factory) as unit_of_work:
        row = unit_of_work.load_user_credit_for_update(credit_id)
        if row is None:
            raise UserCreditNotFound()
        if "credits" in data:
            unit_of_work.set_user_credit_amount(row, int(data["credits"]))
        unit_of_work.commit()
        record = unit_of_work.refresh_user_credit(row)
        return user_credit_payload(record)


def list_credit_logs(
    factory: UnitOfWorkFactory | None,
    actor: AdminCreditQueriesActor,
    *,
    skip: int,
    limit: int,
    sort_field: str,
    sort_order: str,
    user_id: str | None,
) -> CreditLogListPayload:
    _require_admin(actor)
    with _open(factory) as unit_of_work:
        records, total = unit_of_work.list_credit_logs(
            skip=skip,
            limit=limit,
            sort_field=sort_field,
            sort_order=sort_order,
            user_id=user_id,
        )
        return {
            "data": [credit_log_payload(record) for record in records],
            "total": total,
        }
