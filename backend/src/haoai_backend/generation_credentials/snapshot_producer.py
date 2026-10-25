"""Build protected per-item snapshots from the same locked admission transaction.

This module is an isolated producer draft. Its result only stages encrypted input
rows in the caller's transaction; it does not authorize graph creation or billing.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import weakref
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields, field, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from threading import RLock
from types import MappingProxyType
from typing import Any, Literal, TypeAlias
from weakref import ReferenceType

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.generation_catalog.admission import (
    AdmissionPolicy,
    prepare_admission,
)
from haoai_backend.generation_catalog.admission_types import (
    AdmissionContext,
    AdmissionContextError,
    AdmissionOperation,
    AdmissionPreparationFacts,
    AdmissionRefusal,
    AdmissionReplay,
    PreparedItemFacts,
    build_admission_context,
    prompt_facts,
)
from haoai_backend.generation_catalog.application import compose_batch_image_prompt
from haoai_backend.generation_catalog.domain import (
    ModelSelection,
    ModelSource,
    PromptOperation,
    PromptSelection,
)
from haoai_backend.generation_catalog.tables import (
    ASSET_TABLES,
    chapters,
    model_configs,
    prompt_configs,
    system_models,
    system_prompts,
)
from haoai_backend.generation_contracts.canvas_asset_targets import (
    canvas_asset_target_digest,
)
from haoai_backend.generation_contracts.types import (
    EndpointPolicy as SnapshotEndpointPolicy,
    FrozenExecutionSnapshot,
    FrozenGrantSnapshot,
    GenerationActor,
    ModelReference,
    TargetSnapshot,
    TrustedActor,
)
from haoai_backend.generation_credentials.application import (
    CredentialRegistry,
    RegistryCapabilities,
    check_credential_registry_readiness,
)
from haoai_backend.generation_credentials.domain import (
    CredentialCrypto,
    CredentialScope,
    EndpointPolicy as CredentialEndpointPolicy,
    HmacProofAuthority,
    PreparedPayload,
    PreparedPayloadStaged,
    ProtectedAccountRecord,
    RegistryConflict,
    RegistryRefusal,
    RootEnrollmentSource,
    SealedMaterial,
    ServerAttempt,
    account_guard_associated_data,
)
from haoai_backend.generation_credentials.persistence import (
    RegistryReadRepository,
    prepared_payloads,
)
from haoai_backend.series_access.domain import TeamMembership, can_enter_claimed_series
from haoai_backend.series_access.tables import series, team_members


SUPPORTED_OPERATIONS = frozenset(
    {
        AdmissionOperation.IMAGE_SINGLE.value,
        AdmissionOperation.VIDEO_SINGLE.value,
        AdmissionOperation.IMAGE_BATCH.value,
    }
)
_PROTECTED_INPUT_PURPOSE = "protected-generation-input"
_COMMAND_PROOF_PURPOSE = "prepared-generation-input-v1"
_REFERENCE_SNAPSHOT_VERSION = 1
_ADMISSION_POLICY_VERSION = "generation-admission-policy-v1"


@dataclass(frozen=True, slots=True)
class SnapshotVersions:
    """Explicit server-owned versions used to bind the protected input."""

    projector: str
    reference_policy: str

    def __post_init__(self) -> None:
        if not self.projector.strip() or not self.reference_policy.strip():
            raise ValueError("snapshot versions must be nonblank")


@dataclass(frozen=True, slots=True)
class SnapshotRefusal:
    status_code: int
    code: str
    caller_must_abort: bool = False


@dataclass(frozen=True, slots=True)
class PayloadVerificationResult:
    """Safe durable-read outcome; it never returns protected payload contents."""

    status: Literal["verified", "unresolved"]
    item_count: int = 0

    def __post_init__(self) -> None:
        if self.status not in {"verified", "unresolved"}:
            raise ValueError("payload verification status is invalid")
        if type(self.item_count) is not int or self.item_count < 0:
            raise ValueError("payload verification count is invalid")
        if (self.status == "verified") != (self.item_count > 0):
            raise ValueError("payload verification result is inconsistent")


@dataclass(frozen=True, slots=True)
class LegacyMessageProjection:
    user_content: str = field(repr=False)
    assistant_content: str
    assistant_wire_content: None
    model_name: str
    chapter_id: str
    asset_type: str
    asset_id: str
    asset_name: str


@dataclass(frozen=True, slots=True)
class RequestDataProjection:
    operation: str
    submission_id: str
    request_digest: str
    rule_digest: str
    mode: str
    model_id: str
    model_source: str
    model_owner_id: str | None
    prompt_id: str | None
    size: str | int | float | None
    chapter_id: str | None
    asset_type: str | None
    asset_id: str | None
    asset_name: str | None
    reference_images_version: int
    reference_images_count: int
    reference_images_digest: str
    prepared_payload_id: str
    protected_ref: str
    target_ref: str
    model_ref: str
    grant_ref: str
    target_snapshot: TargetSnapshot = field(repr=False)


@dataclass(frozen=True, slots=True)
class SnapshotPayloadItem:
    index: int
    server_attempt_id: str
    prepared_payload_id: str
    protected_ref: str
    payload_staged: PreparedPayloadStaged
    snapshot: FrozenExecutionSnapshot = field(repr=False)
    legacy_message_projection: LegacyMessageProjection | None = field(repr=False)
    request_data_projection: RequestDataProjection = field(repr=False)


class _StagingOriginToken:
    __slots__ = ("__weakref__",)


@dataclass(frozen=True, slots=True)
class _StagingOrigin:
    writer_session_ref: ReferenceType[Session]
    lifecycle_handles: tuple[object, ...]
    manifest_digest: str


@dataclass(frozen=True, slots=True)
class SnapshotPayloadsStaged:
    """Prepared-only value for immediate same-transaction G2 consumption."""

    parent_attempt_id: str
    actor_id: str
    operation: str
    graph_version: str
    fingerprint: str
    manifest_digest: str = field(repr=False)
    staging_session_ref: ReferenceType[Session] = field(repr=False, compare=False)
    preparation: AdmissionPreparationFacts = field(repr=False)
    items: tuple[SnapshotPayloadItem, ...]
    executable: bool = False
    _staging_origin_token: _StagingOriginToken | None = field(
        default=None, repr=False, compare=False
    )


SnapshotProducerResult: TypeAlias = (
    AdmissionReplay
    | AdmissionRefusal
    | SnapshotRefusal
    | SnapshotPayloadsStaged
)


@dataclass(frozen=True, slots=True)
class _FullModel:
    selection: ModelSelection
    owner_or_system: str
    provider: str
    wire_model: str | None
    raw_base_url: str
    params: Mapping[str, object] = field(repr=False)
    raw_api_key: str = field(repr=False)
    scope: CredentialScope


@dataclass(frozen=True, slots=True)
class _FullTarget:
    snapshot: TargetSnapshot
    protected_values: Mapping[str, object] = field(repr=False)
    series_id: str | None = None


@dataclass(frozen=True, slots=True)
class _FullGrant:
    snapshot: FrozenGrantSnapshot
    protected_values: Mapping[str, object] = field(repr=False)


@dataclass(frozen=True, slots=True)
class _FullItem:
    index: int
    facts: PreparedItemFacts
    model: _FullModel = field(repr=False)
    effective_prompt: str = field(repr=False)
    prompt_values: Mapping[str, object] = field(repr=False)
    prompt_ref: str | None
    references: Mapping[str, tuple[str, ...]] = field(repr=False)
    target: _FullTarget = field(repr=False)
    grant: _FullGrant = field(repr=False)
    legacy_message_projection: LegacyMessageProjection | None = field(repr=False)
    credential_record: ProtectedAccountRecord | None = field(repr=False)
    credential_provenance: Mapping[str, object] = field(repr=False)
    binding_id: str
    binding_version: int
    endpoint_policy: CredentialEndpointPolicy | None = field(repr=False)


@dataclass(frozen=True, slots=True)
class _VerifiedBinding:
    record: ProtectedAccountRecord
    binding_id: str
    version: int
    endpoint_policy: CredentialEndpointPolicy
    provenance: Mapping[str, object] = field(repr=False)


@dataclass(frozen=True, slots=True)
class _EncryptedItem:
    parent_attempt_id: str
    index: int
    child_attempt_id: str
    payload_id: str
    protected_ref: str
    graph_version: str
    manifest_digest: str = field(repr=False)
    payload: PreparedPayload = field(repr=False)
    snapshot: FrozenExecutionSnapshot = field(repr=False)
    legacy_message_projection: LegacyMessageProjection | None = field(repr=False)
    request_data_projection: RequestDataProjection = field(repr=False)


class MediaSnapshotProducer:
    """Account-authenticated, A2-first producer using one caller-owned Session."""

    def __init__(
        self,
        authentication_service: AuthenticationService,
        registry_capabilities: RegistryCapabilities,
        registry: CredentialRegistry,
        *,
        server_id_factory: Callable[[], str],
        policies: Mapping[str, AdmissionPolicy],
        versions: SnapshotVersions,
    ) -> None:
        self._authentication_service = authentication_service
        self._registry_capabilities = registry_capabilities
        self._registry = registry
        self._server_id_factory = server_id_factory
        self._policies = dict(policies)
        self._versions = versions
        self._staging_origins: weakref.WeakKeyDictionary[_StagingOriginToken, _StagingOrigin] = (
            weakref.WeakKeyDictionary()
        )
        self._staging_origins_lock = RLock()

    def prepare_and_stage(
        self,
        caller_session: Session,
        *,
        authorization: str,
        operation: str,
        raw_key: str,
        request: Mapping[str, object],
    ) -> SnapshotProducerResult:
        if operation not in SUPPORTED_OPERATIONS:
            return SnapshotRefusal(422, "unsupported_operation")
        policy = self._policies.get(operation)
        if policy is None:
            return SnapshotRefusal(503, "operation_policy_unavailable")
        if not isinstance(authorization, str) or not authorization:
            return SnapshotRefusal(401, "authentication_required")

        actor_id = self._authenticated_account_id(authorization)
        if actor_id is None:
            return SnapshotRefusal(401, "authentication_required")

        try:
            actor = GenerationActor(
                user_id=actor_id,
                trusted_account_identity=TrustedActor(user_id=actor_id),
                new_admission_allowed=None,
            )
            context = build_admission_context(
                actor,
                operation,
                request,
                raw_key=raw_key,
            )
        except (AdmissionContextError, TypeError, ValueError):
            return SnapshotRefusal(422, "invalid_context")

        # A2 owns the first business SQL. Its original-receipt branch must return
        # before this producer inspects fresh membership, credentials, or crypto.
        try:
            with caller_session.no_autoflush:
                prepared = prepare_admission(caller_session, context, policy=policy)
        except Exception:
            return SnapshotRefusal(503, "admission_unavailable")
        if isinstance(prepared, (AdmissionReplay, AdmissionRefusal)):
            return prepared
        if not isinstance(prepared, AdmissionPreparationFacts):
            return SnapshotRefusal(503, "admission_unavailable")
        if context.fingerprint is None or prepared.fingerprint != context.fingerprint:
            return SnapshotRefusal(409, "admission_fingerprint_mismatch")
        if (
            prepared.executable
            or prepared.reserved_amount != 0
            or prepared.credential_binding_status != "binding_unverified"
        ):
            return SnapshotRefusal(409, "admission_facts_unexpected")

        try:
            with caller_session.no_autoflush:
                readiness = check_credential_registry_readiness(
                    caller_session,
                    self._registry_capabilities,
                )
        except Exception:
            return SnapshotRefusal(503, "credential_registry_unavailable")
        if not readiness.ready:
            return SnapshotRefusal(503, "credential_registry_unavailable")

        try:
            with caller_session.no_autoflush:
                full_items = self._resolve_full_items(
                    caller_session,
                    context=context,
                    preparation=prepared,
                )
                bindings = self._lock_and_verify_bindings(
                    caller_session,
                    full_items,
                )
                enriched = tuple(
                    self._with_binding(item, bindings[item.model.scope])
                    for item in full_items
                )
            encrypted = self._encrypt_batch(context, prepared, enriched, policy)
        except _UnsafeSnapshot as exc:
            return SnapshotRefusal(exc.status_code, exc.code)
        except (SQLAlchemyError, TypeError, ValueError, KeyError, AttributeError):
            return SnapshotRefusal(503, "snapshot_inputs_unavailable")
        except Exception:
            # Crypto and protected source errors may contain sensitive values.
            return SnapshotRefusal(503, "snapshot_crypto_unavailable")

        staged: list[SnapshotPayloadItem] = []
        for item in encrypted:
            try:
                with caller_session.no_autoflush:
                    outcome = self._registry.stage_prepared_payload(
                        caller_session,
                        item.payload,
                        payload_id=item.payload_id,
                    )
            except Exception:
                return SnapshotRefusal(
                    503,
                    "payload_stage_unavailable",
                    caller_must_abort=True,
                )
            if not isinstance(outcome, PreparedPayloadStaged):
                code = (
                    outcome.code
                    if isinstance(outcome, (RegistryConflict, RegistryRefusal))
                    else "payload_stage_unavailable"
                )
                return SnapshotRefusal(
                    503,
                    code,
                    caller_must_abort=True,
                )
            if outcome.prepared_payload_id != item.payload_id:
                return SnapshotRefusal(503, "payload_stage_mismatch", caller_must_abort=True)
            staged.append(
                SnapshotPayloadItem(
                    index=item.index,
                    server_attempt_id=item.child_attempt_id,
                    prepared_payload_id=item.payload_id,
                    protected_ref=item.protected_ref,
                    payload_staged=outcome,
                    snapshot=item.snapshot,
                    legacy_message_projection=item.legacy_message_projection,
                    request_data_projection=item.request_data_projection,
                )
            )

        origin = _capture_staging_origin(caller_session, encrypted[0].manifest_digest)
        if origin is None:
            return SnapshotRefusal(503, "payload_stage_unavailable", caller_must_abort=True)
        origin_token = _StagingOriginToken()
        with self._staging_origins_lock:
            self._staging_origins[origin_token] = origin

        return SnapshotPayloadsStaged(
            parent_attempt_id=encrypted[0].parent_attempt_id,
            actor_id=actor_id,
            operation=operation,
            graph_version=encrypted[0].graph_version,
            fingerprint=_context_fingerprint_digest(context),
            manifest_digest=encrypted[0].manifest_digest,
            staging_session_ref=weakref.ref(caller_session),
            preparation=prepared,
            _staging_origin_token=origin_token,
            items=tuple(staged),
        )

    def verify_staged_payloads(
        self,
        read_session: Session,
        expected: SnapshotPayloadsStaged,
    ) -> PayloadVerificationResult:
        """Verify a staged result after an unknown commit using a caller-owned read Session."""

        try:
            if not isinstance(read_session, Session) or not isinstance(
                expected, SnapshotPayloadsStaged
            ):
                return _unresolved_payloads()
            origin_token = expected._staging_origin_token
            with self._staging_origins_lock:
                origin = (
                    self._staging_origins.get(origin_token)
                    if origin_token is not None
                    else None
                )
            if (
                origin is None
                or origin.manifest_digest != expected.manifest_digest
                or origin.writer_session_ref() is read_session
                or not _original_lifecycle_ended(origin.lifecycle_handles)
                or read_session.new
                or read_session.dirty
                or read_session.deleted
            ):
                return _unresolved_payloads()
            authority = self._registry_capabilities.proof_authority
            crypto = self._registry_capabilities.crypto
            if authority is None or crypto is None:
                return _unresolved_payloads()
            if not crypto.ready():
                return _unresolved_payloads()
            with read_session.no_autoflush:
                verified = _verify_staged_payload_rows(
                    read_session,
                    expected,
                    authority=authority,
                    crypto=crypto,
                )
            if not verified:
                return _unresolved_payloads()
            return PayloadVerificationResult("verified", len(expected.items))
        except Exception:
            # Crypto, database, and caller-supplied expectation failures are intentionally safe.
            return _unresolved_payloads()

    def _authenticated_account_id(self, authorization: str) -> str | None:
        try:
            with self._authentication_service.authenticated(
                authorization,
                require_membership=False,
            ) as principal:
                user_id = principal.user.id
                if not isinstance(user_id, str) or not user_id:
                    return None
                return user_id
        except Exception:
            return None

    def _resolve_full_items(
        self,
        session: Session,
        *,
        context: AdmissionContext,
        preparation: AdmissionPreparationFacts,
    ) -> tuple[_FullItem, ...]:
        raw_items = _request_items(context)
        if len(raw_items) != len(preparation.items) or not raw_items:
            raise _UnsafeSnapshot(409, "prepared_item_count_mismatch")

        full: list[_FullItem] = []
        for index, (raw_item, facts) in enumerate(zip(raw_items, preparation.items, strict=True)):
            if facts.index != index or facts.model.quote.amount != facts.quote_amount:
                raise _UnsafeSnapshot(409, "prepared_item_order_mismatch")
            model = self._read_full_model(session, context.actor.user_id, facts.model)
            prompt_ref, prompt_selection = self._read_prompt(
                session,
                context.actor.user_id,
                context.operation,
                raw_item,
                facts,
            )
            effective_prompt = _effective_prompt(context.operation, raw_item, prompt_selection)
            if _digest_text(effective_prompt) != facts.prompt_digest:
                raise _UnsafeSnapshot(409, "prepared_prompt_mismatch")
            target = self._read_target(session, facts)
            grant = self._read_grant(session, context.actor.user_id, target.series_id)
            references = _references(context.operation, raw_item)
            legacy_message = _legacy_message_projection(
                operation=context.operation,
                raw_item=raw_item,
                facts=facts,
                target=target,
                references=references,
            )
            full.append(
                _FullItem(
                    index=index,
                    facts=facts,
                    model=model,
                    effective_prompt=effective_prompt,
                    prompt_values=_prompt_values(prompt_selection, context.actor.user_id),
                    prompt_ref=prompt_ref,
                    references=references,
                    target=target,
                    grant=grant,
                    legacy_message_projection=legacy_message,
                    credential_record=None,  # Filled only after all exact heads are locked.
                    credential_provenance=MappingProxyType({}),
                    binding_id="",
                    binding_version=0,
                    endpoint_policy=None,  # Filled only after all exact heads are locked.
                )
            )
        return tuple(full)

    def _read_full_model(
        self,
        session: Session,
        actor_id: str,
        selection: ModelSelection,
    ) -> _FullModel:
        if selection.source is ModelSource.SYSTEM:
            table = system_models
            owner_or_system = "system"
            statement = select(table).where(table.c.id == selection.catalog_id)
        elif selection.source is ModelSource.USER:
            table = model_configs
            owner_or_system = actor_id
            statement = select(table).where(
                table.c.id == selection.catalog_id,
                table.c.user_id == actor_id,
            )
        else:
            raise _UnsafeSnapshot(409, "model_source_unavailable")
        row = session.execute(statement).mappings().one_or_none()
        if row is None:
            raise _UnsafeSnapshot(409, "locked_model_missing")

        category = str(row["category"])
        display_name = str(row["name"])
        wire_model = row["model"]
        provider = row["provider"]
        base_url = row["base_url"]
        active = bool(row["is_active"]) if selection.source is ModelSource.SYSTEM else True
        if (
            category != selection.category.value
            or display_name != selection.display_name
            or wire_model != selection.wire_model
            or (selection.source is ModelSource.USER and str(row["user_id"]) != actor_id)
            or not active
            or not isinstance(provider, str)
            or not provider
            or not isinstance(base_url, str)
            or not base_url
        ):
            raise _UnsafeSnapshot(409, "locked_model_changed")

        params = _parse_params(row["params"])
        scope = CredentialScope(
            source=selection.source.value,
            row_id=selection.catalog_id,
            owner_or_system=owner_or_system,
            provider=provider,
        )
        config = RegistryReadRepository(session).lock_config(
            scope,
            require_active=True,
            require_api_key=True,
        )
        if (
            config is None
            or config.category != category
            or config.provider != provider
            or config.model != wire_model
            or config.base_url != base_url
            or config.active is not active
            or not config.api_key
        ):
            raise _UnsafeSnapshot(409, "locked_credential_config_changed")
        return _FullModel(
            selection=selection,
            owner_or_system=owner_or_system,
            provider=provider,
            wire_model=wire_model if isinstance(wire_model, str) else None,
            raw_base_url=base_url,
            params=params,
            raw_api_key=config.api_key,
            scope=scope,
        )

    def _read_prompt(
        self,
        session: Session,
        actor_id: str,
        operation: str,
        raw_item: Mapping[str, object],
        facts: PreparedItemFacts,
    ) -> tuple[str | None, PromptSelection | None]:
        prompt_facts_value = facts.prompt
        if prompt_facts_value is None:
            if operation == AdmissionOperation.IMAGE_BATCH.value and raw_item.get("prompt_id"):
                raise _UnsafeSnapshot(409, "prompt_selection_missing")
            return None, None
        if operation != AdmissionOperation.IMAGE_BATCH.value:
            raise _UnsafeSnapshot(409, "unexpected_prompt_selection")
        prompt_id = raw_item.get("prompt_id")
        if not isinstance(prompt_id, str) or prompt_id != prompt_facts_value.prompt_id:
            raise _UnsafeSnapshot(409, "prompt_identity_mismatch")
        if prompt_facts_value.source == ModelSource.SYSTEM.value:
            table = system_prompts
            statement = select(table).where(table.c.id == prompt_id)
            owner_id = None
            active = True
        elif prompt_facts_value.source == ModelSource.USER.value:
            table = prompt_configs
            statement = select(table).where(
                table.c.id == prompt_id,
                table.c.user_id == actor_id,
            )
            owner_id = actor_id
            active = True
        else:
            raise _UnsafeSnapshot(409, "prompt_source_unavailable")
        row = session.execute(statement).mappings().one_or_none()
        if row is None or (prompt_facts_value.source == ModelSource.SYSTEM.value and not row["is_active"]):
            raise _UnsafeSnapshot(409, "locked_prompt_missing")
        if row["category"] != prompt_facts_value.category:
            raise _UnsafeSnapshot(409, "locked_prompt_changed")
        try:
            selection = PromptSelection(
                prompt_id=prompt_id,
                operation=PromptOperation.IMAGE_BATCH,
                source=ModelSource(prompt_facts_value.source),
                category=str(row["category"]),
                display_name=str(row["name"]),
                system_prompt=row["system_prompt"] or "",
                user_prompt=row["user_prompt"] or "",
            )
        except (TypeError, ValueError):
            raise _UnsafeSnapshot(409, "locked_prompt_invalid") from None
        if prompt_facts(selection) != prompt_facts_value:
            raise _UnsafeSnapshot(409, "locked_prompt_changed")
        return prompt_id, selection

    def _read_target(
        self,
        session: Session,
        facts: PreparedItemFacts,
    ) -> _FullTarget:
        target_facts = facts.target
        if target_facts.kind in {"none", "standalone"}:
            return _FullTarget(
                snapshot=TargetSnapshot(kind="none"),
                protected_values=MappingProxyType({"kind": target_facts.kind}),
                series_id=None,
            )
        chapter_id = target_facts.chapter_id
        if not isinstance(chapter_id, str) or not chapter_id:
            raise _UnsafeSnapshot(409, "locked_target_missing")
        chapter = session.execute(
            select(chapters).where(chapters.c.id == chapter_id)
        ).mappings().one_or_none()
        if chapter is None:
            raise _UnsafeSnapshot(409, "locked_chapter_missing")
        chapter_values = dict(chapter)
        chapter_text = chapter_values.get("content") or ""
        if (
            str(chapter_values.get("series_id")) != target_facts.series_id
            or _digest_text(chapter_text) != target_facts.chapter_digest
        ):
            raise _UnsafeSnapshot(409, "locked_chapter_changed")

        asset_values: dict[str, object] | None = None
        if target_facts.asset_type is not None or target_facts.asset_id is not None:
            if (
                target_facts.asset_type not in ASSET_TABLES
                or not isinstance(target_facts.asset_id, str)
            ):
                raise _UnsafeSnapshot(409, "locked_asset_identity_invalid")
            asset_table = ASSET_TABLES[target_facts.asset_type]
            asset = session.execute(
                select(asset_table).where(asset_table.c.id == target_facts.asset_id)
            ).mappings().one_or_none()
            if asset is None:
                raise _UnsafeSnapshot(409, "locked_asset_missing")
            asset_values = dict(asset)
            if (
                asset_values.get("series_id") != chapter_values.get("series_id")
                or _asset_digest(asset_values) != target_facts.asset_digest
            ):
                raise _UnsafeSnapshot(409, "locked_asset_changed")

        source_chapter: dict[str, object] | None = None
        if asset_values is not None and target_facts.asset_type == "storyboard":
            source_chapter_id = asset_values.get("chapter_id")
            if not isinstance(source_chapter_id, str) or not source_chapter_id:
                raise _UnsafeSnapshot(409, "storyboard_source_missing")
            source_row = session.execute(
                select(chapters).where(chapters.c.id == source_chapter_id)
            ).mappings().one_or_none()
            if source_row is None:
                raise _UnsafeSnapshot(409, "storyboard_source_missing")
            source_chapter = dict(source_row)
            if source_chapter.get("series_id") != chapter_values.get("series_id"):
                raise _UnsafeSnapshot(409, "storyboard_source_mismatch")

        kind = target_facts.kind
        target_kind = {
            "frame_message": "frame_message",
            "asset_message": "asset_message",
            "canvas_frame_preview": "canvas_frame_preview",
            "canvas_asset_image": "canvas_asset_image",
            "asset_batch": "asset_message",
        }.get(kind)
        if target_kind is None:
            raise _UnsafeSnapshot(409, "target_kind_unsupported")
        target_digest = target_facts.asset_digest
        if target_kind == "canvas_asset_image":
            if asset_values is None:
                raise _UnsafeSnapshot(409, "locked_asset_missing")
            try:
                target_digest = canvas_asset_target_digest(
                    target_facts.asset_type or "",
                    asset_values,
                )
            except (TypeError, ValueError):
                raise _UnsafeSnapshot(409, "locked_asset_invalid") from None
        stable_storyboard_id = (
            target_facts.asset_id
            if kind in {"frame_message", "canvas_frame_preview"}
            else None
        )
        full_frame_digest = None
        full_chapter_digest = target_facts.chapter_digest
        if kind == "canvas_frame_preview":
            frame_index = target_facts.frame_index
            if target_facts.identity_mode == "strict-no-id-v1":
                canonical_frames = _parse_canonical_chapter_frames(chapter_text)
                if canonical_frames is None:
                    raise _UnsafeSnapshot(409, "locked_no_id_chapter_invalid")
                frames, canonical_chapter_digest = canonical_frames
                if (
                    stable_storyboard_id is not None
                    or target_facts.expected_chapter_digest
                    != canonical_chapter_digest
                    or target_facts.canonical_chapter_digest
                    != canonical_chapter_digest
                    or type(frame_index) is not int
                    or not 0 <= frame_index < len(frames)
                ):
                    raise _UnsafeSnapshot(409, "locked_frame_target_changed")
                frame = frames[frame_index]
                if not _frame_has_no_storyboard_identity(frame):
                    raise _UnsafeSnapshot(409, "locked_frame_target_changed")
                full_frame_digest = _digest_canonical_json(frame)
                full_chapter_digest = canonical_chapter_digest
            else:
                frames = _parse_chapter_frames(chapter_text)
                if (
                    not stable_storyboard_id
                    or frames is None
                    or type(frame_index) is not int
                    or not 0 <= frame_index < len(frames)
                    or not isinstance(frames[frame_index], Mapping)
                ):
                    raise _UnsafeSnapshot(409, "locked_frame_target_changed")
                frame = frames[frame_index]
                references = frame.get("storyboard")
                stable_matches = [
                    index
                    for index, candidate in enumerate(frames)
                    if isinstance(candidate, Mapping)
                    and isinstance(candidate.get("storyboard"), list)
                    and candidate["storyboard"]
                    and candidate["storyboard"][0] == stable_storyboard_id
                ]
                if (
                    not isinstance(references, list)
                    or not references
                    or references[0] != stable_storyboard_id
                    or stable_matches != [frame_index]
                ):
                    raise _UnsafeSnapshot(409, "locked_frame_target_changed")
                full_frame_digest = _digest_canonical_json(frame)
                full_chapter_digest = _digest_canonical_json(frames)
        snapshot = TargetSnapshot(
            kind=target_kind,
            chapter_id=chapter_id,
            series_id=str(chapter_values["series_id"]),
            stable_storyboard_asset_id=stable_storyboard_id,
            asset_type=target_facts.asset_type,
            asset_id=target_facts.asset_id,
            compat_frame_index=target_facts.frame_index,
            full_chapter_digest=full_chapter_digest,
            full_frame_digest=full_frame_digest,
            expected_asset_image_url=(
                str(asset_values["image_url"])
                if asset_values is not None and asset_values.get("image_url") is not None
                else None
            ),
            same_series_target_digest=target_digest,
        )
        protected = {
            "kind": kind,
            "chapter": chapter_values,
            "storyboard_source_chapter": source_chapter,
            "asset_type": target_facts.asset_type,
            "asset": asset_values,
            "chapter_digest": target_facts.chapter_digest,
            "asset_digest": target_facts.asset_digest,
        }
        if target_facts.identity_mode is not None:
            protected["identity_mode"] = target_facts.identity_mode
            protected["expected_chapter_digest"] = (
                target_facts.expected_chapter_digest
            )
            protected["canonical_chapter_digest"] = (
                target_facts.canonical_chapter_digest
            )
        return _FullTarget(
            snapshot=snapshot,
            protected_values=_freeze_mapping(
                protected,
                invalid_code="protected_value_invalid",
                allow_datetime=True,
            ),
            series_id=str(chapter_values["series_id"]),
        )

    def _read_grant(
        self,
        session: Session,
        actor_id: str,
        series_id: str | None,
    ) -> _FullGrant:
        if series_id is None:
            return _FullGrant(
                snapshot=FrozenGrantSnapshot(actor_id=actor_id),
                protected_values=MappingProxyType({"actor_id": actor_id, "scope": "account"}),
            )
        series_row = session.execute(
            select(
                series.c.id,
                series.c.user_id,
                series.c.team_id,
                series.c.claimed_by,
            ).where(series.c.id == series_id)
        ).mappings().one_or_none()
        if series_row is None:
            raise _UnsafeSnapshot(409, "locked_series_missing")
        series_values = dict(series_row)
        team_id = series_values.get("team_id")
        if team_id is not None and not isinstance(team_id, str):
            raise _UnsafeSnapshot(409, "series_team_invalid")
        member_values: dict[str, object] | None = None
        if isinstance(team_id, str) and team_id:
            member_rows = session.execute(
                select(team_members)
                .where(
                    team_members.c.team_id == team_id,
                    team_members.c.user_id == actor_id,
                )
                .order_by(team_members.c.id)
            ).mappings().all()
            if len(member_rows) > 1:
                raise _UnsafeSnapshot(409, "membership_ambiguous")
            if member_rows:
                member_values = dict(member_rows[0])

        is_owner = series_values.get("user_id") == actor_id
        is_member = member_values is not None
        claimed_by = series_values.get("claimed_by")
        claim_permission = False
        if claimed_by and claimed_by != actor_id:
            if member_values is None:
                raise _UnsafeSnapshot(403, "claimed_series_denied")
            membership = TeamMembership(
                role=member_values["role"],
                permissions=member_values["permissions"],
            )
            claim_permission = can_enter_claimed_series(membership)
            if not claim_permission:
                raise _UnsafeSnapshot(403, "claimed_series_denied")
        if not is_owner and not is_member:
            raise _UnsafeSnapshot(403, "series_access_denied")

        if is_owner:
            provenance = ("series_owner",)
        else:
            provenance = ("team_member",)
        if claim_permission:
            provenance = (*provenance, "claimed_series_permission")
        return _FullGrant(
            snapshot=FrozenGrantSnapshot(
                actor_id=actor_id,
                series_id=series_id,
                team_id=team_id if isinstance(team_id, str) else None,
                access_provenance=provenance,
            ),
            protected_values=_freeze_mapping(
                {
                    "actor_id": actor_id,
                    "series": series_values,
                    "member": member_values,
                    "is_owner": is_owner,
                    "is_member": is_member,
                    "claimed_by": claimed_by,
                    "claim_permission": claim_permission,
                }
            ),
        )

    def _lock_and_verify_bindings(
        self,
        session: Session,
        items: Sequence[_FullItem],
    ) -> dict[CredentialScope, _VerifiedBinding]:
        scopes = sorted({item.model.scope for item in items}, key=CredentialScope.key)
        repository = RegistryReadRepository(session)
        now = _current_utc(self._registry_capabilities)
        source = self._registry_capabilities.enrollment_source
        crypto = self._registry_capabilities.crypto
        authority = self._registry_capabilities.proof_authority
        if (
            not isinstance(source, RootEnrollmentSource)
            or crypto is None
            or authority is None
            or not hmac.compare_digest(source.authority.key, authority.key)
            or source.authority.key_id != authority.key_id
        ):
            raise _UnsafeSnapshot(503, "credential_proof_unavailable")

        bindings: dict[CredentialScope, _VerifiedBinding] = {}
        for scope in scopes:
            binding = repository.lock_binding(scope)
            if binding is None or binding.get("enabled") is not True:
                raise _UnsafeSnapshot(409, "credential_binding_unavailable")
            binding_id = binding.get("binding_id")
            head = binding.get("current_version")
            if not isinstance(binding_id, str) or type(head) is not int or head < 1:
                raise _UnsafeSnapshot(409, "credential_binding_invalid")
            revision = binding.get("revision")
            if type(revision) is not int or revision != head:
                raise _UnsafeSnapshot(409, "credential_binding_revision_mismatch")
            version = repository.read_version(binding_id, head)
            if version is None or version.get("binding_version") != head:
                raise _UnsafeSnapshot(409, "credential_head_missing")
            principal_id = version.get("principal_id")
            principal = repository.find_principal(principal_id) if isinstance(principal_id, str) else None
            if principal is None or principal.get("source_state") != "valid":
                raise _UnsafeSnapshot(409, "credential_principal_unavailable")

            origin_record = _unique_record(source, principal.get("record_id"))
            record_id = version.get("record_id")
            record = _unique_record(source, record_id)
            if (
                origin_record is None
                or not _principal_matches_origin(principal, scope, origin_record)
                or record is None
                or not _version_matches_record(version, scope, record)
                or not _same_principal_identity(principal, scope, record)
            ):
                raise _UnsafeSnapshot(409, "credential_record_mismatch")
            if source.load_account(scope, record.key_generation, now=now) != record:
                raise _UnsafeSnapshot(409, "credential_account_unavailable")

            attempt = _attempt_from_version(version, scope)
            if (
                attempt is None
                or not attempt.verify(source.authority)
                or not _head_operation_matches(version, attempt, head)
                or attempt.protected_record_id != record.record_id
                or attempt.key_generation != record.key_generation
            ):
                raise _UnsafeSnapshot(409, "credential_head_proof_invalid")
            _verify_rotation_if_needed(
                repository,
                source,
                attempt,
                record,
                binding_id,
                now,
            )
            provenance = _version_provenance(version, attempt, source)

            config = repository.lock_config(scope, require_active=True, require_api_key=True)
            if (
                config is None
                or config.provider != record.scope.provider
                or not config.base_url
            ):
                raise _UnsafeSnapshot(409, "credential_config_unavailable")
            model = next(item.model for item in items if item.model.scope == scope)
            if (
                config.category != model.selection.category.value
                or config.model != model.wire_model
                or config.base_url != model.raw_base_url
                or config.api_key != model.raw_api_key
            ):
                raise _UnsafeSnapshot(409, "credential_config_changed")
            endpoint = _endpoint_policy_from_config(config, version, record)
            legacy_value = crypto.resolve_legacy_value(config.api_key)
            if not legacy_value:
                raise _UnsafeSnapshot(503, "credential_config_unavailable")
            guard = crypto.guard(legacy_value, account_guard_associated_data(record))
            if (
                guard is None
                or not hmac.compare_digest(guard, record.secret_consistency_guard)
                or record.guard_key_id != crypto.guard_key_id
                or version.get("secret_consistency_guard") != record.secret_consistency_guard
            ):
                raise _UnsafeSnapshot(409, "credential_config_proof_mismatch")

            bindings[scope] = _VerifiedBinding(
                record=record,
                binding_id=binding_id,
                version=head,
                endpoint_policy=endpoint,
                provenance=provenance,
            )
        return bindings

    def _with_binding(
        self,
        item: _FullItem,
        binding: _VerifiedBinding,
    ) -> _FullItem:
        return _FullItem(
            index=item.index,
            facts=item.facts,
            model=item.model,
            effective_prompt=item.effective_prompt,
            prompt_values=item.prompt_values,
            prompt_ref=item.prompt_ref,
            references=item.references,
            target=item.target,
            grant=item.grant,
            legacy_message_projection=item.legacy_message_projection,
            credential_record=binding.record,
            credential_provenance=binding.provenance,
            binding_id=binding.binding_id,
            binding_version=binding.version,
            endpoint_policy=binding.endpoint_policy,
        )

    def _encrypt_batch(
        self,
        context: AdmissionContext,
        preparation: AdmissionPreparationFacts,
        items: Sequence[_FullItem],
        policy: AdmissionPolicy,
    ) -> tuple[_EncryptedItem, ...]:
        capabilities = self._registry_capabilities
        authority = capabilities.proof_authority
        crypto = capabilities.crypto
        if authority is None or crypto is None:
            raise _UnsafeSnapshot(503, "snapshot_crypto_unavailable")
        if not items:
            raise _UnsafeSnapshot(409, "prepared_items_empty")
        graph_version = _graph_version_for_items(context.operation, items)

        raw_items = _request_items(context)
        protected_values = tuple(
            _protected_item_value(
                item,
                context,
                raw_items[item.index],
                preparation,
                self._versions,
                policy,
                graph_version,
            )
            for item in items
        )
        parent_attempt_id = _server_identifier(
            self._server_id_factory(),
            maximum_length=36,
        )
        children = tuple(
            _server_identifier(
                f"{parent_attempt_id}:{item.index}",
                maximum_length=128,
            )
            for item in items
        )
        payload_ids = tuple(
            _server_identifier(self._server_id_factory(), maximum_length=36)
            for _ in items
        )
        if len(set((parent_attempt_id, *payload_ids))) != len(payload_ids) + 1:
            raise _UnsafeSnapshot(503, "server_id_unavailable")
        protected_refs = tuple(_protected_payload_reference(payload_id) for payload_id in payload_ids)
        execution_snapshots = tuple(
            _execution_snapshot(
                context=context,
                item=item,
                versions=self._versions,
                protected_ref=protected_refs[item.index],
            )
            for item in items
        )
        request_projections = tuple(
            _request_data_projection(
                context=context,
                item=item,
                raw_item=raw_items[item.index],
                submission_id=parent_attempt_id,
                preparation=preparation,
                versions=self._versions,
                policy=policy,
                protected_ref=protected_refs[item.index],
                payload_id=payload_ids[item.index],
            )
            for item in items
        )
        item_values = tuple(
            _protected_value_with_projections(
                value,
                item.legacy_message_projection,
                request_projections[item.index],
                execution_snapshots[item.index],
            )
            for item, value in zip(items, protected_values, strict=True)
        )
        item_digests = tuple(_sha256(_canonical_json(value)) for value in item_values)
        manifest = {
            "purpose": _COMMAND_PROOF_PURPOSE,
            "actor_id": context.actor.user_id,
            "operation": context.operation,
            "raw_key": context.raw_key.raw if context.raw_key else None,
            "fingerprint": _context_fingerprint_digest(context),
            "canonical_request": _plain(context.canonical_request),
            "preparation": _plain(preparation),
            "parent_attempt_id": parent_attempt_id,
            "children": children,
            "payload_ids": payload_ids,
            "protected_refs": protected_refs,
            "ordered_count": len(items),
            "item_digests": item_digests,
            "projector_version": self._versions.projector,
            "reference_policy_version": self._versions.reference_policy,
            "graph_version": graph_version,
        }
        manifest_bytes = _canonical_json(manifest)
        command_proof = authority.sign(_COMMAND_PROOF_PURPOSE, manifest_bytes)
        manifest_digest = _sha256(manifest_bytes)
        ordered_manifest = tuple(
            {
                "index": item.index,
                "child_attempt_id": children[item.index],
                "payload_id": payload_ids[item.index],
                "protected_ref": protected_refs[item.index],
                "item_digest": item_digests[item.index],
            }
            for item in items
        )

        encrypted: list[_EncryptedItem] = []
        for item, child_id, payload_id, protected_ref, protected_value, snapshot in zip(
            items,
            children,
            payload_ids,
            protected_refs,
            item_values,
            execution_snapshots,
            strict=True,
        ):
            record = item.credential_record
            endpoint_policy = item.endpoint_policy
            if record is None or endpoint_policy is None:
                raise _UnsafeSnapshot(409, "credential_binding_unavailable")
            target_ref = _protected_payload_subreference(protected_ref, "target")
            model_ref = _protected_payload_subreference(protected_ref, "model")
            grant_ref = _protected_payload_subreference(protected_ref, "grant")
            payload_value = {
                "parent_attempt_id": parent_attempt_id,
                "protected_ref": protected_ref,
                "child_attempt_id": child_id,
                "item_index": item.index,
                "ordered_count": len(items),
                "ordered_manifest": ordered_manifest,
                "ordered_manifest_digest": manifest_digest,
                "manifest_proof": command_proof,
                "graph_version": graph_version,
                "command_manifest": manifest,
                "item": protected_value,
            }
            plaintext = _canonical_json(payload_value).decode("utf-8")
            associated_data = _canonical_json(
                {
                    "purpose": _PROTECTED_INPUT_PURPOSE,
                    "actor_id": context.actor.user_id,
                    "operation": context.operation,
                    "parent_attempt_id": parent_attempt_id,
                    "protected_ref": protected_ref,
                    "child_attempt_id": child_id,
                    "item_index": item.index,
                    "ordered_count": len(items),
                    "ordered_manifest_digest": manifest_digest,
                    "command_proof": command_proof,
                    "fingerprint": _context_fingerprint_digest(context),
                    "scope": item.model.scope.identity(),
                    "binding_id": item.binding_id,
                    "binding_version": item.binding_version,
                    "principal_id": record.principal_id,
                    "schema_version": preparation.fingerprint.schema_version,
                    "projector_version": self._versions.projector,
                    "reference_policy_version": self._versions.reference_policy,
                    "target_ref": target_ref,
                    "model_ref": model_ref,
                    "grant_ref": grant_ref,
                }
            )
            sealed = crypto.seal_secret(plaintext, associated_data)
            payload = PreparedPayload(
                actor_id=context.actor.user_id,
                operation=context.operation,
                server_attempt_id=child_id,
                scope=item.model.scope,
                binding_id=item.binding_id,
                binding_version=item.binding_version,
                principal_id=record.principal_id,
                context_fingerprint=_context_fingerprint_digest(context),
                schema_version=preparation.fingerprint.schema_version,
                projector_version=self._versions.projector,
                reference_policy_version=self._versions.reference_policy,
                target_ref=target_ref,
                model_ref=model_ref,
                grant_ref=grant_ref,
                command_proof=command_proof,
                ciphertext=sealed.ciphertext,
                nonce=sealed.nonce,
                encryption_key_id=sealed.key_id,
            )
            encrypted.append(
                _EncryptedItem(
                    parent_attempt_id=parent_attempt_id,
                    index=item.index,
                    child_attempt_id=child_id,
                    payload_id=payload_id,
                    protected_ref=protected_ref,
                    graph_version=graph_version,
                    manifest_digest=manifest_digest,
                    payload=payload,
                    snapshot=snapshot,
                    legacy_message_projection=item.legacy_message_projection,
                    request_data_projection=request_projections[item.index],
                )
            )
        return tuple(encrypted)


class _UnsafeSnapshot(Exception):
    def __init__(self, status_code: int, code: str) -> None:
        self.status_code = status_code
        self.code = code
        super().__init__(code)


def _capture_staging_origin(
    session: Session,
    manifest_digest: str,
) -> _StagingOrigin | None:
    try:
        session_root = session.get_transaction()
        session_nested = session.get_nested_transaction()
        connection = session.connection()
        connection_root = connection.get_transaction()
        connection_nested = connection.get_nested_transaction()
    except Exception:
        return None
    if session_root is None or connection_root is None:
        return None
    handles = _transaction_lineage(
        session_root,
        session_nested,
        connection_root,
        connection_nested,
    )
    if not handles or any(
        type(getattr(handle, "is_active", None)) is not bool for handle in handles
    ):
        return None
    return _StagingOrigin(
        writer_session_ref=weakref.ref(session),
        lifecycle_handles=handles,
        manifest_digest=manifest_digest,
    )


def _transaction_lineage(*transactions: object | None) -> tuple[object, ...]:
    handles: list[object] = []
    seen: set[int] = set()
    for transaction in transactions:
        current = transaction
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            handles.append(current)
            current = getattr(current, "parent", None)
    return tuple(handles)


def _original_lifecycle_ended(handles: tuple[object, ...]) -> bool:
    if not handles:
        return False
    try:
        return all(
            type(getattr(handle, "is_active", None)) is bool
            and handle.is_active is False
            for handle in handles
        )
    except Exception:
        return False


def _unresolved_payloads() -> PayloadVerificationResult:
    return PayloadVerificationResult("unresolved")


def _verify_staged_payload_rows(
    read_session: Session,
    expected: SnapshotPayloadsStaged,
    *,
    authority: HmacProofAuthority,
    crypto: CredentialCrypto,
) -> bool:
    if (
        expected.executable is not False
        or not isinstance(expected.items, tuple)
        or not 1 <= len(expected.items) <= 64
        or not _is_sha256(expected.fingerprint)
        or not _is_sha256(expected.manifest_digest)
        or not expected.graph_version
        or not expected.parent_attempt_id
        or not expected.actor_id
        or expected.operation not in SUPPORTED_OPERATIONS
    ):
        return False

    expected_items = expected.items
    attempts = tuple(item.server_attempt_id for item in expected_items)
    payload_ids = tuple(item.prepared_payload_id for item in expected_items)
    protected_refs = tuple(item.protected_ref for item in expected_items)
    if (
        len(set(attempts)) != len(expected_items)
        or len(set(payload_ids)) != len(expected_items)
        or len(set(protected_refs)) != len(expected_items)
        or expected.parent_attempt_id in payload_ids
    ):
        return False
    for index, item in enumerate(expected_items):
        if (
            not isinstance(item, SnapshotPayloadItem)
            or item.index != index
            or item.server_attempt_id != f"{expected.parent_attempt_id}:{index}"
            or item.prepared_payload_id != item.payload_staged.prepared_payload_id
            or item.protected_ref != _protected_payload_reference(item.prepared_payload_id)
        ):
            return False

    rows = read_session.execute(
        select(prepared_payloads).where(
            prepared_payloads.c.actor_id == expected.actor_id,
            prepared_payloads.c.operation == expected.operation,
            prepared_payloads.c.server_attempt_id.in_(attempts),
        )
    ).mappings().all()
    if len(rows) != len(expected_items):
        return False
    rows_by_attempt = {row["server_attempt_id"]: dict(row) for row in rows}
    if len(rows_by_attempt) != len(expected_items) or set(rows_by_attempt) != set(attempts):
        return False

    ordered_rows = tuple(rows_by_attempt[attempt] for attempt in attempts)
    proofs = tuple(row["command_proof"] for row in ordered_rows)
    if len(set(proofs)) != 1:
        return False

    opened: list[Mapping[str, object]] = []
    for item, row in zip(expected_items, ordered_rows, strict=True):
        if not _payload_row_matches_expectation(row, item, expected):
            return False
        if expected.graph_version != _graph_version(expected.operation, row["provider"]):
            return False
        plaintext = _open_stored_payload(
            row,
            item,
            expected,
            crypto=crypto,
        )
        if plaintext is None:
            return False
        try:
            decoded = json.loads(plaintext)
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        if not isinstance(decoded, Mapping) or _canonical_json(decoded).decode("utf-8") != plaintext:
            return False
        opened.append(decoded)

    return _verify_opened_payload_manifest(
        expected,
        ordered_rows,
        opened,
        authority=authority,
    )


def _payload_row_matches_expectation(
    row: Mapping[str, object],
    item: SnapshotPayloadItem,
    expected: SnapshotPayloadsStaged,
) -> bool:
    return all(
        (
            row.get("prepared_payload_id") == item.prepared_payload_id,
            row.get("actor_id") == expected.actor_id,
            row.get("operation") == expected.operation,
            row.get("server_attempt_id") == item.server_attempt_id,
            row.get("context_fingerprint") == expected.fingerprint,
            row.get("target_ref") == _protected_payload_subreference(item.protected_ref, "target"),
            row.get("model_ref") == _protected_payload_subreference(item.protected_ref, "model"),
            row.get("grant_ref") == _protected_payload_subreference(item.protected_ref, "grant"),
            isinstance(row.get("command_proof"), str) and _is_sha256(row["command_proof"]),
            isinstance(row.get("ciphertext"), bytes) and bool(row["ciphertext"]),
            isinstance(row.get("nonce"), bytes) and bool(row["nonce"]),
            isinstance(row.get("encryption_key_id"), str) and bool(row["encryption_key_id"]),
            isinstance(row.get("schema_version"), str) and bool(row["schema_version"]),
            isinstance(row.get("projector_version"), str) and bool(row["projector_version"]),
            isinstance(row.get("reference_policy_version"), str)
            and bool(row["reference_policy_version"]),
            isinstance(row.get("binding_id"), str) and bool(row["binding_id"]),
            type(row.get("binding_version")) is int and row["binding_version"] > 0,
            isinstance(row.get("principal_id"), str) and bool(row["principal_id"]),
            row.get("source") in {"system", "user"},
            isinstance(row.get("row_id"), str) and bool(row["row_id"]),
            isinstance(row.get("owner_or_system"), str) and bool(row["owner_or_system"]),
            isinstance(row.get("provider"), str) and bool(row["provider"]),
        )
    )


def _open_stored_payload(
    row: Mapping[str, object],
    item: SnapshotPayloadItem,
    expected: SnapshotPayloadsStaged,
    *,
    crypto: CredentialCrypto,
) -> str | None:
    associated_data = _stored_payload_associated_data(row, item, expected)
    material = SealedMaterial(
        ciphertext=row["ciphertext"],
        nonce=row["nonce"],
        key_id=row["encryption_key_id"],
        algorithm_version=crypto.algorithm_version,
    )
    return crypto.open_secret(material, associated_data)


def canonical_payload_json(value: object) -> bytes:
    """Return the exact canonical encoding used by protected payload proofs."""

    return _canonical_json(value)


def stored_payload_associated_data(
    row: Mapping[str, object],
    *,
    parent_attempt_id: str,
    protected_ref: str,
    item_index: int,
    ordered_count: int,
    ordered_manifest_digest: str,
) -> bytes:
    """Build the producer's frozen AEAD AAD for a durable payload row."""

    return _canonical_json(
        {
            "purpose": _PROTECTED_INPUT_PURPOSE,
            "actor_id": row["actor_id"],
            "operation": row["operation"],
            "parent_attempt_id": parent_attempt_id,
            "protected_ref": protected_ref,
            "child_attempt_id": row["server_attempt_id"],
            "item_index": item_index,
            "ordered_count": ordered_count,
            "ordered_manifest_digest": ordered_manifest_digest,
            "command_proof": row["command_proof"],
            "fingerprint": row["context_fingerprint"],
            "scope": {
                "source": row["source"],
                "row_id": row["row_id"],
                "owner_or_system": row["owner_or_system"],
                "provider": row["provider"],
            },
            "binding_id": row["binding_id"],
            "binding_version": row["binding_version"],
            "principal_id": row["principal_id"],
            "schema_version": row["schema_version"],
            "projector_version": row["projector_version"],
            "reference_policy_version": row["reference_policy_version"],
            "target_ref": row["target_ref"],
            "model_ref": row["model_ref"],
            "grant_ref": row["grant_ref"],
        }
    )


