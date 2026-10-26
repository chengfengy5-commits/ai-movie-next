from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
import test_admission_staging as joint_fixtures
from sqlalchemy import event, insert, select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_catalog.domain import ModelSource
from haoai_backend.generation_catalog.tables import (
    ASSET_TABLES,
    chapters as catalog_chapters,
    model_configs,
    system_models,
)
from haoai_backend.generation_core.admission_receipts import AdmissionStaged
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    ExecutionStepRow,
    TaskSubmissionRow,
    UserRow,
    UserCreditRow,
)
from haoai_backend.generation_credentials.application import build_credential_registry
from haoai_backend.generation_credentials.domain import (
    CredentialCrypto,
    HmacProofAuthority,
    SealedMaterial,
)
from haoai_backend.generation_credentials.persistence import prepared_payloads
from haoai_backend.generation_credentials.snapshot_producer import (
    canonical_payload_json,
    stored_payload_associated_data,
)
from haoai_backend.generation_credentials.video_execution_snapshot import (
    PersistedVideoExecutionBinding,
    read_original_video_single_execution_input,
)
from test_admission_staging import (
    _joint_caller,
    _joint_prepare_and_stage,
    _observe_joint_public_paths,
)


def _authenticated_joint_crypto(
    secrets_by_reference: dict[str, str],
) -> tuple[CredentialCrypto, dict[str, object]]:
    """Supply a test-only authenticated cipher so tampering cannot pass by accident."""

    encryption_key = b"video-execution-reader-test-encryption"
    guard_key = b"video-execution-reader-test-guard"
    state: dict[str, object] = {
        "seal_aads": [],
        "unseal_calls": 0,
        "secrets_by_reference": secrets_by_reference,
    }

    def stream(nonce: bytes, associated_data: bytes, size: int) -> bytes:
        blocks: list[bytes] = []
        counter = 0
        while sum(map(len, blocks)) < size:
            blocks.append(
                hashlib.sha256(
                    encryption_key + nonce + associated_data + counter.to_bytes(4, "big")
                ).digest()
            )
            counter += 1
        return b"".join(blocks)[:size]

    def seal(secret: str, associated_data: bytes) -> SealedMaterial:
        nonce = uuid.uuid4().bytes[:12]
        plaintext = secret.encode("utf-8")
        ciphertext = bytes(
            left ^ right
            for left, right in zip(
                plaintext,
                stream(nonce, associated_data, len(plaintext)),
                strict=True,
            )
        )
        tag = hmac.new(
            encryption_key,
            b"video-reader-fixture-tag" + nonce + associated_data + ciphertext,
            hashlib.sha256,
        ).digest()
        seal_aads = state["seal_aads"]
        assert isinstance(seal_aads, list)
        seal_aads.append(bytes(associated_data))
        return SealedMaterial(tag + ciphertext, nonce, "video-reader-seal-v1", "video-reader-aead-v1")

    def unseal(material: SealedMaterial, associated_data: bytes) -> str | None:
        state["unseal_calls"] = int(state["unseal_calls"]) + 1
        if material.key_id != "video-reader-seal-v1" or len(material.ciphertext) < 32:
            return None
        tag, ciphertext = material.ciphertext[:32], material.ciphertext[32:]
        expected_tag = hmac.new(
            encryption_key,
            b"video-reader-fixture-tag" + material.nonce + associated_data + ciphertext,
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(tag, expected_tag):
            return None
        plaintext = bytes(
            left ^ right
            for left, right in zip(
                ciphertext,
                stream(material.nonce, associated_data, len(ciphertext)),
                strict=True,
            )
        )
        return plaintext.decode("utf-8")

    def guard(secret: str, associated_data: bytes) -> str:
        return hmac.new(
            guard_key,
            associated_data + b"\0" + secret.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    return (
        CredentialCrypto(
            seal_key_id="video-reader-seal-v1",
            guard_key_id="video-reader-guard-v1",
            algorithm_version="video-reader-aead-v1",
            seal=seal,
            unseal=unseal,
            consistency_guard=guard,
            resolve_secret_handle=secrets_by_reference.get,
            resolve_legacy_config_value=lambda stored: stored or None,
            self_test=lambda: True,
        ),
        state,
    )


def _video_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    model_source: ModelSource = ModelSource.SYSTEM,
    wallet_credits: int | None = 500,
    per_second_cost: int = 1,
):
    monkeypatch.setattr(joint_fixtures, "_G2_JOINT_PROVIDER", "xai")
    monkeypatch.setattr(joint_fixtures, "_joint_local_dummy_crypto", _authenticated_joint_crypto)
    insert_catalog_model = joint_fixtures._joint_insert_catalog_model

    def insert_video_model(connection, model_source: ModelSource) -> None:
        insert_catalog_model(connection, model_source)
        table = system_models if model_source is ModelSource.SYSTEM else model_configs
        connection.execute(
            update(table)
            .where(table.c.id == joint_fixtures._G2_JOINT_MODEL_ID)
            .values(
                category="video",
                model="grok-imagine-video",
                params=json.dumps(
                    {
                        "billing_mode": "per_second",
                        "per_second_cost": per_second_cost,
                        "duration": {"enabled": True, "default": 5, "options": [5]},
                        "resolution": {
                            "enabled": True,
                            "default": "720p",
                            "options": ["720p"],
                        },
                        "image_size": {
                            "enabled": True,
                            "default": "16:9",
                            "options": ["16:9"],
                        },
                    }
                ),
            )
        )

    monkeypatch.setattr(joint_fixtures, "_joint_insert_catalog_model", insert_video_model)
    return joint_fixtures._joint_environment(
        tmp_path,
        model_source=model_source,
        operation="video.single",
        wallet_credits=wallet_credits,
    )


def _commit_video_request(environment, *, raw_key: str, request: dict[str, object]):
    caller = _joint_caller(environment.engine)
    try:
        staged = _joint_prepare_and_stage(
            environment.binding,
            caller,
            operation="video.single",
            raw_key=raw_key,
            request=request,
        )
        assert isinstance(staged, AdmissionStaged)
        task_id = staged.ordered_tasks[0].task_id
        caller.commit()
        with Session(environment.engine) as reader:
            return _video_binding_from_committed_rows(
                reader,
                task_id=task_id,
                raw_key=raw_key,
            )
    finally:
        caller.close()


def _video_request() -> dict[str, object]:
    return {
        "model_id": joint_fixtures._G2_JOINT_MODEL_ID,
        "prompt": "A quiet room at sunset",
        "target": {"kind": "standalone"},
        "size": "16:9",
        "duration": 5,
        "resolution": "720p",
        "reference_images": [
            "https://ref.example.test/image-a?ordered=1",
            "https://ref.example.test/image-b?ordered=2",
            "https://ref.example.test/image-a?ordered=1",
        ],
        "reference_audios": [
            "https://ref.example.test/audio-a?ordered=1",
            "https://ref.example.test/audio-b?ordered=2",
            "https://ref.example.test/audio-a?ordered=1",
        ],
        "reference_videos": [
            "https://ref.example.test/video-a?ordered=1",
            "https://ref.example.test/video-b?ordered=2",
            "https://ref.example.test/video-a?ordered=1",
        ],
    }


def _seed_video_storyboard_asset(
    environment,
    *,
    asset_id: str,
    chapter_content: str,
) -> str:
    with environment.engine.begin() as connection:
        connection.execute(
            update(catalog_chapters)
            .where(catalog_chapters.c.id == "joint-chapter-1")
            .values(content=chapter_content)
        )
        connection.execute(
            insert(ASSET_TABLES["storyboard"]).values(
                id=asset_id,
                series_id="joint-series-1",
                chapter_id="joint-chapter-1",
                frame_index=0,
                name="Fixture frame",
                description="Stable storyboard frame for video input",
                image_url=None,
                updated_at=joint_fixtures._G2_JOINT_NOW.replace(tzinfo=None),
            )
        )
    return hashlib.sha256(chapter_content.encode("utf-8")).hexdigest()


def _video_binding_from_committed_rows(
    session: Session,
    *,
    task_id: str,
    raw_key: str,
) -> PersistedVideoExecutionBinding:
    task = session.execute(
        select(AITaskRow.__table__).where(AITaskRow.id == task_id)
    ).mappings().one()
    request_data = json.loads(task["request_data"])
    unit = session.execute(
        select(BillingUnitRow.__table__).where(BillingUnitRow.task_id == task_id)
    ).mappings().one()
    step = session.execute(
        select(ExecutionStepRow.__table__).where(
            ExecutionStepRow.billing_unit_id == unit["id"]
        )
    ).mappings().one()
    submission = session.execute(
        select(TaskSubmissionRow.__table__).where(
            TaskSubmissionRow.id == request_data["submission_id"]
        )
    ).mappings().one()
    snapshot = step["input_snapshot"]
    assert submission["task_ids"] == [task_id]
    return PersistedVideoExecutionBinding(
        owner_id=task["user_id"],
        task_id=task["id"],
        unit_id=unit["id"],
        step_id=step["id"],
        submission_id=submission["id"],
        raw_key=raw_key,
        operation=submission["operation"],
        graph_version=unit["step_graph_version"],
        task_type=task["type"],
        task_model_id=request_data["model_id"],
        task_model_source=request_data["model_source"],
        task_model_owner_id=request_data["model_owner_id"],
        task_model_name=task["model_name"],
        request_digest=request_data["request_digest"],
        rule_digest=request_data["rule_digest"],
        snapshot_digest=request_data["snapshot_digest"],
        server_attempt_id=snapshot["server_attempt_id"],
        prepared_payload_id=snapshot["prepared_payload_id"],
        protected_ref=snapshot["protected_ref"],
        ordered_manifest_digest=snapshot["ordered_manifest_digest"],
        quoted_amount=unit["quoted_amount"],
        task_message_id=task["message_id"] or None,
    )


def _read_video_payload_state(
    engine, binding: PersistedVideoExecutionBinding
) -> tuple[dict[str, object], dict[str, object]]:
    with Session(engine) as session:
        payload = dict(
            session.execute(
                select(prepared_payloads).where(
                    prepared_payloads.c.prepared_payload_id
                    == binding.prepared_payload_id
                )
            ).mappings().one()
        )
        snapshot = dict(
            session.execute(
                select(ExecutionStepRow.__table__.c.input_snapshot).where(
                    ExecutionStepRow.__table__.c.id == binding.step_id
                )
            ).scalar_one()
        )
    return payload, snapshot


def _store_authenticated_video_payload_variant(
    engine,
    binding: PersistedVideoExecutionBinding,
    *,
    crypto: CredentialCrypto,
    proof_authority: HmacProofAuthority,
    mutate: Callable[[dict[str, object]], None],
) -> tuple[PersistedVideoExecutionBinding, dict[str, object], dict[str, object]]:
    original_row, original_step_snapshot = _read_video_payload_state(engine, binding)
    original_aad = stored_payload_associated_data(
        original_row,
        parent_attempt_id=binding.submission_id,
        protected_ref=binding.protected_ref,
        item_index=0,
        ordered_count=1,
        ordered_manifest_digest=binding.ordered_manifest_digest,
    )
    original_material = SealedMaterial(
        ciphertext=original_row["ciphertext"],
        nonce=original_row["nonce"],
        key_id=original_row["encryption_key_id"],
        algorithm_version=crypto.algorithm_version,
    )
    plaintext = crypto.open_secret(original_material, original_aad)
    assert plaintext is not None
    payload = json.loads(plaintext)
    assert isinstance(payload, dict)
    mutate(payload)
    item = payload["item"]
    manifest = payload["command_manifest"]
    assert isinstance(item, dict) and isinstance(manifest, dict)

    item_digest = hashlib.sha256(canonical_payload_json(item)).hexdigest()
    manifest["item_digests"] = [item_digest]
    payload["ordered_manifest"] = [
        {
            "index": 0,
            "child_attempt_id": binding.server_attempt_id,
            "payload_id": binding.prepared_payload_id,
            "protected_ref": binding.protected_ref,
            "item_digest": item_digest,
        }
    ]
    manifest_bytes = canonical_payload_json(manifest)
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    proof = proof_authority.sign("prepared-generation-input-v1", manifest_bytes)
    payload["ordered_manifest_digest"] = manifest_digest
    payload["manifest_proof"] = proof
    assert proof_authority.verify("prepared-generation-input-v1", manifest_bytes, proof)

    row_for_aad = {**original_row, "command_proof": proof}
    associated_data = stored_payload_associated_data(
        row_for_aad,
        parent_attempt_id=binding.submission_id,
        protected_ref=binding.protected_ref,
        item_index=0,
        ordered_count=1,
        ordered_manifest_digest=manifest_digest,
    )
    sealed = crypto.seal_secret(
        canonical_payload_json(payload).decode("utf-8"),
        associated_data,
    )
    assert crypto.open_secret(sealed, associated_data) is not None
    updated_step_snapshot = {
        **original_step_snapshot,
        "ordered_manifest_digest": manifest_digest,
    }
    with engine.begin() as connection:
        connection.execute(
            update(prepared_payloads)
            .where(
                prepared_payloads.c.prepared_payload_id
                == binding.prepared_payload_id
            )
            .values(
                command_proof=proof,
                ciphertext=sealed.ciphertext,
                nonce=sealed.nonce,
            )
        )
        connection.execute(
            update(ExecutionStepRow.__table__)
            .where(ExecutionStepRow.__table__.c.id == binding.step_id)
            .values(input_snapshot=updated_step_snapshot)
        )
    return (
        replace(binding, ordered_manifest_digest=manifest_digest),
        original_row,
        original_step_snapshot,
    )


def _restore_video_payload_state(
    engine,
    binding: PersistedVideoExecutionBinding,
    *,
    payload: dict[str, object],
    step_snapshot: dict[str, object],
) -> None:
    with engine.begin() as connection:
        connection.execute(
            update(prepared_payloads)
            .where(
                prepared_payloads.c.prepared_payload_id
                == binding.prepared_payload_id
            )
            .values(
                command_proof=payload["command_proof"],
                ciphertext=payload["ciphertext"],
                nonce=payload["nonce"],
            )
        )
        connection.execute(
            update(ExecutionStepRow.__table__)
            .where(ExecutionStepRow.__table__.c.id == binding.step_id)
            .values(input_snapshot=step_snapshot)
        )


def test_real_video_joint_fixture_stages_and_commits_the_frozen_g1_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    environment = _video_environment(monkeypatch, tmp_path)
    caller = _joint_caller(environment.engine)
    observed = _observe_joint_public_paths(
        monkeypatch,
        authentication_sessions=environment.authentication_sessions,
    )
    raw_key = "original-video-single-key"
    source_request = _video_request()
    try:
        staged = _joint_prepare_and_stage(
            environment.binding,
            caller,
            operation="video.single",
            raw_key=raw_key,
            request=source_request,
        )
        assert isinstance(staged, AdmissionStaged)
        assert len(observed.a2_calls) == 1
        assert len(observed.producer_sessions) == 1
        # The staging verifier is reserved for unknown-commit durable lookup.
        assert observed.verifier_sessions == []
        assert len(observed.graph_plans) == 1
        assert len(observed.fee_sessions) == 1
        assert observed.graph_plans[0].graph_version == "video-single-v1"
        assert observed.graph_plans[0].items[0].snapshot.model_category == "video"

        task_id = staged.ordered_tasks[0].task_id
        caller.commit()

        source_request["prompt"] = "Changed after admission"
        source_request["model_id"] = "changed-current-model"
        with environment.engine.begin() as current_catalog:
            current_catalog.execute(
                update(system_models)
                .where(system_models.c.id == joint_fixtures._G2_JOINT_MODEL_ID)
                .values(
                    model="changed-current-wire-model",
                    params=json.dumps({"billing_mode": "per_request", "price": 999}),
                )
            )

        with Session(environment.engine) as reader:
            task = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == task_id)
            ).mappings().one()
            request_data = json.loads(task["request_data"])
            unit = reader.execute(
                select(BillingUnitRow.__table__).where(BillingUnitRow.task_id == task_id)
            ).mappings().one()
            step = reader.execute(
                select(ExecutionStepRow.__table__).where(
                    ExecutionStepRow.billing_unit_id == unit["id"]
                )
            ).mappings().one()
            submission = reader.execute(
                select(TaskSubmissionRow.__table__).where(
                    TaskSubmissionRow.id == request_data["submission_id"]
                )
            ).mappings().one()
            payload_row = dict(
                reader.execute(
                    select(prepared_payloads).where(
                        prepared_payloads.c.prepared_payload_id
                        == request_data["prepared_payload_id"]
                    )
                ).mappings().one()
            )
            debit = reader.execute(
                select(CreditLogRow.__table__).where(
                    CreditLogRow.__table__.c.business_key == f"debit:{unit['id']}"
                )
            ).mappings().one()
            wallet = reader.execute(
                select(UserCreditRow.__table__.c.credits).where(
                    UserCreditRow.__table__.c.user_id == joint_fixtures._G2_JOINT_ACTOR_ID
                )
            ).scalar_one()

            assert submission["task_ids"] == [task_id]
            assert submission["operation"] == "video.single"
            assert unit["operation"] == "video.single"
            assert unit["step_graph_version"] == "video-single-v1"
            assert unit["quoted_amount"] == 5
            assert unit["debited_amount"] == 5
            assert unit["refunded_amount"] == 0
            assert debit["amount"] == -5
            assert debit["balance_after"] == 495
            assert debit["type"] == "usage"
            assert debit["task_id"] == task_id
            assert debit["billing_unit_id"] == unit["id"]
            assert wallet == 495
            assert task["type"] == "video-single"
            assert step["step_key"] == "generate"
            assert step["step_order"] == 0
            assert step["required"] is True
            assert step["dependencies"] == []
            assert payload_row["provider"] == "xai"
            assert payload_row["operation"] == "video.single"
            assert payload_row["server_attempt_id"] == f"{submission['id']}:0"

            sealed_aads = environment.crypto_state["seal_aads"]
            assert isinstance(sealed_aads, list) and sealed_aads
            expected_aad = stored_payload_associated_data(
                payload_row,
                parent_attempt_id=submission["id"],
                protected_ref=request_data["protected_ref"],
                item_index=0,
                ordered_count=1,
                ordered_manifest_digest=step["input_snapshot"]["ordered_manifest_digest"],
            )
            assert expected_aad in sealed_aads

            binding = _video_binding_from_committed_rows(
                reader,
                task_id=task_id,
                raw_key=raw_key,
            )

        registry = environment.binding._producer._registry
        crypto = environment.capabilities.crypto
        proof_authority = environment.capabilities.proof_authority
        assert crypto is not None and proof_authority is not None
        writes: list[str] = []
        flushes: list[Session] = []

        def capture_write_statement(
            _connection, _cursor, statement, _parameters, _context, _executemany
        ) -> None:
            if statement.lstrip().split(None, 1)[0].lower() in {
                "insert",
                "update",
                "delete",
                "replace",
            }:
                writes.append(statement)

        def capture_flush(session: Session, *_args: object) -> None:
            flushes.append(session)

        with Session(environment.engine) as execution_reader:
            event.listen(environment.engine, "before_cursor_execute", capture_write_statement)
            event.listen(execution_reader, "before_flush", capture_flush)
            execution_reader.add(UserRow(id="pending-video-reader-no-flush"))
            try:
                verified = read_original_video_single_execution_input(
                    execution_reader,
                    binding,
                    registry=registry,
                    crypto=crypto,
                    proof_authority=proof_authority,
                )
            finally:
                event.remove(environment.engine, "before_cursor_execute", capture_write_statement)
                event.remove(execution_reader, "before_flush", capture_flush)

            assert verified is not None
            assert verified.task_id == binding.task_id
            assert verified.snapshot.model_category == "video"
            assert verified.snapshot.model_name == "grok-imagine-video"
            assert verified.snapshot.effective_prompt == "A quiet room at sunset"
            assert verified.snapshot.model_params["duration"] == 5
            assert verified.snapshot.model_params["resolution"] == "720p"
            assert verified.snapshot.model_params["size"] == "16:9"
            assert verified.snapshot.server_quote == 5
            assert verified.snapshot.target_snapshot.kind == "none"
            assert verified.reference_images == tuple(_video_request()["reference_images"])
            assert verified.reference_audios == tuple(_video_request()["reference_audios"])
            assert verified.reference_videos == tuple(_video_request()["reference_videos"])
            assert verified.credential_binding.provider == "xai"
            assert verified.provider_credential.principal_id == joint_fixtures._G2_JOINT_PRINCIPAL_ID
            assert len(execution_reader.new) == 1
            assert writes == []
            assert flushes == []
            assert joint_fixtures._G2_JOINT_PROVIDER_SECRET not in repr(verified)
            assert joint_fixtures._G2_JOINT_PROVIDER_SECRET not in caplog.text

        unseal_calls = int(environment.crypto_state["unseal_calls"])
        group_binding = replace(
            binding,
            graph_version="video-haoai-attempt-group-v1",
        )
        with Session(environment.engine) as reader:
            assert read_original_video_single_execution_input(
                reader,
                group_binding,
                registry=registry,
                crypto=crypto,
                proof_authority=proof_authority,
            ) is None
        assert int(environment.crypto_state["unseal_calls"]) == unseal_calls

        step_table = ExecutionStepRow.__table__
        with environment.engine.begin() as connection:
            legacy_snapshot = dict(
                connection.execute(
                    select(step_table.c.input_snapshot).where(
                        step_table.c.id == binding.step_id
                    )
                ).scalar_one()
            )
            legacy_snapshot.pop("ordered_manifest_digest")
            connection.execute(
                update(step_table)
                .where(step_table.c.id == binding.step_id)
                .values(input_snapshot=legacy_snapshot)
            )
        with Session(environment.engine) as reader:
            assert read_original_video_single_execution_input(
                reader,
                binding,
                registry=registry,
                crypto=crypto,
                proof_authority=proof_authority,
            ) is None
        assert int(environment.crypto_state["unseal_calls"]) == unseal_calls
    finally:
        caller.close()
        environment.engine.dispose()


