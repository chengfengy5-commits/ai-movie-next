"""Registered worker consumes every saved frame through normal phases."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import event, select

from haoai_backend.generation_contracts.ports import GenerationRuntimeConfig
from haoai_backend.generation_credentials.application import build_credential_registry
from haoai_backend.generation_runtime.frame_optimize_assembly import (
    FrameOptimizeWorkerConfiguration,
    build_frame_optimize_worker_runtime,
)
from haoai_backend.personal_production.notes.tables import metadata as notes_metadata
from generation_execution.frame_optimize_execution_helpers import admit_batch
from generation_execution.frame_optimize_execution_helpers import (
    application_adapter,
    claim_adapter,
    fee_adapter,
    intent_adapter,
    persist_result,
    prepare_item,
    recovery_adapter,
    rows_for,
)


class _Response:
    status_code = 200

    async def iter_bytes(self, *, chunk_size: int):
        yield json.dumps(
            {"choices": [{"message": {"content": "优化后的镜头"}}]},
            ensure_ascii=False,
        ).encode()


class _Client:
    def __init__(self) -> None:
        self.user_contents: list[str] = []

    async def request(self, method, url, *, content, **_kwargs):
        assert method == "POST"
        assert url == "https://provider.example.test/v2/chat/completions"
        self.user_contents.append(json.loads(content)["messages"][1]["content"])
        return _Response()


class _ClientContext:
    def __init__(self, client: _Client) -> None:
        self.client = client

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, *_args):
        return False


class _UnexpectedClientContext:
    async def __aenter__(self):
        raise AssertionError("recovery continuation must not create a provider client")

    async def __aexit__(self, *_args):
        return False


class _FenceChangingClient(_Client):
    def __init__(self, case) -> None:
        super().__init__()
        self.case = case

    async def request(self, method, url, *, content, **kwargs):
        response = await super().request(method, url, content=content, **kwargs)
        from haoai_backend.generation_core.base_schema import AITaskRow

        with self.case.sessions.begin() as session:
            task = session.get(AITaskRow, self.case.task_id)
            assert task is not None
            task.claim_token = "replacement-worker-fence"
            task.execution_generation += 1
        return response


def _worker_for(case, client_context_factory):
    capabilities = case.capabilities
    registry = build_credential_registry(capabilities)
    runtime_config = GenerationRuntimeConfig(
        session_factory=capabilities.read_session_factory,
        db_clock=capabilities.db_clock,
        id_factory=capabilities.id_factory,
        http_client_factory=client_context_factory,
        credential_resolver=registry.resolve_original_credential,
        provider_factories=(),
        media_store_factory=lambda: object(),
        local_muse_staging_dir=None,
        public_media_fetcher_factory=lambda: object(),
        bound_provider_content_fetcher_factory=lambda: object(),
        local_muse_capability_factory=lambda: object(),
        r2_storage_factory=lambda: object(),
        shutdown_timeout_seconds=2.0,
    )
    return build_frame_optimize_worker_runtime(
        FrameOptimizeWorkerConfiguration(
            runtime=runtime_config,
            registry_capabilities=capabilities,
            max_user_concurrency=4,
            enabled=True,
        )
    )


def test_worker_sends_and_applies_each_original_frame_once(tmp_path) -> None:
    case = admit_batch(
        tmp_path,
        frame_count=2,
        request_indexes=(1, 0),
        include_source_tables=True,
    )
    try:
        notes_metadata.create_all(case.engine)
        capabilities = case.capabilities
        session_factory = capabilities.read_session_factory
        assert session_factory is case.capabilities.read_session_factory
        client = _Client()
        worker = _worker_for(case, lambda: _ClientContext(client))

        outcome = worker.run_claimed_once("batch-optimize")

        # Finalization revokes the generation claim after committing terminal work.
        assert outcome in {"completed", "pending"}
        assert len(client.user_contents) == 2
        assert client.user_contents[0].endswith("requested frame 1")
        assert client.user_contents[1].endswith("requested frame 0")
        with session_factory() as session:
            from haoai_backend.generation_core.base_schema import AITaskRow, BillingUnitRow

            task = session.get(AITaskRow, case.task_id)
            assert task is not None and task.status == "completed"
            units = session.query(BillingUnitRow).filter_by(task_id=case.task_id).all()
            assert len(units) == 2
            assert all(unit.result_status == "succeeded" for unit in units)
            assert all(unit.billing_status == "charged" for unit in units)
    finally:
        case.engine.dispose()


def test_new_epoch_sends_only_unstarted_siblings_after_unknown_intent(tmp_path) -> None:
    case = admit_batch(
        tmp_path,
        frame_count=3,
        request_indexes=(0, 1, 2),
        include_source_tables=True,
    )
    try:
        claims = claim_adapter(case, token="unknown-intent-parent")
        parent = claims.claim_next("batch-optimize", "unknown-intent-worker")
        assert parent is not None
        assert claim_adapter(case, token="active-lease-competitor").claim_next(
            "batch-optimize", "competitor"
        ) is None

        _, prepared = prepare_item(case, parent, 0)
        assert prepared.intent is not None
        assert prepared.verified_batch is not None
        assert prepared.verified_item is not None
        recovery_adapter(case).record_observation(
            parent,
            prepared.intent,
            prepared.verified_batch,
            prepared.verified_item,
            code="provider_outcome_unknown",
            request_started=True,
            http_status=503,
        )
        prior = rows_for(case)
        original_submission = prior["external"][0]
        assert len(prior["external"]) == 1
        assert claims.release_claim(parent)
        client = _Client()
        worker = _worker_for(case, lambda: _ClientContext(client))
        outcome = worker.run_claimed_once("batch-optimize")

        after = rows_for(case)
        assert outcome in {"completed", "pending"}
        assert len(client.user_contents) == 2, (
            outcome,
            after["task"]["status"],
            after["task"]["execution_generation"],
            len(after["external"]),
        )
        assert client.user_contents[0].endswith("requested frame 1")
        assert client.user_contents[1].endswith("requested frame 2")
        assert len(after["external"]) == 3
        assert after["external"][0]["id"] == original_submission["id"]
        assert after["external"][0]["request_digest"] == original_submission["request_digest"]
        assert after["external"][0]["created_at"] == original_submission["created_at"]
        observations = [
            evidence
            for evidence in after["evidence"]
            if evidence["evidence_kind"] == "frame_optimize_observation_v1"
        ]
        assert len(observations) == 1
        assert observations[0]["submission_id"] == original_submission["id"]
        assert observations[0]["validity"] == "unknown"
        assert len(after["receipts"]) == 2
        assert len(after["fees"]) == 2
        assert len(after["applications"]) == 2
        assert after["task"]["status"] not in {"completed", "failed"}
        assert after["task"]["execution_generation"] == parent.execution_generation + 1
        assert [row["index"] for row in json.loads(after["task"]["result"])] == [1, 2]
    finally:
        case.engine.dispose()


@pytest.mark.parametrize("invalid_sibling_state", ("charged", "no_charge", "missing_manifest"))
def test_resume_claim_rejects_untrusted_sibling_progress(tmp_path, invalid_sibling_state) -> None:
    case = admit_batch(tmp_path, frame_count=2, request_indexes=(0, 1))
    try:
        from haoai_backend.generation_core.base_schema import AITaskRow, BillingUnitRow

        claims = claim_adapter(case, token=f"invalid-sibling-parent-{invalid_sibling_state}")
        parent = claims.claim_next("batch-optimize", "invalid-sibling-test-worker")
        assert parent is not None
        submitted_item, prepared = prepare_item(case, parent, 0)
        assert prepared.intent is not None
        assert prepared.verified_item is not None
        assert claims.release_claim(parent)

        with case.sessions.begin() as session:
            if invalid_sibling_state in {"charged", "no_charge"}:
                sibling = session.execute(
                    select(BillingUnitRow)
                    .where(BillingUnitRow.task_id == case.task_id)
                    .where(BillingUnitRow.id != submitted_item.unit_id)
                ).scalar_one()
                sibling.billing_status = invalid_sibling_state
            else:
                task = session.get(AITaskRow, case.task_id)
                assert task is not None
                request = json.loads(task.request_data)
                sibling_item = next(
                    item
                    for item in request["items"]
                    if item["unit_id"] != submitted_item.unit_id
                )
                request["items"] = [
                    item
                    for item in request["items"]
                    if item["unit_id"] != sibling_item["unit_id"]
                ]
                task.request_data = json.dumps(
                    request, ensure_ascii=False, separators=(",", ":")
                )

        assert claim_adapter(case, token=f"invalid-sibling-next-{invalid_sibling_state}").claim_next(
            "batch-optimize", "invalid-sibling-next-worker"
        ) is None
        state = rows_for(case)
        assert len(state["external"]) == 1
        assert state["external"][0]["id"] == prepared.intent.external_submission_id
        assert state["task"]["execution_generation"] == parent.execution_generation
        assert state["task"]["claim_token"] is None
    finally:
        case.engine.dispose()


def test_lost_fence_observation_expires_without_reposting_provider_intent(tmp_path) -> None:
    case = admit_batch(
        tmp_path,
        frame_count=1,
        request_indexes=(0,),
        include_source_tables=True,
    )
    try:
        client = _FenceChangingClient(case)
        worker = _worker_for(case, lambda: _ClientContext(client))

        outcome = worker.run_claimed_once("batch-optimize")

        assert outcome in {"pending", "ownership_lost"}
        assert len(client.user_contents) == 1
        before_due = rows_for(case)
        original_submission = before_due["external"][0]
        assert len(before_due["external"]) == 1
        assert before_due["task"]["claim_token"] == "replacement-worker-fence"
        assert len(before_due["receipts"]) == 0
        assert len(before_due["fees"]) == 0
        assert len(before_due["applications"]) == 0
        assert len(before_due["work"]) == 0
        observations = [
            evidence
            for evidence in before_due["evidence"]
            if evidence["evidence_kind"] == "frame_optimize_observation_v1"
        ]
        assert len(observations) == 1
        assert observations[0]["submission_id"] == original_submission["id"]
        assert observations[0]["validity"] == "unknown"
        assert json.loads(observations[0]["result_location"])["observed_text"] == "优化后的镜头"

        case.clock[0] = original_submission["deadline_at"] + timedelta(seconds=1)
        recovery = recovery_adapter(case)
        recovery.page_size = 1
        page_queries: list[tuple[str, object]] = []

        def capture_page_query(_connection, _cursor, statement, parameters, _context, _executemany):
            if "ORDER BY ai_tasks.created_at, ai_tasks.id" in statement:
                page_queries.append((statement, parameters))

        event.listen(case.engine, "before_cursor_execute", capture_page_query)
        try:
            due_outcomes = recovery.scan_due()
        finally:
            event.remove(case.engine, "before_cursor_execute", capture_page_query)

        assert len(page_queries) == 1
        assert "LIMIT" in page_queries[0][0].upper()
        assert page_queries[0][1][-2:] == (recovery.page_size, 0)
        assert len(due_outcomes) == 1
        after_due = rows_for(case)
        assert after_due["task"]["status"] == "failed"
        assert json.loads(after_due["task"]["result"]) == {
            "partial": [],
            "error": "frame_optimize_result_unknown_after_deadline",
        }
        assert after_due["task"]["claim_token"] is None
        assert after_due["task"]["claimed_by"] is None
        assert after_due["task"]["lease_until"] is None
        assert after_due["external"][0]["id"] == original_submission["id"]
        assert after_due["external"][0]["request_digest"] == original_submission["request_digest"]
        assert after_due["external"][0]["deadline_at"] == original_submission["deadline_at"]
        due_facts = [
            evidence
            for evidence in after_due["evidence"]
            if evidence["evidence_kind"] == "frame_optimize_due_v1"
        ]
        assert len(due_facts) == 1
        assert due_facts[0]["submission_id"] == original_submission["id"]
        assert len(after_due["external"]) == 1
        assert after_due["receipts"] == after_due["fees"] == []
        assert after_due["applications"] == after_due["work"] == []

        assert worker.run_claimed_once("batch-optimize") in {None, "pending"}
        assert len(client.user_contents) == 1
        assert len(rows_for(case)["external"]) == 1
    finally:
        case.engine.dispose()


@pytest.mark.parametrize(
    ("starting_phase", "expected_phases"),
    (
        ("fee_pending", ("delivery_pending", "finalize_pending", "done")),
        ("delivery_pending", ("finalize_pending", "done")),
        ("finalize_pending", ("done",)),
    ),
)
def test_runtime_reclaims_durable_work_without_reposting_provider_request(
    tmp_path, starting_phase, expected_phases
) -> None:
    case = admit_batch(
        tmp_path,
        frame_count=1,
        request_indexes=(0,),
        include_source_tables=True,
    )
    try:
        generation_claims = claim_adapter(
            case, token=f"continuation-parent-{starting_phase}"
        )
        parent = generation_claims.claim_next(
            "batch-optimize", "continuation-test-worker"
        )
        assert parent is not None
        item, prepared = prepare_item(
            case, parent, intents=intent_adapter(case, prefix=f"continuation-{starting_phase}")
        )
        receipt = persist_result(case, parent, prepared, text="durable result before restart")
        assert not isinstance(receipt, str)
        work = rows_for(case)["work"][0]

        if starting_phase in {"delivery_pending", "finalize_pending"}:
            assert fee_adapter(case, prefix=f"stage-fee-{starting_phase}").resolve_current_fee(
                parent, work.work_id
            ).disposition in {"charged", "no_charge"}
        if starting_phase == "finalize_pending":
            assert application_adapter(case, token=f"stage-application-{starting_phase}").deliver_current(
                parent, work.work_id
            ).kind == "applied"
        assert rows_for(case)["work"][0].phase == starting_phase
        assert generation_claims.release_claim(parent)

        worker = _worker_for(case, _UnexpectedClientContext)
        for expected_phase in expected_phases:
            outcome = worker.run_claimed_once("batch-optimize")
            state = rows_for(case)
            assert state["work"][0].phase == expected_phase, (
                outcome,
                state["task"]["status"],
                state["task"]["execution_generation"],
                state["work"][0].phase,
            )
            assert outcome == ("completed" if expected_phase == "done" else "pending")

        state = rows_for(case)
        assert state["task"]["status"] == "completed"
        assert state["task"]["result"] is not None
        assert len(state["external"]) == 1
        assert len(state["receipts"]) == 1
        assert state["receipts"][0].result_ref == receipt.result_ref
        assert len(state["fees"]) == len(state["creditLogs"]) == 1
        assert len(state["applications"]) == 1
        assert json.loads(state["task"]["result"]) == [
            {"index": 0, "optimized_text": "durable result before restart"}
        ]
        assert item.unit_id == state["work"][0].unit_id
    finally:
        case.engine.dispose()
