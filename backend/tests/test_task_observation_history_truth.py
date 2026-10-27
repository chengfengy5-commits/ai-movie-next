from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
import httpx
from sqlalchemy import Column, JSON, MetaData, String, Table, event, insert, select, true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import dialect as postgresql_dialect
from sqlalchemy.dialects.sqlite import dialect as sqlite_dialect
from sqlalchemy.orm import Session, sessionmaker

from chapter_asset_replacement_support import OWNER_TABLES, OwnerDatabase, create_owner_database
from haoai_backend.app import create_app
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.tokens import JoseTokenCodec
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.task_observation.application import get_task_receipt, get_task_request
from haoai_backend.task_observation.errors import TaskObservationNotFound
from haoai_backend.task_observation.history_truth import build_task_history_truth
from haoai_backend.task_observation.history_truth_persistence import _submission_task_id_values
from haoai_backend.task_observation.persistence import task_observation_unit_of_work_factory


@pytest.fixture
def owner_database(tmp_path):
    database = create_owner_database(tmp_path / "task-history-truth.sqlite")
    try:
        yield database
    finally:
        database.close()


def _task_values(task_id: str, owner_id: str, now: datetime) -> dict[str, Any]:
    return {
        "id": task_id,
        "user_id": owner_id,
        "type": "image-single",
        "message_id": "message-" + task_id,
        "status": "failed",
        "credit_cost": 0,
        "result": '{"signature":"private-result-secret"}',
        "request_data": '{"api_key":"private-request-secret"}',
        "model_name": "synthetic-model",
        "progress": 100,
        "progress_message": "任务结束",
        "external_task_id": None,
        "external_provider": None,
        "claimed_by": "private-worker-id",
        "lease_until": now - timedelta(minutes=1),
        "execution_generation": 4,
        "claim_token": "private-claim-secret",
        "recovery_status": "reconciling",
        "billing_status": "charged",
        "user_cancelled_at": None,
        "cancellation_reason": None,
        "created_at": now - timedelta(hours=2),
        "updated_at": now - timedelta(minutes=1),
    }


def _billing_unit(
    unit_id: str,
    task_id: str,
    now: datetime,
    *,
    operation: str,
    graph_version: str,
    quote: int,
    result_status: str,
    deadline: datetime,
    cancelled_at: datetime | None = None,
) -> dict[str, Any]:
    return {
        "id": unit_id,
        "task_id": task_id,
        "item_key": unit_id,
        "operation": operation,
        "step_graph_version": graph_version,
        "input_digest": "synthetic-input-digest",
        "quoted_amount": quote,
        "debited_amount": quote,
        "refunded_amount": 0,
        "billing_status": "no_charge" if quote == 0 else "charged",
        "result_status": result_status,
        "first_intent_at": now - timedelta(hours=2),
        "deadline_at": deadline,
        "success_confirmed_at": now - timedelta(hours=2) if result_status == "succeeded" else None,
        "user_cancelled_at": cancelled_at,
        "cancellation_reason": "user_requested" if cancelled_at is not None else None,
        "quote_id": None,
        "quote_unit_key": None,
        "candidate_digest": None,
        "created_at": now - timedelta(hours=2),
        "updated_at": now - timedelta(minutes=30),
    }


def _observe_reads(database: OwnerDatabase):
    activity: dict[str, Any] = {"statements": [], "commits": 0}
    tracking = {"enabled": True}

    def before_cursor_execute(_conn, _cursor, statement, _parameters, _context, _executemany):
        if tracking["enabled"]:
            activity["statements"].append(statement)

    def on_commit(_connection):
        if tracking["enabled"]:
            activity["commits"] += 1

    event.listen(database.engine, "before_cursor_execute", before_cursor_execute)
    event.listen(database.engine, "commit", on_commit)
    return activity, tracking, before_cursor_execute, on_commit


def _stop_observing(database, before_cursor_execute, on_commit) -> None:
    event.remove(database.engine, "before_cursor_execute", before_cursor_execute)
    event.remove(database.engine, "commit", on_commit)


def _assert_reads_only(activity: dict[str, Any]) -> None:
    writes = ("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP", "TRUNCATE")
    assert activity["commits"] == 0
    assert not [
        statement
        for statement in activity["statements"]
        if statement.lstrip().upper().startswith(writes)
    ]


