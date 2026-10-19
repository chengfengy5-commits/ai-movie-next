"""Focused SQL coverage for frame.review request observations and due recovery."""

from __future__ import annotations

import asyncio
import json
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.generation_contracts.ports import GenerationRuntimeConfig
from haoai_backend.generation_contracts.types import OwnerTaskScope
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
from haoai_backend.generation_core.frame_review_claims import SqlAlchemyFrameReviewClaims
from haoai_backend.generation_core.frame_review_intents import SqlAlchemyFrameReviewIntents
from haoai_backend.generation_core.schema import (
    ApplicationRecordRow,
    GenerationResultReceiptRow,
    LocalContinuationWorkRow,
    UnitFeeDecisionRow,
)
from haoai_backend.generation_credentials.application import build_credential_registry
from haoai_backend.generation_runtime.frame_review_assembly import (
    FrameReviewWorkerConfiguration,
    build_frame_review_worker_runtime,
)

from generation_execution.test_frame_review_results import _claimed_review
from generation_execution.test_frame_review_admission import _build_app, _post_review, _review_body


_OBSERVATION_PREFIX = "frame-review-observation-v1:"


class _Response:
    def __init__(
        self,
        payload: object,
        status_code: int,
        *,
        body: bytes | None = None,
    ) -> None:
        self.status_code = status_code
        self._body = body or json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.headers = {"content-length": str(len(self._body))}

    async def iter_bytes(self, *, chunk_size: int):
        assert chunk_size > 0
        yield self._body


class _Client:
    def __init__(
        self,
        requests: list[dict[str, object]],
        status_code: int,
        *,
        payload: object | None = None,
        body: bytes | None = None,
        request_error: Exception | None = None,
    ) -> None:
        self._requests = requests
        self._status_code = status_code
        self._payload = payload
        self._body = body
        self._request_error = request_error

    async def request(self, method, url, **kwargs):
        self._requests.append({"method": method, "url": url, **kwargs})
        if self._request_error is not None:
            raise self._request_error
        payload = self._payload
        if payload is None:
            payload = (
                {"choices": [{"message": {"content": "应被受控拒绝"}}]}
                if self._status_code == 200
                else {"error": "provider response must not be persisted"}
            )
        return _Response(payload, self._status_code, body=self._body)


class _ClientContext(AbstractAsyncContextManager):
    def __init__(self, client: _Client | None = None, *, enter_error: Exception | None = None) -> None:
        self._client = client
        self._enter_error = enter_error

    async def __aenter__(self):
        if self._enter_error is not None:
            raise self._enter_error
        return self._client

    async def __aexit__(self, *_args):
        return False


class _Ids:
    def __init__(self, prefix: str) -> None:
        self._prefix = prefix
        self._count = 0

    def __call__(self) -> str:
        self._count += 1
        return f"{self._prefix}-{self._count}"


def _runtime(
    engine,
    capabilities,
    now,
    requests,
    *,
    status_code: int,
    payload: object | None = None,
    body: bytes | None = None,
    request_error: Exception | None = None,
    enter_error: Exception | None = None,
    credential_resolver=None,
):
    sessions = sessionmaker(engine, expire_on_commit=False)
    registry = build_credential_registry(capabilities)
    ids = _Ids("recovery-runtime")
    config = GenerationRuntimeConfig(
        session_factory=sessions,
        db_clock=lambda: now[0],
        id_factory=ids,
        http_client_factory=lambda: _ClientContext(
            _Client(
                requests,
                status_code,
                payload=payload,
                body=body,
                request_error=request_error,
            ),
            enter_error=enter_error,
        ),
        credential_resolver=credential_resolver or registry.resolve_original_credential,
        provider_factories=(),
        media_store_factory=lambda: object(),
        local_muse_staging_dir=None,
        public_media_fetcher_factory=lambda: object(),
        bound_provider_content_fetcher_factory=lambda: object(),
        local_muse_capability_factory=lambda: object(),
        r2_storage_factory=lambda: object(),
        shutdown_timeout_seconds=1.0,
    )
    return build_frame_review_worker_runtime(
        FrameReviewWorkerConfiguration(config, capabilities)
    )


def _admitted_runtime(tmp_path, monkeypatch, *, status_code: int, **options):
    now = [datetime.now(timezone.utc)]
    monkeypatch.setattr("generation_core.test_admission_staging._G2_JOINT_NOW", now[0])
    monkeypatch.setattr("generation_execution.test_frame_review_admission._G2_JOINT_NOW", now[0])
    engine, app, token, capabilities, _crypto_state = _build_app(tmp_path, monkeypatch)
    response = asyncio.run(_post_review(app, token, _review_body()))
    assert response.status_code == 200, response.text
    requests: list[dict[str, object]] = []
    runtime = _runtime(
        engine,
        capabilities,
        now,
        requests,
        status_code=status_code,
        **options,
    )
    return engine, now, requests, runtime, capabilities


