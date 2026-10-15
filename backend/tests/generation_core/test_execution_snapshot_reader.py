from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import replace
from pathlib import Path

import pytest
import test_admission_staging as joint_fixtures
from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_catalog.domain import ModelSource
from haoai_backend.generation_credentials.domain import (
    CredentialCrypto,
    HmacProofAuthority,
    SealedMaterial,
)
from haoai_backend.generation_credentials.execution_snapshot import (
    PersistedImageExecutionBinding,
    VerifiedImageExecutionInput,
    _snapshot_matches_binding,
    read_original_image_single_execution_input,
)
from haoai_backend.generation_credentials.persistence import prepared_payloads
from haoai_backend.generation_credentials.snapshot_producer import (
    stored_payload_associated_data,
)
from haoai_backend.generation_core.admission_receipts import AdmissionStaged
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    ExecutionStepRow,
    TaskSubmissionRow,
    UserCreditRow,
)
from test_admission_staging import _joint_caller, _joint_environment, _joint_prepare_and_stage


def _authenticated_joint_crypto(
    secrets_by_reference: dict[str, str],
) -> tuple[CredentialCrypto, dict[str, object]]:
    """Provide a stateless HMAC-tagged AEAD fake for reader boundary tests."""

    encryption_key = b"execution-snapshot-reader-test-encryption"
    guard_key = b"execution-snapshot-reader-test-guard"
    state: dict[str, object] = {"seal_aads": [], "unseal_calls": 0}

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
            b"reader-fixture-tag" + nonce + associated_data + ciphertext,
            hashlib.sha256,
        ).digest()
        cast_aads = state["seal_aads"]
        assert isinstance(cast_aads, list)
        cast_aads.append(bytes(associated_data))
        return SealedMaterial(tag + ciphertext, nonce, "reader-seal-v1", "reader-aead-v1")

    def unseal(material: SealedMaterial, associated_data: bytes) -> str | None:
        state["unseal_calls"] = int(state["unseal_calls"]) + 1
        if material.key_id != "reader-seal-v1" or len(material.ciphertext) < 32:
            return None
        tag, ciphertext = material.ciphertext[:32], material.ciphertext[32:]
        expected_tag = hmac.new(
            encryption_key,
            b"reader-fixture-tag" + material.nonce + associated_data + ciphertext,
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
            seal_key_id="reader-seal-v1",
            guard_key_id="reader-guard-v1",
            algorithm_version="reader-aead-v1",
            seal=seal,
            unseal=unseal,
            consistency_guard=guard,
            resolve_secret_handle=secrets_by_reference.get,
            resolve_legacy_config_value=lambda stored: stored or None,
            self_test=lambda: True,
        ),
        state,
    )


