from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime
from typing import get_type_hints

import pytest

from haoai_backend.generation_contracts import (
    PUBLIC_TYPE_NAMES,
    ApplicationRecord,
    AdmissionFingerprint,
    CommittedIntent,
    CommittedPollReservation,
    CommittedStreamDebitReceipt,
    CommittedStreamStartPermit,
    ControlledQueryAuthority,
    ControlledRecoveryScope,
    GenerationRuntimeConfig,
    ModelReference,
    RuntimeConcurrencySettings,
    RuntimeTimeoutSettings,
    StagedPollReservation,
    StagedStreamDebitReceipt,
    StagedStreamStartPermit,
    CredentialBinding,
    GenerationActor,
    GenerationError,
    GenerationResultReceipt,
    LifecycleUnitOfWork,
    OwnerTaskScope,
    PollReservation,
    ProviderObservation,
    ProviderTransport,
    ExternalOperationRef,
    OperationPolicy,
    PUBLIC_RECORD_NAMES,
    StreamFeeRecord,
    PolicyStopFinancialDisposition,
    ReceiptLookup,
    ShutdownReceipt,
    TextEvent,
    WebStreamCommand,
    RawAdmissionKey,
    StreamDebitReceipt,
    StreamFeeAuthority,
    StreamSettlementProof,
    TargetSnapshot,
    WebStreamAuthority,
)
from generation_core.fakes import fake_generation_ports
from haoai_backend.shared.identity import TrustedActor


def test_frozen_public_type_vector_has_exactly_forty_names() -> None:
    import haoai_backend.generation_contracts as contracts

    assert len(PUBLIC_TYPE_NAMES) == 40
    assert len(set(PUBLIC_TYPE_NAMES)) == 40
    assert all(hasattr(contracts, name) for name in PUBLIC_TYPE_NAMES)
    supplemental_names = (
        "InertGenerationRuntime",
        "GenerationExecutor",
        "WorkerExecutionOutcome",
        "ShutdownReceipt",
        "TextEvent",
        "WebStreamCommand",
        "CallerTransaction",
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
        "FrozenJSON",
        "JSONValue",
    )
    assert all(hasattr(contracts, name) for name in supplemental_names)
    capability_names = (
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
        "AsyncHttpClient",
        "PublicMediaFetcher",
        "BoundProviderContentFetcher",
        "LocalMuseCapability",
        "R2StorageCapability",
        "RuntimeTimeoutSettings",
        "RuntimeConcurrencySettings",
        "StagedStreamStartPermit",
        "CommittedStreamStartPermit",
        "CommittedWebStartCapability",
        "StagedPollReservation",
        "CommittedPollReservation",
    )
    assert all(getattr(contracts, name).__name__ == name for name in capability_names)
    assert not set(capability_names) & set(PUBLIC_TYPE_NAMES)
    assert not set(supplemental_names) & set(PUBLIC_TYPE_NAMES)


def test_raw_admission_key_preserves_the_original_nonblank_value() -> None:
    value = "  original/key  "
    assert RawAdmissionKey(value).raw == value

    with pytest.raises(ValueError):
        RawAdmissionKey("  ")
    with pytest.raises(ValueError):
        RawAdmissionKey("x" * 256)


