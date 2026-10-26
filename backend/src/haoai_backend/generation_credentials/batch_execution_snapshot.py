"""Authenticate the original frozen input for one image.batch item.

The selected item is derived from the durable ordered submission graph. Every
child payload is reopened and checked against the same signed parent manifest
before the selected item's credential or references are returned.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from haoai_backend.chat_data.tables import chat_messages
from haoai_backend.generation_catalog.application import compose_batch_image_prompt
from haoai_backend.generation_catalog.domain import ModelSource, PromptOperation, PromptSelection
from haoai_backend.generation_contracts.errors import GenerationError
from haoai_backend.generation_contracts.types import (
    BoundCredential,
    CredentialBinding,
    EndpointPolicy,
    FrozenExecutionSnapshot,
    FrozenGrantSnapshot,
    ModelReference,
    ReferenceInput,
    TargetSnapshot,
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
from haoai_backend.generation_credentials.application import CredentialRegistry
from haoai_backend.generation_credentials.domain import (
    CredentialCrypto,
    HmacProofAuthority,
    SealedMaterial,
)
from haoai_backend.generation_credentials.persistence import prepared_payloads
from haoai_backend.generation_credentials.snapshot_producer import (
    canonical_payload_json,
    stored_payload_associated_data,
)
from haoai_backend.series_access.domain import TeamMembership, can_enter_claimed_series

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MAX_BATCH_ITEMS = 64
_OPERATION = "image.batch"
_GRAPH_VERSION = "image-batch-v1"
_TASK_TYPE = "batch-image"
_COMMAND_PROOF_PURPOSE = "prepared-generation-input-v1"
_COMMAND_MANIFEST_KEYS = frozenset(
    {
        "purpose",
        "actor_id",
        "operation",
        "raw_key",
        "fingerprint",
        "canonical_request",
        "preparation",
        "parent_attempt_id",
        "children",
        "payload_ids",
        "protected_refs",
        "ordered_count",
        "item_digests",
        "projector_version",
        "reference_policy_version",
        "graph_version",
    }
)
_PREPARATION_KEYS = frozenset(
    {
        "operation",
        "fingerprint",
        "phase",
        "items",
        "quoted_total",
        "required_balance",
        "available_balance",
        "reserved_amount",
        "credential_binding_status",
        "executable",
    }
)
_PREPARATION_ITEM_KEYS = frozenset(
    {
        "index",
        "model",
        "credential_configured",
        "target",
        "prompt",
        "quote_amount",
        "prompt_digest",
        "effective_parameters",
        "reference_counts",
    }
)
_FINGERPRINT_KEYS = frozenset(
    {
        "schema_version",
        "ordered_nonsecret_payload_digest",
        "actor_id",
        "operation",
    }
)
_PAYLOAD_KEYS = frozenset(
    {
        "parent_attempt_id",
        "protected_ref",
        "child_attempt_id",
        "item_index",
        "ordered_count",
        "ordered_manifest",
        "ordered_manifest_digest",
        "manifest_proof",
        "graph_version",
        "command_manifest",
        "item",
    }
)
_ORDERED_ITEM_KEYS = frozenset(
    {"index", "child_attempt_id", "payload_id", "protected_ref", "item_digest"}
)
_PROTECTED_ITEM_KEYS = frozenset(
    {
        "index",
        "operation",
        "context_schema",
        "fingerprint",
        "raw_key",
        "raw_item",
        "model",
        "prompt",
        "references",
        "target",
        "grant",
        "credential",
        "projector_version",
        "reference_policy_version",
        "graph_version",
        "admission_policy",
        "execution_snapshot",
        "legacy_message_projection",
        "request_data_projection",
    }
)
_MODEL_KEYS = frozenset(
    {
        "source",
        "row_id",
        "owner_or_system",
        "provider",
        "provider_route",
        "category",
        "display_name",
        "wire_model",
        "snapshot_model_name",
        "base_url",
        "params",
        "effective_parameters",
        "snapshot_parameters",
        "quote_amount",
    }
)
_PROMPT_KEYS = frozenset({"id", "selection", "effective_prompt", "effective_context"})
_CREDENTIAL_KEYS = frozenset(
    {
        "scope",
        "binding_id",
        "binding_version",
        "principal_id",
        "record_id",
        "issuer_namespace",
        "stable_account_subject",
        "key_generation",
        "evidence_ref",
        "evidence_version",
        "endpoint_origin",
        "api_base_path",
        "allowed_content_paths",
        "endpoint_policy_version",
        "protected_record",
        "version_provenance",
    }
)
_REQUEST_PROJECTION_KEYS = frozenset(
    {
        "operation",
        "submission_id",
        "request_digest",
        "rule_digest",
        "mode",
        "model_id",
        "model_source",
        "model_owner_id",
        "prompt_id",
        "size",
        "chapter_id",
        "asset_type",
        "asset_id",
        "asset_name",
        "reference_images_version",
        "reference_images_count",
        "reference_images_digest",
        "prepared_payload_id",
        "protected_ref",
        "target_ref",
        "model_ref",
        "grant_ref",
        "target_snapshot",
    }
)
_REFERENCE_KEYS = frozenset({"reference_images"})
_STEP_SNAPSHOT_KEYS = frozenset(
    {
        "server_attempt_id",
        "prepared_payload_id",
        "protected_ref",
        "snapshot_digest",
        "ordered_manifest_digest",
    }
)
_REQUEST_DATA_KEYS = frozenset(
    {
        "via",
        "operation",
        "submission_id",
        "parent_attempt_id",
        "server_attempt_id",
        "prepared_payload_id",
        "protected_ref",
        "billing_unit_id",
        "item_id",
        "unit_key",
        "step_graph_version",
        "request_digest",
        "rule_digest",
        "snapshot_digest",
        "mode",
        "model_id",
        "model_source",
        "model_owner_id",
        "model_display_name",
        "prompt_id",
        "size",
        "chapter_id",
        "asset_type",
        "asset_id",
        "asset_name",
        "reference_images_version",
        "reference_images_count",
        "reference_images_digest",
        "target_ref",
        "model_ref",
        "grant_ref",
        "reference_ref",
        "target_snapshot",
        "context_message_id",
        "context_message_sha256",
    }
)
_LEGACY_MESSAGE_KEYS = frozenset(
    {
        "user_content",
        "assistant_content",
        "assistant_wire_content",
        "model_name",
        "chapter_id",
        "asset_type",
        "asset_id",
        "asset_name",
    }
)
_RAW_ITEM_KEYS = frozenset(
    {
        "type",
        "id",
        "name",
        "description",
        "model_id",
        "prompt_id",
        "size",
        "reference_images",
    }
)
_ADMISSION_POLICY_KEYS = frozenset({"version", "max_concurrent_tasks"})
_ADAPTER_VERSION_KEYS = frozenset(
    {
        "projector",
        "reference_policy",
        "credential_binding_version",
        "endpoint_policy",
        "api_base_path",
    }
)


@dataclass(frozen=True, slots=True)
class PersistedImageBatchExecutionBinding:
    """Task graph identity supplied by the caller's current claim transaction."""

    owner_id: str
    task_id: str
    unit_id: str
    step_id: str
    submission_id: str


@dataclass(frozen=True, slots=True)
class VerifiedImageBatchExecutionInput:
    """One selected batch item reopened from its complete signed parent."""

    task_id: str
    unit_id: str
    step_id: str
    submission_id: str
    item_index: int
    ordered_count: int
    prepared_payload_id: str
    server_attempt_id: str
    task_model_name: str
    snapshot: FrozenExecutionSnapshot = field(repr=False)
    credential_binding: CredentialBinding = field(repr=False)
    provider_credential: BoundCredential = field(repr=False)
    references: tuple[ReferenceInput, ...] = field(default=(), repr=False)


@dataclass(frozen=True, slots=True)
class _PersistedItem:
    raw_key: str
    task: Mapping[str, object]
    unit: Mapping[str, object]
    step: Mapping[str, object]
    request_data: Mapping[str, object]
    step_snapshot: Mapping[str, object]
    payload_row: Mapping[str, object]