def _stored_payload_associated_data(
    row: Mapping[str, object],
    item: SnapshotPayloadItem,
    expected: SnapshotPayloadsStaged,
) -> bytes:
    return stored_payload_associated_data(
        row,
        parent_attempt_id=expected.parent_attempt_id,
        protected_ref=item.protected_ref,
        item_index=item.index,
        ordered_count=len(expected.items),
        ordered_manifest_digest=expected.manifest_digest,
    )


def _verify_opened_payload_manifest(
    expected: SnapshotPayloadsStaged,
    rows: Sequence[Mapping[str, object]],
    opened: Sequence[Mapping[str, object]],
    *,
    authority: HmacProofAuthority,
) -> bool:
    if len(opened) != len(expected.items) or not opened:
        return False
    proofs = {row["command_proof"] for row in rows}
    if len(proofs) != 1:
        return False
    proof = next(iter(proofs))
    command_manifest = opened[0].get("command_manifest")
    if not isinstance(command_manifest, Mapping):
        return False
    manifest_bytes = _canonical_json(command_manifest)
    if _sha256(manifest_bytes) != expected.manifest_digest:
        return False
    if not authority.verify(_COMMAND_PROOF_PURPOSE, manifest_bytes, proof):
        return False

    item_digests: list[str] = []
    for index, (item, row, payload) in enumerate(
        zip(expected.items, rows, opened, strict=True)
    ):
        if (
            payload.get("parent_attempt_id") != expected.parent_attempt_id
            or payload.get("protected_ref") != item.protected_ref
            or payload.get("child_attempt_id") != item.server_attempt_id
            or payload.get("item_index") != index
            or payload.get("ordered_count") != len(expected.items)
            or payload.get("ordered_manifest_digest") != expected.manifest_digest
            or payload.get("manifest_proof") != proof
            or payload.get("graph_version") != expected.graph_version
            or payload.get("command_manifest") != command_manifest
        ):
            return False
        protected_item = payload.get("item")
        if not isinstance(protected_item, Mapping):
            return False
        if (
            protected_item.get("index") != index
            or protected_item.get("operation") != expected.operation
            or protected_item.get("fingerprint") != expected.fingerprint
            or protected_item.get("context_schema") != row["schema_version"]
            or protected_item.get("projector_version") != row["projector_version"]
            or protected_item.get("reference_policy_version") != row["reference_policy_version"]
            or protected_item.get("graph_version") != expected.graph_version
        ):
            return False
        if _canonical_json(
            protected_item.get("execution_snapshot")
        ) != _canonical_json(item.snapshot):
            return False
        if _canonical_json(protected_item.get("legacy_message_projection")) != _canonical_json(
            _legacy_message_projection_value(item.legacy_message_projection)
        ):
            return False
        if _canonical_json(protected_item.get("request_data_projection")) != _canonical_json(
            _request_data_projection_value(item.request_data_projection)
        ):
            return False
        credential = protected_item.get("credential")
        if not isinstance(credential, Mapping) or not _protected_credential_matches_row(
            credential, row
        ):
            return False
        projection = protected_item.get("request_data_projection")
        if not isinstance(projection, Mapping) or not _payload_projection_matches(
            projection, item, expected
        ):
            return False
        item_digests.append(_sha256(_canonical_json(protected_item)))

    children = [item.server_attempt_id for item in expected.items]
    payload_ids = [item.prepared_payload_id for item in expected.items]
    protected_refs = [item.protected_ref for item in expected.items]
    ordered_manifest = [
        {
            "index": item.index,
            "child_attempt_id": item.server_attempt_id,
            "payload_id": item.prepared_payload_id,
            "protected_ref": item.protected_ref,
            "item_digest": digest,
        }
        for item, digest in zip(expected.items, item_digests, strict=True)
    ]
    required_manifest_keys = {
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
    if set(command_manifest) != required_manifest_keys:
        return False
    first_row = rows[0]
    if not (
        command_manifest.get("purpose") == _COMMAND_PROOF_PURPOSE
        and command_manifest.get("actor_id") == expected.actor_id
        and command_manifest.get("operation") == expected.operation
        and command_manifest.get("fingerprint") == expected.fingerprint
        and _canonical_json(command_manifest.get("preparation"))
        == _canonical_json(expected.preparation)
        and command_manifest.get("parent_attempt_id") == expected.parent_attempt_id
        and command_manifest.get("children") == children
        and command_manifest.get("payload_ids") == payload_ids
        and command_manifest.get("protected_refs") == protected_refs
        and command_manifest.get("ordered_count") == len(expected.items)
        and command_manifest.get("item_digests") == item_digests
        and command_manifest.get("projector_version") == first_row["projector_version"]
        and command_manifest.get("reference_policy_version")
        == first_row["reference_policy_version"]
        and command_manifest.get("graph_version") == expected.graph_version
    ):
        return False

    for payload in opened:
        if payload.get("ordered_manifest") != ordered_manifest:
            return False
    return True


def _protected_credential_matches_row(
    credential: Mapping[str, object],
    row: Mapping[str, object],
) -> bool:
    return (
        credential.get("scope")
        == {
            "source": row["source"],
            "row_id": row["row_id"],
            "owner_or_system": row["owner_or_system"],
            "provider": row["provider"],
        }
        and credential.get("binding_id") == row["binding_id"]
        and credential.get("binding_version") == row["binding_version"]
        and credential.get("principal_id") == row["principal_id"]
    )


def _payload_projection_matches(
    projection: Mapping[str, object],
    item: SnapshotPayloadItem,
    expected: SnapshotPayloadsStaged,
) -> bool:
    return (
        projection.get("operation") == expected.operation
        and projection.get("submission_id") == expected.parent_attempt_id
        and projection.get("request_digest") == expected.fingerprint
        and projection.get("prepared_payload_id") == item.prepared_payload_id
        and projection.get("protected_ref") == item.protected_ref
        and projection.get("target_ref") == _protected_payload_subreference(item.protected_ref, "target")
        and projection.get("model_ref") == _protected_payload_subreference(item.protected_ref, "model")
        and projection.get("grant_ref") == _protected_payload_subreference(item.protected_ref, "grant")
    )


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{64}", value))


def build_media_snapshot_producer(
    authentication_service: AuthenticationService,
    registry_capabilities: RegistryCapabilities,
    *,
    server_id_factory: Callable[[], str],
    policies: Mapping[str, AdmissionPolicy],
    versions: SnapshotVersions,
) -> MediaSnapshotProducer:
    """Build an explicit producer; no environment or provider discovery occurs."""

    from haoai_backend.generation_credentials.application import build_credential_registry

    return MediaSnapshotProducer(
        authentication_service,
        registry_capabilities,
        build_credential_registry(registry_capabilities),
        server_id_factory=server_id_factory,
        policies=policies,
        versions=versions,
    )


def _request_items(context: AdmissionContext) -> tuple[Mapping[str, object], ...]:
    request = context.canonical_request
    if context.operation == AdmissionOperation.IMAGE_BATCH.value:
        values = request.get("assets")
        if not isinstance(values, (list, tuple)):
            raise _UnsafeSnapshot(409, "canonical_batch_unavailable")
        if any(not isinstance(value, Mapping) for value in values):
            raise _UnsafeSnapshot(409, "canonical_batch_unavailable")
        return tuple(values)
    if context.operation in {
        AdmissionOperation.IMAGE_SINGLE.value,
        AdmissionOperation.VIDEO_SINGLE.value,
    }:
        return (request,)
    raise _UnsafeSnapshot(422, "unsupported_operation")


def _references(operation: str, item: Mapping[str, object]) -> Mapping[str, tuple[str, ...]]:
    if operation == AdmissionOperation.IMAGE_BATCH.value:
        values = item.get("reference_images", ())
        if not isinstance(values, (list, tuple)) or any(
            not isinstance(value, str) for value in values
        ):
            raise _UnsafeSnapshot(409, "canonical_references_invalid")
        return MappingProxyType(
            {"reference_images": tuple(values)}
        )
    result: dict[str, tuple[str, ...]] = {}
    for name in ("reference_images", "reference_audios", "reference_videos"):
        values = item.get(name, ())
        if not isinstance(values, (list, tuple)) or any(not isinstance(value, str) for value in values):
            raise _UnsafeSnapshot(409, "canonical_references_invalid")
        result[name] = tuple(values)
    return MappingProxyType(result)


def _effective_prompt(
    operation: str,
    raw_item: Mapping[str, object],
    selection: PromptSelection | None,
) -> str:
    if operation == AdmissionOperation.IMAGE_BATCH.value:
        description = raw_item.get("description") or raw_item.get("name")
        if not isinstance(description, str) or not description:
            raise _UnsafeSnapshot(409, "batch_description_missing")
        return compose_batch_image_prompt(selection, description=description)
    prompt = raw_item.get("prompt")
    if not isinstance(prompt, str) or not prompt:
        raise _UnsafeSnapshot(409, "single_prompt_missing")
    return prompt


def _legacy_message_projection(
    *,
    operation: str,
    raw_item: Mapping[str, object],
    facts: PreparedItemFacts,
    target: _FullTarget,
    references: Mapping[str, tuple[str, ...]],
) -> LegacyMessageProjection | None:
    if operation != AdmissionOperation.IMAGE_BATCH.value:
        return None
    if facts.target.kind != "asset_batch" or facts.target.asset_id is None:
        raise _UnsafeSnapshot(409, "batch_target_unavailable")

    raw_description = raw_item.get("description") or raw_item.get("name")
    if not isinstance(raw_description, str) or not raw_description:
        raise _UnsafeSnapshot(409, "batch_description_missing")
    asset_row = target.protected_values.get("asset")
    if not isinstance(asset_row, Mapping):
        raise _UnsafeSnapshot(409, "locked_asset_missing")
    asset_name = (
        raw_item.get("name")
        or asset_row.get("name")
        or asset_row.get("title")
        or facts.target.asset_id
    )
    if not isinstance(asset_name, str) or not asset_name:
        raise _UnsafeSnapshot(409, "locked_asset_name_unavailable")

    size = facts.effective_parameters.get("size")
    if not _is_json_scalar(size) or isinstance(size, bool):
        raise _UnsafeSnapshot(409, "effective_size_unavailable")
    reference_count, reference_digest = _reference_image_metadata(
        references.get("reference_images", ())
    )
    model_id = raw_item.get("model_id")
    if not isinstance(model_id, str) or not model_id:
        raise _UnsafeSnapshot(409, "selected_model_id_unavailable")
    prompt_id = raw_item.get("prompt_id") or ""
    if not isinstance(prompt_id, str):
        raise _UnsafeSnapshot(409, "selected_prompt_id_unavailable")

    context = {
        "modelId": model_id,
        "promptId": prompt_id,
        "size": size,
        "refImagesCount": reference_count,
        "refImagesDigest": reference_digest,
    }
    chapter_id = facts.target.chapter_id
    asset_type = facts.target.asset_type
    if not isinstance(chapter_id, str) or not isinstance(asset_type, str):
        raise _UnsafeSnapshot(409, "batch_target_unavailable")
    return LegacyMessageProjection(
        user_content=f"__CTX__:{json.dumps(context, ensure_ascii=False)}\n{raw_description}",
        assistant_content="正在生成图片...",
        assistant_wire_content=None,
        model_name=facts.model.display_name,
        chapter_id=chapter_id,
        asset_type=asset_type,
        asset_id=facts.target.asset_id,
        asset_name=asset_name,
    )


def _execution_snapshot(
    *,
    context: AdmissionContext,
    item: _FullItem,
    versions: SnapshotVersions,
    protected_ref: str,
) -> FrozenExecutionSnapshot:
    if item.credential_record is None or item.endpoint_policy is None:
        raise _UnsafeSnapshot(409, "credential_binding_unavailable")
    endpoint_policy = item.endpoint_policy
    return FrozenExecutionSnapshot(
        model_ref=ModelReference(
            owner_id=item.model.owner_or_system,
            provider=item.model.provider,
            model_id=item.model.selection.catalog_id,
        ),
        model_category=item.model.selection.category.value,
        model_name=_snapshot_model_name(
            context.operation, item.model.provider, item.model.wire_model
        ),
        model_params=_snapshot_model_parameters(item),
        prompt_ref=item.prompt_ref,
        effective_prompt=item.effective_prompt,
        effective_context="",
        server_quote=item.facts.quote_amount,
        reference_snapshot_ref=_protected_payload_subreference(protected_ref, "references"),
        target_snapshot=item.target.snapshot,
        credential_binding_ref=f"{item.binding_id}:v{item.binding_version}",
        grant_snapshot=item.grant.snapshot,
        adapter_config_versions=(
            ("projector", versions.projector),
            ("reference_policy", versions.reference_policy),
            ("credential_binding_version", str(item.binding_version)),
            ("endpoint_policy", endpoint_policy.policy_version),
            ("api_base_path", endpoint_policy.api_base_path),
        ),
        endpoint_policy=SnapshotEndpointPolicy(
            provider_origin=endpoint_policy.provider_origin,
            allowed_content_paths=endpoint_policy.allowed_content_paths,
            allow_private_network=False,
        ),
    )


def _protected_value_with_projections(
    protected_value: Mapping[str, object],
    legacy: LegacyMessageProjection | None,
    request_data: RequestDataProjection,
    snapshot: FrozenExecutionSnapshot,
) -> Mapping[str, object]:
    result = dict(protected_value)
    result["execution_snapshot"] = _plain(snapshot)
    result["legacy_message_projection"] = _legacy_message_projection_value(legacy)
    result["request_data_projection"] = _request_data_projection_value(request_data)
    return MappingProxyType(result)


def _legacy_message_projection_value(
    legacy: LegacyMessageProjection | None,
) -> Mapping[str, object] | None:
    if legacy is None:
        return None
    return {
        "user_content": legacy.user_content,
        "assistant_content": legacy.assistant_content,
        "assistant_wire_content": legacy.assistant_wire_content,
        "model_name": legacy.model_name,
        "chapter_id": legacy.chapter_id,
        "asset_type": legacy.asset_type,
        "asset_id": legacy.asset_id,
        "asset_name": legacy.asset_name,
    }


def _request_data_projection_value(
    request_data: RequestDataProjection,
) -> Mapping[str, object]:
    return {
        "operation": request_data.operation,
        "submission_id": request_data.submission_id,
        "request_digest": request_data.request_digest,
        "rule_digest": request_data.rule_digest,
        "mode": request_data.mode,
        "model_id": request_data.model_id,
        "model_source": request_data.model_source,
        "model_owner_id": request_data.model_owner_id,
        "prompt_id": request_data.prompt_id,
        "size": request_data.size,
        "chapter_id": request_data.chapter_id,
        "asset_type": request_data.asset_type,
        "asset_id": request_data.asset_id,
        "asset_name": request_data.asset_name,
        "reference_images_version": request_data.reference_images_version,
        "reference_images_count": request_data.reference_images_count,
        "reference_images_digest": request_data.reference_images_digest,
        "prepared_payload_id": request_data.prepared_payload_id,
        "protected_ref": request_data.protected_ref,
        "target_ref": request_data.target_ref,
        "model_ref": request_data.model_ref,
        "grant_ref": request_data.grant_ref,
        "target_snapshot": _target_snapshot_value(request_data.target_snapshot),
    }


def _target_snapshot_value(target: TargetSnapshot) -> Mapping[str, object]:
    return {
        "kind": target.kind,
        "chapter_id": target.chapter_id,
        "series_id": target.series_id,
        "stable_storyboard_asset_id": target.stable_storyboard_asset_id,
        "asset_type": target.asset_type,
        "asset_id": target.asset_id,
        "compat_frame_index": target.compat_frame_index,
        "expected_text_digest": target.expected_text_digest,
        "media_identity": target.media_identity,
        "content_digest": target.content_digest,
        "notes_revision": target.notes_revision,
        "full_frame_digest": target.full_frame_digest,
        "full_chapter_digest": target.full_chapter_digest,
        "expected_asset_image_url": target.expected_asset_image_url,
        "same_series_target_digest": target.same_series_target_digest,
    }


def _protected_payload_reference(payload_id: str) -> str:
    return f"prepared-payload-v1/{len(payload_id)}:{payload_id}"


def _protected_payload_subreference(protected_ref: str, part: str) -> str:
    if part not in {"target", "model", "grant", "references"}:
        raise _UnsafeSnapshot(409, "protected_reference_part_invalid")
    return f"{protected_ref}#{part}"


def _request_data_projection(
    *,
    context: AdmissionContext,
    item: _FullItem,
    raw_item: Mapping[str, object],
    submission_id: str,
    preparation: AdmissionPreparationFacts,
    versions: SnapshotVersions,
    policy: AdmissionPolicy,
    protected_ref: str,
    payload_id: str,
) -> RequestDataProjection:
    reference_count, reference_digest = _reference_image_metadata(
        item.references.get("reference_images", ())
    )
    model_id = raw_item.get("model_id")
    if not isinstance(model_id, str) or not model_id:
        raise _UnsafeSnapshot(409, "selected_model_id_unavailable")
    prompt_value = raw_item.get("prompt_id") or None
    if prompt_value is not None and not isinstance(prompt_value, str):
        raise _UnsafeSnapshot(409, "selected_prompt_id_unavailable")
    size = item.facts.effective_parameters.get("size")
    if not _is_json_scalar(size) or isinstance(size, bool):
        raise _UnsafeSnapshot(409, "effective_size_unavailable")

    target = item.target.snapshot
    legacy = item.legacy_message_projection
    target_ref = _protected_payload_subreference(protected_ref, "target")
    model_ref = _protected_payload_subreference(protected_ref, "model")
    grant_ref = _protected_payload_subreference(protected_ref, "grant")
    request_digest = _context_fingerprint_digest(context)
    rule_digest = _rule_digest(
        context=context,
        item=item,
        preparation=preparation,
        reference_count=reference_count,
        reference_digest=reference_digest,
        versions=versions,
        policy=policy,
        graph_version=_graph_version(context.operation, item.model.scope.provider),
    )
    return RequestDataProjection(
        operation=context.operation,
        submission_id=submission_id,
        request_digest=request_digest,
        rule_digest=rule_digest,
        mode=(
            "batch_image"
            if context.operation == AdmissionOperation.IMAGE_BATCH.value
            else context.operation
        ),
        model_id=model_id,
        model_source=item.model.selection.source.value,
        model_owner_id=(
            item.model.owner_or_system
            if item.model.selection.source is ModelSource.USER
            else None
        ),
        prompt_id=prompt_value,
        size=size,
        chapter_id=legacy.chapter_id if legacy is not None else target.chapter_id,
        asset_type=legacy.asset_type if legacy is not None else target.asset_type,
        asset_id=legacy.asset_id if legacy is not None else target.asset_id,
        asset_name=legacy.asset_name if legacy is not None else None,
        reference_images_version=_REFERENCE_SNAPSHOT_VERSION,
        reference_images_count=reference_count,
        reference_images_digest=reference_digest,
        prepared_payload_id=payload_id,
        protected_ref=protected_ref,
        target_ref=target_ref,
        model_ref=model_ref,
        grant_ref=grant_ref,
        target_snapshot=target,
    )


def _rule_digest(
    *,
    context: AdmissionContext,
    item: _FullItem,
    preparation: AdmissionPreparationFacts,
    reference_count: int,
    reference_digest: str,
    versions: SnapshotVersions,
    policy: AdmissionPolicy,
    graph_version: str,
) -> str:
    rule = {
        "schema": "generation-graph-item-rule-v1",
        "actor_id": context.actor.user_id,
        "operation": context.operation,
        "item_index": item.index,
        "request_digest": _context_fingerprint_digest(context),
        "phase": preparation.phase,
        "quote_amount": item.facts.quote_amount,
        "model": {
            "source": item.model.selection.source.value,
            "id": item.model.selection.catalog_id,
            "category": item.model.selection.category.value,
            "provider": item.model.provider,
            "provider_route": _provider_route(item.model.provider),
            "wire_model": item.model.wire_model,
            "snapshot_model_name": _snapshot_model_name(
                context.operation,
                item.model.provider,
                item.model.wire_model,
            ),
            "raw_parameters": _plain(item.model.params),
            "snapshot_parameters": _plain(_snapshot_model_parameters(item)),
        },
        "prompt": {
            "id": item.prompt_ref,
            "digest": item.facts.prompt_digest,
        },
        "effective_parameters": _plain(item.facts.effective_parameters),
        "target": _target_snapshot_value(item.target.snapshot),
        "reference_images": {
            "version": _REFERENCE_SNAPSHOT_VERSION,
            "count": reference_count,
            "digest": reference_digest,
        },
        "projector_version": versions.projector,
        "reference_policy_version": versions.reference_policy,
        "graph_version": graph_version,
        "admission_policy": _admission_policy_value(policy),
    }
    return _sha256(_canonical_json(rule))


def _admission_policy_value(policy: AdmissionPolicy) -> Mapping[str, object]:
    """Return every configured A2 policy field under its stable schema version."""

    return {
        "version": _ADMISSION_POLICY_VERSION,
        "max_concurrent_tasks": policy.max_concurrent_tasks,
    }


def _graph_version(operation: str, provider: str) -> str:
    if operation == AdmissionOperation.IMAGE_SINGLE.value:
        return "image-single-v1"
    if operation == AdmissionOperation.IMAGE_BATCH.value:
        return "image-batch-v1"
    if operation == AdmissionOperation.VIDEO_SINGLE.value:
        version = (
            "video-haoai-attempt-group-v1"
            if _provider_route(provider) == "haoai"
            else "video-single-v1"
        )
        return version
    raise _UnsafeSnapshot(422, "unsupported_operation")


def _graph_version_for_items(operation: str, items: Sequence[_FullItem]) -> str:
    versions = {
        _graph_version(operation, item.model.scope.provider)
        for item in items
    }
    if len(versions) != 1:
        raise _UnsafeSnapshot(409, "graph_version_ambiguous")
    return next(iter(versions))


def _context_fingerprint_digest(context: AdmissionContext) -> str:
    fingerprint = context.fingerprint
    if fingerprint is None:
        raise _UnsafeSnapshot(409, "context_fingerprint_unavailable")
    return fingerprint.ordered_nonsecret_payload_digest


def _provider_route(provider: str) -> str:
    """Normalize only for fixed routing; retain the exact provider in scope data."""

    return provider.lower()


def _snapshot_model_name(operation: str, provider: str, wire_model: str | None) -> str:
    if wire_model:
        if not isinstance(wire_model, str):
            raise _UnsafeSnapshot(409, "wire_model_unavailable")
        return wire_model

    route = _provider_route(provider)
    if operation in {
        AdmissionOperation.IMAGE_SINGLE.value,
        AdmissionOperation.IMAGE_BATCH.value,
    } and route == "openai":
        return "dall-e-3"
    if operation == AdmissionOperation.VIDEO_SINGLE.value:
        defaults = {
            "haoai": "grok-imagine-video-1.5-preview",
            "manxiaobai": "grok-video-1.0",
            "xai": "grok-imagine-video",
        }
        default = defaults.get(route)
        if default is not None:
            return default
    raise _UnsafeSnapshot(409, "wire_model_unavailable")


def _snapshot_model_parameters(item: _FullItem) -> Mapping[str, object]:
    parameters = dict(item.model.params)
    parameters.update(item.facts.effective_parameters)
    return _freeze_mapping(parameters)


def _reference_image_metadata(values: Sequence[str]) -> tuple[int, str]:
    if any(not isinstance(value, str) for value in values):
        raise _UnsafeSnapshot(409, "canonical_references_invalid")
    payload = json.dumps(list(values), ensure_ascii=False, separators=(",", ":"))
    return len(values), _sha256(payload.encode("utf-8"))


def _is_json_scalar(value: object) -> bool:
    if value is None or isinstance(value, (str, int, bool)):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _prompt_values(
    selection: PromptSelection | None,
    actor_id: str,
) -> Mapping[str, object]:
    if selection is None:
        return MappingProxyType({"source": "request"})
    return _freeze_mapping(
        {
            "prompt_id": selection.prompt_id,
            "owner_or_system": (
                actor_id if selection.source is ModelSource.USER else "system"
            ),
            "operation": selection.operation.value,
            "source": selection.source.value,
            "category": selection.category,
            "display_name": selection.display_name,
            "system_prompt": selection.system_prompt,
            "user_prompt": selection.user_prompt,
        }
    )


def _parse_params(raw: object) -> Mapping[str, object]:
    if raw is None or raw == "":
        return MappingProxyType({})
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError, json.JSONDecodeError):
        raise _UnsafeSnapshot(409, "model_params_invalid") from None
    if not isinstance(value, Mapping):
        raise _UnsafeSnapshot(409, "model_params_invalid")
    return _freeze_mapping(value)