def test_reader_accepts_a_real_frame_message_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = _video_environment(monkeypatch, tmp_path)
    asset_id = "joint-storyboard-1"
    chapter_content = (
        f'[ {{ "storyboard" : ["{asset_id}"], '
        '"text" : "Preserve the original frame message" } ]'
    )
    chapter_digest = _seed_video_storyboard_asset(
        environment,
        asset_id=asset_id,
        chapter_content=chapter_content,
    )

    request = _video_request()
    request["target"] = {
        "kind": "frame_message",
        "chapter_id": "joint-chapter-1",
        "storyboard_asset_id": asset_id,
    }
    binding = _commit_video_request(
        environment,
        raw_key="video-frame-message-reader",
        request=request,
    )
    crypto = environment.capabilities.crypto
    proof_authority = environment.capabilities.proof_authority
    assert crypto is not None and proof_authority is not None
    try:
        with Session(environment.engine) as reader:
            verified = read_original_video_single_execution_input(
                reader,
                binding,
                registry=environment.binding._producer._registry,
                crypto=crypto,
                proof_authority=proof_authority,
            )
        assert verified is not None
        target = verified.snapshot.target_snapshot
        assert target.kind == "frame_message"
        assert target.chapter_id == "joint-chapter-1"
        assert target.asset_id == asset_id
        assert target.stable_storyboard_asset_id == asset_id
        assert target.compat_frame_index == 0
        assert target.full_chapter_digest == chapter_digest
        assert target.full_frame_digest is None
    finally:
        environment.engine.dispose()


