from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Callable

import pytest
import test_admission_staging as joint_fixtures
from sqlalchemy import event, select, update
from sqlalchemy.orm import Session

from haoai_backend.chat_data.tables import chat_messages
from haoai_backend.generation_catalog.domain import ModelSource
from haoai_backend.generation_catalog.tables import prompt_configs, system_models, system_prompts
from haoai_backend.generation_contracts.types import ReferenceInput
from haoai_backend.generation_core.admission_receipts import AdmissionStaged
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    ExecutionStepRow,
    TaskSubmissionRow,
    UserCreditRow,
)
from haoai_backend.generation_credentials.application import build_credential_registry
from haoai_backend.generation_credentials.batch_execution_snapshot import (
    PersistedImageBatchExecutionBinding,
    VerifiedImageBatchExecutionInput,
    read_original_image_batch_execution_input,
)
from haoai_backend.generation_credentials.domain import (
    CredentialCrypto,
    HmacProofAuthority,
    RootEnrollmentSource,
    SealedMaterial,
)
from haoai_backend.generation_credentials.persistence import prepared_payloads
from haoai_backend.generation_credentials.snapshot_producer import (
    canonical_payload_json,
    stored_payload_associated_data,
)
from test_admission_staging import _joint_caller, _joint_environment, _joint_prepare_and_stage
from test_execution_snapshot_reader import _authenticated_joint_crypto


