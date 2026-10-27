"""Prepare generation requests from fresh locked rows without staging effects."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .admission_types import (
    AdmissionContext,
    AdmissionContextError,
    AdmissionOperation,
    AdmissionPreparationFacts,
    AdmissionRefusal,
    AdmissionReplay,
    AdmissionResult,
    LockedTargetFacts,
    PreparedItemFacts,
    prompt_facts,
)
from .application import compose_batch_image_prompt, resolve_model, resolve_prompt, select_text_model
from .domain import CatalogError, ModelCategory, ModelSource, PromptOperation, PromptSelection
from haoai_backend.generation_task_concurrency import (
    count_user_in_flight_tasks,
    session_uses_fresh_postgres_statements,
    task_shape_for_operation,
)
from .persistence import (
    AccountFacts,
    AdmissionPersistence,
    LockedCatalogReader,
    LockedResources,
    ResourceDiscovery,
)


@dataclass(frozen=True, slots=True)
class AdmissionPolicy:
    """Explicit server policy values; environment variables are never read here."""

    max_concurrent_tasks: int = 0

    def __post_init__(self) -> None:
        if type(self.max_concurrent_tasks) is not int:
            raise ValueError("max_concurrent_tasks must be an integer")


@dataclass(frozen=True, slots=True)
class _TargetSpec:
    kind: str
    chapter_id: str | None = None
    asset_type: str | None = None
    asset_id: str | None = None
    stable_storyboard: bool = False
    frame_index: int | None = None
    identity_mode: str | None = None
    expected_chapter_digest: str | None = None


@dataclass(frozen=True, slots=True)
class _ItemSpec:
    index: int
    category: ModelCategory
    model_id: str | None
    size: object = None
    duration: object = None
    resolution: object = None
    prompt_id: str | None = None
    prompt_operation: PromptOperation | None = None
    prompt_text: str | None = None
    reference_images: tuple[str, ...] = ()
    reference_audios: tuple[str, ...] = ()
    reference_videos: tuple[str, ...] = ()
    target: _TargetSpec = _TargetSpec("none")


@dataclass(frozen=True, slots=True)
class _RequestPlan:
    operation: AdmissionOperation
    items: tuple[_ItemSpec, ...]
    chapter_ids: tuple[str, ...]
    asset_keys: tuple[tuple[str, str], ...]
    prompt_ids: tuple[str, ...]
    include_auto_text: bool
    phase: str
    balance_mode: str
    optimize_chapter_id: str | None = None


class _PreparationFailure(Exception):
    def __init__(self, refusal: AdmissionRefusal) -> None:
        self.refusal = refusal
        super().__init__(refusal.message)


def prepare_admission(
    session: Session,
    context: AdmissionContext,
    *,
    now: datetime | None = None,
    policy: AdmissionPolicy = AdmissionPolicy(),
) -> AdmissionResult:
    """Return a replay, locked preparation facts, or a safe refusal.

    The supplied Session remains caller-owned. This function performs Core
    SELECTs only and never commits, rolls back, closes, stages, or calls a provider.
    """

    try:
        context.validate()
        plan = _request_plan(context)
    except AdmissionContextError as exc:
        return _refusal(422, "invalid_context", str(exc))
    except _PreparationFailure as exc:
        return exc.refusal
    store = AdmissionPersistence(session)
    try:
        with session.no_autoflush:
            account = store.lock_account(context.actor.user_id)
            if account is None or account.user_id != context.fingerprint.actor_id:
                return _refusal(401, "account_unavailable", "无法验证当前账号")

            if context.raw_key is not None:
                submission = store.find_original_submission(
                    user_id=account.user_id,
                    operation=context.operation,
                    raw_key=context.raw_key.raw,
                )
                if submission is not None:
                    if submission.request_digest != context.fingerprint.ordered_nonsecret_payload_digest:
                        return _refusal(409, "idempotency_conflict", "该幂等键已用于不同请求")
                    expected_size = len(context.canonical_request["assets"]) if context.operation == "image.batch" else None
                    receipt = store.load_original_receipt(
                        submission,
                        expected_batch_size=expected_size,
                    )
                    if isinstance(receipt, AdmissionRefusal):
                        return receipt
                    return AdmissionReplay(fingerprint=context.fingerprint, receipt=receipt)

            task_shape = None
            if policy.max_concurrent_tasks > 0:
                task_shape = task_shape_for_operation(context.operation, len(plan.items))
                if task_shape is None:
                    return _refusal(
                        503,
                        "concurrency_policy_not_bound",
                        "用户并发限额的任务类型绑定尚未完成",
                    )

            if not _has_active_membership(account, now=now):
                return _refusal(403, "membership_required", "需要有效会员才能提交新任务")

            if plan.chapter_ids or plan.asset_keys:
                discovery = store.discover_resources(
                    chapter_ids=plan.chapter_ids,
                    asset_keys=plan.asset_keys,
                )
                resources = store.lock_resources(discovery, actor_id=account.user_id)
                if isinstance(resources, AdmissionRefusal):
                    return resources
            else:
                discovery = ResourceDiscovery((), {}, {})
                resources = LockedResources({}, {}, {}, {})

            target_facts = _locked_target_facts(plan, discovery, resources)
            if isinstance(target_facts, AdmissionRefusal):
                return target_facts

            if plan.optimize_chapter_id is not None:
                running = store.has_running_optimize(
                    user_id=account.user_id,
                    chapter_id=plan.optimize_chapter_id,
                )
                if isinstance(running, AdmissionRefusal):
                    return running
                if running:
                    return _refusal(
                        409,
                        "optimize_already_running",
                        "该章节已有批量优化任务正在运行",
                    )

            if task_shape is not None:
                if not session_uses_fresh_postgres_statements(session):
                    return _refusal(
                        503,
                        "concurrency_isolation_unavailable",
                        "数据库隔离级别无法安全确认用户并发限额",
                    )
                current = count_user_in_flight_tasks(session, account.user_id)
                if current + task_shape.task_count > policy.max_concurrent_tasks:
                    return _refusal(
                        429,
                        "concurrency_limit_reached",
                        f"您同时在处理的任务已达上限（{policy.max_concurrent_tasks}），请等待部分完成后再提交",
                    )

            model_ids = tuple(item.model_id for item in plan.items if item.model_id)
            try:
                catalog = store.lock_catalog(
                    user_id=account.user_id,
                    model_ids=model_ids,
                    prompt_ids=plan.prompt_ids,
                    include_auto_text=plan.include_auto_text,
                )
            except (TypeError, ValueError):
                return _refusal(409, "catalog_state_invalid", "当前模型目录配置无法安全校验")
            prepared_items: list[PreparedItemFacts] = []
            for item, target in zip(plan.items, target_facts, strict=True):
                try:
                    selection = _select_model(catalog, account.user_id, item)
                    details = catalog.model_details(selection)
                    if details is None:
                        return _refusal(409, "catalog_changed", "所选模型配置在锁定后无法确认")
                    if not details.credential_configured:
                        return _refusal(422, "model_credential_missing", "所选模型缺少有效凭据")
                    _validate_reference_policy(
                        item,
                        details.params,
                        provider=details.provider,
                    )
                    prompt = _select_prompt(catalog, account.user_id, item)
                    if prompt is not None and item.prompt_operation in {
                        PromptOperation.REVIEW,
                        PromptOperation.OPTIMIZE,
                    } and prompt.category != "ai-review":
                        return _refusal(422, "prompt_category_mismatch", "所选提示词类别不适用于此操作")
                    prompt_digest = _effective_prompt_digest(item, prompt)
                except CatalogError as exc:
                    return _refusal(422, "catalog_selection_failed", str(exc))
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    return _refusal(422, "catalog_configuration_invalid", str(exc))

                model = selection
                prepared_items.append(
                    PreparedItemFacts(
                        index=item.index,
                        model=model,
                        credential_configured=details.credential_configured,
                        target=target,
                        prompt=prompt_facts(prompt),
                        quote_amount=model.quote.amount,
                        effective_parameters=model.quote.effective_parameters,
                        reference_counts=(
                            ("image", len(item.reference_images)),
                            ("audio", len(item.reference_audios)),
                            ("video", len(item.reference_videos)),
                        ),
                        prompt_digest=prompt_digest,
                    )
                )

            quoted_total = sum(item.quote_amount for item in prepared_items)
            required_balance = _required_balance(plan, prepared_items)
            try:
                available = store.lock_wallet_balance(account.user_id)
            except ValueError:
                return _refusal(409, "wallet_state_invalid", "当前积分余额记录无法安全校验")
            if required_balance > available:
                return _refusal(402, "insufficient_credits", "积分不足，无法满足当前准入余额检查")
            return AdmissionPreparationFacts(
                operation=context.operation,
                fingerprint=context.fingerprint,
                phase=plan.phase,
                items=tuple(prepared_items),
                quoted_total=quoted_total,
                required_balance=required_balance,
                available_balance=available,
                reserved_amount=0,
                credential_binding_status="binding_unverified",
                executable=False,
            )
    except SQLAlchemyError:
        return _refusal(503, "schema_not_ready", "准入所需的现有数据结构不可用")


def _has_active_membership(account: AccountFacts, *, now: datetime | None) -> bool:
    if account.is_superuser:
        return True
    if account.membership_type != "premium":
        return False
    expires_at = account.membership_expires_at
    if expires_at is None:
        return True
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    else:
        current = current.astimezone(timezone.utc)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    else:
        expires_at = expires_at.astimezone(timezone.utc)
    return current <= expires_at


def _request_plan(context: AdmissionContext) -> _RequestPlan:
    operation = AdmissionOperation(context.operation)
    request = context.canonical_request
    items: list[_ItemSpec] = []
    chapter_ids: set[str] = set()
    asset_keys: set[tuple[str, str]] = set()
    prompt_ids: set[str] = set()
    include_auto_text = False
    optimize_chapter_id = None

    if operation in {AdmissionOperation.IMAGE_SINGLE, AdmissionOperation.VIDEO_SINGLE}:
        category = ModelCategory.IMAGE if operation is AdmissionOperation.IMAGE_SINGLE else ModelCategory.VIDEO
        target = _single_target(request["target"], operation)
        if target.chapter_id:
            chapter_ids.add(target.chapter_id)
        if target.asset_type and target.asset_id:
            asset_keys.add((target.asset_type, target.asset_id))
        prompt = request["prompt"]
        items.append(
            _ItemSpec(
                index=0,
                category=category,
                model_id=request["model_id"],
                size=request["size"],
                duration=request.get("duration"),
                resolution=request.get("resolution"),
                prompt_text=prompt,
                reference_images=tuple(request["reference_images"]),
                reference_audios=tuple(request.get("reference_audios", ())),
                reference_videos=tuple(request.get("reference_videos", ())),
                target=target,
            )
        )
        phase = "single_admission"
        balance_mode = "total"
    elif operation is AdmissionOperation.IMAGE_BATCH:
        chapter_id = request["chapter_id"]
        chapter_ids.add(chapter_id)
        for index, raw_item in enumerate(request["assets"]):
            item = raw_item
            asset_type = item["type"]
            asset_id = item["id"]
            if asset_type not in {"character", "scene", "prop", "storyboard"}:
                raise _PreparationFailure(_refusal(422, "asset_type_unsupported", "素材类型不受支持"))
            if not item["model_id"]:
                raise _PreparationFailure(_refusal(422, "model_required", "批量素材未指定模型"))
            description = item["description"] or item["name"]
            if not description:
                raise _PreparationFailure(_refusal(422, "prompt_required", "批量素材缺少提示词"))
            asset_keys.add((asset_type, asset_id))
            prompt_id = item["prompt_id"] or None
            if prompt_id:
                prompt_ids.add(prompt_id)
            target = _TargetSpec(
                kind="asset_batch",
                chapter_id=chapter_id,
                asset_type=asset_type,
                asset_id=asset_id,
            )
            items.append(
                _ItemSpec(
                    index=index,
                    category=ModelCategory.IMAGE,
                    model_id=item["model_id"],
                    size=item["size"],
                    prompt_id=prompt_id,
                    prompt_operation=PromptOperation.IMAGE_BATCH,
                    prompt_text=description,
                    reference_images=tuple(item["reference_images"]),
                    target=target,
                )
            )
        phase = "batch_admission"
        balance_mode = "total"
    elif operation is AdmissionOperation.FRAME_REVIEW:
        chapter_id = request["chapter_id"]
        chapter_ids.add(chapter_id)
        model_id = request["model_id"] or None
        include_auto_text = model_id is None
        prompt_id = request["prompt_id"] or None
        if prompt_id:
            prompt_ids.add(prompt_id)
        frame_index = request["frame_index"]
        items.append(
            _ItemSpec(
                index=0,
                category=ModelCategory.TEXT,
                model_id=model_id,
                prompt_id=prompt_id,
                prompt_operation=PromptOperation.REVIEW,
                target=_TargetSpec("frame_review", chapter_id=chapter_id, frame_index=frame_index),
            )
        )
        phase = "post_apply"
        balance_mode = "single"
    elif operation is AdmissionOperation.FRAME_OPTIMIZE:
        chapter_id = request["chapter_id"]
        chapter_ids.add(chapter_id)
        optimize_chapter_id = chapter_id
        prompt_id = request["prompt_id"] or None
        if prompt_id:
            prompt_ids.add(prompt_id)
        for index, frame in enumerate(request["frames"]):
            items.append(
                _ItemSpec(
                    index=index,
                    category=ModelCategory.TEXT,
                    model_id=request["model_id"],
                    prompt_id=prompt_id,
                    prompt_operation=PromptOperation.OPTIMIZE,
                    target=_TargetSpec(
                        "frame_optimize",
                        chapter_id=chapter_id,
                        frame_index=frame["index"],
                    ),
                )
            )
        phase = "pre_apply"
        balance_mode = "one_frame"
    else:
        model_id = request.get("model_id") or None
        include_auto_text = model_id is None
        prompt_id = request.get("prompt_id")
        items.append(
            _ItemSpec(
                index=0,
                category=ModelCategory.TEXT,
                model_id=model_id,
                prompt_id=prompt_id if isinstance(prompt_id, str) and prompt_id else None,
                prompt_operation=PromptOperation.TEXT_STREAM,
                target=_TargetSpec("none"),
            )
        )
        phase = "stream_pre_task"
        balance_mode = "single"

    return _RequestPlan(
        operation=operation,
        items=tuple(items),
        chapter_ids=tuple(sorted(chapter_ids)),
        asset_keys=tuple(sorted(asset_keys)),
        prompt_ids=tuple(sorted(prompt_ids)),
        include_auto_text=include_auto_text,
        phase=phase,
        balance_mode=balance_mode,
        optimize_chapter_id=optimize_chapter_id,
    )


def _single_target(
    raw: object,
    operation: AdmissionOperation,
) -> _TargetSpec:
    if not isinstance(raw, Mapping):
        raise _PreparationFailure(_refusal(422, "target_invalid", "目标格式无效"))
    kind = raw.get("kind")
    if kind == "standalone":
        return _TargetSpec("standalone")
    if kind == "frame_message":
        chapter_id = raw["chapter_id"]
        asset_id = raw["storyboard_asset_id"]
        return _TargetSpec("frame_message", chapter_id, "storyboard", asset_id, True)
    if kind == "asset_message":
        return _TargetSpec(
            "asset_message",
            raw["chapter_id"],
            raw["asset_type"],
            raw["asset_id"],
        )
    if kind == "canvas_apply":
        if operation is AdmissionOperation.VIDEO_SINGLE:
            raise _PreparationFailure(_refusal(422, "video_canvas_unsupported", "画布目标仅支持单图操作"))
        destination = raw["destination"]
        if destination["kind"] == "frame_preview":
            if destination.get("identity_mode") == "strict-no-id-v1":
                return _TargetSpec(
                    "canvas_frame_preview",
                    chapter_id=destination["chapter_id"],
                    frame_index=destination["frame_index"],
                    identity_mode="strict-no-id-v1",
                    expected_chapter_digest=destination["expected_chapter_digest"],
                )
            return _TargetSpec(
                "canvas_frame_preview",
                destination["chapter_id"],
                "storyboard",
                destination["storyboard_asset_id"],
                True,
            )
        return _TargetSpec(
            "canvas_asset_image",
            destination["chapter_id"],
            destination["asset_type"],
            destination["asset_id"],
        )
    raise _PreparationFailure(_refusal(422, "target_unsupported", "目标类型不受支持"))


def _select_model(
    catalog: LockedCatalogReader,
    user_id: str,
    item: _ItemSpec,
):
    if item.category is ModelCategory.TEXT:
        return select_text_model(catalog, user_id=user_id, model_id=item.model_id)
    return resolve_model(
        catalog,
        user_id=user_id,
        model_id=item.model_id or "",
        category=item.category,
        size=item.size,
        duration=item.duration,
        resolution=item.resolution,
    )


def _select_prompt(
    catalog: LockedCatalogReader,
    user_id: str,
    item: _ItemSpec,
) -> PromptSelection | None:
    if item.prompt_operation is None:
        return None
    return resolve_prompt(
        catalog,
        user_id=user_id,
        operation=item.prompt_operation,
        prompt_id=item.prompt_id,
    )


def _effective_prompt_digest(item: _ItemSpec, selection: PromptSelection | None) -> str | None:
    if item.prompt_operation is PromptOperation.IMAGE_BATCH:
        return _digest_text(compose_batch_image_prompt(selection, description=item.prompt_text or ""))
    if item.prompt_text is not None:
        return _digest_text(item.prompt_text)
    return None


def _validate_reference_policy(
    item: _ItemSpec,
    params: object,
    *,
    provider: str | None = None,
) -> None:
    references = {
        "reference_images": item.reference_images,
        "reference_audios": item.reference_audios,
        "reference_videos": item.reference_videos,
    }
    if item.category is ModelCategory.IMAGE and (item.reference_audios or item.reference_videos):
        raise CatalogError("image operation does not accept audio or video references")
    if item.category is ModelCategory.TEXT and any(references.values()):
        raise CatalogError("text operation does not accept media references")
    if item.category is ModelCategory.TEXT:
        return
    if item.category is ModelCategory.VIDEO:
        route = provider.lower() if isinstance(provider, str) else None
        if route == "aistarslab":
            if (
                len(item.reference_images) > 9
                or len(item.reference_audios) > 5
                or len(item.reference_videos) > 5
            ):
                raise CatalogError("AIStarsLab reference count exceeds its provider limits")
        elif route in {"xai", "manxiaobai"} and (
            item.reference_audios or item.reference_videos
        ):
            raise CatalogError(
                "selected video provider does not accept audio or video references"
            )
    parsed = params
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CatalogError("model params must be valid JSON") from exc
    if not isinstance(parsed, Mapping):
        parsed = {}
    config = parsed.get("max_reference_images")
    if config is None:
        enabled, maximum = True, 9
    elif not isinstance(config, Mapping):
        raise CatalogError("model max_reference_images configuration is invalid")
    else:
        enabled = config.get("enabled", True)
        options = config.get("options")
        if type(enabled) is not bool:
            raise CatalogError("model max_reference_images.enabled is invalid")
        maximum = config.get(
            "default",
            max(options) if isinstance(options, (list, tuple)) and options else 9,
        )
        if type(maximum) is not int or not 1 <= maximum <= 64:
            raise CatalogError("model max_reference_images.default is invalid")
        if options is not None and (
            not isinstance(options, (list, tuple))
            or not options
            or any(type(value) is not int or not 1 <= value <= 64 for value in options)
            or maximum not in options
        ):
            raise CatalogError("model max_reference_images.options is invalid")
    if not enabled and item.reference_images:
        raise CatalogError("model does not allow reference images")
    if len(item.reference_images) > maximum:
        raise CatalogError(f"reference image count exceeds model limit {maximum}")


def _locked_target_facts(
    plan: _RequestPlan,
    discovery: ResourceDiscovery,
    resources: LockedResources,
) -> tuple[LockedTargetFacts, ...] | AdmissionRefusal:
    facts: list[LockedTargetFacts] = []
    for item in plan.items:
        target = item.target
        if target.kind in {"none", "standalone"}:
            facts.append(LockedTargetFacts(kind=target.kind))
            continue
        chapter = resources.chapter_rows.get(target.chapter_id or "")
        if chapter is None:
            return _refusal(404, "chapter_not_found", "章节不存在")
        series_id = chapter["series_id"]
        chapter_id = chapter["id"]
        chapter_digest = _digest_text(chapter["content"] or "")
        asset = None
        asset_digest = None
        canonical_chapter_digest = None
        if target.asset_type and target.asset_id:
            asset = resources.asset_rows.get((target.asset_type, target.asset_id))
            if asset is None:
                return _refusal(422, "asset_not_found", "素材不存在或不可用")
            if asset["series_id"] != series_id:
                return _refusal(422, "asset_series_mismatch", "素材与聊天章节不属于同一剧集")
            if target.kind == "asset_batch" and target.asset_type == "storyboard" and asset["chapter_id"] != chapter_id:
                return _refusal(422, "storyboard_chapter_mismatch", "故事板素材与章节不匹配")
            if target.stable_storyboard and (
                target.asset_type != "storyboard" or asset["chapter_id"] != chapter_id
            ):
                return _refusal(422, "storyboard_chapter_mismatch", "稳定分镜与章节不匹配")
            if target.asset_type == "storyboard":
                source_chapter = resources.chapter_rows.get(asset["chapter_id"])
                if source_chapter is None or source_chapter["series_id"] != series_id:
                    return _refusal(422, "storyboard_source_mismatch", "故事板素材关联章节不一致")
            asset_digest = _asset_digest(asset)

        frame_index = target.frame_index
        if target.stable_storyboard:
            frames = _chapter_frames(chapter["content"])
            matches = [
                index
                for index, frame in enumerate(frames)
                if isinstance(frame, Mapping)
                and isinstance(frame.get("storyboard"), (list, tuple))
                and target.asset_id in frame["storyboard"]
            ]
            if len(matches) != 1:
                return _refusal(422, "stable_storyboard_ambiguous", "分镜目标缺失或存在歧义")
            frame_index = matches[0]
        elif target.identity_mode == "strict-no-id-v1":
            canonical_frames = _parse_canonical_chapter_frames(chapter["content"])
            if canonical_frames is None:
                return _refusal(
                    422,
                    "canvas_chapter_invalid",
                    "无稳定ID目标要求有效的完整章节JSON",
                )
            frames, canonical_chapter_digest = canonical_frames
            if canonical_chapter_digest != target.expected_chapter_digest:
                return _refusal(
                    409,
                    "canvas_chapter_changed",
                    "章节内容与画布目标断言不一致",
                )
            frame_index = target.frame_index
            if (
                type(frame_index) is not int
                or frame_index < 0
                or frame_index >= len(frames)
                or not _frame_has_no_storyboard_identity(frames[frame_index])
            ):
                return _refusal(
                    409,
                    "canvas_frame_identity_changed",
                    "指定分镜不再是无稳定ID目标",
                )
        elif frame_index is not None:
            frames = _chapter_frames(chapter["content"])
            if frame_index < 0 or frame_index >= len(frames):
                return _refusal(422, "frame_index_invalid", "分镜索引超出当前章节范围")

        facts.append(
            LockedTargetFacts(
                kind=target.kind,
                series_id=series_id,
                chapter_id=chapter_id,
                asset_type=target.asset_type,
                asset_id=target.asset_id,
                frame_index=frame_index,
                chapter_digest=chapter_digest,
                asset_digest=asset_digest,
                identity_mode=target.identity_mode,
                expected_chapter_digest=target.expected_chapter_digest,
                canonical_chapter_digest=canonical_chapter_digest,
            )
        )
    return tuple(facts)


def _chapter_frames(raw_content: object) -> list[object]:
    if not isinstance(raw_content, str) or not raw_content:
        return []
    try:
        value = json.loads(raw_content)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


def _parse_canonical_chapter_frames(
    raw_content: object,
) -> tuple[list[Mapping[str, object]], str] | None:
    if not isinstance(raw_content, str) or not raw_content:
        return None
    try:
        frames = json.loads(
            raw_content,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(frames, list) or any(
        not isinstance(frame, Mapping) for frame in frames
    ):
        return None
    try:
        return frames, _digest_json(frames)
    except (TypeError, ValueError, OverflowError):
        return None


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("chapter JSON contains a duplicate object key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"chapter JSON contains a non-finite value: {value}")


def _frame_has_no_storyboard_identity(frame: Mapping[str, object]) -> bool:
    if "storyboard" not in frame:
        return True
    references = frame["storyboard"]
    return isinstance(references, list) and all(
        reference is None or reference == "" for reference in references
    )


def _asset_digest(asset: Mapping[str, object]) -> str:
    name = asset.get("name") or asset.get("title")
    return _digest_json(
        {
            "name": name,
            "description": asset.get("description"),
            "image_url": asset.get("image_url"),
            "updated_at": _datetime_value(asset.get("updated_at")),
        }
    )


def _datetime_value(value: object) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _required_balance(plan: _RequestPlan, items: list[PreparedItemFacts]) -> int:
    if plan.balance_mode == "one_frame":
        return max((item.quote_amount for item in items), default=0)
    return sum(item.quote_amount for item in items)


def _digest_text(value: object) -> str:
    text = value if isinstance(value, str) else str(value)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _digest_json(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _refusal(status_code: int, code: str, message: str) -> AdmissionRefusal:
    return AdmissionRefusal(status_code=status_code, code=code, message=message)
