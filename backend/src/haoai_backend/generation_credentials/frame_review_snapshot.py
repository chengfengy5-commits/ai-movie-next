"""Freeze one text-review request and protect its execution inputs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from enum import Enum
from sqlalchemy import select
from sqlalchemy.orm import Session

from haoai_backend.generation_catalog.admission_types import (
    AdmissionContext,
    AdmissionPreparationFacts,
    PreparedItemFacts,
    prompt_facts,
)
from haoai_backend.generation_catalog.domain import ModelSource, PromptOperation, PromptSelection
from haoai_backend.generation_catalog.tables import (
    chapters,
    model_configs,
    prompt_configs,
    storyboard_assets,
    system_models,
    system_prompts,
)
from haoai_backend.generation_contracts.types import (
    EndpointPolicy as SnapshotEndpointPolicy,
    FrozenExecutionSnapshot,
    FrozenGrantSnapshot,
    ModelReference,
    TargetSnapshot,
)
from haoai_backend.generation_credentials.application import (
    CapturedCredentialBinding,
    CredentialRegistry,
    RegistryCapabilities,
)
from haoai_backend.generation_credentials.domain import CredentialScope, PreparedPayload
from haoai_backend.series_access.domain import TeamMembership, can_enter_claimed_series
from haoai_backend.series_access.tables import series, team_members


SNAPSHOT_VERSION = "frame-review-snapshot-v1"
_PROOF_PURPOSE = "prepared-frame-review-input-v1"
_PAYLOAD_PURPOSE = "protected-frame-review-input-v1"


@dataclass(frozen=True, slots=True)
class FrameReviewSnapshot:
    snapshot: FrozenExecutionSnapshot
    target: TargetSnapshot
    submission_id: str
    server_attempt_id: str
    prepared_payload_id: str
    protected_ref: str
    request_digest: str
    rule_digest: str
    manifest_snapshot_digest: str
    payload: PreparedPayload


def build_frame_review_snapshot(
    session: Session,
    *,
    actor_id: str,
    request: Mapping[str, object],
    context: AdmissionContext,
    preparation: AdmissionPreparationFacts,
    registry_capabilities: RegistryCapabilities,
    submission_id: str,
    task_id: str,
    unit_id: str,
    step_id: str,
    server_attempt_id: str,
    prepared_payload_id: str,
    original_message_id: str,
) -> FrameReviewSnapshot:
    """Build the exact immutable model, prompt, target, grant, and credential refs."""

    item = _prepared_item(preparation)
    request_digest = context.fingerprint.ordered_nonsecret_payload_digest
    chapter_id = str(request["chapter_id"])
    chapter = session.execute(
        select(chapters)
        .where(chapters.c.id == chapter_id)
        .with_for_update()
    ).mappings().one_or_none()
    if chapter is None or chapter["series_id"] != item.target.series_id:
        raise ValueError("locked review chapter changed")

    chapter_content = chapter["content"]
    if (
        not isinstance(chapter_content, str)
        or _sha256_text(chapter_content) != item.target.chapter_digest
    ):
        raise ValueError("locked review chapter content changed")
    frames = json.loads(chapter_content)
    frame_index = item.target.frame_index
    if (
        not isinstance(frames, list)
        or type(frame_index) is not int
        or not 0 <= frame_index < len(frames)
    ):
        raise ValueError("review frame index is outside the locked chapter")
    frame = frames[frame_index]
    if not isinstance(frame, dict):
        raise ValueError("review frame must be an object")
    original_text = frame.get("text")
    if not isinstance(original_text, str):
        raise ValueError("review frame text is unavailable")
    stable_storyboard_id = _stable_storyboard_id(frames, frame_index)
    storyboard = None
    if stable_storyboard_id is not None:
        storyboard = (
            session.execute(
                select(storyboard_assets)
                .where(
                    storyboard_assets.c.id == stable_storyboard_id,
                    storyboard_assets.c.chapter_id == chapter_id,
                    storyboard_assets.c.series_id == item.target.series_id,
                )
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        if storyboard is None:
            raise ValueError("stable review storyboard is unavailable")

    target = TargetSnapshot(
        kind="frame_text",
        chapter_id=chapter_id,
        series_id=str(item.target.series_id),
        stable_storyboard_asset_id=stable_storyboard_id,
        compat_frame_index=frame_index,
        expected_text_digest=_sha256_json(original_text),
        full_frame_digest=_sha256_json(frame),
        full_chapter_digest=_sha256_json(frames),
    )
    grant = _capture_grant(session, actor_id=actor_id, series_id=str(item.target.series_id))
    model, scope, endpoint = _capture_model(
        session,
        actor_id=actor_id,
        item=item,
        capabilities=registry_capabilities,
    )
    prompt_id, prompt_values = _capture_prompt(
        session,
        actor_id=actor_id,
        request=request,
        item=item,
    )

    protected_ref = _protected_ref(prepared_payload_id)
    target_ref = f"{protected_ref}#target"
    model_ref = f"{protected_ref}#model"
    grant_ref = f"{protected_ref}#grant"
    reference_ref = f"{protected_ref}#references"
    model_name = model["wire_model"] or item.model.display_name
    snapshot = FrozenExecutionSnapshot(
        model_ref=ModelReference(
            owner_id=scope.owner_or_system,
            provider=scope.provider,
            model_id=scope.row_id,
        ),
        model_category="text",
        model_name=str(model_name),
        model_params=model["params"],
        prompt_ref=prompt_id,
        effective_prompt=prompt_values["system_prompt"],
        effective_context=_canonical_json(
            {
                "prompt_user": prompt_values["user_prompt"],
                "request": {
                    "source_message_id": original_message_id,
                    "error_reason": str(request.get("error_reason") or ""),
                    "asset_context": str(request.get("asset_context") or ""),
                    "prev_context": str(request.get("prev_context") or ""),
                    "enable_thinking": request.get("enable_thinking") is True,
                },
                "original_text": original_text,
                "original_frame": frame,
                "storyboard_asset": dict(storyboard) if storyboard is not None else None,
            }
        ),
        server_quote=item.quote_amount,
        reference_snapshot_ref=reference_ref,
        target_snapshot=target,
        credential_binding_ref=f"{endpoint.binding_id}:v{endpoint.binding.binding_version}",
        grant_snapshot=grant,
        adapter_config_versions=(
            ("snapshot", SNAPSHOT_VERSION),
            ("credential_binding_version", endpoint.binding.binding_version),
            ("endpoint_policy", endpoint.endpoint_policy.policy_version),
            ("api_base_path", endpoint.endpoint_policy.api_base_path),
        ),
        endpoint_policy=SnapshotEndpointPolicy(
            provider_origin=endpoint.endpoint_policy.provider_origin,
            allowed_content_paths=endpoint.endpoint_policy.allowed_content_paths,
            allow_private_network=False,
        ),
    )
    rule_digest = _sha256_json(
        {
            "operation": "frame.review",
            "graph_version": "frame-review-v1",
            "snapshot_version": SNAPSHOT_VERSION,
            "model_source": item.model.source.value,
            "model_id": item.model.catalog_id,
            "quote": item.quote_amount,
            "prompt_digest": item.prompt_digest,
            "target_digest": target.full_frame_digest,
        }
    )
    manifest_snapshot_digest = _sha256_json(_plain(snapshot))
    manifest = {
        "purpose": _PROOF_PURPOSE,
        "owner_id": actor_id,
        "operation": "frame.review",
        "submission_id": submission_id,
        "task_id": task_id,
        "unit_id": unit_id,
        "step_id": step_id,
        "server_attempt_id": server_attempt_id,
        "prepared_payload_id": prepared_payload_id,
        "request_digest": request_digest,
        "rule_digest": rule_digest,
        "snapshot_digest": manifest_snapshot_digest,
        "target_ref": target_ref,
        "model_ref": model_ref,
        "grant_ref": grant_ref,
    }
    authority = registry_capabilities.proof_authority
    crypto = registry_capabilities.crypto
    if authority is None or crypto is None or not crypto.ready():
        raise ValueError("protected frame review capabilities are unavailable")
    command_bytes = _canonical_bytes(manifest)
    command_proof = authority.sign(_PROOF_PURPOSE, command_bytes)
    payload_value = {
        "schema": SNAPSHOT_VERSION,
        "manifest": manifest,
        "manifest_proof": command_proof,
        "execution_snapshot": _plain(snapshot),
        "target": {
            "snapshot": _plain(target),
            "frame": frame,
            "original_text": original_text,
            "chapter_digest": item.target.chapter_digest,
        },
        "model": model,
        "prompt": prompt_values,
        "request": _plain(context.canonical_request),
        "grant": _plain(grant),
        "credential": {
            "scope": scope.identity(),
            "binding_id": endpoint.binding_id,
            "binding_version": endpoint.binding.binding_version,
            "principal_id": endpoint.binding.original_principal_id,
            "endpoint_origin": endpoint.endpoint_policy.provider_origin,
            "api_base_path": endpoint.endpoint_policy.api_base_path,
            "allowed_content_paths": endpoint.endpoint_policy.allowed_content_paths,
            "endpoint_policy_version": endpoint.endpoint_policy.policy_version,
            "rotation_provenance": endpoint.binding.rotation_provenance,
        },
    }
    associated_data = frame_review_payload_associated_data(
        manifest,
        scope=scope,
        binding_id=endpoint.binding_id,
        binding_version=int(endpoint.binding.binding_version),
        principal_id=endpoint.binding.original_principal_id,
        schema_version=context.fingerprint.schema_version,
        projector_version=SNAPSHOT_VERSION,
        reference_policy_version="frame-review-no-references-v1",
        target_ref=target_ref,
        model_ref=model_ref,
        grant_ref=grant_ref,
    )
    sealed = crypto.seal_secret(_canonical_bytes(payload_value).decode("utf-8"), associated_data)
    payload = PreparedPayload(
        actor_id=actor_id,
        operation="frame.review",
        server_attempt_id=server_attempt_id,
        scope=scope,
        binding_id=endpoint.binding_id,
        binding_version=int(endpoint.binding.binding_version),
        principal_id=endpoint.binding.original_principal_id,
        context_fingerprint=request_digest,
        schema_version=context.fingerprint.schema_version,
        projector_version=SNAPSHOT_VERSION,
        reference_policy_version="frame-review-no-references-v1",
        target_ref=target_ref,
        model_ref=model_ref,
        grant_ref=grant_ref,
        command_proof=command_proof,
        ciphertext=sealed.ciphertext,
        nonce=sealed.nonce,
        encryption_key_id=sealed.key_id,
    )
    return FrameReviewSnapshot(
        snapshot=snapshot,
        target=target,
        submission_id=submission_id,
        server_attempt_id=server_attempt_id,
        prepared_payload_id=prepared_payload_id,
        protected_ref=protected_ref,
        request_digest=request_digest,
        rule_digest=rule_digest,
        manifest_snapshot_digest=manifest_snapshot_digest,
        payload=payload,
    )


def frame_review_payload_associated_data(
    manifest: Mapping[str, object],
    *,
    scope: CredentialScope,
    binding_id: str,
    binding_version: int,
    principal_id: str,
    schema_version: str,
    projector_version: str,
    reference_policy_version: str,
    target_ref: str,
    model_ref: str,
    grant_ref: str,
) -> bytes:
    """Rebuild the exact v1 associated data for a persisted review payload.

    In the original v1 envelope, the manifest spread follows ``purpose`` and
    replaces that key with its proof purpose. Keep that ordering for stored
    ciphertext compatibility.
    """

    return canonical_frame_review_bytes(
        {
            "purpose": _PAYLOAD_PURPOSE,
            **manifest,
            "scope": scope.identity(),
            "binding_id": binding_id,
            "binding_version": binding_version,
            "principal_id": principal_id,
            "schema_version": schema_version,
            "projector_version": projector_version,
            "reference_policy_version": reference_policy_version,
            "target_ref": target_ref,
            "model_ref": model_ref,
            "grant_ref": grant_ref,
        }
    )


def canonical_frame_review_bytes(value: object) -> bytes:
    """Serialize a frozen frame-review value using the producer's v1 format."""

    return _canonical_bytes(value)


