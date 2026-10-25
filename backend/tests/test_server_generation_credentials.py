"""The server credential bundle loader and CLI use only synthetic local data."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import selectors
import stat
import subprocess
import sys
import threading
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import event, insert, select
from sqlalchemy.engine import create_engine
from sqlalchemy.orm import Session

from backend.server.__main__ import run_cli
from backend.server.configuration import load_server_configuration
from backend.server.credentials import (
    CredentialBundleError,
    load_root_credential_resources,
    provision_credential_bundle,
)
from haoai_backend.authentication.tables import metadata as auth_metadata, users
from haoai_backend.generation_catalog.tables import model_configs, system_models
from haoai_backend.generation_credentials.application import (
    build_credential_registry,
)
from haoai_backend.generation_credentials.persistence import (
    metadata as registry_metadata,
    credential_versions,
)
from haoai_backend.generation_core.base_schema import AITaskRow


_ACTOR_ID = "10000000-0000-4000-8000-000000000001"
_MODEL_ID = "20000000-0000-4000-8000-000000000001"
_SYSTEM_MODEL_ID = "20000000-0000-4000-8000-000000000002"
_PRINCIPAL_ID = "30000000-0000-4000-8000-000000000001"
_PROVIDER_SECRET = "synthetic-provider-secret-604"


def _write_owner_file(path: Path, value: object, *, mode: int = 0o600) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        os.write(
            descriptor,
            json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(),
        )
    finally:
        os.close(descriptor)


def _write_owner_bytes(path: Path, content: bytes, *, mode: int = 0o600) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        os.write(descriptor, content)
    finally:
        os.close(descriptor)


def _credential_input(
    *,
    source: str = "user",
    row_id: str = _MODEL_ID,
    owner_or_system: str = _ACTOR_ID,
) -> dict[str, object]:
    encode = lambda key: base64.b64encode(key).decode("ascii")
    return {
        "format": "haoai-root-enrollment-input-v1",
        "proof_key": {"key_id": "proof-test-v1", "key_base64": encode(b"p" * 32)},
        "guard_key": {"key_id": "guard-test-v1", "key_base64": encode(b"g" * 32)},
        "aead_key": {
            "key_id": "aead-test-v1",
            "algorithm_version": "aes-256-gcm-v1",
            "key_base64": encode(b"a" * 32),
        },
        "records": [
            {
                "record_id": "root-record-test-1",
                "principal_id": _PRINCIPAL_ID,
                "scope": {
                    "source": source,
                    "row_id": row_id,
                    "owner_or_system": owner_or_system,
                    "provider": "openai",
                },
                "issuer_namespace": "provider-account-v1",
                "stable_account_subject": "synthetic-account-subject",
                "key_generation": "generation-test-1",
                "material_ref": "synthetic://provider/account-1",
                "provider_secret": _PROVIDER_SECRET,
                "evidence_ref": "synthetic-evidence://root-record-test-1",
                "evidence_version": "synthetic-evidence-v1",
                "valid_from": "2026-01-01T00:00:00+00:00",
                "valid_until": None,
                "revoked_at": None,
                "endpoint_policy": {
                    "provider_origin": "https://provider.example.test",
                    "api_base_path": "/v2",
                    "allowed_content_paths": [],
                    "policy_version": "endpoint-policy-v1",
                },
            }
        ],
    }


def _provisioned_bundle(tmp_path: Path) -> tuple[Path, Path]:
    input_path = tmp_path / "root-input.json"
    bundle_path = tmp_path / "root-bundle.json"
    _write_owner_file(input_path, _credential_input())
    assert provision_credential_bundle(input_path, bundle_path) == 1
    return input_path, bundle_path


def test_provisioned_bundle_is_authenticated_encrypted_and_owner_only(
    tmp_path: Path,
) -> None:
    input_path, bundle_path = _provisioned_bundle(tmp_path)
    bundle_bytes = bundle_path.read_bytes()
    assert stat.S_IMODE(bundle_path.stat().st_mode) == 0o600
    assert _PROVIDER_SECRET.encode() not in bundle_bytes

    resources = load_root_credential_resources(bundle_path, legacy_secret_key="synthetic-legacy-key")
    assert resources.enrollment_source.ready
    assert len(resources.enrollment_source.records) == 1
    assert resources.crypto.ready()
    record = resources.enrollment_source.records[0]
    assert resources.crypto.resolve_material(record.material_ref) == _PROVIDER_SECRET
    assert resources.crypto.resolve_material(record.material_ref + "/wrong") is None
    assert _PROVIDER_SECRET not in repr(resources)
    assert _PROVIDER_SECRET not in repr(resources.crypto)

    with pytest.raises(CredentialBundleError, match="already exists"):
        provision_credential_bundle(input_path, bundle_path)


def test_bundle_rejects_bad_signature_and_unsafe_file_modes(tmp_path: Path) -> None:
    _, bundle_path = _provisioned_bundle(tmp_path)
    damaged = json.loads(bundle_path.read_text())
    damaged["records"][0]["proof_signature"] = "f" * 64
    bundle_path.chmod(0o600)
    bundle_path.write_text(json.dumps(damaged, separators=(",", ":")))
    with pytest.raises(CredentialBundleError):
        load_root_credential_resources(bundle_path, legacy_secret_key="synthetic")

    bundle_path.chmod(0o644)
    with pytest.raises(CredentialBundleError, match="permissions"):
        load_root_credential_resources(bundle_path, legacy_secret_key="synthetic")


def test_bundle_rejects_fifo_without_waiting_for_a_writer(tmp_path: Path) -> None:
    fifo_path = tmp_path / "credential-bundle.fifo"
    os.mkfifo(fifo_path, 0o600)
    script = """