def test_reader_opens_user_video_without_a_wallet_at_zero_cost(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = _video_environment(
        monkeypatch,
        tmp_path,
        model_source=ModelSource.USER,
        wallet_credits=None,
    )
    binding = _commit_video_request(
        environment,
        raw_key="video-user-byok-zero-wallet",
        request=_video_request(),
    )
    crypto = environment.capabilities.crypto
    proof_authority = environment.capabilities.proof_authority
    assert crypto is not None and proof_authority is not None
    try:
        with Session(environment.engine) as reader:
            unit = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.id == binding.unit_id
                )
            ).mappings().one()
            assert unit["quoted_amount"] == 0
            assert unit["debited_amount"] == 0
            assert unit["refunded_amount"] == 0
            assert reader.execute(select(UserCreditRow.__table__)).all() == []
            assert reader.execute(select(CreditLogRow.__table__)).all() == []
            verified = read_original_video_single_execution_input(
                reader,
                binding,
                registry=environment.binding._producer._registry,
                crypto=crypto,
                proof_authority=proof_authority,
            )
        assert verified is not None
        assert verified.snapshot.server_quote == 0
        assert verified.snapshot.model_ref.owner_id == joint_fixtures._G2_JOINT_ACTOR_ID
        assert verified.provider_credential.principal_id == joint_fixtures._G2_JOINT_PRINCIPAL_ID
    finally:
        environment.engine.dispose()


