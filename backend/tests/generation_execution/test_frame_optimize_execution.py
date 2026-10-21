"""Whole-batch SQL identity checks used by optimize claim and intent adapters."""

from __future__ import annotations

from sqlalchemy import select, update

from haoai_backend.generation_core.base_schema import AITaskRow, BillingUnitRow
from haoai_backend.generation_credentials.persistence import prepared_payloads
from generation_execution.frame_optimize_execution_helpers import (
    admit_batch,
    assert_only_original_graph,
    claim_one,
    intent_adapter,
    locked_batch,
    reopen_batch,
    rows_for,
)


def test_execution_loader_reopens_original_nonlinear_batch_in_one_caller_session(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(2, 0, 1))
    try:
        graph = locked_batch(case)
        assert graph is not None
        assert graph.binding.task_id == case.task_id
        assert graph.verified.task_id == case.task_id
        assert [item.frame_index for item in graph.verified.items] == [2, 0, 1]
        assert [item.order for item in graph.verified.items] == [0, 1, 2]
        assert [item.requested_text for item in graph.verified.items] == [
            "requested frame 2",
            "requested frame 0",
            "requested frame 1",
        ]
        assert [item.database_text for item in graph.verified.items] == [
            "database frame 2",
            "database frame 0",
            "database frame 1",
        ]
        assert all(not hasattr(item, "secret") for item in graph.verified.items)
        assert reopen_batch(case) is not None
        assert_only_original_graph(case, external_count=0)
    finally:
        case.engine.dispose()


def test_execution_loader_rejects_foreign_owner_without_creating_authority(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(2, 0, 1))
    try:
        from haoai_backend.generation_core.frame_optimize_execution import lock_frame_optimize_batch

        with case.sessions() as session:
            assert (
                lock_frame_optimize_batch(
                    session,
                    "foreign-fixture-actor",
                    case.task_id,
                    crypto=case.capabilities.crypto,
                    proof_authority=case.capabilities.proof_authority,
                )
                is None
            )
        assert_only_original_graph(case, external_count=0)
    finally:
        case.engine.dispose()


def test_execution_loader_refuses_mutated_unit_quote_and_never_repairs_it(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(0,))
    try:
        with case.engine.begin() as connection:
            unit_id = connection.execute(
                select(BillingUnitRow.id).where(BillingUnitRow.task_id == case.task_id)
            ).scalar_one()
            connection.execute(
                update(BillingUnitRow.__table__)
                .where(BillingUnitRow.__table__.c.id == unit_id)
                .values(quoted_amount=BillingUnitRow.__table__.c.quoted_amount + 1)
            )
        assert locked_batch(case) is None
        with case.sessions() as session:
            task = session.get(AITaskRow, case.task_id)
            assert task is not None and task.status == "queued"
            assert task.execution_generation == 0
            assert session.execute(select(BillingUnitRow.quoted_amount)).scalar_one() == 4
        assert_only_original_graph(case, external_count=0)
    finally:
        case.engine.dispose()


def test_execution_loader_does_not_treat_processing_projection_as_immutable_corruption(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(2, 0, 1))
    try:
        with case.engine.begin() as connection:
            connection.execute(
                update(AITaskRow.__table__)
                .where(AITaskRow.__table__.c.id == case.task_id)
                .values(status="processing", claimed_by="fixture", claim_token="fixture-token", lease_until=case.clock[0].replace(tzinfo=None), execution_generation=1)
            )
        graph = locked_batch(case)
        assert graph is not None
        assert graph.task["status"] == "processing"
        assert [item.frame_index for item in graph.verified.items] == [2, 0, 1]
        assert_only_original_graph(case, external_count=0)
    finally:
        case.engine.dispose()


def test_execution_loader_requires_every_immutable_payload_sibling(tmp_path):
    from haoai_backend.generation_credentials.schema import SQLITE_GUARD_TABLES

    case = admit_batch(tmp_path, request_indexes=(2, 0, 1))
    try:
        graph = locked_batch(case)
        assert graph is not None
        target_payload = graph.verified.items[1].prepared_payload_id
        with case.engine.begin() as connection:
            connection.exec_driver_sql(
                "DROP TRIGGER IF EXISTS trg_generation_prepared_payloads_immutable_delete"
            )
            connection.execute(
                prepared_payloads.delete().where(
                    prepared_payloads.c.prepared_payload_id == target_payload
                )
            )
        assert "trg_generation_prepared_payloads_immutable_delete" in SQLITE_GUARD_TABLES

        assert locked_batch(case) is None
        task = rows_for(case)["task"]
        assert task["status"] == "queued"
        assert task["execution_generation"] == 0
        assert_only_original_graph(case, external_count=0, payload_count=2)
    finally:
        case.engine.dispose()


def test_execution_loader_rejects_ciphertext_mutation_without_repair(tmp_path):
    from haoai_backend.generation_credentials.schema import SQLITE_GUARD_TABLES

    case = admit_batch(tmp_path, request_indexes=(2, 0, 1))
    try:
        graph = locked_batch(case)
        assert graph is not None
        payload_id = graph.verified.items[1].prepared_payload_id
        with case.sessions() as session:
            original_ciphertext = session.execute(
                select(prepared_payloads.c.ciphertext).where(
                    prepared_payloads.c.prepared_payload_id == payload_id
                )
            ).scalar_one()
        altered_ciphertext = bytes([original_ciphertext[0] ^ 1]) + original_ciphertext[1:]
        with case.engine.begin() as connection:
            connection.exec_driver_sql(
                "DROP TRIGGER IF EXISTS trg_generation_prepared_payloads_immutable_update"
            )
            connection.execute(
                update(prepared_payloads)
                .where(prepared_payloads.c.prepared_payload_id == payload_id)
                .values(ciphertext=altered_ciphertext)
            )
        assert "trg_generation_prepared_payloads_immutable_update" in SQLITE_GUARD_TABLES

        assert locked_batch(case) is None
        with case.sessions() as session:
            assert session.execute(
                select(prepared_payloads.c.ciphertext).where(
                    prepared_payloads.c.prepared_payload_id == payload_id
                )
            ).scalar_one() == altered_ciphertext
        task = rows_for(case)["task"]
        assert task["status"] == "queued"
        assert task["execution_generation"] == 0
        assert_only_original_graph(case, external_count=0)
    finally:
        case.engine.dispose()


def test_execution_loader_handles_thirty_items_and_preserves_requested_order(tmp_path):
    request_indexes = tuple(range(29, -1, -1))
    case = admit_batch(tmp_path, request_indexes=request_indexes)
    try:
        graph = locked_batch(case)
        assert graph is not None
        assert [item.frame_index for item in graph.verified.items] == list(request_indexes)
        assert [item.order for item in graph.verified.items] == list(range(30))
        assert len(graph.units) == len(graph.steps) == len(graph.verified.items) == 30

        claim = claim_one(case, token="frame-optimize-thirty-item-claim")
        final_item = graph.verified.items[-1]
        intent = intent_adapter(case, prefix="thirty-item").prepare(claim, final_item.unit_id)
        assert intent.disposition == "committed"
        assert intent.verified_item is not None
        assert intent.verified_item.frame_index == 0
        assert intent.verified_item.order == 29
        assert_only_original_graph(case, external_count=1)
    finally:
        case.engine.dispose()
