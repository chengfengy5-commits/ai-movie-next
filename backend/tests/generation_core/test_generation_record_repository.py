import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from sqlalchemy import create_engine, event, func, select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.records import (
    ApplicationRecord,
    ExecutionAttemptGroup,
    GenerationResultReceipt,
    LocalContinuationWork,
    PollReservation,
    StreamFeeRecord,
    UnitFeeDecision,
)
from haoai_backend.generation_contracts.canvas_targets import (
    canonical_target_snapshot_digest,
    target_snapshot_mapping,
)
from haoai_backend.generation_contracts.types import TargetSnapshot
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
    ResultEvidenceRow,
    UserRow,
)
from haoai_backend.generation_core.persistence import GenerationRecordRepository
from haoai_backend.generation_core.schema import (
    ApplicationRecordRow,
    FoundationBase,
    GenerationAuxiliaryBase,
    GenerationResultReceiptRow,
    GenerationResultTargetRow,
    UnitFeeDecisionRow,
)


def _id() -> str:
    return str(uuid4())


def _seed_session() -> tuple[Session, dict[str, object]]:
    engine = create_engine("sqlite://")
    FoundationBase.metadata.create_all(engine)
    session = Session(engine)
    now = datetime.now(timezone.utc)
    owner_id, task_id, unit_id = _id(), _id(), _id()
    step_ids = tuple(_id() for _ in range(3))
    submission_ids = tuple(_id() for _ in range(3))
    external_ids = ("remote-0", "remote-1", "remote-2")
    digest = "payload-digest"

    session.add_all(
        [
            UserRow(id=owner_id),
            AITaskRow(
                id=task_id,
                user_id=owner_id,
                type="video-single",
                status="processing",
                billing_status="unbilled",
                message_id=_id(),
                created_at=now.replace(tzinfo=None),
                updated_at=now.replace(tzinfo=None),
            ),
            BillingUnitRow(
                id=unit_id,
                task_id=task_id,
                item_key="video-0",
                operation="video.single",
                step_graph_version="video-single-v1",
                input_digest=digest,
                quoted_amount=10,
                first_intent_at=now,
                deadline_at=now + timedelta(hours=2),
            ),
        ]
    )
    session.flush()
    for index, step_id in enumerate(step_ids):
        session.add(
            ExecutionStepRow(
                id=step_id,
                billing_unit_id=unit_id,
                step_key=f"generate.attempt{index}",
                dependencies=[],
                input_digest=digest,
                required=False,
                step_order=index,
                execution_status="submitted",
            )
        )
        session.add(
            ExternalSubmissionRow(
                id=submission_ids[index],
                step_id=step_id,
                submission_key=f"submission-{index}",
                provider="haoai",
                request_digest=digest,
                request_snapshot={},
                intent_at=now,
                external_task_id=external_ids[index],
                submission_status="submitted",
                deadline_at=now + timedelta(hours=2),
            )
        )
    session.flush()
    evidence_id = _id()
    session.add(
        ResultEvidenceRow(
            id=evidence_id,
            step_id=step_ids[0],
            submission_id=submission_ids[0],
            evidence_key="evidence-0",
            evidence_kind="provider_result",
            payload_digest=digest,
            result_location="managed://result/0",
            result_digest="content-digest",
            received_at=now,
            validity="valid",
            application_status="pending",
        )
    )
    session.flush()
    return session, {
        "owner_id": owner_id,
        "task_id": task_id,
        "unit_id": unit_id,
        "step_ids": step_ids,
        "submission_ids": submission_ids,
        "external_ids": external_ids,
        "digest": digest,
        "evidence_id": evidence_id,
        "now": now,
    }


def _receipt_for_seed(seeded: dict[str, object], *, operation: str = "video.single"):
    now = seeded["now"]
    return GenerationResultReceipt(
        result_ref=_id(),
        owner_id=seeded["owner_id"],
        task_id=seeded["task_id"],
        unit_id=seeded["unit_id"],
        operation=operation,
        step_id=seeded["step_ids"][0],
        submission_id=seeded["submission_ids"][0],
        evidence_id=seeded["evidence_id"],
        selected_attempt_id="attempt-0",
        recoverable_locator="managed://result/0",
        content_digest="content-digest",
        validation_version="validation-v1",
        validated_at=now,
        generation_fulfilled_at=now,
        late_disposition=None,
        policy_stop_disposition=None,
        automatic_application_allowed=False,
    )