@pytest.mark.parametrize("status_code", (400, 401, 429))
def test_registered_worker_persists_a_controlled_provider_rejection_once(
    tmp_path, monkeypatch, status_code
) -> None:
    engine, now, requests, runtime, _capabilities = _admitted_runtime(
        tmp_path, monkeypatch, status_code=status_code
    )
    try:
        assert runtime.run_claimed_once("ai-review") == "pending"
        assert len(requests) == 1

        with engine.connect() as connection:
            task = connection.execute(select(AITaskRow.__table__)).mappings().one()
            unit = connection.execute(select(BillingUnitRow.__table__)).mappings().one()
            step = connection.execute(select(ExecutionStepRow.__table__)).mappings().one()
            external = connection.execute(
                select(ExternalSubmissionRow.__table__)
            ).mappings().one()
            observations = connection.execute(
                select(ResultEvidenceRow.__table__).where(
                    ResultEvidenceRow.evidence_key.like(f"{_OBSERVATION_PREFIX}%")
                )
            ).mappings().all()

        assert task["status"] == "failed"
        assert json.loads(task["result"]) == {
            "code": "provider_rejected",
            "error": "文本服务拒绝了原分镜复核请求。",
        }
        assert unit["result_status"] == "failed"
        assert unit["billing_status"] == "unbilled"
        assert unit["debited_amount"] == unit["refunded_amount"] == 0
        assert step["execution_status"] == "failed"
        assert external["submission_status"] == "failed"
        assert len(observations) == 1
        observation_text = observations[0]["result_location"]
        assert isinstance(observation_text, str)
        assert "provider_rejected" in observation_text
        assert f'"http_status":{status_code}' in observation_text
        assert "provider response must not be persisted" not in observation_text
        assert "Authorization" not in observation_text

        assert runtime.run_claimed_once("ai-review") == "pending"
        assert len(requests) == 1
    finally:
        engine.dispose()


def test_due_recovery_settles_at_the_original_deadline_without_financial_rows(
    tmp_path, monkeypatch
) -> None:
    engine, sessions, now, claim, capabilities, verified, intent = _claimed_review(
        tmp_path, monkeypatch
    )
    wallet_before = None
    with sessions() as session:
        wallet_before = session.execute(
            select(UserCreditRow.__table__)
        ).mappings().all()
    from haoai_backend.generation_core.frame_review_recovery import (
        SqlAlchemyFrameReviewRecovery,
    )

    recovery = SqlAlchemyFrameReviewRecovery(
        session_factory=sessions,
        db_clock=lambda: now[0],
        id_factory=_Ids("recovery-due"),
        crypto=capabilities.crypto,
        proof_authority=capabilities.proof_authority,
    )
    try:
        now[0] = intent.deadline_at - timedelta(microseconds=1)
        assert recovery.scan_due() == ()
        now[0] = intent.deadline_at
        assert recovery.scan_due() == ("settled",)

        with sessions() as session:
            task = session.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == claim.scope.task_id)
            ).mappings().one()
            unit = session.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.id == verified.unit_id
                )
            ).mappings().one()
            step = session.execute(
                select(ExecutionStepRow.__table__).where(
                    ExecutionStepRow.id == intent.step_id
                )
            ).mappings().one()
            external = session.execute(
                select(ExternalSubmissionRow.__table__).where(
                    ExternalSubmissionRow.id == intent.external_submission_id
                )
            ).mappings().one()
            observations = session.execute(
                select(ResultEvidenceRow.__table__).where(
                    ResultEvidenceRow.evidence_key.like(f"{_OBSERVATION_PREFIX}%")
                )
            ).mappings().all()
            assert session.execute(select(GenerationResultReceiptRow.__table__)).all() == []
            assert session.execute(select(UnitFeeDecisionRow.__table__)).all() == []
            assert session.execute(select(CreditLogRow.__table__)).all() == []
            assert session.execute(select(UserCreditRow.__table__)).mappings().all() == wallet_before

        assert task["status"] == "failed"
        assert unit["result_status"] == "unknown"
        assert unit["billing_status"] == "unbilled"
        assert unit["first_intent_at"] == intent.first_intent_at.replace(tzinfo=None)
        assert unit["deadline_at"] == intent.deadline_at.replace(tzinfo=None)
        assert unit["debited_amount"] == unit["refunded_amount"] == 0
        assert step["execution_status"] == "reconciling"
        assert external["submission_status"] == "unknown"
        assert len(observations) == 1
        assert "outcome_unknown" in observations[0]["result_location"]
        assert recovery.scan_due() == ()
    finally:
        engine.dispose()