def _prepared_item(preparation: AdmissionPreparationFacts) -> PreparedItemFacts:
    if (
        preparation.operation != "frame.review"
        or preparation.executable
        or preparation.reserved_amount != 0
        or preparation.credential_binding_status != "binding_unverified"
        or len(preparation.items) != 1
        or preparation.items[0].index != 0
    ):
        raise ValueError("locked review admission facts are invalid")
    return preparation.items[0]


def _capture_model(
    session: Session,
    *,
    actor_id: str,
    item: PreparedItemFacts,
    capabilities: RegistryCapabilities,
) -> tuple[dict[str, object], CredentialScope, CapturedCredentialBinding]:
    if item.model.source is ModelSource.SYSTEM:
        table = system_models
        owner_or_system = "system"
        columns = (
            table.c.id,
            table.c.category,
            table.c.name,
            table.c.provider,
            table.c.model,
            table.c.base_url,
            table.c.params,
            table.c.is_active,
        )
        statement = select(*columns).where(table.c.id == item.model.catalog_id)
    else:
        table = model_configs
        owner_or_system = actor_id
        columns = (
            table.c.id,
            table.c.user_id,
            table.c.category,
            table.c.name,
            table.c.provider,
            table.c.model,
            table.c.base_url,
            table.c.params,
        )
        statement = select(*columns).where(
            table.c.id == item.model.catalog_id,
            table.c.user_id == actor_id,
        )
    row = session.execute(statement.with_for_update()).mappings().one_or_none()
    if row is None:
        raise ValueError("selected text model is unavailable")
    params = _parse_params(row["params"])
    facts_params = dict(item.model.quote.effective_parameters)
    params.update(facts_params)
    if (
        row["category"] != "text"
        or not isinstance(row["provider"], str)
        or (item.model.source is ModelSource.SYSTEM and not row["is_active"])
        or (item.model.source is ModelSource.USER and row["user_id"] != actor_id)
        or row["name"] != item.model.display_name
        or row["model"] != item.model.wire_model
    ):
        raise ValueError("selected text model changed after admission")
    scope = CredentialScope(
        source=item.model.source.value,
        row_id=item.model.catalog_id,
        owner_or_system=owner_or_system,
        provider=str(row["provider"]),
    )
    endpoint = CredentialRegistry(capabilities).capture_original_binding(session, scope)
    if endpoint is None:
        raise ValueError("selected model credential binding is unavailable")
    if endpoint.endpoint_policy.provider_origin is None:
        raise ValueError("selected model endpoint is unavailable")
    model = {
        "source": item.model.source.value,
        "row_id": item.model.catalog_id,
        "owner_or_system": owner_or_system,
        "provider": str(row["provider"]),
        "display_name": item.model.display_name,
        "wire_model": item.model.wire_model,
        "params": params,
        "quote": item.quote_amount,
    }
    return model, scope, endpoint