def _authenticated_batch_environment(
    monkeypatch,
    tmp_path: Path,
    *,
    model_source: ModelSource,
    item_count: int = 2,
    prompt_source: str | None = None,
) -> tuple[object, AdmissionStaged]:
    def authenticated_crypto_factory(secrets_by_reference):
        crypto, state = _authenticated_joint_crypto(secrets_by_reference)
        state["secrets_by_reference"] = secrets_by_reference
        return crypto, state

    monkeypatch.setattr(joint_fixtures, "_joint_local_dummy_crypto", authenticated_crypto_factory)
    environment = _joint_environment(
        tmp_path,
        model_source=model_source,
        operation="image.batch",
        wallet_credits=500 if model_source is ModelSource.SYSTEM else None,
    )
    request = joint_fixtures._joint_request("image.batch")
    assets = request["assets"][:item_count]
    timestamp = joint_fixtures._G2_JOINT_NOW.replace(tzinfo=None)
    if prompt_source is not None:
        prompt_id = f"joint-{prompt_source}-batch-prompt"
        table = system_prompts if prompt_source == "system" else prompt_configs
        values: dict[str, object] = {
            "id": prompt_id,
            "category": "ai-review",
            "name": f"{prompt_source.title()} batch prompt",
            "system_prompt": "Frozen system instructions",
            "user_prompt": "Frozen user instructions",
            "sort_order": 0,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        if prompt_source == "system":
            values["is_active"] = True
        else:
            values["user_id"] = joint_fixtures._G2_JOINT_ACTOR_ID
        with environment.engine.begin() as connection:
            connection.execute(table.insert().values(**values))
        assets[0]["prompt_id"] = prompt_id
    if item_count > 2:
        with environment.engine.begin() as connection:
            for index in range(2, item_count):
                asset_type = "character" if index % 2 == 0 else "scene"
                asset_id = f"joint-batch-asset-{index:02d}"
                values: dict[str, object] = {
                    "id": asset_id,
                    "series_id": "joint-series-1",
                    "description": f"Batch fixture description {index}",
                    "image_url": None,
                    "updated_at": timestamp,
                }
                values["name" if asset_type == "character" else "title"] = (
                    f"Batch fixture asset {index}"
                )
                connection.execute(
                    joint_fixtures.ASSET_TABLES[asset_type].insert().values(**values)
                )
                assets.append(
                    {
                        "type": asset_type,
                        "id": asset_id,
                        "name": f"Batch fixture asset {index}",
                        "description": f"Batch fixture description {index}",
                        "model_id": "model-1",
                        "prompt_id": "",
                        "size": "1024x1024",
                        "reference_images": [],
                    }
                )
    request["assets"] = assets
    request["assets"][0]["reference_images"] = [
        "https://ref.example.test/first?ordered=1",
        "https://ref.example.test/shared?ordered=2",
        "https://ref.example.test/first?ordered=1",
    ]
    if item_count > 1:
        request["assets"][1]["reference_images"] = [
            "https://ref.example.test/shared?ordered=2",
            "https://ref.example.test/second?ordered=3",
        ]

    caller = _joint_caller(environment.engine)
    staged = _joint_prepare_and_stage(
        environment.binding,
        caller,
        operation="image.batch",
        raw_key=f"batch-reader-{model_source.value}-{item_count}",
        request=request,
    )
    assert isinstance(staged, AdmissionStaged)
    caller.commit()
    caller.close()
    return environment, staged


def _binding_for_task(
    environment: object,
    staged: AdmissionStaged,
    item_index: int,
) -> PersistedImageBatchExecutionBinding:
    task_id = staged.ordered_tasks[item_index].task_id
    with Session(environment.engine) as session:
        task = session.execute(
            select(AITaskRow).where(AITaskRow.id == task_id)
        ).scalar_one()
        unit = session.execute(
            select(BillingUnitRow).where(BillingUnitRow.task_id == task_id)
        ).scalar_one()
        step = session.execute(
            select(ExecutionStepRow).where(
                ExecutionStepRow.billing_unit_id == unit.id
            )
        ).scalar_one()
        return PersistedImageBatchExecutionBinding(
            owner_id=task.user_id,
            task_id=task.id,
            unit_id=unit.id,
            step_id=step.id,
            submission_id=staged.submission_id,
        )


def _read_batch_item(
    environment: object,
    binding: PersistedImageBatchExecutionBinding,
    *,
    crypto: CredentialCrypto | None = None,
    registry=None,
) -> VerifiedImageBatchExecutionInput | None:
    selected_crypto = crypto or environment.capabilities.crypto
    proof_authority = environment.capabilities.proof_authority
    assert isinstance(selected_crypto, CredentialCrypto)
    assert isinstance(proof_authority, HmacProofAuthority)
    with Session(environment.engine) as session:
        return read_original_image_batch_execution_input(
            session,
            binding,
            registry=registry or build_credential_registry(environment.capabilities),
            crypto=selected_crypto,
            proof_authority=proof_authority,
        )


def _crypto_with_opened_payload_mutation(
    crypto: CredentialCrypto,
    mutate: Callable[[dict[str, object]], None],
) -> CredentialCrypto:
    original_unseal = crypto.unseal
    first = True

    def unseal(material, associated_data):
        nonlocal first
        plaintext = original_unseal(material, associated_data)
        if plaintext is None or not first:
            return plaintext
        first = False
        payload = json.loads(plaintext)
        mutate(payload)
        return canonical_payload_json(payload).decode("utf-8")

    return replace(crypto, unseal=unseal)


def _record_database_writes(engine, statements: list[str]) -> Callable[[], None]:
    def record(_connection, _cursor, statement, _parameters, _context, _executemany):
        verb = statement.lstrip().split(maxsplit=1)[0].upper()
        if verb in {"INSERT", "UPDATE", "DELETE", "REPLACE"}:
            statements.append(verb)

    event.listen(engine, "before_cursor_execute", record)
    return lambda: event.remove(engine, "before_cursor_execute", record)


def _resign_batch_after_semantic_mutation(
    environment: object,
    staged: AdmissionStaged,
    mutation: str,
) -> None:
    crypto = environment.capabilities.crypto
    authority = environment.capabilities.proof_authority
    assert isinstance(crypto, CredentialCrypto)
    assert isinstance(authority, HmacProofAuthority)

    task_table = AITaskRow.__table__
    step_table = ExecutionStepRow.__table__
    with Session(environment.engine) as session:
        tasks = [
            dict(row)
            for row in session.execute(
                select(task_table).where(
                    task_table.c.id.in_([task.task_id for task in staged.ordered_tasks])
                )
            ).mappings().all()
        ]
        tasks_by_id = {row["id"]: row for row in tasks}
        ordered_tasks = [tasks_by_id[item.task_id] for item in staged.ordered_tasks]
        request_data = [json.loads(row["request_data"]) for row in ordered_tasks]
        payload_ids = [item["prepared_payload_id"] for item in request_data]
        rows = [
            dict(row)
            for row in session.execute(
                select(prepared_payloads).where(
                    prepared_payloads.c.prepared_payload_id.in_(payload_ids)
                )
            ).mappings().all()
        ]
        rows_by_id = {row["prepared_payload_id"]: row for row in rows}
        ordered_rows = [rows_by_id[payload_id] for payload_id in payload_ids]
        step_rows = [
            dict(row)
            for row in session.execute(
                select(step_table).where(
                    step_table.c.id.in_(
                        [step_id for task in staged.ordered_tasks for step_id in task.step_ids]
                    )
                )
            ).mappings().all()
        ]
        steps_by_id = {row["id"]: row for row in step_rows}
        ordered_steps = [
            steps_by_id[task.step_ids[0]] for task in staged.ordered_tasks
        ]

    original_manifest_digest = ordered_steps[0]["input_snapshot"][
        "ordered_manifest_digest"
    ]
    payloads: list[dict[str, object]] = []
    for index, (row, request) in enumerate(zip(ordered_rows, request_data, strict=True)):
        aad = stored_payload_associated_data(
            row,
            parent_attempt_id=staged.submission_id,
            protected_ref=request["protected_ref"],
            item_index=index,
            ordered_count=len(ordered_rows),
            ordered_manifest_digest=original_manifest_digest,
        )
        plaintext = crypto.open_secret(
            SealedMaterial(
                ciphertext=row["ciphertext"],
                nonce=row["nonce"],
                key_id=row["encryption_key_id"],
                algorithm_version=crypto.algorithm_version,
            ),
            aad,
        )
        assert plaintext is not None
        payloads.append(json.loads(plaintext))

    manifest = dict(payloads[0]["command_manifest"])
    if mutation == "target_chapter_identity":
        target = payloads[0]["item"]["target"]
        chapter = dict(target["chapter"])
        chapter["id"] = "interchanged-chapter"
        target["chapter"] = chapter
    elif mutation == "target_grant_actor":
        payloads[0]["item"]["grant"]["actor_id"] = "interchanged-actor"
    elif mutation == "target_asset_name":
        target = payloads[0]["item"]["target"]
        asset = dict(target["asset"])
        asset["name"] = "interchanged-asset-name"
        target["asset"] = asset
    elif mutation == "model_snapshot_parameters":
        model = payloads[0]["item"]["model"]
        model["snapshot_parameters"] = {"size": "interchanged-size"}
    elif mutation == "model_params":
        payloads[0]["item"]["model"]["params"] = {"size": "interchanged-size"}
    elif mutation == "model_effective_parameters":
        payloads[0]["item"]["model"]["effective_parameters"] = {
            "size": "interchanged-size"
        }
    elif mutation == "model_wire_model":
        payloads[0]["item"]["model"]["wire_model"] = "interchanged-wire-model"
    elif mutation == "model_base_url":
        payloads[0]["item"]["model"]["base_url"] = "https://interchanged.example.test/v2"
    elif mutation == "model_provider_route":
        payloads[0]["item"]["model"]["provider_route"] = "interchanged-route"
    elif mutation == "preparation_effective_parameters":
        preparation = dict(manifest["preparation"])
        prepared_items = [dict(item) for item in preparation["items"]]
        prepared_items[0]["effective_parameters"] = {"size": "interchanged-size"}
        preparation["items"] = prepared_items
        manifest["preparation"] = preparation
    else:
        raise AssertionError(f"unknown signed semantic mutation: {mutation}")

    item_digests = [
        hashlib.sha256(canonical_payload_json(payload["item"])).hexdigest()
        for payload in payloads
    ]
    manifest["item_digests"] = item_digests
    manifest_bytes = canonical_payload_json(manifest)
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    proof = authority.sign("prepared-generation-input-v1", manifest_bytes)
    ordered_manifest = [
        {
            "index": index,
            "child_attempt_id": request["server_attempt_id"],
            "payload_id": request["prepared_payload_id"],
            "protected_ref": request["protected_ref"],
            "item_digest": item_digests[index],
        }
        for index, request in enumerate(request_data)
    ]

    with environment.engine.begin() as connection:
        connection.exec_driver_sql(
            "DROP TRIGGER trg_generation_prepared_payloads_immutable_update"
        )
        for index, (payload, row, request) in enumerate(
            zip(payloads, ordered_rows, request_data, strict=True)
        ):
            payload["command_manifest"] = manifest
            payload["ordered_manifest"] = ordered_manifest
            payload["ordered_manifest_digest"] = manifest_digest
            payload["manifest_proof"] = proof
            updated_row = {**row, "command_proof": proof}
            aad = stored_payload_associated_data(
                updated_row,
                parent_attempt_id=staged.submission_id,
                protected_ref=request["protected_ref"],
                item_index=index,
                ordered_count=len(ordered_rows),
                ordered_manifest_digest=manifest_digest,
            )
            sealed = crypto.seal_secret(canonical_payload_json(payload).decode("utf-8"), aad)
            connection.execute(
                update(prepared_payloads)
                .where(prepared_payloads.c.prepared_payload_id == row["prepared_payload_id"])
                .values(
                    command_proof=proof,
                    ciphertext=sealed.ciphertext,
                    nonce=sealed.nonce,
                    encryption_key_id=sealed.key_id,
                )
            )
            snapshot = dict(ordered_steps[index]["input_snapshot"])
            snapshot["ordered_manifest_digest"] = manifest_digest
            connection.execute(
                update(step_table)
                .where(step_table.c.id == ordered_steps[index]["id"])
                .values(input_snapshot=snapshot)
            )
        connection.exec_driver_sql(
            "CREATE TRIGGER trg_generation_prepared_payloads_immutable_update "
            "BEFORE UPDATE ON generation_prepared_payloads "
            "BEGIN SELECT RAISE(ABORT, 'immutable'); END"
        )


@pytest.mark.parametrize(("item_count", "item_index"), ((1, 0), (2, 1), (64, 63)))
def test_reader_uses_parent_order_and_reopens_one_many_or_max_batch_items(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    item_count: int,
    item_index: int,
) -> None:
    environment, staged = _authenticated_batch_environment(
        monkeypatch,
        tmp_path,
        model_source=ModelSource.SYSTEM,
        item_count=item_count,
    )
    binding = _binding_for_task(environment, staged, item_index)
    database_writes: list[str] = []
    remove_listener = _record_database_writes(environment.engine, database_writes)

    try:
        result = _read_batch_item(environment, binding)
        assert database_writes == []
    finally:
        remove_listener()

    assert type(result) is VerifiedImageBatchExecutionInput
    assert result.item_index == item_index
    assert result.ordered_count == item_count
    assert result.task_id == staged.ordered_tasks[item_index].task_id
    assert result.submission_id == staged.submission_id
    if item_count == 2 and item_index == 1:
        assert result.references == (
            ReferenceInput(
                kind="public_http",
                locator="https://ref.example.test/shared?ordered=2",
            ),
            ReferenceInput(
                kind="public_http",
                locator="https://ref.example.test/second?ordered=3",
            ),
        )


def test_reader_preserves_repeated_reference_order_and_original_model_after_catalog_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment, staged = _authenticated_batch_environment(
        monkeypatch,
        tmp_path,
        model_source=ModelSource.SYSTEM,
    )
    binding = _binding_for_task(environment, staged, 0)
    original_task_model_name = staged.ordered_tasks[0].model_display_name
    with environment.engine.begin() as connection:
        connection.execute(
            update(system_models)
            .where(system_models.c.id == "model-1")
            .values(name="Changed current display", model="changed-current-wire")
        )

    result = _read_batch_item(environment, binding)

    assert type(result) is VerifiedImageBatchExecutionInput
    assert result.item_index == 0
    assert result.snapshot.model_name == "wire-joint-fixture-model"
    assert result.task_model_name == original_task_model_name
    assert result.references == (
        ReferenceInput(
            kind="public_http",
            locator="https://ref.example.test/first?ordered=1",
        ),
        ReferenceInput(
            kind="public_http",
            locator="https://ref.example.test/shared?ordered=2",
        ),
        ReferenceInput(
            kind="public_http",
            locator="https://ref.example.test/first?ordered=1",
        ),
    )


def test_reader_accepts_user_scoped_batch_without_wallet_or_fee(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment, staged = _authenticated_batch_environment(
        monkeypatch,
        tmp_path,
        model_source=ModelSource.USER,
    )
    binding = _binding_for_task(environment, staged, 0)

    result = _read_batch_item(environment, binding)

    assert type(result) is VerifiedImageBatchExecutionInput
    with Session(environment.engine) as session:
        units = session.execute(
            select(BillingUnitRow).where(
                BillingUnitRow.task_id.in_([task.task_id for task in staged.ordered_tasks])
            )
        ).scalars().all()
        assert len(units) == 2
        assert all(unit.quoted_amount == 0 for unit in units)
        assert all(unit.debited_amount == 0 for unit in units)
        assert session.execute(
            select(UserCreditRow.id).where(UserCreditRow.user_id == binding.owner_id)
        ).scalar_one_or_none() is None


@pytest.mark.parametrize(
    "tamper",
    (
        "owner_binding",
        "wrong_step_binding",
        "raw_key",
        "operation",
        "graph_version",
        "quote",
        "task_order",
        "duplicate_child",
        "missing_child",
        "swapped_payloads",
        "swapped_messages",
        "legacy_step",
    ),
)
def test_reader_fails_closed_on_parent_graph_identity_or_quote_tampering_without_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
) -> None:
    environment, staged = _authenticated_batch_environment(
        monkeypatch,
        tmp_path,
        model_source=ModelSource.SYSTEM,
    )
    binding = _binding_for_task(environment, staged, 1)
    if tamper == "owner_binding":
        binding = replace(binding, owner_id="interchanged-owner")
    elif tamper == "wrong_step_binding":
        binding = replace(
            binding,
            step_id=staged.ordered_tasks[0].step_ids[0],
        )
    else:
        with Session(environment.engine) as mutator:
            if tamper == "raw_key":
                mutator.execute(
                    update(TaskSubmissionRow)
                    .where(TaskSubmissionRow.id == binding.submission_id)
                    .values(idempotency_key="interchanged-raw-key")
                )
            elif tamper == "operation":
                mutator.execute(
                    update(TaskSubmissionRow)
                    .where(TaskSubmissionRow.id == binding.submission_id)
                    .values(operation="image.single")
                )
            elif tamper == "task_order":
                mutator.execute(
                    update(TaskSubmissionRow)
                    .where(TaskSubmissionRow.id == binding.submission_id)
                    .values(task_ids=[
                        staged.ordered_tasks[1].task_id,
                        staged.ordered_tasks[0].task_id,
                    ])
                )
            elif tamper == "duplicate_child":
                mutator.execute(
                    update(TaskSubmissionRow)
                    .where(TaskSubmissionRow.id == binding.submission_id)
                    .values(task_ids=[
                        staged.ordered_tasks[0].task_id,
                        staged.ordered_tasks[0].task_id,
                    ])
                )
            elif tamper == "missing_child":
                mutator.execute(
                    update(TaskSubmissionRow)
                    .where(TaskSubmissionRow.id == binding.submission_id)
                    .values(task_ids=[staged.ordered_tasks[0].task_id])
                )
            elif tamper == "graph_version":
                mutator.execute(
                    update(BillingUnitRow)
                    .where(BillingUnitRow.id == binding.unit_id)
                    .values(step_graph_version="image-batch-v2")
                )
            elif tamper == "swapped_payloads":
                task_table = AITaskRow.__table__
                first_task, second_task = [
                    dict(row)
                    for row in mutator.execute(
                        select(task_table).where(
                            task_table.c.id.in_(
                                [task.task_id for task in staged.ordered_tasks]
                            )
                        )
                    ).mappings().all()
                ]
                first_data = json.loads(first_task["request_data"])
                second_data = json.loads(second_task["request_data"])
                swapped_fields = (
                    "prepared_payload_id",
                    "protected_ref",
                    "target_ref",
                    "model_ref",
                    "grant_ref",
                    "reference_ref",
                )
                for field_name in swapped_fields:
                    first_data[field_name], second_data[field_name] = (
                        second_data[field_name],
                        first_data[field_name],
                    )
                mutator.execute(
                    update(task_table)
                    .where(task_table.c.id == first_task["id"])
                    .values(request_data=json.dumps(first_data, separators=(",", ":")))
                )
                mutator.execute(
                    update(task_table)
                    .where(task_table.c.id == second_task["id"])
                    .values(request_data=json.dumps(second_data, separators=(",", ":")))
                )
            elif tamper == "swapped_messages":
                task_table = AITaskRow.__table__
                first_task, second_task = [
                    dict(row)
                    for row in mutator.execute(
                        select(task_table).where(
                            task_table.c.id.in_(
                                [task.task_id for task in staged.ordered_tasks]
                            )
                        )
                    ).mappings().all()
                ]
                mutator.execute(
                    update(task_table)
                    .where(task_table.c.id == first_task["id"])
                    .values(message_id=second_task["message_id"])
                )
                mutator.execute(
                    update(task_table)
                    .where(task_table.c.id == second_task["id"])
                    .values(message_id=first_task["message_id"])
                )
            elif tamper == "quote":
                mutator.execute(
                    update(BillingUnitRow)
                    .where(BillingUnitRow.id == binding.unit_id)
                    .values(quoted_amount=99)
                )
            elif tamper == "legacy_step":
                step = mutator.execute(
                    select(ExecutionStepRow).where(ExecutionStepRow.id == binding.step_id)
                ).scalar_one()
                old_snapshot = dict(step.input_snapshot)
                del old_snapshot["ordered_manifest_digest"]
                mutator.execute(
                    update(ExecutionStepRow)
                    .where(ExecutionStepRow.id == binding.step_id)
                    .values(input_snapshot=old_snapshot)
                )
            mutator.commit()

    database_writes: list[str] = []
    remove_listener = _record_database_writes(environment.engine, database_writes)
    try:
        assert _read_batch_item(environment, binding) is None
        assert database_writes == []
    finally:
        remove_listener()


@pytest.mark.parametrize(
    "tampered_field",
    (
        "item_index",
        "ordered_count",
        "proof",
        "item",
        "preparation_total",
        "credential_principal",
        "model_scope",
        "reference_order",
    ),
)
def test_reader_rejects_modified_decrypted_child_manifest_and_proof_without_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tampered_field: str,
) -> None:
    environment, staged = _authenticated_batch_environment(
        monkeypatch,
        tmp_path,
        model_source=ModelSource.SYSTEM,
    )
    binding = _binding_for_task(environment, staged, 1)
    crypto = environment.capabilities.crypto
    assert isinstance(crypto, CredentialCrypto)

    def mutate(payload: dict[str, object]) -> None:
        if tampered_field == "item_index":
            payload["item_index"] = 1
        elif tampered_field == "ordered_count":
            payload["ordered_count"] = 3
        elif tampered_field == "proof":
            payload["manifest_proof"] = "0" * 64
        elif tampered_field == "preparation_total":
            manifest = dict(payload["command_manifest"])
            preparation = dict(manifest["preparation"])
            preparation["quoted_total"] = 999
            manifest["preparation"] = preparation
            payload["command_manifest"] = manifest
        elif tampered_field == "credential_principal":
            item = dict(payload["item"])
            credential = dict(item["credential"])
            credential["principal_id"] = "interchanged-principal"
            item["credential"] = credential
            payload["item"] = item
        elif tampered_field == "model_scope":
            item = dict(payload["item"])
            model = dict(item["model"])
            model["source"] = "user"
            item["model"] = model
            payload["item"] = item
        elif tampered_field == "reference_order":
            item = dict(payload["item"])
            references = dict(item["references"])
            reordered = list(references["reference_images"])
            reordered[0], reordered[1] = reordered[1], reordered[0]
            references["reference_images"] = reordered
            item["references"] = references
            payload["item"] = item
        else:
            item = dict(payload["item"])
            item["raw_key"] = "interchanged-raw-key"
            payload["item"] = item

    database_writes: list[str] = []
    remove_listener = _record_database_writes(environment.engine, database_writes)
    try:
        assert _read_batch_item(
            environment,
            binding,
            crypto=_crypto_with_opened_payload_mutation(crypto, mutate),
        ) is None
        assert database_writes == []
    finally:
        remove_listener()