def _canvas_receipt_for_seed(
    session: Session,
    seeded: dict[str, object],
    *,
    submission_target: TargetSnapshot | None = None,
) -> tuple[TargetSnapshot, GenerationResultReceipt]:
    target = TargetSnapshot(
        kind="canvas_asset_image",
        chapter_id="chapter-canvas",
        series_id="series-canvas",
        asset_type="character",
        asset_id="character-canvas",
        same_series_target_digest="a" * 64,
    )
    payload = target_snapshot_mapping(target)
    task = session.get(AITaskRow, seeded["task_id"])
    unit = session.get(BillingUnitRow, seeded["unit_id"])
    submission = session.get(ExternalSubmissionRow, seeded["submission_ids"][0])
    assert task is not None and unit is not None and submission is not None
    task.type = "image-single"
    task.request_data = json.dumps({"target_snapshot": payload}, sort_keys=True)
    unit.operation = "image.single"
    submission.request_snapshot = {
        "target_snapshot": target_snapshot_mapping(submission_target or target)
    }
    receipt = _receipt_for_seed(seeded, operation="image.single")
    return target, receipt


def _continuation_for_seed(
    seeded: dict[str, object],
    result: GenerationResultReceipt,
    *,
    policy_version: str = "policy-v1",
    operation: str = "frame.review",
) -> LocalContinuationWork:
    now = seeded["now"]
    phase_order = {
        "frame.review": ("delivery_pending", "fee_pending", "finalize_pending"),
        "frame.optimize": ("fee_pending", "delivery_pending", "finalize_pending"),
    }[operation]
    return LocalContinuationWork(
        _id(),
        seeded["owner_id"],
        seeded["task_id"],
        seeded["unit_id"],
        operation,
        policy_version,
        result.result_ref,
        result.content_digest,
        now.replace(tzinfo=None),
        now.replace(tzinfo=None),
        (now + timedelta(hours=2)).replace(tzinfo=None),
        phase_order,
        phase_order[0],
        0,
        None,
        None,
        None,
        None,
    )


def _advance_to_final_phase(
    repository: GenerationRecordRepository,
    work: LocalContinuationWork,
    *,
    fee_decision_ref: str | None = None,
    application_ref: str | None = None,
) -> LocalContinuationWork:
    next_phase, final_phase_name = work.phase_order[1:]
    advanced = repository.advance_continuation_work(
        replace(
            work,
            phase=next_phase,
            phase_version=1,
            fee_decision_ref=fee_decision_ref,
            application_ref=application_ref,
        ),
        expected_phase_version=0,
    )
    assert advanced.status == "updated"
    assert repository.stage_record(advanced.record).status == "same"
    final_phase = repository.advance_continuation_work(
        replace(advanced.record, phase=final_phase_name, phase_version=2),
        expected_phase_version=1,
    )
    assert final_phase.status == "updated"
    assert repository.stage_record(final_phase.record).status == "same"
    return final_phase.record


def test_repository_stages_valid_receipt_and_detects_natural_key_conflict() -> None:
    session, seeded = _seed_session()
    repository = GenerationRecordRepository(session)
    receipt = _receipt_for_seed(seeded)

    created = repository.stage_record(receipt)
    replay = repository.stage_record(receipt)
    conflict = repository.stage_record(
        replace(receipt, content_digest="other-digest")
    )

    assert created.status == "created"
    assert replay.status == "same"
    assert conflict.status == "conflict"
    assert repository.find_result_receipt(
        seeded["owner_id"], seeded["task_id"], receipt.result_ref
    ) == receipt
    assert session.in_transaction()
    session.rollback()
    session.close()