from pathlib import Path
import sys
from backend.server.credentials import CredentialBundleError, load_root_credential_resources

print("READY", flush=True)
if sys.stdin.readline() != "GO\\n":
    raise SystemExit("parent did not release FIFO check")

try:
    load_root_credential_resources(Path(sys.argv[1]), legacy_secret_key="synthetic")
except CredentialBundleError:
    raise SystemExit(0)
raise SystemExit("FIFO was accepted")
"""
    child = subprocess.Popen(
        [sys.executable, "-B", "-c", script, str(fifo_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        assert child.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=15):
                pytest.fail("credential child did not finish cold import within 15 seconds")
        ready_line = child.stdout.readline()
        if ready_line != "READY\n":
            _remaining_stdout, stderr = child.communicate(timeout=1)
            pytest.fail(
                "credential child exited before the FIFO check was released: "
                + stderr
            )

        try:
            _stdout, stderr = child.communicate(input="GO\n", timeout=2)
        except subprocess.TimeoutExpired:
            pytest.fail("FIFO credential loading blocked past the 2-second check deadline")
        assert child.returncode == 0, stderr
    finally:
        if child.poll() is None:
            child.kill()
        try:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
        for stream in (child.stdin, child.stdout, child.stderr):
            if stream is not None and not stream.closed:
                stream.close()


def test_bundle_rejects_symlink_duplicate_json_key_and_oversize_file(tmp_path: Path) -> None:
    _, bundle_path = _provisioned_bundle(tmp_path)
    symlink_path = tmp_path / "credential-bundle-link.json"
    symlink_path.symlink_to(bundle_path)
    with pytest.raises(CredentialBundleError, match="unavailable"):
        load_root_credential_resources(symlink_path, legacy_secret_key="synthetic")

    duplicate_key_path = tmp_path / "duplicate-key.json"
    _write_owner_bytes(duplicate_key_path, b'{"format":"first","format":"second"}')
    with pytest.raises(CredentialBundleError, match="JSON is invalid"):
        load_root_credential_resources(duplicate_key_path, legacy_secret_key="synthetic")

    oversized_path = tmp_path / "oversized.json"
    _write_owner_bytes(oversized_path, b"x" * (2 * 1024 * 1024 + 1))
    with pytest.raises(CredentialBundleError, match="permissions or size"):
        load_root_credential_resources(oversized_path, legacy_secret_key="synthetic")


def _prepare_cli_registry(tmp_path: Path) -> tuple[Path, dict[str, str], Path]:
    database_path = tmp_path / "credential-cli.sqlite"
    engine = create_engine(f"sqlite:///{database_path}")
    auth_metadata.create_all(engine, tables=[users])
    model_configs.create(engine)
    system_models.create(engine)
    registry_metadata.create_all(engine)
    AITaskRow.__table__.create(engine)
    with engine.begin() as connection:
        connection.execute(
            insert(users).values(
                id=_ACTOR_ID,
                username="synthetic-owner",
                email="synthetic-owner@example.test",
                hashed_password="fixture-only",
                is_superuser=True,
                membership_type="free",
                created_at=datetime(2026, 1, 1),
            )
        )
        connection.execute(
            insert(model_configs).values(
                id=_MODEL_ID,
                user_id=_ACTOR_ID,
                category="image",
                name="Synthetic provider model",
                provider="openai",
                api_key=_PROVIDER_SECRET,
                model="synthetic-model",
                base_url="https://provider.example.test/v2",
                sort_order=1,
                params="{}",
                created_at=datetime(2026, 1, 1),
                updated_at=datetime(2026, 1, 1),
            )
        )
        connection.execute(
            insert(system_models).values(
                id=_SYSTEM_MODEL_ID,
                category="image",
                name="Synthetic system model",
                provider="openai",
                api_key=_PROVIDER_SECRET,
                model="synthetic-model",
                base_url="https://provider.example.test/v2",
                cost_per_call=0,
                description="synthetic fixture",
                params="{}",
                is_active=True,
                sort_order=1,
                created_at=datetime(2026, 1, 1),
                updated_at=datetime(2026, 1, 1),
            )
        )
    engine.dispose()
    _, bundle_path = _provisioned_bundle(tmp_path)
    environment = {
        "ENVIRONMENT": "test",
        "DATABASE_URL": f"sqlite:///{database_path}",
        "SECRET_KEY": "synthetic-server-secret",
    }
    return database_path, environment, bundle_path


@pytest.mark.parametrize("enabled", [False, True])
def test_server_host_cold_composition_uses_lazy_shared_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
) -> None:
    _database_path, environment, bundle_path = _prepare_cli_registry(tmp_path)
    configuration = load_server_configuration(
        {**environment, "FRAME_OPTIMIZE_CREDENTIALS_FILE": str(bundle_path)}
    )
    configuration = replace(configuration, frame_optimize_enabled=enabled)
    from backend.server import application as server_application
    from haoai_backend.generation_credentials.domain import CredentialCrypto

    original_create_engine = server_application._create_engine
    original_resolve_material = CredentialCrypto.resolve_material
    tracked_engines = []
    statements: list[str] = []
    connections: list[object] = []
    material_resolutions: list[str] = []

    def track_engine(database_url: str):
        engine = original_create_engine(database_url)
        tracked_engines.append(engine)
        event.listen(engine, "connect", lambda *_args: connections.append(object()))
        event.listen(
            engine,
            "before_cursor_execute",
            lambda _connection, _cursor, statement, *_args: statements.append(statement),
        )
        return engine

    def track_material_resolution(self, material_ref: str):
        material_resolutions.append(material_ref)
        return original_resolve_material(self, material_ref)

    monkeypatch.setattr(server_application, "_create_engine", track_engine)
    monkeypatch.setattr(CredentialCrypto, "resolve_material", track_material_resolution)
    before_threads = {thread.ident for thread in threading.enumerate()}

    host = server_application.create_server_host(configuration)
    try:
        worker = host.app.state.server_frame_optimize_worker_configuration
        assert worker is not None and worker.enabled is enabled
        assert worker.runtime.session_factory is host.session_factory
        assert worker.registry_capabilities.read_session_factory is host.session_factory
        assert worker.runtime.db_clock is worker.registry_capabilities.db_clock
        assert worker.runtime.id_factory is worker.registry_capabilities.id_factory
        assert host.app.state.frame_optimize_worker_service is None
        assert statements == []
        assert connections == []
        assert material_resolutions == []
        assert {thread.ident for thread in threading.enumerate()} == before_threads
    finally:
        host.close()

    assert len(tracked_engines) == 1


def test_server_host_opt_in_wires_image_single_admission_and_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transfer_dir = tmp_path / "transfer"
    transfer_dir.mkdir()
    _, bundle_path = _provisioned_bundle(tmp_path)
    environment = {
        "ENVIRONMENT": "test",
        "DATABASE_URL": f"sqlite:///{tmp_path / 'image-single-host.sqlite'}",
        "SECRET_KEY": "synthetic-server-secret",
        "OBJECT_STORAGE_ENDPOINT_URL": "https://acct.r2.cloudflarestorage.com",
        "OBJECT_STORAGE_ACCESS_KEY_ID": "synthetic-access-key",
        "OBJECT_STORAGE_SECRET_ACCESS_KEY": "synthetic-r2-secret",
        "OBJECT_STORAGE_BUCKET": "haoai-media",
        "OBJECT_STORAGE_PUBLIC_BASE_URL": "https://cdn.example.test/media",
        "TRANSFER_TMP_DIR": str(transfer_dir),
        "IMAGE_SINGLE_ENABLED": "true",
        "IMAGE_SINGLE_CREDENTIALS_FILE": str(bundle_path),
    }
    configuration = load_server_configuration(environment)
    from haoai_backend import app as app_module
    from backend.server import application as server_application
    from haoai_backend.chat_media_admission import ChatMediaAdmissionConfiguration
    from haoai_backend.generation_runtime.assembly import GenerationWorkerConfiguration

    captured: dict[str, object] = {}
    original_create_app = app_module.create_app

    def observe_create_app(**kwargs: object):
        captured.update(kwargs)
        return original_create_app(**kwargs)

    monkeypatch.setattr(app_module, "create_app", observe_create_app)
    host = server_application.create_server_host(
        configuration,
        sdk_factories=(lambda **_kwargs: object(), lambda **_kwargs: object()),
    )
    try:
        admission = captured.get("chat_media_admission_configuration")
        worker = captured.get("generation_worker_configuration")
        assert isinstance(admission, ChatMediaAdmissionConfiguration)
        assert frozenset(admission.enabled_operations) == frozenset({"image.single"})
        with pytest.raises(TypeError, match="image_references_enabled"):
            replace(admission, image_references_enabled=1)  # type: ignore[arg-type]
        assert isinstance(worker, GenerationWorkerConfiguration)
        assert worker.runtime.session_factory is host.session_factory
        assert worker.registry_capabilities.read_session_factory is host.session_factory
        assert worker.runtime.db_clock is worker.registry_capabilities.db_clock
        assert worker.runtime.id_factory is worker.registry_capabilities.id_factory
        assert worker.registry_capabilities.enrollment_source is not None
        image_host = host.app.state.server_image_generation_configuration
        assert image_host is not None
        assert image_host.media_runtime is not None
        assert image_host.media_runtime.state == "new"
        assert admission.image_references_enabled is False
        assert worker.runtime.timeout_settings.public_media_fetch_seconds == 600
        assert worker.runtime.concurrency_settings.public_media_fetches == 2
    finally:
        host.close()


def test_server_host_disabled_image_single_does_not_load_bundle_or_create_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.server import application as server_application
    from backend.server import credentials as server_credentials

    credentials_path = tmp_path / "missing-image-single-bundle.json"
    configuration = load_server_configuration(
        {
            "ENVIRONMENT": "test",
            "DATABASE_URL": f"sqlite:///{tmp_path / 'image-single-disabled.sqlite'}",
            "SECRET_KEY": "synthetic-server-secret",
            "IMAGE_SINGLE_CREDENTIALS_FILE": str(credentials_path),
        }
    )
    monkeypatch.setattr(
        server_credentials,
        "load_registry_capabilities",
        lambda *_args, **_kwargs: pytest.fail(
            "disabled image.single must not read its credentials file"
        ),
    )

    host = server_application.create_server_host(configuration)
    try:
        assert host.app.state.server_image_generation_configuration is None
        assert host.app.state.generation_worker_runtime is None
        assert not credentials_path.exists()
    finally:
        host.close()


def test_server_host_injects_storyboard_asset_runtime_without_startup_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configuration = load_server_configuration(
        {
            "ENVIRONMENT": "test",
            "DATABASE_URL": f"sqlite:///{tmp_path / 'storyboard-asset-runtime.sqlite'}",
            "SECRET_KEY": "synthetic-server-secret",
        }
    )
    from haoai_backend import app as app_module
    from backend.server import application as server_application
    from haoai_backend.storyboard_asset_generation.openai_runtime import (
        OpenAICompatibleStoryboardAssetRuntime,
    )

    captured: dict[str, object] = {}
    original_create_app = app_module.create_app

    def observe_create_app(**kwargs: object):
        captured.update(kwargs)
        return original_create_app(**kwargs)

    monkeypatch.setattr(app_module, "create_app", observe_create_app)
    host = server_application.create_server_host(configuration)
    try:
        assert isinstance(
            captured["storyboard_asset_generation_runtime"],
            OpenAICompatibleStoryboardAssetRuntime,
        )
    finally:
        host.close()


def _register_arguments(
    bundle_path: Path,
    *,
    attempt_id: str,
    source: str = "user",
    row_id: str = _MODEL_ID,
    owner_or_system: str = _ACTOR_ID,
) -> list[str]:
    return [
        "credential",
        "register",
        "--credentials-file",
        str(bundle_path),
        "--source",
        source,
        "--row-id",
        row_id,
        "--owner-or-system",
        owner_or_system,
        "--record-id",
        "root-record-test-1",
        "--actor-id",
        _ACTOR_ID,
        "--attempt-id",
        attempt_id,
    ]


def test_actual_cli_registers_then_freshly_resolves_original_credential(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path, environment, bundle_path = _prepare_cli_registry(tmp_path)
    attempt_id = str(uuid4())

    exit_code = run_cli(
        _register_arguments(bundle_path, attempt_id=attempt_id),
        environ=environment,
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "credential registration confirmed" in captured.out
    assert _PROVIDER_SECRET not in captured.out + captured.err

    host_configuration = load_server_configuration(
        {**environment, "FRAME_OPTIMIZE_CREDENTIALS_FILE": str(bundle_path)}
    )
    from backend.server.application import create_server_host
    from haoai_backend.generation_credentials.domain import CredentialScope

    host = create_server_host(host_configuration)
    try:
        worker = host.app.state.server_frame_optimize_worker_configuration
        assert worker is not None
        assert worker.enabled is False
        assert worker.runtime.session_factory is host.session_factory
        assert worker.registry_capabilities.read_session_factory is host.session_factory
        assert worker.runtime.db_clock is worker.registry_capabilities.db_clock
        assert worker.runtime.id_factory is worker.registry_capabilities.id_factory
        registry = build_credential_registry(worker.registry_capabilities)
        scope = CredentialScope("user", _MODEL_ID, _ACTOR_ID, "openai")
        with host.session_factory() as session:
            captured_binding = registry.capture_original_binding(session, scope)
        assert captured_binding is not None
        resolved = registry.resolve_original_credential(captured_binding.binding, "query")
        assert resolved is not None
        assert resolved.secret == _PROVIDER_SECRET
        assert _PROVIDER_SECRET not in repr(resolved)
    finally:
        host.close()

    enabled_configuration = replace(host_configuration, frame_optimize_enabled=True)
    enabled_host = create_server_host(enabled_configuration)
    try:
        enabled_worker = enabled_host.app.state.server_frame_optimize_worker_configuration
        assert enabled_worker is not None
        assert enabled_worker.enabled is True
        assert enabled_worker.runtime.session_factory is enabled_host.session_factory
        assert enabled_worker.registry_capabilities.read_session_factory is enabled_host.session_factory
        assert enabled_worker.runtime.db_clock is enabled_worker.registry_capabilities.db_clock
        assert enabled_worker.runtime.id_factory is enabled_worker.registry_capabilities.id_factory
        assert enabled_host.app.state.frame_optimize_worker_service is None

        async def enter_worker_lifespan() -> None:
            async with enabled_host.app.router.lifespan_context(enabled_host.app):
                service = enabled_host.app.state.frame_optimize_worker_service
                assert service is not None
                assert service.is_running

        asyncio.run(enter_worker_lifespan())
        assert enabled_host.app.state.frame_optimize_worker_service is None
        shutdown = enabled_host.app.state.frame_optimize_worker_shutdown
        assert shutdown is not None
        assert shutdown.timed_out is False
    finally:
        enabled_host.close()

    engine = create_engine(f"sqlite:///{database_path}")
    try:
        with engine.connect() as connection:
            attempts = connection.execute(select(credential_versions.c.server_attempt_id)).all()
        assert [row[0] for row in attempts] == [attempt_id]
    finally:
        engine.dispose()


def test_actual_cli_registers_system_model_only_for_database_superuser(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _database_path, environment, _user_bundle_path = _prepare_cli_registry(tmp_path)
    input_path = tmp_path / "system-root-input.json"
    bundle_path = tmp_path / "system-root-bundle.json"
    _write_owner_file(
        input_path,
        _credential_input(
            source="system",
            row_id=_SYSTEM_MODEL_ID,
            owner_or_system="system",
        ),
    )
    assert provision_credential_bundle(input_path, bundle_path) == 1
    attempt_id = str(uuid4())

    exit_code = run_cli(
        _register_arguments(
            bundle_path,
            attempt_id=attempt_id,
            source="system",
            row_id=_SYSTEM_MODEL_ID,
            owner_or_system="system",
        ),
        environ=environment,
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "credential registration confirmed" in captured.out
    assert _PROVIDER_SECRET not in captured.out + captured.err


def test_cli_unknown_commit_confirms_once_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path, environment, bundle_path = _prepare_cli_registry(tmp_path)
    commit_calls = 0

    def fail_commit_before_durability(_session: Session) -> None:
        nonlocal commit_calls
        commit_calls += 1
        raise RuntimeError("synthetic connection loss")

    monkeypatch.setattr(Session, "commit", fail_commit_before_durability)
    attempt_id = str(uuid4())
    exit_code = run_cli(
        _register_arguments(bundle_path, attempt_id=attempt_id),
        environ=environment,
    )
    captured = capsys.readouterr()
    assert exit_code == 3
    assert commit_calls == 1
    assert "original attempt_id=" + attempt_id in captured.err
    assert "automatic retry disabled" in captured.err
    assert _PROVIDER_SECRET not in captured.out + captured.err

    engine = create_engine(f"sqlite:///{database_path}")
    try:
        with engine.connect() as connection:
            attempts = connection.execute(select(credential_versions)).all()
        assert attempts == []
    finally:
        engine.dispose()