@pytest.mark.parametrize(
    ("dialect_name", "column_type", "dialect_factory", "expected_function"),
    [
        ("sqlite", JSON(), sqlite_dialect, "json_each(task_submissions.task_ids)"),
        (
            "postgresql",
            JSON(),
            postgresql_dialect,
            "json_array_elements_text(task_submissions.task_ids)",
        ),
        (
            "postgresql",
            JSONB(),
            postgresql_dialect,
            "jsonb_array_elements_text(task_submissions.task_ids)",
        ),
    ],
)
def test_intent_array_expansion_selects_the_database_specific_function(
    dialect_name, column_type, dialect_factory, expected_function
):
    table = Table(
        "task_submissions",
        MetaData(),
        Column("operation", String),
        Column("task_ids", column_type),
    )
    session = SimpleNamespace(
        get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name=dialect_name))
    )

    task_id_values = _submission_task_id_values(session, table.c.task_ids)
    statement = (
        select(table.c.operation)
        .select_from(table.join(task_id_values, true()))
        .where(task_id_values.c.value == "task-a")
    )
    sql = str(statement.compile(dialect=dialect_factory()))

    assert expected_function in sql
    assert "task_submissions.operation" in sql
    if dialect_name == "postgresql":
        assert "history_submission_task_id(value)" in sql


def test_intent_array_expansion_is_unsupported_for_unknown_database_dialects():
    table = Table("task_submissions", MetaData(), Column("task_ids", JSON))
    session = SimpleNamespace(
        get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="unknown"))
    )

    assert _submission_task_id_values(session, table.c.task_ids) is None


def test_owner_receipt_projects_independent_truth_for_all_units_without_writes(owner_database):
    task_id = "history-truth-task"
    now = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)
    first_deadline = now - timedelta(hours=1)
    second_deadline = now - timedelta(hours=3)
    with owner_database.engine.begin() as connection:
        connection.execute(insert(OWNER_TABLES["ai_tasks"]).values(**_task_values(task_id, "user-a", now)))
        connection.execute(
            insert(OWNER_TABLES["billing_units"]),
            [
                _billing_unit(
                    "history-unit-first",
                    task_id,
                    now,
                    operation="image.single",
                    graph_version="image-single-v1",
                    quote=0,
                    result_status="unknown",
                    deadline=first_deadline,
                    cancelled_at=now - timedelta(minutes=30),
                ),
                _billing_unit(
                    "history-unit-second",
                    task_id,
                    now,
                    operation="video.single",
                    graph_version="video-single-v1",
                    quote=8,
                    result_status="succeeded",
                    deadline=second_deadline,
                ),
            ],
        )
        connection.execute(
            insert(OWNER_TABLES["execution_steps"]),
            [
                {
                    "id": "history-step-first",
                    "billing_unit_id": "history-unit-first",
                    "step_key": "generate",
                    "dependencies": [],
                    "input_digest": "input-first",
                    "input_snapshot": {"token": "private-step-secret"},
                    "required": True,
                    "step_order": 0,
                    "execution_status": "failed",
                    "created_at": now - timedelta(hours=2),
                    "updated_at": now - timedelta(minutes=30),
                },
                {
                    "id": "history-step-second",
                    "billing_unit_id": "history-unit-second",
                    "step_key": "generate",
                    "dependencies": [],
                    "input_digest": "input-second",
                    "input_snapshot": {"signature": "private-snapshot-secret"},
                    "required": True,
                    "step_order": 0,
                    "execution_status": "result_observed",
                    "created_at": now - timedelta(hours=2),
                    "updated_at": now - timedelta(hours=1),
                },
            ],
        )
        connection.execute(
            insert(OWNER_TABLES["external_submissions"]),
            [
                {
                    "id": "history-submission-first",
                    "step_id": "history-step-first",
                    "submission_key": "private-first-submission-key",
                    "provider": "openai",
                    "model_ref": "model-a",
                    "request_digest": "digest-first",
                    "request_snapshot": {"api_key": "private-submission-secret"},
                    "intent_at": now - timedelta(hours=2),
                    "submitted_at": now - timedelta(hours=2),
                    "external_task_id": "private-upstream-id",
                    "submission_status": "failed",
                    "deadline_at": first_deadline,
                    "created_at": now - timedelta(hours=2),
                    "updated_at": now - timedelta(minutes=30),
                },
                {
                    "id": "history-submission-second",
                    "step_id": "history-step-second",
                    "submission_key": "private-second-submission-key",
                    "provider": "aistarslab",
                    "model_ref": "model-b",
                    "request_digest": "digest-second",
                    "request_snapshot": {"password": "private-submission-secret-2"},
                    "intent_at": now - timedelta(hours=2),
                    "submitted_at": now - timedelta(hours=2),
                    "external_task_id": "private-upstream-id-2",
                    "submission_status": "completed",
                    "deadline_at": second_deadline,
                    "created_at": now - timedelta(hours=2),
                    "updated_at": now - timedelta(hours=1),
                },
            ],
        )
        connection.execute(
            insert(OWNER_TABLES["result_evidence"]).values(
                id="history-evidence-second",
                step_id="history-step-second",
                submission_id="history-submission-second",
                evidence_key="private-evidence-key",
                source="provider",
                evidence_kind="provider_video",
                payload_digest="private-payload-digest",
                result_location="https://cdn.example.test/out.mp4?X-Amz-Signature=private-media-signature",
                result_digest="private-result-digest",
                received_at=now - timedelta(hours=2),
                validity="valid",
                application_status="pending",
                applied_at=None,
            )
        )

    before = owner_database.snapshot()
    activity, tracking, before_cursor_execute, on_commit = _observe_reads(owner_database)
    factory = task_observation_unit_of_work_factory(owner_database.session_factory)
    try:
        receipt = get_task_receipt(factory, TrustedActor(user_id="user-a"), task_id=task_id)
        request_record = get_task_request(factory, TrustedActor(user_id="user-a"), task_id=task_id)
        with pytest.raises(TaskObservationNotFound):
            get_task_receipt(factory, TrustedActor(user_id="user-b"), task_id=task_id)
    finally:
        tracking["enabled"] = False
        _stop_observing(owner_database, before_cursor_execute, on_commit)

    truth = receipt["history_truth"]
    assert receipt["quoted_amount"] == 0
    assert request_record["credit_cost"] == 0
    assert truth["source"]["status"] == "registered"
    assert truth["billing_scope"] == {
        "unit_count": 2,
        "legacy_quote_scope": "first_unit",
        "aggregated": False,
    }
    assert truth["worker"]["status"] == "expired_lease"
    assert truth["worker"]["recovery_status"] == "reconciling"
    assert truth["provider"]["status"] == "partial"
    assert truth["provider"]["verified_unit_count"] == 1
    first_unit, second_unit = truth["provider"]["units"]
    assert first_unit["status"] == "unknown"
    assert first_unit["has_unverified_failed_submission"] is True
    assert second_unit["status"] == "verified_success"
    assert second_unit["timing"] == "late"
    assert {item["status"] for item in truth["local_disposition"]["observations"]} == {
        "cancel_requested",
        "deadline_elapsed_unsettled",
    }
    serialized_truth = json.dumps(truth, ensure_ascii=False)
    for secret in (
        "private-worker-id",
        "private-claim-secret",
        "private-request-secret",
        "private-result-secret",
        "private-step-secret",
        "private-snapshot-secret",
        "private-submission-secret",
        "private-upstream-id",
        "private-media-signature",
        "private-evidence-key",
    ):
        assert secret not in serialized_truth
    _assert_reads_only(activity)
    assert owner_database.snapshot() == before