def _authenticated_environment(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(joint_fixtures, "_joint_local_dummy_crypto", _authenticated_joint_crypto)
    return _joint_environment(
        tmp_path,
        model_source=ModelSource.SYSTEM,
        operation="image.single",
        wallet_credits=20,
    )


def _crypto_with_invalid_stored_material(
    crypto: CredentialCrypto, corruption: str
) -> CredentialCrypto:
    def unseal(material: SealedMaterial, associated_data: bytes) -> str | None:
        if corruption == "aad":
            associated_data += b"tampered"
        elif corruption == "nonce":
            material = replace(
                material,
                nonce=bytes([material.nonce[0] ^ 1]) + material.nonce[1:],
            )
        elif corruption == "ciphertext":
            material = replace(
                material,
                ciphertext=bytes([material.ciphertext[0] ^ 1])
                + material.ciphertext[1:],
            )
        elif corruption == "key-id":
            material = replace(material, key_id="interchanged-key-id")
        else:
            raise AssertionError(f"unknown test corruption: {corruption}")
        return crypto.unseal(material, associated_data)

    return replace(crypto, unseal=unseal)


def _binding_from_committed_rows(
    session: Session,
    *,
    task_id: str,
    raw_key: str,
) -> PersistedImageExecutionBinding:
    task_table = AITaskRow.__table__
    unit_table = BillingUnitRow.__table__
    step_table = ExecutionStepRow.__table__
    submission_table = TaskSubmissionRow.__table__
    task = session.execute(
        select(task_table).where(task_table.c.id == task_id)
    ).mappings().one()
    request_data = json.loads(task["request_data"])
    unit = session.execute(
        select(unit_table).where(unit_table.c.task_id == task_id)
    ).mappings().one()
    step = session.execute(
        select(step_table).where(step_table.c.billing_unit_id == unit["id"])
    ).mappings().one()
    snapshot = step["input_snapshot"]
    submission = session.execute(
        select(submission_table).where(submission_table.c.id == request_data["submission_id"])
    ).mappings().one()
    assert submission["task_ids"] == [task_id]
    return PersistedImageExecutionBinding(
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


def test_reader_reopens_only_the_original_durable_image_snapshot_and_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    environment = _authenticated_environment(monkeypatch, tmp_path)
    engine = environment.engine
    registry = environment.binding._producer._registry
    crypto = environment.capabilities.crypto
    proof_authority = environment.capabilities.proof_authority
    crypto_state = environment.crypto_state
    assert crypto is not None and proof_authority is not None
    caller = _joint_caller(environment.engine)
    raw_key = "original-image-single-key"
    try:
        staged = _joint_prepare_and_stage(
            environment.binding,
            caller,
            operation="image.single",
            raw_key=raw_key,
            request={
                "model_id": "model-1",
                "prompt": "A quiet room",
                "target": {"kind": "standalone"},
                "size": None,
                "reference_images": [],
            },
        )
        assert isinstance(staged, AdmissionStaged)
        task_id = staged.ordered_tasks[0].task_id
        caller.commit()
        del staged

        with Session(environment.engine) as reader:
            binding = _binding_from_committed_rows(
                reader,
                task_id=task_id,
                raw_key=raw_key,
            )
            payload_row = dict(
                reader.execute(
                    select(prepared_payloads).where(
                        prepared_payloads.c.prepared_payload_id
                        == binding.prepared_payload_id
                    )
                ).mappings().one()
            )
            assert crypto_state["seal_aads"]
            expected_aad = stored_payload_associated_data(
                payload_row,
                parent_attempt_id=binding.submission_id,
                protected_ref=binding.protected_ref,
                item_index=0,
                ordered_count=1,
                ordered_manifest_digest=binding.ordered_manifest_digest,
            )
            assert expected_aad in crypto_state["seal_aads"]
            caller.close()
            del environment

        # The reader gets only a new Session and authenticated durable bytes.
        with Session(engine) as reader:
            binding = _binding_from_committed_rows(
                reader,
                task_id=task_id,
                raw_key=raw_key,
            )
            payload_row = dict(
                reader.execute(
                    select(prepared_payloads).where(
                        prepared_payloads.c.prepared_payload_id
                        == binding.prepared_payload_id
                    )
                ).mappings().one()
            )
            expected_aad = stored_payload_associated_data(
                payload_row,
                parent_attempt_id=binding.submission_id,
                protected_ref=binding.protected_ref,
                item_index=0,
                ordered_count=1,
                ordered_manifest_digest=binding.ordered_manifest_digest,
            )
            material = SealedMaterial(
                ciphertext=payload_row["ciphertext"],
                nonce=payload_row["nonce"],
                key_id=payload_row["encryption_key_id"],
                algorithm_version=crypto.algorithm_version,
            )
            assert crypto.open_secret(material, expected_aad) is not None
            result = read_original_image_single_execution_input(
                reader,
                binding,
                registry=registry,
                crypto=crypto,
                proof_authority=proof_authority,
            )
            assert type(result) is VerifiedImageExecutionInput
            assert result.snapshot.effective_prompt == "A quiet room"
            assert result.snapshot.model_ref.model_id == "model-1"
            assert result.task_model_name == binding.task_model_name
            assert result.task_model_name == "Public joint-fixture model"
            assert result.snapshot.model_name == "wire-joint-fixture-model"
            assert result.task_model_name != result.snapshot.model_name
            assert result.references == ()
            assert result.provider_credential.secret == "joint-fixture-provider-secret"
            assert "joint-fixture-provider-secret" not in repr(result)
            assert "A quiet room" not in repr(result)

            for corruption in ("aad", "nonce", "ciphertext", "key-id"):
                with caplog.at_level("DEBUG"):
                    assert read_original_image_single_execution_input(
                        reader,
                        binding,
                        registry=registry,
                        crypto=_crypto_with_invalid_stored_material(crypto, corruption),
                        proof_authority=proof_authority,
                    ) is None

            payload_plaintext = crypto.open_secret(material, expected_aad)
            assert payload_plaintext is not None
            payload = json.loads(payload_plaintext)
            item = payload["item"]
            assert not _snapshot_matches_binding(
                result.snapshot,
                {**item, "model": {**item["model"], "provider": "wrong-provider"}},
                binding,
                payload_row,
            )
            assert not _snapshot_matches_binding(
                result.snapshot,
                {
                    **item,
                    "model": {
                        **item["model"],
                        "display_name": "interchanged-display-name",
                    },
                },
                binding,
                payload_row,
            )
            assert not _snapshot_matches_binding(
                result.snapshot,
                {
                    **item,
                    "credential": {
                        **item["credential"],
                        "endpoint_origin": "https://wrong.invalid",
                    },
                },
                binding,
                payload_row,
            )
            assert not _snapshot_matches_binding(
                result.snapshot,
                {
                    **item,
                    "credential": {
                        **item["credential"],
                        "binding_id": "interchanged-binding",
                    },
                },
                binding,
                payload_row,
            )

            wrong_proof_authority = HmacProofAuthority(
                "wrong-reader-test-key", b"x" * 32
            )
            with caplog.at_level("DEBUG"):
                assert read_original_image_single_execution_input(
                    reader,
                    binding,
                    registry=registry,
                    crypto=crypto,
                    proof_authority=wrong_proof_authority,
                ) is None

            wrong_digest = replace(binding, ordered_manifest_digest="f" * 64)
            assert read_original_image_single_execution_input(
                reader,
                wrong_digest,
                registry=registry,
                crypto=crypto,
                proof_authority=proof_authority,
            ) is None

            wrong_display_name = replace(
                binding, task_model_name="interchanged-display-name"
            )
            assert read_original_image_single_execution_input(
                reader,
                wrong_display_name,
                registry=registry,
                crypto=crypto,
                proof_authority=proof_authority,
            ) is None
            legacy_binding = replace(binding, ordered_manifest_digest="")
            assert read_original_image_single_execution_input(
                reader,
                legacy_binding,
                registry=registry,
                crypto=crypto,
                proof_authority=proof_authority,
            ) is None
        assert "joint-fixture-provider-secret" not in caplog.text
        assert "A quiet room" not in caplog.text
    finally:
        caller.close()
        engine.dispose()


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    (
        ("owner_id", "different-owner"),
        ("graph_version", "image-single-v2"),
        ("step_id", "different-step"),
        ("prepared_payload_id", "different-payload"),
        ("raw_key", "different-idempotency-key"),
        ("task_model_id", "interchanged-model"),
    ),
)
def test_reader_fails_closed_when_durable_graph_binding_is_interchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    field_name: str,
    replacement: str,
) -> None:
    environment = _authenticated_environment(monkeypatch, tmp_path)
    engine = environment.engine
    caller = _joint_caller(environment.engine)
    secret = "joint-fixture-provider-secret"
    try:
        staged = _joint_prepare_and_stage(
            environment.binding,
            caller,
            operation="image.single",
            raw_key="bound-image-single-key",
            request={
                "model_id": "model-1",
                "prompt": "Bound original prompt",
                "target": {"kind": "standalone"},
                "size": None,
                "reference_images": [],
            },
        )
        assert isinstance(staged, AdmissionStaged)
        task_id = staged.ordered_tasks[0].task_id
        caller.commit()

        with Session(environment.engine) as reader:
            binding = _binding_from_committed_rows(
                reader,
                task_id=task_id,
                raw_key="bound-image-single-key",
            )
            capabilities = environment.capabilities
            assert capabilities.crypto is not None
            assert capabilities.proof_authority is not None
            changed_binding = replace(binding, **{field_name: replacement})
            with caplog.at_level("DEBUG"):
                assert read_original_image_single_execution_input(
                    reader,
                    changed_binding,
                    registry=environment.binding._producer._registry,
                    crypto=capabilities.crypto,
                    proof_authority=capabilities.proof_authority,
                ) is None
        assert secret not in caplog.text
        assert "Bound original prompt" not in caplog.text
    finally:
        caller.close()
        engine.dispose()


