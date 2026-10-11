"""Immutable public value shapes for generation admission, execution, and recovery.

These values describe trusted adapter inputs and outputs. Constructing one in
Python does not create database, provider, billing, or application evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol, TypeAlias

from haoai_backend.shared.identity import TrustedActor

JSONScalar: TypeAlias = str | int | float | bool | None
JSONValue: TypeAlias = (
    JSONScalar
    | tuple["JSONValue", ...]
    | list["JSONValue"]
    | Mapping[str, "JSONValue"]
)
FrozenJSON: TypeAlias = Mapping[str, JSONValue]
ParameterValue: TypeAlias = (
    JSONScalar | tuple[JSONScalar, ...] | Mapping[str, JSONScalar]
)
FrozenParameters: TypeAlias = tuple[tuple[str, ParameterValue], ...]

ExecutionMode: TypeAlias = Literal["web_stream", "claimed_worker"]
ChargePhase: TypeAlias = Literal[
    "stream_pre_task",
    "single_admission",
    "batch_admission",
    "post_apply",
    "pre_apply",
]
CancellationPolicyVersion: TypeAlias = Literal[
    "legacy_signal_only", "lifecycle_v1_for_new_registered_graphs"
]
TaskStatus: TypeAlias = Literal[
    "queued", "processing", "cancelling", "reconciling", "completed", "failed"
]
BillingStatus: TypeAlias = Literal["unbilled", "charged", "refunded", "no_charge"]
UnitFeeResolution: TypeAlias = Literal[
    "pending",
    "charged",
    "no_charge",
    "waived_insufficient",
    "waived_charge_failed",
    "waived_cancelled",
    "waived_delivery_failed",
    "stopped_insufficient",
    "stopped_late_generation",
]
WorkPhase: TypeAlias = Literal[
    "delivery_pending",
    "fee_pending",
    "finalize_pending",
    "done",
    "stopped_cancelled",
    "stopped_insufficient",
    "stopped_delivery_failed",
    "stopped_late_generation",
]
ApplicationKind: TypeAlias = Literal[
    "applied", "already_applied", "stale", "target_deleted",
    "target_conflict", "write_failed",
]
BillingKind: TypeAlias = Literal[
    "charged", "already_charged", "no_charge", "insufficient", "refunded",
    "already_refunded", "retained_by_cancellation", "no_verified_debit",
]
RecoveryKind: TypeAlias = Literal[
    "submit_prepared", "query_existing", "verify_unknown", "settle_due",
    "resume_fee", "resume_delivery", "finalize", "reconcile_stream_fee",
    "keep_pending", "incomplete_graph", "noop",
]
TargetKind: TypeAlias = Literal[
    "none", "stream_message", "frame_message", "asset_message",
    "canvas_frame_preview", "canvas_asset_image", "frame_text",
]


def _freeze_json(value: JSONValue) -> JSONValue:
    """Copy nested JSON values into immutable tuples and mapping proxies."""

    if isinstance(value, Mapping):
        from types import MappingProxyType

        return MappingProxyType(
            {key: _freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError("value must contain only JSON-compatible values")


@dataclass(frozen=True, slots=True)
class FrozenGrantSnapshot:
    actor_id: str
    series_id: str | None = None
    team_id: str | None = None
    access_provenance: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EndpointPolicy:
    provider_origin: str | None
    allowed_content_paths: tuple[str, ...] = ()
    allow_private_network: bool = False


@dataclass(frozen=True, slots=True)
class ModelReference:
    owner_id: str
    provider: str
    model_id: str


@dataclass(frozen=True, slots=True)
class TaskDraft:
    task_type: str
    graph_version: str
    payload_digest: str


@dataclass(frozen=True, slots=True)
class BillingUnitDraft:
    unit_key: str
    quote: int
    currency: str = "credit"


@dataclass(frozen=True, slots=True)
class ExecutionStepDraft:
    step_key: str
    required: bool
    order: int
    request_digest: str


@dataclass(frozen=True, slots=True)
class MessageDraft:
    message_id: str
    content: str
    role: str = "assistant"


@dataclass(frozen=True, slots=True)
class GenerationItem:
    item_id: str
    target: TargetSnapshot
    request_digest: str


@dataclass(frozen=True, slots=True)
class AdmissionCommand:
    actor: GenerationActor
    operation: str
    payload_digest: str
    target: TargetSnapshot


@dataclass(frozen=True, slots=True)
class ReceiptLookup:
    kind: Literal["replay", "absent", "conflict"]
    receipt: SingleAdmissionReceipt | BatchImageReceipt | FrameQueuedReceipt | None = None


@dataclass(frozen=True, slots=True)
class UnknownCommit:
    attempt_id: str
    phase: str


@dataclass(frozen=True, slots=True)
class StreamTaskBinding:
    task_id: str
    message_id: str


@dataclass(frozen=True, slots=True)
class StagedIntent:
    """Intent proposal; this value does not authorize a provider request."""

    step_id: str
    attempt_id: str
    request_digest: str


@dataclass(frozen=True, slots=True)
class WebStartCapability:
    """Single-use in-process capability returned after a committed expose step."""

    attempt_id: str
    token: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class ProviderFailure:
    code: str
    retryable: bool
    safe_detail: str


@dataclass(frozen=True, slots=True)
class UnknownCreate:
    reason: str


@dataclass(frozen=True, slots=True)
class Failure:
    code: str
    safe_detail: str


@dataclass(frozen=True, slots=True)
class TextEvent:
    kind: Literal["text_content", "text_reasoning", "text_error", "text_end"]
    content: str = ""
    safe_error: str | None = None


@dataclass(frozen=True, slots=True)
class TextResult:
    text: str
    finish_reason: str | None = None


@dataclass(frozen=True, slots=True)
class MediaResult:
    media_kind: Literal["image", "video"]
    locator: str | None
    content_digest: str | None
    provider_reference: str | None = None


@dataclass(frozen=True, slots=True)
class BoundCredential:
    provider: str
    principal_id: str
    secret: str = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class ProviderContentCapability:
    provider: str
    origin: str
    allowed_paths: tuple[str, ...]
    credential_binding_ref: str


@dataclass(frozen=True, slots=True)
class PreparedReference:
    kind: Literal[
        "managed_url",
        "public_http",
        "data_uri",
        "bytes",
        "local_muse_path",
        "provider_content",
    ]
    locator: str | None
    content: bytes | None
    media_type: str | None


@dataclass(frozen=True, slots=True)
class StoredMedia:
    locator: str
    content_digest: str
    media_type: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class TransferFailure:
    code: str
    safe_detail: str


@dataclass(frozen=True, slots=True)
class GenerationActor:
    """Trusted identity plus a separately resolved new-submission permission."""

    user_id: str
    trusted_account_identity: TrustedActor
    new_admission_allowed: bool | None = None


@dataclass(frozen=True, slots=True)
class OperationPolicy:
    """Server-owned operation metadata; never selected by request fields."""

    operation: str
    task_type: str | None
    graph_version: str | None
    execution_mode: ExecutionMode
    charge_phase: ChargePhase
    cancellation_policy_version: CancellationPolicyVersion
    local_continuation_policy: str | None = None
    query_budget_policy: str | None = None
    stream_fee_policy: str | None = None


@dataclass(frozen=True, slots=True)
class RawAdmissionKey:
    """Exact client header value for the three existing media admission paths."""

    raw: str

    def __post_init__(self) -> None:
        if not self.raw or not self.raw.strip() or len(self.raw) > 255:
            raise ValueError("raw admission key must be nonblank and at most 255 characters")


@dataclass(frozen=True, slots=True)
class AdmissionFingerprint:
    schema_version: str
    ordered_nonsecret_payload_digest: str
    actor_id: str
    operation: str


@dataclass(frozen=True, slots=True)
class TargetSnapshot:
    kind: TargetKind
    chapter_id: str | None = None
    series_id: str | None = None
    stable_storyboard_asset_id: str | None = None
    asset_type: str | None = None
    asset_id: str | None = None
    compat_frame_index: int | None = None
    expected_text_digest: str | None = None
    media_identity: str | None = None
    content_digest: str | None = None
    notes_revision: int | None = None
    full_frame_digest: str | None = None
    full_chapter_digest: str | None = None
    expected_asset_image_url: str | None = None
    same_series_target_digest: str | None = None


@dataclass(frozen=True, slots=True)
class FrozenExecutionSnapshot:
    model_ref: ModelReference
    model_category: str
    model_name: str
    model_params: FrozenJSON
    prompt_ref: str | None
    effective_prompt: str
    effective_context: str
    server_quote: int
    reference_snapshot_ref: str
    target_snapshot: TargetSnapshot
    credential_binding_ref: str
    grant_snapshot: FrozenGrantSnapshot
    adapter_config_versions: tuple[tuple[str, str], ...]
    endpoint_policy: EndpointPolicy

    def __post_init__(self) -> None:
        if self.server_quote < 0:
            raise ValueError("server quote cannot be negative")
        object.__setattr__(self, "model_params", _freeze_json(self.model_params))


@dataclass(frozen=True, slots=True)
class SubmissionGraphDraft:
    submission_id: str
    internal_key: str
    operation: str
    graph_version: str
    tasks: tuple[TaskDraft, ...]
    units: tuple[BillingUnitDraft, ...]
    steps: tuple[ExecutionStepDraft, ...]
    messages: tuple[MessageDraft, ...]
    snapshot: FrozenExecutionSnapshot
    precharge_plan: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class SingleAdmissionReceipt:
    submission_id: str
    operation: Literal["image.single", "video.single"]
    task_id: str
    message_id: str | None
    status: TaskStatus
    quoted_amount: int
    billing_status: BillingStatus


@dataclass(frozen=True, slots=True)
class BatchImageTaskReceipt:
    task_type: str
    asset_id: str
    asset_name: str
    message_id: str
    task_id: str


@dataclass(frozen=True, slots=True)
class BatchImageReceipt:
    tasks: tuple[BatchImageTaskReceipt, ...]


@dataclass(frozen=True, slots=True)
class FrameQueuedReceipt:
    operation: Literal["frame.review", "frame.optimize"]
    task_id: str
    chapter_id: str
    frame_count: int
    model_name: str | None
    is_system: bool
    quoted_amount: int


@dataclass(frozen=True, slots=True)
class OwnerTaskScope:
    user_id: str
    task_id: str
    operation: str
    trusted_account_identity: TrustedActor | None = None


class CancellationSignal(Protocol):
    def is_set(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class ClaimAuthority:
    scope: OwnerTaskScope
    worker_instance: str
    token: str = field(repr=False)
    execution_generation: int
    lease_until: datetime
    local_lost_signal: CancellationSignal


@dataclass(frozen=True, slots=True)
class StreamFeeAuthority:
    actor: GenerationActor
    server_attempt_id: str
    operation: Literal["text.stream"]
    frozen_model_ref: ModelReference
    frozen_quote: int
    disposition: Literal["debit", "skipped", "byok", "zero"]


@dataclass(frozen=True, slots=True)
class StreamDebitReceipt:
    actor_id: str
    server_attempt_id: str
    operation: Literal["text.stream"]
    quoted_amount: int
    disposition: Literal["debit", "skipped", "byok", "zero"]
    actual_debited_amount: int
    original_debit_log_id: str | None
    original_business_key: str | None
    commit_state: Literal["staged", "committed", "unknown"]
    task_id: str | None = None
    message_id: str | None = None

    def __post_init__(self) -> None:
        if self.quoted_amount < 0 or self.actual_debited_amount < 0:
            raise ValueError("stream amounts cannot be negative")
        if self.actual_debited_amount == 0 and (
            self.original_debit_log_id is not None
            or self.original_business_key is not None
        ):
            raise ValueError("zero-debit receipts cannot claim a ledger linkage")


@dataclass(frozen=True, slots=True)
class StagedStreamDebitReceipt:
    receipt: StreamDebitReceipt

    def __post_init__(self) -> None:
        if self.receipt.commit_state != "staged":
            raise ValueError("staged stream receipts require a staged debit")


@dataclass(frozen=True, slots=True)
class CommittedStreamDebitReceipt:
    receipt: StreamDebitReceipt
    confirmed_at: datetime

    def __post_init__(self) -> None:
        if self.receipt.commit_state != "committed":
            raise ValueError("committed stream receipts require a committed debit")


@dataclass(frozen=True, slots=True)
class StreamStartPermit:
    server_attempt_id: str
    token: str = field(repr=False)
    state: Literal["start_reserved", "start_exposed", "revoked"]


@dataclass(frozen=True, slots=True)
class StagedStreamStartPermit:
    permit: StreamStartPermit

    def __post_init__(self) -> None:
        if self.permit.state != "start_reserved":
            raise ValueError("staged start permits must be reserved")


@dataclass(frozen=True, slots=True)
class CommittedStreamStartPermit:
    permit: StreamStartPermit
    confirmed_at: datetime

    def __post_init__(self) -> None:
        if self.permit.state != "start_reserved":
            raise ValueError("committed start permits must be reserved")


@dataclass(frozen=True, slots=True)
class StagedWebStartCapability:
    capability: WebStartCapability


@dataclass(frozen=True, slots=True)
class CommittedWebStartCapability:
    capability: WebStartCapability
    confirmed_at: datetime


@dataclass(frozen=True, slots=True)
class StreamSettlementProof:
    server_attempt_id: str
    kind: Literal["known_not_started", "source_policy_failure"]
    start_token: str = field(repr=False)
    observation_ref: str | None
    policy_version: str


@dataclass(frozen=True, slots=True)
class WebStreamAuthority:
    actor: GenerationActor
    fee_authority: StreamFeeAuthority
    confirmed_receipt: CommittedStreamDebitReceipt
    task_id: str | None
    message_id: str | None
    start_capability: CommittedWebStartCapability = field(repr=False)
    cancellation_signal: CancellationSignal = field(repr=False)


@dataclass(frozen=True, slots=True)
class CommittedIntent:
    """Adapter-issued value shape; not a provider capability by itself."""

    step_id: str
    attempt_id: str
    external_submission_id: str
    provider: str
    model_id: str
    request_digest: str
    snapshot_ref: str
    committed_at: datetime
    first_intent_at: datetime
    deadline_at: datetime
    provider_idempotency_key: str | None = None


@dataclass(frozen=True, slots=True)
class ExternalOperationRef:
    intent_id: str
    attempt_id: str
    provider: str
    external_id: str | None
    create_outcome: Literal["submitted", "sync_result", "unknown", "definitive_failure"]
    last_safe_provider_state: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderObservation:
    owner_scope: OwnerTaskScope
    intent_id: str
    attempt_id: str
    external_ref: ExternalOperationRef
    dedupe_key: str
    payload_digest: str
    received_at: datetime
    status: str
    media_locator: str | None = None
    result_text: str | None = None
    safe_error: str | None = None
    payload: FrozenJSON = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", _freeze_json(self.payload))


@dataclass(frozen=True, slots=True)
class ValidatedGenerationResult:
    result_ref: str
    observation_ref: str
    validation_version: str
    recoverable_locator: str
    content_digest: str
    validated_at: datetime
    generation_fulfilled_at: datetime
    timely_success: bool
    selected_attempt_id: str
    safe_output: MediaResult | TextResult
    automatic_application_allowed: bool


@dataclass(frozen=True, slots=True)
class CanvasApplyAuthority:
    actor: GenerationActor
    task_id: str
    result_ref: str
    operation: str
    target: TargetSnapshot
    result_digest: str


@dataclass(frozen=True, slots=True)
class ApplicationOutcome:
    kind: ApplicationKind
    result_ref: str
    application_ref: str | None
    target_revision: str | None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class BillingOutcome:
    kind: BillingKind
    unit_id: str | None
    attempt_id: str | None
    actor_id: str
    operation: str
    related_debit_ref: str | None
    exact_amount: int


@dataclass(frozen=True, slots=True)
class RecoveryPlan:
    kind: RecoveryKind
    owner_scope: OwnerTaskScope | None
    step_id: str | None = None
    submission_id: str | None = None
    work_id: str | None = None
    phase: WorkPhase | None = None
    result_ref: str | None = None
    fee_decision_ref: str | None = None
    application_ref: str | None = None
    policy_version: str | None = None
    stream_fee_authority: StreamFeeAuthority | None = None


@dataclass(frozen=True, slots=True)
class ReferenceInput:
    kind: Literal[
        "managed_url", "public_http", "data_uri", "bytes",
        "local_muse_path", "provider_content",
    ]
    locator: str | None = None
    content: bytes | None = field(default=None, repr=False)
    media_type: str | None = None
    provider_operation_ref: ExternalOperationRef | None = None


@dataclass(frozen=True, slots=True)
class GenerationApplicationCommand:
    task_id: str
    result_ref: str
    expected_target: TargetSnapshot
    application_key: str | None = None


@dataclass(frozen=True, slots=True)
class WebStreamCommand:
    """Frozen server input for the legacy web-stream bridge."""

    snapshot: FrozenExecutionSnapshot


@dataclass(frozen=True, slots=True)
class ShutdownReceipt:
    """Operational cleanup evidence; it carries no business settlement power."""

    reason: str
    released_resources: tuple[str, ...]
    unreleased_resources: tuple[str, ...]
    revoked_claim_refs: tuple[str, ...]
    recovery_plan_refs: tuple[str, ...]
    timed_out: bool


@dataclass(frozen=True, slots=True)
class CredentialBinding:
    source: str
    row_id: str
    owner_or_system: str
    provider: str
    original_principal_id: str
    endpoint_origin: str
    allowed_content_paths: tuple[str, ...]
    binding_version: str
    rotation_provenance: str | None = None


@dataclass(frozen=True, slots=True)
class ContinuationClaimAuthority:
    owner_scope: OwnerTaskScope
    unit_id: str
    work_id: str
    result_ref: str
    phase_version: int
    worker_instance: str
    token: str = field(repr=False)
    execution_generation: int
    lease_until: datetime


@dataclass(frozen=True, slots=True)
class ControlSettlementAuthority:
    owner_scope: OwnerTaskScope
    policy_stop_event_refs: tuple[str, ...]
    revoked_execution_generation: int | None
    quiescent: bool
    purpose: Literal["cancel", "due", "policy_stop", "terminal_projection"]


@dataclass(frozen=True, slots=True)
class ControlledQueryAuthority:
    """Narrow authority for original poll reservation and observation accounting."""

    owner_scope: OwnerTaskScope
    group_key: str
    active_step_id: str
    active_submission_id: str
    external_id: str
    registry_version: str
    binding_ref: str
    remaining_poll_budget: int


@dataclass(frozen=True, slots=True)
class ControlledRecoveryScope:
    """Typed scope for validating a linked result against its attempt group."""

    owner_scope: OwnerTaskScope
    group_key: str
    registry_version: str


@dataclass(frozen=True, slots=True)
class PolicyStopFinancialDisposition:
    event_id: str
    owner_id: str
    task_id: str
    unit_id: str
    group_key: str
    registry_version: str
    state: Literal[
        "pending", "refunded", "retained_by_started_cancel", "no_debit"
    ]
    original_debit_id: str | None
    original_business_key: str | None
    original_amount: int
    refund_id: str | None = None
    refund_amount: int = 0
    started_cancel_marker_ref: str | None = None


# Frozen contract vector. It is intentionally separate from helper value types.
PUBLIC_TYPE_NAMES: tuple[str, ...] = (
    "GenerationActor",
    "OperationPolicy",
    "RawAdmissionKey",
    "AdmissionFingerprint",
    "FrozenExecutionSnapshot",
    "TargetSnapshot",
    "SubmissionGraphDraft",
    "SingleAdmissionReceipt",
    "BatchImageReceipt",
    "FrameQueuedReceipt",
    "ClaimAuthority",
    "WebStreamAuthority",
    "OwnerTaskScope",
    "CommittedIntent",
    "ExternalOperationRef",
    "ProviderObservation",
    "ValidatedGenerationResult",
    "CanvasApplyAuthority",
    "ApplicationOutcome",
    "BillingOutcome",
    "RecoveryPlan",
    "ReferenceInput",
    "GenerationRuntimeConfig",
    "UnitFeeDecision",
    "GenerationResultReceipt",
    "ExecutionAttemptGroup",
    "GenerationApplicationCommand",
    "ApplicationRecord",
    "CredentialBinding",
    "LocalContinuationWork",
    "ContinuationClaimAuthority",
    "StreamFeeAuthority",
    "StreamDebitReceipt",
    "StreamStartPermit",
    "StreamSettlementProof",
    "PollReservation",
    "PolicyStopEvent",
    "ControlSettlementAuthority",
    "ControlledQueryAuthority",
    "PolicyStopFinancialDisposition",
)

__all__ = tuple(
    name for name in PUBLIC_TYPE_NAMES if name in globals()
) + (
    "PUBLIC_TYPE_NAMES",
    "GenerationRuntimeConfig",
    "FrozenJSON",
    "JSONValue",
    "StagedStreamDebitReceipt",
    "CommittedStreamDebitReceipt",
    "StagedWebStartCapability",
    "StagedStreamStartPermit",
    "CommittedStreamStartPermit",
    "CommittedWebStartCapability",
    "ShutdownReceipt",
    "TextEvent",
    "WebStreamCommand",
    "FrozenGrantSnapshot",
    "EndpointPolicy",
    "ModelReference",
    "TaskDraft",
    "BillingUnitDraft",
    "ExecutionStepDraft",
    "MessageDraft",
    "BatchImageTaskReceipt",
    "CancellationSignal",
    "WebStartCapability",
    "ControlledRecoveryScope",
    "AdmissionCommand",
    "GenerationItem",
    "ReceiptLookup",
    "UnknownCommit",
    "StagedIntent",
    "StreamTaskBinding",
    "ProviderFailure",
    "UnknownCreate",
    "Failure",
    "TextResult",
    "MediaResult",
    "BoundCredential",
    "ProviderContentCapability",
    "PreparedReference",
    "StoredMedia",
    "TransferFailure",
)


def __getattr__(name: str) -> type[object]:
    if name == "GenerationRuntimeConfig":
        from .ports import GenerationRuntimeConfig

        return GenerationRuntimeConfig
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
