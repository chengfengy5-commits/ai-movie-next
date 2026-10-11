"""Canonical immutable public shapes for the eight generation records.

These are persistence-facing value contracts only. Their construction does not
prove that any row, ledger entry, provider result, or transaction was committed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from .types import ChargePhase, TargetSnapshot, UnitFeeResolution, WorkPhase


@dataclass(frozen=True, slots=True)
class UnitFeeDecision:
    unit_id: str
    charge_phase: ChargePhase
    resolution: UnitFeeResolution
    ledger_ref: str | None
    generation_result_ref: str | None
    application_ref: str | None
    resolved_at: datetime | None


@dataclass(frozen=True, slots=True)
class GenerationResultReceipt:
    result_ref: str
    owner_id: str
    task_id: str
    unit_id: str
    operation: str
    step_id: str
    submission_id: str
    evidence_id: str
    selected_attempt_id: str
    recoverable_locator: str
    content_digest: str
    validation_version: str
    validated_at: datetime
    generation_fulfilled_at: datetime
    late_disposition: str | None
    policy_stop_disposition: str | None
    automatic_application_allowed: bool


@dataclass(frozen=True, slots=True)
class ApplicationRecord:
    application_ref: str
    result_ref: str
    owner_id: str
    task_id: str
    unit_id: str
    target_scope: str
    result_digest: str
    delivery_status: Literal[
        "pending", "applied", "target_conflict", "target_deleted", "failed"
    ]
    before_target_digest: str | None
    after_target_digest: str | None
    media_revision_before: int | None
    media_revision_after: int | None
    notes_revision_before: int | None
    notes_revision_after: int | None
    committed_at: datetime | None


@dataclass(frozen=True, slots=True)
class ExecutionAttemptGroup:
    unit_id: str
    group_key: str
    registry_version: str
    attempt_step_ids: tuple[str, str, str]
    original_payload_digest: str
    max_retries: int
    active_attempt: int
    selected_evidence_id: str | None
    status: Literal[
        "prepared", "active", "pending_verification", "succeeded", "failed",
        "cancelled", "policy_stopped", "succeeded_late",
    ]
    poll_used: int
    auth_unavailable_streak: int
    terminal_reason: str | None
    poll_sequence: int
    next_poll_at: datetime | None
    outstanding_reservation_ref: str | None
    sticky_policy_stop_event_ref: str | None
    active_eligibility_version: str


@dataclass(frozen=True, slots=True)
class LocalContinuationWork:
    work_id: str
    owner_id: str
    task_id: str
    unit_id: str
    operation: str
    policy_version: str
    result_ref: str
    content_digest: str
    generation_fulfilled_at: datetime
    original_first_intent_at: datetime
    original_deadline_at: datetime
    phase_order: tuple[WorkPhase, ...]
    phase: WorkPhase
    phase_version: int
    fee_decision_ref: str | None
    application_ref: str | None
    terminal_reason: str | None
    last_execution_generation: int | None


@dataclass(frozen=True, slots=True)
class StreamFeeRecord:
    server_attempt_id: str
    owner_id: str
    operation: Literal["text.stream"]
    frozen_model_provenance: str
    quoted_amount: int
    disposition: Literal["debit", "skipped", "byok", "zero"]
    actual_debited_amount: int
    original_debit_log_id: str | None
    original_business_key: str | None
    settlement_state: Literal[
        "committed", "success_retained", "settlement_pending",
        "refunded", "no_debit_terminal",
    ]
    start_state: Literal[
        "ready_to_start", "start_reserved", "start_exposed", "revoked"
    ]
    start_token: str | None
    task_id: str | None
    message_id: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PollReservation:
    reservation_id: str
    group_key: str
    sequence: int
    attempt_id: str
    step_id: str
    submission_id: str
    external_id: str
    payload_digest: str
    committed_poll_count: int
    reserved_at: datetime
    timeout_at: datetime
    next_poll_at: datetime
    token: str
    state: Literal["reserved", "accepted", "closed_unknown"]
    observation_ref: str | None = None


@dataclass(frozen=True, slots=True)
class PolicyStopEvent:
    event_id: str
    group_key: str
    policy_version: str
    reason: Literal["auth_unavailable_12"]
    reservation_refs: tuple[str, ...]
    observation_refs: tuple[str, ...]
    active_step_id: str
    active_submission_id: str
    external_id: str
    stopped_at: datetime
    financial_disposition: Literal[
        "pending", "refunded", "retained_by_started_cancel", "no_debit"
    ]
    original_debit_ref: str | None
    refund_ref: str | None
    amount: int
    owner_id: str
    task_id: str
    unit_id: str


PUBLIC_RECORD_NAMES: tuple[str, ...] = (
    "UnitFeeDecision",
    "GenerationResultReceipt",
    "ApplicationRecord",
    "ExecutionAttemptGroup",
    "LocalContinuationWork",
    "StreamFeeRecord",
    "PollReservation",
    "PolicyStopEvent",
)

__all__ = PUBLIC_RECORD_NAMES + ("PUBLIC_RECORD_NAMES",)