def test_reader_reopens_pending_sibling_after_another_item_has_a_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment, staged = _authenticated_batch_environment(
        monkeypatch,
        tmp_path,
        model_source=ModelSource.SYSTEM,
    )
    first_task = staged.ordered_tasks[0]
    second_binding = _binding_for_task(environment, staged, 1)
    with environment.engine.begin() as connection:
        connection.execute(
            update(AITaskRow)
            .where(AITaskRow.id == first_task.task_id)
            .values(status="completed", result='{"status":"synthetic-success"}')
        )
        connection.execute(
            update(BillingUnitRow)
            .where(BillingUnitRow.task_id == first_task.task_id)
            .values(result_status="succeeded")
        )
        connection.execute(
            update(ExecutionStepRow)
            .where(ExecutionStepRow.id == first_task.step_ids[0])
            .values(execution_status="applied")
        )
        connection.execute(
            update(chat_messages)
            .where(chat_messages.c.id == first_task.message_ids[1])
            .values(content="synthetic result for the first batch item")
        )

    assert type(_read_batch_item(environment, second_binding)) is VerifiedImageBatchExecutionInput


def test_reader_does_not_autoflush_caller_pending_orm_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment, staged = _authenticated_batch_environment(
        monkeypatch,
        tmp_path,
        model_source=ModelSource.SYSTEM,
    )
    binding = _binding_for_task(environment, staged, 1)
    database_writes: list[str] = []
    remove_listener = _record_database_writes(environment.engine, database_writes)
    try:
        with Session(environment.engine) as session:
            task = session.execute(
                select(AITaskRow).where(AITaskRow.id == binding.task_id)
            ).scalar_one()
            task.progress_message = "pending caller-owned update"
            result = read_original_image_batch_execution_input(
                session,
                binding,
                registry=build_credential_registry(environment.capabilities),
                crypto=environment.capabilities.crypto,
                proof_authority=environment.capabilities.proof_authority,
            )
            assert type(result) is VerifiedImageBatchExecutionInput
            assert session.is_modified(task)
            assert database_writes == []
    finally:
        remove_listener()