def test_due_recovery_keeps_a_changed_original_deadline_pending(
    tmp_path, monkeypatch
) -> None:
    engine, sessions, now, claim, capabilities, verified, intent = _claimed_review(
        tmp_path, monkeypatch
    )
    from haoai_backend.generation_core.frame_review_recovery import (
        SqlAlchemyFrameReviewRecovery,
    )

    recovery = SqlAlchemyFrameReviewRecovery(
        session_factory=sessions,
        db_clock=lambda: now[0],
        id_factory=_Ids("recovery-tamper"),
        crypto=capabilities.crypto,
        proof_authority=capabilities.proof_authority,
    )
    changed_deadline = intent.deadline_at + timedelta(seconds=1)
    with engine.begin() as connection:
        connection.execute(
            update(BillingUnitRow.__table__)
            .where(BillingUnitRow.id == verified.unit_id)
            .values(deadline_at=changed_deadline.replace(tzinfo=None))
        )
        connection.execute(
            update(ExternalSubmissionRow.__table__)
            .where(ExternalSubmissionRow.id == intent.external_submission_id)
            .values(deadline_at=changed_deadline.replace(tzinfo=None))
        )
    try:
        now[0] = changed_deadline
        assert recovery.scan_due() == ("pending",)
        with sessions() as session:
            task = session.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == claim.scope.task_id)
            ).mappings().one()
            unit = session.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.id == verified.unit_id
                )
            ).mappings().one()
        assert task["status"] == "processing"
        assert unit["result_status"] == "pending"
        assert unit["deadline_at"] == changed_deadline.replace(tzinfo=None)
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("field", "value"),
    (
        pytest.param("provider", "other-provider", id="provider-changed"),
        pytest.param("model_ref", "other-model", id="model-changed"),
    ),
)
def test_due_recovery_rejects_external_provider_model_drift(
    tmp_path, monkeypatch, field, value
) -> None:
    engine, sessions, now, claim, capabilities, verified, intent = _claimed_review(
        tmp_path, monkeypatch
    )
    from haoai_backend.generation_core.frame_review_recovery import (
        SqlAlchemyFrameReviewRecovery,
    )

    recovery = SqlAlchemyFrameReviewRecovery(
        session_factory=sessions,
        db_clock=lambda: now[0],
        id_factory=_Ids("recovery-model-drift"),
        crypto=capabilities.crypto,
        proof_authority=capabilities.proof_authority,
    )
    with sessions() as session:
        original_step_snapshot = session.execute(
            select(ExecutionStepRow.__table__).where(
                ExecutionStepRow.id == intent.step_id
            )
        ).mappings().one()["input_snapshot"]
        original_request_snapshot = session.execute(
            select(ExternalSubmissionRow.__table__).where(
                ExternalSubmissionRow.id == intent.external_submission_id
            )
        ).mappings().one()["request_snapshot"]
    with engine.begin() as connection:
        connection.execute(
            update(ExternalSubmissionRow.__table__)
            .where(ExternalSubmissionRow.id == intent.external_submission_id)
            .values({field: value})
        )

    def read_state():
        with sessions() as session:
            task = session.execute(
                select(AITaskRow.__table__).where(
                    AITaskRow.id == claim.scope.task_id
                )
            ).mappings().one()
            unit = session.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.id == verified.unit_id
                )
            ).mappings().one()
            step = session.execute(
                select(ExecutionStepRow.__table__).where(
                    ExecutionStepRow.id == intent.step_id
                )
            ).mappings().one()
            external = session.execute(
                select(ExternalSubmissionRow.__table__).where(
                    ExternalSubmissionRow.id == intent.external_submission_id
                )
            ).mappings().one()
            parent = session.execute(
                select(TaskSubmissionRow.__table__).where(
                    TaskSubmissionRow.id == verified.submission_id
                )
            ).mappings().one()
            evidence = session.execute(
                select(ResultEvidenceRow.__table__).where(
                    ResultEvidenceRow.step_id == intent.step_id
                )
            ).mappings().all()
            receipts = session.execute(
                select(GenerationResultReceiptRow.__table__).where(
                    GenerationResultReceiptRow.__table__.c.unit_id == verified.unit_id
                )
            ).mappings().all()
            work = session.execute(
                select(LocalContinuationWorkRow.__table__).where(
                    LocalContinuationWorkRow.__table__.c.unit_id == verified.unit_id
                )
            ).mappings().all()
            fees = session.execute(
                select(UnitFeeDecisionRow.__table__).where(
                    UnitFeeDecisionRow.__table__.c.unit_id == verified.unit_id
                )
            ).mappings().all()
            applications = session.execute(
                select(ApplicationRecordRow.__table__).where(
                    ApplicationRecordRow.__table__.c.task_id == claim.scope.task_id
                )
            ).mappings().all()
            logs = session.execute(
                select(CreditLogRow.__table__).where(
                    CreditLogRow.__table__.c.billing_unit_id == verified.unit_id
                )
            ).mappings().all()
            wallet = session.execute(select(UserCreditRow.__table__)).mappings().all()
        graph_rows = tuple(
            dict(row) for row in (task, unit, step, parent, external)
        )
        return (
            graph_rows,
            tuple(map(dict, evidence)),
            tuple(map(dict, receipts)),
            tuple(map(dict, work)),
            tuple(map(dict, fees)),
            tuple(map(dict, applications)),
            tuple(map(dict, logs)),
            tuple(map(dict, wallet)),
        )

    try:
        assert original_step_snapshot["snapshot_digest"]
        assert (
            original_request_snapshot["provider"]
            == verified.snapshot.model_ref.provider
        )
        assert (
            original_request_snapshot["model_id"]
            == verified.snapshot.model_ref.model_id
        )
        before = read_state()
        assert before[0][4][field] == value
        assert before[0][2]["input_snapshot"] == original_step_snapshot
        assert before[0][4]["request_snapshot"] == original_request_snapshot

        now[0] = intent.deadline_at
        outcome = recovery.consume_due(
            OwnerTaskScope(
                user_id=claim.scope.user_id,
                task_id=claim.scope.task_id,
                operation="frame.review",
            )
        )

        assert outcome == "pending"
        assert read_state() == before
    finally:
        engine.dispose()