def test_new_canvas_receipt_stages_one_projection_and_replay_does_not_repair() -> None:
    session, seeded = _seed_session()
    GenerationAuxiliaryBase.metadata.create_all(session.get_bind())
    target, receipt = _canvas_receipt_for_seed(session, seeded)
    repository = GenerationRecordRepository(session)

    created = repository.stage_record(receipt)
    projection = session.get(GenerationResultTargetRow, receipt.result_ref)
    replay = repository.stage_record(receipt)
    projection_count = session.execute(
        select(func.count()).select_from(GenerationResultTargetRow)
    ).scalar_one()
    assert projection is not None
    assert created.status == "created"
    assert projection.target_kind == "canvas_asset_image"
    assert projection.target_identity == "asset|character|character-canvas"
    assert projection.target_snapshot_digest == canonical_target_snapshot_digest(target)
    assert replay.status == "same"
    assert projection_count == 1

    session.delete(projection)
    session.flush()
    assert repository.stage_record(receipt).status == "same"
    assert session.get(GenerationResultTargetRow, receipt.result_ref) is None
    session.rollback()
    session.close()


def test_canvas_receipt_with_disagreeing_original_targets_is_not_staged() -> None:
    session, seeded = _seed_session()
    GenerationAuxiliaryBase.metadata.create_all(session.get_bind())
    _target, receipt = _canvas_receipt_for_seed(
        session,
        seeded,
        submission_target=TargetSnapshot(
            kind="canvas_asset_image",
            chapter_id="chapter-canvas",
            series_id="series-canvas",
            asset_type="character",
            asset_id="different-character",
            same_series_target_digest="a" * 64,
        ),
    )
    result = GenerationRecordRepository(session).stage_record(receipt)

    assert result.status == "invalid_binding"
    assert session.get(GenerationResultReceiptRow, receipt.result_ref) is None
    assert session.execute(select(GenerationResultTargetRow)).scalars().all() == []
    session.rollback()
    session.close()


def test_projection_flush_failure_leaves_no_committed_receipt_after_rollback() -> None:
    session, seeded = _seed_session()
    GenerationAuxiliaryBase.metadata.create_all(session.get_bind())
    _target, receipt = _canvas_receipt_for_seed(session, seeded)

    def fail_projection_flush(current_session, _flush_context, _instances) -> None:
        if any(
            isinstance(row, GenerationResultTargetRow)
            for row in current_session.new
        ):
            raise RuntimeError("synthetic projection insert failure")

    event.listen(session, "before_flush", fail_projection_flush)
    with pytest.raises(RuntimeError, match="synthetic projection insert failure"):
        GenerationRecordRepository(session).stage_record(receipt)
    event.remove(session, "before_flush", fail_projection_flush)
    session.rollback()

    assert session.get(GenerationResultReceiptRow, receipt.result_ref) is None
    assert session.execute(select(GenerationResultTargetRow)).scalars().all() == []
    session.close()


def test_natural_key_conflicts_do_not_return_another_owner_record() -> None:
    session, seeded = _seed_session()
    repository = GenerationRecordRepository(session)
    receipt = _receipt_for_seed(seeded)
    assert repository.stage_record(receipt).status == "created"
    same_owner_replay = repository.stage_record(receipt)

    foreign_receipt = repository.stage_record(
        replace(receipt, owner_id="foreign-owner", task_id="foreign-task")
    )
    foreign_task_receipt = repository.stage_record(
        replace(receipt, task_id="another-task")
    )
    foreign_unit_receipt = repository.stage_record(
        replace(receipt, unit_id="another-unit")
    )
    stream = StreamFeeRecord(
        _id(),
        seeded["owner_id"],
        "text.stream",
        "provider/model@registry-v1",
        0,
        "skipped",
        0,
        None,
        None,
        "no_debit_terminal",
        "ready_to_start",
        None,
        None,
        None,
        seeded["now"],
        seeded["now"],
    )
    assert repository.stage_record(stream).status == "created"
    foreign_stream = repository.stage_record(
        replace(stream, owner_id="foreign-owner")
    )

    assert same_owner_replay.status == "same"
    assert foreign_receipt.status == "conflict"
    assert foreign_receipt.record is None
    assert foreign_task_receipt.status == "conflict"
    assert foreign_task_receipt.record is None
    assert foreign_unit_receipt.status == "conflict"
    assert foreign_unit_receipt.record is None
    assert foreign_stream.status == "conflict"
    assert foreign_stream.record is None