def test_reader_rejects_interchanged_model_scope_without_secret_logging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    environment = _authenticated_environment(monkeypatch, tmp_path)
    engine = environment.engine
    caller = _joint_caller(environment.engine)
    secret = "joint-fixture-provider-secret"
    try:
        staged = _joint_prepare_and_stage(
            environment.binding,
            caller,
            operation="image.single",
            raw_key="model-bound-image-key",
            request={
                "model_id": "model-1",
                "prompt": "Model-bound prompt",
                "target": {"kind": "standalone"},
                "size": None,
                "reference_images": [],
            },
        )
        assert isinstance(staged, AdmissionStaged)
        task_id = staged.ordered_tasks[0].task_id
        caller.commit()

        with Session(environment.engine) as reader:
            binding = _binding_from_committed_rows(
                reader,
                task_id=task_id,
                raw_key="model-bound-image-key",
            )
            capabilities = environment.capabilities
            assert capabilities.crypto is not None
            assert capabilities.proof_authority is not None
            changed_binding = replace(binding, task_model_id="interchanged-model")
            with caplog.at_level("DEBUG"):
                assert read_original_image_single_execution_input(
                    reader,
                    changed_binding,
                    registry=environment.binding._producer._registry,
                    crypto=capabilities.crypto,
                    proof_authority=capabilities.proof_authority,
                ) is None
        assert secret not in caplog.text
        assert "Model-bound prompt" not in caplog.text
    finally:
        caller.close()
        engine.dispose()


