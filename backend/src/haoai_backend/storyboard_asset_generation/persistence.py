"""Scoped SQL adapter for models, prompts, chapters and asset alias matching."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from haoai_backend.asset_data import naming
from haoai_backend.generation_catalog.tables import model_configs, prompt_configs, system_models, system_prompts
from haoai_backend.series_access.application import require_series_access as apply_series_access
from haoai_backend.series_access.domain import ActorIdentity
from haoai_backend.series_access.errors import SeriesAccessDenied, SeriesNotFound
from haoai_backend.series_access.persistence import SqlAlchemySeriesAccessReader
from haoai_backend.series_data import tables as series_tables
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.storyboard_generation.domain import StoryboardServerModel

from .domain import match_asset_name
from .errors import StoryboardAssetBadRequest, StoryboardAssetForbidden, StoryboardAssetNotFound, StoryboardAssetUnavailable

_FERNET_PREFIX = "gAAAAA"
_ASSET_TABLES = {
    "character": (series_tables.characters, "name"),
    "scene": (series_tables.scenes, "title"),
    "prop": (series_tables.props, "name"),
}


class SqlAlchemyStoryboardAssetUnitOfWork:
    def __init__(
        self,
        session: Session,
        *,
        decrypt_api_key: Callable[[str], str] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._decrypt_api_key = decrypt_api_key
        self._now = now or (lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    def get_chapter(self, chapter_id: str) -> Mapping[str, Any] | None:
        row = self._session.execute(
            select(series_tables.chapters).where(series_tables.chapters.c.id == chapter_id)
        ).mappings().first()
        return dict(row) if row is not None else None

    def require_series_access(self, actor: TrustedActor, series_id: str) -> None:
        try:
            apply_series_access(
                SqlAlchemySeriesAccessReader(self._session),
                ActorIdentity(actor.user_id),
                series_id,
            )
        except SeriesNotFound as exc:
            raise StoryboardAssetNotFound("剧集不存在") from exc
        except SeriesAccessDenied as exc:
            raise StoryboardAssetForbidden(exc.detail) from exc

    def resolve_model(self, actor: TrustedActor, model_id: str) -> StoryboardServerModel:
        row = self._session.execute(
            select(
                system_models.c.id,
                system_models.c.name,
                system_models.c.provider,
                system_models.c.api_key,
                system_models.c.model,
                system_models.c.base_url,
                system_models.c.params,
            ).where(system_models.c.id == model_id, system_models.c.is_active.is_(True))
        ).mappings().first()
        if row is None:
            row = self._session.execute(
                select(
                    model_configs.c.id,
                    model_configs.c.name,
                    model_configs.c.provider,
                    model_configs.c.api_key,
                    model_configs.c.model,
                    model_configs.c.base_url,
                    model_configs.c.params,
                ).where(model_configs.c.id == model_id, model_configs.c.user_id == actor.user_id)
            ).mappings().first()
        if row is None:
            raise StoryboardAssetNotFound("模型配置不存在")

        api_key = row["api_key"]
        if isinstance(api_key, str) and api_key.startswith(_FERNET_PREFIX):
            if self._decrypt_api_key is None:
                raise StoryboardAssetUnavailable("模型凭据不可用，请检查服务配置")
            try:
                api_key = self._decrypt_api_key(api_key)
            except Exception as exc:
                raise StoryboardAssetUnavailable("模型凭据不可用，请检查服务配置") from exc
        provider = row["provider"]
        if not isinstance(api_key, str) or not api_key.strip() or not isinstance(provider, str) or not provider.strip():
            raise StoryboardAssetUnavailable("模型执行配置不完整，请检查服务配置")
        model = row["model"]
        if model is not None and not isinstance(model, str):
            raise StoryboardAssetUnavailable("模型执行配置不完整，请检查服务配置")
        return StoryboardServerModel(
            id=row["id"],
            name=row["name"] or "",
            provider=provider,
            api_key=api_key,
            model=model,
            base_url=row["base_url"],
            params=row["params"],
        )

    def resolve_prompt(self, actor: TrustedActor, prompt_id: str) -> tuple[str, str]:
        row = self._session.execute(
            select(prompt_configs.c.system_prompt, prompt_configs.c.user_prompt).where(
                prompt_configs.c.id == prompt_id,
                prompt_configs.c.user_id == actor.user_id,
            )
        ).mappings().first()
        if row is None:
            row = self._session.execute(
                select(system_prompts.c.system_prompt, system_prompts.c.user_prompt).where(
                    system_prompts.c.id == prompt_id,
                    system_prompts.c.is_active.is_(True),
                )
            ).mappings().first()
        if row is None:
            raise StoryboardAssetBadRequest("所选提示词不存在、已停用或无权引用，请重新选择后重试")
        return row["system_prompt"] or "", row["user_prompt"] or ""

    def merge_candidates(
        self,
        actor: TrustedActor,
        *,
        kind: str,
        series_id: str,
        candidates: list[Any],
    ) -> list[dict[str, Any]]:
        del actor
        table, name_field = _ASSET_TABLES[kind]
        statement = select(table).where(table.c.series_id == series_id)
        if kind in {"scene", "prop"}:
            statement = statement.order_by(table.c.updated_at.desc())
        existing = [dict(row) for row in self._session.execute(statement).mappings().all()]
        staged_aliases: dict[str, list[str]] = {}
        staged_rows: dict[str, dict[str, Any]] = {}
        merged: list[dict[str, Any]] = []
        extracted_names: set[str] = set()

        for candidate in candidates:
            if isinstance(candidate, str):
                data: dict[str, Any] = {name_field: candidate, "description": ""}
            elif isinstance(candidate, Mapping):
                data = dict(candidate)
            else:
                continue
            candidate_name = str(data.get(name_field) or data.get("name") or data.get("title") or "")
            if not candidate_name.strip():
                continue
            if kind != "character":
                candidate_name = candidate_name.strip()
            extracted_names.add(candidate_name)
            hit = match_asset_name(candidate_name, existing, kind=kind, name_field=name_field)
            if kind == "character":
                if hit is None:
                    merged.append({
                        "name": candidate_name,
                        "gender": data.get("gender", ""),
                        "age": data.get("age", ""),
                        "role": data.get("role", ""),
                        "appearance": data.get("appearance", ""),
                        "voice_ref": data.get("voice_ref", ""),
                        "is_existing": False,
                        "existing_id": None,
                        "aliases": naming.load_aliases(data.get("aliases")),
                    })
                    continue
                hit_id = str(hit["id"])
                aliases = staged_aliases.setdefault(hit_id, naming.load_aliases(hit.get("aliases")))
                self._add_staged_alias(aliases, candidate_name, str(hit.get(name_field) or ""))
                staged_rows[hit_id] = dict(hit)
                merged.append({
                    "name": hit.get("name") or "",
                    "gender": hit.get("gender") or "",
                    "age": hit.get("age") or "",
                    "role": hit.get("role") or "",
                    "appearance": hit.get("appearance") or "",
                    "voice_ref": hit.get("voice_ref") or "",
                    "is_existing": True,
                    "existing_id": hit_id,
                    "aliases": list(aliases),
                })
                continue

            if hit is None:
                merged.append({
                    name_field: candidate_name,
                    "description": data.get("description", "") or "",
                    "is_existing": False,
                    "existing_id": None,
                    "aliases": naming.load_aliases(data.get("aliases")),
                })
                continue
            hit_id = str(hit["id"])
            aliases = staged_aliases.setdefault(hit_id, naming.load_aliases(hit.get("aliases")))
            self._add_staged_alias(aliases, candidate_name, str(hit.get(name_field) or ""))
            staged_rows[hit_id] = dict(hit)
            merged.append({
                name_field: candidate_name,
                "description": data.get("description", "") or hit.get("description") or "",
                "is_existing": True,
                "existing_id": hit_id,
                "aliases": list(aliases),
            })

        merged_names = {
            naming.normalize_name(item.get(name_field) or "")
            for item in merged
            if item.get(name_field)
        }
        backfilled = 0
        extracted_character_names = set(extracted_names)
        for row in existing:
            name = str(row.get(name_field) or "").strip()
            normalized = naming.normalize_name(name)
            if not name or not normalized:
                continue
            if kind == "character":
                if name in extracted_character_names:
                    continue
                merged.append(self._character_backfill(row, staged_aliases))
            else:
                if normalized in merged_names or backfilled >= 50:
                    continue
                merged.append({
                    name_field: name,
                    "description": row.get("description") or "",
                    "is_existing": True,
                    "existing_id": str(row["id"]),
                    "is_backfill": True,
                    "aliases": list(staged_aliases.get(str(row["id"]), naming.load_aliases(row.get("aliases")))),
                })
                merged_names.add(normalized)
                backfilled += 1

        changed_aliases = {
            asset_id: aliases
            for asset_id, aliases in staged_aliases.items()
            if naming.load_aliases(staged_rows[asset_id].get("aliases")) != aliases
        }
        for asset_id, aliases in changed_aliases.items():
            self._session.execute(
                update(table).where(table.c.id == asset_id, table.c.series_id == series_id).values(
                    aliases=naming.dump_aliases(aliases),
                    canonical_key=naming.make_key(kind, staged_rows[asset_id][name_field]),
                    updated_at=self._now(),
                )
            )
        if changed_aliases:
            self._session.commit()
        return merged

    @staticmethod
    def _add_staged_alias(aliases: list[str], name: str, primary: str) -> None:
        appended = naming.append_alias(aliases, name, primary)
        if appended is None:
            return
        aliases[:] = naming.load_aliases(appended)

    @staticmethod
    def _character_backfill(
        row: Mapping[str, Any], staged_aliases: Mapping[str, list[str]]
    ) -> dict[str, Any]:
        asset_id = str(row["id"])
        return {
            "name": row.get("name") or "",
            "gender": row.get("gender") or "",
            "age": row.get("age") or "",
            "role": row.get("role") or "",
            "appearance": row.get("appearance") or "",
            "voice_ref": row.get("voice_ref") or "",
            "is_existing": True,
            "existing_id": asset_id,
            "is_backfill": True,
            "aliases": list(staged_aliases.get(asset_id, naming.load_aliases(row.get("aliases")))),
        }

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()

    def close(self) -> None:
        self._session.close()


def storyboard_asset_uow_factory(
    session_factory: Callable[[], Session] | None,
    *,
    decrypt_api_key: Callable[[str], str] | None = None,
) -> Callable[[], SqlAlchemyStoryboardAssetUnitOfWork] | None:
    if session_factory is None:
        return None

    def create() -> SqlAlchemyStoryboardAssetUnitOfWork:
        return SqlAlchemyStoryboardAssetUnitOfWork(
            session_factory(), decrypt_api_key=decrypt_api_key
        )

    return create
