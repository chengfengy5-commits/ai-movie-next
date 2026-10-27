"""C1110 PostgreSQL-only concurrency checks for a supervisor-owned temp server."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from haoai_backend.authentication.tables import user_credits
from haoai_backend.fused_generation.domain import FusedGenerationUnknown
from haoai_backend.fused_generation.durable_application import (
    submit_durable_fused_generation,
)
from haoai_backend.fused_generation.errors import (
    FusedGenerationConflict,
    FusedGenerationUnavailable,
)
from haoai_backend.fused_generation.execution import FusedGenerationExecution
from haoai_backend.fused_generation.persistence import (
    SqlAlchemyFusedGenerationUnitOfWork,
    fused_generation_uow_factory,
)
from haoai_backend.fused_generation.schemas import FusedAssetGenerateRequest
from haoai_backend.generation_core.base_schema import AITaskRow, CreditLogRow
from haoai_backend.generation_core.schema import FusedGenerationSubmissionRow
from haoai_backend.generation_credentials.application import build_credential_registry
from haoai_backend.shared.identity import TrustedActor

_MARKER = "haoai-fused-durable-submissions-1110"
_REQUIRED_ENV = (
    "HAOAI_OWNED_PG_1110_URL",
    "HAOAI_OWNED_PG_1110_PORT",
    "HAOAI_OWNED_PG_1110_MARKER",
    "HAOAI_OWNED_PG_1110_METADATA",
    "HAOAI_OWNED_PG_1110_REPO",
    "HAOAI_OWNED_PG_1110_RECEIPT",
)


def _write_receipt(
    path: Path | None,
    status: str,
    cases: dict[str, object],
    error: str | None = None,
    *,
    fake_provider_posts: int = 0,
) -> None:
    if path is None:
        return
    payload: dict[str, object] = {
        "status": status,
        "purpose": _MARKER,
        "cases": cases,
        "network": "none",
        "fakeProviderPosts": fake_provider_posts,
        "realDatabase": False,
    }
    if error is not None:
        payload["failureType"] = error
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_owned_metadata() -> tuple[Path, Path, dict[str, Any], Any]:
    receipt_value = os.environ.get("HAOAI_OWNED_PG_1110_RECEIPT")
    receipt_path = Path(receipt_value).resolve() if receipt_value else None
    try:
        values = {name: os.environ.get(name) for name in _REQUIRED_ENV}
        if any(value is None or value == "" for value in values.values()):
            raise RuntimeError("owned PostgreSQL evidence variables are incomplete")
        if values["HAOAI_OWNED_PG_1110_MARKER"] != _MARKER:
            raise RuntimeError("owned PostgreSQL marker did not match")

        repo = Path(str(values["HAOAI_OWNED_PG_1110_REPO"])).resolve(strict=True)
        test_repo = Path(__file__).resolve().parents[2]
        if repo != test_repo:
            raise RuntimeError("owned PostgreSQL test source is outside the frozen repository")
        metadata_path = Path(str(values["HAOAI_OWNED_PG_1110_METADATA"]))
        if not metadata_path.is_absolute():
            raise RuntimeError("owned PostgreSQL metadata path must be absolute")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict):
            raise RuntimeError("owned PostgreSQL metadata is invalid")

        expected = {
            "purpose": _MARKER,
            "host": "127.0.0.1",
            "user": "haoai1110",
            "database": "postgres",
            "ownedPidConfirmed": True,
        }
        for key, value in expected.items():
            if metadata.get(key) != value:
                raise RuntimeError("owned PostgreSQL metadata did not match")
        port = int(str(values["HAOAI_OWNED_PG_1110_PORT"]))
        if type(metadata.get("port")) is not int or metadata["port"] != port or port == 5432:
            raise RuntimeError("owned PostgreSQL port did not match")
        if type(metadata.get("postgresPid")) is not int or metadata["postgresPid"] <= 0:
            raise RuntimeError("owned PostgreSQL PID metadata is invalid")

        tmp_root_value = metadata.get("tmpRoot")
        data_directory_value = metadata.get("dataDirectory")
        if type(tmp_root_value) is not str or type(data_directory_value) is not str:
            raise RuntimeError("owned PostgreSQL directory metadata is invalid")
        tmp_root = Path(tmp_root_value).resolve(strict=True)
        data_directory = Path(data_directory_value).resolve(strict=True)
        if not tmp_root.is_absolute() or not data_directory.is_relative_to(tmp_root):
            raise RuntimeError("owned PostgreSQL data directory is outside its temporary root")

        url = make_url(str(values["HAOAI_OWNED_PG_1110_URL"]))
        if (
            not url.drivername.startswith("postgresql")
            or url.host != "127.0.0.1"
            or url.port != port
            or url.username != "haoai1110"
            or url.database != "postgres"
            or url.password is not None
            or url.query
        ):
            raise RuntimeError("owned PostgreSQL URL is not authorized")
        return repo, tmp_root, metadata, (str(values["HAOAI_OWNED_PG_1110_URL"]), receipt_path)
    except Exception as error:
        _write_receipt(receipt_path, "FAIL", {"ownedInstanceValidation": False}, type(error).__name__)
        raise


@pytest.fixture(scope="module")
def owned_postgres() -> tuple[Engine, Path, Path, dict[str, Any], Path]:
    if not any(os.environ.get(name) for name in _REQUIRED_ENV):
        pytest.skip("requires the supervisor-owned HAOAI_OWNED_PG_1110_* environment")
    repo, tmp_root, metadata, (database_url, receipt_path) = _load_owned_metadata()
    schema = f"c1110_{uuid4().hex}"
    engine = create_engine(
        database_url,
        poolclass=NullPool,
        connect_args={
            "connect_timeout": 3,
            # Startup options survive the migration runner's rollback during
            # preflight; a transactional SET search_path would not.
            "options": f"-c search_path={schema} -c lock_timeout=5000",
        },
    )

    try:
        with engine.begin() as connection:
            data_directory = Path(connection.execute(text("SHOW data_directory")).scalar_one()).resolve()
            server_port = int(connection.execute(text("SHOW port")).scalar_one())
            if data_directory != Path(metadata["dataDirectory"]).resolve() or server_port != metadata["port"]:
                raise RuntimeError("connected PostgreSQL instance did not match owned metadata")
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        yield engine, repo, tmp_root, metadata, receipt_path
    except Exception as error:
        _write_receipt(receipt_path, "FAIL", {"ownedInstanceConnection": False}, type(error).__name__)
        raise
    finally:
        try:
            with engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        finally:
            engine.dispose()


def _test_helpers(repo: Path):
    path = repo / "backend" / "tests" / "test_server_media_storage.py"
    spec = importlib.util.spec_from_file_location("_c1110_pg_host_test_helpers", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("synthetic PostgreSQL setup helpers are unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build_context(engine: Engine, repo: Path, tmp_path: Path):
    from backend.server.credentials import load_registry_capabilities
    from backend.server.generation import _database_clock, _uuid_id

    helpers = _test_helpers(repo)
    configuration = helpers._fused_server_configuration(tmp_path)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    capabilities = load_registry_capabilities(
        configuration.fused_generation_credentials_file,
        legacy_secret_key=configuration.secret_key.value,
        session_factory=session_factory,
        db_clock=_database_clock(session_factory),
        id_factory=_uuid_id,
    )
    registry = build_credential_registry(capabilities)
    fused_configuration = SimpleNamespace(
        credential_registry=registry,
        proof_authority=capabilities.proof_authority,
    )
    helpers._seed_fused_server_database(
        engine,
        provider_origin="https://api.openai.com",
        fused_configuration=fused_configuration,
    )
    return SimpleNamespace(
        helpers=helpers,
        configuration=configuration,
        session_factory=session_factory,
        registry=registry,
        crypto=capabilities.crypto,
        proof_authority=capabilities.proof_authority,
        uow_factory=fused_generation_uow_factory(
            session_factory,
            decrypt_api_key=helpers.LegacyFernet(helpers._SECRET).decrypt,
        ),
    )


def _request(context: Any, *, prompt: str = "合成PG门禁提示") -> FusedAssetGenerateRequest:
    body = context.helpers._fused_request_body()
    body["prompt_user"] = prompt
    return FusedAssetGenerateRequest.model_validate(body)


def test_fused_generation_owned_postgres_atomicity_dispatch_and_ack_unknown(
    owned_postgres: tuple[Engine, Path, Path, dict[str, Any], Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, repo, _tmp_root, metadata, receipt_path = owned_postgres
    cases: dict[str, object] = {
        "sameKeyOneDebit": False,
        "sameKeyOneTask": False,
        "sameKeyIntermediateReceipt": False,
        "reservationBothInnerLookupsMissed": False,
        "reservationUniqueInsertRaceOneDebit": False,
        "reservationUniqueInsertRaceOneTask": False,
        "differentBodyConflict": False,
        "reservationRollbackAtomic": False,
        "claimDispatchLockOrder": False,
        "generationFence": False,
        "dispatchAckUnknownZeroPost": False,
    }
    fake_provider_posts = {"count": 0}
    try:
        context = _build_context(engine, repo, tmp_path)
        actor = TrustedActor("user-server-test")
        key = "fused-postgres-same-key-1110"

        def submit_once():
            return submit_durable_fused_generation(
                context.uow_factory,
                registry=context.registry,
                crypto=context.crypto,
                proof_authority=context.proof_authority,
                actor=actor,
                request=_request(context),
                idempotency_key=key,
            )

        reservation_committed = threading.Event()
        resume_first_submission = threading.Event()

        def paused_reservation_factory():
            uow = context.uow_factory()
            reserve = uow.reserve_durable_submission
            commit = uow.commit
            reservation = {"written": False}

            def reserve_then_mark(**kwargs):
                reserve(**kwargs)
                reservation["written"] = True

            def commit_then_pause():
                commit()
                if reservation["written"]:
                    reservation["written"] = False
                    reservation_committed.set()
                    if not resume_first_submission.wait(timeout=10):
                        raise TimeoutError("same-key reservation gate was not released")

            uow.reserve_durable_submission = reserve_then_mark
            uow.commit = commit_then_pause
            return uow

        with ThreadPoolExecutor(max_workers=1) as executor:
            first = executor.submit(
                lambda: submit_durable_fused_generation(
                    paused_reservation_factory,
                    registry=context.registry,
                    crypto=context.crypto,
                    proof_authority=context.proof_authority,
                    actor=actor,
                    request=_request(context),
                    idempotency_key=key,
                )
            )
            assert reservation_committed.wait(timeout=10)
            try:
                intermediate_response = submit_once()
                assert intermediate_response.status_code == 202
                assert intermediate_response.body.get("status") == "charged_incomplete"
                cases["sameKeyIntermediateReceipt"] = True
            finally:
                resume_first_submission.set()
            first_response = first.result(timeout=15)
        assert first_response.status_code == 200
        task_id = str(first_response.body["task_id"])
        replay_response = submit_once()
        assert replay_response.status_code == 200
        assert replay_response.body.get("task_id") == task_id
        assert replay_response.body.get("message_id") == first_response.body.get("message_id")
        with engine.connect() as connection:
            submission_rows = connection.execute(
                select(FusedGenerationSubmissionRow.__table__)
            ).mappings().all()
            task_rows = connection.execute(
                select(AITaskRow.__table__.c.id).where(AITaskRow.__table__.c.type == "fused")
            ).all()
            credit_logs = connection.execute(
                select(CreditLogRow.__table__.c.type, CreditLogRow.__table__.c.amount)
            ).all()
            balance = connection.execute(
                select(user_credits.c.credits).where(user_credits.c.user_id == actor.user_id)
            ).scalar_one()
        assert len(submission_rows) == 1
        assert len(task_rows) == 1 and task_rows[0].id == task_id
        assert len(credit_logs) == 1 and (credit_logs[0].type, credit_logs[0].amount) == ("usage", -4)
        assert balance == 16
        cases["sameKeyOneDebit"] = True
        cases["sameKeyOneTask"] = True

        with pytest.raises(FusedGenerationConflict):
            submit_durable_fused_generation(
                context.uow_factory,
                registry=context.registry,
                crypto=context.crypto,
                proof_authority=context.proof_authority,
                actor=actor,
                request=_request(context, prompt="同键异内容"),
                idempotency_key=key,
            )
        cases["differentBodyConflict"] = True

        # Give the race enough synthetic balance that a duplicate debit cannot
        # be hidden by the insufficient-balance guard. Both transactions first
        # miss the key outside their fee transactions and then meet after the
        # second in-transaction miss, before credential capture acquires its
        # config-row FOR UPDATE lock.
        with engine.begin() as connection:
            balance_before_race = connection.execute(
                select(user_credits.c.credits).where(
                    user_credits.c.user_id == actor.user_id
                )
            ).scalar_one()
            connection.execute(
                update(user_credits)
                .where(user_credits.c.user_id == actor.user_id)
                .values(credits=balance_before_race + 16)
            )
            base_submission_count = len(
                connection.execute(
                    select(FusedGenerationSubmissionRow.__table__.c.submission_id)
                ).all()
            )
            base_task_count = len(
                connection.execute(
                    select(AITaskRow.__table__.c.id).where(
                        AITaskRow.__table__.c.type == "fused"
                    )
                ).all()
            )
            base_log_count = len(
                connection.execute(select(CreditLogRow.__table__.c.id)).all()
            )
        balance_before_race += 16

        race_key = "fused-postgres-insert-race-1110"
        inner_lookup_barrier = threading.Barrier(2)
        lookup_counts = threading.local()
        original_find_submission = SqlAlchemyFusedGenerationUnitOfWork.find_durable_submission

        def synchronize_after_inner_miss(uow, owner_id, idempotency_key):
            result = original_find_submission(uow, owner_id, idempotency_key)
            if idempotency_key == race_key and result is None:
                count = getattr(lookup_counts, "count", 0) + 1
                lookup_counts.count = count
                if count == 2:
                    inner_lookup_barrier.wait(timeout=10)
            return result

        monkeypatch.setattr(
            SqlAlchemyFusedGenerationUnitOfWork,
            "find_durable_submission",
            synchronize_after_inner_miss,
        )

        def submit_race_once():
            return submit_durable_fused_generation(
                context.uow_factory,
                registry=context.registry,
                crypto=context.crypto,
                proof_authority=context.proof_authority,
                actor=actor,
                request=_request(context, prompt="同键首次预约竞争"),
                idempotency_key=race_key,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            race_futures = [executor.submit(submit_race_once) for _ in range(2)]
            race_responses = [future.result(timeout=30) for future in race_futures]
        monkeypatch.setattr(
            SqlAlchemyFusedGenerationUnitOfWork,
            "find_durable_submission",
            original_find_submission,
        )
        assert all(response.status_code in {200, 202} for response in race_responses)
        assert any(response.status_code == 200 for response in race_responses)
        cases["reservationBothInnerLookupsMissed"] = True

        raced_replay = submit_race_once()
        assert raced_replay.status_code == 200
        race_task_id = str(raced_replay.body["task_id"])
        with engine.connect() as connection:
            race_rows = connection.execute(
                select(FusedGenerationSubmissionRow.__table__).where(
                    FusedGenerationSubmissionRow.idempotency_key == race_key
                )
            ).mappings().all()
            task_rows_after_race = connection.execute(
                select(AITaskRow.__table__.c.id).where(
                    AITaskRow.__table__.c.type == "fused"
                )
            ).all()
            credit_logs_after_race = connection.execute(
                select(CreditLogRow.__table__.c.type, CreditLogRow.__table__.c.amount)
            ).all()
            balance_after_race = connection.execute(
                select(user_credits.c.credits).where(
                    user_credits.c.user_id == actor.user_id
                )
            ).scalar_one()
        assert len(race_rows) == 1
        assert race_rows[0]["task_id"] == race_task_id
        assert len(task_rows_after_race) == base_task_count + 1
        assert race_task_id in {row.id for row in task_rows_after_race}
        assert len(credit_logs_after_race) == base_log_count + 1
        assert all((row.type, row.amount) == ("usage", -4) for row in credit_logs_after_race)
        assert balance_after_race == balance_before_race - 4
        cases["reservationUniqueInsertRaceOneDebit"] = True
        cases["reservationUniqueInsertRaceOneTask"] = True

        failure_key = "fused-postgres-rollback-1110"
        base_factory = context.uow_factory

        def failing_reservation_factory():
            uow = base_factory()
            reserve = uow.reserve_durable_submission

            def reserve_then_abort(**kwargs):
                reserve(**kwargs)
                if kwargs.get("idempotency_key") == failure_key:
                    raise RuntimeError("synthetic precommit failure")

            uow.reserve_durable_submission = reserve_then_abort
            return uow

        with pytest.raises(FusedGenerationUnavailable):
            submit_durable_fused_generation(
                failing_reservation_factory,
                registry=context.registry,
                crypto=context.crypto,
                proof_authority=context.proof_authority,
                actor=actor,
                request=_request(context, prompt="预提交回滚用例"),
                idempotency_key=failure_key,
            )
        with engine.connect() as connection:
            submission_count = connection.execute(
                select(FusedGenerationSubmissionRow.__table__.c.submission_id)
            ).all()
            log_count = connection.execute(select(CreditLogRow.__table__.c.id)).all()
            balance = connection.execute(
                select(user_credits.c.credits).where(user_credits.c.user_id == actor.user_id)
            ).scalar_one()
        assert len(submission_count) == base_submission_count + 1
        assert len(log_count) == base_log_count + 1
        assert balance == balance_before_race - 4
        cases["reservationRollbackAtomic"] = True

        row = submission_rows[0]
        submission_id = str(row["submission_id"])
        old_token = "c1110-dispatch-old-owner-token"
        claim_uow = context.uow_factory()
        initial_claim = claim_uow.claim_durable_task(
            task_id,
            submission_id,
            old_token,
            expected_user_id=actor.user_id,
        )
        assert initial_claim is not None and initial_claim.generation is not None
        claim_uow.commit()
        old_generation = initial_claim.generation
        with engine.begin() as connection:
            connection.execute(
                update(AITaskRow.__table__)
                .where(AITaskRow.id == task_id)
                .values(
                    lease_until=datetime.now(timezone.utc).replace(tzinfo=None)
                    - timedelta(minutes=1)
                )
            )

        ledger_lock_acquired = threading.Event()
        release_claim = threading.Event()
        dispatch_started = threading.Event()
        new_token = "c1110-dispatch-new-owner-token"
        dispatch_application_name = f"c1110_dispatch_{uuid4().hex[:12]}"

        def recovering_claim():
            uow = context.uow_factory()
            original_execute = uow._session.execute
            paused = False

            def pause_after_ledger_lock(statement, *args, **kwargs):
                nonlocal paused
                result = original_execute(statement, *args, **kwargs)
                sql = str(statement).lower()
                if not paused and "fused_generation_submissions" in sql and "for update" in sql:
                    paused = True
                    ledger_lock_acquired.set()
                    if not release_claim.wait(timeout=5):
                        raise TimeoutError("claim lock release timed out")
                return result

            uow._session.execute = pause_after_ledger_lock
            try:
                claim = uow.claim_durable_task(
                    task_id,
                    submission_id,
                    new_token,
                    expected_user_id=actor.user_id,
                )
                if claim is None:
                    uow.rollback()
                else:
                    uow.commit()
                return claim
            finally:
                uow.close()

        def old_dispatch_attempt():
            uow = context.uow_factory()
            try:
                uow._session.connection().exec_driver_sql(
                    "SELECT set_config('application_name', %s, false)",
                    (dispatch_application_name,),
                )
                dispatch_started.set()
                inserted = uow.persist_dispatch_gate(
                    task_id=task_id,
                    submission_id=submission_id,
                    user_id=actor.user_id,
                    claim_token=old_token,
                    generation=old_generation,
                    dispatch_nonce="f20c993f-941f-4b90-a6df-b7a4732c1110",
                    wire_digest="a" * 64,
                )
                if inserted:
                    uow.commit()
                else:
                    uow.rollback()
                return inserted
            finally:
                uow.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            claim_future = executor.submit(recovering_claim)
            assert ledger_lock_acquired.wait(timeout=5)
            dispatch_future = executor.submit(old_dispatch_attempt)
            wait_evidence = None
            deadline = time.monotonic() + 5
            try:
                assert dispatch_started.wait(timeout=5)
                while time.monotonic() < deadline:
                    with engine.connect() as observer:
                        wait_evidence = observer.execute(
                            text(
                                "SELECT pid, wait_event_type, wait_event, query, "
                                "pg_blocking_pids(pid) AS blocking_pids "
                                "FROM pg_stat_activity WHERE application_name = :name"
                            ),
                            {"name": dispatch_application_name},
                        ).mappings().one_or_none()
                    if wait_evidence is not None and wait_evidence["wait_event_type"] == "Lock":
                        break
                    time.sleep(0.02)
                assert wait_evidence is not None
                assert wait_evidence["wait_event_type"] == "Lock"
                assert wait_evidence["blocking_pids"]
                assert "fused_generation_submissions" in str(wait_evidence["query"]).lower()
                cases["claimDispatchLockWaitBackendPid"] = int(wait_evidence["pid"])
                cases["claimDispatchLockWaitEvent"] = str(wait_evidence["wait_event"])
                cases["claimDispatchBlockingBackendPids"] = [
                    int(pid) for pid in wait_evidence["blocking_pids"]
                ]
            finally:
                release_claim.set()
            recovered_claim = claim_future.result(timeout=8)
            stale_dispatch_inserted = dispatch_future.result(timeout=8)
        assert recovered_claim is not None and recovered_claim.generation == old_generation + 1
        assert stale_dispatch_inserted is False
        cases["claimDispatchLockOrder"] = True

        correct_nonce = "a1e34967-2854-4636-b639-7780fcea1110"
        dispatch_uow = context.uow_factory()
        assert dispatch_uow.persist_dispatch_gate(
            task_id=task_id,
            submission_id=submission_id,
            user_id=actor.user_id,
            claim_token=new_token,
            generation=recovered_claim.generation,
            dispatch_nonce=correct_nonce,
            wire_digest="b" * 64,
        )
        dispatch_uow.commit()
        stale_uow = context.uow_factory()
        assert stale_uow.persist_dispatch_gate(
            task_id=task_id,
            submission_id=submission_id,
            user_id=actor.user_id,
            claim_token=old_token,
            generation=old_generation,
            dispatch_nonce="d5045147-f6bf-4222-87eb-e848b0b51110",
            wire_digest="c" * 64,
        ) is False
        stale_uow.rollback()
        dispatch_uow = context.uow_factory()
        assert dispatch_uow.dispatch_gate_matches(
            task_id=task_id,
            submission_id=submission_id,
            user_id=actor.user_id,
            claim_token=new_token,
            generation=recovered_claim.generation,
            dispatch_nonce=correct_nonce,
            wire_digest="b" * 64,
        ) is True
        dispatch_uow.rollback()
        cases["generationFence"] = True

        ack_request = submit_durable_fused_generation(
            context.uow_factory,
            registry=context.registry,
            crypto=context.crypto,
            proof_authority=context.proof_authority,
            actor=actor,
            request=_request(context, prompt="派发ACK未知用例"),
            idempotency_key="fused-postgres-dispatch-ack-1110",
        )
        ack_row = next(
            item for item in _submission_rows(engine)
            if item["idempotency_key"] == "fused-postgres-dispatch-ack-1110"
        )

        class CountingRuntime:
            def __init__(self) -> None:
                self.provider_posts = 0

            async def generate(self, request):
                assert request.dispatch_gate is not None
                if await request.dispatch_gate("e" * 64) is True:
                    self.provider_posts += 1
                    fake_provider_posts["count"] += 1
                return FusedGenerationUnknown()

        runtime = CountingRuntime()
        ack_state = {"gate_written": False}

        def ack_unknown_factory():
            uow = context.uow_factory()
            persist = uow.persist_dispatch_gate
            commit = uow.commit

            def persist_and_mark(**kwargs):
                inserted = persist(**kwargs)
                if inserted:
                    ack_state["gate_written"] = True
                return inserted

            def commit_then_lose_ack():
                commit()
                if ack_state["gate_written"]:
                    ack_state["gate_written"] = False
                    raise RuntimeError("synthetic dispatch commit ACK loss")

            uow.persist_dispatch_gate = persist_and_mark
            uow.commit = commit_then_lose_ack
            return uow

        execution = FusedGenerationExecution(
            runtime=runtime,
            uow_factory=ack_unknown_factory,
            credential_registry=context.registry,
            credential_crypto=context.crypto,
            proof_authority=context.proof_authority,
        )
        asyncio.run(execution.execute_submission(str(ack_row["submission_id"]), actor.user_id))
        assert ack_request.status_code == 200
        assert runtime.provider_posts == 0
        with engine.connect() as connection:
            ack_persisted = connection.execute(
                select(FusedGenerationSubmissionRow.__table__.c.stage).where(
                    FusedGenerationSubmissionRow.submission_id == ack_row["submission_id"]
                )
            ).scalar_one()
            ack_task = connection.execute(
                select(AITaskRow.__table__.c.recovery_status).where(
                    AITaskRow.id == ack_row["task_id"]
                )
            ).scalar_one()
        assert ack_persisted == "provider_result_unknown"
        assert ack_task == "provider_result_unknown"
        cases["dispatchAckUnknownZeroPost"] = True

        _write_receipt(
            receipt_path,
            "PASS",
            {
                **cases,
                "ownedInstanceValidated": True,
                "syntheticOnly": True,
                "usedProviderNetwork": False,
                "usedObjectStorage": False,
                "sourceRepository": str(repo),
                "metadataPort": metadata["port"],
            },
            fake_provider_posts=fake_provider_posts["count"],
        )
    except BaseException as error:
        _write_receipt(
            receipt_path,
            "FAIL",
            {
                **cases,
                "ownedInstanceValidated": True,
                "syntheticOnly": True,
            },
            type(error).__name__,
            fake_provider_posts=fake_provider_posts["count"],
        )
        raise


def _submission_rows(engine: Engine) -> list[dict[str, object]]:
    with engine.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                select(FusedGenerationSubmissionRow.__table__)
            ).mappings().all()
        ]