@pytest.mark.parametrize("scope_field", ["owner_id", "task_id", "unit_id"])
def test_advance_continuation_scope_conflict_never_returns_foreign_work(
    scope_field: str,
) -> None:
    session, seeded = _seed_session()
    session.get(BillingUnitRow, seeded["unit_id"]).operation = "frame.review"
    session.flush()
    repository = GenerationRecordRepository(session)
    receipt = _receipt_for_seed(seeded, operation="frame.review")
    assert repository.stage_record(receipt).status == "created"
    work = _continuation_for_seed(seeded, receipt)
    assert repository.stage_record(work).status == "created"

    stale_same_scope = repository.advance_continuation_work(
        replace(work, phase="fee_pending", phase_version=2),
        expected_phase_version=1,
    )
    foreign_value = "foreign-" + scope_field
    foreign_scope = repository.advance_continuation_work(
        replace(
            work,
            **{
                scope_field: foreign_value,
                "phase": "fee_pending",
                "phase_version": 2,
            },
        ),
        expected_phase_version=1,
    )

    assert stale_same_scope.status == "conflict"
    assert stale_same_scope.record is not None
    assert stale_same_scope.record.work_id == work.work_id
    assert stale_same_scope.record.phase == work.phase
    assert stale_same_scope.record.phase_version == work.phase_version
    assert foreign_scope.status == "conflict"
    assert foreign_scope.record is None
    session.close()


@pytest.mark.parametrize(
    ("field_name", "foreign_value"),
    [
        ("owner_id", "foreign-owner"),
        ("task_id", "foreign-task"),
        ("unit_id", "foreign-unit"),
        ("result_ref", "foreign-result"),
        ("result_digest", "foreign-digest"),
    ],
)
def test_application_record_requires_original_owner_task_unit_and_result(
    field_name: str,
    foreign_value: str,
) -> None:
    session, seeded = _seed_session()
    repository = GenerationRecordRepository(session)
    receipt = _receipt_for_seed(seeded)
    assert repository.stage_record(receipt).status == "created"
    application = ApplicationRecord(
        _id(),
        receipt.result_ref,
        seeded["owner_id"],
        seeded["task_id"],
        seeded["unit_id"],
        "frame-review",
        receipt.content_digest,
        "pending",
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    )

    result = repository.stage_record(
        replace(application, **{field_name: foreign_value})
    )

    assert result.status == "invalid_binding"
    assert result.record is None
    session.close()


def test_continuation_done_requires_ordered_phases_and_real_completion_records() -> None:
    session, seeded = _seed_session()
    session.get(BillingUnitRow, seeded["unit_id"]).operation = "frame.review"
    session.flush()
    repository = GenerationRecordRepository(session)
    receipt = _receipt_for_seed(seeded, operation="frame.review")
    assert repository.stage_record(receipt).status == "created"

    initial = _continuation_for_seed(seeded, receipt)
    assert repository.stage_record(initial).status == "created"
    initial_done = repository.stage_record(
        replace(initial, phase="done", phase_version=3)
    )
    invalid_initial = repository.stage_record(
        replace(
            _continuation_for_seed(seeded, receipt, policy_version="invalid-initial"),
            phase="fee_pending",
            phase_version=1,
        )
    )
    skipped_phase = repository.advance_continuation_work(
        replace(initial, phase="done", phase_version=1),
        expected_phase_version=0,
    )
    assert initial_done.status == "conflict"
    assert invalid_initial.status == "invalid_binding"
    assert skipped_phase.status == "conflict"

    pending_application_ref = _id()
    pending_application = ApplicationRecord(
        pending_application_ref,
        receipt.result_ref,
        seeded["owner_id"],
        seeded["task_id"],
        seeded["unit_id"],
        "frame-review",
        receipt.content_digest,
        "pending",
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    )
    assert repository.stage_record(pending_application).status == "created"
    pending_fee = UnitFeeDecision(
        seeded["unit_id"],
        "post_apply",
        "pending",
        None,
        receipt.result_ref,
        pending_application_ref,
        None,
    )
    assert repository.stage_record(pending_fee).status == "created"
    final_phase = _advance_to_final_phase(repository, initial)
    pending_done = repository.advance_continuation_work(
        replace(
            final_phase,
            phase="done",
            phase_version=3,
            fee_decision_ref=seeded["unit_id"],
            application_ref=pending_application_ref,
        ),
        expected_phase_version=2,
    )
    assert pending_done.status == "invalid_binding"
    assert "settled fee" in (pending_done.detail or "")

    stopped = _continuation_for_seed(seeded, receipt, policy_version="policy-v2")
    assert repository.stage_record(stopped).status == "created"
    stopped_result = repository.advance_continuation_work(
        replace(stopped, phase="stopped_cancelled", phase_version=1),
        expected_phase_version=0,
    )
    assert stopped_result.status == "updated"
    assert repository.stage_record(stopped_result.record).status == "same"
    session.close()



