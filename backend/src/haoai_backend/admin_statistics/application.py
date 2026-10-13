"""Administrator statistics use case and transaction lifecycle."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

from .domain import AdminStatisticsActor, statistics_payload
from .errors import AdminStatisticsForbidden, AdminStatisticsUnavailable
from .ports import BalanceReader, Clock, UnitOfWorkFactory, AdminStatisticsUnitOfWork


@contextmanager
def _open(factory: UnitOfWorkFactory | None) -> Iterator[AdminStatisticsUnitOfWork]:
    if factory is None:
        raise AdminStatisticsUnavailable()
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


def get_admin_statistics(
    factory: UnitOfWorkFactory | None,
    actor: AdminStatisticsActor,
    balance_reader: BalanceReader,
    clock: Clock = datetime.utcnow,
) -> dict[str, Any]:
    if not actor.is_superuser:
        raise AdminStatisticsForbidden()
    with _open(factory) as unit_of_work:
        now = clock()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        values = unit_of_work.aggregate_statistics(now, month_start)
        try:
            token = unit_of_work.get_muse_token()
            upstream_balance = balance_reader.read_balance(token) if token else None
        except Exception:
            upstream_balance = None
        return statistics_payload(values, upstream_balance)