def _capture_prompt(
    session: Session,
    *,
    actor_id: str,
    request: Mapping[str, object],
    item: PreparedItemFacts,
) -> tuple[str | None, dict[str, object]]:
    prompt_facts_value = item.prompt
    prompt_id = request.get("prompt_id") or None
    if prompt_facts_value is None:
        if prompt_id is not None:
            raise ValueError("selected prompt was not prepared")
        return None, {"source": "request", "system_prompt": "", "user_prompt": ""}
    if prompt_facts_value.prompt_id != prompt_id:
        raise ValueError("selected prompt identity changed")
    if prompt_facts_value.source == ModelSource.SYSTEM.value:
        table = system_prompts
        statement = select(table).where(table.c.id == prompt_id)
        owner_id = "system"
    elif prompt_facts_value.source == ModelSource.USER.value:
        table = prompt_configs
        statement = select(table).where(
            table.c.id == prompt_id,
            table.c.user_id == actor_id,
        )
        owner_id = actor_id
    else:
        raise ValueError("selected prompt source is invalid")
    row = session.execute(statement.with_for_update()).mappings().one_or_none()
    if row is None or (
        prompt_facts_value.source == ModelSource.SYSTEM.value and not row["is_active"]
    ):
        raise ValueError("selected prompt is unavailable")
    selection = PromptSelection(
        prompt_id=str(prompt_id),
        operation=PromptOperation.REVIEW,
        source=ModelSource(prompt_facts_value.source),
        category=str(row["category"]),
        display_name=str(row["name"]),
        system_prompt=row["system_prompt"] or "",
        user_prompt=row["user_prompt"] or "",
    )
    if prompt_facts(selection) != prompt_facts_value:
        raise ValueError("selected prompt changed after admission")
    return str(prompt_id), {
        "id": str(prompt_id),
        "owner_or_system": owner_id,
        "source": prompt_facts_value.source,
        "operation": "review",
        "category": selection.category,
        "display_name": selection.display_name,
        "system_prompt": selection.system_prompt,
        "user_prompt": selection.user_prompt,
    }