def _freeze_mapping(
    value: Mapping[str, object],
    *,
    invalid_code: str = "model_params_invalid",
    allow_datetime: bool = False,
) -> Mapping[str, object]:
    frozen: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise _UnsafeSnapshot(409, invalid_code)
        frozen[key] = _freeze_value(
            item,
            invalid_code=invalid_code,
            allow_datetime=allow_datetime,
        )
    return MappingProxyType(frozen)


def _freeze_value(
    value: object,
    *,
    invalid_code: str = "model_params_invalid",
    allow_datetime: bool = False,
) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _UnsafeSnapshot(409, invalid_code)
        return value
    if isinstance(value, datetime):
        if allow_datetime:
            return value.isoformat()
        raise _UnsafeSnapshot(409, invalid_code)
    if isinstance(value, Mapping):
        return _freeze_mapping(
            value,
            invalid_code=invalid_code,
            allow_datetime=allow_datetime,
        )
    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_value(
                item,
                invalid_code=invalid_code,
                allow_datetime=allow_datetime,
            )
            for item in value
        )
    raise _UnsafeSnapshot(409, invalid_code)


def _unique_record(
    source: RootEnrollmentSource,
    record_id: object,
) -> ProtectedAccountRecord | None:
    if not isinstance(record_id, str) or not record_id:
        return None
    matches = [record for record in source.records if record.record_id == record_id]
    return matches[0] if len(matches) == 1 and source.verify_record(matches[0]) else None


