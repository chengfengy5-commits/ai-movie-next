"""Typed public ports for generation contracts.

Methods describe caller-owned effects. Except for explicit claim acquisition,
implementations stage or flush work on the supplied transaction and leave its
commit, rollback, and close to the caller.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol, TypeAlias

from .records import (
    ApplicationRecord,
    ExecutionAttemptGroup,
    GenerationResultReceipt,
    LocalContinuationWork,
    PolicyStopEvent,
    PollReservation,
    UnitFeeDecision,
)

from .types import (
    AdmissionCommand,
    AdmissionFingerprint,
    ApplicationOutcome,
    BatchImageReceipt,
    BillingOutcome,
    BoundCredential,
    CanvasApplyAuthority,
    ClaimAuthority,
    CommittedIntent,
    CommittedStreamDebitReceipt,
    CommittedStreamStartPermit,
    ContinuationClaimAuthority,
    ControlSettlementAuthority,
    ControlledQueryAuthority,
    ControlledRecoveryScope,
    CredentialBinding,
    ExternalOperationRef,
    Failure,
    FrameQueuedReceipt,
    FrozenExecutionSnapshot,
    FrozenJSON,
    GenerationActor,
    GenerationApplicationCommand,
    GenerationItem,
    JSONValue,
    MediaResult,
    OperationPolicy,
    OwnerTaskScope,
    PolicyStopFinancialDisposition,
    PreparedReference,
    ProviderContentCapability,
    ProviderFailure,
    ProviderObservation,
    RawAdmissionKey,
    ReceiptLookup,
    RecoveryPlan,
    ReferenceInput,
    ShutdownReceipt,
    SingleAdmissionReceipt,
    StagedIntent,
    StagedStreamDebitReceipt,
    StagedStreamStartPermit,
    StagedWebStartCapability,
    StoredMedia,
    StreamFeeAuthority,
    StreamSettlementProof,
    StreamTaskBinding,
    SubmissionGraphDraft,
    TargetSnapshot,
    TextEvent,
    TextResult,
    TransferFailure,
    UnknownCommit,
    UnknownCreate,
    ValidatedGenerationResult,
    WebStreamAuthority,
    WebStreamCommand,
)



class CallerTransaction(Protocol):
    """Minimal staging surface. It intentionally has no commit method."""

    def flush(self) -> None: ...


@dataclass(frozen=True, slots=True)
class StagedPollReservation:
    reservation: PollReservation

    def __post_init__(self) -> None:
        if self.reservation.state != "reserved":
            raise ValueError("staged poll reservations must be reserved")


@dataclass(frozen=True, slots=True)
class CommittedPollReservation:
    reservation: PollReservation
    confirmed_at: datetime

    def __post_init__(self) -> None:
        if self.reservation.state != "reserved":
            raise ValueError("committed poll reservations must be reserved")


class GenerationAdmissionUnitOfWork(Protocol):
    def stage_graph(
        self,
        snapshot: FrozenExecutionSnapshot,
        policy: OperationPolicy,
        ordered_items: Sequence[GenerationItem],
    ) -> SubmissionGraphDraft: ...

    def stage_precharges(
        self, graph: SubmissionGraphDraft, quote_policy: OperationPolicy
    ) -> Sequence[BillingOutcome]: ...

    def commit_receipt(
        self, graph: SubmissionGraphDraft
    ) -> SingleAdmissionReceipt | BatchImageReceipt | FrameQueuedReceipt: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


AdmissionUnitOfWork: TypeAlias = GenerationAdmissionUnitOfWork


class GenerationCatalog(Protocol):
    def begin_admission(self, actor: GenerationActor) -> AdmissionUnitOfWork: ...

    def find_original_receipt(
        self,
        operation: str,
        raw_key: RawAdmissionKey | None,
        fingerprint: AdmissionFingerprint,
    ) -> ReceiptLookup: ...

    def authorize_new_and_lock_catalog_targets(
        self, command: AdmissionCommand
    ) -> FrozenExecutionSnapshot: ...

    def stage_stream_debit(
        self,
        tx: CallerTransaction,
        authority: StreamFeeAuthority,
        frozen_quote: int,
    ) -> StagedStreamDebitReceipt: ...

    def confirm_stream_commit(
        self,
        authority: StreamFeeAuthority,
        original_attempt: str,
        commit_result: UnknownCommit | None,
    ) -> CommittedStreamDebitReceipt | UnknownCommit: ...

    def bind_stream_task(
        self,
        tx: CallerTransaction,
        authority: StreamFeeAuthority,
        original_receipt: CommittedStreamDebitReceipt,
        owned_task: str,
        message: str,
    ) -> StreamTaskBinding | Literal["same", "conflict"]: ...


class GenerationExecutionPort(Protocol):
    def claim_next(self, task_type: str, instance: str) -> ClaimAuthority | None: ...

    def renew_or_revoke(self, claim: ClaimAuthority) -> Literal["renewed", "ownership_lost"]: ...

    def claim_continuation(
        self, task_type: str, instance: str, lease_seconds: int
    ) -> ContinuationClaimAuthority | None: ...


class LifecycleUnitOfWork(Protocol):
    def prepare_policy_intent(
        self,
        tx: CallerTransaction,
        claim: ClaimAuthority,
        step: str,
        frozen_operation_policy: OperationPolicy,
        safe_request: FrozenExecutionSnapshot,
    ) -> StagedIntent: ...

    def commit_intent(
        self, tx: CallerTransaction, staged: StagedIntent
    ) -> CommittedIntent: ...

    def record_observation(
        self, tx: CallerTransaction, scope: OwnerTaskScope, observation: ProviderObservation
    ) -> Literal["recorded", "duplicate", "conflict"]: ...

    def validate_observation(
        self, scope: OwnerTaskScope, observation_ref: str
    ) -> ValidatedGenerationResult | Literal["pending", "invalid"]: ...

    def charge_or_refund(
        self,
        tx: CallerTransaction,
        scope: OwnerTaskScope,
        unit: str,
        evidence: str,
        policy: OperationPolicy,
    ) -> BillingOutcome: ...

    def signal_cancel(self, scope: OwnerTaskScope) -> Literal["accepted_signal"]: ...

    def cancel_and_settle(
        self,
        tx: CallerTransaction,
        scope: OwnerTaskScope,
        lifecycle_v1_policy: str,
    ) -> Literal[
        "partial", "succeeded", "succeeded_refunded", "failed_refunded",
        "failed_retained", "failed_charged", "failed_without_verified_debit",
        "expired_refunded", "expired_retained",
        "expired_without_verified_debit", "not_due", "already_settled",
        "stale_observation", "cancelled",
    ]: ...

    def settle_due(
        self, tx: CallerTransaction, scope: OwnerTaskScope, unit: str
    ) -> Literal[
        "partial", "succeeded", "succeeded_refunded", "failed_refunded",
        "failed_retained", "failed_charged", "failed_without_verified_debit",
        "expired_refunded", "expired_retained",
        "expired_without_verified_debit", "not_due", "already_settled",
        "stale_observation", "cancelled",
    ]: ...

    def classify_recovery(self, scope: OwnerTaskScope) -> Sequence[RecoveryPlan]: ...

    def resolve_fee_once(
        self,
        tx: CallerTransaction,
        authority: ContinuationClaimAuthority,
        unit: str,
        result_ref: str,
        application_ref: str | None,
        phase: str,
    ) -> UnitFeeDecision: ...

    def advance_attempt_group(
        self,
        tx: CallerTransaction,
        claim: ClaimAuthority,
        group: ExecutionAttemptGroup,
        validated_active_failure: ProviderObservation,
    ) -> Literal["next_prepared", "group_failed", "blocked"]: ...

    def complete_attempt_group(
        self,
        tx: CallerTransaction,
        controlled_recovery: ControlledRecoveryScope,
        group: ExecutionAttemptGroup,
        linked_validated_result: ValidatedGenerationResult,
    ) -> Literal["generation_fulfilled", "failed", "pending"]: ...

    def persist_fulfillment_and_continuation(
        self,
        tx: CallerTransaction,
        controlled_validator: str,
        linked_validated_result: ValidatedGenerationResult,
    ) -> tuple[GenerationResultReceipt, LocalContinuationWork]: ...

    def bind_current_continuation(
        self,
        tx: CallerTransaction,
        current: ClaimAuthority,
        work_ref: str,
    ) -> ContinuationClaimAuthority | Literal["ownership_lost", "phase_conflict"]: ...

    def lookup_continuation(
        self, scope: OwnerTaskScope, unit_and_result: tuple[str, str]
    ) -> (
        tuple[LocalContinuationWork, UnitFeeDecision | None, ApplicationRecord | None]
        | Literal["pending"]
    ): ...

    def lookup_stream_debit(
        self, authority: StreamFeeAuthority, original_attempt: str
    ) -> CommittedStreamDebitReceipt | Literal["proven_absent", "unknown"]: ...

    def refund_stream_once(
        self,
        tx: CallerTransaction,
        authority: StreamFeeAuthority,
        original_receipt: CommittedStreamDebitReceipt,
        proof: StreamSettlementProof,
    ) -> Literal["refunded", "already_refunded", "no_debit", "pending"]: ...

    def acquire_stream_start(
        self,
        tx: CallerTransaction,
        authority: StreamFeeAuthority,
        committed_receipt: CommittedStreamDebitReceipt,
        optional_binding: StreamTaskBinding | None,
    ) -> StagedStreamStartPermit: ...

    def expose_stream_start(
        self, tx: CallerTransaction, committed_permit: CommittedStreamStartPermit
    ) -> StagedWebStartCapability | Literal[
        "revoked", "already_exposed", "unknown"
    ]: ...

    def reserve_active_poll(
        self,
        tx: CallerTransaction,
        authority: ClaimAuthority | ControlledQueryAuthority,
        group: ExecutionAttemptGroup,
    ) -> StagedPollReservation | Literal["not_due", "blocked", "budget_exhausted"]: ...

    def accept_poll_observation(
        self, tx: CallerTransaction, reservation: CommittedPollReservation, raw: ProviderObservation
    ) -> Literal["recorded", "duplicate", "conflict", "policy_stop"]: ...

    def close_expired_poll_reservation(
        self,
        tx: CallerTransaction,
        controlled_recovery: ControlledQueryAuthority,
        reservation: CommittedPollReservation,
    ) -> Literal["closed_unknown", "same", "not_due"]: ...

    def stop_group_by_policy(
        self,
        tx: CallerTransaction,
        controlled_validator: str,
        event: PolicyStopEvent,
    ) -> Literal["policy_stopped", "same"]: ...

    def settle_policy_stop(
        self, tx: CallerTransaction, scope: OwnerTaskScope, event: PolicyStopEvent
    ) -> Literal["refunded", "retained", "no_debit", "pending"]: ...

    def settle_committed_policy_before_cancel(
        self,
        tx: CallerTransaction,
        scope: OwnerTaskScope,
        locked_new_registry_scope: str,
    ) -> Sequence[PolicyStopFinancialDisposition] | Literal["pending", "conflict"]: ...


class ProviderTransport(Protocol):
    def stream_text(
        self, snapshot: FrozenExecutionSnapshot, authority: WebStreamAuthority
    ) -> AsyncIterator[TextEvent]: ...

    def optimize_frame(
        self,
        snapshot: FrozenExecutionSnapshot,
        context: str,
        intent: CommittedIntent,
    ) -> TextResult | ProviderFailure: ...

    def generate_sync_image(
        self,
        snapshot: FrozenExecutionSnapshot,
        refs: Sequence[ReferenceInput],
        intent: CommittedIntent,
    ) -> MediaResult | UnknownCreate | Failure: ...

    def submit_async_media(
        self,
        snapshot: FrozenExecutionSnapshot,
        refs: Sequence[ReferenceInput],
        intent: CommittedIntent,
    ) -> ExternalOperationRef: ...

    def query_existing(
        self,
        snapshot: FrozenExecutionSnapshot,
        external_ref: ExternalOperationRef,
        reservation: CommittedPollReservation | None = None,
    ) -> ProviderObservation: ...

    def fetch_provider_content(
        self, snapshot: FrozenExecutionSnapshot, external_ref: ExternalOperationRef
    ) -> ReferenceInput: ...

    def resolve_original_credential(
        self,
        binding: CredentialBinding,
        purpose: Literal["create", "query", "content"],
    ) -> BoundCredential | Literal["unavailable"]: ...


GenerationProviderPort: TypeAlias = ProviderTransport


class AsyncHttpResponse(Protocol):
    status_code: int
    headers: Mapping[str, str]

    async def iter_bytes(self, *, chunk_size: int) -> AsyncIterator[bytes]: ...


class AsyncHttpClient(Protocol):
    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        content: bytes | None = None,
        timeout_seconds: float,
        follow_redirects: bool = False,
    ) -> AsyncHttpResponse: ...


class RuntimeDatabaseSession(Protocol):
    def begin(self) -> AbstractContextManager[CallerTransaction]: ...


class PublicMediaFetcher(Protocol):
    async def fetch_public(
        self,
        reference: ReferenceInput,
        *,
        max_bytes: int,
        timeout_seconds: float,
    ) -> PreparedReference | TransferFailure: ...


class BoundProviderContentFetcher(Protocol):
    async def fetch_bound_content(
        self,
        external_ref: ExternalOperationRef,
        capability: ProviderContentCapability,
    ) -> PreparedReference | TransferFailure: ...


class LocalMuseCapability(Protocol):
    def stage_local_reference(
        self, reference: ReferenceInput, staging_directory: Path
    ) -> PreparedReference | TransferFailure: ...


class R2StorageCapability(Protocol):
    def store_prepared(self, reference: PreparedReference) -> StoredMedia | TransferFailure: ...

    def release(self, stored: StoredMedia) -> Literal[
        "released", "already_released", "not_owned", "failed"
    ]: ...


@dataclass(frozen=True, slots=True)
class RuntimeTimeoutSettings:
    public_media_fetch_seconds: Literal[600] = 600

    def __post_init__(self) -> None:
        if self.public_media_fetch_seconds != 600:
            raise ValueError("public media fetch timeout is fixed at 600 seconds")


@dataclass(frozen=True, slots=True)
class RuntimeConcurrencySettings:
    public_media_fetches: Literal[2] = 2

    def __post_init__(self) -> None:
        if self.public_media_fetches != 2:
            raise ValueError("public media fetch concurrency is fixed at two")


class GenerationMediaStore(Protocol):
    def normalize_managed_url(
        self, url: str, explicit_origin_map: Mapping[str, str]
    ) -> str | Literal["unmanaged"]: ...

    def prepare_reference(
        self, input: ReferenceInput, capability: ProviderContentCapability | None,
        config: GenerationRuntimeConfig,
    ) -> PreparedReference: ...

    def transfer_public_url(
        self, input: ReferenceInput, guard_config: FrozenJSON
    ) -> StoredMedia | TransferFailure: ...

    def store_bytes_or_provider_content(
        self, input: ReferenceInput, object_policy: FrozenJSON
    ) -> StoredMedia: ...

    def release_prepared_reference(
        self, handle: PreparedReference
    ) -> Literal["released", "already_released", "not_owned", "failed"]: ...

    def fetch_bound_content(
        self, external_ref: ExternalOperationRef, capability: ProviderContentCapability
    ) -> PreparedReference: ...


class WorkerCapacityLease(Protocol):
    """A capacity permit whose release is safe to call from runtime cleanup."""

    def release(self) -> bool: ...


class WorkerExecutionCapacity(Protocol):
    """A nonblocking process-local gate checked before generation claims."""

    def try_acquire(self) -> WorkerCapacityLease | None: ...


@dataclass(frozen=True, slots=True)
class GenerationRuntimeConfig:
    """Explicit lazy dependency bundle; construction opens no resources."""

    session_factory: Callable[
        [], AbstractContextManager[RuntimeDatabaseSession]
    ]
    db_clock: Callable[[], datetime]
    id_factory: Callable[[], str]
    http_client_factory: Callable[
        [], AbstractAsyncContextManager[AsyncHttpClient]
    ]
    credential_resolver: Callable[
        [CredentialBinding, Literal["create", "query", "content"]],
        BoundCredential | None,
    ]
    provider_factories: tuple[tuple[str, Callable[[], ProviderTransport]], ...]
    media_store_factory: Callable[[], GenerationMediaStore]
    local_muse_staging_dir: Path | None
    public_media_fetcher_factory: Callable[[], PublicMediaFetcher]
    bound_provider_content_fetcher_factory: Callable[
        [], BoundProviderContentFetcher
    ]
    local_muse_capability_factory: Callable[[], LocalMuseCapability]
    r2_storage_factory: Callable[[], R2StorageCapability]
    timeout_settings: RuntimeTimeoutSettings = RuntimeTimeoutSettings()
    concurrency_settings: RuntimeConcurrencySettings = RuntimeConcurrencySettings()
    shutdown_timeout_seconds: float = 10.0
    worker_capacity: WorkerExecutionCapacity | None = field(
        default=None,
        repr=False,
        compare=False,
    )


class GenerationResultApplicationPort(Protocol):
    def apply_worker_result(
        self,
        tx: CallerTransaction,
        authority: ClaimAuthority | ContinuationClaimAuthority,
        validated_result: ValidatedGenerationResult,
        target: TargetSnapshot,
    ) -> ApplicationOutcome: ...

    def apply_web_text(
        self, tx: CallerTransaction, authority: WebStreamAuthority, text_result: TextResult
    ) -> ApplicationOutcome: ...

    def apply_canvas_media(
        self,
        shared_tx: CallerTransaction,
        authority: CanvasApplyAuthority,
        command: GenerationApplicationCommand,
    ) -> ApplicationOutcome: ...

    def lookup_applied_result(
        self, scope: OwnerTaskScope, result_ref: str
    ) -> ApplicationRecord | None: ...

    def load_owned_completed_result(
        self, shared_tx: CallerTransaction, actor: GenerationActor,
        task_id: str, result_ref: str,
    ) -> GenerationResultReceipt | Literal["not_found", "unready"]: ...


WorkerExecutionOutcome: TypeAlias = (
    ApplicationOutcome
    | BillingOutcome
    | GenerationResultReceipt
    | LocalContinuationWork
    | RecoveryPlan
    | UnitFeeDecision
    | Literal["pending", "ownership_lost"]
    | None
)


class GenerationExecutor(Protocol):
    """One lazily constructed task-family executor for the inert runtime."""

    def execute_claim(
        self, claim: ClaimAuthority | ContinuationClaimAuthority
    ) -> WorkerExecutionOutcome: ...


class InertGenerationRuntime(Protocol):
    """Resource-free runtime surface returned by the composition factory."""

    def run_claimed_once(self, task_type: str) -> WorkerExecutionOutcome: ...

    def run_web_stream(
        self, command: WebStreamCommand, authority: WebStreamAuthority
    ) -> AsyncIterator[TextEvent]: ...

    def shutdown(self, reason: str) -> ShutdownReceipt: ...

    def consume_cancel_and_due(
        self, scope: OwnerTaskScope
    ) -> Literal["settled", "retained", "pending", "terminal_projection"]: ...


class GenerationWorkerRuntime(InertGenerationRuntime, Protocol):
    def build_runtime(
        self,
        explicit_config: GenerationRuntimeConfig,
        executor_factories: Sequence[Callable[[], GenerationExecutor]],
    ) -> InertGenerationRuntime: ...


class SubmissionRecoveryPort(Protocol):
    def consume_recovery(
        self, plan: RecoveryPlan
    ) -> Literal[
        "query", "verify", "resume_fee", "resume_delivery",
        "finalize", "settled", "pending",
    ]: ...

    def consume_local_continuation(
        self, plan: RecoveryPlan
    ) -> Literal[
        "fee_resolved", "delivered", "finalized", "pending", "ownership_lost",
    ]: ...

    def finalize_continuation(
        self,
        tx: CallerTransaction,
        authority: ContinuationClaimAuthority,
        work: LocalContinuationWork,
    ) -> Literal["task_projection", "pending"]: ...

    def finalize_control_stop(
        self,
        tx: CallerTransaction,
        authority: ControlSettlementAuthority,
        task: str,
    ) -> Literal["control_terminal_projection", "pending"]: ...

    def consume_stream_fee_reconciliation(
        self, authority: StreamFeeAuthority, original_attempt: str
    ) -> Literal["settled", "pending"]: ...


__all__ = (
    "CallerTransaction",
    "StagedPollReservation",
    "CommittedPollReservation",
    "AsyncHttpResponse",
    "AsyncHttpClient",
    "RuntimeDatabaseSession",
    "PublicMediaFetcher",
    "BoundProviderContentFetcher",
    "LocalMuseCapability",
    "R2StorageCapability",
    "RuntimeTimeoutSettings",
    "RuntimeConcurrencySettings",
    "GenerationRuntimeConfig",
    "AdmissionUnitOfWork",
    "GenerationAdmissionUnitOfWork",
    "GenerationCatalog",
    "GenerationExecutionPort",
    "LifecycleUnitOfWork",
    "GenerationProviderPort",
    "ProviderTransport",
    "GenerationMediaStore",
    "GenerationResultApplicationPort",
    "GenerationWorkerRuntime",
    "InertGenerationRuntime",
    "GenerationExecutor",
    "WorkerExecutionOutcome",
    "SubmissionRecoveryPort",
)