def _capture_grant(session: Session, *, actor_id: str, series_id: str) -> FrozenGrantSnapshot:
    series_row = session.execute(
        select(series).where(series.c.id == series_id).with_for_update()
    ).mappings().one_or_none()
    if series_row is None:
        raise ValueError("review series is unavailable")
    team_id = series_row["team_id"]
    memberships = ()
    if team_id:
        memberships = tuple(
            dict(row)
            for row in session.execute(
                select(team_members)
                .where(team_members.c.team_id == team_id, team_members.c.user_id == actor_id)
                .order_by(team_members.c.id)
                .with_for_update()
            ).mappings().all()
        )
    if len(memberships) > 1:
        raise ValueError("review series membership is ambiguous")
    is_owner = series_row["user_id"] == actor_id
    is_member = bool(memberships)
    claimed_by = series_row["claimed_by"]
    claim_permission = False
    if claimed_by and claimed_by != actor_id:
        if not memberships or not can_enter_claimed_series(
            TeamMembership(
                role=memberships[0]["role"],
                permissions=memberships[0]["permissions"],
            )
        ):
            raise ValueError("claimed review series access is denied")
        claim_permission = True
    if not is_owner and not is_member:
        raise ValueError("review series access is denied")
    provenance = ["series_owner" if is_owner else "team_member"]
    if claim_permission:
        provenance.append("claimed_series_permission")
    return FrozenGrantSnapshot(
        actor_id=actor_id,
        series_id=series_id,
        team_id=str(team_id) if team_id is not None else None,
        access_provenance=tuple(provenance),
    )


