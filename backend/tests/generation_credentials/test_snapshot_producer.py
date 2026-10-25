from __future__ import annotations

import hashlib
import hmac
import json
import uuid
import weakref
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Iterator

import pytest
from sqlalchemy import Column, DateTime, String, Table, create_engine, event, func, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, registry as orm_registry, sessionmaker

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.persistence import SqlAlchemyAuthenticationUnitOfWork
from haoai_backend.authentication.tables import metadata as authentication_metadata
from haoai_backend.authentication.tables import user_credits, users
from haoai_backend.generation_catalog.admission import AdmissionPolicy, prepare_admission
from haoai_backend.generation_catalog.admission_types import (
    AdmissionPreparationFacts,
    AdmissionOperation,
    AdmissionRefusal,
    AdmissionReplay,
    LockedTargetFacts,
    build_admission_context,
)
from haoai_backend.generation_catalog.tables import (
    ASSET_TABLES,
    chapters,
    metadata as catalog_metadata,
    model_configs,
)
from haoai_backend.generation_contracts.types import (
    AdmissionFingerprint,
    GenerationActor,
    TargetSnapshot,
)
from haoai_backend.generation_core.base_schema import AITaskRow, Base, BillingUnitRow, TaskSubmissionRow
from haoai_backend.generation_core.schema import (
    GENERATION_MIGRATION_HISTORY_TABLE,
    GENERATION_SCHEMA_CHECKSUM,
    GENERATION_SCHEMA_VERSION,
)
from haoai_backend.generation_credentials.application import (
    CredentialRegistry,
    RegistryCapabilities,
    build_credential_registry,
)
from haoai_backend.generation_credentials.domain import (
    CredentialCrypto,
    CredentialScope,
    EndpointPolicy,
    HmacProofAuthority,
    ProtectedAccountRecord,
    RegistrationStaged,
    RegistryRefusal,
    RootEnrollmentSource,
    RotationAttestation,
    SealedMaterial,
    account_guard_associated_data,
    make_server_attempt,
)
from haoai_backend.generation_credentials.persistence import (
    credential_bindings,
    credential_principals,
    credential_schema_migrations,
    metadata as credential_metadata,
    prepared_payloads,
)
from haoai_backend.generation_credentials.schema import (
    CREDENTIAL_MIGRATION_FILENAME,
    CREDENTIAL_SCHEMA_VERSION,
    SQLITE_GUARD_TABLES,
)
from haoai_backend.generation_credentials.snapshot_producer import (
    MediaSnapshotProducer,
    PayloadVerificationResult,
    SnapshotVersions,
    SnapshotRefusal,
    SnapshotPayloadsStaged,
    _UnsafeSnapshot,
    _admission_policy_value,
    _context_fingerprint_digest,
    _freeze_mapping,
    _graph_version,
    _legacy_message_projection,
    _parse_params,
    _prompt_values,
    _plain,
    _reference_image_metadata,
    _rule_digest,
    _server_identifier,
    _snapshot_model_name,
    _snapshot_model_parameters,
    build_media_snapshot_producer,
)
from haoai_backend.generation_catalog.domain import ModelSource, PromptOperation, PromptSelection
from haoai_backend.series_access.tables import metadata as series_access_metadata, series
from haoai_backend.shared.identity import TrustedActor


FIXTURE_NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
ACTOR_ID = "account-1"
FOREIGN_ACTOR_ID = "account-2"
MODEL_ID = "model-1"
PRINCIPAL_ID = "30000000-0000-4000-8000-000000000001"
PROVIDER_SECRET = "fixture-provider-secret"

generation_schema_migrations = Table(
    GENERATION_MIGRATION_HISTORY_TABLE,
    Base.metadata,
    Column("version", String(64), primary_key=True, nullable=False),
    Column("checksum", String(64), nullable=False),
    Column("applied_at", DateTime(timezone=False), nullable=False),
)


class _AuthenticationService:
    def __init__(self) -> None:
        self.events: list[str] = []

    @contextmanager
    def authenticated(
        self,
        authorization: str,
        *,
        require_membership: bool,
    ) -> Iterator[SimpleNamespace]:
        assert authorization == "Bearer account-token"
        assert require_membership is False
        self.events.append("authentication_open")
        try:
            yield SimpleNamespace(user=SimpleNamespace(id="account-1"))
        finally:
            self.events.append("authentication_closed")


class _RegistryMustNotStage:
    def stage_prepared_payload(self, *args: object, **kwargs: object) -> None:
        pytest.fail("replay and refused preparation must not stage payloads")


def _producer(authentication: _AuthenticationService) -> MediaSnapshotProducer:
    return MediaSnapshotProducer(
        authentication,
        object(),
        _RegistryMustNotStage(),
        server_id_factory=lambda: pytest.fail("no ID is needed before fresh preparation"),
        policies={AdmissionOperation.IMAGE_SINGLE.value: AdmissionPolicy()},
        versions=SnapshotVersions(projector="projector-v1", reference_policy="refs-v1"),
    )


def _fingerprint() -> AdmissionFingerprint:
    return AdmissionFingerprint(
        schema_version="legacy-admission-context-v1",
        ordered_nonsecret_payload_digest="a" * 64,
        actor_id="account-1",
        operation=AdmissionOperation.IMAGE_SINGLE.value,
    )


@pytest.mark.parametrize(
    "result",
    [
        AdmissionReplay(fingerprint=_fingerprint(), receipt=object()),
        AdmissionRefusal(409, "original_request_conflict", "safe refusal"),
    ],
)
def test_original_receipt_or_refusal_returns_before_registry_or_snapshot_work(
    monkeypatch: pytest.MonkeyPatch,
    result: AdmissionReplay | AdmissionRefusal,
) -> None:
    authentication = _AuthenticationService()
    producer = _producer(authentication)
    events = authentication.events

    def prepare(session: Session, context: object, *, policy: object) -> object:
        assert session.autoflush is False
        assert getattr(context, "actor").user_id == "account-1"
        events.append("a2_original_receipt")
        return result

    def readiness(*args: object, **kwargs: object) -> object:
        pytest.fail("credential readiness must follow the A2 replay branch")

    monkeypatch.setattr("haoai_backend.generation_credentials.snapshot_producer.prepare_admission", prepare)
    monkeypatch.setattr(
        "haoai_backend.generation_credentials.snapshot_producer.check_credential_registry_readiness",
        readiness,
    )

    session = Session()
    actual = producer.prepare_and_stage(
        session,
        authorization="Bearer account-token",
        operation=AdmissionOperation.IMAGE_SINGLE.value,
        raw_key="request-key-01",
        request={"model_id": "model-1", "prompt": "draw", "target": {"kind": "standalone"}},
    )

    assert actual is result
    assert events == ["authentication_open", "authentication_closed", "a2_original_receipt"]
    assert session.autoflush is True
    assert session.in_transaction() is False
    session.close()


def test_unready_registry_refuses_before_ids_or_payload_staging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authentication = _AuthenticationService()
    producer = _producer(authentication)
    events = authentication.events

    def prepare(session: Session, context: object, *, policy: object) -> object:
        assert session.autoflush is False
        events.append("a2_prepare")
        return AdmissionPreparationFacts(
            operation=AdmissionOperation.IMAGE_SINGLE.value,
            fingerprint=getattr(context, "fingerprint"),
            phase="initial",
            items=(),
            quoted_total=0,
            required_balance=0,
            available_balance=0,
        )

    def readiness(session: Session, capabilities: object) -> SimpleNamespace:
        assert session.autoflush is False
        events.append("registry_readiness")
        return SimpleNamespace(ready=False)

    monkeypatch.setattr("haoai_backend.generation_credentials.snapshot_producer.prepare_admission", prepare)
    monkeypatch.setattr(
        "haoai_backend.generation_credentials.snapshot_producer.check_credential_registry_readiness",
        readiness,
    )

    session = Session()
    result = producer.prepare_and_stage(
        session,
        authorization="Bearer account-token",
        operation=AdmissionOperation.IMAGE_SINGLE.value,
        raw_key="request-key-02",
        request={"model_id": "model-1", "prompt": "draw", "target": {"kind": "standalone"}},
    )

    assert result == SnapshotRefusal(503, "credential_registry_unavailable")
    assert events == ["authentication_open", "authentication_closed", "a2_prepare", "registry_readiness"]
    assert session.autoflush is True
    assert session.in_transaction() is False
    session.close()


def test_a2_fingerprint_must_match_authenticated_request_before_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authentication = _AuthenticationService()
    producer = _producer(authentication)

    def prepare(session: Session, context: object, *, policy: object) -> AdmissionPreparationFacts:
        assert session.autoflush is False
        return AdmissionPreparationFacts(
            operation=AdmissionOperation.IMAGE_SINGLE.value,
            fingerprint=_fingerprint(),
            phase="initial",
            items=(),
            quoted_total=0,
            required_balance=0,
            available_balance=0,
        )

    def readiness(*args: object, **kwargs: object) -> object:
        pytest.fail("a mismatched A2 fingerprint must stop before registry readiness")

    monkeypatch.setattr("haoai_backend.generation_credentials.snapshot_producer.prepare_admission", prepare)
    monkeypatch.setattr(
        "haoai_backend.generation_credentials.snapshot_producer.check_credential_registry_readiness",
        readiness,
    )

    session = Session()
    result = producer.prepare_and_stage(
        session,
        authorization="Bearer account-token",
        operation=AdmissionOperation.IMAGE_SINGLE.value,
        raw_key="request-key-02b",
        request={"model_id": "model-1", "prompt": "draw", "target": {"kind": "standalone"}},
    )

    assert result == SnapshotRefusal(409, "admission_fingerprint_mismatch")
    assert authentication.events == ["authentication_open", "authentication_closed"]
    assert session.autoflush is True
    assert session.in_transaction() is False
    session.close()