@pytest.mark.parametrize("resolution", ["waived_insufficient", "waived_charge_failed"])
def test_review_completion_accepts_applied_result_with_definitive_fee_waiver(
    resolution: str,
) -> None:
    session, seeded = _seed_session()
    session.get(BillingUnitRow, seeded["unit_id"]).operation = "frame.review"
    session.flush()
    repository = GenerationRecordRepository(session)
    receipt = _receipt_for_seed(seeded, operation="frame.review")
    assert repository.stage_record(receipt).status == "created"

    application_ref = _id()
    application = ApplicationRecord(
        application_ref,
        receipt.result_ref,
        seeded["owner_id"],
        seeded["task_id"],
        seeded["unit_id"],
        "frame-review",
        receipt.content_digest,
        "applied",
        None,
        "target-digest",
        1,
        2,
        None,
        None,
        seeded["now"],
    )
    assert repository.stage_record(application).status == "created"

    waiver = UnitFeeDecision(
        seeded["unit_id"],
        "post_apply",
        resolution,
        None,
        receipt.result_ref,
        application_ref,
        seeded["now"],
    )
    assert repository.stage_record(waiver).status == "created"
    work = _continuation_for_seed(seeded, receipt)
    assert repository.stage_record(work).status == "created"
    final_phase = _advance_to_final_phase(repository, work)
    completed = repository.advance_continuation_work(
        replace(
            final_phase,
            phase="done",
            phase_version=3,
            fee_decision_ref=seeded["unit_id"],
            application_ref=application_ref,
        ),
        expected_phase_version=2,
    )

    assert completed.status == "updated"
    assert completed.record.phase == "done"
    session.close()


@pytest.mark.parametrize("unit_state", ["charged", "ledger"])
def test_review_completion_rejects_waiver_when_unit_was_debited(
    unit_state: str,
) -> None:
    session, seeded = _seed_session()
    session.get(BillingUnitRow, seeded["unit_id"]).operation = "frame.review"
    session.flush()
    repository = GenerationRecordRepository(session)
    receipt = _receipt_for_seed(seeded, operation="frame.review")
    assert repository.stage_record(receipt).status == "created"

    application_ref = _id()
    application = ApplicationRecord(
        application_ref,
        receipt.result_ref,
        seeded["owner_id"],
        seeded["task_id"],
        seeded["unit_id"],
        "frame-review",
        receipt.content_digest,
        "applied",
        None,
        "target-digest",
        1,
        2,
        None,
        None,
        seeded["now"],
    )
    assert repository.stage_record(application).status == "created"
    waiver = UnitFeeDecision(
        seeded["unit_id"],
        "post_apply",
        "waived_insufficient",
        None,
        receipt.result_ref,
        application_ref,
        seeded["now"],
    )
    assert repository.stage_record(waiver).status == "created"

    if unit_state == "charged":
        session.execute(
            update(BillingUnitRow)
            .where(BillingUnitRow.id == seeded["unit_id"])
            .values(billing_status="charged", debited_amount=10)
        )
    else:
        session.add(
            CreditLogRow(
                id=_id(),
                user_id=seeded["owner_id"],
                amount=-10,
                balance_after=90,
                type="usage",
                task_id=seeded["task_id"],
                business_key="unexpected-review-waiver-debit",
                billing_unit_id=seeded["unit_id"],
                related_debit_id=None,
                created_at=seeded["now"],
            )
        )
    session.flush()
    work = _continuation_for_seed(seeded, receipt)
    assert repository.stage_record(work).status == "created"
    final_phase = _advance_to_final_phase(repository, work)
    completed = repository.advance_continuation_work(
        replace(
            final_phase,
            phase="done",
            phase_version=3,
            fee_decision_ref=seeded["unit_id"],
            application_ref=application_ref,
        ),
        expected_phase_version=2,
    )

    assert completed.status == "invalid_binding"
    assert completed.record.phase == "finalize_pending"
    session.close()