def test_authority_and_result_shapes_remain_separate_and_immutable() -> None:
    actor = GenerationActor(
        user_id="user-1",
        trusted_account_identity=TrustedActor("user-1"),
        new_admission_allowed=None,
    )
    assert actor.new_admission_allowed is None

    intent = CommittedIntent(
        step_id="step-1",
        attempt_id="attempt-1",
        external_submission_id="submission-1",
        provider="provider-a",
        model_id="model-a",
        request_digest="digest",
        snapshot_ref="snapshot-1",
        committed_at=datetime.now(UTC),
        first_intent_at=datetime.now(UTC),
        deadline_at=datetime.now(UTC),
    )
    assert intent.attempt_id == "attempt-1"
    with pytest.raises(FrozenInstanceError):
        intent.provider = "provider-b"  # type: ignore[misc]

    staged = StreamDebitReceipt(
        actor_id="user-1",
        server_attempt_id="attempt-1",
        operation="text.stream",
        quoted_amount=10,
        disposition="debit",
        actual_debited_amount=0,
        original_debit_log_id=None,
        original_business_key=None,
        commit_state="staged",
    )
    confirmed = StreamDebitReceipt(
        actor_id="user-1",
        server_attempt_id="attempt-1",
        operation="text.stream",
        quoted_amount=10,
        disposition="debit",
        actual_debited_amount=10,
        original_debit_log_id="ledger-1",
        original_business_key="stream:debit:attempt-1",
        commit_state="committed",
    )
    assert staged.commit_state == "staged"
    assert confirmed.commit_state == "committed"
    assert staged.original_debit_log_id is None
    assert confirmed.original_debit_log_id is not None


def test_supplemental_shapes_do_not_claim_financial_or_application_evidence() -> None:
    assert "GenerationError" not in PUBLIC_TYPE_NAMES
    error = GenerationError("admission_commit_unknown", "commit result pending")
    assert error.code == "admission_commit_unknown"
    assert not hasattr(error, "status_code")

    disposition = PolicyStopFinancialDisposition(
        event_id="event-1",
        owner_id="user-1",
        task_id="task-1",
        unit_id="unit-1",
        group_key="group-1",
        registry_version="video-haoai-attempt-group-v1",
        state="pending",
        original_debit_id=None,
        original_business_key=None,
        original_amount=0,
    )
    with pytest.raises(FrozenInstanceError):
        disposition.state = "refunded"  # type: ignore[misc]

    assert "operation" in {field.name for field in fields(OperationPolicy)}
    assert "expected_asset_image_url" in {
        field.name for field in fields(TargetSnapshot)
    }
    assert "committed_at" in {field.name for field in fields(ApplicationRecord)}
    assert "application_ref" not in {
        field.name for field in fields(GenerationResultReceipt)
    }


def test_canonical_record_vector_includes_stream_fee_shape() -> None:
    assert PUBLIC_RECORD_NAMES == (
        "UnitFeeDecision",
        "GenerationResultReceipt",
        "ApplicationRecord",
        "ExecutionAttemptGroup",
        "LocalContinuationWork",
        "StreamFeeRecord",
        "PollReservation",
        "PolicyStopEvent",
    )
    assert "settlement_state" in {field.name for field in fields(StreamFeeRecord)}


def test_supplemental_runtime_shapes_keep_legacy_boundaries() -> None:
    event = TextEvent(kind="text_error", safe_error="provider unavailable")
    assert event.kind == "text_error"
    assert [field.name for field in fields(WebStreamCommand)] == ["snapshot"]

    receipt = ShutdownReceipt(
        reason="worker-stop",
        released_resources=("http-client",),
        unreleased_resources=(),
        revoked_claim_refs=("claim-1",),
        recovery_plan_refs=("plan-1",),
        timed_out=False,
    )
    assert receipt.revoked_claim_refs == ("claim-1",)
    assert "refund" not in {field.name for field in fields(ShutdownReceipt)}
    with pytest.raises(FrozenInstanceError):
        receipt.timed_out = True  # type: ignore[misc]