@pytest.mark.parametrize(
    ("row_kind", "column", "value"),
    (
        ("step", "step_key", "not-generate"),
        ("step", "required", False),
        ("step", "step_order", 4),
        ("step", "dependencies", ["another-step"]),
        ("step", "input_digest", "f" * 64),
        ("unit", "item_key", "interchanged-item"),
        ("unit", "input_digest", "e" * 64),
    ),
)
def test_reader_rejects_nonregistered_sql_graph_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    row_kind: str,
    column: str,
    value: object,
) -> None:
    environment = _authenticated_environment(monkeypatch, tmp_path)
    engine = environment.engine
    caller = _joint_caller(engine)
    try:
        staged = _joint_prepare_and_stage(
            environment.binding,
            caller,
            operation="image.single",
            raw_key="mutated-graph-key",
            request={
                "model_id": "model-1",
                "prompt": "Original registered graph",
                "target": {"kind": "standalone"},
                "size": None,
                "reference_images": [],
            },
        )
        assert isinstance(staged, AdmissionStaged)
        task_id = staged.ordered_tasks[0].task_id
        caller.commit()

        with Session(engine) as mutator:
            binding = _binding_from_committed_rows(
                mutator, task_id=task_id, raw_key="mutated-graph-key"
            )
            table = (
                ExecutionStepRow.__table__
                if row_kind == "step"
                else BillingUnitRow.__table__
            )
            row_id = binding.step_id if row_kind == "step" else binding.unit_id
            mutator.execute(
                update(table).where(table.c.id == row_id).values(**{column: value})
            )
            mutator.commit()

        with Session(engine) as reader:
            assert read_original_image_single_execution_input(
                reader,
                binding,
                registry=environment.binding._producer._registry,
                crypto=environment.capabilities.crypto,
                proof_authority=environment.capabilities.proof_authority,
            ) is None
    finally:
        caller.close()
        engine.dispose()