def test_continuation_done_accepts_completed_fee_delivery_and_utc_reloads() -> None:
    session, seeded = _seed_session()
    session.get(BillingUnitRow, seeded["unit_id"]).operation = "frame.review"
    session.flush()
    repository = GenerationRecordRepository(session)
    receipt = _receipt_for_seed(seeded, operation="frame.review")
    assert repository.stage_record(receipt).status == "created"

    application_ref = _id()
    application = ApplicationRecord(
        application_ref,
        receipt.result_ref,
        seeded["owner_id"],
        seeded["task_id"],
        seeded["unit_id"],
        "frame-review",
        receipt.content_digest,
        "applied",
        None,
        "target-digest",
        1,
        2,
        None,
        None,
        seeded["now"],
    )
    assert repository.stage_record(application).status == "created"
    session.expire_all()
    reloaded_application = repository.find_application_record(
        seeded["owner_id"],
        seeded["task_id"],
        receipt.result_ref,
        "frame-review",
    )
    assert reloaded_application is not None
    assert reloaded_application.committed_at == application.committed_at
    assert reloaded_application.committed_at.tzinfo == timezone.utc

    ledger_ref = _id()
    session.add(
        CreditLogRow(
            id=ledger_ref,
            user_id=seeded["owner_id"],
            amount=-10,
            balance_after=90,
            type="usage",
            task_id=seeded["task_id"],
            business_key="frame-review-debit",
            billing_unit_id=seeded["unit_id"],
            related_debit_id=None,
            created_at=seeded["now"],
        )
    )
    session.flush()
    fee = UnitFeeDecision(
        seeded["unit_id"],
        "post_apply",
        "charged",
        ledger_ref,
        receipt.result_ref,
        application_ref,
        seeded["now"],
    )
    assert repository.stage_record(fee).status == "created"

    work = _continuation_for_seed(seeded, receipt)
    assert repository.stage_record(work).status == "created"
    final_phase = _advance_to_final_phase(repository, work)
    completion = replace(
        final_phase,
        phase="done",
        phase_version=3,
        fee_decision_ref=seeded["unit_id"],
        application_ref=application_ref,
    )
    other_receipt = _receipt_for_seed(seeded, operation="frame.review")
    assert repository.stage_record(other_receipt).status == "created"
    alternate_application = replace(
        application,
        application_ref=_id(),
        target_scope="frame-review-alternate",
    )
    assert repository.stage_record(alternate_application).status == "created"

    session.execute(
        update(UnitFeeDecisionRow)
        .where(UnitFeeDecisionRow.unit_id == seeded["unit_id"])
        .values(generation_result_ref=other_receipt.result_ref)
    )
    session.flush()
    session.expire_all()
    misbound_result = repository.advance_continuation_work(
        completion, expected_phase_version=2
    )
    assert misbound_result.status == "invalid_binding"
    assert "original result and application" in (misbound_result.detail or "")

    session.execute(
        update(UnitFeeDecisionRow)
        .where(UnitFeeDecisionRow.unit_id == seeded["unit_id"])
        .values(
            generation_result_ref=receipt.result_ref,
            application_ref=alternate_application.application_ref,
        )
    )
    session.flush()
    session.expire_all()
    misbound_application = repository.advance_continuation_work(
        completion, expected_phase_version=2
    )
    assert misbound_application.status == "invalid_binding"
    assert "original result and application" in (misbound_application.detail or "")

    session.execute(
        update(UnitFeeDecisionRow)
        .where(UnitFeeDecisionRow.unit_id == seeded["unit_id"])
        .values(application_ref=application_ref, resolved_at=None)
    )
    session.flush()
    session.expire_all()
    unresolved_fee = repository.advance_continuation_work(
        completion, expected_phase_version=2
    )
    assert unresolved_fee.status == "invalid_binding"
    assert "resolved fee decision" in (unresolved_fee.detail or "")

    session.execute(
        update(UnitFeeDecisionRow)
        .where(UnitFeeDecisionRow.unit_id == seeded["unit_id"])
        .values(resolved_at=seeded["now"])
    )
    session.flush()
    session.expire_all()
    missing_unit_debit = repository.advance_continuation_work(
        completion, expected_phase_version=2
    )
    assert missing_unit_debit.status == "invalid_binding"
    assert "charged" in (missing_unit_debit.detail or "")

    session.execute(
        update(BillingUnitRow)
        .where(BillingUnitRow.id == seeded["unit_id"])
        .values(billing_status="charged", debited_amount=9, refunded_amount=0)
    )
    session.flush()
    session.expire_all()
    mismatched_unit_debit = repository.advance_continuation_work(
        completion, expected_phase_version=2
    )
    assert mismatched_unit_debit.status == "invalid_binding"
    assert "amount" in (mismatched_unit_debit.detail or "")

    session.execute(
        update(BillingUnitRow)
        .where(BillingUnitRow.id == seeded["unit_id"])
        .values(billing_status="charged", debited_amount=10, refunded_amount=0)
    )
    session.flush()
    session.expire_all()
    completed = repository.advance_continuation_work(
        completion, expected_phase_version=2
    )
    assert completed.status == "updated"
    assert completed.record.phase == "done"
    assert repository.stage_record(completed.record).status == "same"

    session.execute(
        update(ApplicationRecordRow)
        .where(ApplicationRecordRow.application_ref == application_ref)
        .values(delivery_status="pending", committed_at=None)
    )
    session.flush()
    session.expire_all()
    retry_work = _continuation_for_seed(
        seeded, receipt, policy_version="policy-v2"
    )
    assert repository.stage_record(retry_work).status == "created"
    retry_final = _advance_to_final_phase(repository, retry_work)
    unapplied_done = repository.advance_continuation_work(
        replace(
            retry_final,
            phase="done",
            phase_version=3,
            fee_decision_ref=seeded["unit_id"],
            application_ref=application_ref,
        ),
        expected_phase_version=2,
    )
    assert unapplied_done.status == "invalid_binding"
    assert "committed application" in (unapplied_done.detail or "")

    different_instant = replace(
        completed.record,
        phase="done",
        phase_version=4,
        generation_fulfilled_at=completed.record.generation_fulfilled_at + timedelta(seconds=1),
    )
    immutable_change = repository.advance_continuation_work(
        different_instant,
        expected_phase_version=3,
    )
    assert immutable_change.status == "conflict"
    assert "immutable" in (immutable_change.detail or "")
    session.close()


