"""Authenticate and reopen the original frozen input for frame.review."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from types import MappingProxyType
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from haoai_backend.generation_catalog.admission_types import canonicalize_request
from haoai_backend.generation_contracts.errors import GenerationError
from haoai_backend.generation_contracts.types import (
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
from haoai_backend.generation_credentials.domain import (
    CredentialCrypto,
    CredentialScope,
    HmacProofAuthority,
    SealedMaterial,
)
from haoai_backend.generation_credentials.frame_review_snapshot import (
    SNAPSHOT_VERSION,
    _PROOF_PURPOSE,
    canonical_frame_review_bytes,
    frame_review_payload_associated_data,
)
from haoai_backend.generation_credentials.persistence import prepared_payloads

_OPERATION = "frame.review"
_GRAPH_VERSION = "frame-review-v1"
_TASK_TYPE = "ai-review"
_CONTEXT_SCHEMA_VERSION = "legacy-admission-context-v1"
_REFERENCE_POLICY_VERSION = "frame-review-no-references-v1"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_STEP_SNAPSHOT_KEYS = frozenset(
    {
        "server_attempt_id",
        "prepared_payload_id",
        "protected_ref",
        "snapshot_digest",
        "manifest_snapshot_digest",
    }
)
_MANIFEST_KEYS = frozenset(
    {
        "purpose",
        "owner_id",
        "operation",
        "submission_id",
        "task_id",
        "unit_id",
        "step_id",
        "server_attempt_id",
        "prepared_payload_id",
        "request_digest",
        "rule_digest",
        "snapshot_digest",
        "target_ref",
        "model_ref",
        "grant_ref",
    }
)
_PAYLOAD_KEYS = frozenset(
    {
        "schema",
        "manifest",
        "manifest_proof",
        "execution_snapshot",
        "target",
        "model",
        "prompt",
        "request",
        "grant",
        "credential",
    }
)
_TARGET_PAYLOAD_KEYS = frozenset(
    {"snapshot", "frame", "original_text", "chapter_digest"}
)
_MODEL_KEYS = frozenset(
    {"source", "row_id", "owner_or_system", "provider", "display_name", "wire_model", "params", "quote"}
)
_PROMPT_KEYS = frozenset(
    {
        "id",
        "owner_or_system",
        "source",
        "operation",
        "category",
        "display_name",
        "system_prompt",
        "user_prompt",
    }
)
_REQUEST_PROMPT_KEYS = frozenset({"source", "system_prompt", "user_prompt"})
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
class PersistedFrameReviewExecutionBinding:
    """Claim-derived SQL identities used to reopen one immutable review graph."""

    owner_id: str
    task_id: str
    unit_id: str
    step_id: str
    submission_id: str
    raw_key: str
    task_message_id: str | None
    task_model_name: str
    task_model_id: str
    task_model_source: str
    task_model_owner_id: str
    request_digest: str
    rule_digest: str
    snapshot_digest: str
    manifest_snapshot_digest: str
    server_attempt_id: str
    prepared_payload_id: str
    protected_ref: str
    quoted_amount: int


@dataclass(frozen=True, slots=True)
class VerifiedFrameReviewExecutionInput:
    """Authenticated frozen review data; it contains no Provider credential."""

    task_id: str
    unit_id: str
    step_id: str
    submission_id: str
    server_attempt_id: str
    prepared_payload_id: str
    task_model_name: str
    snapshot: FrozenExecutionSnapshot = field(repr=False)
    request: Mapping[str, object] = field(repr=False)
    model: Mapping[str, object] = field(repr=False)
    prompt: Mapping[str, object] = field(repr=False)
    original_frame: Mapping[str, object] = field(repr=False)
    original_text: str = field(repr=False)
    chapter_digest: str = field(repr=False)
    credential_binding: CredentialBinding = field(repr=False)


def read_original_frame_review_execution_input(
    session: Session,
    binding: PersistedFrameReviewExecutionBinding,
    *,
    crypto: CredentialCrypto,
    proof_authority: HmacProofAuthority,
) -> VerifiedFrameReviewExecutionInput | None:
    """Reopen a committed frame.review input without resolving a Provider key.

    This verifies immutable graph and protected-payload facts only. It is not a
    claim capability and grants no authority to call a Provider or apply output.
    """

    if (
        not isinstance(session, Session)
        or type(binding) is not PersistedFrameReviewExecutionBinding
        or type(crypto) is not CredentialCrypto
        or type(proof_authority) is not HmacProofAuthority
        or not crypto.ready()
        or not _valid_binding(binding)
    ):
        return None
    try:
        with session.no_autoflush:
            graph = _read_persisted_graph(session, binding)
            if graph is None:
                return None
            task, unit, step, submission, request_data = graph
            rows = session.execute(
                select(prepared_payloads).where(
                    prepared_payloads.c.prepared_payload_id == binding.prepared_payload_id,
                    prepared_payloads.c.actor_id == binding.owner_id,
                    prepared_payloads.c.operation == _OPERATION,
                    prepared_payloads.c.server_attempt_id == binding.server_attempt_id,
                )
            ).mappings().all()
        if len(rows) != 1:
            return None
        row = dict(rows[0])
        if not _payload_row_matches_binding(row, binding):
            return None

        scope = CredentialScope(
            source=row["source"],
            row_id=row["row_id"],
            owner_or_system=row["owner_or_system"],
            provider=row["provider"],
        )
        manifest = _expected_manifest(binding)
        associated_data = frame_review_payload_associated_data(
            manifest,
            scope=scope,
            binding_id=row["binding_id"],
            binding_version=row["binding_version"],
            principal_id=row["principal_id"],
            schema_version=row["schema_version"],
            projector_version=row["projector_version"],
            reference_policy_version=row["reference_policy_version"],
            target_ref=row["target_ref"],
            model_ref=row["model_ref"],
            grant_ref=row["grant_ref"],
        )
        plaintext = crypto.open_secret(
            SealedMaterial(
                ciphertext=row["ciphertext"],
                nonce=row["nonce"],
                key_id=row["encryption_key_id"],
                algorithm_version=crypto.algorithm_version,
            ),
            associated_data,
        )
        if plaintext is None:
            return None
        payload = _decode_canonical_object(plaintext)
        if payload is None or not _payload_proof_matches(payload, row, manifest, proof_authority):
            return None

        snapshot_value = payload["execution_snapshot"]
        snapshot = _decode_execution_snapshot(snapshot_value)
        if snapshot is None:
            return None

        # This producer digest is over plain dataclass fields. It is distinct
        # from the public graph planner digest verified below.
        plain_snapshot_digest = hashlib.sha256(
            canonical_frame_review_bytes(snapshot_value)
        ).hexdigest()
        if (
            plain_snapshot_digest != binding.manifest_snapshot_digest
            or canonical_frame_review_bytes(snapshot)
            != canonical_frame_review_bytes(snapshot_value)
        ):
            return None

        target = payload["target"]
        model = payload["model"]
        prompt = payload["prompt"]
        request = payload["request"]
        grant = payload["grant"]
        credential = payload["credential"]
        projections = _decode_frozen_projections(target, model, prompt, request, grant, credential)
        if projections is None:
            return None
        (
            original_frame,
            original_target,
            original_text,
            chapter_digest,
            request_value,
            model_value,
            prompt_value,
            credential_binding,
        ) = projections
        if not _snapshot_projections_match(
            snapshot,
            binding,
            request_data,
            row,
            scope,
            original_frame,
            original_target,
            original_text,
            chapter_digest,
            request_value,
            model_value,
            prompt_value,
            grant,
            credential,
        ):
            return None

        plan = build_operation_graph_plan(
            _OPERATION,
            graph_version=_GRAPH_VERSION,
            items=(
                GraphItemInput(
                    item_id=binding.server_attempt_id,
                    target_id=f"{binding.protected_ref}#target",
                    task_id=binding.task_id,
                    unit_id=binding.unit_id,
                    step_ids=(binding.step_id,),
                    request_digest=binding.request_digest,
                    rule_digest=binding.rule_digest,
                    snapshot=snapshot,
                ),
            ),
        )
        planner_snapshot_digest = plan.items[0].snapshot_digest
        if planner_snapshot_digest != binding.snapshot_digest:
            return None

        return VerifiedFrameReviewExecutionInput(
            task_id=binding.task_id,
            unit_id=binding.unit_id,
            step_id=binding.step_id,
            submission_id=binding.submission_id,
            server_attempt_id=binding.server_attempt_id,
            prepared_payload_id=binding.prepared_payload_id,
            task_model_name=binding.task_model_name,
            snapshot=snapshot,
            request=_freeze_json_mapping(request_value),
            model=_freeze_json_mapping(model_value),
            prompt=_freeze_json_mapping(prompt_value),
            original_frame=_freeze_json_mapping(original_frame),
            original_text=original_text,
            chapter_digest=chapter_digest,
            credential_binding=credential_binding,
        )
    except (GenerationError, KeyError, TypeError, ValueError, UnicodeError):
        return None
    except Exception:
        # SQL, crypto, and proof failures are unresolved without leaking payloads.
        return None


def _valid_binding(binding: PersistedFrameReviewExecutionBinding) -> bool:
    scalar_fields = (
        binding.owner_id,
        binding.task_id,
        binding.unit_id,
        binding.step_id,
        binding.submission_id,
        binding.raw_key,
        binding.task_model_name,
        binding.task_model_id,
        binding.task_model_source,
        binding.task_model_owner_id,
        binding.server_attempt_id,
        binding.prepared_payload_id,
        binding.protected_ref,
    )
    if (
        any(not isinstance(value, str) or not value for value in scalar_fields)
        or binding.task_model_source not in {"system", "user"}
        or binding.task_model_owner_id
        != ("system" if binding.task_model_source == "system" else binding.owner_id)
        or binding.raw_key != f"frame-review:{binding.submission_id}"
        or binding.protected_ref != _protected_ref(binding.prepared_payload_id)
        or (binding.task_message_id is not None and not isinstance(binding.task_message_id, str))
        or type(binding.quoted_amount) is not int
        or binding.quoted_amount < 0
    ):
        return False
    return all(
        _is_digest(value)
        for value in (
            binding.request_digest,
            binding.rule_digest,
            binding.snapshot_digest,
            binding.manifest_snapshot_digest,
        )
    )


def _read_persisted_graph(session: Session, binding: PersistedFrameReviewExecutionBinding):
    task = session.execute(
        select(AITaskRow.__table__).where(AITaskRow.id == binding.task_id)
    ).mappings().one_or_none()
    units = session.execute(
        select(BillingUnitRow.__table__).where(
            BillingUnitRow.__table__.c.task_id == binding.task_id
        )
    ).mappings().all()
    if task is None or len(units) != 1 or units[0]["id"] != binding.unit_id:
        return None
    unit = units[0]
    steps = session.execute(
        select(ExecutionStepRow.__table__).where(
            ExecutionStepRow.__table__.c.billing_unit_id == binding.unit_id
        )
    ).mappings().all()
    submission = session.execute(
        select(TaskSubmissionRow.__table__).where(
            TaskSubmissionRow.id == binding.submission_id
        )
    ).mappings().one_or_none()
    if len(steps) != 1 or steps[0]["id"] != binding.step_id or submission is None:
        return None
    step = steps[0]
    try:
        request_data = json.loads(task["request_data"])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(request_data, Mapping):
        return None
    input_snapshot = step["input_snapshot"]
    if not isinstance(input_snapshot, Mapping) or set(input_snapshot) != _STEP_SNAPSHOT_KEYS:
        return None

    valid = all(
        (
            task["id"] == binding.task_id,
            task["user_id"] == binding.owner_id,
            task["type"] == _TASK_TYPE,
            task["model_name"] == binding.task_model_name,
            task["credit_cost"] == 0,
            (task["message_id"] or None) == binding.task_message_id,
            unit["id"] == binding.unit_id,
            unit["task_id"] == binding.task_id,
            unit["operation"] == _OPERATION,
            unit["step_graph_version"] == _GRAPH_VERSION,
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
            input_snapshot.get("manifest_snapshot_digest")
            == binding.manifest_snapshot_digest,
            submission["id"] == binding.submission_id,
            submission["user_id"] == binding.owner_id,
            submission["operation"] == _OPERATION,
            submission["idempotency_key"] == binding.raw_key,
            submission["request_digest"] == binding.request_digest,
            submission["task_ids"] == [binding.task_id],
            request_data.get("via") == "registered_generation_graph",
            request_data.get("mode") == "frame_review",
            request_data.get("operation") == _OPERATION,
            request_data.get("submission_id") == binding.submission_id,
            request_data.get("parent_attempt_id") == binding.submission_id,
            request_data.get("billing_unit_id") == binding.unit_id,
            request_data.get("server_attempt_id") == binding.server_attempt_id,
            request_data.get("prepared_payload_id") == binding.prepared_payload_id,
            request_data.get("protected_ref") == binding.protected_ref,
            request_data.get("item_id") == binding.server_attempt_id,
            request_data.get("unit_key") == unit["item_key"],
            request_data.get("graph_version") == _GRAPH_VERSION,
            request_data.get("step_graph_version") == _GRAPH_VERSION,
            request_data.get("request_digest") == binding.request_digest,
            request_data.get("rule_digest") == binding.rule_digest,
            request_data.get("snapshot_digest") == binding.snapshot_digest,
            request_data.get("manifest_snapshot_digest")
            == binding.manifest_snapshot_digest,
            request_data.get("target_ref") == f"{binding.protected_ref}#target",
            request_data.get("model_ref") == f"{binding.protected_ref}#model",
            request_data.get("grant_ref") == f"{binding.protected_ref}#grant",
            request_data.get("model_id") == binding.task_model_id,
            request_data.get("model_source") == binding.task_model_source,
            request_data.get("model_owner_id") == binding.task_model_owner_id,
            request_data.get("model_display_name") == binding.task_model_name,
            request_data.get("cost_per_call") == binding.quoted_amount,
        )
    )
    if not valid:
        return None
    return dict(task), dict(unit), dict(step), dict(submission), dict(request_data)


def _payload_row_matches_binding(
    row: Mapping[str, object], binding: PersistedFrameReviewExecutionBinding
) -> bool:
    owner_or_system = binding.task_model_owner_id
    return all(
        (
            row.get("prepared_payload_id") == binding.prepared_payload_id,
            row.get("actor_id") == binding.owner_id,
            row.get("operation") == _OPERATION,
            row.get("server_attempt_id") == binding.server_attempt_id,
            row.get("context_fingerprint") == binding.request_digest,
            row.get("target_ref") == f"{binding.protected_ref}#target",
            row.get("model_ref") == f"{binding.protected_ref}#model",
            row.get("grant_ref") == f"{binding.protected_ref}#grant",
            row.get("source") == binding.task_model_source,
            row.get("row_id") == binding.task_model_id,
            row.get("owner_or_system") == owner_or_system,
            isinstance(row.get("provider"), str) and bool(row["provider"]),
            isinstance(row.get("command_proof"), str)
            and _is_digest(row["command_proof"]),
            isinstance(row.get("ciphertext"), bytes) and bool(row["ciphertext"]),
            isinstance(row.get("nonce"), bytes) and bool(row["nonce"]),
            isinstance(row.get("encryption_key_id"), str)
            and bool(row["encryption_key_id"]),
            row.get("schema_version") == _CONTEXT_SCHEMA_VERSION,
            row.get("projector_version") == SNAPSHOT_VERSION,
            row.get("reference_policy_version") == _REFERENCE_POLICY_VERSION,
            type(row.get("binding_version")) is int and row["binding_version"] > 0,
            isinstance(row.get("binding_id"), str) and bool(row["binding_id"]),
            isinstance(row.get("principal_id"), str) and bool(row["principal_id"]),
        )
    )


def _expected_manifest(binding: PersistedFrameReviewExecutionBinding) -> dict[str, object]:
    return {
        "purpose": _PROOF_PURPOSE,
        "owner_id": binding.owner_id,
        "operation": _OPERATION,
        "submission_id": binding.submission_id,
        "task_id": binding.task_id,
        "unit_id": binding.unit_id,
        "step_id": binding.step_id,
        "server_attempt_id": binding.server_attempt_id,
        "prepared_payload_id": binding.prepared_payload_id,
        "request_digest": binding.request_digest,
        "rule_digest": binding.rule_digest,
        "snapshot_digest": binding.manifest_snapshot_digest,
        "target_ref": f"{binding.protected_ref}#target",
        "model_ref": f"{binding.protected_ref}#model",
        "grant_ref": f"{binding.protected_ref}#grant",
    }


def _payload_proof_matches(
    payload: Mapping[str, object],
    row: Mapping[str, object],
    expected_manifest: Mapping[str, object],
    authority: HmacProofAuthority,
) -> bool:
    if set(payload) != _PAYLOAD_KEYS:
        return False
    manifest = payload.get("manifest")
    if (
        payload.get("schema") != SNAPSHOT_VERSION
        or not isinstance(manifest, Mapping)
        or set(manifest) != _MANIFEST_KEYS
        or dict(manifest) != dict(expected_manifest)
        or payload.get("manifest_proof") != row.get("command_proof")
    ):
        return False
    proof = payload.get("manifest_proof")
    return authority.verify(
        _PROOF_PURPOSE,
        canonical_frame_review_bytes(manifest),
        proof,
    )


def _decode_canonical_object(plaintext: str) -> dict[str, object] | None:
    try:
        decoded = json.loads(
            plaintext,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if (
        not isinstance(decoded, dict)
        or canonical_frame_review_bytes(decoded).decode("utf-8") != plaintext
    ):
        return None
    return decoded


def _decode_execution_snapshot(value: object) -> FrozenExecutionSnapshot | None:
    if not isinstance(value, Mapping):
        return None
    if set(value) != {item.name for item in fields(FrozenExecutionSnapshot)}:
        return None
    model_value = value.get("model_ref")
    target_value = value.get("target_snapshot")
    grant_value = value.get("grant_snapshot")
    endpoint_value = value.get("endpoint_policy")
    if not all(
        isinstance(item, Mapping)
        for item in (model_value, target_value, grant_value, endpoint_value)
    ):
        return None
    model_ref = _decode_record(ModelReference, model_value)
    target = _decode_record(TargetSnapshot, target_value)
    grant = _decode_record(FrozenGrantSnapshot, grant_value)
    endpoint = _decode_record(EndpointPolicy, endpoint_value)
    adapters = value.get("adapter_config_versions")
    if (
        model_ref is None
        or target is None
        or grant is None
        or endpoint is None
        or not isinstance(adapters, list)
        or any(
            not isinstance(pair, list)
            or len(pair) != 2
            or any(not isinstance(part, str) or not part for part in pair)
            for pair in adapters
        )
    ):
        return None
    try:
        snapshot = FrozenExecutionSnapshot(
            model_ref=model_ref,
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
            adapter_config_versions=tuple(tuple(pair) for pair in adapters),
            endpoint_policy=endpoint,
        )
    except (KeyError, TypeError, ValueError):
        return None
    if (
        snapshot.model_category != "text"
        or not isinstance(snapshot.model_name, str)
        or not isinstance(snapshot.effective_prompt, str)
        or not isinstance(snapshot.effective_context, str)
        or type(snapshot.server_quote) is not int
        or not isinstance(snapshot.reference_snapshot_ref, str)
        or not isinstance(snapshot.credential_binding_ref, str)
    ):
        return None
    return snapshot


def _valid_frame_target_snapshot(target: TargetSnapshot) -> bool:
    return all(
        (
            target.kind == "frame_text",
            isinstance(target.chapter_id, str) and bool(target.chapter_id),
            isinstance(target.series_id, str) and bool(target.series_id),
            target.stable_storyboard_asset_id is None
            or (
                isinstance(target.stable_storyboard_asset_id, str)
                and bool(target.stable_storyboard_asset_id)
            ),
            type(target.compat_frame_index) is int and target.compat_frame_index >= 0,
            _is_digest(target.expected_text_digest),
            _is_digest(target.full_frame_digest),
            _is_digest(target.full_chapter_digest),
            target.asset_type is None,
            target.asset_id is None,
            target.media_identity is None,
            target.content_digest is None,
            target.notes_revision is None,
            target.expected_asset_image_url is None,
            target.same_series_target_digest is None,
        )
    )


def _decode_record(record_type: type[Any], value: Mapping[str, object]) -> Any | None:
    if set(value) != {item.name for item in fields(record_type)}:
        return None
    decoded = dict(value)
    if record_type is FrozenGrantSnapshot:
        provenance = decoded.get("access_provenance")
        if not isinstance(provenance, list) or any(not isinstance(item, str) for item in provenance):
            return None
        decoded["access_provenance"] = tuple(provenance)
    elif record_type is EndpointPolicy:
        paths = decoded.get("allowed_content_paths")
        if not isinstance(paths, list) or any(not isinstance(path, str) for path in paths):
            return None
        decoded["allowed_content_paths"] = tuple(paths)
        if type(decoded.get("allow_private_network")) is not bool:
            return None
    try:
        return record_type(**decoded)
    except (TypeError, ValueError):
        return None


def _decode_frozen_projections(
    target: object,
    model: object,
    prompt: object,
    request: object,
    grant: object,
    credential: object,
):
    if (
        not isinstance(target, Mapping)
        or set(target) != _TARGET_PAYLOAD_KEYS
        or not isinstance(target.get("snapshot"), Mapping)
        or not isinstance(target.get("frame"), Mapping)
        or not isinstance(target.get("original_text"), str)
        or not _is_digest(target.get("chapter_digest"))
        or not isinstance(model, Mapping)
        or set(model) != _MODEL_KEYS
        or not isinstance(prompt, Mapping)
        or not isinstance(request, Mapping)
        or not isinstance(grant, Mapping)
        or not isinstance(credential, Mapping)
        or set(credential) != _CREDENTIAL_KEYS
    ):
        return None
    frame = dict(target["frame"])
    original_target = _decode_record(TargetSnapshot, target["snapshot"])
    if original_target is None or not _valid_frame_target_snapshot(original_target):
        return None
    original_text = target["original_text"]
    chapter_digest = target["chapter_digest"]
    if not isinstance(frame.get("text"), str) or frame["text"] != original_text:
        return None
    try:
        canonical_request = canonicalize_request(
            _OPERATION,
            {key: value for key, value in request.items() if key != "operation"},
        )
        grant_snapshot = _decode_record(FrozenGrantSnapshot, grant)
    except (TypeError, ValueError, GenerationError):
        return None
    if grant_snapshot is None or canonical_request != dict(request):
        return None

    if canonical_request.get("prompt_id"):
        if (
            set(prompt) != _PROMPT_KEYS
            or not all(isinstance(prompt.get(key), str) for key in _PROMPT_KEYS)
            or prompt.get("source") not in {"system", "user"}
            or prompt.get("operation") != "review"
        ):
            return None
    elif (
        set(prompt) != _REQUEST_PROMPT_KEYS
        or prompt.get("source") != "request"
        or prompt.get("system_prompt") != ""
        or prompt.get("user_prompt") != ""
    ):
        return None

    if (
        not all(
            isinstance(model.get(key), str) and bool(model[key])
            for key in (
                "source",
                "row_id",
                "owner_or_system",
                "provider",
                "display_name",
            )
        )
        or model.get("source") not in {"system", "user"}
        or (
            model.get("wire_model") is not None
            and (
                not isinstance(model.get("wire_model"), str)
                or not model["wire_model"]
            )
        )
        or not isinstance(model.get("params"), Mapping)
        or type(model.get("quote")) is not int
        or model["quote"] < 0
    ):
        return None

    paths = credential.get("allowed_content_paths")
    if not isinstance(paths, list) or any(not isinstance(path, str) for path in paths):
        return None
    if (
        not isinstance(credential.get("binding_id"), str)
        or not credential["binding_id"]
        or not isinstance(credential.get("binding_version"), str)
        or not credential["binding_version"].isdigit()
        or not isinstance(credential.get("principal_id"), str)
        or not credential["principal_id"]
        or not isinstance(credential.get("endpoint_origin"), str)
        or not credential["endpoint_origin"]
        or not isinstance(credential.get("api_base_path"), str)
        or not credential["api_base_path"]
        or not isinstance(credential.get("endpoint_policy_version"), str)
        or not credential["endpoint_policy_version"]
        or (
            credential.get("rotation_provenance") is not None
            and not isinstance(credential.get("rotation_provenance"), str)
        )
    ):
        return None
    scope_value = credential.get("scope")
    if not isinstance(scope_value, Mapping) or set(scope_value) != {
        "source", "row_id", "owner_or_system", "provider"
    }:
        return None
    if any(
        not isinstance(scope_value.get(key), str) or not scope_value[key]
        for key in ("source", "row_id", "owner_or_system", "provider")
    ):
        return None
    credential_binding = CredentialBinding(
        source=scope_value["source"],
        row_id=scope_value["row_id"],
        owner_or_system=scope_value["owner_or_system"],
        provider=scope_value["provider"],
        original_principal_id=credential["principal_id"],
        endpoint_origin=credential["endpoint_origin"],
        allowed_content_paths=tuple(paths),
        binding_version=str(credential["binding_version"]),
        rotation_provenance=credential["rotation_provenance"],
    )
    return (
        frame,
        original_target,
        original_text,
        chapter_digest,
        canonical_request,
        dict(model),
        dict(prompt),
        credential_binding,
    )


def _snapshot_projections_match(
    snapshot: FrozenExecutionSnapshot,
    binding: PersistedFrameReviewExecutionBinding,
    request_data: Mapping[str, object],
    row: Mapping[str, object],
    scope: CredentialScope,
    original_frame: Mapping[str, object],
    original_target: TargetSnapshot,
    original_text: str,
    chapter_digest: str,
    request: Mapping[str, object],
    model: Mapping[str, object],
    prompt: Mapping[str, object],
    grant: Mapping[str, object],
    credential: Mapping[str, object],
) -> bool:
    target = snapshot.target_snapshot
    try:
        target_value = {
            field.name: getattr(target, field.name) for field in fields(TargetSnapshot)
        }
        safe_target = request_data.get("target_snapshot")
        prompt_context = _decode_canonical_object(snapshot.effective_context)
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(safe_target, Mapping) or not isinstance(prompt_context, Mapping):
        return False
    adapters = dict(snapshot.adapter_config_versions)
    expected_context = {
        "prompt_user": prompt.get("user_prompt"),
        "request": {
            "source_message_id": request.get("message_id", ""),
            "error_reason": request.get("error_reason", ""),
            "asset_context": request.get("asset_context", ""),
            "prev_context": request.get("prev_context", ""),
            "enable_thinking": request.get("enable_thinking") is True,
        },
        "original_text": original_text,
        "original_frame": dict(original_frame),
        "storyboard_asset": prompt_context.get("storyboard_asset"),
    }
    storyboard_asset = prompt_context.get("storyboard_asset")
    if target.stable_storyboard_asset_id is None:
        if storyboard_asset is not None:
            return False
    elif (
        not isinstance(storyboard_asset, Mapping)
        or storyboard_asset.get("id") != target.stable_storyboard_asset_id
        or storyboard_asset.get("chapter_id") != target.chapter_id
        or storyboard_asset.get("series_id") != target.series_id
        or storyboard_asset.get("frame_index") != target.compat_frame_index
    ):
        return False

    return all(
        (
            target.kind == "frame_text",
            target.chapter_id == request.get("chapter_id"),
            target.compat_frame_index == request.get("frame_index"),
            target.expected_text_digest == _sha256_json(original_text),
            target.full_frame_digest == _sha256_json(original_frame),
            _is_digest(target.full_chapter_digest),
            _is_digest(chapter_digest),
            canonical_frame_review_bytes(target_value)
            == canonical_frame_review_bytes(safe_target),
            canonical_frame_review_bytes(target_value)
            == canonical_frame_review_bytes(request_data.get("target_snapshot")),
            canonical_frame_review_bytes(target_value)
            == canonical_frame_review_bytes(original_target),
            snapshot.model_category == "text",
            snapshot.model_name == (model.get("wire_model") or model.get("display_name")),
            snapshot.model_params == model.get("params"),
            snapshot.server_quote == binding.quoted_amount == model.get("quote"),
            snapshot.model_ref.model_id == binding.task_model_id == model.get("row_id"),
            snapshot.model_ref.owner_id == binding.task_model_owner_id == model.get("owner_or_system"),
            snapshot.model_ref.provider == scope.provider == model.get("provider"),
            model.get("display_name") == binding.task_model_name,
            model.get("source") == scope.source,
            model.get("row_id") == scope.row_id,
            prompt.get("id") == snapshot.prompt_ref,
            prompt.get("system_prompt") == snapshot.effective_prompt,
            (
                prompt.get("source") == "request"
                and not request.get("prompt_id")
                and snapshot.prompt_ref is None
                and prompt.get("system_prompt") == ""
                and prompt.get("user_prompt") == ""
            )
            or (
                prompt.get("operation") == "review"
                and prompt.get("source") in {"system", "user"}
                and prompt.get("owner_or_system")
                == ("system" if prompt.get("source") == "system" else binding.owner_id)
                and prompt.get("category") == "ai-review"
            ),
            grant.get("actor_id") == binding.owner_id,
            grant.get("series_id") == target.series_id,
            canonical_frame_review_bytes(snapshot.grant_snapshot)
            == canonical_frame_review_bytes(grant),
            snapshot.reference_snapshot_ref == f"{binding.protected_ref}#references",
            snapshot.credential_binding_ref
            == f"{row['binding_id']}:v{row['binding_version']}",
            snapshot.endpoint_policy.provider_origin == credential.get("endpoint_origin"),
            tuple(snapshot.endpoint_policy.allowed_content_paths)
            == tuple(credential.get("allowed_content_paths", ())),
            snapshot.endpoint_policy.allow_private_network is False,
            adapters.get("snapshot") == SNAPSHOT_VERSION,
            adapters.get("credential_binding_version") == str(row["binding_version"]),
            adapters.get("endpoint_policy") == credential.get("endpoint_policy_version"),
            adapters.get("api_base_path") == credential.get("api_base_path"),
            credential.get("scope") == scope.identity(),
            credential.get("binding_id") == row.get("binding_id"),
            credential.get("binding_version") == str(row["binding_version"]),
            credential.get("principal_id") == row.get("principal_id"),
            canonical_frame_review_bytes(prompt_context)
            == canonical_frame_review_bytes(expected_context),
            canonical_frame_review_bytes(model.get("params"))
            == canonical_frame_review_bytes(snapshot.model_params),
        )
    )


def _canonical_json(value: object) -> str:
    return canonical_frame_review_bytes(value).decode("utf-8")


def _sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_frame_review_bytes(value)).hexdigest()


def _protected_ref(payload_id: str) -> str:
    return f"prepared-payload-v1/{len(payload_id)}:{payload_id}"


def _freeze_json_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    def freeze(item: object) -> object:
        if isinstance(item, Mapping):
            return MappingProxyType({str(key): freeze(nested) for key, nested in item.items()})
        if isinstance(item, list):
            return tuple(freeze(nested) for nested in item)
        return item

    return freeze(value)  # type: ignore[return-value]


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


__all__ = [
    "PersistedFrameReviewExecutionBinding",
    "VerifiedFrameReviewExecutionInput",
    "read_original_frame_review_execution_input",
]