def test_prompt_snapshot_keeps_the_exact_selected_templates() -> None:
    selection = PromptSelection(
        prompt_id="prompt-1",
        operation=PromptOperation.IMAGE_BATCH,
        source=ModelSource.USER,
        category="image",
        display_name="Storyboard prompt",
        system_prompt="system template",
        user_prompt="user template",
    )

    values = _plain(_prompt_values(selection, "account-1"))

    assert values == {
        "prompt_id": "prompt-1",
        "owner_or_system": "account-1",
        "operation": PromptOperation.IMAGE_BATCH.value,
        "source": ModelSource.USER.value,
        "category": "image",
        "display_name": "Storyboard prompt",
        "system_prompt": "system template",
        "user_prompt": "user template",
    }


def test_reference_image_metadata_preserves_order_duplicates_and_unicode() -> None:
    assert _reference_image_metadata(("one", "one", "图")) == (
        3,
        "2297512eba635d6b8fd2ad5f11d0c47c4523bb4f2f2a6d35ab3e15e8715d095c",
    )
    assert _reference_image_metadata(()) == (
        0,
        "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
    )


def test_batch_message_uses_raw_description_and_legacy_asset_name_fallbacks() -> None:
    facts = SimpleNamespace(
        target=SimpleNamespace(
            kind="asset_batch",
            asset_id="asset-1",
            asset_type="storyboard",
            chapter_id="chapter-1",
        ),
        effective_parameters={"size": "1024x1024"},
        model=SimpleNamespace(display_name="Selected model"),
    )
    target = SimpleNamespace(
        protected_values=MappingProxyType(
            {
                "asset": MappingProxyType(
                    {"name": "locked-name", "title": "locked-title"}
                )
            }
        )
    )

    projection = _legacy_message_projection(
        operation=AdmissionOperation.IMAGE_BATCH.value,
        raw_item={
            "description": "raw description",
            "name": "",
            "model_id": "selected-model-id",
            "prompt_id": "selected-prompt-id",
        },
        facts=facts,
        target=target,
        references=MappingProxyType({"reference_images": ("one", "one", "图")}),
    )

    assert projection is not None
    assert projection.user_content == (
        '__CTX__:{"modelId": "selected-model-id", "promptId": "selected-prompt-id", '
        '"size": "1024x1024", "refImagesCount": 3, '
        '"refImagesDigest": "2297512eba635d6b8fd2ad5f11d0c47c4523bb4f2f2a6d35ab3e15e8715d095c"}'
        "\nraw description"
    )
    assert projection.asset_name == "locked-name"
    assert projection.assistant_content == "正在生成图片..."
    assert projection.assistant_wire_content is None
    assert projection.model_name == "Selected model"


def test_batch_message_does_not_fall_back_to_locked_name_for_raw_description() -> None:
    facts = SimpleNamespace(
        target=SimpleNamespace(
            kind="asset_batch",
            asset_id="asset-1",
            asset_type="storyboard",
            chapter_id="chapter-1",
        ),
        effective_parameters={"size": "1024x1024"},
        model=SimpleNamespace(display_name="Selected model"),
    )
    target = SimpleNamespace(
        protected_values={"asset": {"name": "locked-name"}}
    )

    with pytest.raises(_UnsafeSnapshot, match="batch_description_missing"):
        _legacy_message_projection(
            operation=AdmissionOperation.IMAGE_BATCH.value,
            raw_item={"description": "", "name": "", "model_id": "m"},
            facts=facts,
            target=target,
            references=MappingProxyType({"reference_images": ()}),
        )


def test_locked_target_datetime_projection_does_not_expand_model_json_values() -> None:
    target_values = _freeze_mapping(
        {"chapter": {"updated_at": FIXTURE_NOW}},
        invalid_code="protected_value_invalid",
        allow_datetime=True,
    )

    assert _plain(target_values) == {
        "chapter": {"updated_at": FIXTURE_NOW.isoformat()},
    }
    with pytest.raises(_UnsafeSnapshot, match="model_params_invalid"):
        _parse_params({"updated_at": FIXTURE_NOW})


def test_server_submission_id_is_length_checked_without_uuid_only_rules() -> None:
    assert _server_identifier("submission-1", maximum_length=36) == "submission-1"
    with pytest.raises(_UnsafeSnapshot, match="server_id_unavailable"):
        _server_identifier("x" * 37, maximum_length=36)


def test_request_digest_uses_a2_fingerprint_and_rule_digest_binds_policy_and_all_parameters() -> None:
    fingerprint = _fingerprint()
    context = SimpleNamespace(
        actor=SimpleNamespace(user_id="account-1"),
        operation=AdmissionOperation.IMAGE_SINGLE.value,
        fingerprint=fingerprint,
    )
    preparation = SimpleNamespace(phase="initial")
    item = SimpleNamespace(
        index=0,
        facts=SimpleNamespace(
            quote_amount=3,
            prompt_digest="prompt-digest",
            effective_parameters={"size": "1024x1024", "quality": "high"},
        ),
        model=SimpleNamespace(
            selection=SimpleNamespace(
                source=SimpleNamespace(value="system"),
                catalog_id="model-1",
                category=SimpleNamespace(value="image"),
            ),
            provider="fixture-provider",
            wire_model="wire-model-1",
            params={"quality": "raw", "size": "raw-size"},
        ),
        prompt_ref="prompt-1",
        target=SimpleNamespace(
            snapshot=SimpleNamespace(
                kind="standalone",
                chapter_id=None,
                series_id=None,
                stable_storyboard_asset_id=None,
                asset_type=None,
                asset_id=None,
                compat_frame_index=None,
                expected_text_digest=None,
                media_identity=None,
                content_digest=None,
                notes_revision=None,
                full_frame_digest=None,
                full_chapter_digest=None,
                expected_asset_image_url=None,
                same_series_target_digest=None,
            )
        ),
    )
    versions = SnapshotVersions(projector="projector-v1", reference_policy="refs-v1")
    policy = AdmissionPolicy(max_concurrent_tasks=4)

    request_digest = _context_fingerprint_digest(context)
    first = _rule_digest(
        context=context,
        item=item,
        preparation=preparation,
        reference_count=0,
        reference_digest=_reference_image_metadata(())[1],
        versions=versions,
        policy=policy,
        graph_version="image-single-v1",
    )
    changed_policy = _rule_digest(
        context=context,
        item=item,
        preparation=preparation,
        reference_count=0,
        reference_digest=_reference_image_metadata(())[1],
        versions=versions,
        policy=AdmissionPolicy(max_concurrent_tasks=5),
        graph_version="image-single-v1",
    )
    changed_parameters_item = SimpleNamespace(
        **{
            **vars(item),
            "facts": SimpleNamespace(
                quote_amount=3,
                prompt_digest="prompt-digest",
                effective_parameters={"size": "1024x1024", "quality": "standard"},
            ),
        }
    )
    changed_parameters = _rule_digest(
        context=context,
        item=changed_parameters_item,
        preparation=preparation,
        reference_count=0,
        reference_digest=_reference_image_metadata(())[1],
        versions=versions,
        policy=policy,
        graph_version="image-single-v1",
    )
    changed_graph = _rule_digest(
        context=context,
        item=item,
        preparation=preparation,
        reference_count=0,
        reference_digest=_reference_image_metadata(())[1],
        versions=versions,
        policy=policy,
        graph_version="image-batch-v1",
    )

    assert request_digest == fingerprint.ordered_nonsecret_payload_digest
    assert _admission_policy_value(policy) == {
        "version": "generation-admission-policy-v1",
        "max_concurrent_tasks": 4,
    }
    assert first != changed_policy
    assert first != changed_parameters
    assert first != changed_graph


def test_graph_version_uses_fixed_operation_and_locked_provider_policy() -> None:
    assert _graph_version(AdmissionOperation.IMAGE_SINGLE.value, "untrusted-alias") == "image-single-v1"
    assert _graph_version(AdmissionOperation.IMAGE_BATCH.value, "untrusted-alias") == "image-batch-v1"
    assert _graph_version(AdmissionOperation.VIDEO_SINGLE.value, "haoai") == "video-haoai-attempt-group-v1"
    assert _graph_version(AdmissionOperation.VIDEO_SINGLE.value, "HAOAI") == "video-haoai-attempt-group-v1"
    assert _graph_version(AdmissionOperation.VIDEO_SINGLE.value, "fixture-provider") == "video-single-v1"


def test_upstream_model_name_uses_locked_wire_value_or_fixed_legacy_default() -> None:
    image = AdmissionOperation.IMAGE_SINGLE.value
    image_batch = AdmissionOperation.IMAGE_BATCH.value
    video = AdmissionOperation.VIDEO_SINGLE.value

    assert _snapshot_model_name(image, "OpenAI", "locked-wire-v2") == "locked-wire-v2"
    assert _snapshot_model_name(image, "openai", None) == "dall-e-3"
    assert _snapshot_model_name(image_batch, "OPENAI", "") == "dall-e-3"
    assert _snapshot_model_name(video, "HAOAI", None) == "grok-imagine-video-1.5-preview"
    assert _snapshot_model_name(video, "manxiaobai", None) == "grok-video-1.0"
    assert _snapshot_model_name(video, "xai", None) == "grok-imagine-video"
    with pytest.raises(_UnsafeSnapshot, match="wire_model_unavailable"):
        _snapshot_model_name(image, "openai ", None)
    with pytest.raises(_UnsafeSnapshot, match="wire_model_unavailable"):
        _snapshot_model_name(video, "unknown-provider", None)