def test_unknown_and_legacy_graph_sources_are_not_filled_in():
    base_facts = {
        "capabilities": {
            "worker_lease": False,
            "task_submission_intent": True,
            "billing_units": True,
            "execution_steps": True,
            "external_submissions": True,
            "result_evidence": True,
            "policy_stop_events": False,
            "local_continuation_work": False,
        },
        "units": [],
        "steps": [],
        "submissions": [],
        "evidences": [],
    }
    now = datetime(2026, 10, 28, tzinfo=timezone.utc)
    legacy = build_task_history_truth(
        {"id": "legacy", "user_id": "owner", "billing_status": "legacy_unreconciled"},
        base_facts,
        now=now,
    )
    missing = build_task_history_truth(
        {"id": "missing", "user_id": "owner", "billing_status": "charged"},
        base_facts,
        now=now,
    )
    unsupported = build_task_history_truth(
        {"id": "future", "user_id": "owner", "billing_status": "charged"},
        {
            **base_facts,
            "units": [
                {
                    "id": "unit",
                    "operation": "private-future-operation",
                    "step_graph_version": "future-v9",
                }
            ],
        },
        now=now,
    )
    unsupported_intent = build_task_history_truth(
        {"id": "future-intent", "user_id": "owner", "billing_status": "legacy_unreconciled"},
        {**base_facts, "intents": [{"operation": "private-future-operation"}]},
        now=now,
    )
    conflicting_source = build_task_history_truth(
        {"id": "conflicting-source", "user_id": "owner", "billing_status": "charged"},
        {
            **base_facts,
            "units": [{
                "id": "unit",
                "operation": "image.single",
                "step_graph_version": "image-single-v1",
            }],
            "intents": [{"operation": "video.single"}],
        },
        now=now,
    )

    assert legacy["source"]["status"] == "legacy"
    assert missing["source"]["status"] == "unknown"
    assert unsupported["source"]["status"] == "unsupported"
    assert unsupported["source"]["operations"] == []
    assert unsupported_intent["source"]["status"] == "unsupported"
    assert unsupported_intent["source"]["operations"] == []
    assert conflicting_source["source"]["status"] == "unknown"
    assert conflicting_source["source"]["conflicts"] == ["intent_graph_mismatch"]
    assert conflicting_source["source"]["operations"] == ["image.single"]
    assert unsupported["provider"]["status"] == "unsupported"