def test_autospecced_fakes_require_explicit_domain_outcomes() -> None:
    ports = fake_generation_ports()
    lookup = ReceiptLookup(kind="absent")
    fingerprint = AdmissionFingerprint(
        schema_version="admission-v1",
        ordered_nonsecret_payload_digest="digest-1",
        actor_id="user-1",
        operation="image.single",
    )
    binding = CredentialBinding(
        source="owner",
        row_id="credential-1",
        owner_or_system="user-1",
        provider="provider-a",
        original_principal_id="principal-1",
        endpoint_origin="https://provider.invalid",
        allowed_content_paths=(),
        binding_version="binding-v1",
    )
    ports.admission.find_original_receipt.return_value = lookup
    assert not isinstance(ports.provider.resolve_original_credential.return_value, str)
    ports.provider.resolve_original_credential.return_value = "unavailable"

    assert ports.admission.find_original_receipt("image.single", None, fingerprint) is lookup
    assert ports.provider.resolve_original_credential(binding, "create") == "unavailable"
    with pytest.raises(AttributeError):
        ports.provider.generate_sync_images  # type: ignore[attr-defined]



def test_runtime_config_has_explicit_typed_factories_and_frozen_limits() -> None:
    hints = get_type_hints(GenerationRuntimeConfig)
    assert hints["session_factory"] != object
    assert hints["provider_factories"] != object
    assert hints["media_store_factory"] != object
    assert hints["public_media_fetcher_factory"] != object
    assert hints["bound_provider_content_fetcher_factory"] != object
    assert hints["local_muse_capability_factory"] != object
    assert hints["r2_storage_factory"] != object
    assert "object" not in repr(hints)
    assert GenerationRuntimeConfig.__dataclass_fields__["shutdown_timeout_seconds"].default == 10.0
    assert RuntimeTimeoutSettings().public_media_fetch_seconds == 600
    assert RuntimeConcurrencySettings().public_media_fetches == 2
    with pytest.raises(ValueError):
        RuntimeTimeoutSettings(public_media_fetch_seconds=599)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        RuntimeConcurrencySettings(public_media_fetches=1)  # type: ignore[arg-type]


def test_public_dto_helpers_are_real_exported_types() -> None:
    import haoai_backend.generation_contracts as contracts
    from haoai_backend.generation_contracts.types import GenerationRuntimeConfig as TypesConfig

    assert TypesConfig is GenerationRuntimeConfig
    helpers = (
        "ModelReference",
        "FrozenGrantSnapshot",
        "EndpointPolicy",
        "TaskDraft",
        "BillingUnitDraft",
        "ExecutionStepDraft",
        "MessageDraft",
        "BatchImageTaskReceipt",
        "CancellationSignal",
    )
    for name in helpers:
        helper_type = getattr(contracts, name)
        assert helper_type.__name__ == name
        assert not helper_type.__name__.startswith("_")

    snapshot_hints = get_type_hints(contracts.FrozenExecutionSnapshot)
    assert snapshot_hints["model_ref"] is ModelReference
    assert snapshot_hints["grant_snapshot"].__name__ == "FrozenGrantSnapshot"