def test_snapshot_model_parameters_merge_raw_and_effective_values() -> None:
    item = SimpleNamespace(
        model=SimpleNamespace(params={"quality": "raw", "size": "raw-size"}),
        facts=SimpleNamespace(
            effective_parameters={"size": "effective-size", "duration": 5}
        ),
    )

    assert dict(_snapshot_model_parameters(item)) == {
        "quality": "raw",
        "size": "effective-size",
        "duration": 5,
    }


class _FixedTokenCodec:
    def __init__(self, user_id: str) -> None:
        self._claims = {"sub": user_id}

    def encode(self, claims: object) -> str:
        raise AssertionError("fixture never issues tokens")

    def decode(self, token: str) -> dict[str, str]:
        assert token == "fixture-token"
        return dict(self._claims)


class _AuthenticationSession(Session):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.closed_by_authentication = False

    def close(self) -> None:
        self.closed_by_authentication = True
        super().close()


class _CallerSession(Session):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0

    def commit(self) -> None:
        self.commit_calls += 1
        super().commit()

    def rollback(self) -> None:
        self.rollback_calls += 1
        super().rollback()

    def close(self) -> None:
        self.close_calls += 1
        super().close()


class _CommitThenUnknownSession(_CallerSession):
    def commit(self) -> None:
        super().commit()
        raise RuntimeError("fixture commit acknowledgement was lost")


def _sqlite_schema(engine: Engine) -> None:
    def enable_foreign_keys(connection, _record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    event.listen(engine, "connect", enable_foreign_keys)
    authentication_metadata.create_all(engine)
    series_access_metadata.create_all(engine)
    catalog_metadata.create_all(engine)
    Base.metadata.create_all(engine)
    credential_metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            generation_schema_migrations.insert().values(
                version=GENERATION_SCHEMA_VERSION,
                checksum=GENERATION_SCHEMA_CHECKSUM,
                applied_at=FIXTURE_NOW.replace(tzinfo=None),
            )
        )
        for trigger_name, table_name in SQLITE_GUARD_TABLES.items():
            action = "DELETE" if trigger_name.endswith("_delete") else "UPDATE"
            connection.exec_driver_sql(
                f"CREATE TRIGGER {trigger_name} BEFORE {action} ON {table_name} "
                "BEGIN SELECT RAISE(ABORT, 'immutable'); END"
            )
    migration_directory = Path(__file__).resolve().parents[2] / "migrations" / "credential_registry"
    manifest = json.loads((migration_directory / "manifest.json").read_text(encoding="utf-8"))
    migration_sql = (migration_directory / CREDENTIAL_MIGRATION_FILENAME).read_bytes()
    assert hashlib.sha256(migration_sql).hexdigest() == manifest["sha256"]
    assert manifest["version"] == CREDENTIAL_SCHEMA_VERSION
    with engine.begin() as connection:
        connection.execute(
            credential_schema_migrations.insert().values(
                version=CREDENTIAL_SCHEMA_VERSION,
                checksum=manifest["sha256"],
                applied_at=FIXTURE_NOW.replace(tzinfo=None),
            )
        )


@contextmanager
def _allow_sqlite_binding_update_for_fixture(engine: Engine) -> Iterator[None]:
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "DROP TRIGGER trg_generation_credential_bindings_guard"
        )
    try:
        yield
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TRIGGER trg_generation_credential_bindings_guard "
                "BEFORE UPDATE ON generation_credential_bindings "
                "BEGIN SELECT RAISE(ABORT, 'immutable'); END"
            )


def _insert_user(
    connection,
    user_id: str,
    *,
    membership: str = "premium",
    expires_at: datetime | None = None,
) -> None:
    stamp = FIXTURE_NOW.replace(tzinfo=None)
    connection.execute(
        insert(users).values(
            id=user_id,
            username=f"{user_id}-name",
            email=f"{user_id}@example.test",
            hashed_password="test-only-hash",
            is_superuser=False,
            membership_type=membership,
            membership_expires_at=expires_at,
            created_at=stamp,
        )
    )
    connection.execute(
        insert(user_credits).values(
            id=f"wallet-{user_id}",
            user_id=user_id,
            credits=500,
            created_at=stamp,
            updated_at=stamp,
        )
    )


def _insert_model(connection, *, model_id: str, owner_id: str) -> None:
    stamp = FIXTURE_NOW.replace(tzinfo=None)
    connection.execute(
        insert(model_configs).values(
            id=model_id,
            user_id=owner_id,
            category="image",
            name=f"Public {model_id}",
            provider="fixture-provider",
            api_key=PROVIDER_SECRET,
            model=f"wire-{model_id}",
            base_url="https://provider.example.test/v2",
            sort_order=0,
            params=json.dumps({"quality": "high", "size": "1024x1024"}),
            created_at=stamp,
            updated_at=stamp,
        )
    )


def _insert_batch_targets(connection) -> None:
    stamp = FIXTURE_NOW.replace(tzinfo=None)
    connection.execute(
        insert(series).values(
            id="series-1",
            user_id=ACTOR_ID,
            team_id=None,
            claimed_by=None,
        )
    )
    connection.execute(
        insert(chapters).values(
            id="chapter-1",
            series_id="series-1",
            title="Fixture chapter",
            content="[{}, {}]",
            **{"order": 1},
            created_at=stamp,
            updated_at=stamp,
        )
    )
    for asset_type, asset_id, display_name in (
        ("character", "character-1", "Locked Character Name"),
        ("scene", "scene-1", "Locked Scene Title"),
    ):
        values: dict[str, object] = {
            "id": asset_id,
            "series_id": "series-1",
            "description": f"Locked {asset_id}",
            "image_url": None,
            "updated_at": stamp,
        }
        values["title" if asset_type == "scene" else "name"] = display_name
        connection.execute(insert(ASSET_TABLES[asset_type]).values(**values))


def _new_database(
    tmp_path: Path,
    *,
    membership: str = "premium",
    expires_at: datetime | None = None,
    actor_model: bool = True,
    foreign_model: bool = False,
) -> Engine:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'producer.sqlite'}")
    _sqlite_schema(engine)
    with engine.begin() as connection:
        _insert_user(connection, ACTOR_ID, membership=membership, expires_at=expires_at)
        _insert_user(connection, FOREIGN_ACTOR_ID, membership="premium")
        if actor_model:
            _insert_model(connection, model_id=MODEL_ID, owner_id=ACTOR_ID)
        if foreign_model:
            _insert_model(connection, model_id="foreign-model", owner_id=FOREIGN_ACTOR_ID)
        _insert_batch_targets(connection)
    return engine


def _authentication_service(engine: Engine) -> tuple[AuthenticationService, list[_AuthenticationSession]]:
    sessions: list[_AuthenticationSession] = []

    def unit_of_work() -> SqlAlchemyAuthenticationUnitOfWork:
        session = _AuthenticationSession(bind=engine, expire_on_commit=False)
        sessions.append(session)
        return SqlAlchemyAuthenticationUnitOfWork(session)

    runtime = AuthenticationRuntime(
        session_factory=lambda: None,
        secret_key="fixture-only-secret",
        token_codec=_FixedTokenCodec(ACTOR_ID),
        clock=lambda: FIXTURE_NOW,
        epoch=lambda: FIXTURE_NOW.timestamp(),
        id_factory=lambda: "unused-auth-session-id",
    )
    return AuthenticationService(runtime, unit_of_work), sessions


def _caller_session(engine: Engine) -> _CallerSession:
    return _CallerSession(bind=engine, autoflush=True, expire_on_commit=False)