def _principal_matches_origin(
    principal: Mapping[str, object],
    scope: CredentialScope,
    origin: ProtectedAccountRecord,
) -> bool:
    return all(
        (
            origin.scope == scope,
            principal.get("principal_id") == origin.principal_id,
            principal.get("provider") == scope.provider,
            principal.get("issuer_namespace") == origin.issuer_namespace,
            principal.get("stable_account_subject") == origin.stable_account_subject,
            principal.get("owner_scope") == scope.owner_or_system,
            principal.get("record_id") == origin.record_id,
            principal.get("evidence_ref") == origin.evidence_ref,
            principal.get("evidence_version") == origin.evidence_version,
        )
    )


def _same_principal_identity(
    principal: Mapping[str, object],
    scope: CredentialScope,
    record: ProtectedAccountRecord,
) -> bool:
    return all(
        (
            record.scope == scope,
            principal.get("principal_id") == record.principal_id,
            principal.get("provider") == scope.provider,
            principal.get("issuer_namespace") == record.issuer_namespace,
            principal.get("stable_account_subject") == record.stable_account_subject,
            principal.get("owner_scope") == scope.owner_or_system,
        )
    )


def _version_matches_record(
    version: Mapping[str, object],
    scope: CredentialScope,
    record: ProtectedAccountRecord,
) -> bool:
    return all(
        (
            record.scope == scope,
            version.get("source") == scope.source,
            version.get("row_id") == scope.row_id,
            version.get("owner_or_system") == scope.owner_or_system,
            version.get("provider") == scope.provider,
            version.get("principal_id") == record.principal_id,
            version.get("issuer_namespace") == record.issuer_namespace,
            version.get("stable_account_subject") == record.stable_account_subject,
            version.get("key_generation") == record.key_generation,
            version.get("record_id") == record.record_id,
            version.get("protected_record_id") == record.record_id,
            version.get("evidence_ref") == record.evidence_ref,
            version.get("evidence_version") == record.evidence_version,
            version.get("guard_key_id") == record.guard_key_id,
            version.get("secret_consistency_guard") == record.secret_consistency_guard,
        )
    )