@pytest.mark.parametrize("barrier", ("after-lock", "before-write"))
def test_explicit_due_is_stopped_and_tracked_during_shutdown(
    tmp_path, monkeypatch, barrier
) -> None:
    from threading import Event, Thread

    from haoai_backend.generation_core import frame_review_recovery as recovery_module
    from haoai_backend.generation_core.frame_review_recovery import (
        SqlAlchemyFrameReviewRecovery,
    )

    engine, sessions, now, claim, capabilities, verified, intent = _claimed_review(
        tmp_path, monkeypatch
    )
    requests: list[dict[str, object]] = []
    runtime = _runtime(engine, capabilities, now, requests, status_code=500)
    now[0] = intent.deadline_at
    entered = Event()
    resume = Event()
    shutdown_done = Event()
    outcomes: list[str] = []
    receipts: list[object] = []
    errors: list[BaseException] = []

    def read_state():
        with Session(engine) as session:
            task = dict(
                session.execute(
                    select(AITaskRow.__table__).where(
                        AITaskRow.id == claim.scope.task_id
                    )
                ).mappings().one()
            )
            unit = dict(
                session.execute(
                    select(BillingUnitRow.__table__).where(
                        BillingUnitRow.id == verified.unit_id
                    )
                ).mappings().one()
            )
            step = dict(
                session.execute(
                    select(ExecutionStepRow.__table__).where(
                        ExecutionStepRow.id == intent.step_id
                    )
                ).mappings().one()
            )
            external = dict(
                session.execute(
                    select(ExternalSubmissionRow.__table__).where(
                        ExternalSubmissionRow.id == intent.external_submission_id
                    )
                ).mappings().one()
            )
            evidence = tuple(
                map(
                    dict,
                    session.execute(
                        select(ResultEvidenceRow.__table__).where(
                            ResultEvidenceRow.submission_id
                            == intent.external_submission_id
                        )
                    ).mappings().all(),
                )
            )
            receipts = tuple(
                map(
                    dict,
                    session.execute(
                        select(GenerationResultReceiptRow.__table__).where(
                            GenerationResultReceiptRow.__table__.c.unit_id
                            == verified.unit_id
                        )
                    ).mappings().all(),
                )
            )
            work = tuple(
                map(
                    dict,
                    session.execute(
                        select(LocalContinuationWorkRow.__table__).where(
                            LocalContinuationWorkRow.__table__.c.unit_id
                            == verified.unit_id
                        )
                    ).mappings().all(),
                )
            )
            fees = tuple(
                map(
                    dict,
                    session.execute(
                        select(UnitFeeDecisionRow.__table__).where(
                            UnitFeeDecisionRow.__table__.c.unit_id
                            == verified.unit_id
                        )
                    ).mappings().all(),
                )
            )
            applications = tuple(
                map(
                    dict,
                    session.execute(
                        select(ApplicationRecordRow.__table__).where(
                            ApplicationRecordRow.__table__.c.task_id
                            == claim.scope.task_id
                        )
                    ).mappings().all(),
                )
            )
            logs = tuple(
                map(
                    dict,
                    session.execute(
                        select(CreditLogRow.__table__).where(
                            CreditLogRow.__table__.c.billing_unit_id
                            == verified.unit_id
                        )
                    ).mappings().all(),
                )
            )
            wallets = tuple(
                map(
                    dict,
                    session.execute(select(UserCreditRow.__table__)).mappings().all(),
                )
            )
        return (
            task,
            unit,
            step,
            external,
            evidence,
            receipts,
            work,
            fees,
            applications,
            logs,
            wallets,
        )

    original_lock_graph = SqlAlchemyFrameReviewRecovery._lock_graph
    original_stage_due = recovery_module._stage_due_rows

    def wait_at_barrier():
        entered.set()
        if not resume.wait(timeout=5.0):
            raise TimeoutError("test barrier was not released")

    def blocking_lock_graph(self, session, scope, **kwargs):
        graph = original_lock_graph(self, session, scope, **kwargs)
        if graph is not None:
            wait_at_barrier()
        return graph

    def blocking_stage_due(session, graph, stage, *args, **kwargs):
        wait_at_barrier()
        return original_stage_due(session, graph, stage, *args, **kwargs)

    if barrier == "after-lock":
        monkeypatch.setattr(
            SqlAlchemyFrameReviewRecovery, "_lock_graph", blocking_lock_graph
        )
    else:
        monkeypatch.setattr(recovery_module, "_stage_due_rows", blocking_stage_due)

    scope = OwnerTaskScope(
        user_id=claim.scope.user_id,
        task_id=claim.scope.task_id,
        operation="frame.review",
    )
    before = read_state()

    def consume():
        try:
            outcomes.append(runtime.consume_cancel_and_due(scope))
        except BaseException as exc:
            errors.append(exc)

    def shutdown():
        try:
            receipts.append(runtime.shutdown("explicit due recovery stop test"))
        except BaseException as exc:
            errors.append(exc)
        finally:
            shutdown_done.set()

    consumer = Thread(target=consume, name=f"i06-due-{barrier}")
    shutdown_thread = Thread(target=shutdown, name=f"i06-shutdown-{barrier}")
    try:
        consumer.start()
        assert entered.wait(timeout=3.0)
        with runtime._lock:
            active = runtime._active_recoveries.get("ai-review")
        shutdown_thread.start()
        if active is None:
            assert shutdown_done.wait(timeout=3.0)
        else:
            assert active.stop_signal.wait(timeout=3.0)
        resume.set()
        consumer.join(timeout=3.0)
        shutdown_thread.join(timeout=3.0)
        assert not consumer.is_alive()
        assert not shutdown_thread.is_alive()
        assert errors == []
        assert len(receipts) == 1
        assert len(outcomes) == 1
        assert outcomes[0] == "pending"
        assert receipts[0].timed_out is False
        assert "recovery:ai-review" in receipts[0].recovery_plan_refs
        assert "recovery:ai-review" in receipts[0].released_resources
        assert receipts[0].unreleased_resources == ()
        assert read_state() == before
        assert requests == []
        with runtime._lock:
            assert runtime._active_recoveries == {}
    finally:
        resume.set()
        if consumer.ident is not None:
            consumer.join(timeout=3.0)
        if shutdown_thread.ident is not None:
            shutdown_thread.join(timeout=3.0)
        engine.dispose()