def read_original_image_batch_execution_input(
    session: Session,
    binding: PersistedImageBatchExecutionBinding,
    *,
    registry: CredentialRegistry,
    crypto: CredentialCrypto,
    proof_authority: HmacProofAuthority,
) -> VerifiedImageBatchExecutionInput | None:
    """Verify the full persisted batch and return only the caller's claimed item.

    The batch order and size come solely from the committed submission graph.
    This read performs no mutation and does not authorize provider execution.
    """

    if (
        not isinstance(session, Session)
        or type(binding) is not PersistedImageBatchExecutionBinding
        or type(registry) is not CredentialRegistry
        or type(crypto) is not CredentialCrypto
        or type(proof_authority) is not HmacProofAuthority
        or not crypto.ready()
        or not _valid_binding(binding)
    ):
        return None

    try:
        with session.no_autoflush:
            persisted = _read_persisted_batch(session, binding)
        if persisted is None:
            return None

        opened = _open_batch_payloads(
            persisted,
            crypto=crypto,
            proof_authority=proof_authority,
        )
        if opened is None:
            return None

        snapshots: list[FrozenExecutionSnapshot] = []
        references: list[tuple[ReferenceInput, ...]] = []
        command_manifest = opened[0].get("command_manifest")
        if not isinstance(command_manifest, Mapping):
            return None
        canonical_request = command_manifest.get("canonical_request")
        preparation = command_manifest.get("preparation")
        if not isinstance(canonical_request, Mapping) or not isinstance(preparation, Mapping):
            return None
        prepared_items = preparation.get("items")
        if not isinstance(prepared_items, list) or len(prepared_items) != len(persisted):
            return None
        for index, item in enumerate(persisted):
            payload_item = opened[index]["item"]
            snapshot = _decode_execution_snapshot(payload_item.get("execution_snapshot"))
            item_references = _reference_inputs(payload_item)
            if (
                snapshot is None
                or item_references is None
                or not isinstance(prepared_items[index], Mapping)
                or not _snapshot_matches_item(
                    snapshot,
                    payload_item,
                    item,
                    prepared_item=prepared_items[index],
                    canonical_request=canonical_request,
                )
            ):
                return None
            snapshots.append(snapshot)
            references.append(item_references)

        if not _graph_matches(persisted, snapshots):
            return None
        with session.no_autoflush:
            messages = _read_messages(session, persisted)
        if messages is None or not _messages_match(persisted, opened, messages):
            return None

        selected_index = next(
            index for index, item in enumerate(persisted) if item.task["id"] == binding.task_id
        )
        selected = persisted[selected_index]
        payload_item = opened[selected_index]["item"]
        row = selected.payload_row
        credential = payload_item["credential"]
        provenance = credential["version_provenance"]
        if not isinstance(credential, Mapping) or not isinstance(provenance, Mapping):
            return None
        credential_binding = CredentialBinding(
            source=str(row["source"]),
            row_id=str(row["row_id"]),
            owner_or_system=str(row["owner_or_system"]),
            provider=str(row["provider"]),
            original_principal_id=str(row["principal_id"]),
            endpoint_origin=str(credential["endpoint_origin"]),
            allowed_content_paths=tuple(credential["allowed_content_paths"]),
            binding_version=str(row["binding_version"]),
            rotation_provenance=provenance["rotation_proof_ref"],
        )
        provider_credential = registry.resolve_original_credential(
            credential_binding,
            "create",
        )
        if (
            type(provider_credential) is not BoundCredential
            or provider_credential.provider != row["provider"]
            or provider_credential.principal_id != row["principal_id"]
        ):
            return None

        request_data = selected.request_data
        return VerifiedImageBatchExecutionInput(
            task_id=binding.task_id,
            unit_id=binding.unit_id,
            step_id=binding.step_id,
            submission_id=binding.submission_id,
            item_index=selected_index,
            ordered_count=len(persisted),
            prepared_payload_id=str(request_data["prepared_payload_id"]),
            server_attempt_id=str(request_data["server_attempt_id"]),
            task_model_name=str(request_data["model_display_name"]),
            snapshot=snapshots[selected_index],
            credential_binding=credential_binding,
            provider_credential=provider_credential,
            references=references[selected_index],
        )
    except (GenerationError, KeyError, TypeError, ValueError, UnicodeError):
        return None
    except Exception:
        return None


def _valid_binding(binding: PersistedImageBatchExecutionBinding) -> bool:
    return all(
        isinstance(value, str) and bool(value)
        for value in (
            binding.owner_id,
            binding.task_id,
            binding.unit_id,
            binding.step_id,
            binding.submission_id,
        )
    )