def _version_provenance(
    version: Mapping[str, object],
    attempt: ServerAttempt,
    source: RootEnrollmentSource,
) -> Mapping[str, object]:
    rotation: Mapping[str, object] | None = None
    if attempt.rotation_proof_ref:
        proof = source.rotation_proof(attempt.rotation_proof_ref)
        if proof is None:
            raise _UnsafeSnapshot(409, "credential_rotation_proof_missing")
        rotation = {
            "proof_id": proof.proof_id,
            "original_record_id": proof.original_record_id,
            "replacement_record_id": proof.replacement_record_id,
            "binding_id": proof.binding_id,
            "predecessor_version": proof.predecessor_version,
            "proof_key_id": proof.proof_key_id,
            "proof_signature": proof.proof_signature,
        }
    return _freeze_mapping(
        {
            "version_kind": version.get("version_kind"),
            "binding_version": version.get("binding_version"),
            "predecessor_version": version.get("predecessor_version"),
            "expected_revision": version.get("expected_revision"),
            "server_attempt_id": attempt.attempt_id,
            "actor_id": attempt.actor_id,
            "operation": attempt.operation,
            "key_generation": attempt.key_generation,
            "protected_record_id": attempt.protected_record_id,
            "rotation_proof_ref": attempt.rotation_proof_ref,
            "proof_key_id": attempt.proof_key_id,
            "command_proof": attempt.command_proof,
            "rotation_proof": rotation,
        }
    )