@pytest.mark.parametrize(
    ("status_code", "payload", "body", "request_error"),
    (
        pytest.param(500, None, None, None, id="http-500"),
        pytest.param(200, None, b"not-json", None, id="corrupt-200"),
        pytest.param(
            200,
            {"choices": [{"message": {"content": ""}}]},
            None,
            None,
            id="empty-200",
        ),
        pytest.param(200, None, None, TimeoutError("synthetic timeout"), id="timeout"),
    ),
)
@pytest.mark.parametrize(
    "deadline_offset_seconds",
    (0, 1),
    ids=("exact-deadline", "after-deadline"),
)
def test_unknown_provider_outcome_stays_unknown_and_due_never_reposts(
    tmp_path,
    monkeypatch,
    status_code,
    payload,
    body,
    request_error,
    deadline_offset_seconds,
) -> None:
    engine, now, requests, runtime, capabilities = _admitted_runtime(
        tmp_path,
        monkeypatch,
        status_code=status_code,
        payload=payload,
        body=body,
        request_error=request_error,
    )
    try:
        assert runtime.run_claimed_once("ai-review") == "pending"
        assert len(requests) == 1
        with Session(engine) as session:
            task = session.execute(select(AITaskRow.__table__)).mappings().one()
            unit = session.execute(select(BillingUnitRow.__table__)).mappings().one()
            step = session.execute(select(ExecutionStepRow.__table__)).mappings().one()
            external = session.execute(
                select(ExternalSubmissionRow.__table__)
            ).mappings().one()
            wallet_before = session.execute(
                select(UserCreditRow.__table__)
            ).mappings().all()
            evidence = session.execute(
                select(ResultEvidenceRow.__table__).where(
                    ResultEvidenceRow.evidence_key.like(f"{_OBSERVATION_PREFIX}%")
                )
            ).mappings().all()

        assert task["status"] == "queued"
        assert task["result"] is None
        assert unit["result_status"] == "unknown"
        assert unit["billing_status"] == "unbilled"
        assert unit["debited_amount"] == unit["refunded_amount"] == 0
        assert step["execution_status"] == "reconciling"
        assert external["submission_status"] == "unknown"
        assert len(evidence) == 1
        assert '"kind":"outcome_unknown"' in evidence[0]["result_location"]
        assert "provider response must not be persisted" not in evidence[0]["result_location"]

        original_first = unit["first_intent_at"]
        original_deadline = unit["deadline_at"]
        assert original_first is not None and original_deadline is not None
        deadline = original_deadline.replace(tzinfo=timezone.utc)
        assert deadline == original_first.replace(tzinfo=timezone.utc) + timedelta(
            seconds=7200
        )
        restarted_runtime = _runtime(
            engine,
            capabilities,
            now,
            requests,
            status_code=status_code,
            payload=payload,
            body=body,
            request_error=request_error,
        )
        assert restarted_runtime.run_claimed_once("ai-review") == "pending"
        assert len(requests) == 1
        with Session(engine) as session:
            repeated_task = session.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == task["id"])
            ).mappings().one()
            repeated_unit = session.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.id == unit["id"]
                )
            ).mappings().one()
            repeated_step = session.execute(
                select(ExecutionStepRow.__table__).where(
                    ExecutionStepRow.id == step["id"]
                )
            ).mappings().one()
            repeated_external = session.execute(
                select(ExternalSubmissionRow.__table__).where(
                    ExternalSubmissionRow.id == external["id"]
                )
            ).mappings().one()
            repeated_evidence = session.execute(
                select(ResultEvidenceRow.__table__).where(
                    ResultEvidenceRow.evidence_key.like(f"{_OBSERVATION_PREFIX}%")
                )
            ).mappings().all()
        assert repeated_task["status"] == "queued"
        assert repeated_unit["result_status"] == "unknown"
        assert repeated_unit["first_intent_at"] == original_first
        assert repeated_unit["deadline_at"] == original_deadline
        assert repeated_step["execution_status"] == "reconciling"
        assert repeated_external["submission_status"] == "unknown"
        assert len(repeated_evidence) == 1

        now[0] = deadline + timedelta(seconds=deadline_offset_seconds)
        scope = OwnerTaskScope(
            user_id=task["user_id"], task_id=task["id"], operation="frame.review"
        )
        assert restarted_runtime.consume_cancel_and_due(scope) == "settled"

        with Session(engine) as session:
            due_task = session.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == task["id"])
            ).mappings().one()
            due_unit = session.execute(
                select(BillingUnitRow.__table__).where(BillingUnitRow.id == unit["id"])
            ).mappings().one()
            due_step = session.execute(
                select(ExecutionStepRow.__table__).where(
                    ExecutionStepRow.id == step["id"]
                )
            ).mappings().one()
            due_external = session.execute(
                select(ExternalSubmissionRow.__table__).where(
                    ExternalSubmissionRow.id == external["id"]
                )
            ).mappings().one()
            due_evidence = session.execute(
                select(ResultEvidenceRow.__table__).where(
                    ResultEvidenceRow.evidence_key.like(f"{_OBSERVATION_PREFIX}%")
                )
            ).mappings().all()
            assert session.execute(
                select(GenerationResultReceiptRow.__table__)
            ).all() == []
            assert session.execute(select(UnitFeeDecisionRow.__table__)).all() == []
            assert session.execute(select(CreditLogRow.__table__)).all() == []
            assert session.execute(
                select(UserCreditRow.__table__)
            ).mappings().all() == wallet_before

        assert due_task["status"] == "failed"
        assert json.loads(due_task["result"])["code"] == "provider_outcome_unknown"
        assert due_unit["result_status"] == "unknown"
        assert due_unit["first_intent_at"] == original_first
        assert due_unit["deadline_at"] == original_deadline
        assert due_unit["debited_amount"] == due_unit["refunded_amount"] == 0
        assert due_step["execution_status"] == "reconciling"
        assert due_external["submission_status"] == "unknown"
        assert len(due_evidence) == 1
        assert runtime.run_claimed_once("ai-review") == "pending"
        assert len(requests) == 1
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("runtime_options", "expected_code"),
    (
        pytest.param(
            {"credential_resolver": lambda *_args: None},
            "credential_unavailable",
            id="credential-unavailable",
        ),
        pytest.param(
            {"enter_error": RuntimeError("synthetic context failure")},
            "transport_unavailable",
            id="transport-context-before-request",
        ),
    ),
)
def test_known_pre_request_failure_is_failed_without_http_request(
    tmp_path, monkeypatch, runtime_options, expected_code
) -> None:
    engine, _now, requests, runtime, _capabilities = _admitted_runtime(
        tmp_path, monkeypatch, status_code=200, **runtime_options
    )
    try:
        assert runtime.run_claimed_once("ai-review") == "pending"
        assert requests == []
        with engine.connect() as connection:
            task = connection.execute(select(AITaskRow.__table__)).mappings().one()
            unit = connection.execute(select(BillingUnitRow.__table__)).mappings().one()
            step = connection.execute(select(ExecutionStepRow.__table__)).mappings().one()
            external = connection.execute(
                select(ExternalSubmissionRow.__table__)
            ).mappings().one()
            observation = connection.execute(
                select(ResultEvidenceRow.__table__).where(
                    ResultEvidenceRow.evidence_key.like(f"{_OBSERVATION_PREFIX}%")
                )
            ).mappings().one()
        assert task["status"] == "failed"
        assert json.loads(task["result"])["code"] == expected_code
        assert unit["result_status"] == "failed"
        assert unit["debited_amount"] == unit["refunded_amount"] == 0
        assert step["execution_status"] == "failed"
        assert external["submission_status"] == "failed"
        assert '"kind":"known_not_started"' in observation["result_location"]
        assert '"request_started":false' in observation["result_location"]
    finally:
        engine.dispose()