def test_history_source_uses_owner_scoped_intent_without_inventing_a_graph(owner_database):
    task_id = "history-intent-task"
    now = datetime(2026, 10, 9, 12, 0, 0)
    task = _task_values(task_id, "user-a", now)
    task["billing_status"] = "legacy_unreconciled"
    with owner_database.engine.begin() as connection:
        connection.execute(insert(OWNER_TABLES["ai_tasks"]).values(**task))
        connection.execute(
            insert(OWNER_TABLES["task_submissions"]),
            [
                {
                    "id": "owned-history-intent",
                    "user_id": "user-a",
                    "operation": "image.single",
                    "idempotency_key": "private-owned-idempotency-key",
                    "request_digest": "private-owned-request-digest",
                    "task_ids": [task_id],
                    "task_quote_id": None,
                    "status": "accepted",
                    "created_at": now,
                },
                {
                    "id": "foreign-history-intent",
                    "user_id": "user-b",
                    "operation": "video.single",
                    "idempotency_key": "private-foreign-idempotency-key",
                    "request_digest": "private-foreign-request-digest",
                    "task_ids": [task_id],
                    "task_quote_id": None,
                    "status": "accepted",
                    "created_at": now,
                },
            ],
        )

    before = owner_database.snapshot()
    activity, tracking, before_cursor_execute, on_commit = _observe_reads(owner_database)
    factory = task_observation_unit_of_work_factory(owner_database.session_factory)
    try:
        receipt = get_task_receipt(factory, TrustedActor(user_id="user-a"), task_id=task_id)
    finally:
        tracking["enabled"] = False
        _stop_observing(owner_database, before_cursor_execute, on_commit)

    truth = receipt["history_truth"]
    assert truth["source"] == {
        "status": "registered",
        "source": "task_submissions.operation",
        "unit_count": 0,
        "registered_unit_count": 0,
        "operations": ["image.single"],
        "conflicts": [],
    }
    assert truth["billing_scope"] == {
        "unit_count": 0,
        "legacy_quote_scope": "task_record",
        "aggregated": False,
    }
    assert truth["provider"]["status"] == "unknown"
    serialized_truth = json.dumps(truth, ensure_ascii=False)
    assert "private-owned-idempotency-key" not in serialized_truth
    assert "private-foreign-idempotency-key" not in serialized_truth
    assert "private-owned-request-digest" not in serialized_truth
    _assert_reads_only(activity)
    assert owner_database.snapshot() == before