def _read_persisted_batch(
    session: Session,
    binding: PersistedImageBatchExecutionBinding,
) -> tuple[_PersistedItem, ...] | None:
    submission = session.execute(
        select(TaskSubmissionRow.__table__).where(
            TaskSubmissionRow.id == binding.submission_id
        )
    ).mappings().one_or_none()
    if submission is None:
        return None
    task_ids = submission["task_ids"]
    if (
        submission["user_id"] != binding.owner_id
        or submission["operation"] != _OPERATION
        or submission["task_quote_id"] is not None
        or not isinstance(submission["idempotency_key"], str)
        or not submission["idempotency_key"]
        or not isinstance(task_ids, list)
        or not 1 <= len(task_ids) <= _MAX_BATCH_ITEMS
        or any(not isinstance(value, str) or not value for value in task_ids)
        or len(set(task_ids)) != len(task_ids)
        or not _is_digest(submission["request_digest"])
    ):
        return None

    task_rows = session.execute(
        select(AITaskRow.__table__).where(AITaskRow.id.in_(task_ids))
    ).mappings().all()
    tasks_by_id = {row["id"]: dict(row) for row in task_rows}
    if len(tasks_by_id) != len(task_ids) or set(tasks_by_id) != set(task_ids):
        return None
    ordered_tasks = [tasks_by_id[task_id] for task_id in task_ids]

    unit_rows = session.execute(
        select(BillingUnitRow.__table__).where(BillingUnitRow.task_id.in_(task_ids))
    ).mappings().all()
    units_by_task: dict[str, list[dict[str, object]]] = {}
    for row in unit_rows:
        units_by_task.setdefault(row["task_id"], []).append(dict(row))
    if set(units_by_task) != set(task_ids) or any(
        len(rows) != 1 for rows in units_by_task.values()
    ):
        return None
    ordered_units = [units_by_task[task_id][0] for task_id in task_ids]
    unit_ids = [str(unit["id"]) for unit in ordered_units]
    if len(set(unit_ids)) != len(unit_ids):
        return None

    step_rows = session.execute(
        select(ExecutionStepRow.__table__).where(
            ExecutionStepRow.billing_unit_id.in_(unit_ids)
        )
    ).mappings().all()
    steps_by_unit: dict[str, list[dict[str, object]]] = {}
    for row in step_rows:
        steps_by_unit.setdefault(row["billing_unit_id"], []).append(dict(row))
    if set(steps_by_unit) != set(unit_ids) or any(
        len(rows) != 1 for rows in steps_by_unit.values()
    ):
        return None
    ordered_steps = [steps_by_unit[unit_id][0] for unit_id in unit_ids]

    if not any(
        task["id"] == binding.task_id
        and unit["id"] == binding.unit_id
        and step["id"] == binding.step_id
        for task, unit, step in zip(ordered_tasks, ordered_units, ordered_steps, strict=True)
    ):
        return None

    request_data_by_task: list[dict[str, object]] = []
    step_snapshots: list[dict[str, object]] = []
    payload_ids: list[str] = []
    for index, (task, unit, step) in enumerate(
        zip(ordered_tasks, ordered_units, ordered_steps, strict=True)
    ):
        request_data = _decode_json_mapping(task["request_data"])
        input_snapshot = _decode_json_mapping(step["input_snapshot"])
        if request_data is None or input_snapshot is None:
            return None
        if (
            set(request_data) != _REQUEST_DATA_KEYS
            or set(input_snapshot) != _STEP_SNAPSHOT_KEYS
            or task["user_id"] != binding.owner_id
            or task["type"] != _TASK_TYPE
            or not isinstance(task["model_name"], str)
            or not task["model_name"]
            or type(task["credit_cost"]) is not int
            or unit["task_id"] != task["id"]
            or unit["operation"] != _OPERATION
            or unit["step_graph_version"] != _GRAPH_VERSION
            or unit["item_key"] != f"{binding.submission_id}:{index}"
            or unit["input_digest"] != submission["request_digest"]
            or type(unit["quoted_amount"]) is not int
            or unit["quoted_amount"] < 0
            or task["credit_cost"] != unit["quoted_amount"]
            or step["billing_unit_id"] != unit["id"]
            or step["step_key"] != "generate"
            or step["required"] is not True
            or step["step_order"] != 0
            or step["dependencies"] not in ([], ())
            or step["input_digest"] != submission["request_digest"]
            or request_data.get("via") != "worker"
            or request_data.get("operation") != _OPERATION
            or request_data.get("submission_id") != binding.submission_id
            or request_data.get("parent_attempt_id") != binding.submission_id
            or request_data.get("server_attempt_id") != f"{binding.submission_id}:{index}"
            or request_data.get("prepared_payload_id") is None
            or request_data.get("protected_ref")
            != _protected_payload_reference(request_data.get("prepared_payload_id"))
            or request_data.get("billing_unit_id") != unit["id"]
            or request_data.get("item_id") != request_data.get("server_attempt_id")
            or request_data.get("unit_key") != unit["item_key"]
            or request_data.get("step_graph_version") != _GRAPH_VERSION
            or request_data.get("request_digest") != submission["request_digest"]
            or request_data.get("snapshot_digest")
            != input_snapshot.get("snapshot_digest")
            or input_snapshot.get("server_attempt_id")
            != request_data.get("server_attempt_id")
            or input_snapshot.get("prepared_payload_id")
            != request_data.get("prepared_payload_id")
            or input_snapshot.get("protected_ref") != request_data.get("protected_ref")
            or not _is_digest(request_data.get("rule_digest"))
            or not _is_digest(request_data.get("snapshot_digest"))
            or not _is_digest(input_snapshot.get("ordered_manifest_digest"))
            or request_data.get("target_ref")
            != f"{request_data['protected_ref']}#target"
            or request_data.get("model_ref")
            != f"{request_data['protected_ref']}#model"
            or request_data.get("grant_ref")
            != f"{request_data['protected_ref']}#grant"
            or request_data.get("reference_ref")
            != f"{request_data['protected_ref']}#references"
            or request_data.get("context_message_id") == task["message_id"]
            or not isinstance(request_data.get("context_message_id"), str)
            or not _is_digest(request_data.get("context_message_sha256"))
            or not isinstance(request_data.get("model_id"), str)
            or not request_data.get("model_id")
            or request_data.get("model_source") not in {"system", "user"}
            or (
                request_data.get("model_owner_id") is not None
                if request_data.get("model_source") == "system"
                else request_data.get("model_owner_id") != binding.owner_id
            )
            or not isinstance(request_data.get("model_display_name"), str)
            or request_data.get("model_display_name") != task["model_name"]
            or request_data.get("mode") != "batch_image"
            or type(request_data.get("reference_images_version")) is not int
            or request_data.get("reference_images_version") != 1
            or type(request_data.get("reference_images_count")) is not int
            or not _is_digest(request_data.get("reference_images_digest"))
        ):
            return None
        payload_id = request_data["prepared_payload_id"]
        if not isinstance(payload_id, str) or not payload_id:
            return None
        payload_ids.append(payload_id)
        request_data_by_task.append(request_data)
        step_snapshots.append(input_snapshot)

    if len(set(payload_ids)) != len(payload_ids):
        return None
    manifest_digests = {snapshot["ordered_manifest_digest"] for snapshot in step_snapshots}
    if len(manifest_digests) != 1:
        return None

    payload_rows = session.execute(
        select(prepared_payloads).where(
            prepared_payloads.c.prepared_payload_id.in_(payload_ids)
        )
    ).mappings().all()
    rows_by_id = {row["prepared_payload_id"]: dict(row) for row in payload_rows}
    if len(rows_by_id) != len(payload_ids) or set(rows_by_id) != set(payload_ids):
        return None

    result: list[_PersistedItem] = []
    for index, (task, unit, step, request_data, step_snapshot, payload_id) in enumerate(
        zip(
            ordered_tasks,
            ordered_units,
            ordered_steps,
            request_data_by_task,
            step_snapshots,
            payload_ids,
            strict=True,
        )
    ):
        payload_row = rows_by_id[payload_id]
        if not _payload_row_matches_graph(
            payload_row,
            request_data,
            owner_id=binding.owner_id,
            operation=_OPERATION,
        ):
            return None
        result.append(
            _PersistedItem(
                raw_key=submission["idempotency_key"],
                task=task,
                unit=unit,
                step=step,
                request_data=request_data,
                step_snapshot=step_snapshot,
                payload_row=payload_row,
            )
        )
    return tuple(result)


def _payload_row_matches_graph(
    row: Mapping[str, object],
    request_data: Mapping[str, object],
    *,
    owner_id: str,
    operation: str,
) -> bool:
    return all(
        (
            row.get("prepared_payload_id") == request_data.get("prepared_payload_id"),
            row.get("actor_id") == owner_id,
            row.get("operation") == operation,
            row.get("server_attempt_id") == request_data.get("server_attempt_id"),
            row.get("context_fingerprint") == request_data.get("request_digest"),
            row.get("target_ref") == request_data.get("target_ref"),
            row.get("model_ref") == request_data.get("model_ref"),
            row.get("grant_ref") == request_data.get("grant_ref"),
            isinstance(row.get("command_proof"), str) and _is_digest(row["command_proof"]),
            isinstance(row.get("ciphertext"), bytes) and bool(row["ciphertext"]),
            isinstance(row.get("nonce"), bytes) and bool(row["nonce"]),
            isinstance(row.get("encryption_key_id"), str)
            and bool(row["encryption_key_id"]),
            isinstance(row.get("schema_version"), str) and bool(row["schema_version"]),
            isinstance(row.get("projector_version"), str)
            and bool(row["projector_version"]),
            isinstance(row.get("reference_policy_version"), str)
            and bool(row["reference_policy_version"]),
            isinstance(row.get("binding_id"), str) and bool(row["binding_id"]),
            type(row.get("binding_version")) is int and row["binding_version"] > 0,
            isinstance(row.get("principal_id"), str) and bool(row["principal_id"]),
            row.get("source") in {"system", "user"},
            isinstance(row.get("row_id"), str) and bool(row["row_id"]),
            isinstance(row.get("owner_or_system"), str)
            and bool(row["owner_or_system"]),
            isinstance(row.get("provider"), str) and bool(row["provider"]),
            row.get("row_id") == request_data.get("model_id"),
            row.get("source") == request_data.get("model_source"),
            row.get("owner_or_system")
            == (request_data.get("model_owner_id") or "system"),
        )
    )


