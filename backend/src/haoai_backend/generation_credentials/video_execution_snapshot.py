"""Reopen the original frozen input for ordinary ``video.single`` execution.

This reader validates only the durable, one-step video graph. It neither claims
work nor grants permission to contact a provider.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.errors import GenerationError
from haoai_backend.generation_contracts.types import (
    BoundCredential,
    CredentialBinding,
    EndpointPolicy,
    FrozenExecutionSnapshot,
    FrozenGrantSnapshot,
    ModelReference,
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
_COMMAND_PROOF_PURPOSE = "prepared-generation-input-v1"
_VIDEO_SINGLE_OPERATION = "video.single"
_VIDEO_SINGLE_GRAPH_VERSION = "video-single-v1"
_VIDEO_SINGLE_TASK_TYPE = "video-single"
_STEP_SNAPSHOT_KEYS = frozenset(
    {
        "server_attempt_id",
        "prepared_payload_id",
        "protected_ref",
        "snapshot_digest",
        "ordered_manifest_digest",
    }
)
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
_REFERENCE_KEYS = frozenset(
    {"reference_images", "reference_audios", "reference_videos"}
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
_PREPARED_ITEM_KEYS = frozenset(
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
_PREPARED_MODEL_KEYS = frozenset(
    {"catalog_id", "category", "source", "display_name", "wire_model", "quote"}
)
_SERVER_QUOTE_KEYS = frozenset(
    {"category", "source", "amount", "effective_parameters"}
)
_TARGET_FACT_KEYS = frozenset(
    {
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
)
_REQUEST_KEYS = frozenset(
    {
        "operation",
        "model_id",
        "prompt",
        "target",
        "size",
        "reference_images",
        "duration",
        "resolution",
        "reference_audios",
        "reference_videos",
    }
)


@dataclass(frozen=True, slots=True)
class PersistedVideoExecutionBinding:
    """Exact SQL graph projection supplied by the current claim transaction."""

    owner_id: str
    task_id: str
    unit_id: str
    step_id: str
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
class VerifiedVideoExecutionInput:
    """Authenticated frozen inputs; this value does not authorize execution."""

    task_id: str
    unit_id: str
    step_id: str
    prepared_payload_id: str
    server_attempt_id: str
    task_model_name: str
    snapshot: FrozenExecutionSnapshot = field(repr=False)
    credential_binding: CredentialBinding = field(repr=False)
    provider_credential: BoundCredential = field(repr=False)
    reference_images: tuple[str, ...] = field(default=(), repr=False)
    reference_audios: tuple[str, ...] = field(default=(), repr=False)
    reference_videos: tuple[str, ...] = field(default=(), repr=False)


def read_original_video_single_execution_input(
    session: Session,
    binding: PersistedVideoExecutionBinding,
    *,
    registry: CredentialRegistry,
    crypto: CredentialCrypto,
    proof_authority: HmacProofAuthority,
) -> VerifiedVideoExecutionInput | None:
    """Reopen the payload bound to one ordinary ``video.single`` graph.

    Legacy step snapshots, HaoAI attempt-group graphs, and video canvas targets
    remain unresolved. The reader performs no repair, accounting, or I/O.
    """

    if (
        not isinstance(session, Session)
        or type(binding) is not PersistedVideoExecutionBinding
        or type(registry) is not CredentialRegistry
        or type(crypto) is not CredentialCrypto
        or type(proof_authority) is not HmacProofAuthority
        or not _valid_binding(binding)
    ):
        return None
    try:
        if not crypto.ready():
            return None
        with session.no_autoflush:
            if not _persisted_graph_matches_binding(session, binding):
                return None
            rows = session.execute(
                select(prepared_payloads).where(
                    prepared_payloads.c.prepared_payload_id == binding.prepared_payload_id,
                    prepared_payloads.c.actor_id == binding.owner_id,
                    prepared_payloads.c.operation == binding.operation,
                    prepared_payloads.c.server_attempt_id == binding.server_attempt_id,
                )
            ).mappings().all()
        if len(rows) != 1:
            return None
        row = dict(rows[0])
        if not _payload_row_matches_binding(row, binding):
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
        if payload is None or not _verify_payload(payload, row, binding, proof_authority):
            return None

        item = payload["item"]
        snapshot = _decode_execution_snapshot(item.get("execution_snapshot"))
        references = _reference_groups_from_item(item)
        if (
            snapshot is None
            or references is None
            or not _persisted_request_projection_matches(session, binding, item)
            or not _snapshot_matches_binding(snapshot, item, binding, row)
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
                    step_ids=(binding.step_id,),
                    request_digest=binding.request_digest,
                    rule_digest=binding.rule_digest,
                    snapshot=snapshot,
                    message_id=binding.task_message_id,
                ),
            ),
        )
        if (
            len(plan.items) != 1
            or len(plan.tasks) != 1
            or len(plan.units) != 1
            or len(plan.steps) != 1
            or plan.graph_version != _VIDEO_SINGLE_GRAPH_VERSION
            or plan.steps[0].step_key != "generate"
            or plan.steps[0].required is not True
            or plan.steps[0].order != 0
            or plan.steps[0].dependencies
            or plan.items[0].snapshot_digest != binding.snapshot_digest
        ):
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
            "create",
        )
        if (
            type(provider_credential) is not BoundCredential
            or provider_credential.provider != row["provider"]
            or provider_credential.principal_id != row["principal_id"]
        ):
            return None
        return VerifiedVideoExecutionInput(
            task_id=binding.task_id,
            unit_id=binding.unit_id,
            step_id=binding.step_id,
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
        # Database, crypto, and registry failures are safe unresolved outcomes.
        return None


def _valid_binding(binding: PersistedVideoExecutionBinding) -> bool:
    text_fields = (
        binding.owner_id,
        binding.task_id,
        binding.unit_id,
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
            binding.operation == _VIDEO_SINGLE_OPERATION,
            binding.graph_version == _VIDEO_SINGLE_GRAPH_VERSION,
            binding.task_type == _VIDEO_SINGLE_TASK_TYPE,
            binding.task_model_source in {"system", "user"},
            binding.server_attempt_id == f"{binding.submission_id}:0",
            type(binding.quoted_amount) is int and binding.quoted_amount >= 0,
            binding.task_message_id is None or isinstance(binding.task_message_id, str),
            _is_digest(binding.request_digest),
            _is_digest(binding.rule_digest),
            _is_digest(binding.snapshot_digest),
            _is_digest(binding.ordered_manifest_digest),
            (
                binding.task_model_source != "system"
                or binding.task_model_owner_id is None
            ),
            (
                binding.task_model_source != "user"
                or binding.task_model_owner_id == binding.owner_id
            ),
        )
    )


def _persisted_graph_matches_binding(
    session: Session,
    binding: PersistedVideoExecutionBinding,
) -> bool:
    task_table = AITaskRow.__table__
    unit_table = BillingUnitRow.__table__
    step_table = ExecutionStepRow.__table__
    submission_table = TaskSubmissionRow.__table__
    task = session.execute(
        select(task_table).where(task_table.c.id == binding.task_id)
    ).mappings().one_or_none()
    units = session.execute(
        select(unit_table).where(unit_table.c.task_id == binding.task_id)
    ).mappings().all()
    if task is None or len(units) != 1 or units[0]["id"] != binding.unit_id:
        return False
    unit = units[0]
    steps = session.execute(
        select(step_table).where(step_table.c.billing_unit_id == binding.unit_id)
    ).mappings().all()
    if len(steps) != 1 or steps[0]["id"] != binding.step_id:
        return False
    step = steps[0]
    submission = session.execute(
        select(submission_table).where(submission_table.c.id == binding.submission_id)
    ).mappings().one_or_none()
    if submission is None:
        return False
    try:
        request_data = json.loads(task["request_data"])
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    input_snapshot = step["input_snapshot"]
    if (
        not isinstance(request_data, Mapping)
        or not isinstance(input_snapshot, Mapping)
        or set(input_snapshot) != _STEP_SNAPSHOT_KEYS
    ):
        return False
    task_message_id = task["message_id"] or None
    return all(
        (
            task["id"] == binding.task_id,
            task["user_id"] == binding.owner_id,
            task["type"] == binding.task_type,
            task["model_name"] == binding.task_model_name,
            type(task["credit_cost"]) is int
            and task["credit_cost"] == binding.quoted_amount,
            task_message_id == binding.task_message_id,
            unit["id"] == binding.unit_id,
            unit["task_id"] == binding.task_id,
            unit["operation"] == binding.operation,
            unit["step_graph_version"] == binding.graph_version,
            unit["item_key"] == binding.server_attempt_id,
            unit["input_digest"] == binding.request_digest,
            type(unit["quoted_amount"]) is int
            and unit["quoted_amount"] == binding.quoted_amount,
            step["id"] == binding.step_id,
            step["billing_unit_id"] == binding.unit_id,
            step["step_key"] == "generate",
            step["required"] is True,
            type(step["step_order"]) is int
            and step["step_order"] == 0,
            step["dependencies"] in ([], ()),
            step["input_digest"] == binding.request_digest,
            input_snapshot.get("server_attempt_id") == binding.server_attempt_id,
            input_snapshot.get("prepared_payload_id") == binding.prepared_payload_id,
            input_snapshot.get("protected_ref") == binding.protected_ref,
            input_snapshot.get("snapshot_digest") == binding.snapshot_digest,
            input_snapshot.get("ordered_manifest_digest")
            == binding.ordered_manifest_digest,
            submission["id"] == binding.submission_id,
            submission["user_id"] == binding.owner_id,
            submission["operation"] == binding.operation,
            submission["idempotency_key"] == binding.raw_key,
            submission["request_digest"] == binding.request_digest,
            submission["task_ids"] == [binding.task_id],
            _request_data_matches_binding(request_data, binding, unit),
        )
    )


def _request_data_matches_binding(
    request_data: Mapping[str, object],
    binding: PersistedVideoExecutionBinding,
    unit: Mapping[str, object],
) -> bool:
    required = {
        "via": "worker",
        "operation": binding.operation,
        "submission_id": binding.submission_id,
        "parent_attempt_id": binding.submission_id,
        "server_attempt_id": binding.server_attempt_id,
        "prepared_payload_id": binding.prepared_payload_id,
        "protected_ref": binding.protected_ref,
        "billing_unit_id": binding.unit_id,
        "item_id": binding.server_attempt_id,
        "unit_key": unit["item_key"],
        "step_graph_version": binding.graph_version,
        "request_digest": binding.request_digest,
        "rule_digest": binding.rule_digest,
        "snapshot_digest": binding.snapshot_digest,
        "mode": binding.operation,
        "model_id": binding.task_model_id,
        "model_source": binding.task_model_source,
        "model_owner_id": binding.task_model_owner_id,
        "model_display_name": binding.task_model_name,
        "target_ref": f"{binding.protected_ref}#target",
        "model_ref": f"{binding.protected_ref}#model",
        "grant_ref": f"{binding.protected_ref}#grant",
        "reference_ref": f"{binding.protected_ref}#references",
    }
    if not all(request_data.get(key) == value for key, value in required.items()):
        return False
    if (
        request_data.get("prompt_id") is not None
        or request_data.get("chapter_id") is not None
        or request_data.get("asset_type") is not None
        or request_data.get("asset_id") is not None
        or request_data.get("asset_name") is not None
        or type(request_data.get("reference_images_version")) is not int
        or request_data.get("reference_images_version") != 1
        or type(request_data.get("reference_images_count")) is not int
        or request_data.get("reference_images_count") < 0
        or not _is_digest(request_data.get("reference_images_digest"))
    ):
        return False
    return isinstance(request_data.get("target_snapshot"), Mapping)


def _persisted_request_projection_matches(
    session: Session,
    binding: PersistedVideoExecutionBinding,
    item: Mapping[str, object],
) -> bool:
    with session.no_autoflush:
        encoded = session.execute(
            select(AITaskRow.__table__.c.request_data).where(
                AITaskRow.__table__.c.id == binding.task_id
            )
        ).scalar_one_or_none()
    try:
        request_data = json.loads(encoded)
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    projection = item.get("request_data_projection")
    if (
        not isinstance(request_data, Mapping)
        or not isinstance(projection, Mapping)
        or set(projection) != _REQUEST_PROJECTION_KEYS
    ):
        return False
    legacy_target_fields = {"chapter_id", "asset_type", "asset_id", "asset_name"}
    if not all(
        request_data.get(key) == projection.get(key)
        for key in _REQUEST_PROJECTION_KEYS - legacy_target_fields
    ):
        return False
    if request_data.get("context_message_id") is None:
        return all(request_data.get(key) is None for key in legacy_target_fields)
    return all(
        request_data.get(key) == projection.get(key)
        for key in legacy_target_fields
    )


def _payload_row_matches_binding(
    row: Mapping[str, object],
    binding: PersistedVideoExecutionBinding,
) -> bool:
    return all(
        (
            row.get("prepared_payload_id") == binding.prepared_payload_id,
            row.get("actor_id") == binding.owner_id,
            row.get("operation") == binding.operation,
            row.get("server_attempt_id") == binding.server_attempt_id,
            row.get("context_fingerprint") == binding.request_digest,
            row.get("target_ref") == f"{binding.protected_ref}#target",
            row.get("model_ref") == f"{binding.protected_ref}#model",
            row.get("grant_ref") == f"{binding.protected_ref}#grant",
            _is_digest(row.get("command_proof")),
            isinstance(row.get("ciphertext"), bytes) and bool(row["ciphertext"]),
            isinstance(row.get("nonce"), bytes) and bool(row["nonce"]),
            isinstance(row.get("encryption_key_id"), str)
            and bool(row["encryption_key_id"]),
            isinstance(row.get("schema_version"), str)
            and bool(row["schema_version"]),
            isinstance(row.get("projector_version"), str)
            and bool(row["projector_version"]),
            isinstance(row.get("reference_policy_version"), str)
            and bool(row["reference_policy_version"]),
            isinstance(row.get("binding_id"), str) and bool(row["binding_id"]),
            type(row.get("binding_version")) is int and row["binding_version"] > 0,
            isinstance(row.get("principal_id"), str) and bool(row["principal_id"]),
            row.get("source") == binding.task_model_source,
            row.get("row_id") == binding.task_model_id,
            row.get("owner_or_system")
            == (binding.task_model_owner_id or "system"),
            isinstance(row.get("provider"), str) and bool(row["provider"]),
        )
    )


def _decode_canonical_object(plaintext: str) -> dict[str, object] | None:
    try:
        value = json.loads(
            plaintext,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
        if (
            not isinstance(value, dict)
            or canonical_payload_json(value).decode("utf-8") != plaintext
        ):
            return None
        return value
    except (TypeError, ValueError, UnicodeError):
        return None


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate protected payload key")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite protected payload number: {value}")


def _verify_payload(
    payload: Mapping[str, object],
    row: Mapping[str, object],
    binding: PersistedVideoExecutionBinding,
    authority: HmacProofAuthority,
) -> bool:
    if set(payload) != _PAYLOAD_KEYS:
        return False
    manifest = payload.get("command_manifest")
    item = payload.get("item")
    if (
        not isinstance(manifest, Mapping)
        or not isinstance(item, Mapping)
        or set(manifest) != _COMMAND_MANIFEST_KEYS
        or set(item) != _PROTECTED_ITEM_KEYS
    ):
        return False
    manifest_bytes = canonical_payload_json(manifest)
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    proof = row["command_proof"]
    item_digest = hashlib.sha256(canonical_payload_json(item)).hexdigest()
    preparation = manifest.get("preparation")
    raw_item = item.get("raw_item")
    if (
        not isinstance(preparation, Mapping)
        or not isinstance(raw_item, Mapping)
        or not isinstance(manifest.get("canonical_request"), Mapping)
        or canonical_payload_json(manifest["canonical_request"])
        != canonical_payload_json(raw_item)
        or manifest_digest != binding.ordered_manifest_digest
        or payload.get("ordered_manifest_digest") != manifest_digest
        or payload.get("manifest_proof") != proof
        or not authority.verify(_COMMAND_PROOF_PURPOSE, manifest_bytes, proof)
    ):
        return False
    ordered_manifest = [
        {
            "index": 0,
            "child_attempt_id": binding.server_attempt_id,
            "payload_id": binding.prepared_payload_id,
            "protected_ref": binding.protected_ref,
            "item_digest": item_digest,
        }
    ]
    if (
        payload.get("parent_attempt_id") != binding.submission_id
        or payload.get("protected_ref") != binding.protected_ref
        or payload.get("child_attempt_id") != binding.server_attempt_id
        or type(payload.get("item_index")) is not int
        or payload.get("item_index") != 0
        or type(payload.get("ordered_count")) is not int
        or payload.get("ordered_count") != 1
        or payload.get("ordered_manifest") != ordered_manifest
        or payload.get("graph_version") != binding.graph_version
        or manifest.get("purpose") != _COMMAND_PROOF_PURPOSE
        or manifest.get("actor_id") != binding.owner_id
        or manifest.get("operation") != binding.operation
        or manifest.get("raw_key") != binding.raw_key
        or manifest.get("fingerprint") != binding.request_digest
        or manifest.get("parent_attempt_id") != binding.submission_id
        or manifest.get("children") != [binding.server_attempt_id]
        or manifest.get("payload_ids") != [binding.prepared_payload_id]
        or manifest.get("protected_refs") != [binding.protected_ref]
        or type(manifest.get("ordered_count")) is not int
        or manifest.get("ordered_count") != 1
        or manifest.get("item_digests") != [item_digest]
        or manifest.get("projector_version") != row["projector_version"]
        or manifest.get("reference_policy_version")
        != row["reference_policy_version"]
        or manifest.get("graph_version") != binding.graph_version
        or item.get("index") != 0
        or item.get("operation") != binding.operation
        or item.get("fingerprint") != binding.request_digest
        or item.get("raw_key") != binding.raw_key
        or item.get("context_schema") != row["schema_version"]
        or item.get("projector_version") != row["projector_version"]
        or item.get("reference_policy_version") != row["reference_policy_version"]
        or item.get("graph_version") != binding.graph_version
        or not _preparation_matches(preparation, item, raw_item, binding, row)
    ):
        return False
    return True


def _preparation_matches(
    preparation: Mapping[str, object],
    item: Mapping[str, object],
    raw_item: Mapping[str, object],
    binding: PersistedVideoExecutionBinding,
    row: Mapping[str, object],
) -> bool:
    if set(preparation) != _PREPARATION_KEYS:
        return False
    fingerprint = preparation.get("fingerprint")
    prepared_items = preparation.get("items")
    if (
        not isinstance(fingerprint, Mapping)
        or set(fingerprint)
        != {
            "schema_version",
            "ordered_nonsecret_payload_digest",
            "actor_id",
            "operation",
        }
        or not isinstance(prepared_items, list)
        or len(prepared_items) != 1
        or not isinstance(prepared_items[0], Mapping)
    ):
        return False
    facts = prepared_items[0]
    model = item.get("model")
    references = item.get("references")
    target = item.get("target")
    execution_snapshot = item.get("execution_snapshot")
    if (
        set(facts) != _PREPARED_ITEM_KEYS
        or not isinstance(model, Mapping)
        or not isinstance(references, Mapping)
        or not isinstance(target, Mapping)
        or not isinstance(facts.get("model"), Mapping)
        or not isinstance(facts.get("target"), Mapping)
        or not isinstance(facts.get("effective_parameters"), Mapping)
        or not isinstance(execution_snapshot, Mapping)
        or not isinstance(execution_snapshot.get("target_snapshot"), Mapping)
    ):
        return False
    quote = model.get("quote_amount")
    effective = model.get("effective_parameters")
    prepared_model = facts["model"]
    prepared_quote = prepared_model.get("quote")
    prepared_target = facts["target"]
    refs = {
        name: references.get(name)
        for name in ("reference_images", "reference_audios", "reference_videos")
    }
    if (
        not isinstance(effective, Mapping)
        or set(prepared_model) != _PREPARED_MODEL_KEYS
        or not isinstance(prepared_quote, Mapping)
        or set(prepared_quote) != _SERVER_QUOTE_KEYS
        or set(prepared_target) != _TARGET_FACT_KEYS
        or type(quote) is not int
        or quote < 0
        or type(facts.get("quote_amount")) is not int
        or facts["quote_amount"] != quote
        or type(preparation.get("quoted_total")) is not int
        or preparation["quoted_total"] != quote
        or type(preparation.get("required_balance")) is not int
        or preparation["required_balance"] != quote
        or preparation.get("reserved_amount") != 0
        or type(preparation.get("reserved_amount")) is not int
        or preparation.get("executable") is not False
        or preparation.get("operation") != binding.operation
        or not isinstance(preparation.get("phase"), str)
        or not preparation.get("phase")
        or not isinstance(preparation.get("credential_binding_status"), str)
        or not preparation.get("credential_binding_status")
        or (
            preparation.get("available_balance") is not None
            and (
                type(preparation["available_balance"]) is not int
                or preparation["available_balance"] < 0
            )
        )
        or fingerprint.get("schema_version") != row["schema_version"]
        or fingerprint.get("ordered_nonsecret_payload_digest") != binding.request_digest
        or fingerprint.get("actor_id") != binding.owner_id
        or fingerprint.get("operation") != binding.operation
        or item.get("raw_key") != binding.raw_key
        or model.get("category") != "video"
        or prepared_model.get("category") != "video"
        or prepared_model.get("source") != binding.task_model_source
        or prepared_model.get("catalog_id") != binding.task_model_id
        or prepared_model.get("display_name") != binding.task_model_name
        or prepared_model.get("wire_model") != model.get("wire_model")
        or prepared_model.get("quote") is None
        or prepared_quote.get("category") != "video"
        or prepared_quote.get("source") != binding.task_model_source
        or type(prepared_quote.get("amount")) is not int
        or prepared_quote.get("amount") != quote
        or not _json_equal(prepared_quote.get("effective_parameters"), effective)
        or not _json_equal(facts.get("effective_parameters"), effective)
        or facts.get("credential_configured") is not True
        or facts.get("index") != 0
        or facts.get("prompt") is not None
        or not _prompt_digest_matches(raw_item, facts.get("prompt_digest"))
        or not _reference_counts_match(facts.get("reference_counts"), refs)
        or not _video_request_matches(raw_item, model, effective)
        or not _target_facts_match(
            prepared_target,
            raw_item,
            target,
            execution_snapshot["target_snapshot"],
        )
    ):
        return False
    return True


def _reference_counts_match(
    value: object,
    references: Mapping[str, object],
) -> bool:
    if not isinstance(value, list) or len(value) != 3:
        return False
    expected = [
        ["image", len(references["reference_images"])],
        ["audio", len(references["reference_audios"])],
        ["video", len(references["reference_videos"])],
    ]
    return value == expected and all(type(pair[1]) is int for pair in value)


def _video_request_matches(
    raw_item: Mapping[str, object],
    model: Mapping[str, object],
    effective: Mapping[str, object],
) -> bool:
    prompt = raw_item.get("prompt")
    if (
        set(raw_item) != _REQUEST_KEYS
        or raw_item.get("operation") != _VIDEO_SINGLE_OPERATION
        or not isinstance(prompt, str)
        or not prompt
    ):
        return False
    duration = raw_item.get("duration")
    resolution = raw_item.get("resolution")
    size = raw_item.get("size")
    if duration is not None and type(duration) is not int:
        return False
    if resolution is not None and not isinstance(resolution, str):
        return False
    if size is not None and not isinstance(size, str):
        return False
    effective_duration = effective.get("duration")
    effective_resolution = effective.get("resolution")
    effective_size = effective.get("size")
    if (
        effective_duration is not None
        and (
            type(effective_duration) is not int
            or not 1 <= effective_duration <= 3600
        )
    ):
        return False
    if effective_resolution is not None and not isinstance(effective_resolution, str):
        return False
    if effective_size is not None and not isinstance(effective_size, str):
        return False
    if (
        duration is not None and effective_duration != duration
        or resolution is not None and effective_resolution != resolution
        or size is not None and effective_size != size
    ):
        return False
    params = model.get("params")
    snapshot_parameters = model.get("snapshot_parameters")
    if not isinstance(params, Mapping) or not isinstance(snapshot_parameters, Mapping):
        return False
    expected_parameters = dict(params)
    expected_parameters.update(effective)
    return _json_equal(snapshot_parameters, expected_parameters)


def _prompt_digest_matches(raw_item: Mapping[str, object], value: object) -> bool:
    prompt = raw_item.get("prompt")
    return (
        isinstance(prompt, str)
        and bool(prompt)
        and value == hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    )


def _target_facts_match(
    facts: Mapping[str, object],
    raw_item: Mapping[str, object],
    target: Mapping[str, object],
    snapshot: Mapping[str, object],
) -> bool:
    raw_target = raw_item.get("target")
    if not isinstance(raw_target, Mapping):
        return False
    kind = facts.get("kind")
    if kind in {"standalone", "none"}:
        return (
            kind == "standalone"
            and set(raw_target) == {"kind"}
            and raw_target.get("kind") == "standalone"
            and set(target) == {"kind"}
            and target.get("kind") == "standalone"
            and snapshot.get("kind") == "none"
            and all(
                facts.get(name) is None
                for name in (
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
                )
            )
            and all(
                snapshot.get(name) is None
                for name in (
                    "chapter_id",
                    "series_id",
                    "asset_id",
                    "stable_storyboard_asset_id",
                )
            )
        )
    if kind not in {"frame_message", "asset_message"}:
        return False
    if (
        set(target)
        != {
            "kind",
            "chapter",
            "storyboard_source_chapter",
            "asset_type",
            "asset",
            "chapter_digest",
            "asset_digest",
        }
        or target.get("kind") != kind
        or not isinstance(target.get("chapter"), Mapping)
        or not isinstance(target.get("asset"), Mapping)
    ):
        return False
    chapter = target["chapter"]
    asset = target["asset"]
    source_chapter = target.get("storyboard_source_chapter")
    chapter_id = facts.get("chapter_id")
    series_id = facts.get("series_id")
    asset_type = facts.get("asset_type")
    asset_id = facts.get("asset_id")
    chapter_content = chapter.get("content")
    chapter_content = chapter_content if isinstance(chapter_content, str) else ""
    if (
        not isinstance(chapter_id, str)
        or not isinstance(series_id, str)
        or not isinstance(asset_type, str)
        or not isinstance(asset_id, str)
        or not _is_digest(facts.get("chapter_digest"))
        or not _is_digest(facts.get("asset_digest"))
        or chapter.get("id") != chapter_id
        or chapter.get("series_id") != series_id
        or hashlib.sha256(chapter_content.encode("utf-8")).hexdigest()
        != facts.get("chapter_digest")
        or asset.get("id") != asset_id
        or asset.get("series_id") != series_id
        or _asset_target_digest(asset) != facts.get("asset_digest")
        or target.get("asset_type") != asset_type
        or target.get("chapter_digest") != facts.get("chapter_digest")
        or target.get("asset_digest") != facts.get("asset_digest")
        or snapshot.get("kind") != kind
        or snapshot.get("chapter_id") != chapter_id
        or snapshot.get("series_id") != series_id
        or snapshot.get("asset_type") != asset_type
        or snapshot.get("asset_id") != asset_id
        or snapshot.get("compat_frame_index") != facts.get("frame_index")
        or snapshot.get("full_chapter_digest") != facts.get("chapter_digest")
        or snapshot.get("full_frame_digest") is not None
        or snapshot.get("expected_asset_image_url")
        != _target_image_url(asset.get("image_url"))
        or snapshot.get("same_series_target_digest") != facts.get("asset_digest")
        or facts.get("identity_mode") is not None
        or facts.get("expected_chapter_digest") is not None
        or facts.get("canonical_chapter_digest") is not None
    ):
        return False
    if asset_type == "storyboard":
        if (
            not isinstance(source_chapter, Mapping)
            or source_chapter.get("id") != asset.get("chapter_id")
            or source_chapter.get("series_id") != series_id
            or (
                source_chapter.get("id") == chapter_id
                and not _json_equal(source_chapter, chapter)
            )
        ):
            return False
    elif source_chapter is not None:
        return False
    if kind == "frame_message":
        frame_index = facts.get("frame_index")
        frames = _parse_target_chapter_frames(chapter_content)
        if (
            set(raw_target) != {"kind", "chapter_id", "storyboard_asset_id"}
            or raw_target.get("kind") != kind
            or raw_target.get("chapter_id") != chapter_id
            or raw_target.get("storyboard_asset_id") != asset_id
            or asset_type != "storyboard"
            or asset.get("chapter_id") != chapter_id
            or source_chapter.get("id") != chapter_id
            or snapshot.get("stable_storyboard_asset_id") != asset_id
            or type(frame_index) is not int
            or frame_index < 0
            or snapshot.get("full_frame_digest") is not None
            or frames is None
        ):
            return False
        matches = [
            index
            for index, frame in enumerate(frames)
            if isinstance(frame, Mapping)
            and isinstance(frame.get("storyboard"), list)
            and asset_id in frame["storyboard"]
        ]
        return (
            frame_index < len(frames)
            and matches == [frame_index]
            and isinstance(frames[frame_index], Mapping)
        )
    return (
        set(raw_target) == {"kind", "chapter_id", "asset_type", "asset_id"}
        and raw_target.get("kind") == kind
        and raw_target.get("chapter_id") == chapter_id
        and raw_target.get("asset_type") == asset_type
        and raw_target.get("asset_id") == asset_id
        and snapshot.get("stable_storyboard_asset_id") is None
        and snapshot.get("full_frame_digest") is None
    )


def _snapshot_matches_binding(
    snapshot: FrozenExecutionSnapshot,
    item: Mapping[str, object],
    binding: PersistedVideoExecutionBinding,
    row: Mapping[str, object],
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
        or set(references) != _REFERENCE_KEYS
        or _reference_groups_from_item(item) is None
    ):
        return False
    scope = {
        "source": row["source"],
        "row_id": row["row_id"],
        "owner_or_system": row["owner_or_system"],
        "provider": row["provider"],
    }
    effective = model.get("effective_parameters")
    params = model.get("params")
    snapshot_parameters = model.get("snapshot_parameters")
    raw_item_target = raw_item.get("target")
    if (
        not isinstance(effective, Mapping)
        or not isinstance(params, Mapping)
        or not isinstance(snapshot_parameters, Mapping)
        or not isinstance(raw_item_target, Mapping)
    ):
        return False
    adapters = dict(snapshot.adapter_config_versions)
    if len(adapters) != len(snapshot.adapter_config_versions):
        return False
    return all(
        (
            snapshot.model_category == "video",
            snapshot.model_name == model.get("snapshot_model_name") == model.get("wire_model"),
            snapshot.model_ref.model_id == binding.task_model_id == model.get("row_id"),
            snapshot.model_ref.owner_id == model.get("owner_or_system"),
            snapshot.model_ref.provider == row.get("provider") == model.get("provider"),
            model.get("provider_route") == str(row.get("provider")).lower(),
            model.get("source") == row.get("source") == binding.task_model_source,
            model.get("category") == "video",
            model.get("display_name") == binding.task_model_name,
            model.get("quote_amount") == binding.quoted_amount == snapshot.server_quote,
            type(model.get("quote_amount")) is int,
            prompt.get("id") is None,
            prompt.get("selection") == {"source": "request"},
            prompt.get("effective_prompt") == snapshot.effective_prompt == raw_item.get("prompt"),
            prompt.get("effective_context") == snapshot.effective_context == "",
            _json_equal(snapshot.model_params, snapshot_parameters),
            projection.get("operation") == binding.operation,
            projection.get("submission_id") == binding.submission_id,
            projection.get("request_digest") == binding.request_digest,
            projection.get("rule_digest") == binding.rule_digest,
            projection.get("model_id") == binding.task_model_id,
            projection.get("model_source") == binding.task_model_source,
            projection.get("model_owner_id") == binding.task_model_owner_id,
            projection.get("prepared_payload_id") == binding.prepared_payload_id,
            projection.get("protected_ref") == binding.protected_ref,
            projection.get("target_ref") == f"{binding.protected_ref}#target",
            projection.get("model_ref") == f"{binding.protected_ref}#model",
            projection.get("grant_ref") == f"{binding.protected_ref}#grant",
            projection.get("prompt_id") is None,
            projection.get("size") == effective.get("size"),
            type(projection.get("reference_images_version")) is int
            and projection.get("reference_images_version") == 1,
            type(projection.get("reference_images_count")) is int
            and projection.get("reference_images_count")
            == len(references["reference_images"]),
            projection.get("reference_images_digest")
            == _reference_digest(references["reference_images"]),
            _json_equal(
                projection.get("target_snapshot"),
                _target_snapshot_value(snapshot.target_snapshot),
            ),
            _target_matches(raw_item_target, target, snapshot),
            _grant_matches(grant, snapshot, binding.owner_id),
            _credential_matches(credential, snapshot, row, binding),
            _video_request_matches(raw_item, model, effective),
            _json_equal({**params, **effective}, snapshot.model_params),
            snapshot.grant_snapshot.actor_id == binding.owner_id,
            snapshot.reference_snapshot_ref == f"{binding.protected_ref}#references",
            snapshot.credential_binding_ref
            == f"{row['binding_id']}:v{row['binding_version']}",
            snapshot.endpoint_policy.allow_private_network is False,
            adapters.get("credential_binding_version") == str(row["binding_version"]),
            adapters.get("endpoint_policy") == credential.get("endpoint_policy_version"),
            adapters.get("api_base_path") == credential.get("api_base_path"),
            adapters.get("projector") == row.get("projector_version"),
            adapters.get("reference_policy") == row.get("reference_policy_version"),
            projection.get("size") is None
            or isinstance(projection.get("size"), str),
        )
    )


def _target_matches(
    raw_target: Mapping[str, object],
    protected_target: Mapping[str, object],
    snapshot: FrozenExecutionSnapshot,
) -> bool:
    target = snapshot.target_snapshot
    if target.kind == "none":
        return (
            set(raw_target) == {"kind"}
            and raw_target.get("kind") == "standalone"
            and set(protected_target) == {"kind"}
            and protected_target.get("kind") == "standalone"
        )
    if target.kind not in {"frame_message", "asset_message"}:
        return False
    if (
        protected_target.get("kind") != target.kind
        or not isinstance(protected_target.get("chapter"), Mapping)
        or not isinstance(protected_target.get("asset"), Mapping)
    ):
        return False
    chapter = protected_target["chapter"]
    asset = protected_target["asset"]
    if (
        chapter.get("id") != target.chapter_id
        or chapter.get("series_id") != target.series_id
        or asset.get("id") != target.asset_id
        or asset.get("series_id") != target.series_id
        or protected_target.get("asset_type") != target.asset_type
        or protected_target.get("chapter_digest") != target.full_chapter_digest
        or protected_target.get("asset_digest") != target.same_series_target_digest
    ):
        return False
    if target.kind == "frame_message":
        return (
            set(raw_target) == {"kind", "chapter_id", "storyboard_asset_id"}
            and raw_target.get("kind") == "frame_message"
            and raw_target.get("chapter_id") == target.chapter_id
            and raw_target.get("storyboard_asset_id") == target.asset_id
            and target.asset_type == "storyboard"
            and target.stable_storyboard_asset_id == target.asset_id
            and target.full_frame_digest is None
        )
    return (
        set(raw_target) == {"kind", "chapter_id", "asset_type", "asset_id"}
        and raw_target.get("kind") == "asset_message"
        and raw_target.get("chapter_id") == target.chapter_id
        and raw_target.get("asset_type") == target.asset_type
        and raw_target.get("asset_id") == target.asset_id
        and target.stable_storyboard_asset_id is None
        and target.full_frame_digest is None
    )


def _asset_target_digest(asset: Mapping[str, object]) -> str:
    updated_at = asset.get("updated_at")
    if updated_at is not None:
        updated_at = (
            updated_at.isoformat()
            if hasattr(updated_at, "isoformat")
            else str(updated_at)
        )
    digest_input = {
        "name": asset.get("name") or asset.get("title"),
        "description": asset.get("description"),
        "image_url": asset.get("image_url"),
        "updated_at": updated_at,
    }
    return hashlib.sha256(canonical_payload_json(digest_input)).hexdigest()


def _target_image_url(value: object) -> str | None:
    return str(value) if value is not None else None


def _parse_target_chapter_frames(value: str) -> list[object] | None:
    try:
        frames = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return frames if isinstance(frames, list) else None


def _grant_matches(
    grant: Mapping[str, object],
    snapshot: FrozenExecutionSnapshot,
    owner_id: str,
) -> bool:
    grant_snapshot = snapshot.grant_snapshot
    if grant.get("actor_id") != owner_id or grant_snapshot.actor_id != owner_id:
        return False
    if grant_snapshot.series_id is None:
        return (
            set(grant) == {"actor_id", "scope"}
            and grant.get("scope") == "account"
            and grant_snapshot.team_id is None
            and grant_snapshot.access_provenance == ()
            and snapshot.target_snapshot.kind == "none"
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
    series = grant.get("series")
    if (
        not isinstance(series, Mapping)
        or set(series) != {"id", "user_id", "team_id", "claimed_by"}
    ):
        return False
    series_id = series.get("id")
    series_owner_id = series.get("user_id")
    team_id = series.get("team_id")
    claimed_by = series.get("claimed_by")
    if (
        not isinstance(series_id, str)
        or not series_id
        or not isinstance(series_owner_id, str)
        or not series_owner_id
        or (team_id is not None and not isinstance(team_id, str))
        or (claimed_by is not None and not isinstance(claimed_by, str))
    ):
        return False
    member = grant.get("member")
    if member is not None:
        if (
            not isinstance(member, Mapping)
            or set(member) != {"id", "team_id", "user_id", "role", "permissions"}
            or not isinstance(member.get("id"), str)
            or not member.get("id")
            or not isinstance(member.get("team_id"), str)
            or not member.get("team_id")
            or not isinstance(member.get("user_id"), str)
            or not member.get("user_id")
            or not isinstance(member.get("role"), str)
            or not member.get("role")
            or (
                member.get("permissions") is not None
                and not isinstance(member.get("permissions"), str)
            )
        ):
            return False
        if (
            not isinstance(team_id, str)
            or not team_id
            or member.get("team_id") != team_id
            or member.get("user_id") != owner_id
        ):
            return False

    is_owner = series_owner_id == owner_id
    is_member = member is not None
    claim_permission = False
    if claimed_by and claimed_by != owner_id:
        if not is_member or not isinstance(member, Mapping):
            return False
        claim_permission = can_enter_claimed_series(
            TeamMembership(
                role=member["role"],
                permissions=member["permissions"],
            )
        )
        if not claim_permission:
            return False
    if not is_owner and not is_member:
        return False
    expected_provenance = (
        ("series_owner",) if is_owner else ("team_member",)
    )
    if claim_permission:
        expected_provenance = (*expected_provenance, "claimed_series_permission")
    return all(
        (
            series_id == grant_snapshot.series_id,
            team_id == grant_snapshot.team_id,
            grant_snapshot.series_id == snapshot.target_snapshot.series_id,
            type(grant.get("is_owner")) is bool,
            grant.get("is_owner") is is_owner,
            type(grant.get("is_member")) is bool,
            grant.get("is_member") is is_member,
            grant.get("claimed_by") == claimed_by,
            type(grant.get("claim_permission")) is bool,
            grant.get("claim_permission") is claim_permission,
            isinstance(grant_snapshot.access_provenance, tuple),
            grant_snapshot.access_provenance == expected_provenance,
        )
    )


def _credential_matches(
    credential: Mapping[str, object],
    snapshot: FrozenExecutionSnapshot,
    row: Mapping[str, object],
    binding: PersistedVideoExecutionBinding,
) -> bool:
    scope = {
        "source": row["source"],
        "row_id": row["row_id"],
        "owner_or_system": row["owner_or_system"],
        "provider": row["provider"],
    }
    provenance = credential.get("version_provenance")
    protected_record = credential.get("protected_record")
    paths = credential.get("allowed_content_paths")
    if (
        not isinstance(provenance, Mapping)
        or not isinstance(protected_record, Mapping)
        or not isinstance(paths, list)
        or any(not isinstance(value, str) for value in paths)
    ):
        return False
    protected_endpoint = protected_record.get("endpoint_policy")
    adapters = dict(snapshot.adapter_config_versions)
    return all(
        (
            credential.get("scope") == scope,
            credential.get("binding_id") == row.get("binding_id"),
            type(credential.get("binding_version")) is int
            and credential.get("binding_version") == row.get("binding_version"),
            credential.get("principal_id") == row.get("principal_id"),
            credential.get("record_id") == provenance.get("protected_record_id"),
            credential.get("issuer_namespace")
            == protected_record.get("issuer_namespace"),
            credential.get("stable_account_subject")
            == protected_record.get("stable_account_subject"),
            credential.get("key_generation") == protected_record.get("key_generation"),
            credential.get("evidence_ref") == protected_record.get("evidence_ref"),
            credential.get("evidence_version") == protected_record.get("evidence_version"),
            protected_record.get("record_id") == credential.get("record_id"),
            protected_record.get("principal_id") == row.get("principal_id"),
            protected_record.get("scope") == scope,
            isinstance(protected_endpoint, Mapping),
            protected_endpoint.get("provider_origin") == credential.get("endpoint_origin"),
            protected_endpoint.get("api_base_path") == credential.get("api_base_path"),
            protected_endpoint.get("allowed_content_paths") == paths,
            protected_endpoint.get("policy_version")
            == credential.get("endpoint_policy_version"),
            snapshot.endpoint_policy.provider_origin == credential.get("endpoint_origin"),
            tuple(snapshot.endpoint_policy.allowed_content_paths) == tuple(paths),
            snapshot.endpoint_policy.allow_private_network is False,
            provenance.get("binding_version") == row.get("binding_version"),
            provenance.get("actor_id") == binding.owner_id,
            provenance.get("protected_record_id") == credential.get("record_id"),
            provenance.get("rotation_proof_ref") is None
            or isinstance(provenance.get("rotation_proof_ref"), str),
            adapters.get("credential_binding_version") == str(row.get("binding_version")),
            adapters.get("endpoint_policy") == credential.get("endpoint_policy_version"),
            adapters.get("api_base_path") == credential.get("api_base_path"),
        )
    )


def _reference_groups_from_item(
    item: Mapping[str, object],
) -> dict[str, tuple[str, ...]] | None:
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
    values: dict[str, tuple[str, ...]] = {}
    for name in ("reference_images", "reference_audios", "reference_videos"):
        group = references.get(name)
        raw_group = raw_item.get(name)
        if (
            not isinstance(group, list)
            or any(not isinstance(value, str) for value in group)
            or not isinstance(raw_group, list)
            or raw_group != group
        ):
            return None
        values[name] = tuple(group)
    images = values["reference_images"]
    if (
        type(projection.get("reference_images_version")) is not int
        or projection.get("reference_images_version") != 1
        or type(projection.get("reference_images_count")) is not int
        or projection.get("reference_images_count") != len(images)
        or projection.get("reference_images_digest") != _reference_digest(images)
    ):
        return None
    return values


def _reference_digest(values: object) -> str:
    if not isinstance(values, (tuple, list)) or any(
        not isinstance(value, str) for value in values
    ):
        return ""
    encoded = json.dumps(
        list(values), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _decode_execution_snapshot(value: object) -> FrozenExecutionSnapshot | None:
    if (
        not isinstance(value, Mapping)
        or set(value) != {item.name for item in fields(FrozenExecutionSnapshot)}
    ):
        return None
    model_value = value.get("model_ref")
    target_value = value.get("target_snapshot")
    grant_value = value.get("grant_snapshot")
    endpoint_value = value.get("endpoint_policy")
    if not all(
        isinstance(record, Mapping)
        for record in (model_value, target_value, grant_value, endpoint_value)
    ):
        return None
    model = _decode_record(ModelReference, model_value)
    target = _decode_record(TargetSnapshot, target_value)
    grant = _decode_record(FrozenGrantSnapshot, grant_value)
    endpoint = _decode_record(EndpointPolicy, endpoint_value)
    versions = value.get("adapter_config_versions")
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
    quote = value.get("server_quote")
    model_category = value.get("model_category")
    model_name = value.get("model_name")
    prompt_ref = value.get("prompt_ref")
    prompt = value.get("effective_prompt")
    context = value.get("effective_context")
    reference_ref = value.get("reference_snapshot_ref")
    credential_ref = value.get("credential_binding_ref")
    if (
        type(quote) is not int
        or quote < 0
        or model_category != "video"
        or not isinstance(model_name, str)
        or not model_name
        or (prompt_ref is not None and not isinstance(prompt_ref, str))
        or not isinstance(prompt, str)
        or not isinstance(context, str)
        or not isinstance(reference_ref, str)
        or not isinstance(credential_ref, str)
    ):
        return None
    try:
        return FrozenExecutionSnapshot(
            model_ref=model,
            model_category=model_category,
            model_name=model_name,
            model_params=value["model_params"],
            prompt_ref=prompt_ref,
            effective_prompt=prompt,
            effective_context=context,
            server_quote=quote,
            reference_snapshot_ref=reference_ref,
            target_snapshot=target,
            credential_binding_ref=credential_ref,
            grant_snapshot=grant,
            adapter_config_versions=tuple(tuple(pair) for pair in versions),
            endpoint_policy=endpoint,
        )
    except (TypeError, ValueError):
        return None


def _decode_record(record_type: type[Any], value: Mapping[str, object]) -> Any | None:
    if set(value) != {item.name for item in fields(record_type)}:
        return None
    decoded = dict(value)
    if record_type is ModelReference:
        if any(
            not isinstance(decoded.get(name), str) or not decoded[name]
            for name in ("owner_id", "provider", "model_id")
        ):
            return None
    elif record_type is TargetSnapshot:
        if not isinstance(decoded.get("kind"), str):
            return None
        optional_text = (
            "chapter_id",
            "series_id",
            "stable_storyboard_asset_id",
            "asset_type",
            "asset_id",
            "expected_text_digest",
            "media_identity",
            "content_digest",
            "full_frame_digest",
            "full_chapter_digest",
            "expected_asset_image_url",
            "same_series_target_digest",
        )
        if any(decoded.get(name) is not None and not isinstance(decoded[name], str) for name in optional_text):
            return None
        if any(
            decoded.get(name) is not None and type(decoded[name]) is not int
            for name in ("compat_frame_index", "notes_revision")
        ):
            return None
    elif record_type is FrozenGrantSnapshot:
        provenance = decoded.get("access_provenance")
        if (
            not isinstance(decoded.get("actor_id"), str)
            or not isinstance(provenance, list)
            or any(not isinstance(item, str) for item in provenance)
            or any(
                decoded.get(name) is not None and not isinstance(decoded[name], str)
                for name in ("series_id", "team_id")
            )
        ):
            return None
        decoded["access_provenance"] = tuple(provenance)
    elif record_type is EndpointPolicy:
        paths = decoded.get("allowed_content_paths")
        if (
            not isinstance(decoded.get("provider_origin"), str)
            or not isinstance(paths, list)
            or any(not isinstance(path, str) for path in paths)
            or type(decoded.get("allow_private_network")) is not bool
        ):
            return None
        decoded["allowed_content_paths"] = tuple(paths)
    try:
        return record_type(**decoded)
    except (TypeError, ValueError):
        return None


def _target_id(target: TargetSnapshot, fallback: str) -> str:
    return (
        target.asset_id
        or target.stable_storyboard_asset_id
        or target.chapter_id
        or fallback
    )


def _target_snapshot_value(target: TargetSnapshot) -> dict[str, object]:
    return {item.name: getattr(target, item.name) for item in fields(TargetSnapshot)}


def _json_equal(left: object, right: object) -> bool:
    try:
        return canonical_payload_json(left) == canonical_payload_json(right)
    except (TypeError, ValueError):
        return False


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


__all__ = [
    "PersistedVideoExecutionBinding",
    "VerifiedVideoExecutionInput",
    "read_original_video_single_execution_input",
]