def test_poll_reservation_uses_explicit_unit_scoped_group_cas() -> None:
    session, seeded = _seed_session()
    repository = GenerationRecordRepository(session)
    now = seeded["now"]
    group = ExecutionAttemptGroup(
        unit_id=seeded["unit_id"],
        group_key="video-haoai-attempts",
        registry_version="video-haoai-attempt-group-v1",
        attempt_step_ids=seeded["step_ids"],
        original_payload_digest=seeded["digest"],
        max_retries=2,
        active_attempt=0,
        selected_evidence_id=None,
        status="active",
        poll_used=0,
        auth_unavailable_streak=0,
        terminal_reason=None,
        poll_sequence=0,
        next_poll_at=None,
        outstanding_reservation_ref=None,
        sticky_policy_stop_event_ref=None,
        active_eligibility_version="eligibility-v1",
    )
    assert repository.stage_record(group).status == "created"
    reservation = PollReservation(
        reservation_id=_id(),
        group_key=group.group_key,
        sequence=1,
        attempt_id=_id(),
        step_id=seeded["step_ids"][0],
        submission_id=seeded["submission_ids"][0],
        external_id=seeded["external_ids"][0],
        payload_digest=seeded["digest"],
        committed_poll_count=1,
        reserved_at=now,
        timeout_at=now + timedelta(seconds=5),
        next_poll_at=now + timedelta(seconds=5),
        token="opaque-reservation-token",
        state="reserved",
    )

    created = repository.reserve_poll(
        reservation,
        unit_id=seeded["unit_id"],
        expected_poll_used=0,
        expected_poll_sequence=0,
    )
    replay = repository.reserve_poll(
        reservation,
        unit_id=seeded["unit_id"],
        expected_poll_used=0,
        expected_poll_sequence=0,
    )
    generic_stage = repository.stage_record(reservation)

    assert created.status == "created"
    assert replay.status == "same"
    assert generic_stage.status == "invalid_binding"
    assert repository.find_poll_reservation(
        seeded["unit_id"], group.group_key, 1
    ) == reservation
    session.rollback()
    session.close()