def _open_batch_payloads(
    persisted: tuple[_PersistedItem, ...],
    *,
    crypto: CredentialCrypto,
    proof_authority: HmacProofAuthority,
) -> tuple[dict[str, object], ...] | None:
    count = len(persisted)
    first = persisted[0]
    parent_id = first.request_data["submission_id"]
    request_digest = first.request_data["request_digest"]
    manifest_digest = first.step_snapshot["ordered_manifest_digest"]
    rows = [item.payload_row for item in persisted]
    proofs = {row["command_proof"] for row in rows}
    if len(proofs) != 1:
        return None
    proof = next(iter(proofs))

    opened: list[dict[str, object]] = []
    for index, item in enumerate(persisted):
        row = item.payload_row
        associated_data = stored_payload_associated_data(
            row,
            parent_attempt_id=str(parent_id),
            protected_ref=str(item.request_data["protected_ref"]),
            item_index=index,
            ordered_count=count,
            ordered_manifest_digest=str(manifest_digest),
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
        if payload is None:
            return None
        opened.append(payload)

    command_manifest = opened[0].get("command_manifest")
    if (
        not isinstance(command_manifest, Mapping)
        or set(command_manifest) != _COMMAND_MANIFEST_KEYS
    ):
        return None
    try:
        manifest_bytes = canonical_payload_json(command_manifest)
    except (TypeError, ValueError, UnicodeError):
        return None
    actual_manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    if (
        actual_manifest_digest != manifest_digest
        or not proof_authority.verify(_COMMAND_PROOF_PURPOSE, manifest_bytes, proof)
    ):
        return None

    command_fingerprint = command_manifest.get("fingerprint")
    canonical_request = command_manifest.get("canonical_request")
    preparation = command_manifest.get("preparation")
    if (
        not isinstance(command_fingerprint, str)
        or command_fingerprint != request_digest
        or not isinstance(canonical_request, Mapping)
        or hashlib.sha256(canonical_payload_json(canonical_request)).hexdigest()
        != request_digest
        or not isinstance(preparation, Mapping)
        or set(preparation) != _PREPARATION_KEYS
    ):
        return None
    fingerprint = preparation.get("fingerprint")
    prepared_items = preparation.get("items")
    canonical_assets = canonical_request.get("assets")
    if (
        not isinstance(fingerprint, Mapping)
        or set(fingerprint) != _FINGERPRINT_KEYS
        or fingerprint.get("ordered_nonsecret_payload_digest") != request_digest
        or fingerprint.get("actor_id") != first.payload_row["actor_id"]
        or fingerprint.get("operation") != _OPERATION
        or fingerprint.get("schema_version") != first.payload_row["schema_version"]
        or not isinstance(prepared_items, list)
        or len(prepared_items) != count
        or canonical_request.get("operation") != _OPERATION
        or not isinstance(canonical_request.get("chapter_id"), str)
        or not canonical_request.get("chapter_id")
        or not isinstance(canonical_assets, list)
        or len(canonical_assets) != count
        or set(canonical_request) != {"operation", "chapter_id", "assets"}
    ):
        return None

    child_ids = [item.request_data["server_attempt_id"] for item in persisted]
    payload_ids = [item.request_data["prepared_payload_id"] for item in persisted]
    protected_refs = [item.request_data["protected_ref"] for item in persisted]
    item_digests: list[str] = []
    for index, (payload, durable, row, prepared_item) in enumerate(
        zip(opened, persisted, rows, prepared_items, strict=True)
    ):
        if set(payload) != _PAYLOAD_KEYS:
            return None
        protected_item = payload.get("item")
        if (
            not isinstance(protected_item, Mapping)
            or set(protected_item) != _PROTECTED_ITEM_KEYS
            or not isinstance(protected_item.get("raw_item"), Mapping)
            or set(protected_item["raw_item"]) != _RAW_ITEM_KEYS
            or not isinstance(protected_item.get("admission_policy"), Mapping)
            or set(protected_item["admission_policy"]) != _ADMISSION_POLICY_KEYS
            or protected_item["admission_policy"].get("version")
            != "generation-admission-policy-v1"
            or type(
                protected_item["admission_policy"].get("max_concurrent_tasks")
            )
            is not int
            or not isinstance(prepared_item, Mapping)
            or set(prepared_item) != _PREPARATION_ITEM_KEYS
        ):
            return None
        item_digest = hashlib.sha256(canonical_payload_json(protected_item)).hexdigest()
        item_digests.append(item_digest)
        request_data = durable.request_data
        child_id = request_data["server_attempt_id"]
        payload_id = request_data["prepared_payload_id"]
        protected_ref = request_data["protected_ref"]
        if (
            type(payload.get("item_index")) is not int
            or payload.get("item_index") != index
            or type(payload.get("ordered_count")) is not int
            or payload.get("ordered_count") != count
            or payload.get("parent_attempt_id") != parent_id
            or payload.get("child_attempt_id") != child_id
            or payload.get("protected_ref") != protected_ref
            or payload.get("ordered_manifest_digest") != manifest_digest
            or payload.get("manifest_proof") != proof
            or payload.get("graph_version") != _GRAPH_VERSION
            or payload.get("command_manifest") != command_manifest
            or type(protected_item.get("index")) is not int
            or protected_item.get("index") != index
            or protected_item.get("operation") != _OPERATION
            or protected_item.get("fingerprint") != request_digest
            or protected_item.get("raw_key") != command_manifest.get("raw_key")
            or protected_item.get("context_schema") != row["schema_version"]
            or protected_item.get("projector_version") != row["projector_version"]
            or protected_item.get("reference_policy_version")
            != row["reference_policy_version"]
            or protected_item.get("graph_version") != _GRAPH_VERSION
            or type(prepared_item.get("index")) is not int
            or prepared_item.get("index") != index
            or type(prepared_item.get("quote_amount")) is not int
            or prepared_item.get("quote_amount") != durable.unit["quoted_amount"]
            or canonical_assets[index] != protected_item["raw_item"]
            or not _preparation_item_matches_payload(
                protected_item,
                prepared_item,
                canonical_request,
                durable,
            )
        ):
            return None

    ordered_manifest = [
        {
            "index": index,
            "child_attempt_id": child_ids[index],
            "payload_id": payload_ids[index],
            "protected_ref": protected_refs[index],
            "item_digest": item_digests[index],
        }
        for index in range(count)
    ]
    for payload in opened:
        stored_manifest = payload.get("ordered_manifest")
        if not isinstance(stored_manifest, list) or len(stored_manifest) != count:
            return None
        for index, stored_entry in enumerate(stored_manifest):
            if (
                not isinstance(stored_entry, Mapping)
                or set(stored_entry) != _ORDERED_ITEM_KEYS
                or type(stored_entry.get("index")) is not int
                or stored_entry != ordered_manifest[index]
            ):
                return None

    total_quote = sum(item.unit["quoted_amount"] for item in persisted)
    if (
        preparation.get("operation") != _OPERATION
        or type(preparation.get("quoted_total")) is not int
        or preparation.get("quoted_total") != total_quote
        or type(preparation.get("required_balance")) is not int
        or preparation.get("required_balance") != total_quote
        or type(preparation.get("reserved_amount")) is not int
        or preparation.get("reserved_amount") != 0
        or preparation.get("executable") is not False
        or not isinstance(preparation.get("phase"), str)
        or not isinstance(preparation.get("credential_binding_status"), str)
    ):
        return None

    if (
        command_manifest.get("purpose") != _COMMAND_PROOF_PURPOSE
        or command_manifest.get("actor_id") != first.payload_row["actor_id"]
        or command_manifest.get("operation") != _OPERATION
        or command_manifest.get("raw_key") != first.raw_key
        or command_manifest.get("fingerprint") != request_digest
        or command_manifest.get("parent_attempt_id") != parent_id
        or command_manifest.get("children") != child_ids
        or command_manifest.get("payload_ids") != payload_ids
        or command_manifest.get("protected_refs") != protected_refs
        or type(command_manifest.get("ordered_count")) is not int
        or command_manifest.get("ordered_count") != count
        or command_manifest.get("item_digests") != item_digests
        or command_manifest.get("projector_version") != first.payload_row["projector_version"]
        or command_manifest.get("reference_policy_version")
        != first.payload_row["reference_policy_version"]
        or command_manifest.get("graph_version") != _GRAPH_VERSION
        or any(
            item.step_snapshot["ordered_manifest_digest"] != actual_manifest_digest
            for item in persisted
        )
    ):
        return None
    return tuple(opened)


def _canonical_values_match(left: object, right: object) -> bool:
    try:
        return canonical_payload_json(left) == canonical_payload_json(right)
    except (TypeError, ValueError, UnicodeError):
        return False


def _digest_matches(value: object, expected: object) -> bool:
    if not isinstance(expected, str) or not _is_digest(expected):
        return False
    try:
        return hashlib.sha256(canonical_payload_json(value)).hexdigest() == expected
    except (TypeError, ValueError, UnicodeError):
        return False


def _model_snapshot_matches(
    snapshot: FrozenExecutionSnapshot,
    model: Mapping[str, object],
    credential: Mapping[str, object],
) -> bool:
    provider = model.get("provider")
    route = model.get("provider_route")
    wire_model = model.get("wire_model")
    base_url = model.get("base_url")
    parameters = model.get("params")
    effective_parameters = model.get("effective_parameters")
    snapshot_parameters = model.get("snapshot_parameters")
    if (
        not isinstance(provider, str)
        or not provider
        or route != provider.lower()
        or (wire_model is not None and not isinstance(wire_model, str))
        or not isinstance(base_url, str)
        or not isinstance(parameters, Mapping)
        or not isinstance(effective_parameters, Mapping)
        or not isinstance(snapshot_parameters, Mapping)
    ):
        return False
    try:
        parsed = urlsplit(base_url)
    except ValueError:
        return False
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return False
    expected_origin = f"{parsed.scheme}://{parsed.netloc}"
    expected_path = parsed.path or "/"
    expected_model_name = wire_model or ("dall-e-3" if route == "openai" else None)
    merged_parameters = dict(parameters)
    merged_parameters.update(effective_parameters)
    adapters = dict(snapshot.adapter_config_versions)
    return all(
        (
            expected_model_name is not None
            and snapshot.model_name == expected_model_name,
            _canonical_values_match(merged_parameters, snapshot_parameters),
            _canonical_values_match(snapshot_parameters, snapshot.model_params),
            credential.get("endpoint_origin") == expected_origin,
            snapshot.endpoint_policy.provider_origin == expected_origin,
            credential.get("api_base_path") == expected_path,
            adapters.get("api_base_path") == expected_path,
        )
    )


def _target_snapshot_matches(snapshot: TargetSnapshot, target: Mapping[str, object]) -> bool:
    if set(target) != {
        "kind",
        "chapter",
        "storyboard_source_chapter",
        "asset_type",
        "asset",
        "chapter_digest",
        "asset_digest",
    }:
        return False
    chapter = target.get("chapter")
    asset = target.get("asset")
    chapter_text = chapter.get("content") if isinstance(chapter, Mapping) else None
    source_chapter = target.get("storyboard_source_chapter")
    if not isinstance(chapter, Mapping) or not isinstance(asset, Mapping):
        return False
    updated_at = asset.get("updated_at")
    if updated_at is not None and not isinstance(updated_at, str):
        return False
    asset_name = asset.get("name") or asset.get("title")
    try:
        asset_digest = hashlib.sha256(
            canonical_payload_json(
                {
                    "name": asset_name,
                    "description": asset.get("description"),
                    "image_url": asset.get("image_url"),
                    "updated_at": updated_at,
                }
            )
        ).hexdigest()
    except (TypeError, ValueError, UnicodeError):
        return False
    chapter_digest = hashlib.sha256(str(chapter_text or "").encode("utf-8")).hexdigest()
    if target.get("asset_type") == "storyboard":
        if (
            not isinstance(source_chapter, Mapping)
            or source_chapter.get("id") != asset.get("chapter_id")
            or source_chapter.get("series_id") != chapter.get("series_id")
        ):
            return False
    elif source_chapter is not None:
        return False
    return all(
        (
            target.get("kind") == "asset_batch",
            snapshot.kind == "asset_message",
            snapshot.chapter_id == chapter.get("id"),
            snapshot.series_id == chapter.get("series_id"),
            snapshot.series_id == asset.get("series_id"),
            snapshot.asset_type == target.get("asset_type"),
            snapshot.asset_id == asset.get("id"),
            snapshot.full_chapter_digest == target.get("chapter_digest") == chapter_digest,
            snapshot.same_series_target_digest == target.get("asset_digest") == asset_digest,
            snapshot.expected_asset_image_url
            == (str(asset["image_url"]) if asset.get("image_url") is not None else None),
        )
    )


def _grant_snapshot_matches(
    snapshot: FrozenGrantSnapshot,
    grant: Mapping[str, object],
) -> bool:
    actor_id = grant.get("actor_id")
    if not isinstance(actor_id, str) or actor_id != snapshot.actor_id:
        return False
    if grant.get("scope") == "account":
        return (
            set(grant) == {"actor_id", "scope"}
            and snapshot.series_id is None
            and snapshot.team_id is None
            and snapshot.access_provenance == ()
        )
    if set(grant) != {
        "actor_id",
        "series",
        "member",
        "is_owner",
        "is_member",
        "claimed_by",
        "claim_permission",
    }:
        return False
    series_value = grant.get("series")
    member = grant.get("member")
    if not isinstance(series_value, Mapping) or (
        member is not None and not isinstance(member, Mapping)
    ):
        return False
    series_id = series_value.get("id")
    owner_id = series_value.get("user_id")
    team_id = series_value.get("team_id")
    claimed_by = series_value.get("claimed_by")
    if (
        not isinstance(series_id, str)
        or not isinstance(owner_id, str)
        or (team_id is not None and not isinstance(team_id, str))
        or (claimed_by is not None and not isinstance(claimed_by, str))
        or grant.get("claimed_by") != claimed_by
    ):
        return False
    is_owner = owner_id == actor_id
    is_member = member is not None
    if is_member and (
        member.get("team_id") != team_id or member.get("user_id") != actor_id
    ):
        return False
    claim_permission = False
    if claimed_by and claimed_by != actor_id:
        if member is None or not isinstance(member.get("role"), str):
            return False
        claim_permission = can_enter_claimed_series(
            TeamMembership(role=member["role"], permissions=member.get("permissions"))
        )
    if (
        type(grant.get("is_owner")) is not bool
        or grant.get("is_owner") != is_owner
        or type(grant.get("is_member")) is not bool
        or grant.get("is_member") != is_member
        or type(grant.get("claim_permission")) is not bool
        or grant.get("claim_permission") != claim_permission
        or (not is_owner and not is_member)
    ):
        return False
    provenance = ("series_owner",) if is_owner else ("team_member",)
    if claim_permission:
        provenance += ("claimed_series_permission",)
    return (
        snapshot.series_id == series_id
        and snapshot.team_id == team_id
        and snapshot.access_provenance == provenance
    )


def _preparation_item_matches_payload(
    item: Mapping[str, object],
    prepared_item: Mapping[str, object],
    canonical_request: Mapping[str, object],
    durable: _PersistedItem,
) -> bool:
    model = item.get("model")
    raw_item = item.get("raw_item")
    references = item.get("references")
    target = item.get("target")
    prompt = item.get("prompt")
    prepared_model = prepared_item.get("model")
    prepared_target = prepared_item.get("target")
    if not all(
        isinstance(value, Mapping)
        for value in (
            model,
            raw_item,
            references,
            target,
            prompt,
            prepared_model,
            prepared_target,
        )
    ):
        return False
    assert isinstance(model, Mapping)
    assert isinstance(raw_item, Mapping)
    assert isinstance(references, Mapping)
    assert isinstance(target, Mapping)
    assert isinstance(prompt, Mapping)
    assert isinstance(prepared_model, Mapping)
    assert isinstance(prepared_target, Mapping)

    prepared_quote = prepared_model.get("quote")
    model_parameters = model.get("params")
    effective_parameters = model.get("effective_parameters")
    snapshot_parameters = model.get("snapshot_parameters")
    prepared_parameters = prepared_item.get("effective_parameters")
    if not all(
        isinstance(value, Mapping)
        for value in (
            prepared_quote,
            model_parameters,
            effective_parameters,
            snapshot_parameters,
            prepared_parameters,
        )
    ):
        return False
    assert isinstance(prepared_quote, Mapping)
    assert isinstance(model_parameters, Mapping)
    assert isinstance(effective_parameters, Mapping)
    assert isinstance(snapshot_parameters, Mapping)
    assert isinstance(prepared_parameters, Mapping)
    if (
        set(prepared_model)
        != {"catalog_id", "category", "source", "display_name", "wire_model", "quote"}
        or set(prepared_quote)
        != {"category", "source", "amount", "effective_parameters"}
        or set(prepared_target)
        != {
            "kind",
            "series_id",
            "chapter_id",
            "asset_type",
            "asset_id",
            "frame_index",
            "chapter_digest",
            "asset_digest",
            "identity_mode",
            "expected_chapter_digest",
            "canonical_chapter_digest",
        }
        or type(prepared_item.get("credential_configured")) is not bool
        or any(
            prepared_target.get(field_name) is not None
            for field_name in (
                "frame_index",
                "identity_mode",
                "expected_chapter_digest",
                "canonical_chapter_digest",
            )
        )
    ):
        return False

    quote_amount = model.get("quote_amount")
    prepared_quote_amount = prepared_item.get("quote_amount")
    quote_amount_nested = prepared_quote.get("amount")
    if (
        type(quote_amount) is not int
        or type(prepared_quote_amount) is not int
        or type(quote_amount_nested) is not int
        or quote_amount != prepared_quote_amount
        or quote_amount != quote_amount_nested
        or quote_amount != durable.unit["quoted_amount"]
        or prepared_quote.get("category") != model.get("category")
        or prepared_quote.get("source") != model.get("source")
        or prepared_model.get("catalog_id") != model.get("row_id")
        or prepared_model.get("category") != model.get("category")
        or prepared_model.get("source") != model.get("source")
        or prepared_model.get("display_name") != model.get("display_name")
        or prepared_model.get("wire_model") != model.get("wire_model")
        or not _canonical_values_match(prepared_parameters, effective_parameters)
        or not _canonical_values_match(
            prepared_quote.get("effective_parameters"), prepared_parameters
        )
    ):
        return False

    merged_parameters = dict(model_parameters)
    merged_parameters.update(effective_parameters)
    if not _canonical_values_match(merged_parameters, snapshot_parameters):
        return False

    raw_images = references.get("reference_images")
    reference_counts = prepared_item.get("reference_counts")
    expected_counts = (
        ("image", len(raw_images)),
        ("audio", 0),
        ("video", 0),
    ) if isinstance(raw_images, list) else ()
    if (
        not isinstance(raw_images, list)
        or any(not isinstance(value, str) for value in raw_images)
        or not isinstance(reference_counts, list)
        or any(
            not isinstance(pair, list)
            or len(pair) != 2
            or not isinstance(pair[0], str)
            or type(pair[1]) is not int
            for pair in reference_counts
        )
        or tuple(tuple(pair) for pair in reference_counts) != expected_counts
    ):
        return False

    chapter = target.get("chapter")
    asset = target.get("asset")
    if not isinstance(chapter, Mapping) or not isinstance(asset, Mapping):
        return False
    expected_target_values = (
        prepared_target.get("kind") == target.get("kind") == "asset_batch",
        prepared_target.get("chapter_id") == canonical_request.get("chapter_id")
        == chapter.get("id"),
        prepared_target.get("series_id") == chapter.get("series_id")
        == asset.get("series_id"),
        prepared_target.get("asset_type") == target.get("asset_type")
        == raw_item.get("type"),
        prepared_target.get("asset_id") == asset.get("id") == raw_item.get("id"),
        prepared_target.get("chapter_digest") == target.get("chapter_digest"),
        prepared_target.get("asset_digest") == target.get("asset_digest"),
        _is_digest(prepared_target.get("chapter_digest")),
        _is_digest(prepared_target.get("asset_digest")),
    )
    if not all(expected_target_values):
        return False

    prepared_prompt_value = prepared_item.get("prompt")
    selection = prompt.get("selection")
    if prepared_prompt_value is None:
        if (
            prompt.get("id") is not None
            or selection != {"source": "request"}
            or raw_item.get("prompt_id") not in (None, "")
        ):
            return False
    else:
        if not isinstance(prepared_prompt_value, Mapping) or not isinstance(selection, Mapping):
            return False
        if (
            set(prepared_prompt_value)
            != {"prompt_id", "operation", "source", "category", "digest"}
            or prompt.get("id") != prepared_prompt_value.get("prompt_id")
            or raw_item.get("prompt_id") != prepared_prompt_value.get("prompt_id")
            or prepared_prompt_value.get("operation") != "image.batch"
            or prepared_prompt_value.get("prompt_id") != selection.get("prompt_id")
            or prepared_prompt_value.get("operation") != selection.get("operation")
            or prepared_prompt_value.get("source") != selection.get("source")
            or prepared_prompt_value.get("category") != selection.get("category")
            or not isinstance(selection.get("system_prompt"), str)
            or not isinstance(selection.get("user_prompt"), str)
            or not _digest_matches(
                {
                    "system_prompt": selection["system_prompt"],
                    "user_prompt": selection["user_prompt"],
                },
                prepared_prompt_value.get("digest"),
            )
        ):
            return False
    return _is_digest(prepared_item.get("prompt_digest"))


def _prompt_snapshot_matches(
    snapshot: FrozenExecutionSnapshot,
    prompt: Mapping[str, object],
    prepared_item: Mapping[str, object],
    raw_item: Mapping[str, object],
) -> bool:
    selection_value = prompt.get("selection")
    if selection_value == {"source": "request"}:
        selection = None
    else:
        if not isinstance(selection_value, Mapping) or set(selection_value) != {
            "prompt_id",
            "owner_or_system",
            "operation",
            "source",
            "category",
            "display_name",
            "system_prompt",
            "user_prompt",
        }:
            return False
        try:
            selection = PromptSelection(
                prompt_id=selection_value["prompt_id"],
                operation=PromptOperation(selection_value["operation"]),
                source=ModelSource(selection_value["source"]),
                category=selection_value["category"],
                display_name=selection_value["display_name"],
                system_prompt=selection_value["system_prompt"],
                user_prompt=selection_value["user_prompt"],
            )
        except (TypeError, ValueError):
            return False
        expected_owner_or_system = (
            snapshot.grant_snapshot.actor_id
            if selection.source is ModelSource.USER
            else "system"
        )
        if selection_value.get("owner_or_system") != expected_owner_or_system:
            return False
    description = raw_item.get("description") or raw_item.get("name")
    if not isinstance(description, str):
        return False
    try:
        effective_prompt = compose_batch_image_prompt(selection, description=description)
    except (TypeError, ValueError):
        return False
    digest = hashlib.sha256(effective_prompt.encode("utf-8")).hexdigest()
    return all(
        (
            prompt.get("effective_prompt") == effective_prompt,
            snapshot.effective_prompt == effective_prompt,
            snapshot.prompt_ref == prompt.get("id"),
            prepared_item.get("prompt_digest") == digest,
        )
    )


def _snapshot_matches_item(
    snapshot: FrozenExecutionSnapshot,
    item: Mapping[str, object],
    durable: _PersistedItem,
    *,
    prepared_item: Mapping[str, object],
    canonical_request: Mapping[str, object],
) -> bool:
    model = item.get("model")
    prompt = item.get("prompt")
    credential = item.get("credential")
    projection = item.get("request_data_projection")
    references = item.get("references")
    raw_item = item.get("raw_item")
    target = item.get("target")
    grant = item.get("grant")
    if (
        not isinstance(model, Mapping)
        or not isinstance(prompt, Mapping)
        or not isinstance(credential, Mapping)
        or not isinstance(projection, Mapping)
        or not isinstance(references, Mapping)
        or not isinstance(raw_item, Mapping)
        or not isinstance(target, Mapping)
        or not isinstance(grant, Mapping)
        or set(model) != _MODEL_KEYS
        or set(prompt) != _PROMPT_KEYS
        or set(credential) != _CREDENTIAL_KEYS
        or set(projection) != _REQUEST_PROJECTION_KEYS
        or _reference_inputs(item) is None
    ):
        return False
    request_data = durable.request_data
    row = durable.payload_row
    scope = {
        "source": row["source"],
        "row_id": row["row_id"],
        "owner_or_system": row["owner_or_system"],
        "provider": row["provider"],
    }
    provenance = credential.get("version_provenance")
    paths = credential.get("allowed_content_paths")
    adapters = dict(snapshot.adapter_config_versions)
    if not isinstance(provenance, Mapping) or not isinstance(paths, list):
        return False

    target_value = _target_snapshot_value(snapshot.target_snapshot)
    raw_reference_images = raw_item.get("reference_images")
    prompt_id = raw_item.get("prompt_id") or None
    raw_size = raw_item.get("size")
    target_snapshot = snapshot.target_snapshot
    raw_description = raw_item.get("description") or raw_item.get("name")
    legacy = item.get("legacy_message_projection")
    if not isinstance(legacy, Mapping) or set(legacy) != _LEGACY_MESSAGE_KEYS:
        return False
    legacy_user_content = legacy.get("user_content")
    if (
        not isinstance(raw_description, str)
        or not isinstance(legacy_user_content, str)
        or not legacy_user_content.startswith("__CTX__:")
    ):
        return False
    try:
        encoded_context, message_description = legacy_user_content.removeprefix("__CTX__:").split(
            "\n", 1
        )
        context = json.loads(encoded_context, object_pairs_hook=_unique_object)
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(context, Mapping):
        return False
    if not _preparation_item_matches_payload(item, prepared_item, canonical_request, durable):
        return False
    if not _model_snapshot_matches(snapshot, model, credential):
        return False
    if not _target_snapshot_matches(snapshot.target_snapshot, target):
        return False
    if not _grant_snapshot_matches(snapshot.grant_snapshot, grant):
        return False
    if snapshot.grant_snapshot.series_id != target_snapshot.series_id:
        return False
    if not _prompt_snapshot_matches(snapshot, prompt, prepared_item, raw_item):
        return False

    return all(
        (
            _target_snapshot_value(target_snapshot) == projection.get("target_snapshot"),
            projection.get("operation") == _OPERATION,
            projection.get("submission_id") == request_data.get("submission_id"),
            projection.get("request_digest") == request_data.get("request_digest"),
            projection.get("rule_digest") == request_data.get("rule_digest"),
            projection.get("model_id") == request_data.get("model_id"),
            projection.get("model_source") == request_data.get("model_source"),
            projection.get("model_owner_id") == request_data.get("model_owner_id"),
            projection.get("prompt_id") == request_data.get("prompt_id"),
            projection.get("size") == request_data.get("size"),
            projection.get("chapter_id") == request_data.get("chapter_id"),
            projection.get("asset_type") == request_data.get("asset_type"),
            projection.get("asset_id") == request_data.get("asset_id"),
            projection.get("asset_name") == request_data.get("asset_name"),
            projection.get("prepared_payload_id") == request_data.get("prepared_payload_id"),
            projection.get("protected_ref") == request_data.get("protected_ref"),
            projection.get("target_ref") == request_data.get("target_ref"),
            projection.get("model_ref") == request_data.get("model_ref"),
            projection.get("grant_ref") == request_data.get("grant_ref"),
            projection.get("target_snapshot") == target_value,
            projection.get("mode") == "batch_image",
            projection.get("chapter_id") == target_snapshot.chapter_id,
            projection.get("asset_type") == target_snapshot.asset_type,
            projection.get("asset_id")
            == (target_snapshot.asset_id or target_snapshot.stable_storyboard_asset_id),
            projection.get("asset_name") == legacy.get("asset_name"),
            projection.get("reference_images_version") == 1,
            type(projection.get("reference_images_version")) is int,
            projection.get("reference_images_version")
            == request_data.get("reference_images_version"),
            type(projection.get("reference_images_count")) is int,
            projection.get("reference_images_count") == len(raw_reference_images)
            if isinstance(raw_reference_images, list)
            else False,
            projection.get("reference_images_count")
            == request_data.get("reference_images_count"),
            projection.get("reference_images_digest")
            == request_data.get("reference_images_digest"),
            raw_item.get("type") == projection.get("asset_type"),
            raw_item.get("id") == projection.get("asset_id"),
            raw_item.get("model_id") == projection.get("model_id"),
            prompt_id == projection.get("prompt_id"),
            raw_size == projection.get("size"),
            message_description == raw_description,
            context
            == {
                "modelId": projection.get("model_id"),
                "promptId": projection.get("prompt_id") or "",
                "size": projection.get("size"),
                "refImagesCount": projection.get("reference_images_count"),
                "refImagesDigest": projection.get("reference_images_digest"),
            },
            model.get("source") == row["source"] == request_data.get("model_source"),
            model.get("row_id") == row["row_id"] == snapshot.model_ref.model_id,
            model.get("owner_or_system") == row["owner_or_system"] == snapshot.model_ref.owner_id,
            model.get("provider") == row["provider"] == snapshot.model_ref.provider,
            model.get("category") == "image" == snapshot.model_category,
            model.get("display_name") == request_data.get("model_display_name"),
            model.get("snapshot_model_name") == snapshot.model_name,
            model.get("quote_amount") == snapshot.server_quote == durable.unit["quoted_amount"],
            prompt.get("id") == snapshot.prompt_ref == projection.get("prompt_id"),
            prompt.get("effective_prompt") == snapshot.effective_prompt,
            prompt.get("effective_context") == snapshot.effective_context,
            raw_reference_images == references.get("reference_images"),
            target_snapshot.kind == "asset_message",
            snapshot.grant_snapshot.actor_id == row["actor_id"],
            snapshot.reference_snapshot_ref == f"{request_data['protected_ref']}#references",
            snapshot.credential_binding_ref
            == f"{row['binding_id']}:v{row['binding_version']}",
            snapshot.endpoint_policy.provider_origin == credential.get("endpoint_origin"),
            tuple(snapshot.endpoint_policy.allowed_content_paths) == tuple(paths),
            snapshot.endpoint_policy.allow_private_network is False,
            len(snapshot.adapter_config_versions) == len(_ADAPTER_VERSION_KEYS),
            set(adapters) == _ADAPTER_VERSION_KEYS,
            adapters.get("credential_binding_version") == str(row["binding_version"]),
            adapters.get("endpoint_policy") == credential.get("endpoint_policy_version"),
            adapters.get("api_base_path") == credential.get("api_base_path"),
            adapters.get("projector") == row["projector_version"],
            adapters.get("reference_policy") == row["reference_policy_version"],
            credential.get("scope") == scope,
            credential.get("binding_id") == row["binding_id"],
            credential.get("binding_version") == row["binding_version"],
            credential.get("principal_id") == row["principal_id"],
            credential.get("record_id") == provenance.get("protected_record_id"),
            provenance.get("binding_version") == row["binding_version"],
            provenance.get("actor_id") == row["actor_id"],
            provenance.get("rotation_proof_ref") is None
            or isinstance(provenance.get("rotation_proof_ref"), str),
        )
    )


def _graph_matches(
    persisted: tuple[_PersistedItem, ...],
    snapshots: list[FrozenExecutionSnapshot],
) -> bool:
    try:
        plan = build_operation_graph_plan(
            _OPERATION,
            graph_version=_GRAPH_VERSION,
            items=tuple(
                GraphItemInput(
                    item_id=str(item.request_data["server_attempt_id"]),
                    target_id=(
                        snapshot.target_snapshot.asset_id
                        or snapshot.target_snapshot.stable_storyboard_asset_id
                        or snapshot.target_snapshot.chapter_id
                        or str(item.request_data["server_attempt_id"])
                    ),
                    task_id=str(item.task["id"]),
                    unit_id=str(item.unit["id"]),
                    step_ids=(str(item.step["id"]),),
                    request_digest=str(item.request_data["request_digest"]),
                    rule_digest=str(item.request_data["rule_digest"]),
                    snapshot=snapshot,
                    message_id=str(item.task["message_id"]),
                )
                for item, snapshot in zip(persisted, snapshots, strict=True)
            ),
        )
    except (GenerationError, TypeError, ValueError):
        return False
    if len(plan.items) != len(persisted) or len(plan.tasks) != len(persisted):
        return False
    for index, (plan_item, plan_task, item, snapshot) in enumerate(
        zip(plan.items, plan.tasks, persisted, snapshots, strict=True)
    ):
        if (
            plan_item.order != index
            or plan_item.task_id != item.task["id"]
            or plan_item.unit_id != item.unit["id"]
            or plan_item.item_id != item.request_data["server_attempt_id"]
            or plan_item.snapshot_digest != item.request_data["snapshot_digest"]
            or plan_task.order != index
            or plan_task.task_id != item.task["id"]
            or plan_task.task_type != _TASK_TYPE
            or plan_task.graph_version != _GRAPH_VERSION
            or plan_task.item_ids != (item.request_data["server_attempt_id"],)
            or plan.units[index].unit_id != item.unit["id"]
            or plan.units[index].quoted_amount != snapshot.server_quote
            or item.step_snapshot["snapshot_digest"] != plan_item.snapshot_digest
        ):
            return False
    return True


def _read_messages(
    session: Session,
    persisted: tuple[_PersistedItem, ...],
) -> Mapping[str, Mapping[str, object]] | None:
    message_ids = [
        value
        for item in persisted
        for value in (item.request_data["context_message_id"], item.task["message_id"])
    ]
    if (
        any(not isinstance(value, str) or not value for value in message_ids)
        or len(set(message_ids)) != len(message_ids)
    ):
        return None
    rows = session.execute(
        select(
            chat_messages.c.id,
            chat_messages.c.chapter_id,
            chat_messages.c.asset_type,
            chat_messages.c.asset_id,
            chat_messages.c.chat_mode,
            chat_messages.c.role,
            chat_messages.c.content,
            chat_messages.c.model_name,
        ).where(chat_messages.c.id.in_(message_ids))
    ).mappings().all()
    by_id = {row["id"]: dict(row) for row in rows}
    if len(by_id) != len(message_ids) or set(by_id) != set(message_ids):
        return None
    return by_id


def _messages_match(
    persisted: tuple[_PersistedItem, ...],
    payloads: tuple[dict[str, object], ...],
    messages: Mapping[str, Mapping[str, object]],
) -> bool:
    for durable, payload in zip(persisted, payloads, strict=True):
        item = payload["item"]
        legacy = item.get("legacy_message_projection")
        request_data = durable.request_data
        if not isinstance(legacy, Mapping) or set(legacy) != _LEGACY_MESSAGE_KEYS:
            return False
        user_message = messages[request_data["context_message_id"]]
        assistant_message = messages[durable.task["message_id"]]
        if not all(
            (
                user_message["role"] == "user",
                user_message["chapter_id"] == request_data["chapter_id"],
                user_message["asset_type"] == request_data["asset_type"],
                user_message["asset_id"] == request_data["asset_id"],
                user_message["chat_mode"] == "image",
                user_message["content"] == legacy.get("user_content"),
                hashlib.sha256(str(user_message["content"]).encode("utf-8")).hexdigest()
                == request_data["context_message_sha256"],
                user_message["model_name"] is None,
                assistant_message["role"] == "assistant",
                assistant_message["chapter_id"] == request_data["chapter_id"],
                assistant_message["asset_type"] == request_data["asset_type"],
                assistant_message["asset_id"] == request_data["asset_id"],
                assistant_message["chat_mode"] == "image",
                isinstance(assistant_message["content"], str),
                assistant_message["model_name"] == legacy.get("model_name"),
                legacy.get("model_name") == durable.task["model_name"],
                legacy.get("assistant_wire_content") is None,
                legacy.get("chapter_id") == request_data["chapter_id"],
                legacy.get("asset_type") == request_data["asset_type"],
                legacy.get("asset_id") == request_data["asset_id"],
                legacy.get("asset_name") == request_data["asset_name"],
            )
        ):
            return False
    return True


def _reference_inputs(item: Mapping[str, object]) -> tuple[ReferenceInput, ...] | None:
    references = item.get("references")
    raw_item = item.get("raw_item")
    projection = item.get("request_data_projection")
    if (
        not isinstance(references, Mapping)
        or set(references) != _REFERENCE_KEYS
        or not isinstance(raw_item, Mapping)
        or not isinstance(projection, Mapping)
    ):
        return None
    images = references.get("reference_images")
    raw_images = raw_item.get("reference_images")
    if (
        not isinstance(images, list)
        or any(not isinstance(value, str) for value in images)
        or not isinstance(raw_images, list)
        or any(not isinstance(value, str) for value in raw_images)
        or raw_images != images
        or raw_item.get("reference_audios") not in (None, [])
        or raw_item.get("reference_videos") not in (None, [])
        or type(projection.get("reference_images_version")) is not int
        or projection.get("reference_images_version") != 1
        or type(projection.get("reference_images_count")) is not int
        or projection.get("reference_images_count") != len(images)
    ):
        return None
    try:
        digest_input = json.dumps(
            images,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        return None
    if projection.get("reference_images_digest") != hashlib.sha256(digest_input).hexdigest():
        return None
    return tuple(
        ReferenceInput(
            kind="data_uri" if value.startswith("data:") else "public_http",
            locator=value,
        )
        for value in images
    )


def _decode_execution_snapshot(value: object) -> FrozenExecutionSnapshot | None:
    if not isinstance(value, Mapping):
        return None
    if set(value) != {item.name for item in fields(FrozenExecutionSnapshot)}:
        return None
    try:
        model_value = value["model_ref"]
        target_value = value["target_snapshot"]
        grant_value = value["grant_snapshot"]
        endpoint_value = value["endpoint_policy"]
        if not all(
            isinstance(item, Mapping)
            for item in (model_value, target_value, grant_value, endpoint_value)
        ):
            return None
        model = _decode_dataclass(ModelReference, model_value)
        target = _decode_dataclass(TargetSnapshot, target_value)
        grant = _decode_dataclass(FrozenGrantSnapshot, grant_value)
        endpoint = _decode_dataclass(EndpointPolicy, endpoint_value)
        versions = value["adapter_config_versions"]
        if (
            model is None
            or target is None
            or grant is None
            or endpoint is None
            or not isinstance(versions, list)
            or any(
                not isinstance(pair, list)
                or len(pair) != 2
                or any(not isinstance(part, str) or not part for part in pair)
                for pair in versions
            )
        ):
            return None
        snapshot = FrozenExecutionSnapshot(
            model_ref=model,
            model_category=value["model_category"],
            model_name=value["model_name"],
            model_params=value["model_params"],
            prompt_ref=value["prompt_ref"],
            effective_prompt=value["effective_prompt"],
            effective_context=value["effective_context"],
            server_quote=value["server_quote"],
            reference_snapshot_ref=value["reference_snapshot_ref"],
            target_snapshot=target,
            credential_binding_ref=value["credential_binding_ref"],
            grant_snapshot=grant,
            adapter_config_versions=tuple(tuple(pair) for pair in versions),
            endpoint_policy=endpoint,
        )
    except (KeyError, TypeError, ValueError):
        return None
    if (
        snapshot.model_category != "image"
        or not isinstance(snapshot.model_name, str)
        or not isinstance(snapshot.effective_prompt, str)
        or not isinstance(snapshot.effective_context, str)
        or type(snapshot.server_quote) is not int
        or not isinstance(snapshot.reference_snapshot_ref, str)
        or not isinstance(snapshot.credential_binding_ref, str)
    ):
        return None
    return snapshot


def _decode_dataclass(record_type: type[Any], value: Mapping[str, object]) -> Any | None:
    if set(value) != {item.name for item in fields(record_type)}:
        return None
    decoded = dict(value)
    if record_type is FrozenGrantSnapshot:
        provenance = decoded.get("access_provenance")
        if not isinstance(provenance, list) or any(
            not isinstance(item, str) for item in provenance
        ):
            return None
        decoded["access_provenance"] = tuple(provenance)
    elif record_type is EndpointPolicy:
        paths = decoded.get("allowed_content_paths")
        if not isinstance(paths, list) or any(not isinstance(item, str) for item in paths):
            return None
        decoded["allowed_content_paths"] = tuple(paths)
        if type(decoded.get("allow_private_network")) is not bool:
            return None
    elif record_type is ModelReference and any(
        not isinstance(decoded.get(key), str)
        for key in ("owner_id", "provider", "model_id")
    ):
        return None
    elif record_type is TargetSnapshot and not isinstance(decoded.get("kind"), str):
        return None
    try:
        return record_type(**decoded)
    except (TypeError, ValueError):
        return None


def _decode_canonical_object(plaintext: str) -> dict[str, object] | None:
    try:
        decoded = json.loads(plaintext, object_pairs_hook=_unique_object)
        if (
            not isinstance(decoded, dict)
            or canonical_payload_json(decoded).decode("utf-8") != plaintext
        ):
            return None
    except (TypeError, ValueError, UnicodeError):
        return None
    return decoded


def _decode_json_mapping(value: object) -> dict[str, object] | None:
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str):
        return None
    try:
        decoded = json.loads(value, object_pairs_hook=_unique_object)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _target_snapshot_value(target: TargetSnapshot) -> dict[str, object]:
    return {item.name: getattr(target, item.name) for item in fields(TargetSnapshot)}


def _protected_payload_reference(payload_id: object) -> str | None:
    if not isinstance(payload_id, str) or not payload_id:
        return None
    return f"prepared-payload-v1/{len(payload_id)}:{payload_id}"


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


__all__ = [
    "PersistedImageBatchExecutionBinding",
    "VerifiedImageBatchExecutionInput",
    "read_original_image_batch_execution_input",
]
