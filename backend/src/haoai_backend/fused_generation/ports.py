"""Execution and persistence ports for fused image generation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Protocol

from haoai_backend.generation_credentials.application import CredentialRegistry
from haoai_backend.generation_credentials.binding_capture_ports import (
    CapturedCredentialBinding,
)
from haoai_backend.generation_credentials.domain import CredentialScope
from haoai_backend.shared.identity import TrustedActor

from .domain import (
    FusedGenerationInput,
    FusedGenerationInlineImage,
    FusedGenerationRuntimeOutput,
    FusedGenerationStorageOutcome,
    FusedGenerationUnknownReason,
    FusedPromptText,
    FusedServerImageModel,
    FusedTaskClaim,
    PreparedFusedGeneration,
)
from haoai_backend.generation_contracts import StoredMedia
from haoai_backend.media_storage.owned_write_values import (
    OwnedObjectMaterial,
    OwnedObjectPlan,
    OwnedObjectPutResult,
    OwnedObjectVerification,
    OwnedObjectWritePermit,
)


class FusedGenerationRuntime(Protocol):
    """Configured server-side driver; receives only the selected model identity."""

    async def generate(
        self, request: FusedGenerationInput
    ) -> FusedGenerationRuntimeOutput: ...


class FusedGenerationStorage(Protocol):
    """Transfer adapter; None is a definite transfer failure, not an unknown result."""

    async def store_generated_image(
        self, provider_url: str
    ) -> FusedGenerationStorageOutcome: ...


class FusedGenerationImageStorage(Protocol):
    """Synchronous owned-media adapter for validated inline image bytes."""

    def store_inline_image(self, image: FusedGenerationInlineImage) -> StoredMedia: ...


class FusedGenerationPlannedMediaStorage(Protocol):
    """Prepare, conditionally write, and independently verify fused media."""

    def prepare_public_image(
        self, image_url: str, *, owner_id: str, submission_id: str, result_digest: str
    ) -> OwnedObjectMaterial: ...

    def prepare_inline_image(
        self,
        image: FusedGenerationInlineImage,
        *,
        owner_id: str,
        submission_id: str,
        result_digest: str,
    ) -> OwnedObjectMaterial: ...

    def put_planned(
        self, material: OwnedObjectMaterial, permit: OwnedObjectWritePermit
    ) -> OwnedObjectPutResult: ...

    def verify_planned(self, plan: OwnedObjectPlan) -> OwnedObjectVerification: ...


class FusedGenerationUnitOfWork(Protocol):
    """Operations borrowed by fused application services for one Session."""

    def current_time(self) -> datetime: ...
    def new_id(self) -> str: ...

    # Durable submission reservation, snapshot acceptance, and receipt reads.
    def resolve_durable_model_identity(
        self,
        user_id: str,
        model_id: str,
    ) -> tuple[FusedServerImageModel, CredentialScope]: ...
    def capture_durable_binding(
        self,
        registry: CredentialRegistry,
        scope: CredentialScope,
    ) -> CapturedCredentialBinding | None: ...
    def find_durable_submission(
        self,
        owner_id: str,
        idempotency_key: str,
    ) -> Mapping[str, object] | None: ...
    def find_durable_submission_receipt(
        self, owner_id: str, idempotency_key: str
    ) -> Mapping[str, object] | None: ...
    def durable_submission_by_id(
        self,
        submission_id: str,
        *,
        owner_id: str | None = None,
    ) -> Mapping[str, object] | None: ...
    def recoverable_durable_submissions(
        self, *, limit: int = 32
    ) -> tuple[Mapping[str, object], ...]: ...
    def reserve_durable_submission(
        self,
        *,
        submission_id: str,
        owner_id: str,
        idempotency_key: str,
        request_digest: str,
        model: FusedServerImageModel,
        scope: CredentialScope,
        captured_binding: CapturedCredentialBinding,
        task_id: str,
        message_id: str | None,
        encrypted_request: str,
        request_nonce: str,
        seal_key_id: str,
        crypto_algorithm: str,
    ) -> None: ...
    def save_durable_snapshot(
        self,
        *,
        submission_id: str,
        encrypted_snapshot: str,
        snapshot_nonce: str,
        snapshot_proof_key_id: str,
        snapshot_proof: str,
    ) -> bool: ...
    def reject_durable_submission(
        self,
        *,
        submission_id: str,
        status_code: int,
        response_json: str,
    ) -> bool: ...
    def accept_durable_submission(
        self,
        prepared: PreparedFusedGeneration,
        *,
        response_status: int = 200,
        response_json: str,
    ) -> bool: ...

    # Shared task preparation and legacy execution.
    def require_series_access(self, actor: TrustedActor, series_id: str) -> None: ...
    def load_member_asset(
        self, member_type: str, series_id: str, asset_id: str
    ) -> Mapping[str, object] | None: ...
    def resolve_and_charge_model(self, user_id: str, model_id: str) -> FusedServerImageModel: ...
    def resolve_image_prompt(self, user_id: str, prompt_id: str) -> FusedPromptText | None: ...
    def load_chapter(self, chapter_id: str) -> Mapping[str, object] | None: ...
    def stage_request(self, prepared: PreparedFusedGeneration, user_id: str) -> None: ...
    def claim_task(
        self, task_id: str, claim_token: str, *, expected_user_id: str
    ) -> FusedTaskClaim | None: ...

    # Durable task claim, one-time provider dispatch, and known result storage.
    def claim_durable_task(
        self,
        task_id: str,
        submission_id: str,
        claim_token: str,
        *,
        expected_user_id: str,
    ) -> FusedTaskClaim | None: ...
    def persist_dispatch_gate(
        self,
        *,
        task_id: str,
        submission_id: str,
        user_id: str,
        claim_token: str,
        generation: int,
        dispatch_nonce: str,
        wire_digest: str,
    ) -> bool: ...
    def dispatch_gate_matches(
        self,
        *,
        task_id: str,
        submission_id: str,
        user_id: str,
        claim_token: str,
        generation: int,
        dispatch_nonce: str,
        wire_digest: str,
    ) -> bool: ...
    def persist_provider_result(
        self,
        *,
        task_id: str,
        submission_id: str,
        claim_token: str,
        generation: int,
        encrypted_result: str,
        result_nonce: str,
        result_kind: str,
        result_digest: str,
    ) -> bool: ...
    def result_record_matches(
        self,
        submission_id: str,
        task_id: str,
        claim_token: str,
        generation: int,
        result_digest: str,
    ) -> bool: ...
    def begin_durable_media_storage(
        self,
        task_id: str,
        submission_id: str,
        user_id: str,
        claim_token: str,
        generation: int,
    ) -> bool: ...
    def durable_media_storage_started_matches(
        self,
        task_id: str,
        submission_id: str,
        user_id: str,
        claim_token: str,
        generation: int,
    ) -> bool: ...
    def fused_media_write_by_submission(
        self, submission_id: str, *, slot: str = "primary"
    ) -> Mapping[str, object] | None: ...
    def persist_fused_media_plan(
        self,
        *,
        submission_id: str,
        task_id: str,
        owner_id: str,
        claim_token: str,
        generation: int,
        result_digest: str,
        result_kind: str,
        material_sha256: str,
        media_type: str,
        size_bytes: int,
        config_fingerprint: str,
        config_version: int,
        bucket: str,
        object_key: str,
        public_base: str,
        write_id: str,
        plan_version: int,
        encrypted_material: str,
        material_nonce: str,
        material_key_id: str,
        material_algorithm: str,
    ) -> bool: ...
    def claim_fused_media_put(
        self,
        *,
        submission_id: str,
        task_id: str,
        owner_id: str,
        claim_token: str,
        generation: int,
        write_nonce: str,
    ) -> bool: ...
    def fused_media_put_claim_matches(
        self,
        *,
        submission_id: str,
        task_id: str,
        owner_id: str,
        claim_token: str,
        generation: int,
        write_nonce: str,
    ) -> bool: ...
    def record_fused_media_put_outcome(
        self, submission_id: str, write_id: str, *, unknown: bool
    ) -> bool: ...
    def claim_fused_media_verification(
        self,
        *,
        submission_id: str,
        owner_id: str,
        verifier_token: str,
        lease_seconds: int,
        allow_active_task: bool = False,
        expected_claim_token: str | None = None,
        expected_generation: int | None = None,
    ) -> Mapping[str, object] | None: ...
    def record_fused_media_verification(
        self,
        *,
        submission_id: str,
        write_id: str,
        verifier_token: str,
        status: str,
        observed_sha256: str | None = None,
        observed_size_bytes: int | None = None,
        media_type: str | None = None,
        locator: str | None = None,
        request_id: str | None = None,
    ) -> bool: ...
    def complete_verified_fused_media(
        self,
        prepared: PreparedFusedGeneration,
        *,
        write_id: str,
        verifier_token: str,
        locator: str,
    ) -> bool: ...
    def recoverable_fused_media_writes(
        self, *, limit: int = 32
    ) -> tuple[Mapping[str, object], ...]: ...
    def retain_interrupted_media_write(self, submission_id: str) -> bool: ...
    def retain_dispatch_unknown(self, submission_id: str) -> bool: ...
    def authorize_media_storage(
        self, task_id: str, user_id: str, claim_token: str
    ) -> bool: ...
    def retain_unknown(
        self,
        task_id: str,
        user_id: str,
        claim_token: str,
        safe_reason: FusedGenerationUnknownReason,
    ) -> bool: ...
    def retain_known_media_result(
        self,
        task_id: str,
        user_id: str,
        claim_token: str,
        result_url: str,
    ) -> bool: ...
    def finish_task(
        self,
        prepared: PreparedFusedGeneration,
        claim_token: str,
        *,
        image_url: str | None,
        error_message: str | None,
        preserve_if_cancelled: bool = False,
    ) -> bool: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...


FusedGenerationUnitOfWorkFactory = Callable[[], FusedGenerationUnitOfWork]
