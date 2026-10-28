"""Pure immutable plans for generation task, unit, step, and message graphs.

Building a plan describes frozen server inputs. It does not authorize work,
stage persistence, resolve credentials, or claim that any effect was committed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields, is_dataclass, replace
from datetime import date, datetime
from enum import Enum
from hashlib import sha256
import json
import math
import re
from types import MappingProxyType

from haoai_backend.generation_contracts.errors import GenerationError
from haoai_backend.generation_contracts.types import (
    ChargePhase,
    EndpointPolicy,
    ExecutionMode,
    FrozenExecutionSnapshot,
    FrozenGrantSnapshot,
    ModelReference,
    OperationPolicy,
    TargetSnapshot,
)
from haoai_backend.generation_runtime.registry import HAOAI_ATTEMPT_GROUP, operation_policy
from haoai_backend.generation_task_concurrency import (
    graph_tasks_match_shape,
    task_shape_for_operation,
)


GraphOperation = str
GraphVersion = str
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MAX_BATCH_ITEMS = 64
_MAX_OPTIMIZE_FRAMES = 30
_BATCH_ASSET_TYPES = frozenset({"character", "scene", "prop", "storyboard"})
_TARGET_KINDS = frozenset(
    {
        "none",
        "stream_message",
        "frame_message",
        "asset_message",
        "canvas_frame_preview",
        "canvas_asset_image",
        "frame_text",
    }
)
_CANVAS_TARGET_KINDS = frozenset({"canvas_frame_preview", "canvas_asset_image"})
_MODEL_CATEGORIES: dict[str, str] = {
    "image.single": "image",
    "video.single": "video",
    "image.batch": "image",
    "frame.review": "text",
    "frame.optimize": "text",
}


@dataclass(frozen=True, slots=True)
class GraphItemInput:
    """One ordered request item and its complete, server-frozen snapshot."""

    item_id: str
    target_id: str
    task_id: str
    unit_id: str
    step_ids: tuple[str, ...]
    request_digest: str
    rule_digest: str
    snapshot: FrozenExecutionSnapshot = field(repr=False)
    message_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_ids", tuple(self.step_ids))


@dataclass(frozen=True, slots=True)
class GraphItemPlan:
    order: int
    item_id: str
    target_id: str
    task_id: str
    unit_id: str
    step_ids: tuple[str, ...]
    request_digest: str
    rule_digest: str
    snapshot_digest: str
    snapshot: FrozenExecutionSnapshot = field(repr=False)
    message_id: str | None = None


@dataclass(frozen=True, slots=True)
class GraphTaskPlan:
    order: int
    task_id: str
    item_ids: tuple[str, ...]
    task_type: str
    graph_version: str
    payload_digest: str


@dataclass(frozen=True, slots=True)
class GraphUnitPlan:
    order: int
    unit_id: str
    task_id: str
    item_id: str
    unit_key: str
    quoted_amount: int
    currency: str = "credit"


@dataclass(frozen=True, slots=True)
class GraphStepPlan:
    order: int
    step_id: str
    task_id: str
    unit_id: str
    item_id: str
    step_key: str
    required: bool
    dependencies: tuple[str, ...]
    request_digest: str
    rule_digest: str
    snapshot_digest: str


@dataclass(frozen=True, slots=True)
class GraphMessagePlan:
    order: int
    message_id: str
    task_id: str
    item_id: str


@dataclass(frozen=True, slots=True)
class AttemptGroupPlan:
    group_id: str
    task_id: str
    unit_id: str
    item_id: str
    registry_version: str
    group_key: str
    policy_version: str
    stop_policy_version: str
    step_ids: tuple[str, str, str]
    required: bool
    max_retries: int
    original_payload_digest: str


@dataclass(frozen=True, slots=True)
class OperationGraphPlan:
    operation: GraphOperation
    graph_version: GraphVersion
    execution_mode: ExecutionMode
    charge_phase: ChargePhase
    items: tuple[GraphItemPlan, ...]
    tasks: tuple[GraphTaskPlan, ...]
    units: tuple[GraphUnitPlan, ...]
    steps: tuple[GraphStepPlan, ...]
    messages: tuple[GraphMessagePlan, ...]
    attempt_group: AttemptGroupPlan | None
    plan_digest: str
    policy: OperationPolicy


def build_operation_graph_plan(
    operation: str,
    *,
    graph_version: str,
    items: Sequence[GraphItemInput],
    attempt_group_id: str | None = None,
) -> OperationGraphPlan:
    """Build an immutable graph description from explicit policy and snapshots.

    The graph version is required at the call site. Historical versions are
    never silently upgraded to a currently registered graph.
    """

    policy = operation_policy(operation) if isinstance(operation, str) else None
    if policy is None or not isinstance(graph_version, str) or not graph_version:
        raise _invalid("unsupported operation or graph version")
    if policy.graph_version is None or policy.execution_mode != "claimed_worker":
        raise _invalid("taskless operations cannot be converted into durable graphs")
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        raise _invalid("graph items must be an ordered sequence")
    frozen_inputs = tuple(items)
    if not frozen_inputs or not all(isinstance(item, GraphItemInput) for item in frozen_inputs):
        raise _invalid("graph items are missing or malformed")
    frozen_inputs = tuple(
        replace(item, snapshot=_freeze_execution_snapshot(item.snapshot))
        for item in frozen_inputs
    )

    attempt_group_version = HAOAI_ATTEMPT_GROUP.registry_version
    uses_attempt_group = graph_version == attempt_group_version
    if uses_attempt_group:
        if operation != "video.single" or not _valid_identifier(attempt_group_id):
            raise _invalid("the requested attempt group is not valid for this operation")
    elif graph_version != policy.graph_version or attempt_group_id is not None:
        raise _invalid("graph version does not match the selected server policy")

    count = len(frozen_inputs)
    task_shape = task_shape_for_operation(operation, count)
    if task_shape is None:
        raise _invalid("operation has no supported user-task cardinality")
    if operation == "image.batch" and count > _MAX_BATCH_ITEMS:
        raise _invalid("image batches must contain at most 64 items")
    if operation == "frame.optimize" and count > _MAX_OPTIMIZE_FRAMES:
        raise _invalid("frame optimization accepts at most 30 items")
    if operation not in _MODEL_CATEGORIES:
        raise _invalid("operation has no durable graph planning policy")

    expected_category = _MODEL_CATEGORIES[operation]
    item_ids: set[str] = set()
    target_ids: set[str] = set()
    unit_ids: set[str] = set()
    step_ids: set[str] = set()
    message_ids: set[str] = set()
    task_ids: set[str] = set()
    batch_assets: set[tuple[str, str]] = set()

    for item in frozen_inputs:
        for value, label in (
            (item.item_id, "item id"),
            (item.target_id, "target id"),
            (item.task_id, "task id"),
            (item.unit_id, "unit id"),
        ):
            _require_identifier(value, label)
        if item.message_id is not None:
            _require_identifier(item.message_id, "message id")
        if operation == "image.batch" and item.message_id is None:
            raise _invalid("each batch image item requires its prepared message id")
        if not item.step_ids or not all(_valid_identifier(step_id) for step_id in item.step_ids):
            raise _invalid("each graph item requires stable step ids")
        if not _is_sha256(item.request_digest) or not _is_sha256(item.rule_digest):
            raise _invalid("request and rule digests must be lowercase SHA-256 values")
        if not isinstance(item.snapshot, FrozenExecutionSnapshot):
            raise _invalid("each graph item requires a frozen execution snapshot")
        quote = item.snapshot.server_quote
        if type(quote) is not int or quote < 0:
            raise _invalid("server quote must be a nonnegative integer")
        if item.snapshot.model_category != expected_category:
            raise _invalid("model category does not match the operation")
        target_snapshot = item.snapshot.target_snapshot
        if not isinstance(target_snapshot, TargetSnapshot) or (
            not isinstance(target_snapshot.kind, str)
            or target_snapshot.kind not in _TARGET_KINDS
        ):
            raise _invalid("target snapshot kind is not supported")
        if operation == "video.single" and target_snapshot.kind in _CANVAS_TARGET_KINDS:
            raise _invalid("video operations cannot target a canvas image")
        _stable_value(item.snapshot)

        if item.item_id in item_ids or item.target_id in target_ids or item.unit_id in unit_ids:
            raise _invalid("graph item, target, and unit ids must be unique")
        item_ids.add(item.item_id)
        target_ids.add(item.target_id)
        unit_ids.add(item.unit_id)
        task_ids.add(item.task_id)
        if item.message_id is not None:
            if item.message_id in message_ids:
                raise _invalid("message ids must be unique within a graph")
            message_ids.add(item.message_id)
        for step_id in item.step_ids:
            if step_id in step_ids:
                raise _invalid("step ids must be unique within a graph")
            step_ids.add(step_id)

        if operation == "image.batch":
            target = item.snapshot.target_snapshot
            asset_type = target.asset_type
            asset_id = target.asset_id or target.stable_storyboard_asset_id
            if asset_type not in _BATCH_ASSET_TYPES or not _valid_identifier(asset_id):
                raise _invalid("batch items require a supported stable asset identity")
            asset_key = (asset_type, asset_id)
            if asset_key in batch_assets:
                raise _invalid("batch requests cannot repeat an asset type and id")
            batch_assets.add(asset_key)

    if len(task_ids) != task_shape.task_count:
        raise _invalid("graph task count does not match the user-task policy")

    expected_step_count = 3 if uses_attempt_group else 1
    if any(len(item.step_ids) != expected_step_count for item in frozen_inputs):
        raise _invalid("step id count does not match the selected graph variant")

    plan_items: list[GraphItemPlan] = []
    for order, item in enumerate(frozen_inputs):
        snapshot_digest = _digest_value(_stable_value(item.snapshot))
        plan_items.append(
            GraphItemPlan(
                order=order,
                item_id=item.item_id,
                target_id=item.target_id,
                task_id=item.task_id,
                unit_id=item.unit_id,
                step_ids=item.step_ids,
                request_digest=item.request_digest,
                rule_digest=item.rule_digest,
                snapshot_digest=snapshot_digest,
                snapshot=item.snapshot,
                message_id=item.message_id,
            )
        )

    if operation == "frame.optimize":
        task_inputs = (tuple(plan_items),)
    else:
        task_inputs = tuple((item,) for item in plan_items)
    tasks = tuple(
        GraphTaskPlan(
            order=order,
            task_id=task_group[0].task_id,
            item_ids=tuple(item.item_id for item in task_group),
            task_type=policy.task_type or "",
            graph_version=graph_version,
            payload_digest=_digest_value(
                {
                    "operation": operation,
                    "graph_version": graph_version,
                    "items": [
                        {
                            "item_id": item.item_id,
                            "target_id": item.target_id,
                            "request_digest": item.request_digest,
                            "rule_digest": item.rule_digest,
                            "snapshot_digest": item.snapshot_digest,
                        }
                        for item in task_group
                    ],
                }
            ),
        )
        for order, task_group in enumerate(task_inputs)
    )
    if not graph_tasks_match_shape(operation, count, tasks):
        raise _invalid("planned tasks do not match the user-task policy")

    units = tuple(
        GraphUnitPlan(
            order=order,
            unit_id=item.unit_id,
            task_id=item.task_id,
            item_id=item.item_id,
            unit_key=item.item_id,
            quoted_amount=item.snapshot.server_quote,
        )
        for order, item in enumerate(plan_items)
    )

    steps: list[GraphStepPlan] = []
    for item in plan_items:
        if uses_attempt_group:
            step_keys = HAOAI_ATTEMPT_GROUP.attempt_step_ids
            required_flags = (False, False, False)
        else:
            step_keys = ("generate",)
            required_flags = (True,)
        for offset, (step_id, step_key, required) in enumerate(
            zip(item.step_ids, step_keys, required_flags, strict=True)
        ):
            step_order = offset if uses_attempt_group else (
                item.order if operation == "frame.optimize" else 0
            )
            steps.append(
                GraphStepPlan(
                    order=step_order,
                    step_id=step_id,
                    task_id=item.task_id,
                    unit_id=item.unit_id,
                    item_id=item.item_id,
                    step_key=step_key,
                    required=required,
                    dependencies=(),
                    request_digest=item.request_digest,
                    rule_digest=item.rule_digest,
                    snapshot_digest=item.snapshot_digest,
                )
            )

    messages = tuple(
        GraphMessagePlan(
            order=item.order,
            message_id=item.message_id,
            task_id=item.task_id,
            item_id=item.item_id,
        )
        for item in plan_items
        if item.message_id is not None
    )

    group: AttemptGroupPlan | None = None
    if uses_attempt_group:
        item = plan_items[0]
        group = AttemptGroupPlan(
            group_id=attempt_group_id or "",
            task_id=item.task_id,
            unit_id=item.unit_id,
            item_id=item.item_id,
            registry_version=HAOAI_ATTEMPT_GROUP.registry_version,
            group_key=HAOAI_ATTEMPT_GROUP.group_key,
            policy_version=HAOAI_ATTEMPT_GROUP.policy_version,
            stop_policy_version=HAOAI_ATTEMPT_GROUP.stop_policy_version,
            step_ids=(item.step_ids[0], item.step_ids[1], item.step_ids[2]),
            required=True,
            max_retries=HAOAI_ATTEMPT_GROUP.max_retries,
            # Provider polling binds the original wire request, not the Task's
            # aggregate graph identity. Keep those digests distinct.
            original_payload_digest=item.request_digest,
        )

    plan_payload = {
        "operation": operation,
        "graph_version": graph_version,
        "charge_phase": policy.charge_phase,
        "items": [
            {
                "item_id": item.item_id,
                "target_id": item.target_id,
                "task_id": item.task_id,
                "unit_id": item.unit_id,
                "step_ids": item.step_ids,
                "message_id": item.message_id,
                "request_digest": item.request_digest,
                "rule_digest": item.rule_digest,
                "snapshot_digest": item.snapshot_digest,
            }
            for item in plan_items
        ],
        "attempt_group_id": attempt_group_id,
    }
    return OperationGraphPlan(
        operation=operation,
        graph_version=graph_version,
        execution_mode=policy.execution_mode,
        charge_phase=policy.charge_phase,
        items=tuple(plan_items),
        tasks=tasks,
        units=units,
        steps=tuple(steps),
        messages=messages,
        attempt_group=group,
        plan_digest=_digest_value(plan_payload),
        policy=policy,
    )


def _freeze_execution_snapshot(snapshot: object) -> FrozenExecutionSnapshot:
    """Copy the complete public snapshot into detached immutable values.

    Frozen dataclasses do not freeze mutable containers supplied for tuple-typed
    fields, so plans must own a normalized copy before validation or hashing.
    """

    if type(snapshot) is not FrozenExecutionSnapshot:
        raise _invalid("each graph item requires a supported frozen execution snapshot")

    model_ref = snapshot.model_ref
    if type(model_ref) is not ModelReference:
        raise _invalid("frozen snapshot model reference is malformed")
    frozen_model_ref = ModelReference(
        owner_id=_required_text(model_ref.owner_id, "model owner id"),
        provider=_required_text(model_ref.provider, "model provider"),
        model_id=_required_text(model_ref.model_id, "model id"),
    )

    endpoint = snapshot.endpoint_policy
    if type(endpoint) is not EndpointPolicy:
        raise _invalid("frozen snapshot endpoint policy is malformed")
    frozen_endpoint = EndpointPolicy(
        provider_origin=_optional_text(endpoint.provider_origin, "provider origin"),
        allowed_content_paths=_text_sequence(
            endpoint.allowed_content_paths, "allowed content paths"
        ),
        allow_private_network=_boolean(endpoint.allow_private_network, "private network policy"),
    )

    grant = snapshot.grant_snapshot
    if type(grant) is not FrozenGrantSnapshot:
        raise _invalid("frozen snapshot grant is malformed")
    frozen_grant = FrozenGrantSnapshot(
        actor_id=_required_text(grant.actor_id, "grant actor id"),
        series_id=_optional_text(grant.series_id, "grant series id"),
        team_id=_optional_text(grant.team_id, "grant team id"),
        access_provenance=_text_sequence(grant.access_provenance, "grant access provenance"),
    )

    target = snapshot.target_snapshot
    if type(target) is not TargetSnapshot:
        raise _invalid("frozen snapshot target is malformed")
    frozen_target = TargetSnapshot(
        kind=_required_text(target.kind, "target kind"),
        chapter_id=_optional_text(target.chapter_id, "target chapter id"),
        series_id=_optional_text(target.series_id, "target series id"),
        stable_storyboard_asset_id=_optional_text(
            target.stable_storyboard_asset_id, "stable storyboard asset id"
        ),
        asset_type=_optional_text(target.asset_type, "target asset type"),
        asset_id=_optional_text(target.asset_id, "target asset id"),
        compat_frame_index=_optional_integer(target.compat_frame_index, "compat frame index"),
        expected_text_digest=_optional_text(target.expected_text_digest, "expected text digest"),
        media_identity=_optional_text(target.media_identity, "media identity"),
        content_digest=_optional_text(target.content_digest, "content digest"),
        notes_revision=_optional_integer(target.notes_revision, "notes revision"),
        full_frame_digest=_optional_text(target.full_frame_digest, "full frame digest"),
        full_chapter_digest=_optional_text(target.full_chapter_digest, "full chapter digest"),
        expected_asset_image_url=_optional_text(
            target.expected_asset_image_url, "expected asset image URL"
        ),
        same_series_target_digest=_optional_text(
            target.same_series_target_digest, "same-series target digest"
        ),
    )

    adapter_versions = _sequence(snapshot.adapter_config_versions, "adapter config versions")
    frozen_adapter_versions: list[tuple[str, str]] = []
    for version in adapter_versions:
        pair = _sequence(version, "adapter config version")
        if len(pair) != 2:
            raise _invalid("each adapter config version requires a provider and version")
        frozen_adapter_versions.append(
            (
                _required_text(pair[0], "adapter provider"),
                _required_text(pair[1], "adapter version"),
            )
        )

    return FrozenExecutionSnapshot(
        model_ref=frozen_model_ref,
        model_category=_required_text(snapshot.model_category, "model category"),
        model_name=_required_text(snapshot.model_name, "model name"),
        model_params=_freeze_json_value(snapshot.model_params),
        prompt_ref=_optional_text(snapshot.prompt_ref, "prompt reference"),
        effective_prompt=_required_text(snapshot.effective_prompt, "effective prompt"),
        effective_context=_required_text(snapshot.effective_context, "effective context"),
        server_quote=snapshot.server_quote,
        reference_snapshot_ref=_required_text(
            snapshot.reference_snapshot_ref, "reference snapshot reference"
        ),
        target_snapshot=frozen_target,
        credential_binding_ref=_required_text(
            snapshot.credential_binding_ref, "credential binding reference"
        ),
        grant_snapshot=frozen_grant,
        adapter_config_versions=tuple(frozen_adapter_versions),
        endpoint_policy=frozen_endpoint,
    )


def _freeze_json_value(value: object) -> object:
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise _invalid("frozen snapshot mapping keys must be strings")
        return MappingProxyType(
            {key: _freeze_json_value(value[key]) for key in value}
        )
    if isinstance(value, (tuple, list)):
        return tuple(_freeze_json_value(item) for item in value)
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise _invalid("frozen snapshot contains an unsupported value")


def _sequence(value: object, label: str) -> tuple[object, ...] | list[object]:
    if not isinstance(value, (tuple, list)):
        raise _invalid(f"{label} must be an ordered tuple or list")
    return value


def _text_sequence(value: object, label: str) -> tuple[str, ...]:
    values = _sequence(value, label)
    if not all(isinstance(item, str) for item in values):
        raise _invalid(f"{label} must contain only strings")
    return tuple(values)


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise _invalid(f"{label} must be a string")
    return value


def _optional_text(value: object, label: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise _invalid(f"{label} must be a string or None")
    return value


def _optional_integer(value: object, label: str) -> int | None:
    if value is not None and type(value) is not int:
        raise _invalid(f"{label} must be an integer or None")
    return value


def _boolean(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise _invalid(f"{label} must be a boolean")
    return value


def _require_identifier(value: str, label: str) -> None:
    if not _valid_identifier(value):
        raise _invalid(f"{label} must be a stable nonblank string")


def _valid_identifier(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and len(value) <= 255
        and all(ord(character) >= 32 for character in value)
    )


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _DIGEST_PATTERN.fullmatch(value) is not None


def _stable_value(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _invalid("frozen snapshot contains a non-finite number")
        return value
    if isinstance(value, Enum):
        return _stable_value(value.value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "type": type(value).__qualname__,
            "fields": {item.name: _stable_value(getattr(value, item.name)) for item in fields(value)},
        }
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise _invalid("frozen snapshot mapping keys must be strings")
        return {key: _stable_value(value[key]) for key in sorted(value)}
    if isinstance(value, (tuple, list)):
        return [_stable_value(item) for item in value]
    raise _invalid("frozen snapshot contains an unsupported value")


def _digest_value(value: object) -> str:
    encoded = json.dumps(
        _stable_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return sha256(encoded).hexdigest()


def _invalid(detail: str) -> GenerationError:
    return GenerationError("invalid_generation_request", detail)


__all__ = (
    "AttemptGroupPlan",
    "GraphItemInput",
    "GraphItemPlan",
    "GraphMessagePlan",
    "GraphStepPlan",
    "GraphTaskPlan",
    "GraphUnitPlan",
    "OperationGraphPlan",
    "build_operation_graph_plan",
)