def test_reader_opens_system_video_with_zero_quote_defaults_and_asset_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = _video_environment(
        monkeypatch,
        tmp_path,
        model_source=ModelSource.SYSTEM,
        wallet_credits=0,
        per_second_cost=0,
    )
    request = _video_request()
    request.update(duration=None, resolution=None, size=None)
    request.update(reference_images=[], reference_audios=[], reference_videos=[])
    request["target"] = {
        "kind": "asset_message",
        "chapter_id": "joint-chapter-1",
        "asset_type": "scene",
        "asset_id": "joint-scene-1",
    }
    binding = _commit_video_request(
        environment,
        raw_key="video-system-zero-default-asset-target",
        request=request,
    )
    crypto = environment.capabilities.crypto
    proof_authority = environment.capabilities.proof_authority
    assert crypto is not None and proof_authority is not None
    try:
        with Session(environment.engine) as reader:
            unit = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.id == binding.unit_id
                )
            ).mappings().one()
            wallet = reader.execute(
                select(UserCreditRow.__table__.c.credits).where(
                    UserCreditRow.__table__.c.user_id
                    == joint_fixtures._G2_JOINT_ACTOR_ID
                )
            ).scalar_one()
            ledger_amounts = reader.execute(
                select(CreditLogRow.__table__.c.amount)
            ).scalars().all()
            assert unit["quoted_amount"] == 0
            assert unit["debited_amount"] == 0
            assert unit["refunded_amount"] == 0
            assert wallet == 0
            assert all(amount == 0 for amount in ledger_amounts)
            verified = read_original_video_single_execution_input(
                reader,
                binding,
                registry=environment.binding._producer._registry,
                crypto=crypto,
                proof_authority=proof_authority,
            )
        assert verified is not None
        assert verified.snapshot.server_quote == 0
        assert verified.snapshot.model_params["duration"] == 5
        assert verified.snapshot.model_params["resolution"] == "720p"
        assert verified.snapshot.model_params["size"] == "16:9"
        target = verified.snapshot.target_snapshot
        assert target.kind == "asset_message"
        assert target.chapter_id == "joint-chapter-1"
        assert target.asset_type == "scene"
        assert target.asset_id == "joint-scene-1"
        assert verified.reference_images == ()
        assert verified.reference_audios == ()
        assert verified.reference_videos == ()
    finally:
        environment.engine.dispose()


