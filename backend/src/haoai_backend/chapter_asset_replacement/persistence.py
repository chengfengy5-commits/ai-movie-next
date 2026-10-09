"""SQLAlchemy Core unit of work for chapter asset replacement."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from haoai_backend.shared.identity import TrustedActor

from .domain import ChapterAsset, ChapterRecord
from .errors import ChapterAssetReplacementUnavailable
from .media_writes import reconcile_replacement_media
from .ports import SeriesAccessCheck
from .tables import ASSET_DISPLAY_COLUMNS, ASSET_TABLES, chapters


class SqlAlchemyChapterAssetReplacementUnitOfWork:
    """A request-scoped adapter; application code owns commits and rollback."""

    def __init__(
        self,
        session: Session,
        series_access_check: SeriesAccessCheck | None,
    ) -> None:
        self._session = session
        self._series_access_check = series_access_check

    def ensure_clean(self) -> None:
        if self._session.new or self._session.dirty or self._session.deleted:
            raise RuntimeError("chapter replacement requires a clean request-scoped Session")

    def load_chapter(
        self,
        chapter_id: str,
        *,
        lock: bool = False,
    ) -> ChapterRecord | None:
        statement = select(chapters).where(chapters.c.id == chapter_id)
        if lock:
            statement = statement.with_for_update()
        row = self._session.execute(statement).mappings().first()
        return _chapter_record(row) if row is not None else None

    def require_series_access(self, actor: TrustedActor, series_id: str) -> None:
        if self._series_access_check is None:
            raise ChapterAssetReplacementUnavailable("剧集访问策略尚未接线")
        self._series_access_check(self._session, actor, series_id)

    def load_asset_for_replacement(
        self,
        asset_type: str,
        asset_id: str,
        series_id: str,
    ) -> ChapterAsset | None:
        table = ASSET_TABLES[asset_type]
        display_column = ASSET_DISPLAY_COLUMNS[asset_type]
        row = self._session.execute(
            select(
                table.c.id,
                table.c.series_id,
                display_column.label("display_name"),
            ).where(
                table.c.id == asset_id,
                table.c.series_id == series_id,
            )
        ).mappings().first()
        return _asset_record(row) if row is not None else None

    def reconcile_chapter_media(
        self,
        chapter: ChapterRecord,
        actor: TrustedActor,
        content_override: str,
    ) -> None:
        reconcile_replacement_media(
            self._session,
            chapter,
            actor,
            content_override,
        )

    def update_chapter_content(self, chapter_id: str, content: str) -> int:
        result = self._session.execute(
            update(chapters)
            .where(chapters.c.id == chapter_id)
            .values(content=content, updated_at=_now())
        )
        return int(result.rowcount or 0)

    def list_chapters_for_series(self, series_id: str) -> list[ChapterRecord]:
        rows = self._session.execute(
            select(chapters).where(chapters.c.series_id == series_id)
        ).mappings().all()
        return [_chapter_record(row) for row in rows]

    def list_orphan_candidates(
        self,
        series_id: str,
        asset_type: str,
        asset_id: str,
    ) -> list[str]:
        table = ASSET_TABLES[asset_type]
        rows = self._session.execute(
            select(table.c.id).where(
                table.c.series_id == series_id,
                table.c.id == asset_id,
            )
        ).scalars().all()
        return list(rows)

    def delete_asset_by_id(self, asset_type: str, asset_id: str) -> int:
        table = ASSET_TABLES[asset_type]
        result = self._session.execute(
            delete(table).where(table.c.id == asset_id)
        )
        return int(result.rowcount or 0)

    def load_asset_by_id(
        self,
        asset_type: str,
        asset_id: str,
    ) -> ChapterAsset | None:
        table = ASSET_TABLES[asset_type]
        display_column = ASSET_DISPLAY_COLUMNS[asset_type]
        row = self._session.execute(
            select(
                table.c.id,
                table.c.series_id,
                display_column.label("display_name"),
            ).where(table.c.id == asset_id)
        ).mappings().first()
        return _asset_record(row) if row is not None else None

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()

    def close(self) -> None:
        self._session.close()


def _chapter_record(row: Any) -> ChapterRecord:
    return ChapterRecord(
        id=row["id"],
        series_id=row["series_id"],
        title=row["title"],
        content=row["content"],
        order=int(row["order"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _asset_record(row: Any) -> ChapterAsset:
    return ChapterAsset(
        id=row["id"],
        series_id=row["series_id"],
        display_name=row["display_name"],
    )


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