def test_staged_values_cannot_enter_confirmed_stream_or_poll_shapes() -> None:
    import haoai_backend.generation_contracts as contracts

    now = datetime.now(UTC)
    staged_receipt = StreamDebitReceipt(
        actor_id="user-1",
        server_attempt_id="attempt-1",
        operation="text.stream",
        quoted_amount=5,
        disposition="debit",
        actual_debited_amount=0,
        original_debit_log_id=None,
        original_business_key=None,
        commit_state="staged",
    )
    with pytest.raises(ValueError):
        CommittedStreamDebitReceipt(staged_receipt, now)
    StagedStreamDebitReceipt(staged_receipt)
    for state in ("committed", "unknown"):
        not_staged = StreamDebitReceipt(
            actor_id="user-1",
            server_attempt_id="attempt-1",
            operation="text.stream",
            quoted_amount=5,
            disposition="debit",
            actual_debited_amount=0,
            original_debit_log_id=None,
            original_business_key=None,
            commit_state=state,
        )
        with pytest.raises(ValueError):
            StagedStreamDebitReceipt(not_staged)

    reserved_permit = contracts.StreamStartPermit(
        server_attempt_id="attempt-1", token="token", state="start_reserved"
    )
    StagedStreamStartPermit(reserved_permit)
    CommittedStreamStartPermit(reserved_permit, now)
    revoked_permit = contracts.StreamStartPermit(
        server_attempt_id="attempt-1", token="token", state="revoked"
    )
    with pytest.raises(ValueError):
        StagedStreamStartPermit(revoked_permit)
    with pytest.raises(ValueError):
        CommittedStreamStartPermit(revoked_permit, now)
    exposed_permit = contracts.StreamStartPermit(
        server_attempt_id="attempt-1", token="token", state="start_exposed"
    )
    with pytest.raises(ValueError):
        CommittedStreamStartPermit(exposed_permit, now)

    reservation = PollReservation(
        reservation_id="poll-1",
        group_key="group-1",
        sequence=1,
        attempt_id="attempt-1",
        step_id="step-1",
        submission_id="submission-1",
        external_id="external-1",
        payload_digest="digest-1",
        committed_poll_count=1,
        reserved_at=now,
        timeout_at=now,
        next_poll_at=now,
        token="token",
        state="reserved",
    )
    staged_poll = StagedPollReservation(reservation)
    committed_poll = CommittedPollReservation(reservation, now)
    assert staged_poll.reservation is reservation
    assert committed_poll.reservation is reservation
    accepted_reservation = PollReservation(
        **{
            **{item.name: getattr(reservation, item.name) for item in fields(reservation)},
            "state": "accepted",
            "observation_ref": "observation-1",
        }
    )
    with pytest.raises(ValueError):
        CommittedPollReservation(accepted_reservation, now)

    acquire_hints = get_type_hints(LifecycleUnitOfWork.acquire_stream_start)
    expose_hints = get_type_hints(LifecycleUnitOfWork.expose_stream_start)
    reserve_hints = get_type_hints(LifecycleUnitOfWork.reserve_active_poll)
    query_hints = get_type_hints(ProviderTransport.query_existing)
    assert acquire_hints["return"].__name__ == "StagedStreamStartPermit"
    assert expose_hints["committed_permit"] is CommittedStreamStartPermit
    assert reserve_hints["return"].__args__[0].__name__ == "StagedPollReservation"
    assert query_hints["reservation"].__args__[0] is CommittedPollReservation


def test_provider_observation_deep_freezes_caller_owned_payload() -> None:
    now = datetime.now(UTC)
    payload = {"metadata": {"labels": ["original"]}}
    observation = ProviderObservation(
        owner_scope=OwnerTaskScope("user-1", "task-1", "video.single"),
        intent_id="intent-1",
        attempt_id="attempt-1",
        external_ref=ExternalOperationRef(
            intent_id="intent-1",
            attempt_id="attempt-1",
            provider="provider-a",
            external_id="external-1",
            create_outcome="submitted",
        ),
        dedupe_key="dedupe-1",
        payload_digest="digest-1",
        received_at=now,
        status="running",
        payload=payload,
    )

    payload["metadata"]["labels"][0] = "mutated"
    payload["metadata"]["labels"].append("later")
    payload["metadata"]["extra"] = "caller mutation"

    metadata = observation.payload["metadata"]
    assert metadata["labels"] == ("original",)
    assert "extra" not in metadata
    assert observation.payload_digest == "digest-1"
    assert observation.dedupe_key == "dedupe-1"
    with pytest.raises(TypeError):
        metadata["extra"] = "not frozen"  # type: ignore[index]


def test_controlled_group_recovery_scope_is_not_query_authority() -> None:
    hints = get_type_hints(LifecycleUnitOfWork.complete_attempt_group)
    assert hints["controlled_recovery"] is ControlledRecoveryScope
    assert hints["controlled_recovery"] is not ControlledQueryAuthority
    assert {field.name for field in fields(ControlledRecoveryScope)} == {
        "owner_scope",
        "group_key",
        "registry_version",
    }
    assert "external_id" in {field.name for field in fields(ControlledQueryAuthority)}