@pytest.mark.parametrize(
    "duplicate_kind",
    ("unit", "step"),
)
def test_reader_rejects_duplicate_registered_units_or_steps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    duplicate_kind: str,
) -> None:
    environment = _authenticated_environment(monkeypatch, tmp_path)
    engine = environment.engine
    caller = _joint_caller(engine)
    try:
        staged = _joint_prepare_and_stage(
            environment.binding,
            caller,
            operation="image.single",
            raw_key="duplicate-graph-key",
            request={
                "model_id": "model-1",
                "prompt": "Single registered graph",
                "target": {"kind": "standalone"},
                "size": None,
                "reference_images": [],
            },
        )
        assert isinstance(staged, AdmissionStaged)
        task_id = staged.ordered_tasks[0].task_id
        caller.commit()

        with Session(engine) as mutator:
            binding = _binding_from_committed_rows(
                mutator, task_id=task_id, raw_key="duplicate-graph-key"
            )
            if duplicate_kind == "unit":
                table = BillingUnitRow.__table__
                original = dict(
                    mutator.execute(
                        select(table).where(table.c.id == binding.unit_id)
                    ).mappings().one()
                )
                original["id"] = "alternate-unit"
                original["item_key"] = "alternate-item"
            else:
                table = ExecutionStepRow.__table__
                original = dict(
                    mutator.execute(
                        select(table).where(table.c.id == binding.step_id)
                    ).mappings().one()
                )
                original["id"] = "alternate-step"
                original["step_key"] = "alternate-step-key"
            mutator.execute(insert(table).values(**original))
            mutator.commit()

        with Session(engine) as reader:
            assert read_original_image_single_execution_input(
                reader,
                binding,
                registry=environment.binding._producer._registry,
                crypto=environment.capabilities.crypto,
                proof_authority=environment.capabilities.proof_authority,
            ) is None
            if duplicate_kind == "step":
                assert read_original_image_single_execution_input(
                    reader,
                    replace(binding, step_id="alternate-step"),
                    registry=environment.binding._producer._registry,
                    crypto=environment.capabilities.crypto,
                    proof_authority=environment.capabilities.proof_authority,
                ) is None
    finally:
        caller.close()
        engine.dispose()


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    (
        ("parent_attempt_id", "alternate-parent"),
        ("server_attempt_id", "alternate-attempt"),
        ("prepared_payload_id", "alternate-payload"),
        ("protected_ref", "alternate-protected-ref"),
        ("billing_unit_id", "alternate-unit"),
        ("item_id", "alternate-item"),
        ("unit_key", "alternate-unit-key"),
        ("step_graph_version", "alternate-graph-version"),
        ("rule_digest", "f" * 64),
    ),
)
def test_reader_rejects_interchanged_task_request_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field_name: str,
    replacement: str,
) -> None:
    environment = _authenticated_environment(monkeypatch, tmp_path)
    engine = environment.engine
    caller = _joint_caller(engine)
    try:
        staged = _joint_prepare_and_stage(
            environment.binding,
            caller,
            operation="image.single",
            raw_key="request-projection-key",
            request={
                "model_id": "model-1",
                "prompt": "Request projection binding",
                "target": {"kind": "standalone"},
                "size": None,
                "reference_images": [],
            },
        )
        assert isinstance(staged, AdmissionStaged)
        task_id = staged.ordered_tasks[0].task_id
        caller.commit()

        with Session(engine) as mutator:
            binding = _binding_from_committed_rows(
                mutator, task_id=task_id, raw_key="request-projection-key"
            )
            task_table = AITaskRow.__table__
            task = mutator.execute(
                select(task_table).where(task_table.c.id == task_id)
            ).mappings().one()
            request_data = json.loads(task["request_data"])
            request_data[field_name] = replacement
            mutator.execute(
                update(task_table)
                .where(task_table.c.id == task_id)
                .values(request_data=json.dumps(request_data, separators=(",", ":")))
            )
            mutator.commit()

        with Session(engine) as reader:
            assert read_original_image_single_execution_input(
                reader,
                binding,
                registry=environment.binding._producer._registry,
                crypto=environment.capabilities.crypto,
                proof_authority=environment.capabilities.proof_authority,
            ) is None
    finally:
        caller.close()
        engine.dispose()