def test_observation_commit_ack_unknown_is_confirmed_from_the_exact_projection(
    tmp_path, monkeypatch
) -> None:
    engine, sessions, now, claim, capabilities, verified, intent = _claimed_review(
        tmp_path, monkeypatch
    )
    from haoai_backend.generation_core.frame_review_recovery import (
        SqlAlchemyFrameReviewRecovery,
        _unknown_observation,
    )

    recovery = SqlAlchemyFrameReviewRecovery(
        session_factory=sessions,
        db_clock=lambda: now[0],
        id_factory=_Ids("observation-ack"),
        crypto=capabilities.crypto,
        proof_authority=capabilities.proof_authority,
    )
    committed = {"raised": False}

    def lose_commit_ack(_session):
        if not committed["raised"]:
            committed["raised"] = True
            raise RuntimeError("synthetic commit acknowledgement loss")

    event.listen(Session, "after_commit", lose_commit_ack)
    try:
        outcome = recovery.record_observation(
            claim,
            intent,
            verified,
            _unknown_observation(request_started=True, http_status=500),
        )
    finally:
        event.remove(Session, "after_commit", lose_commit_ack)

    try:
        assert committed["raised"]
        assert outcome == "pending"
        with sessions() as session:
            task = session.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == claim.scope.task_id)
            ).mappings().one()
            unit = session.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.id == verified.unit_id
                )
            ).mappings().one()
            observations = session.execute(
                select(ResultEvidenceRow.__table__).where(
                    ResultEvidenceRow.evidence_key.like(f"{_OBSERVATION_PREFIX}%")
                )
            ).mappings().all()
        assert task["status"] == "queued"
        assert unit["result_status"] == "unknown"
        assert len(observations) == 1
        assert "provider_outcome_unknown" in observations[0]["result_location"]
    finally:
        engine.dispose()