def _registered_capabilities(
    engine: Engine,
) -> tuple[RegistryCapabilities, dict[str, object]]:
    authority = HmacProofAuthority("fixture-root-v1", b"r" * 32)
    crypto, crypto_state = _fixture_crypto({"vault://fixture/provider": PROVIDER_SECRET})
    scope = CredentialScope("user", MODEL_ID, ACTOR_ID, "fixture-provider")
    unsigned = ProtectedAccountRecord(
        record_id="fixture-record-1",
        principal_id=PRINCIPAL_ID,
        scope=scope,
        issuer_namespace="fixture-provider-account-v1",
        stable_account_subject="fixture-account-subject",
        key_generation="generation-1",
        material_ref="vault://fixture/provider",
        evidence_ref="fixture://evidence/fixture-record-1",
        evidence_version="fixture-evidence-v1",
        valid_from=FIXTURE_NOW - timedelta(days=1),
        valid_until=FIXTURE_NOW + timedelta(days=30),
        revoked_at=None,
        endpoint_policy=EndpointPolicy.from_base_url(
            "https://provider.example.test/v2",
            allowed_content_paths=("/v1/files/{file_id}",),
        ),
        guard_key_id=crypto.guard_key_id,
        secret_consistency_guard="0" * 64,
        proof_key_id=authority.key_id,
        proof_signature="0" * 64,
    )
    guard = crypto.guard(PROVIDER_SECRET, account_guard_associated_data(unsigned))
    assert guard is not None
    guarded = replace(unsigned, secret_consistency_guard=guard)
    record = replace(
        guarded,
        proof_signature=authority.sign("root-enrollment-v1", guarded.proof_payload()),
    )
    source = RootEnrollmentSource((record,), authority)
    session_factory = sessionmaker(engine, expire_on_commit=False, autoflush=True)
    registry_id_counter = 0

    def registry_id() -> str:
        nonlocal registry_id_counter
        registry_id_counter += 1
        return f"40000000-0000-4000-8000-{registry_id_counter:012x}"

    capabilities = RegistryCapabilities(
        proof_authority=authority,
        enrollment_source=source,
        crypto=crypto,
        read_session_factory=session_factory,
        db_clock=lambda: FIXTURE_NOW,
        id_factory=registry_id,
    )
    service = build_credential_registry(capabilities)
    attempt = make_server_attempt(
        authority,
        attempt_id="fixture-registration-1",
        actor_id=ACTOR_ID,
        operation="register",
        scope=scope,
        key_generation=record.key_generation,
        protected_record_id=record.record_id,
    )
    with session_factory() as session:
        staged = service.stage_register_owned_config(
            session,
            TrustedActor(ACTOR_ID),
            MODEL_ID,
            attempt,
        )
        assert isinstance(staged, RegistrationStaged)
        session.commit()
    captured = crypto_state["plaintexts"]
    assert isinstance(captured, list)
    captured.clear()
    return capabilities, crypto_state