def _stable_storyboard_id(frames: list[object], frame_index: int) -> str | None:
    frame = frames[frame_index]
    if not isinstance(frame, Mapping):
        raise ValueError("review frame must be an object")
    refs = frame.get("storyboard")
    if "storyboard" not in frame or refs == []:
        return None
    if not isinstance(refs, list) or not refs or not isinstance(refs[0], str) or not refs[0]:
        raise ValueError("review frame has an invalid stable storyboard id")
    stable_id = refs[0]
    identities = [
        identity
        for value in frames
        if isinstance(value, Mapping)
        and isinstance(value.get("storyboard"), list)
        for identity in value["storyboard"]
        if isinstance(identity, str) and identity
    ]
    if identities.count(stable_id) != 1:
        raise ValueError("review storyboard id is missing or ambiguous")
    return stable_id


def _parse_params(raw: object) -> dict[str, object]:
    if raw is None or raw == "":
        return {}
    value = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(value, Mapping):
        raise ValueError("selected model parameters are invalid")
    return dict(value)


def _protected_ref(payload_id: str) -> str:
    return f"prepared-payload-v1/{len(payload_id)}:{payload_id}"


def _canonical_json(value: object) -> str:
    return _canonical_bytes(value).decode("utf-8")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        _plain(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _plain(value: object) -> object:
    if is_dataclass(value):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported frozen value: {type(value).__name__}")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()