def _attempt_from_version(
    row: Mapping[str, object],
    scope: CredentialScope,
) -> ServerAttempt | None:
    try:
        return ServerAttempt(
            attempt_id=str(row["server_attempt_id"]),
            actor_id=str(row["actor_id"]),
            operation=row["operation"],
            scope=scope,
            key_generation=row.get("attempt_key_generation"),
            expected_revision=row.get("expected_revision"),
            predecessor_version=row.get("predecessor_version"),
            protected_record_id=row.get("protected_record_id"),
            rotation_proof_ref=row.get("rotation_proof_ref"),
            proof_key_id=str(row["proof_key_id"]),
            command_proof=str(row["command_proof"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _head_operation_matches(
    version: Mapping[str, object],
    attempt: ServerAttempt,
    head: int,
) -> bool:
    expected = {
        "registered": "register",
        "rotation": "rotate",
        "replacement": "replace",
    }.get(version.get("version_kind"))
    if expected != attempt.operation:
        return False
    predecessor = version.get("predecessor_version")
    if head == 1:
        return predecessor is None and attempt.predecessor_version is None
    return (
        type(predecessor) is int
        and predecessor == head - 1
        and attempt.predecessor_version == predecessor
        and attempt.expected_revision == head - 1
    )


def _verify_rotation_if_needed(
    repository: RegistryReadRepository,
    source: RootEnrollmentSource,
    attempt: ServerAttempt,
    current: ProtectedAccountRecord,
    binding_id: str,
    now: datetime,
) -> None:
    if attempt.operation != "rotate":
        return
    if attempt.predecessor_version is None or not attempt.rotation_proof_ref:
        raise _UnsafeSnapshot(409, "credential_rotation_proof_missing")
    previous = repository.read_version(binding_id, attempt.predecessor_version)
    if previous is None:
        raise _UnsafeSnapshot(409, "credential_rotation_predecessor_missing")
    original = _unique_record(source, previous.get("record_id"))
    principal_id = previous.get("principal_id")
    principal = repository.find_principal(principal_id) if isinstance(principal_id, str) else None
    previous_attempt = _attempt_from_version(previous, attempt.scope)
    origin_record = (
        _unique_record(source, principal.get("record_id")) if principal is not None else None
    )
    proof = source.rotation_proof(attempt.rotation_proof_ref)
    if (
        original is None
        or principal is None
        or principal.get("source_state") != "valid"
        or origin_record is None
        or not _principal_matches_origin(principal, attempt.scope, origin_record)
        or not _version_matches_record(previous, attempt.scope, original)
        or not _same_principal_identity(principal, attempt.scope, original)
        or previous_attempt is None
        or not previous_attempt.verify(source.authority)
        or not _head_operation_matches(
            previous,
            previous_attempt,
            attempt.predecessor_version,
        )
        or proof is None
        or previous.get("principal_id") != current.principal_id
        or not source.verify_rotation(
            original,
            current,
            proof,
            binding_id=binding_id,
            predecessor_version=attempt.predecessor_version,
            now=now,
        )
    ):
        raise _UnsafeSnapshot(409, "credential_rotation_proof_invalid")


def _endpoint_policy_from_config(
    config: Any,
    version: Mapping[str, object],
    record: ProtectedAccountRecord,
) -> CredentialEndpointPolicy:
    raw_base_url = config.base_url
    if not isinstance(raw_base_url, str) or not raw_base_url:
        raise _UnsafeSnapshot(409, "credential_endpoint_unavailable")
    try:
        policy = CredentialEndpointPolicy.from_base_url(
            raw_base_url,
            allowed_content_paths=record.endpoint_policy.allowed_content_paths,
            policy_version=record.endpoint_policy.policy_version,
        )
        allowed = json.loads(str(version["allowed_content_paths"]))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise _UnsafeSnapshot(409, "credential_endpoint_invalid") from None
    if (
        policy != record.endpoint_policy
        or version.get("endpoint_origin") != policy.provider_origin
        or version.get("api_base_path") != policy.api_base_path
        or not isinstance(allowed, list)
        or any(not isinstance(path, str) for path in allowed)
        or tuple(allowed) != policy.allowed_content_paths
        or version.get("endpoint_policy_version") != policy.policy_version
    ):
        raise _UnsafeSnapshot(409, "credential_endpoint_mismatch")
    return policy


def _current_utc(capabilities: RegistryCapabilities) -> datetime:
    clock = capabilities.db_clock
    if clock is None:
        raise _UnsafeSnapshot(503, "credential_clock_unavailable")
    try:
        value = clock()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() != timezone.utc.utcoffset(value)
        ):
            raise ValueError("clock must return aware UTC")
        return value.astimezone(timezone.utc)
    except Exception:
        raise _UnsafeSnapshot(503, "credential_clock_unavailable") from None


def _protected_item_value(
    item: _FullItem,
    context: AdmissionContext,
    raw_item: Mapping[str, object],
    preparation: AdmissionPreparationFacts,
    versions: SnapshotVersions,
    policy: AdmissionPolicy,
    graph_version: str,
) -> Mapping[str, object]:
    record = item.credential_record
    endpoint = item.endpoint_policy
    if record is None or endpoint is None:
        raise _UnsafeSnapshot(409, "credential_binding_unavailable")
    return {
        "index": item.index,
        "operation": context.operation,
        "context_schema": preparation.fingerprint.schema_version,
        "fingerprint": _context_fingerprint_digest(context),
        "raw_key": context.raw_key.raw if context.raw_key else None,
        "raw_item": _plain(raw_item),
        "model": {
            "source": item.model.selection.source.value,
            "row_id": item.model.selection.catalog_id,
            "owner_or_system": item.model.owner_or_system,
            "provider": item.model.provider,
            "provider_route": _provider_route(item.model.provider),
            "category": item.model.selection.category.value,
            "display_name": item.model.selection.display_name,
            "wire_model": item.model.wire_model,
            "snapshot_model_name": _snapshot_model_name(
                context.operation,
                item.model.provider,
                item.model.wire_model,
            ),
            "base_url": item.model.raw_base_url,
            "params": _plain(item.model.params),
            "effective_parameters": _plain(item.facts.effective_parameters),
            "snapshot_parameters": _plain(_snapshot_model_parameters(item)),
            "quote_amount": item.facts.quote_amount,
        },
        "prompt": {
            "id": item.prompt_ref,
            "selection": _plain(item.prompt_values),
            "effective_prompt": item.effective_prompt,
            "effective_context": "",
        },
        "references": _plain(item.references),
        "target": _plain(item.target.protected_values),
        "grant": _plain(item.grant.protected_values),
        "credential": {
            "scope": item.model.scope.identity(),
            "binding_id": item.binding_id,
            "binding_version": item.binding_version,
            "principal_id": record.principal_id,
            "record_id": record.record_id,
            "issuer_namespace": record.issuer_namespace,
            "stable_account_subject": record.stable_account_subject,
            "key_generation": record.key_generation,
            "evidence_ref": record.evidence_ref,
            "evidence_version": record.evidence_version,
            "endpoint_origin": endpoint.provider_origin,
            "api_base_path": endpoint.api_base_path,
            "allowed_content_paths": endpoint.allowed_content_paths,
            "endpoint_policy_version": endpoint.policy_version,
            "protected_record": _protected_record_value(record),
            "version_provenance": _plain(item.credential_provenance),
        },
        "projector_version": versions.projector,
        "reference_policy_version": versions.reference_policy,
        "graph_version": graph_version,
        "admission_policy": _admission_policy_value(policy),
    }


def _protected_record_value(record: ProtectedAccountRecord) -> Mapping[str, object]:
    return {
        "record_id": record.record_id,
        "principal_id": record.principal_id,
        "scope": record.scope.identity(),
        "issuer_namespace": record.issuer_namespace,
        "stable_account_subject": record.stable_account_subject,
        "key_generation": record.key_generation,
        "material_ref": record.material_ref,
        "evidence_ref": record.evidence_ref,
        "evidence_version": record.evidence_version,
        "valid_from": record.valid_from,
        "valid_until": record.valid_until,
        "revoked_at": record.revoked_at,
        "endpoint_policy": record.endpoint_policy.identity(),
        "guard_key_id": record.guard_key_id,
        "secret_consistency_guard": record.secret_consistency_guard,
        "proof_key_id": record.proof_key_id,
        "proof_signature": record.proof_signature,
    }


def _asset_digest(asset: Mapping[str, object]) -> str:
    name = asset.get("name") or asset.get("title")
    return _sha256(
        _canonical_json(
            {
                "name": name,
                "description": asset.get("description"),
                "image_url": asset.get("image_url"),
                "updated_at": _datetime_value(asset.get("updated_at")),
            }
        )
    )


def _datetime_value(value: object) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else str(value) if value is not None else None


def _digest_text(value: object) -> str:
    return _sha256(str(value or "").encode("utf-8"))


def _parse_chapter_frames(value: object) -> list[object] | None:
    if not isinstance(value, str):
        return None
    try:
        frames = json.loads(value)
    except (TypeError, ValueError):
        return None
    return frames if isinstance(frames, list) else None


def _parse_canonical_chapter_frames(
    value: object,
) -> tuple[list[Mapping[str, object]], str] | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        frames = json.loads(
            value,
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
        canonical_digest = _sha256(_canonical_json(frames))
    except (TypeError, ValueError, OverflowError):
        return None
    return frames, canonical_digest


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("chapter JSON contains a duplicate object key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"chapter JSON contains a non-finite value: {value}")


def _frame_has_no_storyboard_identity(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    if "storyboard" not in value:
        return True
    references = value["storyboard"]
    return isinstance(references, list) and all(
        reference is None or reference == "" for reference in references
    )


def _digest_canonical_json(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return _sha256(encoded.encode("utf-8"))


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        _plain(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _plain(value: object) -> object:
    if isinstance(value, Enum):
        return _plain(value.value)
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise _UnsafeSnapshot(409, "protected_value_not_json")
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise _UnsafeSnapshot(409, "protected_value_not_json")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _server_identifier(value: object, *, maximum_length: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum_length:
        raise _UnsafeSnapshot(503, "server_id_unavailable")
    return value