def test_reader_rejects_legacy_durable_four_key_step_without_repair_or_fee_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = _authenticated_environment(monkeypatch, tmp_path)
    engine = environment.engine
    caller = _joint_caller(engine)
    try:
        staged = _joint_prepare_and_stage(
            environment.binding,
            caller,
            operation="image.single",
            raw_key="legacy-step-key",
            request={
                "model_id": "model-1",
                "prompt": "Legacy step projection",
                "target": {"kind": "standalone"},
                "size": None,
                "reference_images": [],
            },
        )
        assert isinstance(staged, AdmissionStaged)
        task_id = staged.ordered_tasks[0].task_id
        caller.commit()

        with Session(engine) as mutator:
            binding = _binding_from_committed_rows(
                mutator, task_id=task_id, raw_key="legacy-step-key"
            )
            task_table = AITaskRow.__table__
            task = mutator.execute(
                select(task_table).where(task_table.c.id == task_id)
            ).mappings().one()
            user_id = task["user_id"]
            unit_table = BillingUnitRow.__table__
            step_table = ExecutionStepRow.__table__
            credit_table = UserCreditRow.__table__
            ledger_table = CreditLogRow.__table__
            unit = mutator.execute(
                select(unit_table).where(unit_table.c.id == binding.unit_id)
            ).mappings().one()
            step = mutator.execute(
                select(step_table).where(step_table.c.id == binding.step_id)
            ).mappings().one()
            snapshot = dict(step["input_snapshot"])
            del snapshot["ordered_manifest_digest"]
            mutator.execute(
                update(step_table)
                .where(step_table.c.id == binding.step_id)
                .values(input_snapshot=snapshot)
            )
            mutator.commit()

        with Session(engine) as reader:
            credit_before = reader.execute(
                select(UserCreditRow.credits).where(UserCreditRow.user_id == user_id)
            ).scalar_one()
            ledger_before = reader.execute(
                select(ledger_table.c.id).where(ledger_table.c.user_id == user_id)
            ).scalars().all()
            assert read_original_image_single_execution_input(
                reader,
                binding,
                registry=environment.binding._producer._registry,
                crypto=environment.capabilities.crypto,
                proof_authority=environment.capabilities.proof_authority,
            ) is None
            after = reader.execute(
                select(step_table.c.input_snapshot).where(step_table.c.id == binding.step_id)
            ).scalar_one()
            assert "ordered_manifest_digest" not in after
            assert reader.execute(
                select(UserCreditRow.credits).where(UserCreditRow.user_id == user_id)
            ).scalar_one() == credit_before
            assert reader.execute(
                select(ledger_table.c.id).where(ledger_table.c.user_id == user_id)
            ).scalars().all() == ledger_before
            assert unit["quoted_amount"] == binding.quoted_amount
    finally:
        caller.close()
        engine.dispose()
