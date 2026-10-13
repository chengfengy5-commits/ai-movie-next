"""SQLAlchemy Core persistence for storyboard save workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, insert, select, update
from sqlalchemy.orm import Session

from haoai_backend.asset_data.naming import make_key
from haoai_backend.asset_data.tables import characters, props, scenes
from haoai_backend.personal_production.notes import stage_media_reconciliation
from haoai_backend.series_access.application import require_series_access
from haoai_backend.series_access.domain import ActorIdentity
from haoai_backend.series_access.persistence import SqlAlchemySeriesAccessReader
from haoai_backend.series_data.tables import chapters
from haoai_backend.shared.identity import TrustedActor

from .domain import AssetKind, AssetRecord, ChapterRecord

_ASSET_TABLES = {
    "character": (characters, "name"),
    "scene": (scenes, "title"),
    "prop": (props, "name"),
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SqlAlchemyStoryboardSaveStore:
    """A request-owned Session adapter; callers control every commit phase."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def load_chapter(self, chapter_id: str) -> ChapterRecord | None:
        row = self._session.execute(
            select(
                chapters.c.id,
                chapters.c.series_id,
                chapters.c.title,
                chapters.c.content,
            ).where(chapters.c.id == chapter_id)
        ).mappings().first()
        if row is None:
            return None
        return ChapterRecord(row["id"], row["series_id"], row["title"], row["content"])

    def require_access(self, actor: TrustedActor, series_id: str) -> None:
        require_series_access(
            SqlAlchemySeriesAccessReader(self._session),
            ActorIdentity(actor.user_id),
            series_id,
        )

    def list_assets(self, kind: AssetKind, series_id: str) -> list[AssetRecord]:
        table, name_column = _ASSET_TABLES[kind]
        columns = [
            table.c.id,
            table.c.series_id,
            getattr(table.c, name_column),
            table.c.aliases,
            table.c.canonical_key,
            table.c.description,
        ]
        if kind == "character":
            columns.extend((table.c.image_url, table.c.audio_url, table.c.voice_ref))
        rows = self._session.execute(
            select(*columns).where(table.c.series_id == series_id)
        ).mappings().all()
        return [
            AssetRecord(
                id=row["id"],
                series_id=row["series_id"],
                name=row[name_column],
                aliases=row["aliases"],
                canonical_key=row["canonical_key"],
                description=row["description"],
                image_url=row["image_url"] if kind == "character" else None,
                audio_url=row["audio_url"] if kind == "character" else None,
                voice_ref=row["voice_ref"] if kind == "character" else None,
            )
            for row in rows
        ]

    def asset_series_id(self, kind: AssetKind, asset_id: str) -> str | None:
        table, _ = _ASSET_TABLES[kind]
        return self._session.execute(
            select(table.c.series_id).where(table.c.id == asset_id)
        ).scalar_one_or_none()

    def find_asset(
        self,
        kind: AssetKind,
        asset_id: str,
        series_id: str,
    ) -> AssetRecord | None:
        table, name_column = _ASSET_TABLES[kind]
        columns = [
            table.c.id,
            table.c.series_id,
            getattr(table.c, name_column),
            table.c.aliases,
            table.c.canonical_key,
            table.c.description,
        ]
        if kind == "character":
            columns.extend((table.c.image_url, table.c.audio_url, table.c.voice_ref))
        row = self._session.execute(
            select(*columns).where(table.c.id == asset_id, table.c.series_id == series_id)
        ).mappings().first()
        if row is None:
            return None
        return AssetRecord(
            id=row["id"],
            series_id=row["series_id"],
            name=row[name_column],
            aliases=row["aliases"],
            canonical_key=row["canonical_key"],
            description=row["description"],
            image_url=row["image_url"] if kind == "character" else None,
            audio_url=row["audio_url"] if kind == "character" else None,
            voice_ref=row["voice_ref"] if kind == "character" else None,
        )

    def insert_asset(
        self,
        kind: AssetKind,
        asset_id: str,
        series_id: str,
        candidate: Mapping[str, Any],
    ) -> None:
        table, name_column = _ASSET_TABLES[kind]
        name = str(candidate.get(name_column) or "")
        if kind != "character":
            name = name.strip()
        values: dict[str, Any] = {
            "id": asset_id,
            "series_id": series_id,
            name_column: name,
            "aliases": "[]",
            "canonical_key": make_key(kind, name),
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
        }
        if kind == "character":
            values.update(
                gender=candidate.get("gender", ""),
                age=candidate.get("age", ""),
                role=candidate.get("role", ""),
                appearance=candidate.get("appearance", ""),
                voice_ref=candidate.get("voice_ref", ""),
            )
        else:
            values["description"] = candidate.get("description", "")
        self._session.execute(insert(table).values(**values))

    def update_aliases(
        self, kind: AssetKind, asset_id: str, series_id: str, aliases: str
    ) -> None:
        table, name_column = _ASSET_TABLES[kind]
        stored_name = self._session.execute(
            select(getattr(table.c, name_column)).where(
                table.c.id == asset_id, table.c.series_id == series_id
            )
        ).scalar_one_or_none()
        if stored_name is None:
            return
        self._session.execute(
            update(table)
            .where(table.c.id == asset_id, table.c.series_id == series_id)
            .values(
                aliases=aliases,
                canonical_key=make_key(kind, stored_name),
                updated_at=_utc_now(),
            )
        )

    def update_chapter_content(
        self,
        chapter: ChapterRecord,
        actor: TrustedActor,
        content: str,
    ) -> None:
        if content == chapter.content:
            return
        # This public facade stages media and approval reconciliation in the same
        # Session; the save workflow retains ownership of commit and rollback.
        stage_media_reconciliation(
            self._session,
            chapter.id,
            chapter_content_override=content,
        )
        result = self._session.execute(
            update(chapters)
            .where(
                chapters.c.id == chapter.id,
                chapters.c.series_id == chapter.series_id,
            )
            .values(content=content, updated_at=_utc_now())
        )
        if result.rowcount != 1:
            raise RuntimeError("章节内容更新失败")

    def list_chapters(self, series_id: str) -> list[ChapterRecord]:
        rows = self._session.execute(
            select(
                chapters.c.id,
                chapters.c.series_id,
                chapters.c.title,
                chapters.c.content,
            ).where(chapters.c.series_id == series_id)
        ).mappings().all()
        return [
            ChapterRecord(row["id"], row["series_id"], row["title"], row["content"])
            for row in rows
        ]

    def delete_assets(self, kind: AssetKind, series_id: str, asset_ids: Sequence[str]) -> None:
        if not asset_ids:
            return
        table, _ = _ASSET_TABLES[kind]
        self._session.execute(
            delete(table).where(table.c.series_id == series_id, table.c.id.in_(asset_ids))
        )

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()
