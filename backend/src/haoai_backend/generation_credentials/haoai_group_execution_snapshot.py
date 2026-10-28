"""Reopen the frozen HaoAI input for a three-step video attempt group.

This reader authenticates the original admission payload and the complete
durable Group graph. It does not claim work or authorize provider I/O.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.errors import GenerationError
from haoai_backend.generation_contracts.types import (
    BoundCredential,
    CredentialBinding,
    FrozenExecutionSnapshot,
)
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    ExecutionStepRow,
    TaskSubmissionRow,
)
from haoai_backend.generation_core.graph_planning import (
    GraphItemInput,
    build_operation_graph_plan,
)
from haoai_backend.generation_core.schema import ExecutionAttemptGroupRow
from haoai_backend.generation_credentials.application import CredentialRegistry
from haoai_backend.generation_credentials.domain import (
    CredentialCrypto,
    HmacProofAuthority,
    SealedMaterial,
)
from haoai_backend.generation_credentials.persistence import prepared_payloads
from haoai_backend.generation_credentials.snapshot_producer import (
    stored_payload_associated_data,
)
from haoai_backend.generation_credentials.video_execution_snapshot import (
    PersistedVideoExecutionBinding,
    _PAYLOAD_KEYS,
    _decode_canonical_object,
    _decode_execution_snapshot,
    _payload_row_matches_binding,
    _persisted_request_projection_matches,
    _reference_groups_from_item,
    _request_data_matches_binding,
    _snapshot_matches_binding,
    _target_id,
    _verify_payload,
)
from haoai_backend.generation_runtime.registry import HAOAI_ATTEMPT_GROUP

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_STEP_SNAPSHOT_KEYS = frozenset(
    {
        "server_attempt_id",
        "prepared_payload_id",
        "protected_ref",
        "snapshot_digest",
        "ordered_manifest_digest",
    }
)
_GROUP_STATUSES = frozenset(
    {
        "prepared",
        "active",
        "pending_verification",
        "succeeded",
        "failed",
        "cancelled",
        "policy_stopped",
        "succeeded_late",
    }
)
_STEP_STATUSES = frozenset(
    {
        "prepared",
        "submitting",
        "submitted",
        "result_observed",
        "applied",
        "failed",
        "reconciling",
        "deterministic_empty",
    }
)


@dataclass(frozen=True, slots=True)
class PersistedHaoaiGroupExecutionBinding:
    """Exact selected attempt and immutable SQL graph facts for one claim."""

    owner_id: str
    task_id: str
    unit_id: str
    group_key: str
    step_id: str
    attempt_index: int
    submission_id: str
    raw_key: str = field(repr=False)
    operation: str
    graph_version: str
    task_type: str
    task_model_id: str
    task_model_source: str
    task_model_owner_id: str | None
    task_model_name: str
    request_digest: str
    rule_digest: str
    snapshot_digest: str
    server_attempt_id: str
    prepared_payload_id: str
    protected_ref: str
    ordered_manifest_digest: str
    quoted_amount: int
    task_message_id: str | None = None


@dataclass(frozen=True, slots=True)
class VerifiedHaoaiGroupExecutionInput:
    """Authenticated original request data; not a claim or I/O capability."""

    task_id: str
    unit_id: str
    step_id: str
    attempt_index: int
    prepared_payload_id: str
    server_attempt_id: str
    task_model_name: str
    snapshot: FrozenExecutionSnapshot = field(repr=False)
    credential_binding: CredentialBinding = field(repr=False)
    provider_credential: BoundCredential = field(repr=False)
    reference_images: tuple[str, ...] = field(default=(), repr=False)
    reference_audios: tuple[str, ...] = field(default=(), repr=False)
    reference_videos: tuple[str, ...] = field(default=(), repr=False)


def read_original_haoai_group_execution_input(
    session: Session,
    binding: PersistedHaoaiGroupExecutionBinding,
    *,
    registry: CredentialRegistry,
    crypto: CredentialCrypto,
    proof_authority: HmacProofAuthority,
    credential_purpose: Literal["create", "query"] = "create",
) -> VerifiedHaoaiGroupExecutionInput | None:
    """Verify the exact three-step Group and reopen its single frozen payload."""

    if (
        not isinstance(session, Session)
        or type(binding) is not PersistedHaoaiGroupExecutionBinding
        or type(registry) is not CredentialRegistry
        or type(crypto) is not CredentialCrypto
        or type(proof_authority) is not HmacProofAuthority
        or credential_purpose not in {"create", "query"}
        or not _valid_binding(binding)
    ):
        return None
    try:
        if not crypto.ready():
            return None
        with session.no_autoflush:
            graph = _read_persisted_graph(session, binding)
            if graph is None:
                return None
            task, parent, unit, steps, group, request_data = graph
            if not persisted_haoai_group_graph_matches(
                binding, task, parent, unit, steps, group, request_data
            ):
                return None
            rows = session.execute(
                select(prepared_payloads).where(
                    prepared_payloads.c.prepared_payload_id
                    == binding.prepared_payload_id,
                    prepared_payloads.c.actor_id == binding.owner_id,
                    prepared_payloads.c.operation == binding.operation,
                    prepared_payloads.c.server_attempt_id
                    == binding.server_attempt_id,
                )
            ).mappings().all()
        if len(rows) != 1:
            return None
        row = dict(rows[0])
        video_binding = _video_binding(binding)
        if not _payload_row_matches_binding(row, video_binding):
            return None

        associated_data = stored_payload_associated_data(
            row,
            parent_attempt_id=binding.submission_id,
            protected_ref=binding.protected_ref,
            item_index=0,
            ordered_count=1,
            ordered_manifest_digest=binding.ordered_manifest_digest,
        )
        material = SealedMaterial(
            ciphertext=row["ciphertext"],
            nonce=row["nonce"],
            key_id=row["encryption_key_id"],
            algorithm_version=crypto.algorithm_version,
        )
        plaintext = crypto.open_secret(material, associated_data)
        if plaintext is None:
            return None
        payload = _decode_canonical_object(plaintext)
        if payload is None or not _verify_group_payload(
            payload, row, video_binding, proof_authority
        ):
            return None

        item = payload["item"]
        snapshot = _decode_execution_snapshot(item.get("execution_snapshot"))
        references = _reference_groups_from_item(item)
        if (
            snapshot is None
            or references is None
            or not _persisted_request_projection_matches(
                session, video_binding, item
            )
            or not _snapshot_matches_binding(snapshot, item, video_binding, row)
        ):
            return None
        target_id = _target_id(snapshot.target_snapshot, binding.server_attempt_id)
        plan = build_operation_graph_plan(
            binding.operation,
            graph_version=binding.graph_version,
            items=(
                GraphItemInput(
                    item_id=binding.server_attempt_id,
                    target_id=target_id,
                    task_id=binding.task_id,
                    unit_id=binding.unit_id,
                    step_ids=tuple(step["id"] for step in _steps_for_plan(graph)),
                    request_digest=binding.request_digest,
                    rule_digest=binding.rule_digest,
                    snapshot=snapshot,
                    message_id=binding.task_message_id,
                ),
            ),
            attempt_group_id=binding.unit_id,
        )
        if not _planned_group_matches(plan, binding, snapshot):
            return None

        credential = item["credential"]
        provenance = credential["version_provenance"]
        credential_binding = CredentialBinding(
            source=row["source"],
            row_id=row["row_id"],
            owner_or_system=row["owner_or_system"],
            provider=row["provider"],
            original_principal_id=row["principal_id"],
            endpoint_origin=credential["endpoint_origin"],
            allowed_content_paths=tuple(credential["allowed_content_paths"]),
            binding_version=str(row["binding_version"]),
            rotation_provenance=provenance["rotation_proof_ref"],
        )
        provider_credential = registry.resolve_original_credential(
            credential_binding,
            credential_purpose,
        )
        if (
            type(provider_credential) is not BoundCredential
            or provider_credential.provider != "haoai"
            or provider_credential.principal_id != row["principal_id"]
        ):
            return None
        return VerifiedHaoaiGroupExecutionInput(
            task_id=binding.task_id,
            unit_id=binding.unit_id,
            step_id=binding.step_id,
            attempt_index=binding.attempt_index,
            prepared_payload_id=binding.prepared_payload_id,
            server_attempt_id=binding.server_attempt_id,
            task_model_name=binding.task_model_name,
            snapshot=snapshot,
            credential_binding=credential_binding,
            provider_credential=provider_credential,
            reference_images=references["reference_images"],
            reference_audios=references["reference_audios"],
            reference_videos=references["reference_videos"],
        )
    except (GenerationError, KeyError, TypeError, ValueError, UnicodeError):
        return None
    except Exception:
        return None


def _read_persisted_graph(session: Session, binding: PersistedHaoaiGroupExecutionBinding):
    task = session.execute(
        select(AITaskRow.__table__).where(AITaskRow.__table__.c.id == binding.task_id)
    ).mappings().one_or_none()
    parent = session.execute(
        select(TaskSubmissionRow.__table__).where(
            TaskSubmissionRow.__table__.c.id == binding.submission_id
        )
    ).mappings().one_or_none()
    unit = session.execute(
        select(BillingUnitRow.__table__).where(
            BillingUnitRow.__table__.c.id == binding.unit_id
        )
    ).mappings().one_or_none()
    steps = session.execute(
        select(ExecutionStepRow.__table__)
        .where(ExecutionStepRow.__table__.c.billing_unit_id == binding.unit_id)
        .order_by(
            ExecutionStepRow.__table__.c.step_order,
            ExecutionStepRow.__table__.c.id,
        )
    ).mappings().all()
    group = session.execute(
        select(ExecutionAttemptGroupRow.__table__).where(
            ExecutionAttemptGroupRow.__table__.c.unit_id == binding.unit_id,
            ExecutionAttemptGroupRow.__table__.c.group_key == binding.group_key,
        )
    ).mappings().one_or_none()
    if task is None or parent is None or unit is None or group is None:
        return None
    try:
        request_data = _decode_json_object(task["request_data"])
    except (TypeError, ValueError, UnicodeError):
        return None
    if request_data is None:
        return None
    return (
        dict(task),
        dict(parent),
        dict(unit),
        tuple(dict(step) for step in steps),
        dict(group),
        request_data,
    )


def persisted_haoai_group_graph_matches(
    binding: PersistedHaoaiGroupExecutionBinding,
    task: Mapping[str, object],
    parent: Mapping[str, object],
    unit: Mapping[str, object],
    steps: tuple[dict[str, object], ...],
    group: Mapping[str, object],
    request_data: Mapping[str, object],
) -> bool:
    if (
        type(binding) is not PersistedHaoaiGroupExecutionBinding
        or not _valid_binding(binding)
    ):
        return False
    if len(steps) != 3:
        return False
    ordered_ids = tuple(step.get("id") for step in steps)
    group_step_ids = group.get("attempt_step_ids")
    if (
        not isinstance(group_step_ids, list)
        or len(group_step_ids) != 3
        or any(not isinstance(step_id, str) or not step_id for step_id in group_step_ids)
        or len(set(group_step_ids)) != 3
        or tuple(group_step_ids) != ordered_ids
    ):
        return False
    step_snapshot = steps[0].get("input_snapshot")
    if not isinstance(step_snapshot, Mapping) or set(step_snapshot) != _STEP_SNAPSHOT_KEYS:
        return False
    video_binding = _video_binding(binding)
    return all(
        (
            task.get("id") == binding.task_id,
            task.get("user_id") == binding.owner_id,
            task.get("type") == "video-single",
            task.get("model_name") == binding.task_model_name,
            type(task.get("credit_cost")) is int
            and task.get("credit_cost") == binding.quoted_amount,
            (task.get("message_id") or None) == binding.task_message_id,
            parent.get("id") == binding.submission_id,
            parent.get("user_id") == binding.owner_id,
            parent.get("operation") == "video.single",
            parent.get("idempotency_key") == binding.raw_key,
            parent.get("request_digest") == binding.request_digest,
            parent.get("task_ids") == [binding.task_id],
            parent.get("status") == "accepted",
            unit.get("id") == binding.unit_id,
            unit.get("task_id") == binding.task_id,
            unit.get("operation") == "video.single",
            unit.get("step_graph_version") == HAOAI_ATTEMPT_GROUP.registry_version,
            unit.get("item_key") == binding.server_attempt_id,
            unit.get("input_digest") == binding.request_digest,
            type(unit.get("quoted_amount")) is int
            and unit.get("quoted_amount") == binding.quoted_amount,
            group.get("unit_id") == binding.unit_id,
            group.get("group_key") == HAOAI_ATTEMPT_GROUP.group_key,
            group.get("registry_version") == HAOAI_ATTEMPT_GROUP.registry_version,
            group_step_ids == list(ordered_ids),
            group.get("original_payload_digest") == binding.request_digest,
            type(group.get("max_retries")) is int
            and group.get("max_retries") == HAOAI_ATTEMPT_GROUP.max_retries,
            type(group.get("active_attempt")) is int
            and 0 <= group.get("active_attempt") <= HAOAI_ATTEMPT_GROUP.max_retries,
            group.get("active_attempt") == binding.attempt_index,
            steps[binding.attempt_index].get("id") == binding.step_id,
            group.get("status") in _GROUP_STATUSES,
            type(group.get("poll_used")) is int
            and 0 <= group.get("poll_used") <= HAOAI_ATTEMPT_GROUP.max_polls,
            type(group.get("auth_unavailable_streak")) is int
            and group.get("auth_unavailable_streak") >= 0,
            type(group.get("poll_sequence")) is int and group.get("poll_sequence") >= 0,
            group.get("active_eligibility_version") == HAOAI_ATTEMPT_GROUP.registry_version,
            request_data.get("operation") == "video.single",
            request_data.get("submission_id") == binding.submission_id,
            request_data.get("parent_attempt_id") == binding.submission_id,
            request_data.get("server_attempt_id") == binding.server_attempt_id,
            request_data.get("item_id") == binding.server_attempt_id,
            request_data.get("unit_key") == binding.server_attempt_id,
            request_data.get("billing_unit_id") == binding.unit_id,
            request_data.get("step_graph_version") == HAOAI_ATTEMPT_GROUP.registry_version,
            request_data.get("request_digest") == binding.request_digest,
            request_data.get("snapshot_digest") == binding.snapshot_digest,
            request_data.get("rule_digest") == binding.rule_digest,
            request_data.get("prepared_payload_id") == binding.prepared_payload_id,
            request_data.get("protected_ref") == binding.protected_ref,
            request_data.get("target_ref") == f"{binding.protected_ref}#target",
            request_data.get("model_ref") == f"{binding.protected_ref}#model",
            request_data.get("grant_ref") == f"{binding.protected_ref}#grant",
            _request_data_matches_binding(request_data, video_binding, unit),
            len(steps) == len(HAOAI_ATTEMPT_GROUP.attempt_step_ids),
            all(
                _step_matches_binding(
                    step,
                    index,
                    step_snapshot,
                    video_binding,
                )
                for index, step in enumerate(steps)
            ),
        )
    )


def _step_matches_binding(
    step: Mapping[str, object],
    index: int,
    first_snapshot: Mapping[str, object],
    binding: PersistedVideoExecutionBinding,
) -> bool:
    snapshot = step.get("input_snapshot")
    return bool(
        isinstance(snapshot, Mapping)
        and set(snapshot) == _STEP_SNAPSHOT_KEYS
        and snapshot == first_snapshot
        and step.get("id")
        and step.get("billing_unit_id") == binding.unit_id
        and step.get("step_key") == HAOAI_ATTEMPT_GROUP.attempt_step_ids[index]
        and step.get("required") is False
        and type(step.get("step_order")) is int
        and step.get("step_order") == index
        and step.get("dependencies") in ([], ())
        and step.get("input_digest") == binding.request_digest
        and step.get("execution_status") in _STEP_STATUSES
    )


def _verify_group_payload(
    payload: Mapping[str, object],
    row: Mapping[str, object],
    binding: PersistedVideoExecutionBinding,
    authority: HmacProofAuthority,
) -> bool:
    if (
        set(payload) != _PAYLOAD_KEYS
        or payload.get("graph_version") != HAOAI_ATTEMPT_GROUP.registry_version
    ):
        return False
    return _verify_payload(payload, row, binding, authority)


def _planned_group_matches(plan, binding, snapshot: FrozenExecutionSnapshot) -> bool:
    group = plan.attempt_group
    return bool(
        plan.operation == "video.single"
        and plan.graph_version == HAOAI_ATTEMPT_GROUP.registry_version
        and len(plan.items) == len(plan.tasks) == len(plan.units) == 1
        and len(plan.steps) == 3
        and group is not None
        and group.group_id == binding.unit_id
        and group.task_id == binding.task_id
        and group.unit_id == binding.unit_id
        and group.step_ids == tuple(step.step_id for step in plan.steps)
        and group.original_payload_digest == binding.request_digest
        and group.required is True
        and group.max_retries == HAOAI_ATTEMPT_GROUP.max_retries
        and tuple(step.step_key for step in plan.steps)
        == HAOAI_ATTEMPT_GROUP.attempt_step_ids
        and all(not step.required for step in plan.steps)
        and all(step.request_digest == binding.request_digest for step in plan.steps)
        and plan.items[0].snapshot_digest == binding.snapshot_digest
        and snapshot.model_ref.provider == "haoai"
    )


def _video_binding(
    binding: PersistedHaoaiGroupExecutionBinding,
) -> PersistedVideoExecutionBinding:
    return PersistedVideoExecutionBinding(
        owner_id=binding.owner_id,
        task_id=binding.task_id,
        unit_id=binding.unit_id,
        step_id=binding.step_id,
        submission_id=binding.submission_id,
        raw_key=binding.raw_key,
        operation=binding.operation,
        graph_version=binding.graph_version,
        task_type=binding.task_type,
        task_model_id=binding.task_model_id,
        task_model_source=binding.task_model_source,
        task_model_owner_id=binding.task_model_owner_id,
        task_model_name=binding.task_model_name,
        request_digest=binding.request_digest,
        rule_digest=binding.rule_digest,
        snapshot_digest=binding.snapshot_digest,
        server_attempt_id=binding.server_attempt_id,
        prepared_payload_id=binding.prepared_payload_id,
        protected_ref=binding.protected_ref,
        ordered_manifest_digest=binding.ordered_manifest_digest,
        quoted_amount=binding.quoted_amount,
        task_message_id=binding.task_message_id,
    )


def _steps_for_plan(graph):
    return graph[3]


def _valid_binding(binding: PersistedHaoaiGroupExecutionBinding) -> bool:
    text_fields = (
        binding.owner_id,
        binding.task_id,
        binding.unit_id,
        binding.group_key,
        binding.step_id,
        binding.submission_id,
        binding.raw_key,
        binding.operation,
        binding.graph_version,
        binding.task_type,
        binding.task_model_id,
        binding.task_model_source,
        binding.task_model_name,
        binding.request_digest,
        binding.rule_digest,
        binding.snapshot_digest,
        binding.server_attempt_id,
        binding.prepared_payload_id,
        binding.protected_ref,
        binding.ordered_manifest_digest,
    )
    return all(
        (
            all(isinstance(value, str) and bool(value) for value in text_fields),
            binding.operation == "video.single",
            binding.graph_version == HAOAI_ATTEMPT_GROUP.registry_version,
            binding.task_type == "video-single",
            binding.group_key == HAOAI_ATTEMPT_GROUP.group_key,
            type(binding.attempt_index) is int
            and 0 <= binding.attempt_index <= HAOAI_ATTEMPT_GROUP.max_retries,
            binding.task_model_source in {"system", "user"},
            binding.server_attempt_id == f"{binding.submission_id}:0",
            binding.step_id,
            type(binding.quoted_amount) is int and binding.quoted_amount >= 0,
            binding.task_message_id is None or isinstance(binding.task_message_id, str),
            all(
                _is_digest(value)
                for value in (
                    binding.request_digest,
                    binding.rule_digest,
                    binding.snapshot_digest,
                    binding.ordered_manifest_digest,
                )
            ),
            binding.task_model_source != "system"
            or binding.task_model_owner_id is None,
            binding.task_model_source != "user"
            or binding.task_model_owner_id == binding.owner_id,
        )
    )


def _decode_json_object(value: object) -> dict[str, object] | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(
            value,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (TypeError, ValueError, UnicodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate request data key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite request data number: {value}")


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


__all__ = (
    "PersistedHaoaiGroupExecutionBinding",
    "VerifiedHaoaiGroupExecutionInput",
    "persisted_haoai_group_graph_matches",
    "read_original_haoai_group_execution_input",
)
