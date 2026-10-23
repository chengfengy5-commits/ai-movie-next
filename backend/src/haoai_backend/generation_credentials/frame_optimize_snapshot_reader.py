"""Authenticate and reopen one complete registered frame.optimize batch."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from types import MappingProxyType

from sqlalchemy import select
from sqlalchemy.orm import Session

from haoai_backend.generation_catalog.admission_types import (
    CONTEXT_SCHEMA_VERSION,
    build_admission_context,
)
from haoai_backend.generation_contracts.types import (
    CredentialBinding,
    EndpointPolicy,
    FrozenExecutionSnapshot,
    FrozenGrantSnapshot,
    GenerationActor,
    ModelReference,
    TargetSnapshot,
    TrustedActor,
)
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    ExecutionStepRow,
    TaskSubmissionRow,
)
from haoai_backend.generation_core.fee_phases import build_fee_phase_plan
from haoai_backend.generation_core.graph_planning import GraphItemInput, build_operation_graph_plan
from haoai_backend.generation_credentials.domain import (
    CredentialCrypto,
    CredentialScope,
    HmacProofAuthority,
    SealedMaterial,
)
from haoai_backend.generation_credentials.frame_optimize_snapshot import (
    SNAPSHOT_VERSION,
    canonical_frame_optimize_bytes,
    frame_optimize_payload_associated_data,
)
from haoai_backend.generation_credentials.persistence import prepared_payloads


_OPERATION = "frame.optimize"
_GRAPH_VERSION = "frame-optimize-v1"
_PROOF_PURPOSE = "prepared-frame-optimize-input-v1"
_REFERENCE_POLICY_VERSION = "frame-optimize-no-references-v1"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_REQUEST_DATA_KEYS = frozenset(
    {
        "via",
        "mode",
        "operation",
        "graph_version",
        "submission_id",
        "task_id",
        "request_digest",
        "graph_digest",
        "manifest_snapshot_digest",
        "chapter_id",
        "frame_count",
        "model_source",
        "model_id",
        "model_owner_id",
        "model_display_name",
        "enable_thinking",
        "concurrency",
        "items",
    }
)
_ITEM_KEYS = frozenset(
    {
        "order",
        "frame_index",
        "item_id",
        "unit_id",
        "step_id",
        "prepared_payload_id",
        "protected_ref",
        "target_ref",
        "model_ref",
        "grant_ref",
        "request_digest",
        "rule_digest",
        "snapshot_digest",
        "manifest_snapshot_digest",
        "quoted_amount",
    }
)
_MANIFEST_ITEM_KEYS = (
    "order",
    "frame_index",
    "item_id",
    "unit_id",
    "step_id",
    "prepared_payload_id",
    "quoted_amount",
    "snapshot_digest",
    "rule_digest",
    "target_ref",
    "model_ref",
    "grant_ref",
)
_PAYLOAD_KEYS = frozenset(
    {
        "schema",
        "manifest",
        "manifest_proof",
        "manifest_snapshot_digest",
        "item",
        "execution_snapshot",
        "target",
        "database_chapter",
        "model",
        "prompt",
        "request",
        "grant",
        "credential",
    }
)
_TARGET_KEYS = frozenset(
    {"snapshot", "database_frame", "database_text", "requested_text", "chapter_digest"}
)
_MODEL_KEYS = frozenset(
    {"source", "row_id", "owner_or_system", "provider", "display_name", "wire_model", "params", "quote"}
)
_PROMPT_KEYS = frozenset(
    {"id", "owner_or_system", "source", "system_prompt", "user_prompt"}
    | ({"operation", "category", "display_name"})
)
_CREDENTIAL_KEYS = frozenset(
    {
        "scope",
        "binding_id",
        "binding_version",
        "principal_id",
        "endpoint_origin",
        "api_base_path",
        "allowed_content_paths",
        "endpoint_policy_version",
        "rotation_provenance",
    }
)


@dataclass(frozen=True, slots=True)
class PersistedFrameOptimizeExecutionBinding:
    """Expected immutable task identity supplied by a trusted SQL caller."""

    owner_id: str
    task_id: str
    submission_id: str
    task_message_id: str
    task_model_name: str
    request_digest: str
    graph_digest: str
    manifest_snapshot_digest: str


@dataclass(frozen=True, slots=True)
class VerifiedFrameOptimizeExecutionItem:
    """Authenticated frozen values for one item; it contains no provider secret."""

    order: int
    frame_index: int
    item_id: str
    unit_id: str
    step_id: str
    prepared_payload_id: str
    snapshot: FrozenExecutionSnapshot
    request: Mapping[str, object]
    model: Mapping[str, object]
    prompt: Mapping[str, object]
    database_frame: Mapping[str, object]
    database_text: str
    requested_text: str
    database_chapter: tuple[object, ...]
    credential_binding: CredentialBinding


@dataclass(frozen=True, slots=True)
class VerifiedFrameOptimizeExecutionBatch:
    """Authenticated whole ordered batch; this grants no claim or create authority."""

    task_id: str
    submission_id: str
    request_digest: str
    manifest_snapshot_digest: str
    request: Mapping[str, object]
    items: tuple[VerifiedFrameOptimizeExecutionItem, ...]


def read_original_frame_optimize_execution_batch(
    session: Session,
    binding: PersistedFrameOptimizeExecutionBinding,
    *,
    crypto: CredentialCrypto,
    proof_authority: HmacProofAuthority,
) -> VerifiedFrameOptimizeExecutionBatch | None:
    """Reopen every immutable input without resolving a provider credential."""

    if (
        not isinstance(session, Session)
        or type(binding) is not PersistedFrameOptimizeExecutionBinding
        or type(crypto) is not CredentialCrypto
        or type(proof_authority) is not HmacProofAuthority
        or not crypto.ready()
        or not _valid_binding(binding)
    ):
        return None
    try:
        with session.no_autoflush:
            task = session.execute(
                select(AITaskRow).where(AITaskRow.id == binding.task_id)
            ).scalar_one_or_none()
            if task is None or not _task_matches(task, binding):
                return None
            request_data = _decode_request_data(task.request_data)
            if request_data is None or not _request_data_matches(request_data, binding):
                return None
            items_value = request_data["items"]
            assert isinstance(items_value, list)
            submission = session.execute(
                select(TaskSubmissionRow).where(TaskSubmissionRow.id == binding.submission_id)
            ).scalar_one_or_none()
            if submission is None or not _submission_matches(submission, binding):
                return None

            units = session.execute(
                select(BillingUnitRow).where(BillingUnitRow.task_id == binding.task_id)
            ).scalars().all()
            unit_by_id = {unit.id: unit for unit in units}
            expected_unit_ids = {str(item["unit_id"]) for item in items_value}
            if len(unit_by_id) != len(items_value) or set(unit_by_id) != expected_unit_ids:
                return None
            steps = session.execute(
                select(ExecutionStepRow).where(
                    ExecutionStepRow.billing_unit_id.in_(tuple(expected_unit_ids))
                )
            ).scalars().all()
            step_by_id = {step.id: step for step in steps}
            expected_step_ids = {str(item["step_id"]) for item in items_value}
            if len(step_by_id) != len(items_value) or set(step_by_id) != expected_step_ids:
                return None
            payload_ids = [str(item["prepared_payload_id"]) for item in items_value]
            payload_rows = session.execute(
                select(prepared_payloads).where(
                    prepared_payloads.c.prepared_payload_id.in_(tuple(payload_ids)),
                    prepared_payloads.c.actor_id == binding.owner_id,
                    prepared_payloads.c.operation == _OPERATION,
                )
            ).mappings().all()
        payload_by_id = {str(row["prepared_payload_id"]): dict(row) for row in payload_rows}
        if len(payload_by_id) != len(items_value) or set(payload_by_id) != set(payload_ids):
            return None

        request: Mapping[str, object] | None = None
        database_chapter: tuple[object, ...] | None = None
        verified: list[VerifiedFrameOptimizeExecutionItem] = []
        graph_items: list[GraphItemInput] = []
        manifest_items = [_manifest_item(item) for item in items_value]
        manifest = _manifest(request_data, manifest_items, owner_id=binding.owner_id)
        manifest_bytes = canonical_frame_optimize_bytes(manifest)
        manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
        if (
            manifest_digest != binding.manifest_snapshot_digest
            or not proof_authority.verify(_PROOF_PURPOSE, manifest_bytes, _proof_from_rows(payload_by_id, items_value))
        ):
            return None

        for order, item_value in enumerate(items_value):
            item = dict(item_value)
            unit = unit_by_id[str(item["unit_id"])]
            step = step_by_id[str(item["step_id"])]
            payload_row = payload_by_id[str(item["prepared_payload_id"])]
            if not _unit_matches(unit, binding, item) or not _step_matches(step, item):
                return None
            scope = _scope_from_row(payload_row, item, owner_id=binding.owner_id)
            if scope is None:
                return None
            manifest_item = manifest_items[order]
            aad = frame_optimize_payload_associated_data(
                manifest_digest=manifest_digest,
                manifest_item=manifest_item,
                scope=scope,
                binding_id=str(payload_row["binding_id"]),
                binding_version=int(payload_row["binding_version"]),
                principal_id=str(payload_row["principal_id"]),
                schema_version=str(payload_row["schema_version"]),
                projector_version=str(payload_row["projector_version"]),
                reference_policy_version=str(payload_row["reference_policy_version"]),
                target_ref=str(payload_row["target_ref"]),
                model_ref=str(payload_row["model_ref"]),
                grant_ref=str(payload_row["grant_ref"]),
            )
            plaintext = crypto.open_secret(
                SealedMaterial(
                    ciphertext=payload_row["ciphertext"],
                    nonce=payload_row["nonce"],
                    key_id=payload_row["encryption_key_id"],
                    algorithm_version=crypto.algorithm_version,
                ),
                aad,
            )
            decoded = _decode_payload(plaintext)
            if decoded is None:
                return None
            credential_binding = _credential_from_payload(
                decoded.get("credential"),
                scope=scope,
                payload_row=payload_row,
            )
            if credential_binding is None or not _payload_matches(
                decoded,
                item=item,
                manifest=manifest,
                manifest_item=manifest_item,
                manifest_digest=manifest_digest,
                proof=str(payload_row["command_proof"]),
                proof_authority=proof_authority,
                scope=scope,
                credential_binding=credential_binding,
                payload_row=payload_row,
            ):
                return None

            snapshot = _snapshot_from_value(decoded["execution_snapshot"])
            if snapshot is None:
                return None
            target_payload = decoded["target"]
            assert isinstance(target_payload, Mapping)
            target = _target_from_value(target_payload["snapshot"])
            if target is None or target != snapshot.target_snapshot:
                return None
            if order == 0:
                request_value = decoded["request"]
                chapter_value = decoded["database_chapter"]
                if (
                    not isinstance(request_value, Mapping)
                    or request_value.get("operation") != _OPERATION
                    or not isinstance(chapter_value, list)
                ):
                    return None
                raw_request = dict(request_value)
                raw_request.pop("operation")
                request_context = build_admission_context(
                    GenerationActor(
                        user_id=binding.owner_id,
                        trusted_account_identity=TrustedActor(binding.owner_id),
                    ),
                    _OPERATION,
                    raw_request,
                )
                if request_context.fingerprint.ordered_nonsecret_payload_digest != binding.request_digest:
                    return None
                request = _freeze_mapping(request_context.canonical_request)
                database_chapter = tuple(_freeze(value) for value in chapter_value)
            elif decoded["request"] is not None or decoded["database_chapter"] is not None:
                return None

            if request is None or database_chapter is None:
                return None
            if not _target_and_context_match(
                target_payload=target_payload,
                target=target,
                snapshot=snapshot,
                request=request,
                database_chapter=database_chapter,
                item=item,
                prompt=decoded["prompt"],
            ):
                return None
            model = decoded["model"]
            prompt = decoded["prompt"]
            grant = decoded["grant"]
            if not _model_prompt_grant_match(
                model=model,
                prompt=prompt,
                grant=grant,
                snapshot=snapshot,
                credential_binding=credential_binding,
                credential_payload=decoded["credential"],
                request_data=request_data,
                owner_id=binding.owner_id,
            ):
                return None

            graph_items.append(
                GraphItemInput(
                    item_id=str(item["item_id"]),
                    target_id=str(item["target_ref"]),
                    task_id=binding.task_id,
                    unit_id=str(item["unit_id"]),
                    step_ids=(str(item["step_id"]),),
                    request_digest=binding.request_digest,
                    rule_digest=str(item["rule_digest"]),
                    snapshot=snapshot,
                )
            )
            verified.append(
                VerifiedFrameOptimizeExecutionItem(
                    order=order,
                    frame_index=int(item["frame_index"]),
                    item_id=str(item["item_id"]),
                    unit_id=str(item["unit_id"]),
                    step_id=str(item["step_id"]),
                    prepared_payload_id=str(item["prepared_payload_id"]),
                    snapshot=snapshot,
                    request=request,
                    model=_freeze_mapping(model),
                    prompt=_freeze_mapping(prompt),
                    database_frame=_freeze_mapping(target_payload["database_frame"]),
                    database_text=str(target_payload["database_text"]),
                    requested_text=str(target_payload["requested_text"]),
                    database_chapter=database_chapter,
                    credential_binding=credential_binding,
                )
            )

        if request is None or database_chapter is None:
            return None
        graph = build_operation_graph_plan(
            _OPERATION,
            graph_version=_GRAPH_VERSION,
            items=tuple(graph_items),
        )
        fee_plan = build_fee_phase_plan(_OPERATION, graph=graph)
        if (
            graph.plan_digest != binding.graph_digest
            or len(graph.items) != len(manifest_items)
            or any(
                graph_item.order != manifest_item["order"]
                or graph_item.snapshot_digest != manifest_item["snapshot_digest"]
                for graph_item, manifest_item in zip(graph.items, manifest_items, strict=True)
            )
            or fee_plan.charge_phase != "pre_apply"
            or fee_plan.debit_timing != "after_generation_before_apply"
            or fee_plan.quoted_total != sum(int(item["quoted_amount"]) for item in items_value)
        ):
            return None
        return VerifiedFrameOptimizeExecutionBatch(
            task_id=binding.task_id,
            submission_id=binding.submission_id,
            request_digest=binding.request_digest,
            manifest_snapshot_digest=manifest_digest,
            request=request,
            items=tuple(verified),
        )
    except Exception:
        return None


def _valid_binding(binding: PersistedFrameOptimizeExecutionBinding) -> bool:
    return all(
        isinstance(value, str) and value and len(value) <= 255
        for value in (
            binding.owner_id,
            binding.task_id,
            binding.submission_id,
            binding.task_message_id,
            binding.task_model_name,
        )
    ) and all(
        isinstance(value, str) and _DIGEST.fullmatch(value)
        for value in (binding.request_digest, binding.graph_digest, binding.manifest_snapshot_digest)
    )


def _task_matches(task: AITaskRow, binding: PersistedFrameOptimizeExecutionBinding) -> bool:
    return bool(
        task.user_id == binding.owner_id
        and task.type == "batch-optimize"
        and task.message_id == binding.task_message_id
        and task.model_name == binding.task_model_name
        and task.request_data is not None
    )


def _decode_request_data(value: object) -> dict[str, object] | None:
    if not isinstance(value, str):
        return None
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if (
        not isinstance(decoded, dict)
        or set(decoded) != _REQUEST_DATA_KEYS
        or canonical_frame_optimize_bytes(decoded).decode("utf-8") != value
    ):
        return None
    if not isinstance(decoded.get("items"), list) or not 1 <= len(decoded["items"]) <= 30:
        return None
    return decoded


def _request_data_matches(
    value: Mapping[str, object],
    binding: PersistedFrameOptimizeExecutionBinding,
) -> bool:
    items = value["items"]
    assert isinstance(items, list)
    if (
        value["via"] != "registered_generation_graph"
        or value["mode"] != "frame_optimize"
        or value["operation"] != _OPERATION
        or value["graph_version"] != _GRAPH_VERSION
        or value["submission_id"] != binding.submission_id
        or value["task_id"] != binding.task_id
        or value["request_digest"] != binding.request_digest
        or value["graph_digest"] != binding.graph_digest
        or value["manifest_snapshot_digest"] != binding.manifest_snapshot_digest
        or not _DIGEST.fullmatch(str(value["manifest_snapshot_digest"]))
        or type(value["frame_count"]) is not int
        or value["frame_count"] != len(items)
        or type(value["enable_thinking"]) is not bool
        or type(value["concurrency"]) is not int
        or not 3 <= value["concurrency"] <= 10
        or value["model_source"] not in {"system", "user"}
        or not isinstance(value["model_id"], str)
        or not value["model_id"]
        or not isinstance(value["model_owner_id"], str)
        or not value["model_owner_id"]
        or not isinstance(value["model_display_name"], str)
        or not value["model_display_name"]
    ):
        return False
    required_unique: tuple[str, ...] = (
        "item_id",
        "unit_id",
        "step_id",
        "prepared_payload_id",
        "protected_ref",
        "target_ref",
        "model_ref",
        "grant_ref",
    )
    seen = {key: set() for key in required_unique}
    for order, raw_item in enumerate(items):
        if not isinstance(raw_item, dict) or set(raw_item) != _ITEM_KEYS:
            return False
        if (
            raw_item["order"] != order
            or type(raw_item["order"]) is not int
            or type(raw_item["frame_index"]) is not int
            or raw_item["frame_index"] < 0
            or type(raw_item["quoted_amount"]) is not int
            or raw_item["quoted_amount"] < 0
            or any(
                not isinstance(raw_item[key], str) or not raw_item[key]
                for key in required_unique
            )
            or any(
                not isinstance(raw_item[key], str) or not _DIGEST.fullmatch(raw_item[key])
                for key in (
                    "request_digest",
                    "rule_digest",
                    "snapshot_digest",
                    "manifest_snapshot_digest",
                )
            )
            or raw_item["request_digest"] != binding.request_digest
            or raw_item["manifest_snapshot_digest"] != binding.manifest_snapshot_digest
        ):
            return False
        for key in required_unique:
            seen[key].add(raw_item[key])
        if raw_item["protected_ref"] != _protected_ref(raw_item["prepared_payload_id"]):
            return False
        if raw_item["target_ref"] != f"{raw_item['protected_ref']}#target":
            return False
        if raw_item["model_ref"] != f"{raw_item['protected_ref']}#model":
            return False
        if raw_item["grant_ref"] != f"{raw_item['protected_ref']}#grant":
            return False
    return all(len(values) == len(items) for values in seen.values())


def _submission_matches(row: TaskSubmissionRow, binding: PersistedFrameOptimizeExecutionBinding) -> bool:
    return bool(
        row.user_id == binding.owner_id
        and row.operation == _OPERATION
        and row.idempotency_key == f"frame-optimize:{binding.submission_id}"
        and row.request_digest == binding.request_digest
        and row.task_ids == [binding.task_id]
        and row.task_quote_id is None
        and row.status == "accepted"
    )


def _unit_matches(unit: BillingUnitRow, binding: PersistedFrameOptimizeExecutionBinding, item: Mapping[str, object]) -> bool:
    return bool(
        unit.task_id == binding.task_id
        and unit.item_key == item["item_id"]
        and unit.operation == _OPERATION
        and unit.step_graph_version == _GRAPH_VERSION
        and unit.input_digest == binding.request_digest
        and unit.quoted_amount == item["quoted_amount"]
        and unit.debited_amount >= 0
        and unit.refunded_amount >= 0
        and unit.billing_status in {"unbilled", "charged", "refunded", "no_charge"}
        and unit.result_status in {"pending", "partial", "succeeded", "failed", "unknown"}
    )


def _step_matches(step: ExecutionStepRow, item: Mapping[str, object]) -> bool:
    expected_snapshot = {
        "item_order": item["order"],
        "frame_index": item["frame_index"],
        "server_attempt_id": item["item_id"],
        "prepared_payload_id": item["prepared_payload_id"],
        "protected_ref": item["protected_ref"],
        "snapshot_digest": item["snapshot_digest"],
        "manifest_snapshot_digest": item["manifest_snapshot_digest"],
    }
    return bool(
        step.billing_unit_id == item["unit_id"]
        and step.step_key == "generate"
        and step.dependencies == []
        and step.input_digest == item["request_digest"]
        and step.input_snapshot == expected_snapshot
        and step.required is True
        and step.step_order == item["order"]
    )


def _payload_row_matches(
    row: Mapping[str, object],
    item: Mapping[str, object],
    *,
    owner_id: str,
) -> bool:
    return bool(
        row["actor_id"] == owner_id
        and row["operation"] == _OPERATION
        and row["server_attempt_id"] == item["item_id"]
        and row["prepared_payload_id"] == item["prepared_payload_id"]
        and row["context_fingerprint"] == item["request_digest"]
        and row["schema_version"] == CONTEXT_SCHEMA_VERSION
        and row["projector_version"] == SNAPSHOT_VERSION
        and row["reference_policy_version"] == _REFERENCE_POLICY_VERSION
        and row["target_ref"] == item["target_ref"]
        and row["model_ref"] == item["model_ref"]
        and row["grant_ref"] == item["grant_ref"]
        and isinstance(row["command_proof"], str)
        and isinstance(row["ciphertext"], bytes)
        and isinstance(row["nonce"], bytes)
    )


def _scope_from_row(
    row: Mapping[str, object],
    item: Mapping[str, object],
    *,
    owner_id: str,
) -> CredentialScope | None:
    if not _payload_row_matches(row, item, owner_id=owner_id) or not isinstance(row["schema_version"], str):
        return None
    try:
        scope = CredentialScope(
            source=str(row["source"]),
            row_id=str(row["row_id"]),
            owner_or_system=str(row["owner_or_system"]),
            provider=str(row["provider"]),
        )
        return scope
    except (TypeError, ValueError):
        return None


def _credential_from_payload(
    value: object,
    *,
    scope: CredentialScope,
    payload_row: Mapping[str, object],
) -> CredentialBinding | None:
    if not isinstance(value, Mapping) or set(value) != _CREDENTIAL_KEYS:
        return None
    scope_value = value["scope"]
    paths = value["allowed_content_paths"]
    if (
        not isinstance(scope_value, Mapping)
        or dict(scope_value) != scope.identity()
        or type(value["binding_version"]) is not int
        or value["binding_version"] != payload_row["binding_version"]
        or value["binding_id"] != payload_row["binding_id"]
        or value["principal_id"] != payload_row["principal_id"]
        or value["endpoint_origin"] is None
        or not isinstance(value["endpoint_origin"], str)
        or not isinstance(value["api_base_path"], str)
        or not isinstance(value["endpoint_policy_version"], str)
        or not isinstance(paths, list)
        or not all(isinstance(path, str) for path in paths)
        or (value["rotation_provenance"] is not None and not isinstance(value["rotation_provenance"], str))
    ):
        return None
    try:
        return CredentialBinding(
            source=scope.source,
            row_id=scope.row_id,
            owner_or_system=scope.owner_or_system,
            provider=scope.provider,
            original_principal_id=str(value["principal_id"]),
            endpoint_origin=value["endpoint_origin"],
            allowed_content_paths=tuple(paths),
            binding_version=str(value["binding_version"]),
            rotation_provenance=value["rotation_provenance"],
        )
    except (TypeError, ValueError):
        return None


def _proof_from_rows(
    payload_by_id: Mapping[str, Mapping[str, object]],
    items: list[object],
) -> str:
    proofs = [
        payload_by_id[str(item["prepared_payload_id"])]["command_proof"]
        for item in items
    ]
    return proofs[0] if proofs and isinstance(proofs[0], str) and all(proof == proofs[0] for proof in proofs) else ""


def _manifest(
    request_data: Mapping[str, object],
    items: list[dict[str, object]],
    *,
    owner_id: str,
) -> dict[str, object]:
    return {
        "purpose": _PROOF_PURPOSE,
        "schema": SNAPSHOT_VERSION,
        "owner_id": owner_id,
        "operation": _OPERATION,
        "graph_version": _GRAPH_VERSION,
        "submission_id": request_data["submission_id"],
        "task_id": request_data["task_id"],
        "request_digest": request_data["request_digest"],
        "item_ids": [item["item_id"] for item in items],
        "items": items,
    }


def _manifest_item(item: Mapping[str, object]) -> dict[str, object]:
    return {key: item[key] for key in _MANIFEST_ITEM_KEYS}


def _decode_payload(value: str | None) -> dict[str, object] | None:
    if not isinstance(value, str):
        return None
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if (
        not isinstance(decoded, dict)
        or set(decoded) != _PAYLOAD_KEYS
        or canonical_frame_optimize_bytes(decoded).decode("utf-8") != value
    ):
        return None
    return decoded


def _payload_matches(
    payload: Mapping[str, object],
    *,
    item: Mapping[str, object],
    manifest: Mapping[str, object],
    manifest_item: Mapping[str, object],
    manifest_digest: str,
    proof: str,
    proof_authority: HmacProofAuthority,
    scope: CredentialScope,
    credential_binding: CredentialBinding,
    payload_row: Mapping[str, object],
) -> bool:
    if (
        payload["schema"] != SNAPSHOT_VERSION
        or payload["manifest"] != manifest
        or payload["manifest_proof"] != proof
        or payload["manifest_snapshot_digest"] != manifest_digest
        or payload["item"] != manifest_item
        or not proof_authority.verify(
            _PROOF_PURPOSE,
            canonical_frame_optimize_bytes(manifest),
            proof,
        )
    ):
        return False
    credential = payload["credential"]
    if not isinstance(credential, Mapping) or set(credential) != _CREDENTIAL_KEYS:
        return False
    if (
        credential["binding_id"] != payload_row["binding_id"]
        or credential["binding_version"] != payload_row["binding_version"]
        or credential["principal_id"] != payload_row["principal_id"]
        or credential["rotation_provenance"] != credential_binding.rotation_provenance
        or credential["endpoint_origin"] != credential_binding.endpoint_origin
        or tuple(credential["allowed_content_paths"]) != credential_binding.allowed_content_paths
    ):
        return False
    model = payload["model"]
    prompt = payload["prompt"]
    grant = payload["grant"]
    return bool(
        isinstance(model, Mapping)
        and set(model) == _MODEL_KEYS
        and isinstance(prompt, Mapping)
        and set(prompt) in {_PROMPT_KEYS, _PROMPT_KEYS - {"operation", "category", "display_name"}}
        and isinstance(grant, Mapping)
        and isinstance(payload["target"], Mapping)
        and set(payload["target"]) == _TARGET_KEYS
        and (payload["database_chapter"] is None or isinstance(payload["database_chapter"], list))
        and (payload["request"] is None or isinstance(payload["request"], Mapping))
        and payload_row["actor_id"] == manifest["owner_id"]
        and payload_row["binding_id"] == credential["binding_id"]
        and payload_row["binding_version"] == credential["binding_version"]
        and payload_row["principal_id"] == credential["principal_id"]
        and scope.source == credential["scope"]["source"]
        and scope.row_id == credential["scope"]["row_id"]
        and scope.owner_or_system == credential["scope"]["owner_or_system"]
        and scope.provider == credential["scope"]["provider"]
    )


def _snapshot_from_value(value: object) -> FrozenExecutionSnapshot | None:
    if not isinstance(value, Mapping):
        return None
    expected = {field.name for field in fields(FrozenExecutionSnapshot)}
    if set(value) != expected:
        return None
    model_ref = value["model_ref"]
    target = _target_from_value(value["target_snapshot"])
    grant = value["grant_snapshot"]
    endpoint = value["endpoint_policy"]
    if (
        not isinstance(model_ref, Mapping)
        or set(model_ref) != {field.name for field in fields(ModelReference)}
        or target is None
        or not isinstance(grant, Mapping)
        or set(grant) != {field.name for field in fields(FrozenGrantSnapshot)}
        or not isinstance(endpoint, Mapping)
        or set(endpoint) != {field.name for field in fields(EndpointPolicy)}
        or not isinstance(value["adapter_config_versions"], list)
    ):
        return None
    try:
        return FrozenExecutionSnapshot(
            model_ref=ModelReference(**dict(model_ref)),
            model_category=str(value["model_category"]),
            model_name=str(value["model_name"]),
            model_params=value["model_params"],
            prompt_ref=value["prompt_ref"],
            effective_prompt=str(value["effective_prompt"]),
            effective_context=str(value["effective_context"]),
            server_quote=value["server_quote"],
            reference_snapshot_ref=str(value["reference_snapshot_ref"]),
            target_snapshot=target,
            credential_binding_ref=str(value["credential_binding_ref"]),
            grant_snapshot=FrozenGrantSnapshot(
                actor_id=grant["actor_id"],
                series_id=grant["series_id"],
                team_id=grant["team_id"],
                access_provenance=tuple(grant["access_provenance"]),
            ),
            adapter_config_versions=tuple(tuple(pair) for pair in value["adapter_config_versions"]),
            endpoint_policy=EndpointPolicy(
                provider_origin=endpoint["provider_origin"],
                allowed_content_paths=tuple(endpoint["allowed_content_paths"]),
                allow_private_network=endpoint["allow_private_network"],
            ),
        )
    except (TypeError, ValueError):
        return None


def _target_from_value(value: object) -> TargetSnapshot | None:
    if not isinstance(value, Mapping) or set(value) != {field.name for field in fields(TargetSnapshot)}:
        return None
    try:
        return TargetSnapshot(**dict(value))
    except (TypeError, ValueError):
        return None


def _target_and_context_match(
    *,
    target_payload: object,
    target: TargetSnapshot | None,
    snapshot: FrozenExecutionSnapshot | None,
    request: Mapping[str, object],
    database_chapter: tuple[object, ...],
    item: Mapping[str, object],
    prompt: object,
) -> bool:
    frames = request.get("frames")
    chapter_texts = request.get("chapter_texts")
    if not isinstance(frames, (tuple, list)) or not isinstance(chapter_texts, (tuple, list)):
        return False
    order = item["order"]
    if type(order) is not int or order >= len(frames):
        return False
    requested = frames[order]
    if not isinstance(requested, Mapping) or requested.get("index") != item["frame_index"]:
        return False
    frame_index = int(item["frame_index"])
    if not 0 <= frame_index < len(database_chapter):
        return False
    database_frame = database_chapter[frame_index]
    if not isinstance(database_frame, Mapping) or not isinstance(database_frame.get("text"), str):
        return False
    if not isinstance(target_payload, Mapping) or target is None or snapshot is None:
        return False
    if set(target_payload) != _TARGET_KEYS:
        return False
    stored_frame = target_payload["database_frame"]
    database_text = target_payload["database_text"]
    requested_text = target_payload["requested_text"]
    if (
        not isinstance(stored_frame, Mapping)
        or _plain(stored_frame) != _plain(database_frame)
        or database_text != database_frame["text"]
        or requested_text != requested.get("text")
        or target_payload["chapter_digest"] != target.full_chapter_digest
        or target.full_chapter_digest != _sha256_json(database_chapter)
        or target.full_frame_digest != _sha256_json(database_frame)
        or target.expected_text_digest != _sha256_json(database_text)
        or target.compat_frame_index != frame_index
        or target.chapter_id != request["chapter_id"]
        or target.kind != "frame_text"
        or target != snapshot.target_snapshot
    ):
        return False
    effective = _decode_canonical_object(snapshot.effective_context)
    if effective is None or set(effective) != {"prompt_user", "request", "database_target"}:
        return False
    prior = tuple(chapter_texts[max(0, frame_index - 40) : frame_index])
    expected_context = {
        "frame_index": frame_index,
        "text": requested.get("text"),
        "asset_context": requested.get("asset_context", ""),
        "previous_frames": list(prior),
        "previous_context": "\n".join(prior),
        "enable_thinking": request.get("enable_thinking") is True,
        "concurrency": request.get("concurrency", 5),
    }
    expected_db_target = {
        "frame": _plain(database_frame),
        "text": database_text,
        "chapter_digest": target.full_chapter_digest,
    }
    return bool(
        effective["request"] == expected_context
        and effective["database_target"] == expected_db_target
        and isinstance(prompt, Mapping)
        and effective["prompt_user"] == prompt.get("user_prompt", "")
        and target.stable_storyboard_asset_id == _stable_storyboard_id(database_chapter, frame_index)
    )


def _model_prompt_grant_match(
    *,
    model: object,
    prompt: object,
    grant: object,
    snapshot: FrozenExecutionSnapshot,
    credential_binding: CredentialBinding,
    credential_payload: object,
    request_data: Mapping[str, object],
    owner_id: str,
) -> bool:
    if (
        not isinstance(model, Mapping)
        or set(model) != _MODEL_KEYS
        or not isinstance(prompt, Mapping)
        or not isinstance(grant, Mapping)
    ):
        return False
    if prompt.get("id") is None:
        if set(prompt) != {"id", "owner_or_system", "source", "system_prompt", "user_prompt"}:
            return False
        if (
            prompt.get("source") != "request"
            or prompt.get("id") is not None
            or prompt.get("owner_or_system") is not None
            or prompt.get("system_prompt") != ""
            or prompt.get("user_prompt") != ""
            or snapshot.prompt_ref is not None
        ):
            return False
    elif (
        set(prompt) != _PROMPT_KEYS
        or prompt.get("operation") != "optimize"
        or prompt.get("category") != "ai-review"
        or prompt.get("id") != snapshot.prompt_ref
        or prompt.get("display_name") is None
        or prompt.get("source") not in {"system", "user"}
    ):
        return False
    if not isinstance(credential_payload, Mapping):
        return False
    adapter_versions = dict(snapshot.adapter_config_versions)
    prompt_source = prompt.get("source")
    prompt_owner = prompt.get("owner_or_system")
    return bool(
        model["source"] == request_data["model_source"]
        and snapshot.model_category == "text"
        and model["row_id"] == request_data["model_id"]
        and model["owner_or_system"] == request_data["model_owner_id"]
        and model["display_name"] == request_data["model_display_name"]
        and model["provider"] == credential_binding.provider
        and model["row_id"] == credential_binding.row_id
        and model["owner_or_system"] == credential_binding.owner_or_system
        and model["source"] == credential_binding.source
        and (model["wire_model"] or model["display_name"]) == snapshot.model_name
        and model["params"] == _plain(snapshot.model_params)
        and model["quote"] == snapshot.server_quote
        and snapshot.model_ref.model_id == credential_binding.row_id
        and snapshot.model_ref.owner_id == credential_binding.owner_or_system
        and snapshot.model_ref.provider == credential_binding.provider
        and snapshot.effective_prompt == prompt.get("system_prompt")
        and grant == _plain(snapshot.grant_snapshot)
        and snapshot.endpoint_policy.provider_origin == credential_binding.endpoint_origin
        and snapshot.endpoint_policy.allowed_content_paths == credential_binding.allowed_content_paths
        and snapshot.credential_binding_ref
        == f"{credential_payload.get('binding_id')}:v{credential_payload.get('binding_version')}"
        and adapter_versions.get("credential_binding_version")
        == str(credential_payload.get("binding_version"))
        and adapter_versions.get("endpoint_policy") == credential_payload.get("endpoint_policy_version")
        and adapter_versions.get("api_base_path") == credential_payload.get("api_base_path")
        and prompt_owner
        == (
            None
            if prompt_source == "request"
            else "system" if prompt_source == "system" else owner_id
        )
    )


def _stable_storyboard_id(frames: tuple[object, ...], frame_index: int) -> str | None:
    frame = frames[frame_index]
    if not isinstance(frame, Mapping):
        return None
    refs = frame.get("storyboard")
    if refs is None or refs == []:
        return None
    if not isinstance(refs, (list, tuple)) or len(refs) != 1 or not isinstance(refs[0], str):
        return None
    stable_id = refs[0]
    all_ids = [
        value
        for entry in frames
        if isinstance(entry, Mapping) and isinstance(entry.get("storyboard"), (list, tuple))
        for value in entry["storyboard"]
        if isinstance(value, str)
    ]
    return stable_id if all_ids.count(stable_id) == 1 else None


def _protected_ref(payload_id: object) -> str:
    value = str(payload_id)
    return f"prepared-payload-v1/{len(value)}:{value}"


def _decode_canonical_object(value: str) -> dict[str, object] | None:
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(decoded, dict) or canonical_frame_optimize_bytes(decoded).decode("utf-8") != value:
        return None
    return decoded


def _freeze_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _plain(value: object) -> object:
    if is_dataclass(value):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported frozen optimize value: {type(value).__name__}")


def _sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_frame_optimize_bytes(value)).hexdigest()


__all__ = [
    "PersistedFrameOptimizeExecutionBinding",
    "VerifiedFrameOptimizeExecutionBatch",
    "VerifiedFrameOptimizeExecutionItem",
    "read_original_frame_optimize_execution_batch",
]
