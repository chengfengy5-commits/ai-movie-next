"""Same-session Core reads and locks for admission preparation."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from haoai_backend.authentication.tables import user_credits, users
from haoai_backend.generation_contracts.types import (
    BatchImageReceipt,
    BatchImageTaskReceipt,
    SingleAdmissionReceipt,
)
from haoai_backend.generation_core.base_schema import AITaskRow, BillingUnitRow, TaskSubmissionRow
from haoai_backend.series_access.domain import TeamMembership, can_enter_claimed_series
from haoai_backend.series_access.tables import series, team_members

from .admission_types import AdmissionRefusal
from .domain import CatalogModel, CatalogPrompt, ModelCategory, ModelSource, PromptOperation, UserModelOrder
from .ports import CatalogReader
from .tables import (
    ASSET_TABLES,
    chapters,
    model_configs,
    prompt_configs,
    system_models,
    system_prompts,
    user_model_orders,
)


@dataclass(frozen=True, slots=True)
class AccountFacts:
    user_id: str
    is_superuser: bool
    membership_type: str
    membership_expires_at: Any


@dataclass(frozen=True, slots=True)
class StoredSubmission:
    id: str
    user_id: str
    operation: str
    idempotency_key: str
    request_digest: str
    task_ids: object
    status: str


@dataclass(frozen=True, slots=True)
class ResourceDiscovery:
    requested_chapter_ids: tuple[str, ...]
    chapter_rows: Mapping[str, Mapping[str, object] | None]
    asset_rows: Mapping[tuple[str, str], Mapping[str, object] | None]

    @property
    def series_ids(self) -> tuple[str, ...]:
        found = {
            row["series_id"]
            for row in (*self.chapter_rows.values(), *self.asset_rows.values())
            if row is not None and isinstance(row.get("series_id"), str)
        }
        return tuple(sorted(found))

    @property
    def chapter_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.chapter_rows))


@dataclass(frozen=True, slots=True)
class LockedResources:
    series_rows: Mapping[str, Mapping[str, object]]
    chapter_rows: Mapping[str, Mapping[str, object]]
    asset_rows: Mapping[tuple[str, str], Mapping[str, object]]
    member_rows: Mapping[str, Mapping[str, object]]


class LockedCatalogReader(CatalogReader):
    """Catalog values built only from rows locked by this caller transaction."""

    def __init__(
        self,
        *,
        user_id: str,
        system_models: Sequence[CatalogModel],
        owned_models: Sequence[CatalogModel],
        system_prompts: Sequence[CatalogPrompt],
        owned_prompts: Sequence[CatalogPrompt],
        model_orders: Sequence[UserModelOrder],
    ) -> None:
        self._user_id = user_id
        self._system_models = tuple(system_models)
        self._owned_models = tuple(owned_models)
        self._system_prompts = tuple(system_prompts)
        self._owned_prompts = tuple(owned_prompts)
        self._model_orders = tuple(model_orders)

    def active_system_model(self, model_id: str, category: str) -> CatalogModel | None:
        return next(
            (
                model
                for model in self._system_models
                if model.id == model_id
                and model.category.value == category
                and model.owner_id is None
                and model.active
            ),
            None,
        )

    def owned_model(self, user_id: str, model_id: str, category: str) -> CatalogModel | None:
        if user_id != self._user_id:
            return None
        return next(
            (
                model
                for model in self._owned_models
                if model.id == model_id
                and model.category.value == category
                and model.owner_id == user_id
            ),
            None,
        )

    def active_system_text_models(self) -> Sequence[CatalogModel]:
        return tuple(
            model
            for model in self._system_models
            if model.category is ModelCategory.TEXT and model.active
        )

    def owned_text_models(self, user_id: str) -> Sequence[CatalogModel]:
        if user_id != self._user_id:
            return ()
        return tuple(
            model
            for model in self._owned_models
            if model.category is ModelCategory.TEXT and model.owner_id == user_id
        )

    def user_model_orders(self, user_id: str, category: str) -> Sequence[UserModelOrder]:
        if user_id != self._user_id:
            return ()
        return tuple(
            order for order in self._model_orders if order.model_id and category == "text"
        )

    def active_system_prompt(self, prompt_id: str) -> CatalogPrompt | None:
        return next(
            (
                prompt
                for prompt in self._system_prompts
                if prompt.id == prompt_id and prompt.owner_id is None and prompt.active
            ),
            None,
        )

    def owned_prompt(self, user_id: str, prompt_id: str) -> CatalogPrompt | None:
        if user_id != self._user_id:
            return None
        return next(
            (
                prompt
                for prompt in self._owned_prompts
                if prompt.id == prompt_id and prompt.owner_id == user_id
            ),
            None,
        )

    def model_details(self, selection: object) -> CatalogModel | None:
        source = getattr(selection, "source", None)
        model_id = getattr(selection, "catalog_id", None)
        category = getattr(selection, "category", None)
        rows = self._system_models if source is ModelSource.SYSTEM else self._owned_models
        owner_id = None if source is ModelSource.SYSTEM else self._user_id
        return next(
            (
                model
                for model in rows
                if model.id == model_id
                and model.category is category
                and model.owner_id == owner_id
            ),
            None,
        )


class AdmissionPersistence:
    """Read and lock current rows through the supplied Session only."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def lock_account(self, user_id: str) -> AccountFacts | None:
        with self._session.no_autoflush:
            row = self._session.execute(
                select(
                    users.c.id,
                    users.c.is_superuser,
                    users.c.membership_type,
                    users.c.membership_expires_at,
                )
                .where(users.c.id == user_id)
                .with_for_update()
            ).mappings().one_or_none()
        if row is None:
            return None
        return AccountFacts(
            user_id=row["id"],
            is_superuser=bool(row["is_superuser"]),
            membership_type=row["membership_type"],
            membership_expires_at=row["membership_expires_at"],
        )

    def find_original_submission(
        self, *, user_id: str, operation: str, raw_key: str
    ) -> StoredSubmission | None:
        table = TaskSubmissionRow.__table__
        with self._session.no_autoflush:
            row = self._session.execute(
                select(
                    table.c.id,
                    table.c.user_id,
                    table.c.operation,
                    table.c.idempotency_key,
                    table.c.request_digest,
                    table.c.task_ids,
                    table.c.status,
                )
                .where(
                    table.c.user_id == user_id,
                    table.c.operation == operation,
                    table.c.idempotency_key == raw_key,
                )
                .with_for_update()
            ).mappings().one_or_none()
        if row is None:
            return None
        return StoredSubmission(
            id=row["id"],
            user_id=row["user_id"],
            operation=row["operation"],
            idempotency_key=row["idempotency_key"],
            request_digest=row["request_digest"],
            task_ids=row["task_ids"],
            status=row["status"],
        )

    def load_original_receipt(
        self, submission: StoredSubmission, *, expected_batch_size: int | None = None
    ) -> SingleAdmissionReceipt | BatchImageReceipt | AdmissionRefusal:
        if not isinstance(submission.task_ids, list):
            return _refusal(409, "receipt_corrupt", "提交记录中的任务集合无效")
        task_ids = submission.task_ids
        if (
            not task_ids
            or any(not isinstance(task_id, str) or not task_id for task_id in task_ids)
            or len(set(task_ids)) != len(task_ids)
        ):
            return _refusal(409, "receipt_corrupt", "提交记录中的任务集合无效")
        if submission.operation in {"image.single", "video.single"} and len(task_ids) != 1:
            return _refusal(409, "receipt_corrupt", "提交记录与单项任务集合不一致")
        if submission.operation == "image.batch":
            if not 1 <= len(task_ids) <= 64:
                return _refusal(409, "receipt_corrupt", "提交记录与批量任务集合不一致")
            if expected_batch_size is not None and len(task_ids) != expected_batch_size:
                return _refusal(409, "receipt_corrupt", "提交记录与批量任务集合不一致")
        elif submission.operation not in {"image.single", "video.single"}:
            return _refusal(409, "receipt_corrupt", "提交记录操作类型无效")

        task_table = AITaskRow.__table__
        with self._session.no_autoflush:
            rows = self._session.execute(
                select(
                    task_table.c.id,
                    task_table.c.user_id,
                    task_table.c.type,
                    task_table.c.status,
                    task_table.c.message_id,
                    task_table.c.request_data,
                ).where(
                    task_table.c.id.in_(task_ids),
                    task_table.c.user_id == submission.user_id,
                )
            ).mappings().all()
        by_id = {row["id"]: row for row in rows}
        if len(by_id) != len(task_ids):
            return _refusal(409, "receipt_corrupt", "提交记录中的任务不存在或不属于当前账号")
        ordered = [by_id[task_id] for task_id in task_ids]

        if submission.operation == "image.batch":
            items: list[BatchImageTaskReceipt] = []
            for row in ordered:
                try:
                    request_data = json.loads(row["request_data"] or "{}")
                except (TypeError, ValueError, json.JSONDecodeError):
                    return _refusal(409, "receipt_corrupt", "批量任务请求快照无效")
                if not isinstance(request_data, dict):
                    return _refusal(409, "receipt_corrupt", "批量任务请求快照无效")
                asset_type = request_data.get("asset_type")
                asset_id = request_data.get("asset_id")
                asset_name = request_data.get("asset_name")
                message_id = row["message_id"]
                if any(not isinstance(value, str) or not value for value in (asset_type, asset_id, asset_name, message_id)):
                    return _refusal(409, "receipt_corrupt", "批量任务回执字段无效")
                items.append(
                    BatchImageTaskReceipt(
                        task_type=asset_type,
                        asset_id=asset_id,
                        asset_name=asset_name,
                        message_id=message_id,
                        task_id=row["id"],
                    )
                )
            return BatchImageReceipt(tasks=tuple(items))

        task = ordered[0]
        if task["status"] not in {"queued", "processing", "cancelling", "reconciling", "completed", "failed"}:
            return _refusal(409, "receipt_corrupt", "单项任务状态无效")
        unit_table = BillingUnitRow.__table__
        with self._session.no_autoflush:
            units = self._session.execute(
                select(
                    unit_table.c.id,
                    unit_table.c.quoted_amount,
                    unit_table.c.billing_status,
                ).where(unit_table.c.task_id == task["id"])
            ).mappings().all()
        if len(units) != 1:
            return _refusal(409, "receipt_corrupt", "单项任务计费记录不存在或不唯一")
        unit = units[0]
        if (
            type(unit["quoted_amount"]) is not int
            or unit["quoted_amount"] < 0
            or unit["billing_status"] not in {"unbilled", "charged", "refunded", "no_charge"}
        ):
            return _refusal(409, "receipt_corrupt", "单项任务计费记录无效")
        return SingleAdmissionReceipt(
            submission_id=submission.id,
            operation=submission.operation,
            task_id=task["id"],
            message_id=task["message_id"] or None,
            status=task["status"],
            quoted_amount=unit["quoted_amount"],
            billing_status=unit["billing_status"],
        )

    def discover_resources(
        self,
        *,
        chapter_ids: Sequence[str],
        asset_keys: Sequence[tuple[str, str]],
    ) -> ResourceDiscovery:
        discovered_chapters: dict[str, Mapping[str, object] | None] = {}
        discovered_assets: dict[tuple[str, str], Mapping[str, object] | None] = {}
        for chapter_id in sorted(set(chapter_ids)):
            discovered_chapters[chapter_id] = None
        with self._session.no_autoflush:
            if discovered_chapters:
                rows = self._session.execute(
                    select(chapters.c.id, chapters.c.series_id).where(
                        chapters.c.id.in_(tuple(discovered_chapters))
                    )
                ).mappings().all()
                for row in rows:
                    discovered_chapters[row["id"]] = dict(row)
            grouped: dict[str, list[str]] = {}
            for asset_type, asset_id in asset_keys:
                if asset_type not in ASSET_TABLES:
                    continue
                grouped.setdefault(asset_type, []).append(asset_id)
                discovered_assets[(asset_type, asset_id)] = None
            source_chapter_ids: set[str] = set()
            for asset_type in sorted(grouped):
                table = ASSET_TABLES[asset_type]
                columns = [table.c.id, table.c.series_id]
                if asset_type == "storyboard":
                    columns.append(table.c.chapter_id)
                rows = self._session.execute(
                    select(*columns).where(table.c.id.in_(tuple(sorted(set(grouped[asset_type])))))
                ).mappings().all()
                for row in rows:
                    discovered_assets[(asset_type, row["id"])] = dict(row)
                    if asset_type == "storyboard" and isinstance(row["chapter_id"], str):
                        source_chapter_ids.add(row["chapter_id"])
            additional_chapter_ids = source_chapter_ids - set(discovered_chapters)
            for chapter_id in additional_chapter_ids:
                discovered_chapters[chapter_id] = None
            if additional_chapter_ids:
                rows = self._session.execute(
                    select(chapters.c.id, chapters.c.series_id).where(
                        chapters.c.id.in_(tuple(sorted(additional_chapter_ids)))
                    )
                ).mappings().all()
                for row in rows:
                    discovered_chapters[row["id"]] = dict(row)
        return ResourceDiscovery(
            requested_chapter_ids=tuple(sorted(set(chapter_ids))),
            chapter_rows=discovered_chapters,
            asset_rows=discovered_assets,
        )

    def lock_resources(
        self, discovery: ResourceDiscovery, *, actor_id: str
    ) -> LockedResources | AdmissionRefusal:
        if any(discovery.chapter_rows.get(chapter_id) is None for chapter_id in discovery.requested_chapter_ids):
            return _refusal(404, "chapter_not_found", "章节不存在")
        if any(row is None for row in discovery.asset_rows.values()):
            return _refusal(422, "asset_not_found", "素材不存在或不可用")
        series_ids = discovery.series_ids
        if not series_ids:
            return _refusal(404, "series_not_found", "剧集不存在")
        with self._session.no_autoflush:
            series_rows = self._session.execute(
                select(series)
                .where(series.c.id.in_(series_ids))
                .order_by(series.c.id)
                .with_for_update(key_share=True, of=series)
            ).mappings().all()
        locked_series = {row["id"]: dict(row) for row in series_rows}
        if len(locked_series) != len(series_ids):
            return _refusal(409, "resource_changed", "目标剧集在准入准备期间发生变化")
        for chapter_id, discovered in discovery.chapter_rows.items():
            if discovered is not None:
                current = locked_series.get(discovered["series_id"])
                if current is None:
                    return _refusal(409, "resource_changed", "目标剧集在准入准备期间发生变化")

        team_ids = tuple(
            sorted({row["team_id"] for row in locked_series.values() if row["team_id"]})
        )
        locked_members: dict[str, Mapping[str, object]] = {}
        if team_ids:
            with self._session.no_autoflush:
                rows = self._session.execute(
                    select(team_members)
                    .where(
                        team_members.c.team_id.in_(team_ids),
                        team_members.c.user_id == actor_id,
                    )
                    .order_by(team_members.c.team_id, team_members.c.id)
                    .with_for_update()
                ).mappings().all()
            for row in rows:
                if row["team_id"] in locked_members:
                    return _refusal(409, "membership_ambiguous", "团队成员权限记录存在歧义")
                locked_members[row["team_id"]] = dict(row)
        for row in locked_series.values():
            member = locked_members.get(row["team_id"]) if row["team_id"] else None
            if row["claimed_by"] and row["claimed_by"] != actor_id:
                allowed = False
                if member is not None:
                    allowed = can_enter_claimed_series(
                        TeamMembership(role=member["role"], permissions=member["permissions"])
                    )
                if not allowed:
                    return _refusal(403, "claimed_series_denied", "该剧集已由其他成员认领，暂不可使用")
            if row["user_id"] != actor_id and member is None:
                return _refusal(403, "series_access_denied", "无权访问该剧集")

        all_chapter_ids = discovery.chapter_ids
        with self._session.no_autoflush:
            chapter_rows = self._session.execute(
                select(chapters)
                .where(chapters.c.id.in_(all_chapter_ids))
                .order_by(chapters.c.id)
                .with_for_update()
            ).mappings().all()
        locked_chapters = {row["id"]: dict(row) for row in chapter_rows}
        if len(locked_chapters) != len(all_chapter_ids):
            return _refusal(409, "resource_changed", "章节在准入准备期间发生变化")
        for chapter_id, discovered in discovery.chapter_rows.items():
            current = locked_chapters.get(chapter_id)
            if current is None or discovered is None or current["series_id"] != discovered["series_id"]:
                return _refusal(409, "resource_changed", "章节所属剧集在准入准备期间发生变化")

        locked_assets: dict[tuple[str, str], Mapping[str, object]] = {}
        for asset_key in sorted(discovery.asset_rows):
            asset_type, asset_id = asset_key
            table = ASSET_TABLES[asset_type]
            with self._session.no_autoflush:
                row = self._session.execute(
                    select(table)
                    .where(table.c.id == asset_id)
                    .with_for_update()
                ).mappings().one_or_none()
            discovered = discovery.asset_rows[asset_key]
            if row is None or discovered is None:
                return _refusal(409, "resource_changed", "素材在准入准备期间发生变化")
            if row["series_id"] != discovered["series_id"]:
                return _refusal(409, "resource_changed", "素材所属剧集在准入准备期间发生变化")
            if asset_type == "storyboard" and row["chapter_id"] != discovered["chapter_id"]:
                return _refusal(409, "resource_changed", "故事板素材所属章节在准入准备期间发生变化")
            locked_assets[asset_key] = dict(row)
        return LockedResources(
            series_rows=locked_series,
            chapter_rows=locked_chapters,
            asset_rows=locked_assets,
            member_rows=locked_members,
        )

    def lock_catalog(
        self,
        *,
        user_id: str,
        model_ids: Sequence[str],
        prompt_ids: Sequence[str],
        include_auto_text: bool,
    ) -> LockedCatalogReader:
        model_ids = tuple(sorted(set(model_ids)))
        prompt_ids = tuple(sorted(set(prompt_ids)))
        system_rows: list[Mapping[str, object]] = []
        owned_rows: list[Mapping[str, object]] = []
        order_rows: list[Mapping[str, object]] = []
        system_prompt_rows: list[Mapping[str, object]] = []
        owned_prompt_rows: list[Mapping[str, object]] = []

        system_credential = _credential_presence(system_models)
        owned_credential = _credential_presence(model_configs)
        if model_ids or include_auto_text:
            system_condition = _id_or_text(system_models, model_ids, include_auto_text)
            with self._session.no_autoflush:
                system_rows = self._session.execute(
                    select(
                        system_models.c.id,
                        system_models.c.category,
                        system_models.c.name,
                        system_models.c.model,
                        system_models.c.provider,
                        system_models.c.cost_per_call,
                        system_models.c.params,
                        system_models.c.is_active,
                        system_models.c.sort_order,
                        system_credential,
                    )
                    .where(system_condition)
                    .order_by(system_models.c.id)
                    .with_for_update()
                ).mappings().all()
            owned_condition = _owned_id_or_text(model_configs, user_id, model_ids, include_auto_text)
            with self._session.no_autoflush:
                owned_rows = self._session.execute(
                    select(
                        model_configs.c.id,
                        model_configs.c.user_id,
                        model_configs.c.category,
                        model_configs.c.name,
                        model_configs.c.model,
                        model_configs.c.provider,
                        model_configs.c.params,
                        model_configs.c.sort_order,
                        owned_credential,
                    )
                    .where(owned_condition)
                    .order_by(model_configs.c.id)
                    .with_for_update()
                ).mappings().all()
        if include_auto_text:
            with self._session.no_autoflush:
                order_rows = self._session.execute(
                    select(
                        user_model_orders.c.id,
                        user_model_orders.c.user_id,
                        user_model_orders.c.model_id,
                        user_model_orders.c.category,
                        user_model_orders.c.sort_order,
                    )
                    .where(
                        user_model_orders.c.user_id == user_id,
                        user_model_orders.c.category == "text",
                    )
                    .order_by(user_model_orders.c.id)
                    .with_for_update()
                ).mappings().all()
        if prompt_ids:
            with self._session.no_autoflush:
                system_prompt_rows = self._session.execute(
                    select(
                        system_prompts.c.id,
                        system_prompts.c.category,
                        system_prompts.c.name,
                        system_prompts.c.system_prompt,
                        system_prompts.c.user_prompt,
                        system_prompts.c.is_active,
                    )
                    .where(system_prompts.c.id.in_(prompt_ids))
                    .order_by(system_prompts.c.id)
                    .with_for_update()
                ).mappings().all()
            with self._session.no_autoflush:
                owned_prompt_rows = self._session.execute(
                    select(
                        prompt_configs.c.id,
                        prompt_configs.c.user_id,
                        prompt_configs.c.category,
                        prompt_configs.c.name,
                        prompt_configs.c.system_prompt,
                        prompt_configs.c.user_prompt,
                    )
                    .where(
                        prompt_configs.c.id.in_(prompt_ids),
                        prompt_configs.c.user_id == user_id,
                    )
                    .order_by(prompt_configs.c.id)
                    .with_for_update()
                ).mappings().all()

        system_models_values = tuple(_system_model(row) for row in system_rows)
        owned_models_values = tuple(_owned_model(row) for row in owned_rows)
        system_prompts_values = tuple(_system_prompt(row) for row in system_prompt_rows)
        owned_prompts_values = tuple(_owned_prompt(row) for row in owned_prompt_rows)
        model_orders = tuple(
            UserModelOrder(model_id=row["model_id"], sort_order=row["sort_order"])
            for row in order_rows
        )
        return LockedCatalogReader(
            user_id=user_id,
            system_models=system_models_values,
            owned_models=owned_models_values,
            system_prompts=system_prompts_values,
            owned_prompts=owned_prompts_values,
            model_orders=model_orders,
        )

    def lock_wallet_balance(self, user_id: str) -> int:
        with self._session.no_autoflush:
            row = self._session.execute(
                select(user_credits.c.user_id, user_credits.c.credits)
                .where(user_credits.c.user_id == user_id)
                .with_for_update()
            ).mappings().one_or_none()
        if row is None:
            return 0
        credits = row["credits"]
        if type(credits) is not int or credits < 0:
            raise ValueError("wallet balance is invalid")
        return credits

    def has_running_optimize(self, *, user_id: str, chapter_id: str) -> bool | AdmissionRefusal:
        task_table = AITaskRow.__table__
        with self._session.no_autoflush:
            rows = self._session.execute(
                select(task_table.c.request_data)
                .where(
                    task_table.c.user_id == user_id,
                    task_table.c.type == "batch-optimize",
                    task_table.c.status.in_(("queued", "processing", "cancelling")),
                )
            ).mappings().all()
        for row in rows:
            try:
                payload = json.loads(row["request_data"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                return _refusal(409, "optimize_state_unknown", "现有优化任务状态无法安全核实")
            if not isinstance(payload, dict):
                return _refusal(409, "optimize_state_unknown", "现有优化任务状态无法安全核实")
            if payload.get("chapter_id") == chapter_id:
                return True
        return False


def _credential_presence(table: Any) -> Any:
    present = and_(table.c.api_key.is_not(None), func.length(func.trim(table.c.api_key)) > 0)
    return case((present, True), else_=False).label("credential_configured")


def _id_or_text(table: Any, model_ids: Sequence[str], include_auto_text: bool) -> Any:
    conditions = []
    if model_ids:
        conditions.append(table.c.id.in_(model_ids))
    if include_auto_text:
        conditions.append(table.c.category == "text")
    return conditions[0] if len(conditions) == 1 else or_(*conditions)


def _owned_id_or_text(
    table: Any, user_id: str, model_ids: Sequence[str], include_auto_text: bool
) -> Any:
    conditions = []
    if model_ids:
        conditions.append(and_(table.c.id.in_(model_ids), table.c.user_id == user_id))
    if include_auto_text:
        conditions.append(and_(table.c.user_id == user_id, table.c.category == "text"))
    return conditions[0] if len(conditions) == 1 else or_(*conditions)


def _system_model(row: Mapping[str, object]) -> CatalogModel:
    return CatalogModel(
        id=row["id"],
        category=row["category"],
        display_name=row["name"],
        wire_model=row["model"],
        owner_id=None,
        base_cost=row["cost_per_call"],
        params=row["params"],
        provider=row["provider"],
        credential_configured=bool(row["credential_configured"]),
        active=bool(row["is_active"]),
        sort_order=row["sort_order"],
    )


def _owned_model(row: Mapping[str, object]) -> CatalogModel:
    return CatalogModel(
        id=row["id"],
        category=row["category"],
        display_name=row["name"],
        wire_model=row["model"],
        owner_id=row["user_id"],
        base_cost=0,
        params=row["params"],
        provider=row["provider"],
        credential_configured=bool(row["credential_configured"]),
        active=True,
        sort_order=row["sort_order"],
    )


def _system_prompt(row: Mapping[str, object]) -> CatalogPrompt:
    return CatalogPrompt(
        id=row["id"],
        category=row["category"],
        display_name=row["name"],
        owner_id=None,
        system_prompt=row["system_prompt"],
        user_prompt=row["user_prompt"],
        active=bool(row["is_active"]),
    )


def _owned_prompt(row: Mapping[str, object]) -> CatalogPrompt:
    return CatalogPrompt(
        id=row["id"],
        category=row["category"],
        display_name=row["name"],
        owner_id=row["user_id"],
        system_prompt=row["system_prompt"],
        user_prompt=row["user_prompt"],
    )


def _refusal(status_code: int, code: str, message: str) -> AdmissionRefusal:
    return AdmissionRefusal(status_code=status_code, code=code, message=message)
