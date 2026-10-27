"""W1123 PostgreSQL-only concurrency checks for owned fused media recovery.

The test connects only to the supervisor-owned loopback PostgreSQL instance
described by HAOAI_OWNED_PG_1123_* variables. Provider and object storage are
in-process fakes; no external services are contacted.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
import threading
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

from haoai_backend.fused_generation.durable_application import (
    load_durable_prepared_snapshot,
    lookup_durable_submission,
    submit_durable_fused_generation,
)
from haoai_backend.fused_generation.durable_values import (
    encode_result,
    seal_json_value,
    seal_owned_media_material,
)
from haoai_backend.fused_generation.domain import FusedGenerationOutput
from haoai_backend.fused_generation.execution import (
    FusedGenerationExecution,
    _claim_fused_media_verification,
    _complete_verified_fused_media,
    _record_fused_media_verification,
)
from haoai_backend.fused_generation.persistence import fused_generation_uow_factory
from haoai_backend.fused_generation.recovery_worker import (
    _read_recoverable_fused_media_writes,
)
from haoai_backend.fused_generation.schemas import FusedAssetGenerateRequest
from haoai_backend.generation_contracts.types import StoredMedia
from haoai_backend.generation_core.base_schema import AITaskRow
from haoai_backend.generation_core.schema import FusedMediaWriteRow
from haoai_backend.media_storage.owned_write_values import (
    OwnedObjectMaterial,
    OwnedObjectPlan,
    OwnedObjectPutResult,
    OwnedObjectVerification,
)
from haoai_backend.shared.identity import TrustedActor


_MARKER = "haoai-fused-media-recovery-1123"
_ENV_PREFIX = "HAOAI_OWNED_PG_1123_"
_REQUIRED_ENV = tuple(
    _ENV_PREFIX + suffix
    for suffix in ("URL", "PORT", "MARKER", "METADATA", "REPO", "RECEIPT")
)


def _write_receipt(
    path: Path | None,
    status: str,
    cases: dict[str, object],
    error: str | None = None,
    *,
    actual_owned_postgres: bool = False,
) -> None:
    if path is None:
        return
    payload: dict[str, object] = {
        "status": status,
        "purpose": _MARKER,
        "cases": cases,
        "network": "none",
        "fakeProviderPosts": 0,
        "productionDatabase": False,
        "actualOwnedPostgreSQL": actual_owned_postgres,
    }
    if error is not None:
        payload["failureType"] = error
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_owned_instance() -> tuple[Path, Path, dict[str, Any], str, Path]:
    values = {name: os.environ.get(name) for name in _REQUIRED_ENV}
    receipt_value = values[_ENV_PREFIX + "RECEIPT"]
    receipt = Path(receipt_value).resolve() if receipt_value else None
    try:
        if any(value is None or value == "" for value in values.values()):
            raise RuntimeError("owned PostgreSQL evidence variables are incomplete")
        if values[_ENV_PREFIX + "MARKER"] != _MARKER:
            raise RuntimeError("owned PostgreSQL marker did not match")

        repo = Path(str(values[_ENV_PREFIX + "REPO"])).resolve(strict=True)
        expected_repo = Path(__file__).resolve().parents[2]
        if repo != expected_repo:
            raise RuntimeError("owned PostgreSQL test source is outside the frozen repository")
        metadata_path = Path(str(values[_ENV_PREFIX + "METADATA"]))
        if not metadata_path.is_absolute():
            raise RuntimeError("owned PostgreSQL metadata path must be absolute")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict):
            raise RuntimeError("owned PostgreSQL metadata is invalid")

        expected = {
            "purpose": _MARKER,
            "host": "127.0.0.1",
            "user": "haoai1123",
            "database": "postgres",
            "ownedPidConfirmed": True,
        }
        for key, value in expected.items():
            if metadata.get(key) != value:
                raise RuntimeError("owned PostgreSQL metadata did not match")
        port = int(str(values[_ENV_PREFIX + "PORT"]))
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
        if not data_directory.is_relative_to(tmp_root):
            raise RuntimeError("owned PostgreSQL data directory is outside its temporary root")

        database_url = str(values[_ENV_PREFIX + "URL"])
        url = make_url(database_url)
        if (
            not url.drivername.startswith("postgresql")
            or url.host != "127.0.0.1"
            or url.port != port
            or url.username != "haoai1123"
            or url.database != "postgres"
            or url.password is not None
            or url.query
        ):
            raise RuntimeError("owned PostgreSQL URL is not authorized")
        if receipt is None:
            raise RuntimeError("owned PostgreSQL receipt path is missing")
        return repo, tmp_root, metadata, database_url, receipt
    except Exception as error:
        _write_receipt(receipt, "FAIL", {"ownedInstanceValidation": False}, type(error).__name__)
        raise


@pytest.fixture(scope="module")
def owned_postgres_1123() -> tuple[Engine, Path, Path, dict[str, Any], Path]:
    if not any(os.environ.get(name) for name in _REQUIRED_ENV):
        pytest.skip("requires supervisor-owned HAOAI_OWNED_PG_1123_* environment")
    repo, tmp_root, metadata, database_url, receipt = _load_owned_instance()
    schema = f"w1123_{uuid4().hex}"
    engine = create_engine(
        database_url,
        poolclass=NullPool,
        connect_args={
            "connect_timeout": 3,
            "options": f"-c search_path={schema} -c lock_timeout=5000",
        },
    )
    try:
        with engine.begin() as connection:
            data_directory = Path(connection.execute(text("SHOW data_directory")).scalar_one()).resolve()
            server_port = int(connection.execute(text("SHOW port")).scalar_one())
            backend_pid = int(connection.execute(text("SELECT pg_backend_pid()")).scalar_one())
            if data_directory != Path(metadata["dataDirectory"]).resolve() or server_port != metadata["port"]:
                raise RuntimeError("connected PostgreSQL instance did not match owned metadata")
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        metadata["probeBackendPid"] = backend_pid
        yield engine, repo, tmp_root, metadata, receipt
    except Exception as error:
        _write_receipt(receipt, "FAIL", {"ownedInstanceConnection": False}, type(error).__name__)
        raise
    finally:
        try:
            with engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        finally:
            engine.dispose()


def _load_host_helpers(repo: Path):
    path = repo / "backend" / "tests" / "test_server_media_storage.py"
    spec = importlib.util.spec_from_file_location("_w1123_owned_media_host_helpers", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("synthetic fused server helpers are unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build_context(engine: Engine, repo: Path, tmp_path: Path):
    from backend.server.credentials import load_registry_capabilities
    from backend.server.generation import _database_clock, _uuid_id
    from haoai_backend.generation_credentials.application import build_credential_registry

    helpers = _load_host_helpers(repo)
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
        registry=registry,
        crypto=capabilities.crypto,
        proof_authority=capabilities.proof_authority,
        uow_factory=fused_generation_uow_factory(
            session_factory,
            decrypt_api_key=helpers.LegacyFernet(helpers._SECRET).decrypt,
        ),
    )


def _plan(
    *,
    prepared: Any,
    result_hash: str,
    material: bytes,
    key_suffix: str,
    bucket: str = "w1123-bucket",
) -> OwnedObjectMaterial:
    return OwnedObjectMaterial(
        plan=OwnedObjectPlan(
            write_id=uuid4().hex,
            owner_ref=prepared.user_id,
            submission_ref=prepared.submission_id,
            slot="primary",
            provider_result_digest=result_hash,
            material_sha256=hashlib.sha256(material).hexdigest(),
            media_type="image/png",
            size_bytes=len(material),
            config_fingerprint=hashlib.sha256(b"trusted synthetic R2 config").hexdigest(),
            config_version=1,
            bucket=bucket,
            object_key=f"fused/{prepared.submission_id}/{key_suffix}.png",
            public_base="https://media.example.test",
        ),
        content=material,
    )


def _persist_plan(
    uow_factory,
    prepared: Any,
    claim_token: str,
    generation: int,
    output: FusedGenerationOutput,
    material: OwnedObjectMaterial,
    crypto: Any,
    before_lock_barrier: threading.Barrier,
) -> tuple[bool, list[str], int]:
    assert prepared.submission_id is not None
    assert prepared.request_digest is not None
    assert prepared.credential_binding is not None
    encrypted, nonce, key_id, algorithm = seal_owned_media_material(
        material,
        crypto=crypto,
        submission_id=prepared.submission_id,
        owner_id=prepared.user_id,
        request_digest=prepared.request_digest,
        task_id=prepared.task_id,
        binding=prepared.credential_binding,
    )
    uow = uow_factory()
    locks: list[str] = []
    original_execute = uow._session.execute

    def execute(statement, *args, **kwargs):
        sql = str(statement).lower()
        if "for update" in sql:
            if "fused_generation_submissions" in sql:
                locks.append("ledger")
            elif "ai_tasks" in sql:
                locks.append("task")
            elif "fused_media_writes" in sql:
                locks.append("journal")
        return original_execute(statement, *args, **kwargs)

    uow._session.execute = execute
    try:
        backend_pid = int(uow._session.execute(text("SELECT pg_backend_pid()")).scalar_one())
        before_lock_barrier.wait(timeout=10)
        saved = uow.persist_fused_media_plan(
            submission_id=prepared.submission_id,
            task_id=prepared.task_id,
            owner_id=prepared.user_id,
            claim_token=claim_token,
            generation=generation,
            result_digest=material.plan.provider_result_digest,
            result_kind="url",
            material_sha256=material.plan.material_sha256,
            media_type=material.plan.media_type,
            size_bytes=material.plan.size_bytes,
            config_fingerprint=material.plan.config_fingerprint,
            config_version=material.plan.config_version,
            bucket=material.plan.bucket,
            object_key=material.plan.object_key,
            public_base=material.plan.public_base,
            write_id=material.plan.write_id,
            plan_version=material.plan.plan_version,
            encrypted_material=encrypted,
            material_nonce=nonce,
            material_key_id=key_id,
            material_algorithm=algorithm,
        )
        if saved:
            uow.commit()
        else:
            uow.rollback()
        return saved, locks, backend_pid
    finally:
        uow.close()


class _SyntheticPlannedStorage:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.present = False
        self.put_count = 0
        self.get_count = 0
        self.requested_keys: list[str] = []

    def prepare_public_image(self, image_url: str, *, owner_id: str, submission_id: str, result_digest: str):
        raise AssertionError("journal recovery must not fetch the expired provider URL")

    def prepare_inline_image(self, image, *, owner_id: str, submission_id: str, result_digest: str):
        raise AssertionError("journal recovery must open sealed bytes rather than reprepare")

    def put_planned(self, material: OwnedObjectMaterial, permit) -> OwnedObjectPutResult:
        self.put_count += 1
        if not permit.claim(material.plan):
            return OwnedObjectPutResult("unknown", error_code="permit_denied")
        self.content = material.content
        self.present = True
        return OwnedObjectPutResult("created")

    def verify_planned(self, plan: OwnedObjectPlan) -> OwnedObjectVerification:
        self.get_count += 1
        self.requested_keys.append(plan.object_key)
        if not self.present:
            return OwnedObjectVerification("not_found_pending")
        digest = hashlib.sha256(self.content).hexdigest()
        locator = f"{plan.public_base}/{plan.object_key}"
        stored = StoredMedia(locator, digest, plan.media_type, len(self.content))
        return OwnedObjectVerification(
            "verified",
            stored_media=stored,
            observed_sha256=digest,
            observed_size_bytes=len(self.content),
            request_id=f"synthetic-get-{self.get_count}",
        )


def _read_row(uow_factory, submission_id: str) -> dict[str, object]:
    uow = uow_factory()
    try:
        row = uow.fused_media_write_by_submission(submission_id)
        assert row is not None
        return dict(row)
    finally:
        try:
            uow.rollback()
        finally:
            uow.close()


def _commit_unknown_after_operation(uow_factory, method_name: str):
    state = {"dropped": False}

    def make_uow():
        uow = uow_factory()
        original_method = getattr(uow, method_name)
        original_commit = uow.commit
        armed = {"value": False}

        def invoke(*args, **kwargs):
            result = original_method(*args, **kwargs)
            armed["value"] = bool(result)
            return result

        def commit():
            original_commit()
            if armed["value"] and not state["dropped"]:
                state["dropped"] = True
                raise RuntimeError("synthetic commit acknowledgement lost")

        setattr(uow, method_name, invoke)
        uow.commit = commit
        return uow

    return make_uow, state


def _expire_recovery_leases(engine: Engine, task_id: str, submission_id: str) -> None:
    past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
    with engine.begin() as connection:
        connection.execute(
            update(AITaskRow.__table__)
            .where(AITaskRow.id == task_id)
            .values(lease_until=past)
        )
        connection.execute(
            update(FusedMediaWriteRow.__table__)
            .where(FusedMediaWriteRow.submission_id == submission_id)
            .values(next_verify_at=past, verify_lease_until=None)
        )


def test_fused_owned_media_postgres_journal_races_ack_unknown_and_recovery(
    owned_postgres_1123: tuple[Engine, Path, Path, dict[str, Any], Path],
    tmp_path: Path,
) -> None:
    from sqlalchemy import func
    from haoai_backend.chat_data.tables import chat_messages
    from haoai_backend.series_data.tables import chapters
    from haoai_backend.series_exchange.tables import fused_assets

    engine, repo, _tmp_root, metadata, receipt_path = owned_postgres_1123
    cases: dict[str, object] = {
        "ownedPostgresInstanceVerified": False,
        "planRaceOnePrimary": False,
        "ledgerTaskJournalLockOrder": False,
        "staleGenerationCannotClaimPut": False,
        "putStartedCommitAckUnknownZeroPut": False,
        "scannerSelectsCommittedOriginalKey": False,
        "recoveryGetOnlyAndNoProviderPosts": False,
        "verifierLeaseRaceAndTakeoverFence": False,
        "verifiedApplyCommitAckUnknown": False,
        "oneAssetOneMessageOneTask": False,
        "singleAdmissionFee": False,
    }
    try:
        context = _build_context(engine, repo, tmp_path)
        actor = TrustedActor("user-server-test")
        chapter_id = "chapter-fused-server"
        series_id = "series-fused-server"
        frame_identity = {"id": "frame-w1123-target", "text": "合成目标分镜"}
        with engine.begin() as connection:
            connection.execute(
                update(chapters)
                .where(chapters.c.id == chapter_id)
                .values(content=json.dumps([frame_identity], ensure_ascii=False))
            )
            baseline_assets = connection.execute(
                select(func.count())
                .select_from(fused_assets)
                .where(fused_assets.c.series_id == series_id)
            ).scalar_one()
            baseline_messages = connection.execute(
                select(func.count())
                .select_from(chat_messages)
                .where(chat_messages.c.chapter_id == chapter_id)
            ).scalar_one()
            baseline_tasks = connection.execute(
                select(func.count())
                .select_from(AITaskRow.__table__)
                .where(
                    AITaskRow.__table__.c.user_id == actor.user_id,
                    AITaskRow.__table__.c.type == "fused",
                )
            ).scalar_one()
        idempotency_key = f"fused-owned-media-1123-{uuid4().hex}"
        request_body = context.helpers._fused_request_body()
        request_body.update({"chapter_id": chapter_id, "frame_index": 0})
        response = submit_durable_fused_generation(
            context.uow_factory,
            registry=context.registry,
            crypto=context.crypto,
            proof_authority=context.proof_authority,
            actor=actor,
            request=FusedAssetGenerateRequest.model_validate(request_body),
            idempotency_key=idempotency_key,
        )
        assert response.status_code == 200
        ledger = lookup_durable_submission(
            context.uow_factory,
            owner_id=actor.user_id,
            idempotency_key=idempotency_key,
        )
        assert ledger is not None
        submission_id = str(ledger["submission_id"])
        task_id = str(ledger["task_id"])
        output = FusedGenerationOutput("https://provider.example.test/result.png")
        result_document, result_hash = encode_result(output)

        claim_token = f"w1123-owner-{uuid4().hex}"
        claim_uow = context.uow_factory()
        try:
            claim = claim_uow.claim_durable_task(
                task_id,
                submission_id,
                claim_token,
                expected_user_id=actor.user_id,
            )
            assert claim is not None and claim.generation is not None
            generation = claim.generation
            claim_uow.commit()
        finally:
            claim_uow.close()

        dispatch_nonce = str(uuid4())
        wire_digest = hashlib.sha256(b"synthetic provider request").hexdigest()
        dispatch_uow = context.uow_factory()
        try:
            assert dispatch_uow.persist_dispatch_gate(
                task_id=task_id,
                submission_id=submission_id,
                user_id=actor.user_id,
                claim_token=claim_token,
                generation=generation,
                dispatch_nonce=dispatch_nonce,
                wire_digest=wire_digest,
            )
            dispatch_uow.commit()
        finally:
            dispatch_uow.close()

        assert ledger["request_digest"]
        result_uow = context.uow_factory()
        try:
            locked_ledger = result_uow.durable_submission_by_id(submission_id)
            assert locked_ledger is not None
            prepared = load_durable_prepared_snapshot(
                locked_ledger,
                crypto=context.crypto,
                proof_authority=context.proof_authority,
            )
            assert prepared is not None and prepared.credential_binding is not None
            encrypted_result, result_nonce, _key_id, _algorithm = seal_json_value(
                result_document,
                crypto=context.crypto,
                submission_id=submission_id,
                owner_id=actor.user_id,
                request_digest=prepared.request_digest or "",
                task_id=task_id,
                binding=prepared.credential_binding,
                purpose="fused-provider-result-v1",
            )
            assert result_uow.persist_provider_result(
                task_id=task_id,
                submission_id=submission_id,
                claim_token=claim_token,
                generation=generation,
                encrypted_result=encrypted_result,
                result_nonce=result_nonce,
                result_kind="url",
                result_digest=result_hash,
            )
            result_uow.commit()
        finally:
            result_uow.close()

        ledger_uow = context.uow_factory()
        try:
            ledger = ledger_uow.durable_submission_by_id(submission_id)
            assert ledger is not None
        finally:
            try:
                ledger_uow.rollback()
            finally:
                ledger_uow.close()
        prepared = load_durable_prepared_snapshot(
            ledger,
            crypto=context.crypto,
            proof_authority=context.proof_authority,
        )
        assert prepared is not None and prepared.credential_binding is not None
        cases["ownedPostgresInstanceVerified"] = True
        cases["supervisorPostgresPid"] = metadata["postgresPid"]
        cases["probeBackendPid"] = metadata["probeBackendPid"]

        content_a = b"\x89PNG\r\n\x1a\nsynthetic-owned-image-a"
        content_b = b"\x89PNG\r\n\x1a\nsynthetic-owned-image-b"
        plan_a = _plan(prepared=prepared, result_hash=result_hash, material=content_a, key_suffix="a")
        plan_b = _plan(prepared=prepared, result_hash=result_hash, material=content_b, key_suffix="b")
        plan_barrier = threading.Barrier(2)

        def save_plan(material: OwnedObjectMaterial):
            return _persist_plan(
                context.uow_factory,
                prepared,
                claim_token,
                generation,
                output,
                material,
                context.crypto,
                plan_barrier,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            plan_futures = [executor.submit(save_plan, plan) for plan in (plan_a, plan_b)]
            plan_results = [future.result(timeout=20) for future in plan_futures]
        saved_results = [result for result in plan_results if result[0]]
        assert len(saved_results) == 1
        assert len({result[2] for result in plan_results}) == 2
        assert all(result[1][:3] == ["ledger", "task", "journal"] for result in plan_results)
        cases["planRaceBackendPids"] = sorted(result[2] for result in plan_results)
        cases["planRaceOnePrimary"] = True
        cases["ledgerTaskJournalLockOrder"] = True

        material = plan_a if plan_results[0][0] else plan_b
        stale_uow = context.uow_factory()
        try:
            assert stale_uow.claim_fused_media_put(
                submission_id=submission_id,
                task_id=task_id,
                owner_id=actor.user_id,
                claim_token=claim_token,
                generation=generation - 1,
                write_nonce=str(uuid4()),
            ) is False
            stale_uow.rollback()
        finally:
            stale_uow.close()
        cases["staleGenerationCannotClaimPut"] = True

        storage = _SyntheticPlannedStorage(material.content)
        unknown_claim_factory, claim_ack_state = _commit_unknown_after_operation(
            context.uow_factory,
            "claim_fused_media_put",
        )
        execution = FusedGenerationExecution(
            runtime=None,
            uow_factory=unknown_claim_factory,
            credential_crypto=context.crypto,
            proof_authority=context.proof_authority,
            planned_media_storage=storage,
        )
        asyncio.run(
            execution._deliver_planned_media(
                prepared,
                claim_token,
                generation,
                output,
            )
        )
        after_lost_claim_ack = _read_row(context.uow_factory, submission_id)
        assert claim_ack_state["dropped"] is True
        assert after_lost_claim_ack["phase"] == "put_started"
        assert storage.put_count == 0
        cases["putStartedCommitAckUnknownZeroPut"] = True

        _expire_recovery_leases(engine, task_id, submission_id)
        scanner_rows = _read_recoverable_fused_media_writes(context.uow_factory, 1)
        assert scanner_rows == ({"submission_id": submission_id, "owner_id": actor.user_id},)
        cases["scannerSelectsCommittedOriginalKey"] = True
        asyncio.run(execution.recover_media_write(submission_id, actor.user_id))
        unknown_row = _read_row(context.uow_factory, submission_id)
        assert unknown_row["phase"] == "ack_unknown"
        assert storage.put_count == 0 and storage.get_count == 1
        assert storage.requested_keys == [material.plan.object_key]
        cases["recoveryGetOnlyAndNoProviderPosts"] = True

        _expire_recovery_leases(engine, task_id, submission_id)
        competing = [f"w1123-verifier-{uuid4().hex}" for _ in range(2)]
        with ThreadPoolExecutor(max_workers=2) as executor:
            verification_futures = [
                executor.submit(
                    _claim_fused_media_verification,
                    context.uow_factory,
                    submission_id,
                    actor.user_id,
                    token,
                    False,
                    None,
                    None,
                )
                for token in competing
            ]
            verification_claims = [future.result(timeout=20) for future in verification_futures]
        assert sum(verification_claims) == 1
        active_verifier = competing[verification_claims.index(True)]

        _expire_recovery_leases(engine, task_id, submission_id)
        takeover_token = f"w1123-verifier-takeover-{uuid4().hex}"
        assert _claim_fused_media_verification(
            context.uow_factory,
            submission_id,
            actor.user_id,
            takeover_token,
            False,
            None,
            None,
        ) is True
        assert _record_fused_media_verification(
            context.uow_factory,
            submission_id,
            material.plan.write_id,
            active_verifier,
            OwnedObjectVerification("unavailable"),
            None,
        ) is False
        cases["verifierLeaseRaceAndTakeoverFence"] = True

        storage.present = True
        verified = storage.verify_planned(material.plan)
        locator = verified.stored_media.locator if verified.stored_media else None
        assert verified.status == "verified" and locator is not None
        assert _record_fused_media_verification(
            context.uow_factory,
            submission_id,
            material.plan.write_id,
            takeover_token,
            verified,
            locator,
        ) is True

        final_ack_dropped = threading.Event()
        final_barrier = threading.Barrier(2)
        final_factory_lock = threading.Lock()
        final_uow_count = {"count": 0}
        final_context = context.uow_factory

        def final_ack_factory():
            uow = final_context()
            with final_factory_lock:
                instrument = final_uow_count["count"] < 2
                final_uow_count["count"] += 1
            if not instrument:
                return uow
            complete = uow.complete_verified_fused_media
            commit = uow.commit
            state = {"successful_apply": False}

            def complete_after_rendezvous(*args, **kwargs):
                final_barrier.wait(timeout=10)
                state["successful_apply"] = bool(complete(*args, **kwargs))
                return state["successful_apply"]

            def commit_after_apply():
                commit()
                if state["successful_apply"] and not final_ack_dropped.is_set():
                    final_ack_dropped.set()
                    raise RuntimeError("synthetic final commit acknowledgement lost")

            uow.complete_verified_fused_media = complete_after_rendezvous
            uow.commit = commit_after_apply
            return uow

        with ThreadPoolExecutor(max_workers=2) as executor:
            final_futures = [
                executor.submit(
                    _complete_verified_fused_media,
                    final_ack_factory,
                    prepared,
                    material.plan.write_id,
                    takeover_token,
                    locator,
                )
                for _ in range(2)
            ]
            final_results = [future.result(timeout=25) for future in final_futures]
        assert final_ack_dropped.is_set()
        assert final_results == [True, True]
        cases["verifiedApplyCommitAckUnknown"] = True

        from haoai_backend.fused_generation.persistence import _fused_submissions
        from haoai_backend.authentication.tables import user_credits
        from haoai_backend.generation_core.base_schema import CreditLogRow

        with engine.connect() as connection:
            journal_count = connection.execute(
                select(FusedMediaWriteRow.__table__.c.write_id).where(
                    FusedMediaWriteRow.submission_id == submission_id,
                    FusedMediaWriteRow.slot == "primary",
                )
            ).all()
            journal = connection.execute(
                select(FusedMediaWriteRow.__table__).where(
                    FusedMediaWriteRow.submission_id == submission_id
                )
            ).mappings().one()
            final_task = connection.execute(
                select(AITaskRow.__table__.c.status, AITaskRow.__table__.c.result)
                .where(AITaskRow.id == task_id)
            ).mappings().one()
            final_ledger = connection.execute(
                select(_fused_submissions.c.stage, _fused_submissions.c.asset_id)
                .where(_fused_submissions.c.submission_id == submission_id)
            ).mappings().one()
            asset_rows = connection.execute(
                select(fused_assets.c.id).where(fused_assets.c.series_id == series_id)
            ).all()
            message_rows = connection.execute(
                select(chat_messages.c.id, chat_messages.c.content).where(
                    chat_messages.c.chapter_id == chapter_id
                )
            ).all()
            task_rows = connection.execute(
                select(AITaskRow.__table__.c.id).where(
                    AITaskRow.__table__.c.user_id == actor.user_id,
                    AITaskRow.__table__.c.type == "fused",
                )
            ).all()
            final_chapter_content = connection.execute(
                select(chapters.c.content).where(chapters.c.id == chapter_id)
            ).scalar_one()
            credit_rows = connection.execute(
                select(CreditLogRow.__table__.c.type, CreditLogRow.__table__.c.amount)
            ).all()
            balance = connection.execute(
                select(user_credits.c.credits).where(user_credits.c.user_id == actor.user_id)
            ).scalar_one()
        assert journal["phase"] == "applied"
        assert len(journal_count) == 1
        assert final_ledger["stage"] == "completed"
        assert final_ledger["asset_id"] == journal["asset_id"]
        assert final_task["status"] == "completed" and final_task["result"] == locator
        assert len(asset_rows) == baseline_assets + 1
        assert [row.id for row in asset_rows].count(journal["asset_id"]) == 1
        assert len(message_rows) == baseline_messages + 1
        assert ledger["message_id"]
        assert sum(row.id == ledger["message_id"] and row.content == locator for row in message_rows) == 1
        assert len(task_rows) == baseline_tasks + 1
        assert [row.id for row in task_rows].count(task_id) == 1
        final_frames = json.loads(final_chapter_content)
        assert len(final_frames) == 1
        assert journal["asset_id"] in final_frames[0]["fused"]
        assert len(credit_rows) == 1 and credit_rows[0].type == "usage"
        assert balance == 16
        assert storage.put_count == 0 and storage.get_count == 2
        cases["oneAssetOneMessageOneTask"] = True
        cases["singleAdmissionFee"] = True
        _write_receipt(receipt_path, "PASS", cases, actual_owned_postgres=True)
    except Exception as error:
        _write_receipt(
            receipt_path,
            "FAIL",
            cases,
            type(error).__name__,
            actual_owned_postgres=True,
        )
        raise