@pytest.mark.parametrize("corruption", ("associated_data", "nonce", "ciphertext", "key_id"))
def test_reader_rejects_authenticated_payload_material_corruption_without_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    environment, staged = _authenticated_batch_environment(
        monkeypatch,
        tmp_path,
        model_source=ModelSource.SYSTEM,
    )
    binding = _binding_for_task(environment, staged, 1)
    crypto = environment.capabilities.crypto
    assert isinstance(crypto, CredentialCrypto)
    original_unseal = crypto.unseal

    def unseal(material, associated_data):
        if corruption == "associated_data":
            associated_data += b"tampered"
        elif corruption == "nonce":
            material = replace(
                material,
                nonce=bytes((material.nonce[0] ^ 1,)) + material.nonce[1:],
            )
        elif corruption == "ciphertext":
            material = replace(
                material,
                ciphertext=bytes((material.ciphertext[0] ^ 1,))
                + material.ciphertext[1:],
            )
        else:
            material = replace(material, key_id="interchanged-key-id")
        return original_unseal(material, associated_data)

    database_writes: list[str] = []
    remove_listener = _record_database_writes(environment.engine, database_writes)
    try:
        assert _read_batch_item(
            environment,
            binding,
            crypto=replace(crypto, unseal=unseal),
        ) is None
        assert database_writes == []
    finally:
        remove_listener()


