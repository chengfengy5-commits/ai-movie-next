from __future__ import annotations

import json
from dataclasses import replace

import test_video_single_claims as video_fixtures
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from test_admission_staging import _joint_caller, _joint_prepare_and_stage

from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    ExecutionStepRow,
    TaskSubmissionRow,
)
from haoai_backend.generation_core.schema import ExecutionAttemptGroupRow
from haoai_backend.generation_credentials.haoai_group_execution_snapshot import (
    PersistedHaoaiGroupExecutionBinding,
    read_original_haoai_group_execution_input,
)
from haoai_backend.generation_runtime.registry import HAOAI_ATTEMPT_GROUP


def _admitted_group(environment, key: str):
    caller = _joint_caller(environment.engine)
    try:
        staged = _joint_prepare_and_stage(
            environment.binding,
            caller,
            operation="video.single",
            raw_key=key,
            request=video_fixtures._video_request("haoai"),
        )
        caller.commit()
        return staged
    finally:
        if caller.in_transaction():
            caller.rollback()
        caller.close()


def _persisted_binding(session: Session, task_id: str, attempt: int = 0):
    task = dict(
        session.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == task_id)
        ).mappings().one()
    )
    request_data = json.loads(task["request_data"])
    parent = dict(
        session.execute(
            select(TaskSubmissionRow.__table__).where(
                TaskSubmissionRow.id == request_data["submission_id"]
            )
        ).mappings().one()
    )
    unit = dict(
        session.execute(
            select(BillingUnitRow.__table__).where(
                BillingUnitRow.id == request_data["billing_unit_id"]
            )
        ).mappings().one()
    )
    steps = session.execute(
        select(ExecutionStepRow.__table__)
        .where(ExecutionStepRow.billing_unit_id == unit["id"])
        .order_by(ExecutionStepRow.step_order, ExecutionStepRow.id)
    ).mappings().all()
    assert len(steps) == 3
    step = dict(steps[attempt])
    snapshot = step["input_snapshot"]
    return PersistedHaoaiGroupExecutionBinding(
        owner_id=task["user_id"],
        task_id=task["id"],
        unit_id=unit["id"],
        group_key=HAOAI_ATTEMPT_GROUP.group_key,
        step_id=step["id"],
        attempt_index=attempt,
        submission_id=parent["id"],
        raw_key=parent["idempotency_key"],
        operation=parent["operation"],
        graph_version=unit["step_graph_version"],
        task_type=task["type"],
        task_model_id=request_data["model_id"],
        task_model_source=request_data["model_source"],
        task_model_owner_id=request_data.get("model_owner_id"),
        task_model_name=task["model_name"],
        request_digest=unit["input_digest"],
        rule_digest=request_data["rule_digest"],
        snapshot_digest=request_data["snapshot_digest"],
        server_attempt_id=snapshot["server_attempt_id"],
        prepared_payload_id=snapshot["prepared_payload_id"],
        protected_ref=snapshot["protected_ref"],
        ordered_manifest_digest=snapshot["ordered_manifest_digest"],
        quoted_amount=unit["quoted_amount"],
        task_message_id=task["message_id"] or None,
    )


def _read(session: Session, environment, binding):
    crypto = environment.capabilities.crypto
    proof_authority = environment.capabilities.proof_authority
    assert crypto is not None and proof_authority is not None
    return read_original_haoai_group_execution_input(
        session,
        binding,
        registry=environment.binding._producer._registry,
        crypto=crypto,
        proof_authority=proof_authority,
    )


def test_haoai_group_snapshot_reader_reopens_original_payload_for_active_step(
    tmp_path,
    monkeypatch,
):
    environment = video_fixtures._video_environment(
        monkeypatch,
        tmp_path,
        provider="haoai",
    )
    try:
        staged = _admitted_group(environment, "haoai-group-snapshot-001")
        task_id = staged.ordered_tasks[0].task_id
        with Session(environment.engine) as reader:
            binding = _persisted_binding(reader, task_id)
            verified = _read(reader, environment, binding)

        assert verified is not None
        assert verified.task_id == task_id
        assert verified.unit_id == binding.unit_id
        assert verified.step_id == binding.step_id
        assert verified.attempt_index == 0
        assert verified.server_attempt_id == binding.server_attempt_id
        assert verified.snapshot.model_ref.provider == "haoai"
        assert verified.provider_credential.provider == "haoai"
        assert len(verified.reference_images) == 3
        assert verified.reference_audios == ()
        assert verified.reference_videos == ()
    finally:
        environment.engine.dispose()


def test_haoai_group_snapshot_reader_rejects_mutated_step_graph(
    tmp_path,
    monkeypatch,
):
    environment = video_fixtures._video_environment(
        monkeypatch,
        tmp_path,
        provider="haoai",
    )
    try:
        staged = _admitted_group(environment, "haoai-group-snapshot-mutated-001")
        task_id = staged.ordered_tasks[0].task_id
        with environment.engine.begin() as connection:
            connection.execute(
                update(ExecutionStepRow.__table__)
                .where(
                    ExecutionStepRow.__table__.c.billing_unit_id
                    == staged.ordered_tasks[0].unit_id,
                    ExecutionStepRow.__table__.c.step_order == 1,
                )
                .values(required=True)
            )
        with Session(environment.engine) as reader:
            binding = _persisted_binding(reader, task_id)
            verified = _read(reader, environment, binding)
        assert verified is None
    finally:
        environment.engine.dispose()


def test_haoai_group_snapshot_reader_rejects_missing_group_record(
    tmp_path,
    monkeypatch,
):
    environment = video_fixtures._video_environment(
        monkeypatch,
        tmp_path,
        provider="haoai",
    )
    try:
        staged = _admitted_group(environment, "haoai-group-snapshot-no-group-001")
        task_id = staged.ordered_tasks[0].task_id
        with environment.engine.begin() as connection:
            connection.execute(
                ExecutionAttemptGroupRow.__table__.delete().where(
                    ExecutionAttemptGroupRow.unit_id
                    == staged.ordered_tasks[0].unit_id
                )
            )
        with Session(environment.engine) as reader:
            binding = _persisted_binding(reader, task_id)
            verified = _read(reader, environment, binding)
        assert verified is None
    finally:
        environment.engine.dispose()


def test_haoai_group_snapshot_reader_rejects_nonactive_step_binding(
    tmp_path,
    monkeypatch,
):
    environment = video_fixtures._video_environment(
        monkeypatch,
        tmp_path,
        provider="haoai",
    )
    try:
        staged = _admitted_group(environment, "haoai-group-snapshot-wrong-step-001")
        task_id = staged.ordered_tasks[0].task_id
        with Session(environment.engine) as reader:
            binding = _persisted_binding(reader, task_id, attempt=0)
            other_step = _persisted_binding(reader, task_id, attempt=1).step_id
            verified = _read(reader, environment, replace(binding, step_id=other_step))
        assert verified is None
    finally:
        environment.engine.dispose()
