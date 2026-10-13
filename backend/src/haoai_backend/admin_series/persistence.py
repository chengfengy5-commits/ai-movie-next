"""Private SQLAlchemy mappings and adapter for administrator series routes."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from sqlalchemy import asc, desc
from sqlalchemy.orm import Session

from .domain import NewSeries, SeriesRecord
from .ports import AdminSeriesUnitOfWork
from .tables import SeriesRow


def _record(row: SeriesRow) -> SeriesRecord:
    return SeriesRecord(
        id=row.id,
        user_id=row.user_id,
        name=row.name,
        description=row.description,
        image_url=row.image_url,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlAlchemyAdminSeriesUnitOfWork(AdminSeriesUnitOfWork):
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_series(
        self,
        *,
        user_id: str | None,
        name: str | None,
        sort_field: str,
        sort_order: str,
        skip: int,
        limit: int,
    ) -> tuple[tuple[SeriesRecord, ...], int]:
        query = self._session.query(SeriesRow)
        if user_id:
            query = query.filter(SeriesRow.user_id == user_id)
        if name:
            query = query.filter(SeriesRow.name.ilike(f"%{name}%"))

        total = query.count()
        column = getattr(SeriesRow, sort_field, SeriesRow.id)
        ordering = desc(column) if sort_order == "desc" else asc(column)
        rows = query.order_by(ordering).offset(skip).limit(limit).all()
        return tuple(_record(row) for row in rows), total

    def load_series(self, series_id: str) -> SeriesRecord | None:
        row = (
            self._session.query(SeriesRow)
            .filter(SeriesRow.id == series_id)
            .first()
        )
        return None if row is None else _record(row)

    def load_series_for_update(self, series_id: str) -> SeriesRow | None:
        return (
            self._session.query(SeriesRow)
            .filter(SeriesRow.id == series_id)
            .first()
        )

    def add_series(self, series: NewSeries) -> SeriesRow:
        row = SeriesRow(
            id=series.id,
            user_id=series.user_id,
            name=series.name,
            description=series.description,
            image_url=series.image_url,
        )
        self._session.add(row)
        return row

    def commit(self) -> None:
        self._session.commit()

    def refresh_series(self, series: object) -> SeriesRecord:
        row = cast(SeriesRow, series)
        self._session.refresh(row)
        return _record(row)

    def rollback(self) -> None:
        self._session.rollback()

    def close(self) -> None:
        self._session.close()


def admin_series_uow_factory(
    session_factory: Callable[[], Session],
) -> Callable[[], SqlAlchemyAdminSeriesUnitOfWork]:
    def create() -> SqlAlchemyAdminSeriesUnitOfWork:
        return SqlAlchemyAdminSeriesUnitOfWork(session_factory())

    return create