@pytest.mark.parametrize(
    "mutation",
    (
        "target_chapter_identity",
        "target_grant_actor",
        "target_asset_name",
        "model_snapshot_parameters",
        "model_params",
        "model_effective_parameters",
        "model_wire_model",
        "model_base_url",
        "model_provider_route",
        "preparation_effective_parameters",
    ),
)
def test_reader_rejects_resigned_payload_semantic_mismatch_without_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    environment, staged = _authenticated_batch_environment(
        monkeypatch,
        tmp_path,
        model_source=ModelSource.SYSTEM,
    )
    binding = _binding_for_task(environment, staged, 0)
    _resign_batch_after_semantic_mutation(environment, staged, mutation)

    database_writes: list[str] = []
    remove_listener = _record_database_writes(environment.engine, database_writes)
    try:
        assert _read_batch_item(environment, binding) is None
        assert database_writes == []
    finally:
        remove_listener()


def test_reader_resolves_original_credential_through_same_principal_rotation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment, staged = _authenticated_batch_environment(
        monkeypatch,
        tmp_path,
        model_source=ModelSource.SYSTEM,
    )
    binding = _binding_for_task(environment, staged, 0)
    original_source = environment.capabilities.enrollment_source
    original_principal = original_source.records[0].principal_id

    environment.capabilities = joint_fixtures._joint_rotate_system_credential(environment)
    rotated = _read_batch_item(environment, binding)

    assert type(rotated) is VerifiedImageBatchExecutionInput
    assert rotated.provider_credential.principal_id == original_principal
    assert rotated.provider_credential.secret == "joint-fixture-provider-secret-v2"

    authority = environment.capabilities.proof_authority
    crypto = environment.capabilities.crypto
    source = environment.capabilities.enrollment_source
    assert isinstance(authority, HmacProofAuthority)
    assert isinstance(crypto, CredentialCrypto)
    assert isinstance(source, RootEnrollmentSource)
    original = source.records[0]
    wrong_unsigned = replace(
        original,
        principal_id="00000000-0000-4000-8000-000000000099",
        secret_consistency_guard="0" * 64,
        proof_signature="0" * 64,
    )
    original_secret = environment.crypto_state["secrets_by_reference"][original.material_ref]
    assert isinstance(original_secret, str)
    wrong_guarded = replace(
        wrong_unsigned,
        secret_consistency_guard=crypto.guard(
            original_secret,
            joint_fixtures.account_guard_associated_data(wrong_unsigned),
        ),
    )
    wrong_record = replace(
        wrong_guarded,
        proof_signature=authority.sign("root-enrollment-v1", wrong_guarded.proof_payload()),
    )
    wrong_source = RootEnrollmentSource(
        (wrong_record, *source.records[1:]),
        authority,
        source.rotation_attestations,
    )
    wrong_capabilities = replace(environment.capabilities, enrollment_source=wrong_source)
    assert _read_batch_item(
        environment,
        binding,
        registry=build_credential_registry(wrong_capabilities),
    ) is None