@pytest.mark.parametrize(
    ("unit_state", "ledger_mode", "expected_status"),
    [
        ("no_charge", "none", "updated"),
        ("no_charge", "referenced", "invalid_binding"),
        ("no_charge", "unreferenced", "invalid_binding"),
        ("charged", "none", "invalid_binding"),
    ],
)
def test_no_charge_completion_requires_zero_unit_without_usage_ledger(
    unit_state: str,
    ledger_mode: str,
    expected_status: str,
) -> None:
    session, seeded = _seed_session()
    session.execute(
        update(BillingUnitRow)
        .where(BillingUnitRow.id == seeded["unit_id"])
        .values(
            operation="frame.optimize",
            quoted_amount=0,
            billing_status=unit_state,
            debited_amount=10 if unit_state == "charged" else 0,
            refunded_amount=0,
        )
    )
    session.flush()
    repository = GenerationRecordRepository(session)
    receipt = _receipt_for_seed(seeded, operation="frame.optimize")
    assert repository.stage_record(receipt).status == "created"

    ledger_ref = None
    if ledger_mode != "none":
        ledger_ref = _id()
        session.add(
            CreditLogRow(
                id=ledger_ref,
                user_id=seeded["owner_id"],
                amount=-1,
                balance_after=99,
                type="usage",
                task_id=seeded["task_id"],
                business_key="zero-quote-unexpected-usage",
                billing_unit_id=seeded["unit_id"],
                related_debit_id=None,
                created_at=seeded["now"],
            )
        )
        session.flush()
    fee = UnitFeeDecision(
        seeded["unit_id"],
        "pre_apply",
        "no_charge",
        ledger_ref if ledger_mode == "referenced" else None,
        None,
        None,
        seeded["now"],
    )
    assert repository.stage_record(fee).status == "created"

    application_ref = _id()
    application = ApplicationRecord(
        application_ref,
        receipt.result_ref,
        seeded["owner_id"],
        seeded["task_id"],
        seeded["unit_id"],
        "frame-optimize",
        receipt.content_digest,
        "applied",
        None,
        "target-digest",
        1,
        2,
        None,
        None,
        seeded["now"],
    )
    assert repository.stage_record(application).status == "created"
    work = _continuation_for_seed(seeded, receipt, operation="frame.optimize")
    assert repository.stage_record(work).status == "created"
    final_phase = _advance_to_final_phase(
        repository,
        work,
        fee_decision_ref=seeded["unit_id"],
        application_ref=application_ref,
    )
    completion = repository.advance_continuation_work(
        replace(
            final_phase,
            phase="done",
            phase_version=3,
            fee_decision_ref=seeded["unit_id"],
            application_ref=application_ref,
        ),
        expected_phase_version=2,
    )

    assert completion.status == expected_status
    if expected_status == "updated":
        assert completion.record.phase == "done"
    else:
        assert completion.record.phase == "finalize_pending"
        assert completion.detail is not None
    session.close()
