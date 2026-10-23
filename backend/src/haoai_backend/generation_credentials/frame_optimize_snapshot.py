"""Freeze and protect every input in one registered frame.optimize batch."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime
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
    EndpointPolicy,
    FrozenExecutionSnapshot,
    FrozenGrantSnapshot,
    ModelReference,
    TargetSnapshot,
)
from haoai_backend.generation_core.graph_planning import OperationGraphPlan
from haoai_backend.generation_credentials.application import (
    CapturedCredentialBinding,
    CredentialRegistry,
    RegistryCapabilities,
)
from haoai_backend.generation_credentials.domain import (
    CredentialScope,
    PreparedPayload,
)
from haoai_backend.series_access.domain import TeamMembership, can_enter_claimed_series
from haoai_backend.series_access.tables import series, team_members


SNAPSHOT_VERSION = "frame-optimize-snapshot-v1"
_PROOF_PURPOSE = "prepared-frame-optimize-input-v1"
_PAYLOAD_PURPOSE = "protected-frame-optimize-input-v1"
_REFERENCE_POLICY_VERSION = "frame-optimize-no-references-v1"
_GRAPH_VERSION = "frame-optimize-v1"


@dataclass(frozen=True, slots=True)
class FrameOptimizeItemIdentity:
    """Server-generated identities for one requested frame."""

    item_id: str
    unit_id: str
    step_id: str
    payload_id: str


@dataclass(frozen=True, slots=True)
class FrameOptimizeSnapshotCandidate:
    """Immutable request and source facts before batch proof and AEAD sealing."""

    order: int
    frame_index: int
    identity: FrameOptimizeItemIdentity
    target: TargetSnapshot
    snapshot: FrozenExecutionSnapshot
    request_digest: str
    rule_digest: str
    model: Mapping[str, object]
    prompt: Mapping[str, object]
    request: Mapping[str, object]
    grant: FrozenGrantSnapshot
    scope: CredentialScope
    endpoint: CapturedCredentialBinding
    database_frame: Mapping[str, object]
    database_chapter: tuple[object, ...]
    database_text: str
    requested_text: str


@dataclass(frozen=True, slots=True)
class FrameOptimizeProtectedItem:
    """One encrypted payload bound to its ordered unit and step."""

    candidate: FrameOptimizeSnapshotCandidate
    target_ref: str
    model_ref: str
    grant_ref: str
    snapshot_digest: str
    manifest_snapshot_digest: str
    manifest_item: Mapping[str, object]
    payload: PreparedPayload


@dataclass(frozen=True, slots=True)
class FrameOptimizeProtectedBatch:
    """A common authenticated manifest and all independently bound payloads."""

    manifest: Mapping[str, object]
    manifest_proof: str
    manifest_snapshot_digest: str
    items: tuple[FrameOptimizeProtectedItem, ...]


def build_frame_optimize_snapshot_candidates(
    session: Session,
    *,
    actor_id: str,
    request: Mapping[str, object],
    context: AdmissionContext,
    preparation: AdmissionPreparationFacts,
    registry_capabilities: RegistryCapabilities,
    submission_id: str,
    task_id: str,
    identities: Sequence[FrameOptimizeItemIdentity],
) -> tuple[FrameOptimizeSnapshotCandidate, ...]:
    """Capture locked chapter, catalog, prompt, grant, and credential facts."""

    if (
        not isinstance(session, Session)
        or preparation.operation != "frame.optimize"
        or context.operation != "frame.optimize"
        or preparation.fingerprint != context.fingerprint
        or len(identities) != len(preparation.items)
        or len(identities) != len(context.canonical_request["frames"])
        or not identities
        or len(identities) > 30
    ):
        raise ValueError("frame optimization preparation is incomplete")

    chapter_id = str(request["chapter_id"])
    chapter = session.execute(
        select(chapters).where(chapters.c.id == chapter_id).with_for_update()
    ).mappings().one_or_none()
    if chapter is None or not isinstance(chapter["content"], str):
        raise ValueError("locked optimize chapter is unavailable")
    raw_chapter = chapter["content"]
    if _sha256_text(raw_chapter) != preparation.items[0].target.chapter_digest:
        raise ValueError("locked optimize chapter changed after admission")
    chapter_frames = json.loads(raw_chapter)
    if not isinstance(chapter_frames, list):
        raise ValueError("locked optimize chapter content is invalid")

    model, scope, endpoint = _capture_model(
        session,
        actor_id=actor_id,
        item=preparation.items[0],
        capabilities=registry_capabilities,
    )
    prompt_id, prompt = _capture_prompt(
        session,
        actor_id=actor_id,
        request=request,
        item=preparation.items[0],
    )
    grant = _capture_grant(
        session,
        actor_id=actor_id,
        series_id=str(preparation.items[0].target.series_id),
    )

    canonical_request = context.canonical_request
    requested_frames = canonical_request["frames"]
    chapter_texts = canonical_request["chapter_texts"]
    if not isinstance(requested_frames, (tuple, list)) or not isinstance(
        chapter_texts, (tuple, list)
    ):
        raise ValueError("canonical optimize frames are unavailable")

    candidates: list[FrameOptimizeSnapshotCandidate] = []
    for order, (prepared, requested, identity) in enumerate(
        zip(preparation.items, requested_frames, identities, strict=True)
    ):
        if (
            prepared.index != order
            or prepared.model != preparation.items[0].model
            or prepared.prompt != preparation.items[0].prompt
            or prepared.target.chapter_id != chapter_id
            or prepared.target.series_id != chapter["series_id"]
            or not isinstance(requested, Mapping)
            or type(requested.get("index")) is not int
        ):
            raise ValueError("optimize item preparation changed")
        frame_index = int(requested["index"])
        if prepared.target.frame_index != frame_index or not 0 <= frame_index < len(chapter_frames):
            raise ValueError("locked optimize target index changed")
        raw_frame = chapter_frames[frame_index]
        if not isinstance(raw_frame, Mapping):
            raise ValueError("locked optimize target frame is invalid")
        database_text = raw_frame.get("text")
        requested_text = requested.get("text")
        asset_context = requested.get("asset_context", "")
        if not isinstance(database_text, str) or not isinstance(requested_text, str) or not isinstance(
            asset_context, str
        ):
            raise ValueError("optimize frame text is unavailable")

        stable_id = _stable_storyboard_id(chapter_frames, frame_index)
        if stable_id is not None:
            asset = session.execute(
                select(storyboard_assets)
                .where(
                    storyboard_assets.c.id == stable_id,
                    storyboard_assets.c.chapter_id == chapter_id,
                    storyboard_assets.c.series_id == chapter["series_id"],
                )
                .with_for_update()
            ).mappings().one_or_none()
            if asset is None:
                raise ValueError("stable optimize storyboard is unavailable")

        previous = tuple(chapter_texts[max(0, frame_index - 40) : frame_index])
        if any(not isinstance(value, str) for value in previous):
            raise ValueError("optimize chapter context is invalid")
        target = TargetSnapshot(
            kind="frame_text",
            chapter_id=chapter_id,
            series_id=str(chapter["series_id"]),
            stable_storyboard_asset_id=stable_id,
            compat_frame_index=frame_index,
            expected_text_digest=_sha256_json(database_text),
            full_frame_digest=_sha256_json(raw_frame),
            full_chapter_digest=_sha256_json(chapter_frames),
        )
        effective_context = _canonical_json(
            {
                "prompt_user": prompt["user_prompt"],
                "request": {
                    "frame_index": frame_index,
                    "text": requested_text,
                    "asset_context": asset_context,
                    "previous_frames": previous,
                    "previous_context": "\n".join(previous),
                    "enable_thinking": canonical_request["enable_thinking"],
                    "concurrency": canonical_request["concurrency"],
                },
                "database_target": {
                    "frame": raw_frame,
                    "text": database_text,
                    "chapter_digest": target.full_chapter_digest,
                },
            }
        )
        snapshot = FrozenExecutionSnapshot(
            model_ref=ModelReference(
                owner_id=scope.owner_or_system,
                provider=scope.provider,
                model_id=scope.row_id,
            ),
            model_category="text",
            model_name=str(model["wire_model"] or prepared.model.display_name),
            model_params=model["params"],
            prompt_ref=prompt_id,
            effective_prompt=str(prompt["system_prompt"]),
            effective_context=effective_context,
            server_quote=prepared.quote_amount,
            reference_snapshot_ref=_payload_ref(identity.payload_id, "references"),
            target_snapshot=target,
            credential_binding_ref=(
                f"{endpoint.binding_id}:v{endpoint.binding.binding_version}"
            ),
            grant_snapshot=grant,
            adapter_config_versions=(
                ("snapshot", SNAPSHOT_VERSION),
                ("credential_binding_version", endpoint.binding.binding_version),
                ("endpoint_policy", endpoint.endpoint_policy.policy_version),
                ("api_base_path", endpoint.endpoint_policy.api_base_path),
            ),
            endpoint_policy=EndpointPolicy(
                provider_origin=endpoint.endpoint_policy.provider_origin,
                allowed_content_paths=endpoint.endpoint_policy.allowed_content_paths,
                allow_private_network=False,
            ),
        )
        rule_digest = _sha256_json(
            {
                "operation": "frame.optimize",
                "graph_version": _GRAPH_VERSION,
                "snapshot_version": SNAPSHOT_VERSION,
                "order": order,
                "frame_index": frame_index,
                "model_source": prepared.model.source.value,
                "model_id": prepared.model.catalog_id,
                "quote": prepared.quote_amount,
                "prompt_digest": prepared.prompt_digest,
                "target_frame_digest": target.full_frame_digest,
                "requested_text_digest": _sha256_json(requested_text),
            }
        )
        candidates.append(
            FrameOptimizeSnapshotCandidate(
                order=order,
                frame_index=frame_index,
                identity=identity,
                target=target,
                snapshot=snapshot,
                request_digest=context.fingerprint.ordered_nonsecret_payload_digest,
                rule_digest=rule_digest,
                model=model,
                prompt=prompt,
                request=_plain(canonical_request),
                grant=grant,
                scope=scope,
                endpoint=endpoint,
                database_frame=_plain(raw_frame),
                database_chapter=tuple(_plain(chapter_frames)),
                database_text=database_text,
                requested_text=requested_text,
            )
        )
    return tuple(candidates)


def protect_frame_optimize_batch(
    candidates: Sequence[FrameOptimizeSnapshotCandidate],
    *,
    owner_id: str,
    submission_id: str,
    task_id: str,
    graph: OperationGraphPlan,
    context: AdmissionContext,
    registry_capabilities: RegistryCapabilities,
) -> FrameOptimizeProtectedBatch:
    """Authenticate the ordered batch and seal one separately bound payload per unit."""

    authority = registry_capabilities.proof_authority
    crypto = registry_capabilities.crypto
    if (
        authority is None
        or crypto is None
        or not crypto.ready()
        or graph.operation != "frame.optimize"
        or graph.graph_version != _GRAPH_VERSION
        or len(candidates) != len(graph.items)
        or not candidates
    ):
        raise ValueError("protected frame optimization capabilities are unavailable")

    manifest_items: list[dict[str, object]] = []
    for candidate, item in zip(candidates, graph.items, strict=True):
        identity = candidate.identity
        target_ref = _payload_ref(identity.payload_id, "target")
        model_ref = _payload_ref(identity.payload_id, "model")
        grant_ref = _payload_ref(identity.payload_id, "grant")
        if (
            item.order != candidate.order
            or item.item_id != identity.item_id
            or item.unit_id != identity.unit_id
            or item.task_id != task_id
            or item.request_digest != candidate.request_digest
            or item.rule_digest != candidate.rule_digest
            or item.target_id != target_ref
        ):
            raise ValueError("frame optimization graph differs from its frozen inputs")
        manifest_items.append(
            {
                "order": candidate.order,
                "frame_index": candidate.frame_index,
                "item_id": identity.item_id,
                "unit_id": identity.unit_id,
                "step_id": identity.step_id,
                "prepared_payload_id": identity.payload_id,
                "quoted_amount": candidate.snapshot.server_quote,
                "snapshot_digest": item.snapshot_digest,
                "rule_digest": candidate.rule_digest,
                "target_ref": target_ref,
                "model_ref": model_ref,
                "grant_ref": grant_ref,
            }
        )

    manifest: dict[str, object] = {
        "purpose": _PROOF_PURPOSE,
        "schema": SNAPSHOT_VERSION,
        "owner_id": owner_id,
        "operation": "frame.optimize",
        "graph_version": _GRAPH_VERSION,
        "submission_id": submission_id,
        "task_id": task_id,
        "request_digest": context.fingerprint.ordered_nonsecret_payload_digest,
        "item_ids": [item["item_id"] for item in manifest_items],
        "items": manifest_items,
    }
    manifest_bytes = canonical_frame_optimize_bytes(manifest)
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    manifest_proof = authority.sign(_PROOF_PURPOSE, manifest_bytes)

    protected_items: list[FrameOptimizeProtectedItem] = []
    for candidate, graph_item, manifest_item in zip(
        candidates, graph.items, manifest_items, strict=True
    ):
        identity = candidate.identity
        target_ref = str(manifest_item["target_ref"])
        model_ref = str(manifest_item["model_ref"])
        grant_ref = str(manifest_item["grant_ref"])
        credential = _credential_snapshot(candidate)
        payload_value = {
            "schema": SNAPSHOT_VERSION,
            "manifest": manifest,
            "manifest_proof": manifest_proof,
            "manifest_snapshot_digest": manifest_digest,
            "item": manifest_item,
            "execution_snapshot": _plain(candidate.snapshot),
            "target": {
                "snapshot": _plain(candidate.target),
                "database_frame": candidate.database_frame,
                "database_text": candidate.database_text,
                "requested_text": candidate.requested_text,
                "chapter_digest": candidate.target.full_chapter_digest,
            },
            "database_chapter": candidate.database_chapter if candidate.order == 0 else None,
            "model": candidate.model,
            "prompt": candidate.prompt,
            "request": candidate.request if candidate.order == 0 else None,
            "grant": _plain(candidate.grant),
            "credential": credential,
        }
        associated_data = frame_optimize_payload_associated_data(
            manifest_digest=manifest_digest,
            manifest_item=manifest_item,
            scope=candidate.scope,
            binding_id=candidate.endpoint.binding_id,
            binding_version=int(candidate.endpoint.binding.binding_version),
            principal_id=candidate.endpoint.binding.original_principal_id,
            schema_version=context.fingerprint.schema_version,
            projector_version=SNAPSHOT_VERSION,
            reference_policy_version=_REFERENCE_POLICY_VERSION,
            target_ref=target_ref,
            model_ref=model_ref,
            grant_ref=grant_ref,
        )
        sealed = crypto.seal_secret(
            canonical_frame_optimize_bytes(payload_value).decode("utf-8"),
            associated_data,
        )
        payload = PreparedPayload(
            actor_id=owner_id,
            operation="frame.optimize",
            server_attempt_id=identity.item_id,
            scope=candidate.scope,
            binding_id=candidate.endpoint.binding_id,
            binding_version=int(candidate.endpoint.binding.binding_version),
            principal_id=candidate.endpoint.binding.original_principal_id,
            context_fingerprint=candidate.request_digest,
            schema_version=context.fingerprint.schema_version,
            projector_version=SNAPSHOT_VERSION,
            reference_policy_version=_REFERENCE_POLICY_VERSION,
            target_ref=target_ref,
            model_ref=model_ref,
            grant_ref=grant_ref,
            command_proof=manifest_proof,
            ciphertext=sealed.ciphertext,
            nonce=sealed.nonce,
            encryption_key_id=sealed.key_id,
        )
        protected_items.append(
            FrameOptimizeProtectedItem(
                candidate=candidate,
                target_ref=target_ref,
                model_ref=model_ref,
                grant_ref=grant_ref,
                snapshot_digest=graph_item.snapshot_digest,
                manifest_snapshot_digest=manifest_digest,
                manifest_item=manifest_item,
                payload=payload,
            )
        )
    return FrameOptimizeProtectedBatch(
        manifest=manifest,
        manifest_proof=manifest_proof,
        manifest_snapshot_digest=manifest_digest,
        items=tuple(protected_items),
    )


def frame_optimize_payload_associated_data(
    *,
    manifest_digest: str,
    manifest_item: Mapping[str, object],
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
    """Bind ciphertext to the whole batch and its exact ordered graph item."""

    return canonical_frame_optimize_bytes(
        {
            "purpose": _PAYLOAD_PURPOSE,
            "manifest_snapshot_digest": manifest_digest,
            "manifest_item": manifest_item,
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


def canonical_frame_optimize_bytes(value: object) -> bytes:
    return json.dumps(
        _plain(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


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
        statement = select(
            table.c.id,
            table.c.category,
            table.c.name,
            table.c.provider,
            table.c.model,
            table.c.params,
            table.c.is_active,
        ).where(table.c.id == item.model.catalog_id)
    else:
        table = model_configs
        owner_or_system = actor_id
        statement = select(
            table.c.id,
            table.c.user_id,
            table.c.category,
            table.c.name,
            table.c.provider,
            table.c.model,
            table.c.params,
        ).where(table.c.id == item.model.catalog_id, table.c.user_id == actor_id)
    row = session.execute(statement.with_for_update()).mappings().one_or_none()
    if row is None:
        raise ValueError("selected optimize model is unavailable")
    params = _parse_params(row["params"])
    params.update(dict(item.effective_parameters))
    if (
        row["category"] != "text"
        or not isinstance(row["provider"], str)
        or (item.model.source is ModelSource.SYSTEM and not row["is_active"])
        or (item.model.source is ModelSource.USER and row["user_id"] != actor_id)
        or row["name"] != item.model.display_name
        or row["model"] != item.model.wire_model
    ):
        raise ValueError("selected optimize model changed after admission")
    scope = CredentialScope(
        source=item.model.source.value,
        row_id=item.model.catalog_id,
        owner_or_system=owner_or_system,
        provider=str(row["provider"]),
    )
    endpoint = CredentialRegistry(capabilities).capture_original_binding(session, scope)
    if endpoint is None or endpoint.endpoint_policy.provider_origin is None:
        raise ValueError("selected optimize credential binding is unavailable")
    return (
        {
            "source": item.model.source.value,
            "row_id": item.model.catalog_id,
            "owner_or_system": owner_or_system,
            "provider": str(row["provider"]),
            "display_name": item.model.display_name,
            "wire_model": item.model.wire_model,
            "params": params,
            "quote": item.quote_amount,
        },
        scope,
        endpoint,
    )


def _capture_prompt(
    session: Session,
    *,
    actor_id: str,
    request: Mapping[str, object],
    item: PreparedItemFacts,
) -> tuple[str | None, dict[str, object]]:
    facts = item.prompt
    prompt_id = request.get("prompt_id") or None
    if facts is None:
        if prompt_id is not None:
            raise ValueError("selected optimize prompt was not prepared")
        return None, {
            "id": None,
            "owner_or_system": None,
            "source": "request",
            "system_prompt": "",
            "user_prompt": "",
        }
    if facts.prompt_id != prompt_id:
        raise ValueError("selected optimize prompt identity changed")
    if facts.source == ModelSource.SYSTEM.value:
        table = system_prompts
        statement = select(table).where(table.c.id == prompt_id)
        owner_or_system = "system"
    elif facts.source == ModelSource.USER.value:
        table = prompt_configs
        statement = select(table).where(table.c.id == prompt_id, table.c.user_id == actor_id)
        owner_or_system = actor_id
    else:
        raise ValueError("selected optimize prompt source is invalid")
    row = session.execute(statement.with_for_update()).mappings().one_or_none()
    if row is None or (facts.source == ModelSource.SYSTEM.value and not row["is_active"]):
        raise ValueError("selected optimize prompt is unavailable")
    selection = PromptSelection(
        prompt_id=str(prompt_id),
        operation=PromptOperation.OPTIMIZE,
        source=ModelSource(facts.source),
        category=str(row["category"]),
        display_name=str(row["name"]),
        system_prompt=row["system_prompt"] or "",
        user_prompt=row["user_prompt"] or "",
    )
    if prompt_facts(selection) != facts:
        raise ValueError("selected optimize prompt changed after admission")
    return str(prompt_id), {
        "id": str(prompt_id),
        "owner_or_system": owner_or_system,
        "source": facts.source,
        "operation": "optimize",
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
        raise ValueError("optimize series is unavailable")
    memberships = ()
    if series_row["team_id"]:
        memberships = tuple(
            dict(row)
            for row in session.execute(
                select(team_members)
                .where(
                    team_members.c.team_id == series_row["team_id"],
                    team_members.c.user_id == actor_id,
                )
                .order_by(team_members.c.id)
                .with_for_update()
            ).mappings().all()
        )
    if len(memberships) > 1:
        raise ValueError("optimize series membership is ambiguous")
    is_owner = series_row["user_id"] == actor_id
    is_member = bool(memberships)
    claimed_by = series_row["claimed_by"]
    claim_permission = False
    if claimed_by and claimed_by != actor_id:
        if not memberships or not can_enter_claimed_series(
            TeamMembership(role=memberships[0]["role"], permissions=memberships[0]["permissions"])
        ):
            raise ValueError("claimed optimize series access is denied")
        claim_permission = True
    if not is_owner and not is_member:
        raise ValueError("optimize series access is denied")
    provenance = ["series_owner" if is_owner else "team_member"]
    if claim_permission:
        provenance.append("claimed_series_permission")
    return FrozenGrantSnapshot(
        actor_id=actor_id,
        series_id=series_id,
        team_id=str(series_row["team_id"]) if series_row["team_id"] is not None else None,
        access_provenance=tuple(provenance),
    )


def _stable_storyboard_id(frames: list[object], frame_index: int) -> str | None:
    frame = frames[frame_index]
    if not isinstance(frame, Mapping):
        raise ValueError("optimize frame must be an object")
    refs = frame.get("storyboard")
    if refs is None or refs == []:
        return None
    if not isinstance(refs, list) or len(refs) != 1 or not isinstance(refs[0], str) or not refs[0]:
        raise ValueError("optimize frame has an ambiguous stable storyboard id")
    stable_id = refs[0]
    identities = [
        identity
        for value in frames
        if isinstance(value, Mapping) and isinstance(value.get("storyboard"), list)
        for identity in value["storyboard"]
        if isinstance(identity, str) and identity
    ]
    if identities.count(stable_id) != 1:
        raise ValueError("optimize storyboard id is missing or ambiguous")
    return stable_id


def _credential_snapshot(candidate: FrameOptimizeSnapshotCandidate) -> dict[str, object]:
    binding = candidate.endpoint.binding
    policy = candidate.endpoint.endpoint_policy
    return {
        "scope": candidate.scope.identity(),
        "binding_id": candidate.endpoint.binding_id,
        "binding_version": int(binding.binding_version),
        "principal_id": binding.original_principal_id,
        "endpoint_origin": policy.provider_origin,
        "api_base_path": policy.api_base_path,
        "allowed_content_paths": policy.allowed_content_paths,
        "endpoint_policy_version": policy.policy_version,
        "rotation_provenance": binding.rotation_provenance,
    }


def _parse_params(raw: object) -> dict[str, object]:
    if raw is None or raw == "":
        return {}
    value = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(value, Mapping):
        raise ValueError("selected optimize model parameters are invalid")
    return dict(value)


def _payload_ref(payload_id: str, suffix: str) -> str:
    return f"prepared-payload-v1/{len(payload_id)}:{payload_id}#{suffix}"


def _canonical_json(value: object) -> str:
    return canonical_frame_optimize_bytes(value).decode("utf-8")


def _plain(value: object) -> object:
    if is_dataclass(value):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported frozen optimize value: {type(value).__name__}")


def _sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_frame_optimize_bytes(value)).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