def _fixture_crypto(
    secrets_by_reference: dict[str, str],
) -> tuple[CredentialCrypto, dict[str, object]]:
    encryption_key = b"producer-fixture-encryption-key"
    guard_key = b"producer-fixture-consistency-key"
    state: dict[str, object] = {
        "plaintexts": [],
        "unseal_calls": 0,
        "secrets_by_reference": secrets_by_reference,
    }

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
        encrypted = bytes(
            left ^ right
            for left, right in zip(plaintext, stream(nonce, associated_data, len(plaintext)), strict=True)
        )
        tag = hmac.new(
            encryption_key,
            b"fixture-tag" + nonce + associated_data + encrypted,
            hashlib.sha256,
        ).digest()
        cast_plaintexts = state["plaintexts"]
        assert isinstance(cast_plaintexts, list)
        cast_plaintexts.append(secret)
        return SealedMaterial(tag + encrypted, nonce, "fixture-seal-v1", "fixture-aead-v1")

    def unseal(material: SealedMaterial, associated_data: bytes) -> str | None:
        state["unseal_calls"] = int(state["unseal_calls"]) + 1
        if material.key_id != "fixture-seal-v1" or len(material.ciphertext) < 32:
            return None
        tag, encrypted = material.ciphertext[:32], material.ciphertext[32:]
        expected = hmac.new(
            encryption_key,
            b"fixture-tag" + material.nonce + associated_data + encrypted,
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(tag, expected):
            return None
        plaintext = bytes(
            left ^ right
            for left, right in zip(
                encrypted,
                stream(material.nonce, associated_data, len(encrypted)),
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

    crypto = CredentialCrypto(
        seal_key_id="fixture-seal-v1",
        guard_key_id="fixture-guard-v1",
        algorithm_version="fixture-aead-v1",
        seal=seal,
        unseal=unseal,
        consistency_guard=guard,
        resolve_secret_handle=secrets_by_reference.get,
        resolve_legacy_config_value=lambda stored: stored or None,
        self_test=lambda: True,
    )
    return crypto, state


def _producer_for(
    authentication: AuthenticationService,
    capabilities: RegistryCapabilities,
    *,
    operation: str = AdmissionOperation.IMAGE_BATCH.value,
) -> MediaSnapshotProducer:
    counter = 0

    def server_id() -> str:
        nonlocal counter
        counter += 1
        return f"producer-id-{counter}"

    return build_media_snapshot_producer(
        authentication,
        capabilities,
        server_id_factory=server_id,
        policies={operation: AdmissionPolicy(max_concurrent_tasks=0)},
        versions=SnapshotVersions(projector="projector-v1", reference_policy="refs-v1"),
    )


def _batch_request(*, second_model_id: str = MODEL_ID) -> dict[str, object]:
    return {
        "chapter_id": "chapter-1",
        "assets": [
            {
                "type": "character",
                "id": "character-1",
                "name": "",
                "description": "First raw description",
                "model_id": MODEL_ID,
                "prompt_id": "",
                "size": "1024x1024",
                "reference_images": ["https://ref.example.test/a?token=one"] * 2,
            },
            {
                "type": "scene",
                "id": "scene-1",
                "name": "Scene display alias",
                "description": "",
                "model_id": second_model_id,
                "prompt_id": "",
                "size": "1024x1024",
                "reference_images": ["https://ref.example.test/b?token=two"],
            },
        ],
    }


def _expected_reference_digest(values: tuple[str, ...]) -> str:
    payload = json.dumps(list(values), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _assert_a2_uses_same_caller_after_authentication(
    monkeypatch: pytest.MonkeyPatch,
    *,
    caller: _CallerSession,
    authentication_sessions: list[_AuthenticationSession],
) -> None:
    real_prepare = prepare_admission

    def observed_prepare(session: Session, context: object, *, policy: object) -> object:
        assert session is caller
        assert session.autoflush is False
        assert authentication_sessions
        assert authentication_sessions[-1].closed_by_authentication
        return real_prepare(session, context, policy=policy)

    monkeypatch.setattr(
        "haoai_backend.generation_credentials.snapshot_producer.prepare_admission",
        observed_prepare,
    )


def _single_request(model_id: str = MODEL_ID) -> dict[str, object]:
    return {
        "model_id": model_id,
        "prompt": "A quiet room",
        "target": {"kind": "standalone"},
        "size": None,
        "reference_images": ["https://ref.example.test/single?ordered=1"],
    }


def _canonical_json_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def test_real_frame_message_accepts_stable_storyboard_after_first_reference(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> None:
    engine = _new_database(tmp_path)
    request.addfinalizer(engine.dispose)
    raw_chapter = (
        '[ { "storyboard" : ["legacy-reference", "storyboard-1"], '
        '"text" : "保留普通消息目标" } ]'
    )
    raw_digest = hashlib.sha256(raw_chapter.encode("utf-8")).hexdigest()
    with engine.begin() as connection:
        connection.execute(
            update(chapters)
            .where(chapters.c.id == "chapter-1")
            .values(content=raw_chapter)
        )
        connection.execute(
            insert(ASSET_TABLES["storyboard"]).values(
                id="storyboard-1",
                series_id="series-1",
                chapter_id="chapter-1",
                frame_index=0,
                name="Frame one",
                description="Fixture storyboard frame",
                image_url=None,
                updated_at=FIXTURE_NOW.replace(tzinfo=None),
            )
        )

    capabilities, crypto_state = _registered_capabilities(engine)
    authentication, authentication_sessions = _authentication_service(engine)
    producer = _producer_for(
        authentication,
        capabilities,
        operation=AdmissionOperation.IMAGE_SINGLE.value,
    )
    producer_callers: list[Session] = []
    observed_admissions: list[object] = []
    real_prepare = prepare_admission

    def observe_prepare(session: Session, context: object, *, policy: object) -> object:
        assert producer_callers and producer_callers[-1] is session
        assert authentication_sessions[-1].closed_by_authentication
        result = real_prepare(session, context, policy=policy)
        observed_admissions.append(result)
        return result

    monkeypatch.setattr(
        "haoai_backend.generation_credentials.snapshot_producer.prepare_admission",
        observe_prepare,
    )
    message_request = _single_request()
    message_request["target"] = {
        "kind": "frame_message",
        "chapter_id": "chapter-1",
        "storyboard_asset_id": "storyboard-1",
    }
    caller = _caller_session(engine)
    producer_callers.append(caller)
    try:
        staged = producer.prepare_and_stage(
            caller,
            authorization="Bearer fixture-token",
            operation=AdmissionOperation.IMAGE_SINGLE.value,
            raw_key="frame-message-nonleading-storyboard-reference",
            request=message_request,
        )

        assert len(observed_admissions) == 1
        admission = observed_admissions[0]
        assert isinstance(admission, AdmissionPreparationFacts)
        locked_target = admission.items[0].target
        assert locked_target.kind == "frame_message"
        assert locked_target.asset_id == "storyboard-1"
        assert locked_target.frame_index == 0
        assert locked_target.chapter_digest == raw_digest

        assert isinstance(staged, SnapshotPayloadsStaged)
        item = staged.items[0]
        target = item.snapshot.target_snapshot
        assert target.kind == "frame_message"
        assert target.stable_storyboard_asset_id == "storyboard-1"
        assert target.compat_frame_index == 0
        assert target.full_frame_digest is None
        assert target.full_chapter_digest == raw_digest
        assert item.request_data_projection.target_snapshot == target

        plaintexts = crypto_state["plaintexts"]
        assert isinstance(plaintexts, list) and plaintexts
        protected = json.loads(plaintexts[-1])
        assert protected["item"]["target"]["chapter"]["content"] == raw_chapter
        assert protected["item"]["target"]["chapter_digest"] == raw_digest
    finally:
        caller.rollback()
        caller.close()


def test_real_canvas_frame_snapshot_freezes_canonical_target_and_raw_chapter_facts(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> None:
    engine = _new_database(tmp_path)
    request.addfinalizer(engine.dispose)
    stamp = FIXTURE_NOW.replace(tzinfo=None)
    target_frame = {
        "unknown": {"nested": ["雪夜", {"enabled": True, "镜头": 2}]},
        "text": "旧画面",
        "storyboard": ["storyboard-1"],
    }
    sibling_frame = {"storyboard": ["storyboard-2"], "text": "雨天"}
    frames = [target_frame, sibling_frame]
    raw_chapter = (
        '[ { "unknown" : {"nested" : ["雪夜", {"enabled": true, "镜头": 2}]}, '
        '"text" : "旧画面", "storyboard" : ["storyboard-1"] }, '
        '{ "storyboard" : ["storyboard-2"], "text" : "雨天" } ]'
    )
    with engine.begin() as connection:
        connection.execute(
            update(chapters)
            .where(chapters.c.id == "chapter-1")
            .values(content=raw_chapter)
        )
        connection.execute(
            insert(ASSET_TABLES["storyboard"]).values(
                id="storyboard-1",
                series_id="series-1",
                chapter_id="chapter-1",
                frame_index=0,
                name="Frame one",
                description="Fixture storyboard frame",
                image_url=None,
                updated_at=stamp,
            )
        )
        connection.execute(
            insert(ASSET_TABLES["storyboard"]).values(
                id="storyboard-2",
                series_id="series-1",
                chapter_id="chapter-1",
                frame_index=1,
                name="Frame two",
                description="Fixture sibling frame",
                image_url=None,
                updated_at=stamp,
            )
        )

    capabilities, crypto_state = _registered_capabilities(engine)
    authentication, authentication_sessions = _authentication_service(engine)
    producer = _producer_for(
        authentication,
        capabilities,
        operation=AdmissionOperation.IMAGE_SINGLE.value,
    )
    producer_callers: list[Session] = []
    real_prepare = prepare_admission

    def observe_prepare(session: Session, context: object, *, policy: object) -> object:
        assert producer_callers and producer_callers[-1] is session
        assert authentication_sessions[-1].closed_by_authentication
        return real_prepare(session, context, policy=policy)

    monkeypatch.setattr(
        "haoai_backend.generation_credentials.snapshot_producer.prepare_admission",
        observe_prepare,
    )
    canvas_request = _single_request()
    canvas_request["target"] = {
        "kind": "canvas_apply",
        "destination": {
            "kind": "frame_preview",
            "chapter_id": "chapter-1",
            "storyboard_asset_id": "storyboard-1",
        },
    }

    def produce(
        raw_key: str,
        content: str,
    ) -> tuple[TargetSnapshot, LockedTargetFacts, dict[str, object]]:
        with engine.begin() as connection:
            connection.execute(
                update(chapters)
                .where(chapters.c.id == "chapter-1")
                .values(content=content)
            )
        caller = _caller_session(engine)
        producer_callers.append(caller)
        try:
            staged = producer.prepare_and_stage(
                caller,
                authorization="Bearer fixture-token",
                operation=AdmissionOperation.IMAGE_SINGLE.value,
                raw_key=raw_key,
                request=canvas_request,
            )
            assert isinstance(staged, SnapshotPayloadsStaged)
            assert len(staged.preparation.items) == 1
            item = staged.items[0]
            assert item.snapshot.target_snapshot.kind == "canvas_frame_preview"
            assert item.request_data_projection.target_snapshot == item.snapshot.target_snapshot

            plaintexts = crypto_state["plaintexts"]
            assert isinstance(plaintexts, list) and plaintexts
            protected = json.loads(plaintexts[-1])
            return (
                item.snapshot.target_snapshot,
                staged.preparation.items[0].target,
                protected["item"]["target"],
            )
        finally:
            caller.rollback()
            caller.close()

    raw_digest = hashlib.sha256(raw_chapter.encode("utf-8")).hexdigest()
    first_target, first_facts, first_protected = produce("canvas-frame-original", raw_chapter)
    assert first_target.full_frame_digest == _canonical_json_digest(target_frame)
    assert first_target.full_chapter_digest == _canonical_json_digest(frames)
    assert first_target.full_chapter_digest != raw_digest
    assert first_facts.chapter_digest == raw_digest
    assert first_protected["chapter"]["content"] == raw_chapter
    assert first_protected["chapter_digest"] == raw_digest

    reordered_frames = [sibling_frame, target_frame]
    reordered_raw = json.dumps(reordered_frames, ensure_ascii=False, indent=2)
    reordered_target, reordered_facts, reordered_protected = produce(
        "canvas-frame-reordered",
        reordered_raw,
    )
    assert reordered_target.compat_frame_index == 1
    assert reordered_target.full_frame_digest == first_target.full_frame_digest
    assert reordered_target.full_chapter_digest == _canonical_json_digest(
        reordered_frames
    )
    assert reordered_target.full_chapter_digest != first_target.full_chapter_digest
    assert reordered_facts.chapter_digest == hashlib.sha256(
        reordered_raw.encode("utf-8")
    ).hexdigest()
    assert reordered_protected["chapter"]["content"] == reordered_raw

    manually_changed_frame = {
        **target_frame,
        "text": "人工改稿",
    }
    manually_changed_frames = [manually_changed_frame, sibling_frame]
    manually_changed_raw = json.dumps(
        manually_changed_frames,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    changed_target, _changed_facts, changed_protected = produce(
        "canvas-frame-manual-edit",
        manually_changed_raw,
    )
    assert changed_target.full_frame_digest == _canonical_json_digest(manually_changed_frame)
    assert changed_target.full_frame_digest != first_target.full_frame_digest
    assert changed_protected["chapter"]["content"] == manually_changed_raw


def _insert_original_single_receipt(
    engine: Engine,
    *,
    raw_key: str,
    request_digest: str,
) -> None:
    stamp = FIXTURE_NOW.replace(tzinfo=None)
    with engine.begin() as connection:
        connection.execute(
            insert(TaskSubmissionRow.__table__).values(
                id="original-submission",
                user_id=ACTOR_ID,
                operation=AdmissionOperation.IMAGE_SINGLE.value,
                idempotency_key=raw_key,
                request_digest=request_digest,
                task_ids=["original-task"],
                status="accepted",
                created_at=stamp,
            )
        )
        connection.execute(
            insert(AITaskRow.__table__).values(
                id="original-task",
                user_id=ACTOR_ID,
                type="image",
                status="completed",
                claimed_by=None,
                lease_until=None,
                execution_generation=0,
                claim_token=None,
                recovery_status="ready",
                billing_status="charged",
                user_cancelled_at=None,
                cancellation_reason=None,
                message_id="original-message",
                credit_cost=8,
                result=None,
                request_data=None,
                model_name="Original selected model",
                progress=100,
                progress_message=None,
                external_task_id=None,
                external_provider=None,
                created_at=stamp,
                updated_at=stamp,
            )
        )
        connection.execute(
            insert(BillingUnitRow.__table__).values(
                id="original-billing-unit",
                task_id="original-task",
                item_key="single",
                operation=AdmissionOperation.IMAGE_SINGLE.value,
                step_graph_version="legacy",
                input_digest=request_digest,
                quoted_amount=8,
                debited_amount=8,
                refunded_amount=0,
                billing_status="charged",
                result_status="succeeded",
                first_intent_at=None,
                deadline_at=None,
                success_confirmed_at=stamp,
                user_cancelled_at=None,
                cancellation_reason=None,
                quote_id=None,
                quote_unit_key=None,
                candidate_digest=None,
                created_at=stamp,
                updated_at=stamp,
            )
        )


def test_actual_auth_and_a2_replay_precede_expired_membership_and_registry_checks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = _new_database(
        tmp_path,
        membership="free",
        expires_at=datetime(2000, 1, 1),
    )
    request = _single_request()
    raw_key = " original request key "
    context = build_admission_context(
        GenerationActor(ACTOR_ID, TrustedActor(ACTOR_ID)),
        AdmissionOperation.IMAGE_SINGLE,
        request,
        raw_key=raw_key,
    )
    assert context.fingerprint is not None
    _insert_original_single_receipt(
        engine,
        raw_key=raw_key,
        request_digest=context.fingerprint.ordered_nonsecret_payload_digest,
    )
    authentication, auth_sessions = _authentication_service(engine)
    capabilities, crypto_state = _registered_capabilities(engine)
    crypto_state["unseal_calls"] = 0
    producer = _producer_for(
        authentication,
        capabilities,
        operation=AdmissionOperation.IMAGE_SINGLE.value,
    )
    caller = _caller_session(engine)
    _assert_a2_uses_same_caller_after_authentication(
        monkeypatch,
        caller=caller,
        authentication_sessions=auth_sessions,
    )
    statements: list[str] = []

    def capture_sql(_connection, _cursor, statement, _parameters, _context, _executemany) -> None:
        statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", capture_sql)
    try:
        result = producer.prepare_and_stage(
            caller,
            authorization="Bearer fixture-token",
            operation=AdmissionOperation.IMAGE_SINGLE.value,
            raw_key=raw_key,
            request=request,
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture_sql)

    assert isinstance(result, AdmissionReplay)
    assert result.receipt.task_id == "original-task"
    assert crypto_state["unseal_calls"] == 0
    assert auth_sessions[-1].closed_by_authentication
    assert caller.in_transaction()
    assert caller.commit_calls == caller.rollback_calls == caller.close_calls == 0
    assert not any(
        name in statement
        for name in (
            "model_configs",
            "system_models",
            "user_credits",
            "generation_credential_bindings",
            "generation_credential_versions",
            "generation_prepared_payloads",
            "sqlite_master",
        )
        for statement in statements
    )
    caller.rollback()
    caller.close()
    engine.dispose()


def test_actual_batch_stages_all_items_in_caller_session_with_safe_projections(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = _new_database(tmp_path)
    capabilities, crypto_state = _registered_capabilities(engine)
    authentication, auth_sessions = _authentication_service(engine)
    producer = _producer_for(authentication, capabilities)
    caller = _caller_session(engine)
    mapped = orm_registry()

    class PendingUser:
        pass

    mapped.map_imperatively(PendingUser, users)
    pending = PendingUser(
        id="pending-user",
        username="pending-user-name",
        email="pending-user@example.test",
        hashed_password="fixture-only",
        is_superuser=False,
        membership_type="free",
        created_at=FIXTURE_NOW.replace(tzinfo=None),
    )
    caller.add(pending)
    _assert_a2_uses_same_caller_after_authentication(
        monkeypatch,
        caller=caller,
        authentication_sessions=auth_sessions,
    )
    request = _batch_request()
    raw_key = "batch-original-key"
    expected_context = build_admission_context(
        GenerationActor(ACTOR_ID, TrustedActor(ACTOR_ID)),
        AdmissionOperation.IMAGE_BATCH,
        request,
        raw_key=raw_key,
    )
    assert expected_context.fingerprint is not None

    result = producer.prepare_and_stage(
        caller,
        authorization="Bearer fixture-token",
        operation=AdmissionOperation.IMAGE_BATCH.value,
        raw_key=raw_key,
        request=request,
    )

    assert isinstance(result, SnapshotPayloadsStaged)
    assert result.executable is False
    assert result.parent_attempt_id == "producer-id-1"
    assert result.actor_id == ACTOR_ID
    assert result.operation == AdmissionOperation.IMAGE_BATCH.value
    assert result.graph_version == "image-batch-v1"
    assert result.fingerprint == expected_context.fingerprint.ordered_nonsecret_payload_digest
    unseal_calls_before_writer_check = crypto_state["unseal_calls"]
    assert producer.verify_staged_payloads(caller, result) == PayloadVerificationResult(
        "unresolved"
    )
    assert crypto_state["unseal_calls"] == unseal_calls_before_writer_check

    shared_connection_reader = Session(
        bind=caller.connection(),
        autoflush=True,
        expire_on_commit=False,
    )
    assert shared_connection_reader is not caller
    assert producer.verify_staged_payloads(
        shared_connection_reader,
        result,
    ) == PayloadVerificationResult("unresolved")
    assert crypto_state["unseal_calls"] == unseal_calls_before_writer_check
    forged_session_reference = replace(
        result,
        staging_session_ref=weakref.ref(shared_connection_reader),
    )
    assert producer.verify_staged_payloads(
        shared_connection_reader, forged_session_reference
    ) == PayloadVerificationResult("unresolved")
    forged_origin = replace(result, _staging_origin_token=object())
    assert producer.verify_staged_payloads(
        shared_connection_reader, forged_origin
    ) == PayloadVerificationResult("unresolved")
    shared_connection_reader.close()

    assert len(result.items) == 2
    assert [item.index for item in result.items] == [0, 1]
    assert [item.server_attempt_id for item in result.items] == [
        "producer-id-1:0",
        "producer-id-1:1",
    ]
    assert caller.autoflush is True
    assert caller.in_transaction()
    assert pending in caller.new
    assert caller.commit_calls == caller.rollback_calls == caller.close_calls == 0
    assert auth_sessions[-1].closed_by_authentication

    first, second = result.items
    first_projection = first.request_data_projection
    second_projection = second.request_data_projection
    first_refs = ("https://ref.example.test/a?token=one",) * 2
    second_refs = ("https://ref.example.test/b?token=two",)
    assert first_projection.request_digest == result.fingerprint
    assert second_projection.request_digest == result.fingerprint
    assert first_projection.reference_images_count == 2
    assert first_projection.reference_images_digest == _expected_reference_digest(first_refs)
    assert second_projection.reference_images_count == 1
    assert second_projection.reference_images_digest == _expected_reference_digest(second_refs)
    assert first_projection.protected_ref == first.protected_ref
    assert first.protected_ref == (
        f"prepared-payload-v1/{len(first.prepared_payload_id)}:{first.prepared_payload_id}"
    )
    assert first.snapshot.reference_snapshot_ref == first.protected_ref + "#references"
    assert first.snapshot.model_ref.model_id == MODEL_ID
    assert first.snapshot.model_name == f"wire-{MODEL_ID}"
    assert dict(first.snapshot.model_params) == {
        "quality": "high",
        "size": "1024x1024",
    }
    assert first.legacy_message_projection is not None
    assert first.legacy_message_projection.model_name == f"Public {MODEL_ID}"
    assert first.legacy_message_projection.user_content.endswith("\nFirst raw description")
    assert first.legacy_message_projection.asset_name == "Locked Character Name"
    assert second.legacy_message_projection is not None
    assert second.legacy_message_projection.user_content.endswith("\nScene display alias")
    assert second.legacy_message_projection.asset_name == "Scene display alias"
    assert all(len(item.request_data_projection.rule_digest) == 64 for item in result.items)

    sealed_plaintexts = crypto_state["plaintexts"]
    assert isinstance(sealed_plaintexts, list)
    assert len(sealed_plaintexts) == 2
    protected = [json.loads(value) for value in sealed_plaintexts]
    assert protected[0]["item"]["references"]["reference_images"] == list(first_refs)
    assert protected[1]["item"]["references"]["reference_images"] == list(second_refs)
    assert protected[0]["item"]["model"]["snapshot_model_name"] == f"wire-{MODEL_ID}"
    assert protected[0]["item"]["model"]["provider_route"] == "fixture-provider"
    assert protected[0]["item"]["model"]["snapshot_parameters"] == {
        "quality": "high",
        "size": "1024x1024",
    }
    assert PROVIDER_SECRET not in "".join(sealed_plaintexts)
    assert protected[0]["item"]["request_data_projection"]["request_digest"] == result.fingerprint
    assert protected[0]["item"]["request_data_projection"]["rule_digest"] == first_projection.rule_digest

    with caller.no_autoflush:
        own_payload_count = caller.execute(
            select(func.count()).select_from(prepared_payloads)
        ).scalar_one()
        assert own_payload_count == 2
        assert caller.execute(select(func.count()).select_from(AITaskRow.__table__)).scalar_one() == 0
        assert caller.execute(select(func.count()).select_from(BillingUnitRow.__table__)).scalar_one() == 0
    with engine.connect() as connection:
        assert connection.execute(select(func.count()).select_from(prepared_payloads)).scalar_one() == 0
        assert connection.execute(select(func.count()).select_from(users).where(users.c.id == "pending-user")).scalar_one() == 0
        assert connection.execute(select(user_credits.c.credits).where(user_credits.c.user_id == ACTOR_ID)).scalar_one() == 500
        assert connection.execute(select(func.count()).select_from(TaskSubmissionRow.__table__)).scalar_one() == 0

    caller.rollback()
    assert caller.rollback_calls == 1
    with engine.connect() as connection:
        assert connection.execute(select(func.count()).select_from(prepared_payloads)).scalar_one() == 0
        assert connection.execute(select(func.count()).select_from(users).where(users.c.id == "pending-user")).scalar_one() == 0
    caller.close()
    engine.dispose()


def test_same_principal_rotations_keep_origin_proof_and_verify_current_version(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = _new_database(tmp_path)
    capabilities, _crypto_state = _registered_capabilities(engine)
    source = capabilities.enrollment_source
    authority = capabilities.proof_authority
    crypto = capabilities.crypto
    assert isinstance(source, RootEnrollmentSource)
    assert authority is not None and crypto is not None
    original = source.records[0]
    scope = original.scope
    secrets_by_reference = _crypto_state["secrets_by_reference"]
    assert isinstance(secrets_by_reference, dict)
    factory = capabilities.read_session_factory
    assert factory is not None

    def signed_rotation_record(
        predecessor: ProtectedAccountRecord,
        *,
        record_id: str,
        key_generation: str,
        material_ref: str,
        secret: str,
    ) -> ProtectedAccountRecord:
        secrets_by_reference[material_ref] = secret
        unsigned = replace(
            predecessor,
            record_id=record_id,
            key_generation=key_generation,
            material_ref=material_ref,
            evidence_ref=f"fixture://evidence/{record_id}",
            evidence_version=f"fixture-evidence-{key_generation}",
            secret_consistency_guard="0" * 64,
            proof_signature="0" * 64,
        )
        guard = crypto.guard(secret, account_guard_associated_data(unsigned))
        assert guard is not None
        guarded = replace(unsigned, secret_consistency_guard=guard)
        return replace(
            guarded,
            proof_signature=authority.sign("root-enrollment-v1", guarded.proof_payload()),
        )

    def signed_attestation(
        predecessor: ProtectedAccountRecord,
        replacement: ProtectedAccountRecord,
        *,
        binding_id: str,
        predecessor_version: int,
        proof_id: str,
    ) -> RotationAttestation:
        unsigned = RotationAttestation(
            proof_id=proof_id,
            original_record_id=predecessor.record_id,
            replacement_record_id=replacement.record_id,
            binding_id=binding_id,
            predecessor_version=predecessor_version,
            proof_key_id=authority.key_id,
            proof_signature="0" * 64,
        )
        return replace(
            unsigned,
            proof_signature=authority.sign("root-rotation-v1", unsigned.proof_payload()),
        )

    with engine.connect() as connection:
        binding_id = connection.execute(
            select(credential_bindings.c.binding_id).where(
                credential_bindings.c.source == scope.source,
                credential_bindings.c.row_id == scope.row_id,
                credential_bindings.c.owner_or_system == scope.owner_or_system,
                credential_bindings.c.provider == scope.provider,
            )
        ).scalar_one()

    current_capabilities = capabilities
    predecessor = original
    for version in (2, 3):
        replacement_record = signed_rotation_record(
            predecessor,
            record_id=f"fixture-record-{version}",
            key_generation=f"generation-{version}",
            material_ref=f"vault://fixture/provider-{version}",
            secret=f"fixture-provider-secret-{version}",
        )
        proof = signed_attestation(
            predecessor,
            replacement_record,
            binding_id=binding_id,
            predecessor_version=version - 1,
            proof_id=f"fixture-rotation-proof-{version}",
        )
        current_source = current_capabilities.enrollment_source
        assert isinstance(current_source, RootEnrollmentSource)
        next_source = RootEnrollmentSource(
            (*current_source.records, replacement_record),
            authority,
            (*current_source.rotation_attestations, proof),
        )
        next_capabilities = replace(current_capabilities, enrollment_source=next_source)
        with engine.begin() as connection:
            connection.execute(
                update(model_configs)
                .where(model_configs.c.id == MODEL_ID)
                .values(api_key=f"fixture-provider-secret-{version}")
            )

        if version == 2:
            attempt = make_server_attempt(
                authority,
                attempt_id="fixture-rotation-no-proof",
                actor_id=ACTOR_ID,
                operation="rotate",
                scope=scope,
                key_generation=replacement_record.key_generation,
                expected_revision=1,
                predecessor_version=1,
                protected_record_id=replacement_record.record_id,
                rotation_proof_ref=proof.proof_id,
            )
            missing_proof_source = RootEnrollmentSource(
                (*current_source.records, replacement_record),
                authority,
            )
            refused_capabilities = replace(
                current_capabilities, enrollment_source=missing_proof_source
            )
            refusal = build_credential_registry(refused_capabilities).stage_rotate_or_replace_config
            with _allow_sqlite_binding_update_for_fixture(engine):
                with factory() as session:
                    denied = refusal(
                        session, TrustedActor(ACTOR_ID), scope, 1, attempt
                    )
                    assert isinstance(denied, RegistryRefusal)
                    assert denied.code == "rotation_proof_unavailable"
                    session.rollback()

        attempt = make_server_attempt(
            authority,
            attempt_id=f"fixture-rotation-{version}",
            actor_id=ACTOR_ID,
            operation="rotate",
            scope=scope,
            key_generation=replacement_record.key_generation,
            expected_revision=version - 1,
            predecessor_version=version - 1,
            protected_record_id=replacement_record.record_id,
            rotation_proof_ref=proof.proof_id,
        )
        with _allow_sqlite_binding_update_for_fixture(engine):
            with factory() as session:
                registered = build_credential_registry(next_capabilities).stage_rotate_or_replace_config(
                    session, TrustedActor(ACTOR_ID), scope, version - 1, attempt
                )
                assert isinstance(registered, RegistrationStaged)
                assert registered.binding_version == version
                session.commit()

        authentication, auth_sessions = _authentication_service(engine)
        producer = _producer_for(authentication, next_capabilities)
        caller = _caller_session(engine)
        _assert_a2_uses_same_caller_after_authentication(
            monkeypatch, caller=caller, authentication_sessions=auth_sessions
        )
        staged = producer.prepare_and_stage(
            caller,
            authorization="Bearer fixture-token",
            operation=AdmissionOperation.IMAGE_BATCH.value,
            raw_key=f"same-principal-rotation-{version}",
            request=_batch_request(),
        )
        assert isinstance(staged, SnapshotPayloadsStaged)
        assert all(
            item.snapshot.credential_binding_ref == f"{binding_id}:v{version}"
            for item in staged.items
        )
        caller.rollback()
        caller.close()

        current_capabilities = next_capabilities
        predecessor = replacement_record

    with engine.connect() as connection:
        principal = connection.execute(
            select(credential_principals).where(
                credential_principals.c.principal_id == PRINCIPAL_ID
            )
        ).mappings().one()
        assert principal["record_id"] == original.record_id
        assert principal["evidence_ref"] == original.evidence_ref
        assert principal["evidence_version"] == original.evidence_version
    engine.dispose()


def test_external_connection_transaction_stays_unresolved_after_session_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = _new_database(tmp_path)
    capabilities, crypto_state = _registered_capabilities(engine)
    authentication, auth_sessions = _authentication_service(engine)
    producer = _producer_for(authentication, capabilities)
    connection = engine.connect()
    external_transaction = connection.begin()
    caller = _CallerSession(
        bind=connection,
        autoflush=True,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    _assert_a2_uses_same_caller_after_authentication(
        monkeypatch,
        caller=caller,
        authentication_sessions=auth_sessions,
    )

    staged = producer.prepare_and_stage(
        caller,
        authorization="Bearer fixture-token",
        operation=AdmissionOperation.IMAGE_BATCH.value,
        raw_key="external-connection-transaction",
        request=_batch_request(),
    )
    assert isinstance(staged, SnapshotPayloadsStaged)
    writer_session_transaction = caller.get_transaction()
    assert writer_session_transaction is not None and writer_session_transaction.is_active
    assert connection.get_transaction() is external_transaction

    caller.commit()
    assert not caller.in_transaction()
    assert not writer_session_transaction.is_active
    assert external_transaction.is_active

    reader = Session(
        bind=connection,
        autoflush=True,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    unseal_calls_before = crypto_state["unseal_calls"]
    assert producer.verify_staged_payloads(reader, staged) == PayloadVerificationResult(
        "unresolved"
    )
    assert crypto_state["unseal_calls"] == unseal_calls_before

    reader.close()
    external_transaction.rollback()
    caller.close()
    connection.close()
    engine.dispose()


def test_unknown_commit_reads_and_verifies_original_payload_without_current_head(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = _new_database(tmp_path)
    capabilities, crypto_state = _registered_capabilities(engine)
    authentication, auth_sessions = _authentication_service(engine)
    producer = _producer_for(authentication, capabilities)
    caller = _CommitThenUnknownSession(bind=engine, autoflush=True, expire_on_commit=False)
    _assert_a2_uses_same_caller_after_authentication(
        monkeypatch,
        caller=caller,
        authentication_sessions=auth_sessions,
    )

    staged = producer.prepare_and_stage(
        caller,
        authorization="Bearer fixture-token",
        operation=AdmissionOperation.IMAGE_BATCH.value,
        raw_key="unknown-commit-payload",
        request=_batch_request(),
    )
    assert isinstance(staged, SnapshotPayloadsStaged)
    with pytest.raises(RuntimeError, match="acknowledgement was lost"):
        caller.commit()
    assert caller.commit_calls == 1
    assert caller.rollback_calls == caller.close_calls == 0

    scope = CredentialScope("user", MODEL_ID, ACTOR_ID, "fixture-provider")
    authority = capabilities.proof_authority
    assert authority is not None
    disable = make_server_attempt(
        authority,
        attempt_id="fixture-disable-after-stage",
        actor_id=ACTOR_ID,
        operation="disable",
        scope=scope,
        key_generation="generation-1",
        expected_revision=1,
        predecessor_version=1,
        protected_record_id="fixture-record-1",
    )
    with _allow_sqlite_binding_update_for_fixture(engine):
        with _caller_session(engine) as change_session:
            outcome = build_credential_registry(capabilities).stage_disable_binding(
                change_session,
                TrustedActor(ACTOR_ID),
                scope,
                1,
                disable,
            )
            assert isinstance(outcome, RegistrationStaged)
            change_session.execute(
                update(model_configs)
                .where(model_configs.c.id == MODEL_ID)
                .values(api_key="changed-after-payload-commit")
            )
            change_session.commit()
    with engine.connect() as connection:
        current = connection.execute(
            select(credential_bindings.c.current_version, credential_bindings.c.enabled)
            .where(credential_bindings.c.row_id == MODEL_ID)
        ).one()
        assert current.current_version == 2
        assert current.enabled is False

    caller.execute(select(func.count()).select_from(users)).scalar_one()
    assert caller.in_transaction()

    read_session = _caller_session(engine)
    # G2 may already have opened a clean read transaction before payload verification.
    read_session.execute(select(func.count()).select_from(TaskSubmissionRow.__table__)).scalar_one()
    statements: list[str] = []

    def capture_sql(_connection, _cursor, statement, _parameters, _context, _executemany) -> None:
        statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", capture_sql)
    crypto_state["unseal_calls"] = 0
    try:
        verified = producer.verify_staged_payloads(read_session, staged)
    finally:
        event.remove(engine, "before_cursor_execute", capture_sql)

    assert verified == PayloadVerificationResult("verified", item_count=2)
    assert staged.executable is False
    assert staged.preparation.executable is False
    assert type(staged.preparation.reserved_amount) is int
    assert staged.preparation.reserved_amount == 0
    assert type(staged.preparation.available_balance) is int
    assert staged.preparation.available_balance == 500

    def with_first_item(**changes):
        changed_first = replace(staged.items[0], **changes)
        return replace(staged, items=(changed_first, *staged.items[1:]))

    first_item = staged.items[0]
    tampered_results = [
        with_first_item(
            snapshot=replace(
                first_item.snapshot,
                server_quote=first_item.snapshot.server_quote + 1,
            )
        ),
        with_first_item(
            snapshot=replace(
                first_item.snapshot,
                effective_prompt=first_item.snapshot.effective_prompt + " changed",
            )
        ),
    ]
    assert first_item.legacy_message_projection is not None
    tampered_results.append(
        with_first_item(
            legacy_message_projection=replace(
                first_item.legacy_message_projection,
                user_content=first_item.legacy_message_projection.user_content + " changed",
            )
        )
    )
    assert first_item.request_data_projection is not None
    for field_name, value in (
        ("model_source", "system"),
        ("model_id", "changed-model"),
        ("prompt_id", "changed-prompt"),
        ("size", "2048x2048"),
        ("asset_id", "changed-asset"),
        ("asset_name", "Changed asset"),
        ("rule_digest", "f" * 64),
        ("reference_images_version", first_item.request_data_projection.reference_images_version + 1),
        ("reference_images_count", first_item.request_data_projection.reference_images_count + 1),
        ("reference_images_digest", "e" * 64),
    ):
        changed_projection = replace(first_item.request_data_projection, **{field_name: value})
        tampered_results.append(
            with_first_item(request_data_projection=changed_projection)
        )
    tampered_phase = replace(staged.preparation, phase=staged.preparation.phase + "-changed")
    tampered_total = replace(
        staged.preparation,
        quoted_total=staged.preparation.quoted_total + 1,
    )
    changed_facts = replace(
        staged.preparation.items[0],
        quote_amount=staged.preparation.items[0].quote_amount + 1,
    )
    tampered_item_quote = replace(
        staged.preparation,
        items=(changed_facts, *staged.preparation.items[1:]),
    )
    tampered_results.extend(
        (
            replace(staged, preparation=tampered_phase),
            replace(staged, preparation=tampered_total),
            replace(staged, preparation=tampered_item_quote),
            replace(
                staged,
                preparation=replace(staged.preparation, executable=0),
            ),
            replace(
                staged,
                preparation=replace(staged.preparation, reserved_amount=False),
            ),
            replace(
                staged,
                preparation=replace(
                    staged.preparation,
                    available_balance=float(staged.preparation.available_balance),
                ),
            ),
        )
    )
    for tampered in tampered_results:
        assert producer.verify_staged_payloads(read_session, tampered).status == "unresolved"

    assert read_session.in_transaction()
    assert read_session.autoflush is True
    assert not read_session.new
    assert not read_session.dirty
    assert not read_session.deleted
    assert read_session.commit_calls == read_session.rollback_calls == read_session.close_calls == 0
    assert crypto_state["unseal_calls"] == 2 * (len(tampered_results) + 1)
    assert statements and all("generation_prepared_payloads" in sql for sql in statements)

    wrong_parent = replace(staged, parent_attempt_id="other-parent-attempt")
    wrong_parent_read = _caller_session(engine)
    assert producer.verify_staged_payloads(wrong_parent_read, wrong_parent).status == "unresolved"
    wrong_parent_read.close()
    wrong_order = replace(staged, items=tuple(reversed(staged.items)))
    wrong_order_read = _caller_session(engine)
    assert producer.verify_staged_payloads(wrong_order_read, wrong_order).status == "unresolved"
    wrong_order_read.close()

    first_payload_id = staged.items[0].prepared_payload_id
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "DROP TRIGGER trg_generation_prepared_payloads_immutable_update"
        )
        ciphertext = connection.execute(
            select(prepared_payloads.c.ciphertext).where(
                prepared_payloads.c.prepared_payload_id == first_payload_id
            )
        ).scalar_one()
        corrupted = ciphertext[:-1] + bytes((ciphertext[-1] ^ 1,))
        connection.execute(
            update(prepared_payloads)
            .where(prepared_payloads.c.prepared_payload_id == first_payload_id)
            .values(ciphertext=corrupted)
        )
    corrupt_read = _caller_session(engine)
    assert producer.verify_staged_payloads(corrupt_read, staged).status == "unresolved"
    assert corrupt_read.commit_calls == corrupt_read.rollback_calls == corrupt_read.close_calls == 0

    corrupt_read.close()
    read_session.close()
    caller.close()
    engine.dispose()


def test_actual_a2_rejects_foreign_model_before_registry_or_payload_work(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = _new_database(tmp_path, actor_model=False, foreign_model=True)
    authentication, auth_sessions = _authentication_service(engine)
    producer = _producer_for(
        authentication,
        RegistryCapabilities.unconfigured(),
        operation=AdmissionOperation.IMAGE_SINGLE.value,
    )
    caller = _caller_session(engine)
    _assert_a2_uses_same_caller_after_authentication(
        monkeypatch,
        caller=caller,
        authentication_sessions=auth_sessions,
    )

    result = producer.prepare_and_stage(
        caller,
        authorization="Bearer fixture-token",
        operation=AdmissionOperation.IMAGE_SINGLE.value,
        raw_key="foreign-model-key",
        request=_single_request("foreign-model"),
    )

    assert isinstance(result, AdmissionRefusal)
    assert result.code != "credential_registry_unavailable"
    assert auth_sessions[-1].closed_by_authentication
    assert caller.commit_calls == caller.rollback_calls == caller.close_calls == 0
    assert caller.execute(select(func.count()).select_from(prepared_payloads)).scalar_one() == 0
    assert caller.execute(select(func.count()).select_from(TaskSubmissionRow.__table__)).scalar_one() == 0
    caller.rollback()
    caller.close()
    engine.dispose()


def test_actual_batch_validates_every_item_before_registry_or_stage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = _new_database(tmp_path, foreign_model=True)
    authentication, auth_sessions = _authentication_service(engine)
    producer = _producer_for(authentication, RegistryCapabilities.unconfigured())
    caller = _caller_session(engine)
    _assert_a2_uses_same_caller_after_authentication(
        monkeypatch,
        caller=caller,
        authentication_sessions=auth_sessions,
    )

    result = producer.prepare_and_stage(
        caller,
        authorization="Bearer fixture-token",
        operation=AdmissionOperation.IMAGE_BATCH.value,
        raw_key="batch-invalid-second-model",
        request=_batch_request(second_model_id="foreign-model"),
    )

    assert isinstance(result, AdmissionRefusal)
    assert result.code != "credential_registry_unavailable"
    assert caller.execute(select(func.count()).select_from(prepared_payloads)).scalar_one() == 0
    assert caller.execute(select(func.count()).select_from(TaskSubmissionRow.__table__)).scalar_one() == 0
    assert caller.execute(select(user_credits.c.credits).where(user_credits.c.user_id == ACTOR_ID)).scalar_one() == 500
    assert caller.commit_calls == caller.rollback_calls == caller.close_calls == 0
    caller.rollback()
    caller.close()
    engine.dispose()


def test_second_payload_stage_refusal_requires_caller_rollback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = _new_database(tmp_path)
    capabilities, _crypto_state = _registered_capabilities(engine)
    authentication, auth_sessions = _authentication_service(engine)
    producer = _producer_for(authentication, capabilities)
    caller = _caller_session(engine)
    _assert_a2_uses_same_caller_after_authentication(
        monkeypatch,
        caller=caller,
        authentication_sessions=auth_sessions,
    )
    original_stage = CredentialRegistry.stage_prepared_payload
    calls = 0

    def refuse_second_stage(
        service: CredentialRegistry,
        session: Session,
        payload,
        *,
        payload_id: str,
    ):
        nonlocal calls
        calls += 1
        if calls == 2:
            return RegistryRefusal("fixture_second_stage_rejected", "fixture refusal")
        return original_stage(service, session, payload, payload_id=payload_id)

    monkeypatch.setattr(CredentialRegistry, "stage_prepared_payload", refuse_second_stage)
    result = producer.prepare_and_stage(
        caller,
        authorization="Bearer fixture-token",
        operation=AdmissionOperation.IMAGE_BATCH.value,
        raw_key="batch-partial-stage",
        request=_batch_request(),
    )

    assert result == SnapshotRefusal(
        503,
        "fixture_second_stage_rejected",
        caller_must_abort=True,
    )
    assert calls == 2
    assert caller.in_transaction()
    assert caller.autoflush is True
    assert caller.execute(select(func.count()).select_from(prepared_payloads)).scalar_one() == 1
    assert caller.execute(select(func.count()).select_from(TaskSubmissionRow.__table__)).scalar_one() == 0
    assert caller.execute(select(func.count()).select_from(BillingUnitRow.__table__)).scalar_one() == 0
    assert caller.commit_calls == caller.rollback_calls == caller.close_calls == 0
    with engine.connect() as connection:
        assert connection.execute(select(func.count()).select_from(prepared_payloads)).scalar_one() == 0

    caller.rollback()
    assert caller.rollback_calls == 1
    with engine.connect() as connection:
        assert connection.execute(select(func.count()).select_from(prepared_payloads)).scalar_one() == 0
    caller.close()
    engine.dispose()
