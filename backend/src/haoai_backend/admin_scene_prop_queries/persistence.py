"""SQLAlchemy adapter preserving legacy dynamic sort and read behavior."""

from __future__ import annotations

from sqlalchemy import asc, desc
from sqlalchemy.orm import Session

from .domain import PropRecord, SceneRecord
from .ports import AdminScenePropQueriesUnitOfWork
from .tables import PropRow, SceneRow


class SqlAlchemyAdminScenePropQueriesUnitOfWork(
    AdminScenePropQueriesUnitOfWork
):
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_scenes(
        self,
        *,
        sort_field: str,
        sort_order: str,
        skip: int,
        limit: int,
    ) -> tuple[tuple[SceneRecord, ...], int]:
        query = self._session.query(SceneRow)
        total = int(query.count())
        column = getattr(SceneRow, sort_field, SceneRow.id)
        ordering = desc(column) if sort_order == "desc" else asc(column)
        rows = query.order_by(ordering).offset(skip).limit(limit).all()
        return tuple(_scene_record(row) for row in rows), total

    def get_scene(self, scene_id: str) -> SceneRecord | None:
        row = (
            self._session.query(SceneRow)
            .filter(SceneRow.id == scene_id)
            .first()
        )
        return _scene_record(row) if row is not None else None

    def list_props(
        self,
        *,
        sort_field: str,
        sort_order: str,
        skip: int,
        limit: int,
    ) -> tuple[tuple[PropRecord, ...], int]:
        query = self._session.query(PropRow)
        total = int(query.count())
        column = getattr(PropRow, sort_field, PropRow.id)
        ordering = desc(column) if sort_order == "desc" else asc(column)
        rows = query.order_by(ordering).offset(skip).limit(limit).all()
        return tuple(_prop_record(row) for row in rows), total

    def get_prop(self, prop_id: str) -> PropRecord | None:
        row = (
            self._session.query(PropRow)
            .filter(PropRow.id == prop_id)
            .first()
        )
        return _prop_record(row) if row is not None else None

    def rollback(self) -> None:
        self._session.rollback()

    def close(self) -> None:
        self._session.close()


def _scene_record(row: SceneRow) -> SceneRecord:
    return SceneRecord(
        id=row.id,
        series_id=row.series_id,
        title=row.title,
        description=row.description,
        image_url=row.image_url,
        created_at=row.created_at,
    )


def _prop_record(row: PropRow) -> PropRecord:
    return PropRecord(
        id=row.id,
        series_id=row.series_id,
        name=row.name,
        description=row.description,
        image_url=row.image_url,
        created_at=row.created_at,
    )