def test_provider_result_timing_is_scoped_and_keeps_timely_and_late_evidence_distinct():
    now = datetime(2026, 10, 28, 12, 0, 0, tzinfo=timezone.utc)
    capabilities = {
        "worker_lease": True,
        "billing_units": True,
        "execution_steps": True,
        "external_submissions": True,
        "result_evidence": True,
        "policy_stop_events": True,
        "local_continuation_work": True,
    }
    facts = {
        "capabilities": capabilities,
        "units": [{
            "id": "video-unit",
            "operation": "video.single",
            "step_graph_version": "video-single-v1",
            "result_status": "failed",
        }],
        "steps": [
            {"id": "video-attempt-0", "billing_unit_id": "video-unit", "execution_status": "failed"},
            {"id": "video-attempt-1", "billing_unit_id": "video-unit", "execution_status": "result_observed"},
        ],
        "submissions": [
            {
                "id": "submission-timely",
                "step_id": "video-attempt-0",
                "provider": "aistarslab",
                "submission_status": "failed",
                "deadline_at": now - timedelta(hours=1),
            },
            {
                "id": "submission-late",
                "step_id": "video-attempt-1",
                "provider": "aistarslab",
                "submission_status": "completed",
                "deadline_at": now - timedelta(hours=1),
            },
        ],
        "evidences": [
            {
                "step_id": "video-attempt-0",
                "submission_id": "submission-timely",
                "source": "provider",
                "evidence_kind": "provider_video",
                "received_at": now - timedelta(hours=2),
                "validity": "valid",
            },
            {
                "step_id": "video-attempt-1",
                "submission_id": "submission-late",
                "source": "provider",
                "evidence_kind": "provider_video",
                "received_at": now - timedelta(minutes=30),
                "validity": "valid",
            },
        ],
        "policy_stops": [],
        "continuation_work": [],
    }
    task = {"id": "video-task", "user_id": "user-a", "status": "failed"}

    truth = build_task_history_truth(task, facts, now=now)
    provider_unit = truth["provider"]["units"][0]

    assert truth["provider"]["status"] == "verified_success"
    assert provider_unit["status"] == "verified_success"
    assert provider_unit["unit_result_status"] == "failed"
    assert provider_unit["timing"] == "mixed"
    assert provider_unit["timely_evidence_count"] == 1
    assert provider_unit["late_evidence_count"] == 1
    assert provider_unit["unknown_timing_count"] == 0

    incomplete_timing = build_task_history_truth(
        task,
        {
            **facts,
            "evidences": facts["evidences"] + [{
                "step_id": "video-attempt-1",
                "submission_id": "submission-late",
                "source": "provider",
                "evidence_kind": "provider_video",
                "received_at": None,
                "validity": "valid",
            }],
        },
        now=now,
    )["provider"]["units"][0]
    assert incomplete_timing["timing"] == "mixed"
    assert incomplete_timing["late_evidence_count"] == 1
    assert incomplete_timing["unknown_timing_count"] == 1

    explicit_failure = build_task_history_truth(
        task,
        {
            **facts,
            "evidences": [{
                "step_id": "video-attempt-0",
                "submission_id": "submission-timely",
                "source": "provider",
                "evidence_kind": "provider_error",
                "received_at": now - timedelta(hours=2),
                "validity": "valid",
            }],
        },
        now=now,
    )
    assert explicit_failure["provider"]["status"] == "known_failure"


def test_real_asgi_jwt_receipt_uses_owner_filter_and_no_business_commit(owner_database):
    now = datetime(2026, 10, 9, 12, 0, 0)
    task_id = "jwt-history-task"
    task_values = _task_values(task_id, "user-a", now)
    task_values.update(
        status="processing",
        billing_status="legacy_unreconciled",
        result="legacy result",
        request_data="legacy request",
        claimed_by="private-worker-id",
        lease_until=now - timedelta(minutes=1),
    )
    with owner_database.engine.begin() as connection:
        connection.execute(insert(OWNER_TABLES["ai_tasks"]).values(**task_values))

    opened_business_sessions: list[BusinessSession] = []

    class BusinessSession(Session):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.commit_count = 0
            self.closed_successfully = False

        def commit(self) -> None:
            self.commit_count += 1
            super().commit()

        def close(self) -> None:
            super().close()
            self.closed_successfully = True

    business_factory = sessionmaker(
        owner_database.engine,
        class_=BusinessSession,
        expire_on_commit=False,
        future=True,
    )

    def open_business_session():
        session = business_factory()
        opened_business_sessions.append(session)
        return session

    secret = "task-history-asgi-synthetic-jwt-secret"
    runtime = AuthenticationRuntime(
        session_factory=owner_database.session_factory,
        secret_key=secret,
        token_codec=JoseTokenCodec(secret),
        clock=lambda: now,
        epoch=lambda: now.replace(tzinfo=timezone.utc).timestamp(),
    )
    app = create_app(
        session_factory=open_business_session,
        authentication_runtime=runtime,
    )
    codec = JoseTokenCodec(secret)

    def token_for(user_id: str, jti: str) -> str:
        expiry = datetime.now(timezone.utc) + timedelta(minutes=5)
        return codec.encode(
            {
                "sub": user_id,
                "jti": jti,
                "password_version": str(int(now.timestamp())),
                "exp": expiry,
            }
        )

    async def request(user_id: str, jti: str) -> httpx.Response:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://task-history.test") as client:
            return await client.get(
                "/api/chat/tasks",
                params={"task_id": task_id},
                headers={"Authorization": "Bearer " + token_for(user_id, jti)},
            )

    before = owner_database.snapshot()
    response = asyncio.run(request("user-a", "test-jti-a"))
    foreign_response = asyncio.run(request("user-b", "test-jti-b"))

    assert response.status_code == 200
    body = response.json()
    assert body["history_truth"]["source"]["status"] == "legacy"
    assert body["history_truth"]["worker"]["status"] == "expired_lease"
    assert body["history_truth"]["provider"]["status"] == "unknown"
    assert foreign_response.status_code == 404
    assert len(opened_business_sessions) == 2
    assert all(session.commit_count == 0 and session.closed_successfully for session in opened_business_sessions)
    assert owner_database.snapshot() == before
