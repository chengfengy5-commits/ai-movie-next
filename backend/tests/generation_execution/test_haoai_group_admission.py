from __future__ import annotations

from sqlalchemy import select
from sqlalchemy import update
from sqlalchemy.orm import Session

import test_video_single_claims as video_fixtures
from test_admission_staging import _joint_caller, _joint_prepare_and_stage

from haoai_backend.generation_core.admission_receipts import (
    AdmissionConfirmed,
    AdmissionStaged,
    lookup_staged_admission,
)
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    ExecutionStepRow,
    TaskSubmissionRow,
)
from haoai_backend.generation_core.schema import ExecutionAttemptGroupRow
from haoai_backend.generation_runtime.registry import HAOAI_ATTEMPT_GROUP


def test_haoai_video_admission_stages_and_confirms_one_group_one_unit_one_debit(
    tmp_path,
    monkeypatch,
):
    environment = video_fixtures._video_environment(
        monkeypatch,
        tmp_path,
        provider="haoai",
    )
    caller = _joint_caller(environment.engine)
    try:
        staged = _joint_prepare_and_stage(
            environment.binding,
            caller,
            operation="video.single",
            raw_key="haoai-group-admission-001",
            request=video_fixtures._video_request("haoai"),
        )

        assert isinstance(staged, AdmissionStaged)
        assert staged.graph_version == HAOAI_ATTEMPT_GROUP.registry_version
        assert len(staged.ordered_tasks) == 1
        expected = staged.ordered_tasks[0]
        assert len(expected.step_ids) == 3

        caller.commit()
        with Session(environment.engine) as reader:
            receipt = lookup_staged_admission(reader, staged)
            assert isinstance(receipt, AdmissionConfirmed)
            parent = reader.execute(
                select(TaskSubmissionRow.__table__).where(
                    TaskSubmissionRow.id == staged.submission_id
                )
            ).mappings().one()
            task = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == expected.task_id)
            ).mappings().one()
            unit = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.id == expected.unit_id
                )
            ).mappings().one()
            steps = reader.execute(
                select(ExecutionStepRow.__table__)
                .where(ExecutionStepRow.billing_unit_id == expected.unit_id)
                .order_by(ExecutionStepRow.step_order, ExecutionStepRow.id)
            ).mappings().all()
            group = reader.execute(
                select(ExecutionAttemptGroupRow.__table__).where(
                    ExecutionAttemptGroupRow.unit_id == expected.unit_id,
                    ExecutionAttemptGroupRow.group_key == HAOAI_ATTEMPT_GROUP.group_key,
                )
            ).mappings().one()
            debit_rows = reader.execute(
                select(CreditLogRow.__table__).where(
                    CreditLogRow.billing_unit_id == expected.unit_id,
                    CreditLogRow.type == "usage",
                )
            ).mappings().all()

        assert parent["task_ids"] == [expected.task_id]
        assert task["type"] == "video-single"
        assert unit["operation"] == "video.single"
        assert unit["step_graph_version"] == HAOAI_ATTEMPT_GROUP.registry_version
        assert tuple(row["id"] for row in steps) == expected.step_ids
        assert tuple(row["step_key"] for row in steps) == HAOAI_ATTEMPT_GROUP.attempt_step_ids
        assert all(row["required"] is False for row in steps)
        assert tuple(row["step_order"] for row in steps) == (0, 1, 2)
        assert tuple(row["input_digest"] for row in steps) == (staged.request_digest,) * 3
        assert group["attempt_step_ids"] == list(expected.step_ids)
        assert group["original_payload_digest"] == staged.request_digest
        assert len(debit_rows) == 1
        assert debit_rows[0]["amount"] == -unit["debited_amount"]
        assert debit_rows[0]["business_key"] == f"debit:{expected.unit_id}"
    finally:
        if caller.in_transaction():
            caller.rollback()
        caller.close()
        environment.engine.dispose()


def test_haoai_staged_receipt_survives_worker_claim_before_independent_read(
    tmp_path,
    monkeypatch,
):
    environment = video_fixtures._video_environment(
        monkeypatch,
        tmp_path,
        provider="haoai",
    )
    caller = _joint_caller(environment.engine)
    try:
        staged = _joint_prepare_and_stage(
            environment.binding,
            caller,
            operation="video.single",
            raw_key="haoai-group-admission-worker-race-001",
            request=video_fixtures._video_request("haoai"),
        )
        assert isinstance(staged, AdmissionStaged)
        caller.commit()

        with environment.engine.begin() as connection:
            connection.execute(
                update(ExecutionAttemptGroupRow.__table__)
                .where(
                    ExecutionAttemptGroupRow.unit_id
                    == staged.ordered_tasks[0].unit_id
                )
                .values(status="active")
            )
            connection.execute(
                update(AITaskRow.__table__)
                .where(AITaskRow.id == staged.ordered_tasks[0].task_id)
                .values(status="processing")
            )

        with Session(environment.engine) as reader:
            receipt = lookup_staged_admission(reader, staged)

        assert isinstance(receipt, AdmissionConfirmed)
    finally:
        if caller.in_transaction():
            caller.rollback()
        caller.close()
        environment.engine.dispose()