def test_observation_compare_and_set_conflict_rolls_back_partial_evidence(
    tmp_path, monkeypatch
) -> None:
    engine, sessions, now, claim, capabilities, verified, intent = _claimed_review(
        tmp_path, monkeypatch
    )
    import haoai_backend.generation_core.frame_review_recovery as recovery_module

    recovery = recovery_module.SqlAlchemyFrameReviewRecovery(
        session_factory=sessions,
        db_clock=lambda: now[0],
        id_factory=_Ids("observation-rollback"),
        crypto=capabilities.crypto,
        proof_authority=capabilities.proof_authority,
    )

    def insert_then_conflict(session, _graph, stage):
        session.execute(
            ResultEvidenceRow.__table__.insert().values(
                id=stage.evidence_id,
                step_id=stage.step_id,
                submission_id=stage.submission_id,
                evidence_key=stage.evidence_key,
                source="worker",
                evidence_kind="frame_review_observation_v1",
                payload_digest=stage.payload_digest,
                result_location=stage.payload_text,
                result_digest=None,
                received_at=stage.observed_at.replace(tzinfo=None),
                validity="unknown",
                application_status="pending",
                applied_at=None,
            )
        )
        return False

    monkeypatch.setattr(recovery_module, "_stage_observation_rows", insert_then_conflict)
    try:
        outcome = recovery.record_observation(
            claim,
            intent,
            verified,
            recovery_module._unknown_observation(
                request_started=True, http_status=500
            ),
        )
        with sessions() as session:
            task = session.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == claim.scope.task_id)
            ).mappings().one()
            unit = session.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.id == verified.unit_id
                )
            ).mappings().one()
            observations = session.execute(
                select(ResultEvidenceRow.__table__).where(
                    ResultEvidenceRow.__table__.c.submission_id
                    == intent.external_submission_id
                )
            ).mappings().all()
        assert outcome == "pending"
        assert task["status"] == "processing"
        assert unit["result_status"] == "pending"
        assert observations == []
    finally:
        engine.dispose()


