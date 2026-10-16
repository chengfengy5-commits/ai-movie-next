"""Reopen the protected frame.review input from its committed SQL graph."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import sessionmaker

from haoai_backend.generation_catalog.tables import system_models
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    ExecutionStepRow,
    TaskSubmissionRow,
)
from haoai_backend.generation_credentials.frame_review_snapshot_reader import (
    PersistedFrameReviewExecutionBinding,
    read_original_frame_review_execution_input,
)
from haoai_backend.generation_credentials import frame_review_snapshot_reader
from haoai_backend.generation_credentials.application import CredentialRegistry
from haoai_backend.generation_credentials.persistence import prepared_payloads
from haoai_backend.prompts.tables import system_prompts

from generation_execution.test_frame_review_admission import (
    _build_app,
    _post_review,
    _review_body,
)


def _committed_binding(engine, task_id: str) -> PersistedFrameReviewExecutionBinding:
    with engine.connect() as connection:
        task = connection.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == task_id)
        ).mappings().one()
        unit = connection.execute(
            select(BillingUnitRow.__table__).where(
                BillingUnitRow.__table__.c.task_id == task_id
            )
        ).mappings().one()
        step = connection.execute(
            select(ExecutionStepRow.__table__).where(
                ExecutionStepRow.__table__.c.billing_unit_id == unit["id"]
            )
        ).mappings().one()
        submission = connection.execute(
            select(TaskSubmissionRow.__table__).where(
                TaskSubmissionRow.__table__.c.task_ids.contains([task_id])
            )
        ).mappings().one()
    request_data = json.loads(task["request_data"])
    return PersistedFrameReviewExecutionBinding(
        owner_id=task["user_id"],
        task_id=task["id"],
        unit_id=unit["id"],
        step_id=step["id"],
        submission_id=submission["id"],
        raw_key=submission["idempotency_key"],
        task_message_id=task["message_id"],
        task_model_name=task["model_name"],
        task_model_id=request_data["model_id"],
        task_model_source=request_data["model_source"],
        task_model_owner_id=request_data["model_owner_id"],
        request_digest=request_data["request_digest"],
        rule_digest=request_data["rule_digest"],
        snapshot_digest=request_data["snapshot_digest"],
        manifest_snapshot_digest=request_data["manifest_snapshot_digest"],
        server_attempt_id=request_data["server_attempt_id"],
        prepared_payload_id=request_data["prepared_payload_id"],
        protected_ref=request_data["protected_ref"],
        quoted_amount=unit["quoted_amount"],
    )


def _admit(
    tmp_path: Path,
    monkeypatch,
    *,
    rotate_credentials: bool = False,
    **admission_options: object,
):
    engine, app, token, capabilities, crypto_state = _build_app(
        tmp_path,
        monkeypatch,
        rotate_credentials=rotate_credentials,
        **admission_options,
    )
    response = asyncio.run(_post_review(app, token))
    assert response.status_code == 200, response.text
    binding = _committed_binding(engine, response.json()["task_id"])
    return engine, binding, capabilities, crypto_state


def test_committed_review_reopens_in_a_new_session_without_resolving_provider_key(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, binding, capabilities, crypto_state = _admit(
        tmp_path,
        monkeypatch,
        rotate_credentials=True,
    )
    sessions = sessionmaker(engine, expire_on_commit=False)
    secret_open_calls_before = crypto_state["unseal_calls"]
    secret_handle_calls_before = crypto_state["secret_handle_calls"]
    legacy_value_calls_before = crypto_state["legacy_value_calls"]
    try:
        with sessions() as reopened_session:
            reopened = read_original_frame_review_execution_input(
                reopened_session,
                binding,
                crypto=capabilities.crypto,
                proof_authority=capabilities.proof_authority,
            )

        assert reopened is not None
        assert reopened.task_id == binding.task_id
        assert reopened.snapshot.target_snapshot.compat_frame_index == 0
        assert reopened.original_text == "原始分镜正文"
        assert reopened.original_frame["custom"] == {"keep": True}
        assert reopened.snapshot.server_quote == 3
        assert not hasattr(reopened, "provider_credential")
        rendered = repr(reopened)
        for private_marker in (
            "原始分镜正文",
            "请审阅分镜文本。",
            "请核实动作方向",
            "角色站在门口",
        ):
            assert private_marker not in rendered
        assert "原始分镜正文" in reopened.snapshot.effective_context
        assert crypto_state["unseal_calls"] - secret_open_calls_before == 1
        assert crypto_state["secret_handle_calls"] == secret_handle_calls_before
        assert crypto_state["legacy_value_calls"] == legacy_value_calls_before
    finally:
        engine.dispose()


def test_review_reopen_rejects_a_tampered_planner_snapshot_digest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, binding, capabilities, _ = _admit(tmp_path, monkeypatch)
    tampered_digest = "0" * 64
    with engine.begin() as connection:
        task = connection.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == binding.task_id)
        ).mappings().one()
        request_data = json.loads(task["request_data"])
        request_data["snapshot_digest"] = tampered_digest
        connection.execute(
            update(AITaskRow.__table__)
            .where(AITaskRow.id == binding.task_id)
            .values(request_data=json.dumps(request_data, ensure_ascii=False, sort_keys=True))
        )
        step = connection.execute(
            select(ExecutionStepRow.__table__).where(
                ExecutionStepRow.__table__.c.id == binding.step_id
            )
        ).mappings().one()
        input_snapshot = dict(step["input_snapshot"])
        input_snapshot["snapshot_digest"] = tampered_digest
        connection.execute(
            update(ExecutionStepRow.__table__)
            .where(ExecutionStepRow.__table__.c.id == binding.step_id)
            .values(input_snapshot=input_snapshot)
        )

    corrupted_binding = replace(binding, snapshot_digest=tampered_digest)
    try:
        with sessionmaker(engine)() as reopened_session:
            reopened = read_original_frame_review_execution_input(
                reopened_session,
                corrupted_binding,
                crypto=capabilities.crypto,
                proof_authority=capabilities.proof_authority,
            )
        assert reopened is None
    finally:
        engine.dispose()


@pytest.mark.parametrize("tamper", ["step_only", "request_step_and_binding"])
def test_review_reopen_rejects_a_tampered_manifest_snapshot_digest(
    tmp_path: Path,
    monkeypatch,
    tamper: str,
) -> None:
    engine, binding, capabilities, _ = _admit(tmp_path, monkeypatch)
    tampered_digest = "f" * 64
    with engine.begin() as connection:
        task = connection.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == binding.task_id)
        ).mappings().one()
        request_data = json.loads(task["request_data"])
        step = connection.execute(
            select(ExecutionStepRow.__table__).where(
                ExecutionStepRow.__table__.c.id == binding.step_id
            )
        ).mappings().one()
        input_snapshot = dict(step["input_snapshot"])
        input_snapshot["manifest_snapshot_digest"] = tampered_digest
        connection.execute(
            update(ExecutionStepRow.__table__)
            .where(ExecutionStepRow.__table__.c.id == binding.step_id)
            .values(input_snapshot=input_snapshot)
        )
        if tamper == "request_step_and_binding":
            request_data["manifest_snapshot_digest"] = tampered_digest
            connection.execute(
                update(AITaskRow.__table__)
                .where(AITaskRow.id == binding.task_id)
                .values(request_data=json.dumps(request_data, ensure_ascii=False, sort_keys=True))
            )

    candidate = (
        replace(binding, manifest_snapshot_digest=tampered_digest)
        if tamper == "request_step_and_binding"
        else binding
    )
    try:
        with sessionmaker(engine)() as reopened_session:
            reopened = read_original_frame_review_execution_input(
                reopened_session,
                candidate,
                crypto=capabilities.crypto,
                proof_authority=capabilities.proof_authority,
            )
        assert reopened is None
    finally:
        engine.dispose()


@pytest.mark.parametrize("legacy_shape", ["missing_field", "extra_step_key"])
def test_review_reopen_fails_closed_for_legacy_or_noncanonical_step_snapshot(
    tmp_path: Path,
    monkeypatch,
    legacy_shape: str,
) -> None:
    engine, binding, capabilities, _ = _admit(tmp_path, monkeypatch)
    with engine.begin() as connection:
        task = connection.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == binding.task_id)
        ).mappings().one()
        request_data = json.loads(task["request_data"])
        step = connection.execute(
            select(ExecutionStepRow.__table__).where(
                ExecutionStepRow.__table__.c.id == binding.step_id
            )
        ).mappings().one()
        input_snapshot = dict(step["input_snapshot"])
        if legacy_shape == "missing_field":
            request_data.pop("manifest_snapshot_digest")
            input_snapshot.pop("manifest_snapshot_digest")
            connection.execute(
                update(AITaskRow.__table__)
                .where(AITaskRow.id == binding.task_id)
                .values(request_data=json.dumps(request_data, ensure_ascii=False, sort_keys=True))
            )
        else:
            input_snapshot["unexpected"] = "value"
        connection.execute(
            update(ExecutionStepRow.__table__)
            .where(ExecutionStepRow.__table__.c.id == binding.step_id)
            .values(input_snapshot=input_snapshot)
        )

    try:
        with sessionmaker(engine)() as reopened_session:
            reopened = read_original_frame_review_execution_input(
                reopened_session,
                binding,
                crypto=capabilities.crypto,
                proof_authority=capabilities.proof_authority,
            )
        assert reopened is None
    finally:
        engine.dispose()


def test_review_reopens_the_original_model_and_prompt_after_catalog_changes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, binding, capabilities, _ = _admit(
        tmp_path,
        monkeypatch,
        rotate_credentials=True,
    )
    with engine.begin() as connection:
        connection.execute(
            update(system_models)
            .where(system_models.c.id == binding.task_model_id)
            .values(
                name="Replaced model name",
                model="replaced-wire-model",
                params=json.dumps({"temperature": 0.9}),
            )
        )
        connection.execute(
            update(system_prompts)
            .where(system_prompts.c.id == "review-prompt-1")
            .values(
                system_prompt="替换后的系统提示",
                user_prompt="替换后的用户提示",
            )
        )

    try:
        with sessionmaker(engine)() as reopened_session:
            reopened = read_original_frame_review_execution_input(
                reopened_session,
                binding,
                crypto=capabilities.crypto,
                proof_authority=capabilities.proof_authority,
            )

        assert reopened is not None
        assert reopened.model["wire_model"] == "review-wire-model"
        assert reopened.prompt["system_prompt"] == "请审阅分镜文本。"
        assert reopened.prompt["user_prompt"] == "请保持人物和场景连续。"
    finally:
        engine.dispose()


def test_review_reopens_nullable_wire_model_from_frozen_display_name(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, app, token, capabilities, _ = _build_app(tmp_path, monkeypatch)
    model_id = _review_body()["model_id"]
    with engine.begin() as connection:
        connection.execute(
            update(system_models)
            .where(system_models.c.id == model_id)
            .values(model=None)
        )

    try:
        response = asyncio.run(_post_review(app, token))
        assert response.status_code == 200, response.text
        binding = _committed_binding(engine, response.json()["task_id"])
        assert len(binding.snapshot_digest) == len(binding.manifest_snapshot_digest) == 64
        assert binding.snapshot_digest != binding.manifest_snapshot_digest
        with sessionmaker(engine)() as reopened_session:
            reopened = read_original_frame_review_execution_input(
                reopened_session,
                binding,
                crypto=capabilities.crypto,
                proof_authority=capabilities.proof_authority,
            )

        assert reopened is not None
        assert reopened.model["wire_model"] is None
        assert reopened.snapshot.model_name == "Public joint-fixture model"

        with engine.begin() as connection:
            connection.execute(
                update(system_models)
                .where(system_models.c.id == model_id)
                .values(name="Later catalog name", model="later-wire-model")
            )
        with sessionmaker(engine)() as reopened_session:
            recovered_after_catalog_change = read_original_frame_review_execution_input(
                reopened_session,
                binding,
                crypto=capabilities.crypto,
                proof_authority=capabilities.proof_authority,
            )

        assert recovered_after_catalog_change is not None
        assert recovered_after_catalog_change.model["wire_model"] is None
        assert recovered_after_catalog_change.snapshot.model_name == "Public joint-fixture model"
    finally:
        engine.dispose()


def test_review_reader_rejects_an_invalid_wire_model_type(
    tmp_path: Path,
    monkeypatch,
) -> None:
    original_stage = CredentialRegistry.stage_prepared_payload

    def stage_invalid_wire_model(self, session, payload, *, payload_id):
        if payload.operation == "frame.review":
            value = json.loads(payload.ciphertext.decode("utf-8"))
            value["model"]["wire_model"] = ["invalid"]
            corrupted_plaintext = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            payload = replace(payload, ciphertext=corrupted_plaintext)
        return original_stage(self, session, payload, payload_id=payload_id)

    monkeypatch.setattr(
        CredentialRegistry,
        "stage_prepared_payload",
        stage_invalid_wire_model,
    )
    engine, binding, capabilities, _ = _admit(tmp_path, monkeypatch)
    try:
        with sessionmaker(engine)() as reopened_session:
            reopened = read_original_frame_review_execution_input(
                reopened_session,
                binding,
                crypto=capabilities.crypto,
                proof_authority=capabilities.proof_authority,
            )
        assert reopened is None
    finally:
        engine.dispose()


def test_review_reopens_with_the_original_empty_request_prompt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, app, token, capabilities, _ = _build_app(tmp_path, monkeypatch)
    try:
        response = asyncio.run(
            _post_review(
                app,
                token,
                {**_review_body(), "prompt_id": ""},
            )
        )
        assert response.status_code == 200, response.text
        binding = _committed_binding(engine, response.json()["task_id"])
        with sessionmaker(engine)() as reopened_session:
            reopened = read_original_frame_review_execution_input(
                reopened_session,
                binding,
                crypto=capabilities.crypto,
                proof_authority=capabilities.proof_authority,
            )

        assert reopened is not None
        assert reopened.snapshot.prompt_ref is None
        assert reopened.snapshot.effective_prompt == ""
        assert reopened.prompt["source"] == "request"
        assert reopened.prompt["user_prompt"] == ""
    finally:
        engine.dispose()


def test_no_id_frame_reopens_without_a_storyboard_asset(
    tmp_path: Path,
    monkeypatch,
) -> None:
    chapter_content = [
        {"text": "无稳定ID的原始帧", "storyboard": [], "notes": ["keep"]}
    ]
    engine, binding, capabilities, _ = _admit(
        tmp_path,
        monkeypatch,
        chapter_content=chapter_content,
        storyboard_asset_ids=(),
    )
    planner_targets: list[str] = []
    original_planner = frame_review_snapshot_reader.build_operation_graph_plan

    def capture_planner_target(*args, **kwargs):
        planner_targets.extend(item.target_id for item in kwargs["items"])
        return original_planner(*args, **kwargs)

    monkeypatch.setattr(
        frame_review_snapshot_reader,
        "build_operation_graph_plan",
        capture_planner_target,
    )
    try:
        with sessionmaker(engine)() as reopened_session:
            reopened = read_original_frame_review_execution_input(
                reopened_session,
                binding,
                crypto=capabilities.crypto,
                proof_authority=capabilities.proof_authority,
            )
        assert reopened is not None
        assert reopened.snapshot.target_snapshot.stable_storyboard_asset_id is None
        assert reopened.original_text == "无稳定ID的原始帧"
        assert reopened.original_frame["notes"] == ("keep",)
        assert planner_targets == [f"{binding.protected_ref}#target"]
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "tamper",
    ["ciphertext", "key_id", "command_proof", "owner"],
)
def test_review_reopen_rejects_tampered_protected_binding(
    tmp_path: Path,
    monkeypatch,
    tamper: str,
) -> None:
    original_stage = CredentialRegistry.stage_prepared_payload

    def stage_corrupted_payload(self, session, payload, *, payload_id):
        if payload.operation == "frame.review" and tamper == "ciphertext":
            payload = replace(payload, ciphertext=b"tampered-protected-input")
        elif payload.operation == "frame.review" and tamper == "key_id":
            payload = replace(payload, encryption_key_id="unknown-test-key")
        elif payload.operation == "frame.review" and tamper == "command_proof":
            payload = replace(payload, command_proof="0" * 64)
        return original_stage(self, session, payload, payload_id=payload_id)

    monkeypatch.setattr(
        CredentialRegistry,
        "stage_prepared_payload",
        stage_corrupted_payload,
    )
    engine, binding, capabilities, _ = _admit(tmp_path, monkeypatch)
    if tamper == "owner":
        binding = replace(binding, owner_id="different-owner")

    try:
        with sessionmaker(engine)() as reopened_session:
            reopened = read_original_frame_review_execution_input(
                reopened_session,
                binding,
                crypto=capabilities.crypto,
                proof_authority=capabilities.proof_authority,
            )
        assert reopened is None
    finally:
        engine.dispose()
