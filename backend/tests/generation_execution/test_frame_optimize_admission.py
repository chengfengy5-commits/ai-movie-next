"""Owned SQLite checks for registered frame.optimize admission and reading."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.chat_text_generation.errors import ChatTextGenerationError
from haoai_backend.generation_catalog.tables import (
    chapters,
    model_configs,
    prompt_configs,
    storyboard_assets,
    system_models,
    system_prompts,
)
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
    ResultEvidenceRow,
    TaskSubmissionRow,
    UserCreditRow,
)
from haoai_backend.generation_core.frame_optimize_admission import admit_frame_optimize
from haoai_backend.generation_credentials.frame_optimize_snapshot_reader import (
    PersistedFrameOptimizeExecutionBinding,
    read_original_frame_optimize_execution_batch,
)
from haoai_backend.generation_credentials.application import CredentialRegistry
from haoai_backend.generation_core.schema import (
    ApplicationRecordRow,
    GenerationResultReceiptRow,
    LocalContinuationWorkRow,
    UnitFeeDecisionRow,
)
from haoai_backend.generation_credentials.persistence import prepared_payloads
from haoai_backend.task_observation.cancellation import SqlTaskCancellationWriter

from generation_core.test_admission_staging import (
    ModelSource,
    _G2_JOINT_ACTOR_ID,
    _G2_JOINT_MODEL_ID,
    _G2_JOINT_NOW,
    _joint_database,
    _joint_registered_capabilities,
    _JointCommitThenUnknownSession,
)


class _Ids:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> str:
        self.value += 1
        return str(uuid.uuid5(uuid.NAMESPACE_OID, f"frame-optimize-test-{self.value}"))


def _build_optimize_case(
    tmp_path: Path,
    *,
    frame_count: int = 1,
    request_indexes: tuple[int, ...] | None = None,
    wallet_credits: int | None = 500,
    model_source: ModelSource = ModelSource.SYSTEM,
    prompt_source: ModelSource | None = ModelSource.SYSTEM,
    prompt_owner_id: str | None = None,
    cost_per_call: int = 3,
):
    engine = _joint_database(
        tmp_path,
        model_source=model_source,
        wallet_credits=wallet_credits,
    )
    request_indexes = request_indexes or tuple(range(frame_count))
    frames = [
        {"text": f"database frame {index}", "storyboard": [f"opt-storyboard-{index}"], "kept": index}
        for index in range(frame_count)
    ]
    stamp = _G2_JOINT_NOW.replace(tzinfo=None)
    with engine.begin() as connection:
        model_table = system_models if model_source is ModelSource.SYSTEM else model_configs
        model_values = {
            "category": "text",
            "params": json.dumps({"temperature": 0.2, "max_tokens": 600}),
        }
        if model_source is ModelSource.SYSTEM:
            model_values["cost_per_call"] = cost_per_call
        connection.execute(
            update(model_table)
            .where(model_table.c.id == _G2_JOINT_MODEL_ID)
            .values(**model_values)
        )
        connection.execute(
            update(chapters)
            .where(chapters.c.id == "joint-chapter-1")
            .values(content=json.dumps(frames, ensure_ascii=False))
        )
        for index in request_indexes:
            connection.execute(
                insert(storyboard_assets).values(
                    id=f"opt-storyboard-{index}",
                    series_id="joint-series-1",
                    chapter_id="joint-chapter-1",
                    frame_index=index,
                    name=f"Optimize frame {index}",
                    description=None,
                    image_url=None,
                    updated_at=stamp,
                )
            )
        if prompt_source is not None:
            if prompt_source is ModelSource.SYSTEM:
                connection.execute(
                    insert(system_prompts).values(
                        id="opt-prompt-1",
                        category="ai-review",
                        name="Optimize prompt",
                        system_prompt="Keep the scene coherent.",
                        user_prompt="Polish the requested shot.",
                        is_active=True,
                        sort_order=0,
                        created_at=stamp,
                        updated_at=stamp,
                    )
                )
            else:
                connection.execute(
                    insert(prompt_configs).values(
                        id="opt-user-prompt-1",
                        user_id=prompt_owner_id or _G2_JOINT_ACTOR_ID,
                        category="ai-review",
                        name="Owned optimize prompt",
                        system_prompt="Keep the owned scene coherent.",
                        user_prompt="Polish the owned requested shot.",
                        sort_order=0,
                        created_at=stamp,
                        updated_at=stamp,
                    )
                )
    capabilities, crypto_state = _joint_registered_capabilities(
        engine,
        model_source=model_source,
    )
    sessions = sessionmaker(engine, expire_on_commit=False, autoflush=True)
    ids = _Ids()
    request: dict[str, object] = {
        "chapter_id": "joint-chapter-1",
        "model_id": _G2_JOINT_MODEL_ID,
        "enable_thinking": True,
        "concurrency": 5,
        "chapter_texts": [f"context {index}" for index in range(frame_count)],
        "frames": [
            {"index": index, "text": f"requested frame {index}", "asset_context": f"request context {index}"}
            for index in request_indexes
        ],
    }
    if prompt_source is ModelSource.SYSTEM:
        request["prompt_id"] = "opt-prompt-1"
    elif prompt_source is ModelSource.USER:
        request["prompt_id"] = "opt-user-prompt-1"
    return engine, sessions, capabilities, crypto_state, ids, request


def _admit(engine, sessions, capabilities, ids, request):
    return admit_frame_optimize(
        sessions,
        _G2_JOINT_ACTOR_ID,
        request,
        registry_capabilities=capabilities,
        max_user_concurrency=8,
        id_factory=ids,
        clock=lambda: _G2_JOINT_NOW,
    )


def _reader_binding(session: Session, task: AITaskRow) -> PersistedFrameOptimizeExecutionBinding:
    request_data = json.loads(task.request_data)
    return PersistedFrameOptimizeExecutionBinding(
        owner_id=task.user_id,
        task_id=task.id,
        submission_id=request_data["submission_id"],
        task_message_id=task.message_id,
        task_model_name=task.model_name,
        request_digest=request_data["request_digest"],
        graph_digest=request_data["graph_digest"],
        manifest_snapshot_digest=request_data["manifest_snapshot_digest"],
    )


def test_registered_frame_optimize_commits_one_complete_unbilled_graph_and_reader_reopens_it(
    tmp_path: Path,
) -> None:
    engine, sessions, capabilities, crypto_state, ids, request = _build_optimize_case(
        tmp_path,
        frame_count=1,
    )
    try:
        result = _admit(engine, sessions, capabilities, ids, request)
        assert result["frame_count"] == 1
        with sessions() as session:
            task = session.get(AITaskRow, result["task_id"])
            assert task is not None
            request_data = json.loads(task.request_data)
            assert task.type == "batch-optimize"
            assert task.status == "queued"
            assert task.billing_status == "unbilled"
            assert task.credit_cost == 0
            assert task.result is None
            assert len(session.execute(select(TaskSubmissionRow)).scalars().all()) == 1
            units = session.execute(select(BillingUnitRow)).scalars().all()
            steps = session.execute(select(ExecutionStepRow)).scalars().all()
            payloads = session.execute(select(prepared_payloads)).mappings().all()
            assert len(units) == len(steps) == len(payloads) == 1
            assert units[0].quoted_amount == 3
            assert units[0].debited_amount == units[0].refunded_amount == 0
            assert units[0].billing_status == "unbilled"
            assert steps[0].execution_status == "prepared"
            assert session.execute(select(ExternalSubmissionRow)).scalars().all() == []
            assert session.execute(select(ResultEvidenceRow)).scalars().all() == []
            assert session.execute(select(CreditLogRow)).scalars().all() == []
            assert session.execute(select(GenerationResultReceiptRow)).scalars().all() == []
            assert session.execute(select(ApplicationRecordRow)).scalars().all() == []
            assert session.execute(select(UnitFeeDecisionRow)).scalars().all() == []
            assert session.execute(select(LocalContinuationWorkRow)).scalars().all() == []
            assert session.execute(select(UserCreditRow.credits)).scalar_one() == 500
            secret_reads_before_reopen = crypto_state["secret_handle_calls"]
            verified = read_original_frame_optimize_execution_batch(
                session,
                _reader_binding(session, task),
                crypto=capabilities.crypto,
                proof_authority=capabilities.proof_authority,
            )
            assert verified is not None
            assert verified.task_id == task.id
            assert verified.request["frames"][0]["index"] == 0
            assert verified.items[0].requested_text == "requested frame 0"
            assert verified.items[0].database_text == "database frame 0"
            assert verified.items[0].database_frame["kept"] == 0
            assert verified.items[0].credential_binding.original_principal_id
            assert request_data["mode"] == "frame_optimize"
            assert crypto_state["secret_handle_calls"] == secret_reads_before_reopen
    finally:
        engine.dispose()


def test_registered_frame_optimize_without_prompt_reopens_from_fresh_sqlite_reader(
    tmp_path: Path,
) -> None:
    engine, sessions, capabilities, _crypto_state, ids, request = _build_optimize_case(
        tmp_path,
        prompt_source=None,
    )
    try:
        result = _admit(engine, sessions, capabilities, ids, request)
        with sessions() as session:
            task = session.get(AITaskRow, result["task_id"])
            assert task is not None
            verified = read_original_frame_optimize_execution_batch(
                session,
                _reader_binding(session, task),
                crypto=capabilities.crypto,
                proof_authority=capabilities.proof_authority,
            )
            assert verified is not None
            assert verified.items[0].prompt == {
                "source": "request",
                "id": None,
                "owner_or_system": None,
                "system_prompt": "",
                "user_prompt": "",
            }
    finally:
        engine.dispose()


def test_system_model_with_actor_owned_prompt_reopens_from_fresh_sqlite_reader(
    tmp_path: Path,
) -> None:
    engine, sessions, capabilities, _crypto_state, ids, request = _build_optimize_case(
        tmp_path,
        model_source=ModelSource.SYSTEM,
        prompt_source=ModelSource.USER,
    )
    try:
        result = _admit(engine, sessions, capabilities, ids, request)
        with sessions() as session:
            task = session.get(AITaskRow, result["task_id"])
            assert task is not None
            verified = read_original_frame_optimize_execution_batch(
                session,
                _reader_binding(session, task),
                crypto=capabilities.crypto,
                proof_authority=capabilities.proof_authority,
            )
            assert verified is not None
            assert verified.items[0].model["source"] == "system"
            assert verified.items[0].prompt["source"] == "user"
            assert verified.items[0].prompt["owner_or_system"] == _G2_JOINT_ACTOR_ID
    finally:
        engine.dispose()


def test_personal_model_with_system_prompt_reopens_from_fresh_sqlite_reader(
    tmp_path: Path,
) -> None:
    engine, sessions, capabilities, _crypto_state, ids, request = _build_optimize_case(
        tmp_path,
        model_source=ModelSource.USER,
        prompt_source=ModelSource.SYSTEM,
    )
    try:
        result = _admit(engine, sessions, capabilities, ids, request)
        with sessions() as session:
            task = session.get(AITaskRow, result["task_id"])
            assert task is not None
            verified = read_original_frame_optimize_execution_batch(
                session,
                _reader_binding(session, task),
                crypto=capabilities.crypto,
                proof_authority=capabilities.proof_authority,
            )
            assert verified is not None
            assert verified.items[0].model["source"] == "user"
            assert verified.items[0].prompt["source"] == "system"
    finally:
        engine.dispose()


def test_personal_model_and_prompt_reopen_from_fresh_sqlite_reader(tmp_path: Path) -> None:
    engine, sessions, capabilities, _crypto_state, ids, request = _build_optimize_case(
        tmp_path,
        model_source=ModelSource.USER,
        prompt_source=ModelSource.USER,
    )
    try:
        result = _admit(engine, sessions, capabilities, ids, request)
        with sessions() as session:
            task = session.get(AITaskRow, result["task_id"])
            assert task is not None
            verified = read_original_frame_optimize_execution_batch(
                session,
                _reader_binding(session, task),
                crypto=capabilities.crypto,
                proof_authority=capabilities.proof_authority,
            )
            assert verified is not None
            assert verified.items[0].model["source"] == "user"
            assert verified.items[0].prompt["source"] == "user"
            assert verified.items[0].prompt["owner_or_system"] == _G2_JOINT_ACTOR_ID
    finally:
        engine.dispose()


def test_personal_prompt_owned_by_another_actor_is_rejected_without_staging(
    tmp_path: Path,
) -> None:
    engine, sessions, capabilities, _crypto_state, ids, request = _build_optimize_case(
        tmp_path,
        model_source=ModelSource.SYSTEM,
        prompt_source=ModelSource.USER,
        prompt_owner_id="another-fixture-actor",
    )
    try:
        with pytest.raises(ChatTextGenerationError) as refusal:
            _admit(engine, sessions, capabilities, ids, request)
        assert refusal.value.status_code == 422
        with sessions() as session:
            assert session.execute(select(AITaskRow)).scalars().all() == []
            assert session.execute(select(TaskSubmissionRow)).scalars().all() == []
            assert session.execute(select(BillingUnitRow)).scalars().all() == []
            assert session.execute(select(ExecutionStepRow)).scalars().all() == []
            assert session.execute(select(prepared_payloads)).mappings().all() == []
            assert session.execute(select(UserCreditRow.credits)).scalar_one() == 500
    finally:
        engine.dispose()


def test_byok_zero_quote_without_prompt_needs_no_wallet_and_reopens_original_input(
    tmp_path: Path,
) -> None:
    engine, sessions, capabilities, _crypto_state, ids, request = _build_optimize_case(
        tmp_path,
        model_source=ModelSource.USER,
        prompt_source=None,
        wallet_credits=None,
    )
    try:
        result = _admit(engine, sessions, capabilities, ids, request)
        with sessions() as session:
            task = session.get(AITaskRow, result["task_id"])
            assert task is not None
            assert session.execute(select(UserCreditRow)).scalars().all() == []
            assert session.execute(select(CreditLogRow)).scalars().all() == []
            units = session.execute(
                select(BillingUnitRow).where(BillingUnitRow.task_id == task.id)
            ).scalars().all()
            assert len(units) == 1
            assert units[0].quoted_amount == units[0].debited_amount == units[0].refunded_amount == 0
            assert session.execute(select(UnitFeeDecisionRow)).scalars().all() == []
            verified = read_original_frame_optimize_execution_batch(
                session,
                _reader_binding(session, task),
                crypto=capabilities.crypto,
                proof_authority=capabilities.proof_authority,
            )
            assert verified is not None
            assert verified.task_id == result["task_id"]
    finally:
        engine.dispose()


def test_reader_reopens_original_input_when_zero_charge_is_projected(tmp_path: Path) -> None:
    engine, sessions, capabilities, _crypto_state, ids, request = _build_optimize_case(
        tmp_path,
        model_source=ModelSource.USER,
        prompt_source=ModelSource.SYSTEM,
        wallet_credits=None,
    )
    try:
        result = _admit(engine, sessions, capabilities, ids, request)
        with engine.begin() as connection:
            connection.execute(
                update(AITaskRow.__table__)
                .where(AITaskRow.__table__.c.id == result["task_id"])
                .values(billing_status="no_charge")
            )
            connection.execute(
                update(BillingUnitRow.__table__)
                .where(BillingUnitRow.__table__.c.task_id == result["task_id"])
                .values(billing_status="no_charge")
            )
        with sessions() as session:
            task = session.get(AITaskRow, result["task_id"])
            assert task is not None
            assert session.execute(select(UnitFeeDecisionRow)).scalars().all() == []
            assert session.execute(select(CreditLogRow)).scalars().all() == []
            assert session.execute(select(UserCreditRow)).scalars().all() == []
            verified = read_original_frame_optimize_execution_batch(
                session,
                _reader_binding(session, task),
                crypto=capabilities.crypto,
                proof_authority=capabilities.proof_authority,
            )
            assert verified is not None
            assert verified.items[0].snapshot.server_quote == 0
    finally:
        engine.dispose()


def test_reader_reopens_original_input_after_commit_without_charge_projection(
    tmp_path: Path,
) -> None:
    engine, sessions, capabilities, _crypto_state, ids, request = _build_optimize_case(
        tmp_path,
        model_source=ModelSource.USER,
        prompt_source=ModelSource.SYSTEM,
        wallet_credits=None,
    )
    try:
        result = _admit(engine, sessions, capabilities, ids, request)
        with sessions() as session:
            task = session.get(AITaskRow, result["task_id"])
            assert task is not None
            assert task.billing_status == "unbilled"
            assert session.execute(select(UserCreditRow)).scalars().all() == []
            assert session.execute(select(CreditLogRow)).scalars().all() == []
            assert session.execute(select(UnitFeeDecisionRow)).scalars().all() == []
            verified = read_original_frame_optimize_execution_batch(
                session,
                _reader_binding(session, task),
                crypto=capabilities.crypto,
                proof_authority=capabilities.proof_authority,
            )
            assert verified is not None
            assert verified.items[0].snapshot.server_quote == 0
    finally:
        engine.dispose()


def test_lost_commit_acknowledgement_confirms_graph_after_independent_cancel_write(
    tmp_path: Path,
) -> None:
    engine, sessions, capabilities, _crypto_state, ids, request = _build_optimize_case(
        tmp_path,
        frame_count=3,
    )
    cancellation_writer = SqlTaskCancellationWriter(engine.begin)

    class CommitThenCancelSession(Session):
        def commit(self) -> None:
            task_id = self.execute(
                select(AITaskRow.id).where(AITaskRow.type == "batch-optimize")
            ).scalar_one()
            super().commit()
            assert cancellation_writer.request_cancel(task_id)
            raise RuntimeError("fixture lost commit acknowledgement after cancellation")

    first_session = True

    def session_factory():
        nonlocal first_session
        if first_session:
            first_session = False
            return CommitThenCancelSession(bind=engine, expire_on_commit=False)
        return Session(bind=engine, expire_on_commit=False, autoflush=True)

    try:
        result = admit_frame_optimize(
            session_factory,
            _G2_JOINT_ACTOR_ID,
            request,
            registry_capabilities=capabilities,
            max_user_concurrency=8,
            id_factory=ids,
            clock=lambda: _G2_JOINT_NOW,
        )
        assert result["frame_count"] == 3
        with sessions() as session:
            task = session.get(AITaskRow, result["task_id"])
            assert task is not None
            assert task.status == "cancelling"
            assert len(session.execute(select(TaskSubmissionRow)).scalars().all()) == 1
            assert len(session.execute(select(BillingUnitRow)).scalars().all()) == 3
            assert len(session.execute(select(ExecutionStepRow)).scalars().all()) == 3
            assert len(session.execute(select(prepared_payloads)).mappings().all()) == 3
            assert session.execute(select(CreditLogRow)).scalars().all() == []
            verified = read_original_frame_optimize_execution_batch(
                session,
                _reader_binding(session, task),
                crypto=capabilities.crypto,
                proof_authority=capabilities.proof_authority,
            )
            assert verified is not None
            assert len(verified.items) == 3
    finally:
        engine.dispose()


def test_lost_commit_acknowledgement_rejects_wrong_task_model_name(
    tmp_path: Path,
) -> None:
    engine, sessions, capabilities, _crypto_state, ids, request = _build_optimize_case(
        tmp_path,
    )

    class CommitThenChangeModelNameSession(Session):
        def commit(self) -> None:
            task_id = self.execute(
                select(AITaskRow.id).where(AITaskRow.type == "batch-optimize")
            ).scalar_one()
            super().commit()
            with engine.begin() as connection:
                connection.execute(
                    update(AITaskRow.__table__)
                    .where(AITaskRow.__table__.c.id == task_id)
                    .values(model_name="changed-after-commit")
                )
            raise RuntimeError("fixture lost commit acknowledgement after model mutation")

    first_session = True

    def session_factory():
        nonlocal first_session
        if first_session:
            first_session = False
            return CommitThenChangeModelNameSession(bind=engine, expire_on_commit=False)
        return Session(bind=engine, expire_on_commit=False, autoflush=True)

    try:
        with pytest.raises(ChatTextGenerationError) as refusal:
            admit_frame_optimize(
                session_factory,
                _G2_JOINT_ACTOR_ID,
                request,
                registry_capabilities=capabilities,
                max_user_concurrency=8,
                id_factory=ids,
                clock=lambda: _G2_JOINT_NOW,
            )
        assert refusal.value.status_code == 503
        with sessions() as session:
            task = session.execute(select(AITaskRow)).scalar_one()
            assert task.model_name == "changed-after-commit"
            assert len(session.execute(select(TaskSubmissionRow)).scalars().all()) == 1
            assert len(session.execute(select(BillingUnitRow)).scalars().all()) == 1
            assert len(session.execute(select(ExecutionStepRow)).scalars().all()) == 1
            assert len(session.execute(select(prepared_payloads)).mappings().all()) == 1
            assert session.execute(select(CreditLogRow)).scalars().all() == []
    finally:
        engine.dispose()


def test_multi_frame_optimize_preserves_order_long_chapter_context_and_preapply_balance_rule(
    tmp_path: Path,
) -> None:
    engine, sessions, capabilities, _crypto_state, ids, request = _build_optimize_case(
        tmp_path,
        frame_count=60,
        request_indexes=(50, 2, 0),
        wallet_credits=3,
    )
    try:
        result = _admit(engine, sessions, capabilities, ids, request)
        assert result["frame_count"] == 3
        with engine.begin() as connection:
            connection.execute(
                update(system_models)
                .where(system_models.c.id == _G2_JOINT_MODEL_ID)
                .values(name="Changed after admission", model="changed-wire-model")
            )
            connection.execute(
                update(system_prompts)
                .where(system_prompts.c.id == "opt-prompt-1")
                .values(system_prompt="Changed after admission", user_prompt="Changed user prompt")
            )
        with sessions() as session:
            task = session.get(AITaskRow, result["task_id"])
            assert task is not None
            request_data = json.loads(task.request_data)
            assert request_data["frame_count"] == 3
            assert [item["frame_index"] for item in request_data["items"]] == [50, 2, 0]
            assert task.type == "batch-optimize"
            assert task.credit_cost == 0
            assert len(session.execute(select(TaskSubmissionRow)).scalars().all()) == 1
            units = session.execute(select(BillingUnitRow).where(BillingUnitRow.task_id == task.id)).scalars().all()
            steps = session.execute(select(ExecutionStepRow).where(ExecutionStepRow.billing_unit_id.in_([u.id for u in units]))).scalars().all()
            payloads = session.execute(select(prepared_payloads)).mappings().all()
            assert len(units) == len(steps) == len(payloads) == 3
            assert sum(unit.quoted_amount for unit in units) == 9
            assert all(unit.debited_amount == unit.refunded_amount == 0 for unit in units)
            assert all(unit.billing_status == "unbilled" for unit in units)
            assert all(step.execution_status == "prepared" for step in steps)
            assert session.execute(select(UserCreditRow.credits)).scalar_one() == 3
            assert session.execute(select(CreditLogRow)).scalars().all() == []
            verified = read_original_frame_optimize_execution_batch(
                session,
                _reader_binding(session, task),
                crypto=capabilities.crypto,
                proof_authority=capabilities.proof_authority,
            )
            assert verified is not None
            assert [item.frame_index for item in verified.items] == [50, 2, 0]
            assert [item.unit_id for item in verified.items] != sorted(
                item.unit_id for item in verified.items
            )
            assert [item.requested_text for item in verified.items] == [
                "requested frame 50",
                "requested frame 2",
                "requested frame 0",
            ]
            assert [item.database_text for item in verified.items] == [
                "database frame 50",
                "database frame 2",
                "database frame 0",
            ]
            context = json.loads(verified.items[0].snapshot.effective_context)
            assert context["request"]["previous_frames"] == [f"context {index}" for index in range(10, 50)]
            assert "context 9" not in context["request"]["previous_frames"]
            assert "context 50" not in context["request"]["previous_frames"]
            assert verified.items[0].model["wire_model"] == "wire-joint-fixture-model"
            assert verified.items[0].prompt["system_prompt"] == "Keep the scene coherent."
    finally:
        engine.dispose()


def test_thirty_frame_batch_reader_keeps_requested_order_past_ten_items(tmp_path: Path) -> None:
    request_order = tuple(range(29, -1, -1))
    engine, sessions, capabilities, _crypto_state, ids, request = _build_optimize_case(
        tmp_path,
        frame_count=60,
        request_indexes=request_order,
        wallet_credits=3,
    )
    try:
        result = _admit(engine, sessions, capabilities, ids, request)
        with sessions() as session:
            task = session.get(AITaskRow, result["task_id"])
            assert task is not None
            verified = read_original_frame_optimize_execution_batch(
                session,
                _reader_binding(session, task),
                crypto=capabilities.crypto,
                proof_authority=capabilities.proof_authority,
            )
            assert verified is not None
            assert [item.order for item in verified.items] == list(range(30))
            assert [item.frame_index for item in verified.items] == list(request_order)
            assert [item.requested_text for item in verified.items] == [
                f"requested frame {index}" for index in request_order
            ]
            assert len(session.execute(select(BillingUnitRow).where(BillingUnitRow.task_id == task.id)).scalars().all()) == 30
    finally:
        engine.dispose()


def test_second_same_chapter_admission_is_refused_without_extra_rows(tmp_path: Path) -> None:
    engine, sessions, capabilities, _crypto_state, ids, request = _build_optimize_case(tmp_path)
    try:
        _admit(engine, sessions, capabilities, ids, request)
        with pytest.raises(ChatTextGenerationError) as refusal:
            _admit(engine, sessions, capabilities, ids, request)
        assert refusal.value.status_code == 409
        with sessions() as session:
            assert len(session.execute(select(AITaskRow)).scalars().all()) == 1
            assert len(session.execute(select(TaskSubmissionRow)).scalars().all()) == 1
            assert len(session.execute(select(BillingUnitRow)).scalars().all()) == 1
            assert len(session.execute(select(ExecutionStepRow)).scalars().all()) == 1
            assert len(session.execute(select(prepared_payloads)).mappings().all()) == 1
            assert session.execute(select(UserCreditRow.credits)).scalar_one() == 500
    finally:
        engine.dispose()


def test_nth_payload_stage_failure_rolls_back_the_entire_batch(tmp_path: Path, monkeypatch) -> None:
    engine, sessions, capabilities, _crypto_state, ids, request = _build_optimize_case(
        tmp_path,
        frame_count=3,
    )
    original_stage = CredentialRegistry.stage_prepared_payload
    stage_calls = 0

    def fail_second_payload(self, session, payload, *, payload_id):
        nonlocal stage_calls
        stage_calls += 1
        if stage_calls == 2:
            return None
        return original_stage(self, session, payload, payload_id=payload_id)

    monkeypatch.setattr(CredentialRegistry, "stage_prepared_payload", fail_second_payload)
    try:
        with pytest.raises(ChatTextGenerationError) as refusal:
            _admit(engine, sessions, capabilities, ids, request)
        assert refusal.value.status_code == 503
        assert stage_calls == 2
        with sessions() as session:
            assert session.execute(select(AITaskRow)).scalars().all() == []
            assert session.execute(select(TaskSubmissionRow)).scalars().all() == []
            assert session.execute(select(BillingUnitRow)).scalars().all() == []
            assert session.execute(select(ExecutionStepRow)).scalars().all() == []
            assert session.execute(select(prepared_payloads)).mappings().all() == []
            assert session.execute(select(CreditLogRow)).scalars().all() == []
            assert session.execute(select(UserCreditRow.credits)).scalar_one() == 500
    finally:
        engine.dispose()


def test_commit_acknowledgement_loss_confirms_every_staged_item_from_fresh_session(
    tmp_path: Path,
) -> None:
    engine, sessions, capabilities, _crypto_state, ids, request = _build_optimize_case(
        tmp_path,
        frame_count=3,
    )
    first = True

    def session_factory():
        nonlocal first
        if first:
            first = False
            return _JointCommitThenUnknownSession(bind=engine, expire_on_commit=False)
        return Session(bind=engine, expire_on_commit=False, autoflush=True)

    try:
        result = admit_frame_optimize(
            session_factory,
            _G2_JOINT_ACTOR_ID,
            request,
            registry_capabilities=capabilities,
            max_user_concurrency=8,
            id_factory=ids,
            clock=lambda: _G2_JOINT_NOW,
        )
        assert result["frame_count"] == 3
        with sessions() as session:
            task = session.get(AITaskRow, result["task_id"])
            assert task is not None
            verified = read_original_frame_optimize_execution_batch(
                session,
                _reader_binding(session, task),
                crypto=capabilities.crypto,
                proof_authority=capabilities.proof_authority,
            )
            assert verified is not None
            assert len(verified.items) == 3
            assert len(session.execute(select(BillingUnitRow).where(BillingUnitRow.task_id == task.id)).scalars().all()) == 3
            assert len(session.execute(select(ExecutionStepRow).where(ExecutionStepRow.billing_unit_id.in_([item.unit_id for item in verified.items]))).scalars().all()) == 3
            assert len(session.execute(select(prepared_payloads)).mappings().all()) == 3
            assert session.execute(select(CreditLogRow)).scalars().all() == []
    finally:
        engine.dispose()


def test_reader_rejects_reordered_manifest_and_missing_sibling(tmp_path: Path) -> None:
    engine, sessions, capabilities, _crypto_state, ids, request = _build_optimize_case(
        tmp_path,
        frame_count=3,
    )
    try:
        result = _admit(engine, sessions, capabilities, ids, request)
        with sessions() as session:
            task = session.get(AITaskRow, result["task_id"])
            assert task is not None
            original_request_data = task.request_data
            request_data = json.loads(original_request_data)
            request_data["items"].reverse()
            task.request_data = json.dumps(request_data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            reordered_binding = _reader_binding(session, task)
            assert read_original_frame_optimize_execution_batch(
                session,
                reordered_binding,
                crypto=capabilities.crypto,
                proof_authority=capabilities.proof_authority,
            ) is None
            task.request_data = original_request_data
            session.commit()

        from haoai_backend.generation_credentials.schema import SQLITE_GUARD_TABLES

        payload_id = json.loads(original_request_data)["items"][1]["prepared_payload_id"]
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP TRIGGER IF EXISTS trg_generation_prepared_payloads_immutable_delete")
            connection.execute(prepared_payloads.delete().where(prepared_payloads.c.prepared_payload_id == payload_id))
        assert "trg_generation_prepared_payloads_immutable_delete" in SQLITE_GUARD_TABLES
        with sessions() as session:
            task = session.get(AITaskRow, result["task_id"])
            assert task is not None
            assert read_original_frame_optimize_execution_batch(
                session,
                _reader_binding(session, task),
                crypto=capabilities.crypto,
                proof_authority=capabilities.proof_authority,
            ) is None
    finally:
        engine.dispose()