def test_due_recovery_rejects_foreign_scope_and_stops_before_writing(
    tmp_path, monkeypatch
) -> None:
    engine, sessions, now, claim, capabilities, verified, intent = _claimed_review(
        tmp_path, monkeypatch
    )
    from threading import Event

    from haoai_backend.generation_core.frame_review_recovery import (
        SqlAlchemyFrameReviewRecovery,
    )

    recovery = SqlAlchemyFrameReviewRecovery(
        session_factory=sessions,
        db_clock=lambda: now[0],
        id_factory=_Ids("due-stop"),
        crypto=capabilities.crypto,
        proof_authority=capabilities.proof_authority,
    )
    now[0] = intent.deadline_at
    try:
        foreign = OwnerTaskScope(
            user_id="foreign-owner",
            task_id=claim.scope.task_id,
            operation="frame.review",
        )
        assert recovery.consume_due(foreign) == "pending"
        stop = Event()
        stop.set()
        assert recovery.scan_due(stop_signal=stop) == ()
        with sessions() as session:
            task = session.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == claim.scope.task_id)
            ).mappings().one()
            unit = session.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.id == verified.unit_id
                )
            ).mappings().one()
            evidence = session.execute(
                select(ResultEvidenceRow.__table__).where(
                    ResultEvidenceRow.__table__.c.submission_id
                    == intent.external_submission_id
                )
            ).all()
        assert task["status"] == "processing"
        assert unit["result_status"] == "pending"
        assert evidence == []
    finally:
        engine.dispose()


def test_timely_result_and_continuation_work_are_retained_by_due_scan(
    tmp_path, monkeypatch
) -> None:
    from generation_execution.test_frame_review_continuation_transactions import (
        _persist_result as persist_review_result,
        _scenario as review_scenario,
    )
    from haoai_backend.generation_core.frame_review_recovery import (
        SqlAlchemyFrameReviewRecovery,
    )

    scenario = persist_review_result(review_scenario(tmp_path, monkeypatch))
    try:
        scenario["clock"][0] = scenario["intent"].deadline_at + timedelta(seconds=1)
        recovery = SqlAlchemyFrameReviewRecovery(
            session_factory=scenario["sessions"],
            db_clock=lambda: scenario["clock"][0],
            id_factory=_Ids("timely-retain"),
            crypto=scenario["capabilities"].crypto,
            proof_authority=scenario["capabilities"].proof_authority,
        )
        assert recovery.scan_due() == ()
        with scenario["sessions"]() as session:
            unit = session.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.id == scenario["verified"].unit_id
                )
            ).mappings().one()
            receipt = session.execute(
                select(GenerationResultReceiptRow.__table__).where(
                    GenerationResultReceiptRow.__table__.c.unit_id
                    == scenario["verified"].unit_id
                )
            ).mappings().one()
            work = session.execute(
                select(LocalContinuationWorkRow.__table__).where(
                    LocalContinuationWorkRow.__table__.c.unit_id
                    == scenario["verified"].unit_id
                )
            ).mappings().one()
        assert unit["result_status"] == "succeeded"
        assert receipt["result_ref"] == scenario["receipt"].result_ref
        assert work["phase"] == "delivery_pending"
        assert work["result_ref"] == scenario["receipt"].result_ref
    finally:
        scenario["engine"].dispose()