@pytest.mark.parametrize("prompt_source", ("system", "user"))
def test_reader_restores_frozen_selected_batch_prompt_without_catalog_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prompt_source: str,
) -> None:
    environment, staged = _authenticated_batch_environment(
        monkeypatch,
        tmp_path,
        model_source=ModelSource.SYSTEM,
        prompt_source=prompt_source,
    )
    binding = _binding_for_task(environment, staged, 0)
    prompt_id = f"joint-{prompt_source}-batch-prompt"
    prompt_table = system_prompts if prompt_source == "system" else prompt_configs
    with environment.engine.begin() as connection:
        connection.execute(
            update(prompt_table)
            .where(prompt_table.c.id == prompt_id)
            .values(
                system_prompt="Changed current system instructions",
                user_prompt="Changed current user instructions",
            )
        )

    database_writes: list[str] = []
    remove_listener = _record_database_writes(environment.engine, database_writes)
    try:
        result = _read_batch_item(environment, binding)
        assert type(result) is VerifiedImageBatchExecutionInput
        assert result.snapshot.prompt_ref == prompt_id
        assert result.snapshot.effective_prompt == (
            "Frozen system instructions\n"
            "Frozen user instructions\n"
            "First ordered description"
        )
        assert "Changed current" not in result.snapshot.effective_prompt
        assert database_writes == []
    finally:
        remove_listener()
