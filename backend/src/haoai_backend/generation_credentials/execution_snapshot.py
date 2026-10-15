"""Read and authenticate the original frozen input for image.single execution.

The caller supplies the durable task graph identities it has already locked in
Task → Unit → Step → Submission order. This module owns the prepared payload
lookup, producer-compatible AEAD/HMAC verification, typed snapshot decoding,
and exact original credential resolution. It never creates provider authority.
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
from haoai_backend.generation_core.graph_planning import (
    GraphItemInput,
    build_operation_graph_plan,
)
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    ExecutionStepRow,
    TaskSubmissionRow,
)
from haoai_backend.generation_credentials.application import CredentialRegistry
from haoai_backend.generation_credentials.domain import (
    CredentialCrypto,
    CredentialScope,
    HmacProofAuthority,
    SealedMaterial,
)
from haoai_backend.generation_credentials.persistence import prepared_payloads
from haoai_backend.generation_credentials.snapshot_producer import (
    canonical_payload_json,
    stored_payload_associated_data,
)

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_COMMAND_PROOF_PURPOSE = "prepared-generation-input-v1"
_IMAGE_SINGLE_GRAPH_VERSION = "image-single-v1"
_IMAGE_SINGLE_TASK_TYPE = "image-single"
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
_PROMPT_KEYS = frozenset(
    {"id", "selection", "effective_prompt", "effective_context"}
)
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


@dataclass(frozen=True, slots=True)
class PersistedImageExecutionBinding:
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
class VerifiedImageExecutionInput:
    """Authenticated server-side inputs; secret and prompt are hidden in repr."""

    task_id: str
    unit_id: str
    step_id: str
    prepared_payload_id: str
    server_attempt_id: str
    snapshot: FrozenExecutionSnapshot = field(repr=False)
    credential_binding: CredentialBinding = field(repr=False)
    provider_credential: BoundCredential = field(repr=False)
    references: tuple[object, ...] = field(default=(), repr=False)


def read_original_image_single_execution_input(
    session: Session,
    binding: PersistedImageExecutionBinding,
    *,
    registry: CredentialRegistry,
    crypto: CredentialCrypto,
    proof_authority: HmacProofAuthority,
) -> VerifiedImageExecutionInput | None:
    """Reopen only the payload bound to this image.single graph and original key.

    A legacy four-field step snapshot is deliberately unresolved. This reader
    performs no repair, fee change, network request, or durable write.
    """

    if (
        not isinstance(session, Session)
        or type(binding) is not PersistedImageExecutionBinding
        or type(registry) is not CredentialRegistry
        or type(crypto) is not CredentialCrypto
        or type(proof_authority) is not HmacProofAuthority
        or not crypto.ready()
        or not _valid_binding(binding)
    ):
        return None
    try:
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
            ordered_manifest_digest=_step_manifest_digest(binding),
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
        if snapshot is None or not _snapshot_matches_binding(snapshot, item, binding, row):
            return None
        plan = build_operation_graph_plan(
            binding.operation,
            graph_version=binding.graph_version,
            items=(
                GraphItemInput(
                    item_id=binding.server_attempt_id,
                    target_id=(
                        snapshot.target_snapshot.asset_id
                        or snapshot.target_snapshot.stable_storyboard_asset_id
                        or snapshot.target_snapshot.chapter_id
                        or binding.server_attempt_id
                    ),
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
        if plan.items[0].snapshot_digest != binding.snapshot_digest:
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
        return VerifiedImageExecutionInput(
            task_id=binding.task_id,
            unit_id=binding.unit_id,
            step_id=binding.step_id,
            prepared_payload_id=binding.prepared_payload_id,
            server_attempt_id=binding.server_attempt_id,
            snapshot=snapshot,
            credential_binding=credential_binding,
            provider_credential=provider_credential,
            references=(),
        )
    except (GenerationError, KeyError, TypeError, ValueError, UnicodeError):
        return None
    except Exception:
        # SQL, crypto, and registry failures are safe unresolved outcomes.
        return None


def _valid_binding(binding: PersistedImageExecutionBinding) -> bool:
    scalar_fields = (
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
    if (
        any(not isinstance(value, str) or not value for value in scalar_fields)
        or binding.operation != "image.single"
        or binding.graph_version != _IMAGE_SINGLE_GRAPH_VERSION
        or binding.task_type != _IMAGE_SINGLE_TASK_TYPE
        or binding.task_model_source not in {"system", "user"}
        or binding.server_attempt_id != f"{binding.submission_id}:0"
        or type(binding.quoted_amount) is not int
        or binding.quoted_amount < 0
        or (binding.task_message_id is not None and not isinstance(binding.task_message_id, str))
        or not _DIGEST.fullmatch(binding.request_digest)
        or not _DIGEST.fullmatch(binding.rule_digest)
        or not _DIGEST.fullmatch(binding.snapshot_digest)
        or not _DIGEST.fullmatch(binding.ordered_manifest_digest)
    ):
        return False
    if binding.task_model_source == "system":
        return binding.task_model_owner_id is None
    return binding.task_model_owner_id == binding.owner_id


def _persisted_graph_matches_binding(
    session: Session, binding: PersistedImageExecutionBinding
) -> bool:
    unit_table = BillingUnitRow.__table__
    step_table = ExecutionStepRow.__table__
    task = session.execute(
        select(AITaskRow.__table__).where(AITaskRow.id == binding.task_id)
    ).mappings().one_or_none()
    units = session.execute(
        select(unit_table).where(unit_table.c.task_id == binding.task_id)
    ).mappings().all()
    if len(units) != 1 or units[0]["id"] != binding.unit_id:
        return False
    unit = units[0]
    steps = session.execute(
        select(step_table).where(step_table.c.billing_unit_id == binding.unit_id)
    ).mappings().all()
    if len(steps) != 1 or steps[0]["id"] != binding.step_id:
        return False
    step = steps[0]
    submission = session.execute(
        select(TaskSubmissionRow.__table__).where(
            TaskSubmissionRow.id == binding.submission_id
        )
    ).mappings().one_or_none()
    if task is None or submission is None:
        return False
    try:
        request_data = json.loads(task["request_data"])
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    input_snapshot = step["input_snapshot"]
    if not isinstance(request_data, Mapping) or not isinstance(input_snapshot, Mapping):
        return False
    if set(input_snapshot) != _STEP_SNAPSHOT_KEYS:
        return False
    return all(
        (
            task["id"] == binding.task_id,
            task["user_id"] == binding.owner_id,
            task["type"] == binding.task_type,
            task["model_name"] == binding.task_model_name,
            task["credit_cost"] == binding.quoted_amount,
            (task["message_id"] or None) == binding.task_message_id,
            unit["id"] == binding.unit_id,
            unit["task_id"] == binding.task_id,
            unit["operation"] == binding.operation,
            unit["step_graph_version"] == binding.graph_version,
            unit["item_key"] == binding.server_attempt_id,
            unit["input_digest"] == binding.request_digest,
            unit["quoted_amount"] == binding.quoted_amount,
            step["id"] == binding.step_id,
            step["billing_unit_id"] == binding.unit_id,
            step["step_key"] == "generate",
            step["required"] is True,
            step["step_order"] == 0,
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
            request_data.get("operation") == binding.operation,
            request_data.get("submission_id") == binding.submission_id,
            request_data.get("parent_attempt_id") == binding.submission_id,
            request_data.get("server_attempt_id") == binding.server_attempt_id,
            request_data.get("prepared_payload_id") == binding.prepared_payload_id,
            request_data.get("protected_ref") == binding.protected_ref,
            request_data.get("billing_unit_id") == binding.unit_id,
            request_data.get("item_id") == binding.server_attempt_id,
            request_data.get("unit_key") == unit["item_key"],
            request_data.get("step_graph_version") == binding.graph_version,
            request_data.get("request_digest") == binding.request_digest,
            request_data.get("rule_digest") == binding.rule_digest,
            request_data.get("snapshot_digest") == binding.snapshot_digest,
            request_data.get("target_ref") == f"{binding.protected_ref}#target",
            request_data.get("model_ref") == f"{binding.protected_ref}#model",
            request_data.get("grant_ref") == f"{binding.protected_ref}#grant",
            request_data.get("model_id") == binding.task_model_id,
            request_data.get("model_source") == binding.task_model_source,
            request_data.get("model_owner_id") == binding.task_model_owner_id,
        )
    )


def _step_manifest_digest(binding: PersistedImageExecutionBinding) -> str:
    return binding.ordered_manifest_digest


def _payload_row_matches_binding(
    row: Mapping[str, object], binding: PersistedImageExecutionBinding
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
            isinstance(row.get("command_proof"), str)
            and _DIGEST.fullmatch(row["command_proof"]),
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
            row.get("row_id") == binding.task_model_id,
            row.get("source") == binding.task_model_source,
            row.get("owner_or_system")
            == (binding.task_model_owner_id or "system"),
        )
    )


def _decode_canonical_object(plaintext: str) -> dict[str, object] | None:
    try:
        decoded = json.loads(plaintext, object_pairs_hook=_unique_object)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if (
        not isinstance(decoded, dict)
        or canonical_payload_json(decoded).decode("utf-8") != plaintext
    ):
        return None
    return decoded


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _verify_payload(
    payload: Mapping[str, object],
    row: Mapping[str, object],
    binding: PersistedImageExecutionBinding,
    authority: HmacProofAuthority,
) -> bool:
    if set(payload) != _PAYLOAD_KEYS:
        return False
    command_manifest = payload.get("command_manifest")
    item = payload.get("item")
    if (
        not isinstance(command_manifest, Mapping)
        or not isinstance(item, Mapping)
        or set(command_manifest) != _COMMAND_MANIFEST_KEYS
        or set(item) != _PROTECTED_ITEM_KEYS
    ):
        return False
    manifest_bytes = canonical_payload_json(command_manifest)
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    expected_proof = row["command_proof"]
    if (
        manifest_digest != _step_manifest_digest(binding)
        or payload.get("ordered_manifest_digest") != manifest_digest
        or payload.get("manifest_proof") != expected_proof
        or not authority.verify(_COMMAND_PROOF_PURPOSE, manifest_bytes, expected_proof)
    ):
        return False
    children = [binding.server_attempt_id]
    payload_ids = [binding.prepared_payload_id]
    protected_refs = [binding.protected_ref]
    item_digest = hashlib.sha256(canonical_payload_json(item)).hexdigest()
    ordered_manifest = [
        {
            "index": 0,
            "child_attempt_id": binding.server_attempt_id,
            "payload_id": binding.prepared_payload_id,
            "protected_ref": binding.protected_ref,
            "item_digest": item_digest,
        }
    ]
    preparation = command_manifest.get("preparation")
    if not isinstance(preparation, Mapping):
        return False
    fingerprint = preparation.get("fingerprint")
    prepared_items = preparation.get("items")
    if (
        payload.get("parent_attempt_id") != binding.submission_id
        or payload.get("protected_ref") != binding.protected_ref
        or payload.get("child_attempt_id") != binding.server_attempt_id
        or payload.get("item_index") != 0
        or payload.get("ordered_count") != 1
        or payload.get("ordered_manifest") != ordered_manifest
        or payload.get("graph_version") != binding.graph_version
        or command_manifest.get("purpose") != _COMMAND_PROOF_PURPOSE
        or command_manifest.get("actor_id") != binding.owner_id
        or command_manifest.get("operation") != binding.operation
        or command_manifest.get("raw_key") != binding.raw_key
        or command_manifest.get("fingerprint") != binding.request_digest
        or command_manifest.get("parent_attempt_id") != binding.submission_id
        or command_manifest.get("children") != children
        or command_manifest.get("payload_ids") != payload_ids
        or command_manifest.get("protected_refs") != protected_refs
        or command_manifest.get("ordered_count") != 1
        or command_manifest.get("item_digests") != [item_digest]
        or command_manifest.get("graph_version") != binding.graph_version
        or command_manifest.get("projector_version") != row["projector_version"]
        or command_manifest.get("reference_policy_version")
        != row["reference_policy_version"]
        or item.get("index") != 0
        or item.get("operation") != binding.operation
        or item.get("fingerprint") != binding.request_digest
        or item.get("raw_key") != binding.raw_key
        or item.get("graph_version") != binding.graph_version
        or item.get("context_schema") != row["schema_version"]
        or item.get("projector_version") != row["projector_version"]
        or item.get("reference_policy_version") != row["reference_policy_version"]
        or not isinstance(fingerprint, Mapping)
        or fingerprint.get("operation") != binding.operation
        or fingerprint.get("actor_id") != binding.owner_id
        or fingerprint.get("ordered_nonsecret_payload_digest") != binding.request_digest
        or not isinstance(prepared_items, list)
        or len(prepared_items) != 1
        or preparation.get("operation") != binding.operation
        or preparation.get("quoted_total") != binding.quoted_amount
        or preparation.get("required_balance") != binding.quoted_amount
        or preparation.get("reserved_amount") != 0
        or preparation.get("executable") is not False
        or type(prepared_items[0]) is not dict
        or prepared_items[0].get("quote_amount") != binding.quoted_amount
    ):
        return False
    return True


def _decode_execution_snapshot(value: object) -> FrozenExecutionSnapshot | None:
    if not isinstance(value, Mapping):
        return None
    try:
        if set(value) != {item.name for item in fields(FrozenExecutionSnapshot)}:
            return None
        model_value = value["model_ref"]
        target_value = value["target_snapshot"]
        grant_value = value["grant_snapshot"]
        endpoint_value = value["endpoint_policy"]
        if not all(isinstance(item, Mapping) for item in (model_value, target_value, grant_value, endpoint_value)):
            return None
        model = _decode_dataclass(ModelReference, model_value)
        target = _decode_dataclass(TargetSnapshot, target_value)
        grant = _decode_dataclass(FrozenGrantSnapshot, grant_value)
        endpoint = _decode_dataclass(EndpointPolicy, endpoint_value)
        if model is None or target is None or grant is None or endpoint is None:
            return None
        versions = value["adapter_config_versions"]
        if (
            not isinstance(versions, list)
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
    except (KeyError, TypeError, ValueError):
        return None


def _decode_dataclass(record_type: type[Any], value: Mapping[str, object]) -> Any | None:
    if set(value) != {item.name for item in fields(record_type)}:
        return None
    try:
        decoded = dict(value)
        if record_type is FrozenGrantSnapshot:
            provenance = decoded.get("access_provenance")
            if not isinstance(provenance, list) or any(not isinstance(item, str) for item in provenance):
                return None
            decoded["access_provenance"] = tuple(provenance)
        elif record_type is EndpointPolicy:
            paths = decoded.get("allowed_content_paths")
            if not isinstance(paths, list) or any(not isinstance(item, str) for item in paths):
                return None
            decoded["allowed_content_paths"] = tuple(paths)
            if type(decoded.get("allow_private_network")) is not bool:
                return None
        if record_type is ModelReference and any(
            not isinstance(decoded.get(key), str)
            for key in ("owner_id", "provider", "model_id")
        ):
            return None
        if record_type is TargetSnapshot and not isinstance(decoded.get("kind"), str):
            return None
        return record_type(**decoded)
    except (TypeError, ValueError):
        return None


def _snapshot_matches_binding(
    snapshot: FrozenExecutionSnapshot,
    item: Mapping[str, object],
    binding: PersistedImageExecutionBinding,
    row: Mapping[str, object],
) -> bool:
    model = item.get("model")
    prompt = item.get("prompt")
    credential = item.get("credential")
    projection = item.get("request_data_projection")
    references = item.get("references")
    raw_item = item.get("raw_item")
    if (
        not isinstance(model, Mapping)
        or not isinstance(prompt, Mapping)
        or not isinstance(credential, Mapping)
        or not isinstance(projection, Mapping)
        or not isinstance(references, Mapping)
        or not isinstance(raw_item, Mapping)
        or set(model) != _MODEL_KEYS
        or set(prompt) != _PROMPT_KEYS
        or set(credential) != _CREDENTIAL_KEYS
        or set(projection) != _REQUEST_PROJECTION_KEYS
        or any(value not in ([], ()) for value in references.values())
        or raw_item.get("reference_images") not in ([], None)
    ):
        return False
    scope = {
        "source": row["source"],
        "row_id": row["row_id"],
        "owner_or_system": row["owner_or_system"],
        "provider": row["provider"],
    }
    version_provenance = credential.get("version_provenance")
    if not isinstance(version_provenance, Mapping):
        return False
    paths = credential.get("allowed_content_paths")
    adapters = dict(snapshot.adapter_config_versions)
    owner_projection = binding.task_model_owner_id
    return all(
        (
            projection.get("operation") == binding.operation,
            projection.get("submission_id") == binding.submission_id,
            projection.get("request_digest") == binding.request_digest,
            projection.get("rule_digest") == binding.rule_digest,
            projection.get("model_id") == binding.task_model_id,
            projection.get("model_source") == binding.task_model_source,
            projection.get("model_owner_id") == owner_projection,
            projection.get("prepared_payload_id") == binding.prepared_payload_id,
            projection.get("protected_ref") == binding.protected_ref,
            projection.get("target_ref") == f"{binding.protected_ref}#target",
            projection.get("model_ref") == f"{binding.protected_ref}#model",
            projection.get("grant_ref") == f"{binding.protected_ref}#grant",
            projection.get("reference_images_version") == 1,
            projection.get("reference_images_count") == 0,
            _is_digest(projection.get("reference_images_digest")),
            canonical_payload_json(projection.get("target_snapshot"))
            == canonical_payload_json(_target_snapshot_value(snapshot.target_snapshot)),
            model.get("source") == row["source"],
            model.get("row_id") == row["row_id"] == snapshot.model_ref.model_id,
            model.get("owner_or_system") == row["owner_or_system"] == snapshot.model_ref.owner_id,
            model.get("provider") == row["provider"] == snapshot.model_ref.provider,
            model.get("category") == "image",
            model.get("display_name") == binding.task_model_name,
            model.get("snapshot_model_name") == snapshot.model_name,
            model.get("quote_amount") == binding.quoted_amount == snapshot.server_quote,
            prompt.get("id") == snapshot.prompt_ref,
            prompt.get("effective_prompt") == snapshot.effective_prompt,
            prompt.get("effective_context") == snapshot.effective_context,
            snapshot.grant_snapshot.actor_id == binding.owner_id,
            snapshot.reference_snapshot_ref == f"{binding.protected_ref}#references",
            snapshot.credential_binding_ref
            == f"{row['binding_id']}:v{row['binding_version']}",
            snapshot.endpoint_policy.provider_origin == credential.get("endpoint_origin"),
            tuple(snapshot.endpoint_policy.allowed_content_paths) == tuple(paths or ()),
            snapshot.endpoint_policy.allow_private_network is False,
            adapters.get("credential_binding_version") == str(row["binding_version"]),
            adapters.get("endpoint_policy") == credential.get("endpoint_policy_version"),
            adapters.get("api_base_path") == credential.get("api_base_path"),
            credential.get("scope") == scope,
            credential.get("binding_id") == row["binding_id"],
            credential.get("binding_version") == row["binding_version"],
            credential.get("principal_id") == row["principal_id"],
            credential.get("record_id") == version_provenance.get("protected_record_id"),
            version_provenance.get("binding_version") == row["binding_version"],
            version_provenance.get("actor_id") == binding.owner_id,
            version_provenance.get("rotation_proof_ref") is None
            or isinstance(version_provenance.get("rotation_proof_ref"), str),
        )
    )


def _target_snapshot_value(target: TargetSnapshot) -> dict[str, object]:
    return {item.name: getattr(target, item.name) for item in fields(TargetSnapshot)}


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


__all__ = [
    "PersistedImageExecutionBinding",
    "VerifiedImageExecutionInput",
    "read_original_image_single_execution_input",
]
