"""Authenticated batch-boundary checks for frame.optimize payloads."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from sqlalchemy import select, update

from haoai_backend.generation_core.base_schema import AITaskRow
from haoai_backend.generation_core.frame_optimize_admission import admit_frame_optimize
from haoai_backend.generation_core.graph_planning import GraphItemInput, build_operation_graph_plan
from haoai_backend.generation_credentials.domain import CredentialScope, SealedMaterial
from haoai_backend.generation_credentials.frame_optimize_snapshot import (
    canonical_frame_optimize_bytes,
    frame_optimize_payload_associated_data,
)
from haoai_backend.generation_credentials.frame_optimize_snapshot_reader import (
    _snapshot_from_value,
    read_original_frame_optimize_execution_batch,
)
from haoai_backend.generation_credentials.persistence import prepared_payloads

from generation_execution.test_frame_optimize_admission import (
    _G2_JOINT_ACTOR_ID,
    _G2_JOINT_NOW,
    _build_optimize_case,
    _reader_binding,
)


def _associated_data(request_data: dict[str, object], item: dict[str, object], row: dict[str, object]) -> bytes:
    manifest_item = {
        key: item[key]
        for key in (
            "order",
            "frame_index",
            "item_id",
            "unit_id",
            "step_id",
            "prepared_payload_id",
            "quoted_amount",
            "snapshot_digest",
            "rule_digest",
            "target_ref",
            "model_ref",
            "grant_ref",
        )
    }
    scope = CredentialScope(
        source=row["source"],
        row_id=row["row_id"],
        owner_or_system=row["owner_or_system"],
        provider=row["provider"],
    )
    return frame_optimize_payload_associated_data(
        manifest_digest=str(request_data["manifest_snapshot_digest"]),
        manifest_item=manifest_item,
        scope=scope,
        binding_id=str(row["binding_id"]),
        binding_version=int(row["binding_version"]),
        principal_id=str(row["principal_id"]),
        schema_version=str(row["schema_version"]),
        projector_version=str(row["projector_version"]),
        reference_policy_version=str(row["reference_policy_version"]),
        target_ref=str(row["target_ref"]),
        model_ref=str(row["model_ref"]),
        grant_ref=str(row["grant_ref"]),
    )


def _open_payload(capabilities, row: dict[str, object], associated_data: bytes) -> dict[str, object]:
    plaintext = capabilities.crypto.open_secret(
        SealedMaterial(
            ciphertext=row["ciphertext"],
            nonce=row["nonce"],
            key_id=row["encryption_key_id"],
            algorithm_version=capabilities.crypto.algorithm_version,
        ),
        associated_data,
    )
    assert plaintext is not None
    return json.loads(plaintext)


def _replace_payload(engine, capabilities, row, associated_data, payload) -> None:
    resealed = capabilities.crypto.seal_secret(
        canonical_frame_optimize_bytes(payload).decode("utf-8"),
        associated_data,
    )
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "DROP TRIGGER IF EXISTS trg_generation_prepared_payloads_immutable_update"
        )
        connection.execute(
            update(prepared_payloads)
            .where(prepared_payloads.c.prepared_payload_id == row["prepared_payload_id"])
            .values(
                ciphertext=resealed.ciphertext,
                nonce=resealed.nonce,
                encryption_key_id=resealed.key_id,
            )
        )


def test_item_associated_data_is_distinct_and_payload_exchange_is_rejected(tmp_path: Path) -> None:
    engine, sessions, capabilities, _crypto_state, ids, request = _build_optimize_case(
        tmp_path,
        frame_count=3,
    )
    try:
        result = admit_frame_optimize(
            sessions,
            _G2_JOINT_ACTOR_ID,
            request,
            registry_capabilities=capabilities,
            max_user_concurrency=8,
            id_factory=ids,
            clock=lambda: _G2_JOINT_NOW,
        )
        with sessions() as session:
            task = session.get(AITaskRow, result["task_id"])
            assert task is not None
            request_data = json.loads(task.request_data)
            rows_by_id = {
                str(row["prepared_payload_id"]): dict(row)
                for row in session.execute(select(prepared_payloads)).mappings().all()
            }
            associated_data = []
            for item in request_data["items"]:
                row = rows_by_id[item["prepared_payload_id"]]
                associated_data.append(
                    _associated_data(request_data, item, row)
                )
            assert len(set(associated_data)) == 3
            first_id, second_id = [item["prepared_payload_id"] for item in request_data["items"][:2]]
            first = rows_by_id[first_id]
            second = rows_by_id[second_id]

        with engine.begin() as connection:
            connection.exec_driver_sql("DROP TRIGGER IF EXISTS trg_generation_prepared_payloads_immutable_update")
            connection.execute(
                update(prepared_payloads)
                .where(prepared_payloads.c.prepared_payload_id == first_id)
                .values(ciphertext=second["ciphertext"], nonce=second["nonce"])
            )
            connection.execute(
                update(prepared_payloads)
                .where(prepared_payloads.c.prepared_payload_id == second_id)
                .values(ciphertext=first["ciphertext"], nonce=first["nonce"])
            )
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


def test_reader_rejects_changed_operation_inside_protected_canonical_request(tmp_path: Path) -> None:
    engine, sessions, capabilities, _crypto_state, ids, request = _build_optimize_case(tmp_path)
    try:
        result = admit_frame_optimize(
            sessions,
            _G2_JOINT_ACTOR_ID,
            request,
            registry_capabilities=capabilities,
            max_user_concurrency=8,
            id_factory=ids,
            clock=lambda: _G2_JOINT_NOW,
        )
        with sessions() as session:
            task = session.get(AITaskRow, result["task_id"])
            assert task is not None
            request_data = json.loads(task.request_data)
            item = request_data["items"][0]
            row = dict(
                session.execute(
                    select(prepared_payloads).where(
                        prepared_payloads.c.prepared_payload_id == item["prepared_payload_id"]
                    )
                ).mappings().one()
            )
            manifest_item = {
                key: item[key]
                for key in (
                    "order",
                    "frame_index",
                    "item_id",
                    "unit_id",
                    "step_id",
                    "prepared_payload_id",
                    "quoted_amount",
                    "snapshot_digest",
                    "rule_digest",
                    "target_ref",
                    "model_ref",
                    "grant_ref",
                )
            }
            scope = CredentialScope(
                source=row["source"],
                row_id=row["row_id"],
                owner_or_system=row["owner_or_system"],
                provider=row["provider"],
            )
            associated_data = frame_optimize_payload_associated_data(
                manifest_digest=request_data["manifest_snapshot_digest"],
                manifest_item=manifest_item,
                scope=scope,
                binding_id=row["binding_id"],
                binding_version=row["binding_version"],
                principal_id=row["principal_id"],
                schema_version=row["schema_version"],
                projector_version=row["projector_version"],
                reference_policy_version=row["reference_policy_version"],
                target_ref=row["target_ref"],
                model_ref=row["model_ref"],
                grant_ref=row["grant_ref"],
            )
            plaintext = capabilities.crypto.open_secret(
                SealedMaterial(
                    ciphertext=row["ciphertext"],
                    nonce=row["nonce"],
                    key_id=row["encryption_key_id"],
                    algorithm_version=capabilities.crypto.algorithm_version,
                ),
                associated_data,
            )
            assert plaintext is not None
            payload = json.loads(plaintext)
            payload["request"]["operation"] = "frame.review"
            resealed = capabilities.crypto.seal_secret(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                associated_data,
            )

        with engine.begin() as connection:
            connection.exec_driver_sql(
                "DROP TRIGGER IF EXISTS trg_generation_prepared_payloads_immutable_update"
            )
            connection.execute(
                update(prepared_payloads)
                .where(prepared_payloads.c.prepared_payload_id == item["prepared_payload_id"])
                .values(
                    ciphertext=resealed.ciphertext,
                    nonce=resealed.nonce,
                    encryption_key_id=resealed.key_id,
                )
            )
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


def test_reader_rejects_changed_snapshot_digest_without_a_new_manifest_proof(
    tmp_path: Path,
) -> None:
    engine, sessions, capabilities, _crypto_state, ids, request = _build_optimize_case(tmp_path)
    try:
        result = admit_frame_optimize(
            sessions,
            _G2_JOINT_ACTOR_ID,
            request,
            registry_capabilities=capabilities,
            max_user_concurrency=8,
            id_factory=ids,
            clock=lambda: _G2_JOINT_NOW,
        )
        with sessions() as session:
            task = session.get(AITaskRow, result["task_id"])
            assert task is not None
            request_data = json.loads(task.request_data)
            item = request_data["items"][0]
            row = dict(
                session.execute(
                    select(prepared_payloads).where(
                        prepared_payloads.c.prepared_payload_id == item["prepared_payload_id"]
                    )
                ).mappings().one()
            )
            associated_data = _associated_data(request_data, item, row)
            payload = _open_payload(capabilities, row, associated_data)

        modified_params = {"temperature": 0.7, "max_tokens": 333}
        payload["model"]["params"] = modified_params
        payload["execution_snapshot"]["model_params"] = modified_params
        modified_snapshot = _snapshot_from_value(payload["execution_snapshot"])
        assert modified_snapshot is not None
        graph = build_operation_graph_plan(
            "frame.optimize",
            graph_version=request_data["graph_version"],
            items=(
                GraphItemInput(
                    item_id=item["item_id"],
                    target_id=item["target_ref"],
                    task_id=result["task_id"],
                    unit_id=item["unit_id"],
                    step_ids=(item["step_id"],),
                    request_digest=item["request_digest"],
                    rule_digest=item["rule_digest"],
                    snapshot=replace(modified_snapshot, model_params=modified_params),
                ),
            ),
        )
        request_data["graph_digest"] = graph.plan_digest
        _replace_payload(engine, capabilities, row, associated_data, payload)
        with engine.begin() as connection:
            connection.execute(
                update(AITaskRow.__table__)
                .where(AITaskRow.__table__.c.id == result["task_id"])
                .values(
                    request_data=canonical_frame_optimize_bytes(request_data).decode("utf-8")
                )
            )
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


def test_reader_rejects_personal_prompt_owned_by_another_actor(tmp_path: Path) -> None:
    from generation_core.test_admission_staging import ModelSource

    engine, sessions, capabilities, _crypto_state, ids, request = _build_optimize_case(
        tmp_path,
        model_source=ModelSource.USER,
        prompt_source=ModelSource.USER,
    )
    try:
        result = admit_frame_optimize(
            sessions,
            _G2_JOINT_ACTOR_ID,
            request,
            registry_capabilities=capabilities,
            max_user_concurrency=8,
            id_factory=ids,
            clock=lambda: _G2_JOINT_NOW,
        )
        with sessions() as session:
            task = session.get(AITaskRow, result["task_id"])
            assert task is not None
            request_data = json.loads(task.request_data)
            item = request_data["items"][0]
            row = dict(
                session.execute(
                    select(prepared_payloads).where(
                        prepared_payloads.c.prepared_payload_id == item["prepared_payload_id"]
                    )
                ).mappings().one()
            )
            associated_data = _associated_data(request_data, item, row)
            payload = _open_payload(capabilities, row, associated_data)

        payload["prompt"]["owner_or_system"] = "another-fixture-actor"
        _replace_payload(engine, capabilities, row, associated_data, payload)
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