def test_reader_resolves_same_principal_rotation_and_rejects_expired_credential(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = _video_environment(monkeypatch, tmp_path)
    binding = _commit_video_request(
        environment,
        raw_key="video-reader-original-principal-rotation",
        request=_video_request(),
    )
    crypto = environment.capabilities.crypto
    proof_authority = environment.capabilities.proof_authority
    assert crypto is not None and proof_authority is not None
    try:
        rotated_capabilities = joint_fixtures._joint_rotate_system_credential(environment)
        rotated_registry = build_credential_registry(rotated_capabilities)
        with Session(environment.engine) as reader:
            verified = read_original_video_single_execution_input(
                reader,
                binding,
                registry=rotated_registry,
                crypto=crypto,
                proof_authority=proof_authority,
            )
        assert verified is not None
        assert verified.credential_binding.binding_version == "1"
        assert verified.credential_binding.original_principal_id == joint_fixtures._G2_JOINT_PRINCIPAL_ID
        assert verified.provider_credential.principal_id == joint_fixtures._G2_JOINT_PRINCIPAL_ID
        assert verified.provider_credential.secret == "joint-fixture-provider-secret-v2"

        expired_capabilities = replace(
            rotated_capabilities,
            db_clock=lambda: joint_fixtures._G2_JOINT_NOW + timedelta(days=31),
        )
        expired_registry = build_credential_registry(expired_capabilities)
        with Session(environment.engine) as reader:
            assert read_original_video_single_execution_input(
                reader,
                binding,
                registry=expired_registry,
                crypto=crypto,
                proof_authority=proof_authority,
            ) is None
    finally:
        environment.engine.dispose()


def test_reader_rejects_resigned_internal_projection_mismatches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    environment = _video_environment(monkeypatch, tmp_path)
    asset_id = "joint-storyboard-resigned"
    chapter_content = (
        f'[ {{ "storyboard" : ["{asset_id}"], '
        '"text" : "Signed source chapter must match" } ]'
    )
    _seed_video_storyboard_asset(
        environment,
        asset_id=asset_id,
        chapter_content=chapter_content,
    )
    request = _video_request()
    request["target"] = {
        "kind": "asset_message",
        "chapter_id": "joint-chapter-1",
        "asset_type": "storyboard",
        "asset_id": asset_id,
    }
    binding = _commit_video_request(
        environment,
        raw_key="video-reader-resigned-projection-mismatches",
        request=request,
    )
    crypto = environment.capabilities.crypto
    proof_authority = environment.capabilities.proof_authority
    assert crypto is not None and proof_authority is not None
    original_payload, original_step_snapshot = _read_video_payload_state(
        environment.engine,
        binding,
    )

    def protected_target_kind(payload: dict[str, object]) -> None:
        item = payload["item"]
        assert isinstance(item, dict) and isinstance(item["target"], dict)
        item["target"]["kind"] = "frame_message"

    def canvas_target_kind(payload: dict[str, object], kind: str) -> None:
        item = payload["item"]
        manifest = payload["command_manifest"]
        assert isinstance(item, dict) and isinstance(manifest, dict)
        protected_target = item["target"]
        raw_item = item["raw_item"]
        execution_snapshot = item["execution_snapshot"]
        preparation = manifest["preparation"]
        assert isinstance(protected_target, dict)
        assert isinstance(raw_item, dict) and isinstance(raw_item["target"], dict)
        assert isinstance(execution_snapshot, dict)
        assert isinstance(execution_snapshot["target_snapshot"], dict)
        assert isinstance(preparation, dict)
        facts = preparation["items"][0]
        assert isinstance(facts, dict) and isinstance(facts["target"], dict)
        protected_target["kind"] = kind
        raw_item["target"]["kind"] = kind
        execution_snapshot["target_snapshot"]["kind"] = kind
        facts["target"]["kind"] = kind

    def protected_chapter_content(payload: dict[str, object]) -> None:
        item = payload["item"]
        assert isinstance(item, dict) and isinstance(item["target"], dict)
        chapter = item["target"]["chapter"]
        assert isinstance(chapter, dict) and isinstance(chapter["content"], str)
        chapter["content"] += " changed"

    def protected_asset_facts(payload: dict[str, object]) -> None:
        item = payload["item"]
        assert isinstance(item, dict) and isinstance(item["target"], dict)
        asset = item["target"]["asset"]
        assert isinstance(asset, dict)
        asset["description"] = "Changed signed target asset facts"

    def grant_owner_projection(payload: dict[str, object]) -> None:
        item = payload["item"]
        assert isinstance(item, dict) and isinstance(item["grant"], dict)
        series = item["grant"]["series"]
        assert isinstance(series, dict)
        series["user_id"] = "different-series-owner"

    def storyboard_source_chapter_content(payload: dict[str, object]) -> None:
        item = payload["item"]
        assert isinstance(item, dict) and isinstance(item["target"], dict)
        source_chapter = item["target"]["storyboard_source_chapter"]
        assert isinstance(source_chapter, dict)
        assert isinstance(source_chapter["content"], str)
        source_chapter["content"] += " changed"

    def model_snapshot_parameters(payload: dict[str, object]) -> None:
        item = payload["item"]
        assert isinstance(item, dict) and isinstance(item["model"], dict)
        parameters = item["model"]["snapshot_parameters"]
        assert isinstance(parameters, dict)
        parameters["duration"] = 6

    def preparation_effective_parameters(payload: dict[str, object]) -> None:
        manifest = payload["command_manifest"]
        assert isinstance(manifest, dict)
        preparation = manifest["preparation"]
        assert isinstance(preparation, dict)
        facts = preparation["items"][0]
        assert isinstance(facts, dict) and isinstance(facts["effective_parameters"], dict)
        facts["effective_parameters"]["duration"] = 6

    def frozen_snapshot_parameters(payload: dict[str, object]) -> None:
        item = payload["item"]
        assert isinstance(item, dict) and isinstance(item["execution_snapshot"], dict)
        snapshot_parameters = item["execution_snapshot"]["model_params"]
        assert isinstance(snapshot_parameters, dict)
        snapshot_parameters["duration"] = 6

    def raw_prompt_vs_frozen_prompt(payload: dict[str, object]) -> None:
        item = payload["item"]
        manifest = payload["command_manifest"]
        assert isinstance(item, dict) and isinstance(manifest, dict)
        raw_item = item["raw_item"]
        canonical_request = manifest["canonical_request"]
        preparation = manifest["preparation"]
        assert isinstance(raw_item, dict) and isinstance(canonical_request, dict)
        assert isinstance(preparation, dict)
        facts = preparation["items"][0]
        assert isinstance(facts, dict)
        prompt = "Changed after the signed preparation"
        raw_item["prompt"] = prompt
        canonical_request["prompt"] = prompt
        facts["prompt_digest"] = hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    def reorder_reference_group(
        payload: dict[str, object], group_name: str
    ) -> None:
        item = payload["item"]
        assert isinstance(item, dict) and isinstance(item["references"], dict)
        group = item["references"][group_name]
        assert isinstance(group, list) and len(group) == 3
        group[:] = [group[1], group[0], group[2]]

    def swap_reference_groups(
        payload: dict[str, object], first: str, second: str
    ) -> None:
        item = payload["item"]
        assert isinstance(item, dict) and isinstance(item["references"], dict)
        references = item["references"]
        references[first], references[second] = (
            references[second],
            references[first],
        )

    def boolean_duration(payload: dict[str, object]) -> None:
        item = payload["item"]
        manifest = payload["command_manifest"]
        assert isinstance(item, dict) and isinstance(manifest, dict)
        raw_item = item["raw_item"]
        canonical_request = manifest["canonical_request"]
        assert isinstance(raw_item, dict) and isinstance(canonical_request, dict)
        raw_item["duration"] = True
        canonical_request["duration"] = True

    def string_duration(payload: dict[str, object]) -> None:
        item = payload["item"]
        manifest = payload["command_manifest"]
        assert isinstance(item, dict) and isinstance(manifest, dict)
        raw_item = item["raw_item"]
        canonical_request = manifest["canonical_request"]
        assert isinstance(raw_item, dict) and isinstance(canonical_request, dict)
        raw_item["duration"] = "5"
        canonical_request["duration"] = "5"

    def wrong_principal(payload: dict[str, object]) -> None:
        item = payload["item"]
        assert isinstance(item, dict) and isinstance(item["credential"], dict)
        item["credential"]["principal_id"] = "interchanged-principal"

    def wrong_credential_scope(payload: dict[str, object]) -> None:
        item = payload["item"]
        assert isinstance(item, dict) and isinstance(item["credential"], dict)
        scope = item["credential"]["scope"]
        assert isinstance(scope, dict)
        scope["provider"] = "another-provider"

    def boolean_quote(payload: dict[str, object]) -> None:
        item = payload["item"]
        assert isinstance(item, dict) and isinstance(item["model"], dict)
        item["model"]["quote_amount"] = True

    variants = (
        ("protected target kind", protected_target_kind),
        (
            "video canvas frame target",
            lambda payload: canvas_target_kind(payload, "canvas_frame_preview"),
        ),
        (
            "video canvas asset target",
            lambda payload: canvas_target_kind(payload, "canvas_asset_image"),
        ),
        ("protected chapter body digest", protected_chapter_content),
        ("protected asset digest", protected_asset_facts),
        ("grant series owner projection", grant_owner_projection),
        ("storyboard source chapter projection", storyboard_source_chapter_content),
        ("model snapshot parameters", model_snapshot_parameters),
        ("prepared effective parameters", preparation_effective_parameters),
        ("frozen snapshot parameters", frozen_snapshot_parameters),
        ("raw prompt against frozen prompt", raw_prompt_vs_frozen_prompt),
        (
            "ordered image references",
            lambda payload: reorder_reference_group(payload, "reference_images"),
        ),
        (
            "ordered audio references",
            lambda payload: reorder_reference_group(payload, "reference_audios"),
        ),
        (
            "ordered video references",
            lambda payload: reorder_reference_group(payload, "reference_videos"),
        ),
        (
            "image/audio group identity",
            lambda payload: swap_reference_groups(
                payload, "reference_images", "reference_audios"
            ),
        ),
        (
            "image/video group identity",
            lambda payload: swap_reference_groups(
                payload, "reference_images", "reference_videos"
            ),
        ),
        (
            "audio/video group identity",
            lambda payload: swap_reference_groups(
                payload, "reference_audios", "reference_videos"
            ),
        ),
        ("boolean duration", boolean_duration),
        ("string duration", string_duration),
        ("boolean quote", boolean_quote),
        ("credential principal", wrong_principal),
        ("credential scope", wrong_credential_scope),
    )
    with environment.engine.begin() as connection:
        connection.exec_driver_sql(
            "DROP TRIGGER trg_generation_prepared_payloads_immutable_update"
        )
    try:
        with Session(environment.engine) as reader:
            assert read_original_video_single_execution_input(
                reader,
                binding,
                registry=environment.binding._producer._registry,
                crypto=crypto,
                proof_authority=proof_authority,
            ) is not None
        before_owner_reject = int(environment.crypto_state["unseal_calls"])
        with Session(environment.engine) as reader:
            assert read_original_video_single_execution_input(
                reader,
                replace(binding, owner_id="different-owner"),
                registry=environment.binding._producer._registry,
                crypto=crypto,
                proof_authority=proof_authority,
            ) is None
        assert int(environment.crypto_state["unseal_calls"]) == before_owner_reject

        with caplog.at_level("DEBUG"):
            for label, mutate in variants:
                changed_binding, _, _ = _store_authenticated_video_payload_variant(
                    environment.engine,
                    binding,
                    crypto=crypto,
                    proof_authority=proof_authority,
                    mutate=mutate,
                )
                try:
                    with Session(environment.engine) as reader:
                        assert read_original_video_single_execution_input(
                            reader,
                            changed_binding,
                            registry=environment.binding._producer._registry,
                            crypto=crypto,
                            proof_authority=proof_authority,
                        ) is None, label
                finally:
                    _restore_video_payload_state(
                        environment.engine,
                        binding,
                        payload=original_payload,
                        step_snapshot=original_step_snapshot,
                    )
        assert joint_fixtures._G2_JOINT_PROVIDER_SECRET not in caplog.text
        assert "Changed after the signed preparation" not in caplog.text
    finally:
        try:
            _restore_video_payload_state(
                environment.engine,
                binding,
                payload=original_payload,
                step_snapshot=original_step_snapshot,
            )
        finally:
            with environment.engine.begin() as connection:
                connection.exec_driver_sql(
                    "CREATE TRIGGER trg_generation_prepared_payloads_immutable_update "
                    "BEFORE UPDATE ON generation_prepared_payloads "
                    "BEGIN SELECT RAISE(ABORT, 'immutable'); END"
                )
            environment.engine.dispose()
