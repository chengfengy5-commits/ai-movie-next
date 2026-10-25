from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import sys
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, fields, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from weakref import ref

import pytest
from sqlalchemy import (
    Table,
    create_engine,
    event,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.persistence import SqlAlchemyAuthenticationUnitOfWork
from haoai_backend.authentication.tables import metadata as authentication_metadata
from haoai_backend.authentication.tables import user_credits, users
from haoai_backend.chat_data.tables import chapters, chat_messages, metadata as chat_metadata
from haoai_backend.generation_catalog.admission import AdmissionPolicy
from haoai_backend.generation_catalog.admission_types import (
    AdmissionOperation,
    AdmissionPreparationFacts,
    AdmissionReplay,
    CONTEXT_SCHEMA_VERSION,
    LockedTargetFacts,
    PreparedItemFacts,
)
from haoai_backend.generation_catalog.domain import (
    ModelCategory,
    ModelSelection,
    ModelSource,
    ServerQuote,
)
from haoai_backend.generation_contracts.errors import GenerationError
from haoai_backend.generation_contracts.types import (
    AdmissionFingerprint,
    EndpointPolicy,
    FrozenExecutionSnapshot,
    FrozenGrantSnapshot,
    GenerationActor,
    ModelReference,
    TargetSnapshot,
)
from haoai_backend.generation_catalog.tables import (
    ASSET_TABLES,
    chapters as catalog_chapters,
    metadata as catalog_metadata,
    model_configs,
    system_models,
)
from haoai_backend.generation_credentials import snapshot_producer
from haoai_backend.generation_credentials.application import (
    RegistryCapabilities,
    build_credential_registry,
)
from haoai_backend.generation_credentials.domain import (
    CredentialCrypto,
    EndpointPolicy as CredentialEndpointPolicy,
    CredentialScope,
    HmacProofAuthority,
    ProtectedAccountRecord,
    RegistrationStaged,
    RootEnrollmentSource,
    RotationAttestation,
    SealedMaterial,
    account_guard_associated_data,
    make_server_attempt,
)
from haoai_backend.generation_credentials.domain import PreparedPayloadStaged
from haoai_backend.generation_credentials.persistence import (
    credential_bindings,
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
    LegacyMessageProjection,
    RequestDataProjection,
    SnapshotPayloadItem,
    SnapshotPayloadsStaged,
)
from haoai_backend.generation_core import admission_staging
from haoai_backend.generation_core.admission_receipts import (
    AdmissionCommitUnknown,
    AdmissionConfirmed,
    AdmissionStaged,
    lookup_staged_admission,
    lookup_unknown_commit,
    retain_unknown_commit,
)
from haoai_backend.generation_core.admission_staging import (
    AdmissionStageRefusal,
    MediaSnapshotProducerBinding,
    build_media_snapshot_producer,
    prepare_and_stage,
)
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    Base,
    BillingUnitRow,
    CreditLogRow,
    ExecutionStepRow,
    FOUNDATION_TABLES,
    TaskSubmissionRow,
    UserCreditRow,
    UserRow,
)
from haoai_backend.generation_core.graph_planning import GraphUnitPlan, OperationGraphPlan
from haoai_backend.series_access.tables import metadata as series_access_metadata, series
from haoai_backend.shared.identity import TrustedActor


class _ProducerSpy:
    def __init__(self, result: SnapshotPayloadsStaged) -> None:
        self.result = result
        self.calls: list[tuple[Session, str, str, str, object]] = []

    def prepare_and_stage(
        self,
        session: Session,
        *,
        authorization: str,
        operation: str,
        raw_key: str,
        request: object,
    ) -> SnapshotPayloadsStaged:
        self.calls.append((session, authorization, operation, raw_key, request))
        return self.result


def _successful_staging_binding(
    session: Session,
    operation: str,
    quotes: tuple[int, ...],
    model_source: ModelSource,
) -> tuple[MediaSnapshotProducerBinding, _ProducerSpy, SnapshotPayloadsStaged]:
    owner_id = "owner-1"
    submission_id = "parent-attempt-1"
    request_digest = "a" * 64
    rule_digest = "b" * 64
    reference_digest = "d" * 64
    display_name = "Friendly image model"
    wire_model = "wire-image-model"
    model_id = "model-1"
    size = "1024x1024"
    preparation_items: list[PreparedItemFacts] = []
    producer_items: list[SnapshotPayloadItem] = []

    for index, quote_amount in enumerate(quotes):
        item_number = index + 1
        child_attempt_id = f"child-attempt-{item_number}"
        payload_id = f"payload-{item_number}"
        payload_ref = f"prepared-payload-v1/{len(payload_id)}:{payload_id}"
        refs = {
            name: f"{payload_ref}#{name}"
            for name in ("target", "model", "grant", "references")
        }
        is_batch = operation == "image.batch"
        asset_id = f"asset-{item_number}" if is_batch else None
        target_snapshot = (
            TargetSnapshot(
                kind="asset_message",
                chapter_id="chapter-1",
                series_id="series-1",
                asset_type="character",
                asset_id=asset_id,
            )
            if is_batch
            else TargetSnapshot(kind="none")
        )
        snapshot = FrozenExecutionSnapshot(
            model_ref=ModelReference(
                owner_id="system" if model_source is ModelSource.SYSTEM else owner_id,
                provider="provider-x",
                model_id=model_id,
            ),
            model_category="image",
            model_name=wire_model,
            model_params={"size": size},
            prompt_ref=None,
            effective_prompt="frozen prompt",
            effective_context="",
            server_quote=quote_amount,
            reference_snapshot_ref=refs["references"],
            target_snapshot=target_snapshot,
            credential_binding_ref=f"credential-binding-{item_number}",
            grant_snapshot=FrozenGrantSnapshot(
                actor_id=owner_id,
                series_id="series-1" if is_batch else None,
                access_provenance=("trusted-fixture",),
            ),
            adapter_config_versions=(("provider-x", "adapter-v1"),),
            endpoint_policy=EndpointPolicy(provider_origin="https://provider.invalid"),
        )
        model = ModelSelection(
            catalog_id=model_id,
            category=ModelCategory.IMAGE,
            source=model_source,
            display_name=display_name,
            wire_model=wire_model,
            quote=ServerQuote(
                category=ModelCategory.IMAGE,
                source=model_source,
                amount=quote_amount,
                effective_parameters={"size": size},
            ),
        )
        target_facts = LockedTargetFacts(
            kind="asset_batch" if is_batch else "none",
            series_id="series-1" if is_batch else None,
            chapter_id="chapter-1" if is_batch else None,
            asset_type="character" if is_batch else None,
            asset_id=asset_id,
        )
        preparation_items.append(
            PreparedItemFacts(
                index=index,
                model=model,
                credential_configured=True,
                target=target_facts,
                prompt=None,
                quote_amount=quote_amount,
                effective_parameters={"size": size},
            )
        )
        projection = RequestDataProjection(
            operation=operation,
            submission_id=submission_id,
            request_digest=request_digest,
            rule_digest=rule_digest,
            mode="batch_image" if is_batch else operation,
            model_id=model_id,
            model_source=model.source.value,
            model_owner_id=(
                None if model_source is ModelSource.SYSTEM else owner_id
            ),
            prompt_id=None,
            size=size,
            chapter_id="chapter-1" if is_batch else None,
            asset_type="character" if is_batch else None,
            asset_id=asset_id,
            asset_name=f"Asset {item_number}" if is_batch else None,
            reference_images_version=1,
            reference_images_count=0,
            reference_images_digest=reference_digest,
            prepared_payload_id=payload_id,
            protected_ref=payload_ref,
            target_ref=refs["target"],
            model_ref=refs["model"],
            grant_ref=refs["grant"],
            target_snapshot=target_snapshot,
        )
        legacy_projection = None
        if is_batch:
            context = {
                "modelId": model_id,
                "promptId": "",
                "size": size,
                "refImagesCount": 0,
                "refImagesDigest": reference_digest,
            }
            legacy_projection = LegacyMessageProjection(
                user_content=(
                    "__CTX__:"
                    + json.dumps(context, ensure_ascii=False)
                    + f"\nBatch description {item_number}"
                ),
                assistant_content="正在生成图片...",
                assistant_wire_content=None,
                model_name=display_name,
                chapter_id="chapter-1",
                asset_type="character",
                asset_id=asset_id or "",
                asset_name=f"Asset {item_number}",
            )
        producer_items.append(
            SnapshotPayloadItem(
                index=index,
                server_attempt_id=child_attempt_id,
                prepared_payload_id=payload_id,
                protected_ref=payload_ref,
                payload_staged=PreparedPayloadStaged(
                    prepared_payload_id=payload_id,
                    reused=False,
                ),
                snapshot=snapshot,
                legacy_message_projection=legacy_projection,
                request_data_projection=projection,
            )
        )

    fingerprint = AdmissionFingerprint(
        schema_version=CONTEXT_SCHEMA_VERSION,
        ordered_nonsecret_payload_digest=request_digest,
        actor_id=owner_id,
        operation=operation,
    )
    preparation = AdmissionPreparationFacts(
        operation=operation,
        fingerprint=fingerprint,
        phase="prepared",
        items=tuple(preparation_items),
        quoted_total=sum(quotes),
        required_balance=sum(quotes),
        available_balance=100,
        reserved_amount=0,
        executable=False,
    )
    staged_result = SnapshotPayloadsStaged(
        parent_attempt_id=submission_id,
        actor_id=owner_id,
        operation=operation,
        graph_version={
            "image.single": "image-single-v1",
            "image.batch": "image-batch-v1",
        }[operation],
        fingerprint=request_digest,
        manifest_digest="c" * 64,
        staging_session_ref=ref(session),
        preparation=preparation,
        items=tuple(producer_items),
    )
    producer = _ProducerSpy(staged_result)
    binding = MediaSnapshotProducerBinding(
        _producer=producer,
        _module=snapshot_producer,
        _server_id_factory=iter(
            f"generated-id-{index}" for index in range(1, 100)
        ).__next__,
    )
    return binding, producer, staged_result


def _staging_database() -> tuple[Session, object]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    chat_metadata.create_all(engine)
    return Session(engine), engine


def test_invalid_staged_manifest_digest_aborts_before_graph_or_fee_writes() -> None:
    session, engine = _staging_database()
    try:
        with session.begin():
            session.execute(insert(UserRow.__table__), [{"id": "owner-1"}])
            session.execute(
                insert(chapters), [{"id": "chapter-1", "series_id": "series-1"}]
            )
            session.execute(
                insert(UserCreditRow.__table__),
                [{"id": "wallet-1", "user_id": "owner-1", "credits": 20}],
            )
            binding, producer, staged_result = _successful_staging_binding(
                session, "image.single", (3,), ModelSource.SYSTEM
            )
            producer.result = replace(staged_result, manifest_digest="bad-digest")

            result = prepare_and_stage(
                binding,
                session,
                authorization="server-auth-context",
                operation="image.single",
                raw_key="bad-manifest-key",
                request={"prompt": "server-owned request"},
            )

            assert isinstance(result, AdmissionStageRefusal)
            assert result.code == "receipt_unverified"
            assert result.caller_must_abort is True
            assert session.execute(select(TaskSubmissionRow.__table__.c.id)).all() == []
            assert session.execute(select(AITaskRow.__table__.c.id)).all() == []
            assert session.execute(select(BillingUnitRow.__table__.c.id)).all() == []
            assert session.execute(select(ExecutionStepRow.__table__.c.id)).all() == []
            assert session.execute(select(CreditLogRow.__table__.c.id)).all() == []
        assert session.execute(select(UserCreditRow.__table__.c.credits)).scalar_one() == 20
    finally:
        session.close()
        engine.dispose()


def test_missing_real_producer_fails_closed_without_a_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_module(_: str) -> object:
        raise ModuleNotFoundError("producer module is not installed")

    monkeypatch.setattr(admission_staging.importlib, "import_module", missing_module)

    with pytest.raises(GenerationError) as error:
        build_media_snapshot_producer(
            authentication_service=object(),
            registry_capabilities=object(),
            server_id_factory=lambda: "unused",
            policies=object(),
            versions=object(),
        )

    assert error.value.code == "configuration_unavailable"


def test_unbound_producer_is_refused_before_any_database_effect() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    session = Session(engine)
    try:
        result = prepare_and_stage(
            object(),
            session,
            authorization="server-auth-context",
            operation="image.batch",
            raw_key="raw-key",
            request={"assets": [{"type": "character", "id": "asset-1"}]},
        )

        assert isinstance(result, AdmissionStageRefusal)
        assert result.caller_must_abort is False
        assert not session.in_transaction()
    finally:
        session.close()
        engine.dispose()


@pytest.mark.parametrize(
    (
        "operation",
        "quotes",
        "starting_wallet",
        "expected_wallet",
        "expected_messages",
        "model_source",
    ),
    (
        ("image.single", (3,), 20, 17, 0, ModelSource.SYSTEM),
        ("image.batch", (2, 3), 20, 15, 4, ModelSource.SYSTEM),
        ("image.single", (0,), None, None, 0, ModelSource.SYSTEM),
        ("image.batch", (0,), 12, 12, 2, ModelSource.SYSTEM),
        ("image.single", (1,), 20, 19, 0, ModelSource.USER),
    ),
)
def test_typed_producer_result_stages_g1_plan_and_durable_rows(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    quotes: tuple[int, ...],
    starting_wallet: int | None,
    expected_wallet: int | None,
    expected_messages: int,
    model_source: ModelSource,
) -> None:
    """Cover G2 result-shape handling with synthetic typed facts and a producer spy.

    The fixture does not exercise A2, the real producer, crypto, or native acceptance.
    """

    session, engine = _staging_database()
    try:
        with session.begin():
            session.execute(insert(UserRow.__table__), [{"id": "owner-1"}])
            session.execute(
                insert(chapters),
                [{"id": "chapter-1", "series_id": "series-1"}],
            )
            if starting_wallet is not None:
                session.execute(
                    insert(UserCreditRow.__table__),
                    [
                        {
                            "id": "wallet-1",
                            "user_id": "owner-1",
                            "credits": starting_wallet,
                        }
                    ],
                )

            binding, producer, typed_result = _successful_staging_binding(
                session, operation, quotes, model_source
            )
            assert type(typed_result) is SnapshotPayloadsStaged
            assert type(typed_result.preparation) is AdmissionPreparationFacts
            assert all(type(item) is PreparedItemFacts for item in typed_result.preparation.items)
            assert all(type(item.model) is ModelSelection for item in typed_result.preparation.items)
            assert all(type(item.target) is LockedTargetFacts for item in typed_result.preparation.items)

            real_g1_builder = admission_staging.build_operation_graph_plan
            accepted_plans: list[OperationGraphPlan] = []

            def capture_accepted_plan(*args: Any, **kwargs: Any) -> OperationGraphPlan:
                plan: OperationGraphPlan = real_g1_builder(*args, **kwargs)
                accepted_plans.append(plan)
                return plan

            monkeypatch.setattr(
                admission_staging, "build_operation_graph_plan", capture_accepted_plan
            )
            request = {"assets": ["server-owned request"]}
            result = prepare_and_stage(
                binding,
                session,
                authorization="server-auth-context",
                operation=operation,
                raw_key="raw-key-1",
                request=request,
                now=datetime(2026, 10, 12, 8, 0, 0),
            )

            assert isinstance(result, admission_staging.AdmissionStaged)
            assert producer.calls == [
                (session, "server-auth-context", operation, "raw-key-1", request)
            ]
            assert len(accepted_plans) == 1
            accepted_plan = accepted_plans[0]
            assert all(type(unit) is GraphUnitPlan for unit in accepted_plan.units)
            assert [unit.unit_id for unit in accepted_plan.units] == [
                task.unit_id for task in result.ordered_tasks
            ]
            assert [unit.quoted_amount for unit in accepted_plan.units] == list(quotes)

            submission = session.execute(
                select(
                    TaskSubmissionRow.__table__.c.id,
                    TaskSubmissionRow.__table__.c.task_ids,
                    TaskSubmissionRow.__table__.c.request_digest,
                )
            ).mappings().one()
            tasks = session.execute(
                select(
                    AITaskRow.__table__.c.id,
                    AITaskRow.__table__.c.model_name,
                    AITaskRow.__table__.c.message_id,
                    AITaskRow.__table__.c.request_data,
                ).order_by(AITaskRow.__table__.c.id)
            ).mappings().all()
            units = session.execute(
                select(
                    BillingUnitRow.__table__.c.id,
                    BillingUnitRow.__table__.c.task_id,
                    BillingUnitRow.__table__.c.item_key,
                    BillingUnitRow.__table__.c.quoted_amount,
                    BillingUnitRow.__table__.c.billing_status,
                ).order_by(BillingUnitRow.__table__.c.id)
            ).mappings().all()
            steps = session.execute(
                select(
                    ExecutionStepRow.__table__.c.billing_unit_id,
                    ExecutionStepRow.__table__.c.input_digest,
                ).order_by(ExecutionStepRow.__table__.c.billing_unit_id)
            ).mappings().all()
            messages = session.execute(
                select(
                    chat_messages.c.id,
                    chat_messages.c.role,
                    chat_messages.c.content,
                    chat_messages.c.model_name,
                ).order_by(chat_messages.c.id)
            ).mappings().all()
            ledger_rows = session.execute(
                select(
                    CreditLogRow.__table__.c.amount,
                    CreditLogRow.__table__.c.business_key,
                    CreditLogRow.__table__.c.balance_after,
                ).order_by(CreditLogRow.__table__.c.business_key)
            ).mappings().all()
            wallet_balance = session.execute(
                select(UserCreditRow.__table__.c.credits).where(
                    UserCreditRow.__table__.c.user_id == "owner-1"
                )
            ).scalar_one_or_none()

            assert submission["id"] == "parent-attempt-1"
            assert submission["task_ids"] == [task["id"] for task in tasks]
            assert submission["request_digest"] == "a" * 64
            assert [unit["quoted_amount"] for unit in units] == list(quotes)
            assert [unit["billing_status"] for unit in units] == [
                "charged" if quote else "no_charge" for quote in quotes
            ]
            assert len(steps) == len(quotes)
            assert len(messages) == expected_messages
            assert wallet_balance == expected_wallet
            assert [row["amount"] for row in ledger_rows] == [
                -quote for quote in quotes if quote > 0
            ]
            assert [row["business_key"] for row in ledger_rows] == [
                f"debit:{unit.unit_id}"
                for unit in accepted_plan.units
                if unit.quoted_amount > 0
            ]
            if operation == "image.batch":
                assert all(
                    message["content"].startswith("__CTX__:")
                    if message["role"] == "user"
                    else message["model_name"] == "Friendly image model"
                    for message in messages
                )
            for task in tasks:
                request_data = json.loads(task["request_data"])
                assert task["model_name"] == "Friendly image model"
                assert request_data["model_display_name"] == "Friendly image model"
                assert request_data["server_attempt_id"] == request_data["item_id"]
                assert request_data["server_attempt_id"] == request_data["unit_key"]
            assert all(step["input_digest"] == "a" * 64 for step in steps)
    finally:
        session.close()
        engine.dispose()


def test_projection_size_preserves_supported_public_scalar_types() -> None:
    session, engine = _staging_database()
    try:
        _, _, staged_result = _successful_staging_binding(
            session, "image.single", (0,), ModelSource.SYSTEM
        )
        projection = staged_result.items[0].request_data_projection
        values = {
            field.name: getattr(projection, field.name)
            for field in fields(projection)
        }

        for size in (None, "1024x1024", 1024, 1024.5):
            candidate = dict(values)
            candidate["size"] = size
            assert admission_staging._valid_projection_scalars(candidate)

        for size in (True, math.nan, math.inf, -math.inf, object()):
            candidate = dict(values)
            candidate["size"] = size
            assert not admission_staging._valid_projection_scalars(candidate)
    finally:
        session.close()
        engine.dispose()


def test_batch_context_uses_the_producer_projection_without_rebuilding_refs() -> None:
    projection = {
        "model_id": "model-1",
        "prompt_id": "prompt-1",
        "size": "1024x1024",
        "reference_images_count": 2,
        "reference_images_digest": "d" * 64,
    }
    context = {
        "modelId": "model-1",
        "promptId": "prompt-1",
        "size": "1024x1024",
        "refImagesCount": 2,
        "refImagesDigest": "d" * 64,
    }
    original_content = (
        "__CTX__:"
        + json.dumps(context, ensure_ascii=False)
        + "\n第一行描述\n保留第二行"
    )

    assert admission_staging._valid_legacy_context(original_content, projection)
    assert not admission_staging._valid_legacy_context(
        original_content.replace('"refImagesCount": 2', '"refImagesCount": 1'),
        projection,
    )
    assert not admission_staging._valid_legacy_context(
        "__CTX__:{}\n", projection
    )


def test_batch_requires_an_exact_asset_message_target_projection() -> None:
    projection = {
        "chapter_id": "chapter-1",
        "asset_type": "character",
        "asset_id": "asset-1",
    }
    accepted_candidate = TargetSnapshot(
        kind="asset_message",
        chapter_id="chapter-1",
        asset_type="character",
        asset_id="asset-1",
    )
    missing_binding = TargetSnapshot(
        kind="asset_message",
        chapter_id="chapter-1",
        asset_type="character",
        asset_id="other-asset",
    )

    assert admission_staging._batch_target_matches(
        "image.batch", projection, accepted_candidate
    )
    assert not admission_staging._batch_target_matches(
        "image.batch", projection, missing_binding
    )
    assert not admission_staging._batch_target_matches(
        "image.batch", projection, TargetSnapshot(kind="none")
    )


def test_payload_subreferences_preserve_opaque_id_without_rewriting_it() -> None:
    payload_id = " id:with/#parts\n "

    references = admission_staging._payload_references(payload_id)
    base = f"prepared-payload-v1/{len(payload_id)}:{payload_id}"

    assert references == {
        "base": base,
        "target": f"{base}#target",
        "model": f"{base}#model",
        "grant": f"{base}#grant",
        "references": f"{base}#references",
    }


def test_unsupported_haoai_attempt_group_is_refused_before_graph_writes() -> None:
    session, engine = _staging_database()
    try:
        with session.begin():
            binding, producer, image_result = _successful_staging_binding(
                session, "image.single", (0,), ModelSource.SYSTEM
            )
            fingerprint = AdmissionFingerprint(
                schema_version=CONTEXT_SCHEMA_VERSION,
                ordered_nonsecret_payload_digest="a" * 64,
                actor_id="owner-1",
                operation="video.single",
            )
            preparation = replace(
                image_result.preparation,
                operation="video.single",
                fingerprint=fingerprint,
            )
            producer.result = replace(
                image_result,
                operation="video.single",
                graph_version="video-haoai-attempt-group-v1",
                fingerprint="a" * 64,
                preparation=preparation,
            )

            result = prepare_and_stage(
                binding,
                session,
                authorization="server-auth-context",
                operation="video.single",
                raw_key="raw-key",
                request={"prompt": "server-owned request"},
                now=datetime(2026, 10, 12, 8, 0, 0),
            )

            assert isinstance(result, AdmissionStageRefusal)
            assert result.code == "configuration_unavailable"
            assert "HaoAI 三步" in result.message
            assert result.caller_must_abort is True
            assert session.execute(select(TaskSubmissionRow.__table__.c.id)).all() == []
    finally:
        session.close()
        engine.dispose()


_G2_JOINT_NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
_G2_JOINT_ACTOR_ID = "account-1"
_G2_JOINT_MODEL_ID = "model-1"
_G2_JOINT_PROVIDER = "fixture-provider"
_G2_JOINT_PROVIDER_SECRET = "joint-fixture-provider-secret"
_G2_JOINT_PRINCIPAL_ID = "30000000-0000-4000-8000-000000000001"

_GENERATION_MIGRATION_DIRECTORY = Path(__file__).resolve().parents[2] / "migrations"
_GENERATION_MIGRATION_RUNNER_SPEC = importlib.util.spec_from_file_location(
    "_haoai_joint_fixture_generation_migration_runner",
    _GENERATION_MIGRATION_DIRECTORY / "run_generation_migrations.py",
)
assert (
    _GENERATION_MIGRATION_RUNNER_SPEC is not None
    and _GENERATION_MIGRATION_RUNNER_SPEC.loader is not None
)
_GENERATION_MIGRATION_RUNNER = importlib.util.module_from_spec(
    _GENERATION_MIGRATION_RUNNER_SPEC
)
sys.modules[_GENERATION_MIGRATION_RUNNER_SPEC.name] = _GENERATION_MIGRATION_RUNNER
_GENERATION_MIGRATION_RUNNER_SPEC.loader.exec_module(_GENERATION_MIGRATION_RUNNER)


class _JointTokenCodec:
    def encode(self, claims: object) -> str:
        raise AssertionError("the fixture never issues tokens")

    def decode(self, token: str) -> dict[str, str]:
        assert token == "joint-fixture-token"
        return {"sub": _G2_JOINT_ACTOR_ID}


class _JointAuthenticationSession(Session):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.closed_by_authentication = False

    def close(self) -> None:
        self.closed_by_authentication = True
        super().close()


class _JointCallerSession(Session):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.commit_calls = 0
        self.rollback_calls = 0

    def commit(self) -> None:
        self.commit_calls += 1
        super().commit()

    def rollback(self) -> None:
        self.rollback_calls += 1
        super().rollback()

class _JointCommitThenUnknownSession(_JointCallerSession):
    def commit(self) -> None:
        super().commit()
        raise RuntimeError("fixture lost the commit acknowledgement")


class _JointIdFactory:
    def __init__(self, prefix: str) -> None:
        self._prefix = prefix
        self.values: list[str] = []

    def __call__(self) -> str:
        value = f"{self._prefix}-{len(self.values) + 1}"
        self.values.append(value)
        return value


class _JointRegistryIdFactory:
    def __init__(self) -> None:
        self.values: list[str] = []

    def __call__(self) -> str:
        value = f"40000000-0000-4000-8000-{len(self.values) + 1:012x}"
        self.values.append(value)
        return value


@dataclass(slots=True)
class _G2JointEnvironment:
    engine: Engine
    authentication: AuthenticationService
    authentication_sessions: list[_JointAuthenticationSession]
    capabilities: RegistryCapabilities
    crypto_state: dict[str, object]
    server_ids: _JointIdFactory
    binding: MediaSnapshotProducerBinding


@dataclass(slots=True)
class _G2PublicObservations:
    producer_sessions: list[Session]
    a2_calls: list[tuple[Session, object]]
    graph_plans: list[OperationGraphPlan]
    fee_sessions: list[Session]
    verifier_sessions: list[Session]

    @classmethod
    def empty(cls) -> _G2PublicObservations:
        return cls([], [], [], [], [])


def _joint_sqlite_schema(engine: Engine) -> None:
    def enable_foreign_keys(connection, _record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    event.listen(engine, "connect", enable_foreign_keys)
    authentication_metadata.create_all(engine)
    series_access_metadata.create_all(engine)
    catalog_metadata.create_all(engine)
    Base.metadata.create_all(engine, tables=list(FOUNDATION_TABLES))
    chat_metadata.create_all(engine)
    credential_metadata.create_all(engine)
    migration_result = _GENERATION_MIGRATION_RUNNER.apply_generation_migrations(
        engine,
        _GENERATION_MIGRATION_DIRECTORY,
    )
    if migration_result.status != "applied":
        raise AssertionError(f"joint fixture migration did not apply: {migration_result}")

    with engine.begin() as connection:
        for trigger_name, table_name in SQLITE_GUARD_TABLES.items():
            action = "DELETE" if trigger_name.endswith("_delete") else "UPDATE"
            connection.exec_driver_sql(
                f"CREATE TRIGGER {trigger_name} BEFORE {action} ON {table_name} "
                "BEGIN SELECT RAISE(ABORT, 'immutable'); END"
            )

    migration_directory = Path(__file__).resolve().parents[2] / "migrations" / "credential_registry"
    manifest = json.loads(
        (migration_directory / "manifest.json").read_text(encoding="utf-8")
    )
    migration_sql = (migration_directory / CREDENTIAL_MIGRATION_FILENAME).read_bytes()
    assert hashlib.sha256(migration_sql).hexdigest() == manifest["sha256"]
    assert manifest["version"] == CREDENTIAL_SCHEMA_VERSION
    with engine.begin() as connection:
        connection.execute(
            credential_schema_migrations.insert().values(
                version=CREDENTIAL_SCHEMA_VERSION,
                checksum=manifest["sha256"],
                applied_at=_G2_JOINT_NOW.replace(tzinfo=None),
            )
        )


def _joint_insert_account(connection, *, wallet_credits: int | None) -> None:
    stamp = _G2_JOINT_NOW.replace(tzinfo=None)
    connection.execute(
        insert(users).values(
            id=_G2_JOINT_ACTOR_ID,
            username="joint-fixture-admin",
            email="joint-fixture@example.test",
            hashed_password="fixture-only-hash",
            is_superuser=True,
            membership_type="premium",
            membership_expires_at=None,
            created_at=stamp,
        )
    )
    if wallet_credits is not None:
        connection.execute(
            insert(user_credits).values(
                id="joint-fixture-wallet",
                user_id=_G2_JOINT_ACTOR_ID,
                credits=wallet_credits,
                created_at=stamp,
                updated_at=stamp,
            )
        )


def _joint_insert_catalog_model(connection, model_source: ModelSource) -> None:
    stamp = _G2_JOINT_NOW.replace(tzinfo=None)
    common = {
        "id": _G2_JOINT_MODEL_ID,
        "category": "image",
        "name": "Public joint-fixture model",
        "provider": _G2_JOINT_PROVIDER,
        "api_key": _G2_JOINT_PROVIDER_SECRET,
        "model": "wire-joint-fixture-model",
        "base_url": "https://provider.example.test/v2",
        "params": json.dumps({"quality": "high", "size": "1024x1024"}),
        "created_at": stamp,
        "updated_at": stamp,
    }
    if model_source is ModelSource.SYSTEM:
        connection.execute(
            insert(system_models).values(
                **common,
                cost_per_call=3,
                description="local fixture only",
                is_active=True,
                sort_order=0,
            )
        )
        return

    connection.execute(
        insert(model_configs).values(
            **common,
            user_id=_G2_JOINT_ACTOR_ID,
            sort_order=0,
        )
    )


def _joint_insert_batch_targets(connection) -> None:
    stamp = _G2_JOINT_NOW.replace(tzinfo=None)
    connection.execute(
        insert(series).values(
            id="joint-series-1",
            user_id=_G2_JOINT_ACTOR_ID,
            team_id=None,
            claimed_by=None,
        )
    )
    connection.execute(
        insert(catalog_chapters).values(
            id="joint-chapter-1",
            series_id="joint-series-1",
            title="Joint fixture chapter",
            content="[{}, {}]",
            **{"order": 1},
            created_at=stamp,
            updated_at=stamp,
        )
    )
    for asset_type, asset_id, title in (
        ("character", "joint-character-1", "Joint Character"),
        ("scene", "joint-scene-1", "Joint Scene"),
    ):
        values: dict[str, object] = {
            "id": asset_id,
            "series_id": "joint-series-1",
            "description": f"Fixture description for {asset_id}",
            "image_url": None,
            "updated_at": stamp,
        }
        values["name" if asset_type == "character" else "title"] = title
        connection.execute(insert(ASSET_TABLES[asset_type]).values(**values))


def _joint_database(
    tmp_path: Path,
    *,
    model_source: ModelSource,
    wallet_credits: int | None = 500,
) -> Engine:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'g2-joint-fixture.sqlite'}")
    _joint_sqlite_schema(engine)
    with engine.begin() as connection:
        _joint_insert_account(connection, wallet_credits=wallet_credits)
        _joint_insert_catalog_model(connection, model_source)
        _joint_insert_batch_targets(connection)
    return engine


def _joint_authentication_service(
    engine: Engine,
) -> tuple[AuthenticationService, list[_JointAuthenticationSession]]:
    sessions: list[_JointAuthenticationSession] = []

    def unit_of_work() -> SqlAlchemyAuthenticationUnitOfWork:
        session = _JointAuthenticationSession(bind=engine, expire_on_commit=False)
        sessions.append(session)
        return SqlAlchemyAuthenticationUnitOfWork(session)

    runtime = AuthenticationRuntime(
        session_factory=lambda: None,
        secret_key="joint-fixture-secret-only",
        token_codec=_JointTokenCodec(),
        clock=lambda: _G2_JOINT_NOW,
        epoch=lambda: _G2_JOINT_NOW.timestamp(),
        id_factory=lambda: "joint-auth-id-unused",
    )
    return AuthenticationService(runtime, unit_of_work), sessions


def _joint_local_dummy_crypto(
    secrets_by_reference: dict[str, str],
) -> tuple[CredentialCrypto, dict[str, object]]:
    # Reversible callbacks support local SQLite verification only; they are not production crypto.
    state: dict[str, object] = {
        "seal_calls": 0,
        "unseal_calls": 0,
        "guard_calls": 0,
        "ready_calls": 0,
        "secret_handle_calls": 0,
        "legacy_value_calls": 0,
        "secrets_by_reference": secrets_by_reference,
    }

    def seal(secret: str, associated_data: bytes) -> SealedMaterial:
        state["seal_calls"] = int(state["seal_calls"]) + 1
        return SealedMaterial(
            secret.encode("utf-8"),
            uuid.uuid4().bytes[:12],
            "joint-seal-v1",
            "joint-aead-v1",
        )

    def unseal(material: SealedMaterial, associated_data: bytes) -> str | None:
        state["unseal_calls"] = int(state["unseal_calls"]) + 1
        if material.key_id != "joint-seal-v1":
            return None
        return material.ciphertext.decode("utf-8")

    def guard(secret: str, associated_data: bytes) -> str:
        state["guard_calls"] = int(state["guard_calls"]) + 1
        return hashlib.sha256(associated_data + b"\0" + secret.encode("utf-8")).hexdigest()

    def resolve_secret(reference: str) -> str | None:
        state["secret_handle_calls"] = int(state["secret_handle_calls"]) + 1
        return secrets_by_reference.get(reference)

    def self_test() -> bool:
        state["ready_calls"] = int(state["ready_calls"]) + 1
        return True

    def resolve_legacy_value(stored: str) -> str | None:
        state["legacy_value_calls"] = int(state["legacy_value_calls"]) + 1
        return stored or None

    return (
        CredentialCrypto(
            seal_key_id="joint-seal-v1",
            guard_key_id="joint-guard-v1",
            algorithm_version="joint-aead-v1",
            seal=seal,
            unseal=unseal,
            consistency_guard=guard,
            resolve_secret_handle=resolve_secret,
            resolve_legacy_config_value=resolve_legacy_value,
            self_test=self_test,
        ),
        state,
    )


def _joint_registered_capabilities(
    engine: Engine,
    *,
    model_source: ModelSource,
) -> tuple[RegistryCapabilities, dict[str, object]]:
    # This signer and the paired crypto callbacks exist only in the local SQLite fixture.
    authority = HmacProofAuthority("joint-fixture-root-v1", b"r" * 32)
    secrets_by_reference = {"vault://joint/provider": _G2_JOINT_PROVIDER_SECRET}
    crypto, crypto_state = _joint_local_dummy_crypto(secrets_by_reference)
    owner_or_system = (
        _G2_JOINT_ACTOR_ID if model_source is ModelSource.USER else "system"
    )
    scope = CredentialScope(
        model_source.value,
        _G2_JOINT_MODEL_ID,
        owner_or_system,
        _G2_JOINT_PROVIDER,
    )
    unsigned = ProtectedAccountRecord(
        record_id="joint-fixture-record-1",
        principal_id=_G2_JOINT_PRINCIPAL_ID,
        scope=scope,
        issuer_namespace="joint-fixture-account-v1",
        stable_account_subject="joint-fixture-account-subject",
        key_generation="generation-1",
        material_ref="vault://joint/provider",
        evidence_ref="fixture://evidence/joint-record-1",
        evidence_version="joint-evidence-v1",
        valid_from=_G2_JOINT_NOW - timedelta(days=1),
        valid_until=_G2_JOINT_NOW + timedelta(days=30),
        revoked_at=None,
        endpoint_policy=CredentialEndpointPolicy.from_base_url(
            "https://provider.example.test/v2",
            allowed_content_paths=("/v1/files/{file_id}",),
        ),
        guard_key_id=crypto.guard_key_id,
        secret_consistency_guard="0" * 64,
        proof_key_id=authority.key_id,
        proof_signature="0" * 64,
    )
    guarded = replace(
        unsigned,
        secret_consistency_guard=crypto.guard(
            _G2_JOINT_PROVIDER_SECRET,
            account_guard_associated_data(unsigned),
        ),
    )
    record = replace(
        guarded,
        proof_signature=authority.sign("root-enrollment-v1", guarded.proof_payload()),
    )
    enrollment_source = RootEnrollmentSource((record,), authority)
    registry_sessions = sessionmaker(engine, expire_on_commit=False, autoflush=True)
    registry_ids = _JointRegistryIdFactory()
    capabilities = RegistryCapabilities(
        proof_authority=authority,
        enrollment_source=enrollment_source,
        crypto=crypto,
        read_session_factory=registry_sessions,
        db_clock=lambda: _G2_JOINT_NOW,
        id_factory=registry_ids,
    )
    registry = build_credential_registry(capabilities)
    attempt = make_server_attempt(
        authority,
        attempt_id="joint-fixture-registration-1",
        actor_id=_G2_JOINT_ACTOR_ID,
        operation="register",
        scope=scope,
        key_generation=record.key_generation,
        protected_record_id=record.record_id,
    )
    with registry_sessions() as session:
        if model_source is ModelSource.SYSTEM:
            staged = registry.stage_register_system_config(
                session,
                TrustedActor(_G2_JOINT_ACTOR_ID),
                _G2_JOINT_MODEL_ID,
                attempt,
            )
        else:
            staged = registry.stage_register_owned_config(
                session,
                TrustedActor(_G2_JOINT_ACTOR_ID),
                _G2_JOINT_MODEL_ID,
                attempt,
            )
        assert isinstance(staged, RegistrationStaged)
        session.commit()
    return capabilities, crypto_state


def _joint_build_binding(
    authentication: AuthenticationService,
    capabilities: RegistryCapabilities,
    server_ids: _JointIdFactory,
    *,
    operation: str,
) -> MediaSnapshotProducerBinding:
    binding = build_media_snapshot_producer(
        authentication,
        capabilities,
        server_id_factory=server_ids,
        policies={operation: AdmissionPolicy(max_concurrent_tasks=0)},
        versions=snapshot_producer.SnapshotVersions(
            projector="joint-fixture-projector-v1",
            reference_policy="joint-fixture-reference-policy-v1",
        ),
    )
    assert type(binding) is MediaSnapshotProducerBinding
    assert type(binding._producer) is snapshot_producer.MediaSnapshotProducer
    return binding


def _joint_environment(
    tmp_path: Path,
    *,
    model_source: ModelSource,
    operation: str,
    wallet_credits: int | None = 500,
) -> _G2JointEnvironment:
    engine = _joint_database(
        tmp_path,
        model_source=model_source,
        wallet_credits=wallet_credits,
    )
    capabilities, crypto_state = _joint_registered_capabilities(
        engine,
        model_source=model_source,
    )
    authentication, authentication_sessions = _joint_authentication_service(engine)
    server_ids = _JointIdFactory("joint-server-id")
    binding = _joint_build_binding(
        authentication,
        capabilities,
        server_ids,
        operation=operation,
    )
    return _G2JointEnvironment(
        engine=engine,
        authentication=authentication,
        authentication_sessions=authentication_sessions,
        capabilities=capabilities,
        crypto_state=crypto_state,
        server_ids=server_ids,
        binding=binding,
    )


def _joint_caller(
    engine: Engine,
    *,
    unknown_after_commit: bool = False,
) -> _JointCallerSession:
    session_type = (
        _JointCommitThenUnknownSession if unknown_after_commit else _JointCallerSession
    )
    caller = session_type(bind=engine, autoflush=True, expire_on_commit=False)
    caller.begin()
    return caller


def _joint_request(operation: str) -> dict[str, object]:
    if operation == AdmissionOperation.IMAGE_SINGLE.value:
        return {
            "model_id": _G2_JOINT_MODEL_ID,
            "prompt": "A quiet room",
            "target": {"kind": "standalone"},
            "size": None,
            "reference_images": ["https://ref.example.test/joint-single?ordered=1"],
        }
    if operation == AdmissionOperation.IMAGE_BATCH.value:
        return {
            "chapter_id": "joint-chapter-1",
            "assets": [
                {
                    "type": "character",
                    "id": "joint-character-1",
                    "name": "",
                    "description": "First ordered description",
                    "model_id": _G2_JOINT_MODEL_ID,
                    "prompt_id": "",
                    "size": "1024x1024",
                    "reference_images": ["https://ref.example.test/first?token=one"],
                },
                {
                    "type": "scene",
                    "id": "joint-scene-1",
                    "name": "Second scene display alias",
                    "description": "Second ordered description",
                    "model_id": _G2_JOINT_MODEL_ID,
                    "prompt_id": "",
                    "size": "1024x1024",
                    "reference_images": ["https://ref.example.test/second?token=two"],
                },
            ],
        }
    raise AssertionError(f"unexpected joint fixture operation: {operation}")


def _observe_joint_public_paths(
    monkeypatch: pytest.MonkeyPatch,
    *,
    authentication_sessions: list[_JointAuthenticationSession],
) -> _G2PublicObservations:
    observed = _G2PublicObservations.empty()
    real_producer_stage = snapshot_producer.MediaSnapshotProducer.prepare_and_stage

    def observe_producer_stage(
        producer: snapshot_producer.MediaSnapshotProducer,
        caller: Session,
        **kwargs: Any,
    ) -> object:
        assert caller.in_transaction()
        observed.producer_sessions.append(caller)
        return real_producer_stage(producer, caller, **kwargs)

    real_verifier = snapshot_producer.MediaSnapshotProducer.verify_staged_payloads

    def observe_verifier(
        producer: snapshot_producer.MediaSnapshotProducer,
        reader: Session,
        expected: SnapshotPayloadsStaged,
    ) -> object:
        assert reader not in observed.producer_sessions
        observed.verifier_sessions.append(reader)
        return real_verifier(producer, reader, expected)

    real_prepare = snapshot_producer.prepare_admission

    def observe_a2(session: Session, context: object, *, policy: object) -> object:
        assert observed.producer_sessions[-1] is session
        assert session.in_transaction()
        assert session.autoflush is False
        assert authentication_sessions
        assert authentication_sessions[-1].closed_by_authentication
        result = real_prepare(session, context, policy=policy)
        observed.a2_calls.append((session, result))
        return result

    real_plan_builder = admission_staging.build_operation_graph_plan

    def observe_g1(*args: Any, **kwargs: Any) -> OperationGraphPlan:
        plan = real_plan_builder(*args, **kwargs)
        observed.graph_plans.append(plan)
        return plan

    real_stage_fees = admission_staging.stage_unit_fees

    def observe_fee_staging(session: Session, **kwargs: Any) -> object:
        assert observed.producer_sessions[-1] is session
        assert session.in_transaction()
        observed.fee_sessions.append(session)
        return real_stage_fees(session, **kwargs)

    monkeypatch.setattr(
        snapshot_producer.MediaSnapshotProducer,
        "prepare_and_stage",
        observe_producer_stage,
    )
    monkeypatch.setattr(
        snapshot_producer.MediaSnapshotProducer,
        "verify_staged_payloads",
        observe_verifier,
    )
    monkeypatch.setattr(snapshot_producer, "prepare_admission", observe_a2)
    monkeypatch.setattr(
        admission_staging,
        "build_operation_graph_plan",
        observe_g1,
    )
    monkeypatch.setattr(admission_staging, "stage_unit_fees", observe_fee_staging)
    return observed


def _joint_prepare_and_stage(
    binding: MediaSnapshotProducerBinding,
    caller: Session,
    *,
    operation: str,
    raw_key: str,
    request: dict[str, object],
) -> object:
    return prepare_and_stage(
        binding,
        caller,
        authorization="Bearer joint-fixture-token",
        operation=operation,
        raw_key=raw_key,
        request=request,
        now=_G2_JOINT_NOW.replace(tzinfo=None),
    )


@contextmanager
def _joint_allow_credential_rotation(engine: Engine) -> Iterator[None]:
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


def _joint_rotate_system_credential(
    environment: _G2JointEnvironment,
) -> RegistryCapabilities:
    capabilities = environment.capabilities
    source = capabilities.enrollment_source
    authority = capabilities.proof_authority
    crypto = capabilities.crypto
    assert isinstance(source, RootEnrollmentSource)
    assert authority is not None
    assert crypto is not None
    predecessor = source.records[0]
    assert predecessor.scope.source == ModelSource.SYSTEM.value
    secrets_by_reference = environment.crypto_state["secrets_by_reference"]
    assert isinstance(secrets_by_reference, dict)

    new_secret = "joint-fixture-provider-secret-v2"
    new_material_ref = "vault://joint/provider-v2"
    secrets_by_reference[new_material_ref] = new_secret
    replacement_unsigned = replace(
        predecessor,
        record_id="joint-fixture-record-2",
        key_generation="generation-2",
        material_ref=new_material_ref,
        evidence_ref="fixture://evidence/joint-record-2",
        evidence_version="joint-evidence-v2",
        secret_consistency_guard="0" * 64,
        proof_signature="0" * 64,
    )
    guarded = replace(
        replacement_unsigned,
        secret_consistency_guard=crypto.guard(
            new_secret,
            account_guard_associated_data(replacement_unsigned),
        ),
    )
    replacement = replace(
        guarded,
        proof_signature=authority.sign("root-enrollment-v1", guarded.proof_payload()),
    )
    with environment.engine.connect() as connection:
        binding_id = connection.execute(
            select(credential_bindings.c.binding_id).where(
                credential_bindings.c.source == predecessor.scope.source,
                credential_bindings.c.row_id == predecessor.scope.row_id,
                credential_bindings.c.owner_or_system
                == predecessor.scope.owner_or_system,
                credential_bindings.c.provider == predecessor.scope.provider,
            )
        ).scalar_one()
    unsigned_proof = RotationAttestation(
        proof_id="joint-fixture-rotation-proof-2",
        original_record_id=predecessor.record_id,
        replacement_record_id=replacement.record_id,
        binding_id=binding_id,
        predecessor_version=1,
        proof_key_id=authority.key_id,
        proof_signature="0" * 64,
    )
    proof = replace(
        unsigned_proof,
        proof_signature=authority.sign(
            "root-rotation-v1", unsigned_proof.proof_payload()
        ),
    )
    next_source = RootEnrollmentSource(
        (*source.records, replacement),
        authority,
        (*source.rotation_attestations, proof),
    )
    next_capabilities = replace(capabilities, enrollment_source=next_source)

    with environment.engine.begin() as connection:
        connection.execute(
            update(users)
            .where(users.c.id == _G2_JOINT_ACTOR_ID)
            .values(
                membership_type="free",
                membership_expires_at=datetime(2000, 1, 1),
            )
        )
        connection.execute(
            update(system_models)
            .where(system_models.c.id == _G2_JOINT_MODEL_ID)
            .values(
                name="Changed current catalog label",
                model="changed-current-wire-model",
                api_key=new_secret,
                cost_per_call=99,
            )
        )

    attempt = make_server_attempt(
        authority,
        attempt_id="joint-fixture-rotation-2",
        actor_id=_G2_JOINT_ACTOR_ID,
        operation="rotate",
        scope=predecessor.scope,
        key_generation=replacement.key_generation,
        expected_revision=1,
        predecessor_version=1,
        protected_record_id=replacement.record_id,
        rotation_proof_ref=proof.proof_id,
    )
    factory = capabilities.read_session_factory
    assert factory is not None
    registry = build_credential_registry(next_capabilities)
    with _joint_allow_credential_rotation(environment.engine):
        with factory() as session:
            registered = registry.stage_rotate_or_replace_config(
                session,
                TrustedActor(_G2_JOINT_ACTOR_ID),
                predecessor.scope,
                1,
                attempt,
            )
            assert isinstance(registered, RegistrationStaged)
            assert registered.binding_version == 2
            session.commit()
    return next_capabilities


def _joint_count(session: Session, table: Table) -> int:
    return session.execute(select(func.count()).select_from(table)).scalar_one()


def _joint_crypto_counts(state: dict[str, object]) -> tuple[int, ...]:
    return tuple(
        int(state[key])
        for key in (
            "seal_calls",
            "unseal_calls",
            "guard_calls",
            "ready_calls",
            "secret_handle_calls",
            "legacy_value_calls",
        )
    )


def _joint_receipt_task_ids(receipt: Any, operation: str) -> tuple[str, ...]:
    if operation == AdmissionOperation.IMAGE_BATCH.value:
        return tuple(task.task_id for task in receipt.tasks)
    return (receipt.task_id,)


def _assert_joint_graph_and_fees(
    session: Session,
    staged: AdmissionStaged,
    *,
    quotes: tuple[int, ...],
    starting_wallet: int | None,
    expected_messages: int,
) -> None:
    submission_table = TaskSubmissionRow.__table__
    task_table = AITaskRow.__table__
    unit_table = BillingUnitRow.__table__
    step_table = ExecutionStepRow.__table__
    tasks = session.execute(select(task_table)).mappings().all()
    units = session.execute(select(unit_table)).mappings().all()
    steps = session.execute(select(step_table)).mappings().all()
    submission = session.execute(select(submission_table)).mappings().one()
    payload_items = staged.producer_staged_result.items
    payload_rows = session.execute(select(prepared_payloads)).mappings().all()
    payloads_by_id = {row["prepared_payload_id"]: row for row in payload_rows}

    assert submission["id"] == staged.submission_id
    assert submission["user_id"] == _G2_JOINT_ACTOR_ID
    assert submission["task_ids"] == [task.task_id for task in staged.ordered_tasks]
    assert len(tasks) == len(units) == len(steps) == len(quotes)
    assert len(payload_items) == len(quotes)
    assert set(payloads_by_id) == {item.prepared_payload_id for item in payload_items}
    assert _joint_count(session, chat_messages) == expected_messages

    tasks_by_id = {row["id"]: row for row in tasks}
    units_by_task = {row["task_id"]: row for row in units}
    steps_by_unit = {row["billing_unit_id"]: row for row in steps}
    ledger_rows = session.execute(select(CreditLogRow.__table__)).mappings().all()
    ledger_by_key = {row["business_key"]: row for row in ledger_rows}
    messages = session.execute(select(chat_messages)).mappings().all()
    messages_by_id = {row["id"]: row for row in messages}
    wallet_balance = session.execute(
        select(UserCreditRow.__table__.c.credits).where(
            UserCreditRow.__table__.c.user_id == _G2_JOINT_ACTOR_ID
        )
    ).scalar_one_or_none()

    if starting_wallet is None:
        assert wallet_balance is None
    else:
        assert wallet_balance == starting_wallet - sum(quotes)

    remaining = starting_wallet
    for index, (expected, payload_item, quote) in enumerate(
        zip(staged.ordered_tasks, payload_items, quotes, strict=True)
    ):
        task = tasks_by_id[expected.task_id]
        unit = units_by_task[expected.task_id]
        step = steps_by_unit[expected.unit_id]
        request_data = json.loads(task["request_data"])
        payload_id = payload_item.prepared_payload_id
        opaque = f"prepared-payload-v1/{len(payload_id)}:{payload_id}"
        payload_row = payloads_by_id[payload_id]

        assert payload_item.index == index
        assert task["id"] == expected.task_id
        assert unit["id"] == expected.unit_id
        assert step["id"] in expected.step_ids
        assert payload_row["actor_id"] == _G2_JOINT_ACTOR_ID
        assert payload_row["operation"] == staged.operation
        assert payload_row["server_attempt_id"] == payload_item.server_attempt_id
        assert payload_row["target_ref"] == f"{opaque}#target"
        assert payload_row["model_ref"] == f"{opaque}#model"
        assert payload_row["grant_ref"] == f"{opaque}#grant"
        assert task["model_name"] == "Public joint-fixture model"
        assert request_data["model_display_name"] == task["model_name"]
        assert request_data["model_id"] == _G2_JOINT_MODEL_ID
        assert request_data["model_source"] == (
            staged.producer_staged_result.preparation.items[index].model.source.value
        )
        assert request_data["prepared_payload_id"] == payload_id
        assert request_data["protected_ref"] == payload_item.protected_ref == opaque
        assert request_data["target_ref"] == f"{opaque}#target"
        assert request_data["model_ref"] == f"{opaque}#model"
        assert request_data["grant_ref"] == f"{opaque}#grant"
        assert request_data["reference_ref"] == f"{opaque}#references"
        assert request_data["server_attempt_id"] == payload_item.server_attempt_id
        assert request_data["server_attempt_id"] == request_data["item_id"]
        assert request_data["server_attempt_id"] == request_data["unit_key"]
        assert payload_item.snapshot.model_name == "wire-joint-fixture-model"
        assert _G2_JOINT_PROVIDER_SECRET not in task["request_data"]
        assert unit["quoted_amount"] == quote
        assert unit["billing_status"] == ("charged" if quote else "no_charge")
        assert step["input_digest"] == request_data["request_digest"]
        assert step["input_snapshot"]["ordered_manifest_digest"] == (
            staged.producer_staged_result.manifest_digest
        )
        assert "ordered_manifest_digest" not in request_data
        if staged.operation == AdmissionOperation.IMAGE_BATCH.value:
            assert request_data["chapter_id"] == "joint-chapter-1"
            assert request_data["asset_id"] == (
                "joint-character-1" if index == 0 else "joint-scene-1"
            )
            assert request_data["asset_type"] == (
                "character" if index == 0 else "scene"
            )
        if expected.message_ids:
            user_message = messages_by_id[expected.message_ids[0]]
            assistant_message = messages_by_id[expected.message_ids[1]]
            assert user_message["role"] == "user"
            assert assistant_message["role"] == "assistant"
            assert user_message["asset_id"] == assistant_message["asset_id"]
            assert request_data["asset_id"] == assistant_message["asset_id"]
            assert assistant_message["model_name"] == "Public joint-fixture model"

        if quote > 0:
            assert remaining is not None
            remaining -= quote
            ledger = ledger_by_key[f"debit:{expected.unit_id}"]
            assert ledger["amount"] == -quote
            assert ledger["balance_after"] == remaining
        else:
            assert f"debit:{expected.unit_id}" not in ledger_by_key

    for message in messages:
        if message["role"] == "user":
            assert message["content"].startswith("__CTX__:")
        else:
            assert message["model_name"] == "Public joint-fixture model"

    assert len(ledger_rows) == sum(quote > 0 for quote in quotes)


@pytest.mark.parametrize(
    ("operation", "quotes", "expected_messages"),
    (
        (AdmissionOperation.IMAGE_SINGLE.value, (3,), 0),
        (AdmissionOperation.IMAGE_BATCH.value, (3, 3), 4),
    ),
)
def test_real_public_entry_stages_actual_producer_g1_graph_and_fees(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    operation: str,
    quotes: tuple[int, ...],
    expected_messages: int,
) -> None:
    environment = _joint_environment(
        tmp_path,
        model_source=ModelSource.SYSTEM,
        operation=operation,
    )
    caller = _joint_caller(environment.engine)
    observed = _observe_joint_public_paths(
        monkeypatch,
        authentication_sessions=environment.authentication_sessions,
    )
    request = _joint_request(operation)
    try:
        staged = _joint_prepare_and_stage(
            environment.binding,
            caller,
            operation=operation,
            raw_key=f"joint-{operation}-positive",
            request=request,
        )

        assert isinstance(staged, AdmissionStaged)
        assert type(environment.binding._producer) is snapshot_producer.MediaSnapshotProducer
        assert observed.producer_sessions == [caller]
        assert len(observed.a2_calls) == 1
        assert observed.a2_calls[0][0] is caller
        assert type(observed.a2_calls[0][1]) is AdmissionPreparationFacts
        assert tuple(item.quote_amount for item in observed.a2_calls[0][1].items) == quotes
        assert len(observed.graph_plans) == 1
        assert type(observed.graph_plans[0]) is OperationGraphPlan
        assert all(type(unit) is GraphUnitPlan for unit in observed.graph_plans[0].units)
        assert [unit.unit_id for unit in observed.graph_plans[0].units] == [
            task.unit_id for task in staged.ordered_tasks
        ]
        assert [unit.quoted_amount for unit in observed.graph_plans[0].units] == list(quotes)
        assert observed.fee_sessions == [caller]
        ids_after_staging = tuple(environment.server_ids.values)
        _assert_joint_graph_and_fees(
            caller,
            staged,
            quotes=quotes,
            starting_wallet=500,
            expected_messages=expected_messages,
        )
        assert caller.commit_calls == caller.rollback_calls == 0

        same_session = lookup_staged_admission(caller, staged)
        assert not isinstance(same_session, AdmissionConfirmed)
        with Session(environment.engine) as independent:
            before_commit = lookup_staged_admission(independent, staged)
            assert isinstance(before_commit, AdmissionCommitUnknown)
            independent.rollback()

        caller.commit()
        assert caller.commit_calls == 1
        with Session(environment.engine) as independent:
            confirmed = lookup_staged_admission(independent, staged)
            assert isinstance(confirmed, AdmissionConfirmed)
            assert confirmed.source == "durable_lookup"
            assert confirmed.submission_id == staged.submission_id
            assert _joint_receipt_task_ids(
                confirmed.receipt, operation
            ) == tuple(
                task.task_id for task in staged.ordered_tasks
            )
        assert observed.verifier_sessions == [independent]
        assert tuple(environment.server_ids.values) == ids_after_staging
    finally:
        caller.close()
        environment.engine.dispose()


def test_real_canvas_asset_safe_projection_preserves_existing_image_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    environment = _joint_environment(
        tmp_path,
        model_source=ModelSource.SYSTEM,
        operation=AdmissionOperation.IMAGE_SINGLE.value,
    )
    old_image_url = "https://assets.example.test/previous-image.png"
    with environment.engine.begin() as connection:
        connection.execute(
            update(ASSET_TABLES["character"])
            .where(ASSET_TABLES["character"].c.id == "joint-character-1")
            .values(image_url=old_image_url)
        )

    caller = _joint_caller(environment.engine)
    captured_payloads: list[SnapshotPayloadsStaged] = []
    original_producer_stage = snapshot_producer.MediaSnapshotProducer.prepare_and_stage

    def capture_producer_result(
        producer: snapshot_producer.MediaSnapshotProducer,
        session: Session,
        **kwargs: Any,
    ) -> object:
        result = original_producer_stage(producer, session, **kwargs)
        if isinstance(result, SnapshotPayloadsStaged):
            captured_payloads.append(result)
        return result

    monkeypatch.setattr(
        snapshot_producer.MediaSnapshotProducer,
        "prepare_and_stage",
        capture_producer_result,
    )
    observed = _observe_joint_public_paths(
        monkeypatch,
        authentication_sessions=environment.authentication_sessions,
    )
    request = _joint_request(AdmissionOperation.IMAGE_SINGLE.value)
    request["target"] = {
        "kind": "canvas_apply",
        "destination": {
            "kind": "asset_image",
            "chapter_id": "joint-chapter-1",
            "asset_type": "character",
            "asset_id": "joint-character-1",
        },
    }

    try:
        staged = _joint_prepare_and_stage(
            environment.binding,
            caller,
            operation=AdmissionOperation.IMAGE_SINGLE.value,
            raw_key="joint-canvas-asset-old-image-url",
            request=request,
        )

        assert isinstance(staged, AdmissionStaged)
        assert observed.producer_sessions == [caller]
        assert len(observed.a2_calls) == 1
        assert len(captured_payloads) == 1
        producer_target = captured_payloads[0].items[0].snapshot.target_snapshot
        assert producer_target.kind == "canvas_asset_image"
        assert producer_target.expected_asset_image_url == old_image_url

        task_id = staged.ordered_tasks[0].task_id
        request_data = json.loads(
            caller.execute(
                select(AITaskRow.__table__.c.request_data).where(
                    AITaskRow.__table__.c.id == task_id
                )
            ).scalar_one()
        )
        safe_target = request_data["target_snapshot"]
        assert safe_target["kind"] == producer_target.kind
        assert safe_target["chapter_id"] == producer_target.chapter_id
        assert safe_target["series_id"] == producer_target.series_id
        assert safe_target["asset_type"] == producer_target.asset_type
        assert safe_target["asset_id"] == producer_target.asset_id
        assert safe_target["same_series_target_digest"] == producer_target.same_series_target_digest
        assert safe_target.get("expected_asset_image_url") == old_image_url
    finally:
        caller.rollback()
        caller.close()
        environment.engine.dispose()


def test_real_zero_quote_byok_stages_without_wallet_or_debit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    environment = _joint_environment(
        tmp_path,
        model_source=ModelSource.USER,
        operation=AdmissionOperation.IMAGE_SINGLE.value,
        wallet_credits=None,
    )
    caller = _joint_caller(environment.engine)
    observed = _observe_joint_public_paths(
        monkeypatch,
        authentication_sessions=environment.authentication_sessions,
    )
    try:
        staged = _joint_prepare_and_stage(
            environment.binding,
            caller,
            operation=AdmissionOperation.IMAGE_SINGLE.value,
            raw_key="joint-image-single-byok-zero",
            request=_joint_request(AdmissionOperation.IMAGE_SINGLE.value),
        )

        assert isinstance(staged, AdmissionStaged)
        assert observed.producer_sessions == [caller]
        assert len(observed.a2_calls) == len(observed.graph_plans) == 1
        assert observed.a2_calls[0][0] is caller
        assert type(observed.a2_calls[0][1]) is AdmissionPreparationFacts
        assert tuple(item.quote_amount for item in observed.a2_calls[0][1].items) == (0,)
        assert observed.fee_sessions == [caller]
        _assert_joint_graph_and_fees(
            caller,
            staged,
            quotes=(0,),
            starting_wallet=None,
            expected_messages=0,
        )
        assert _joint_count(caller, UserCreditRow.__table__) == 0
        assert caller.commit_calls == caller.rollback_calls == 0

        caller.commit()
        with Session(environment.engine) as independent:
            confirmed = lookup_staged_admission(independent, staged)
            assert isinstance(confirmed, AdmissionConfirmed)
            assert confirmed.source == "durable_lookup"
            assert _joint_count(independent, CreditLogRow.__table__) == 0
        assert observed.verifier_sessions == [independent]
    finally:
        caller.close()
        environment.engine.dispose()


def test_real_unknown_commit_resolves_original_ids_without_second_debit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    environment = _joint_environment(
        tmp_path,
        model_source=ModelSource.SYSTEM,
        operation=AdmissionOperation.IMAGE_SINGLE.value,
    )
    caller = _joint_caller(environment.engine, unknown_after_commit=True)
    observed = _observe_joint_public_paths(
        monkeypatch,
        authentication_sessions=environment.authentication_sessions,
    )
    try:
        staged = _joint_prepare_and_stage(
            environment.binding,
            caller,
            operation=AdmissionOperation.IMAGE_SINGLE.value,
            raw_key="joint-image-single-unknown-commit",
            request=_joint_request(AdmissionOperation.IMAGE_SINGLE.value),
        )
        assert isinstance(staged, AdmissionStaged)
        assert observed.producer_sessions == [caller]
        assert observed.fee_sessions == [caller]
        original_ids = tuple(environment.server_ids.values)
        unknown = retain_unknown_commit(staged)
        assert isinstance(unknown, AdmissionCommitUnknown)
        assert not isinstance(lookup_staged_admission(caller, staged), AdmissionConfirmed)
        with Session(environment.engine) as independent:
            before_commit = lookup_unknown_commit(independent, unknown)
            assert isinstance(before_commit, AdmissionCommitUnknown)
            independent.rollback()

        with pytest.raises(RuntimeError, match="lost the commit acknowledgement"):
            caller.commit()

        assert caller.commit_calls == 1
        assert caller.rollback_calls == 0
        assert tuple(environment.server_ids.values) == original_ids
        with Session(environment.engine) as independent:
            confirmed = lookup_unknown_commit(independent, unknown)
            assert isinstance(confirmed, AdmissionConfirmed)
            assert confirmed.source == "durable_lookup"
            assert confirmed.submission_id == staged.submission_id
            assert _joint_receipt_task_ids(
                confirmed.receipt, staged.operation
            ) == tuple(
                task.task_id for task in staged.ordered_tasks
            )
            assert _joint_count(independent, CreditLogRow.__table__) == 1
            balance = independent.execute(
                select(UserCreditRow.__table__.c.credits).where(
                    UserCreditRow.__table__.c.user_id == _G2_JOINT_ACTOR_ID
                )
            ).scalar_one()
            assert balance == 497
        assert len(observed.a2_calls) == 1
        assert observed.a2_calls[0][0] is caller
        assert observed.verifier_sessions == [independent]
    finally:
        caller.close()
        environment.engine.dispose()


def test_original_replay_ignores_changed_catalog_membership_and_credential_head(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    operation = AdmissionOperation.IMAGE_SINGLE.value
    environment = _joint_environment(
        tmp_path,
        model_source=ModelSource.SYSTEM,
        operation=operation,
    )
    first_caller = _joint_caller(environment.engine)
    observed = _observe_joint_public_paths(
        monkeypatch,
        authentication_sessions=environment.authentication_sessions,
    )
    request = _joint_request(operation)
    replay_caller: _JointCallerSession | None = None
    try:
        original = _joint_prepare_and_stage(
            environment.binding,
            first_caller,
            operation=operation,
            raw_key="joint-image-single-original-replay",
            request=request,
        )
        assert isinstance(original, AdmissionStaged)
        assert observed.producer_sessions == [first_caller]
        assert len(observed.a2_calls) == 1
        assert type(observed.a2_calls[0][1]) is AdmissionPreparationFacts
        assert len(observed.graph_plans) == 1
        assert observed.fee_sessions == [first_caller]
        first_caller.commit()

        environment.capabilities = _joint_rotate_system_credential(environment)
        with environment.engine.connect() as connection:
            assert connection.execute(
                select(users.c.membership_type).where(
                    users.c.id == _G2_JOINT_ACTOR_ID
                )
            ).scalar_one() == "free"
            current_model = connection.execute(
                select(system_models.c.name, system_models.c.model, system_models.c.cost_per_call)
                .where(system_models.c.id == _G2_JOINT_MODEL_ID)
            ).one()
            assert current_model == (
                "Changed current catalog label",
                "changed-current-wire-model",
                99,
            )
            assert connection.execute(
                select(credential_bindings.c.current_version).where(
                    credential_bindings.c.source == ModelSource.SYSTEM.value,
                    credential_bindings.c.row_id == _G2_JOINT_MODEL_ID,
                    credential_bindings.c.owner_or_system == "system",
                    credential_bindings.c.provider == _G2_JOINT_PROVIDER,
                )
            ).scalar_one() == 2
        environment.binding = _joint_build_binding(
            environment.authentication,
            environment.capabilities,
            environment.server_ids,
            operation=operation,
        )
        ids_before_replay = tuple(environment.server_ids.values)
        crypto_before_replay = _joint_crypto_counts(environment.crypto_state)

        replay_caller = _joint_caller(environment.engine)
        current_state_reads: list[str] = []

        def observe_current_state_reads(
            _connection,
            _cursor,
            statement: str,
            _parameters,
            _context,
            _executemany: bool,
        ) -> None:
            lowered = statement.lower()
            if any(
                table in lowered
                for table in (
                    "system_models",
                    "model_configs",
                    "generation_credential_bindings",
                    "generation_credential_versions",
                    "generation_credential_principals",
                )
            ):
                current_state_reads.append(lowered)

        event.listen(
            environment.engine,
            "before_cursor_execute",
            observe_current_state_reads,
        )
        try:
            replay = _joint_prepare_and_stage(
                environment.binding,
                replay_caller,
                operation=operation,
                raw_key="joint-image-single-original-replay",
                request=request,
            )
        finally:
            event.remove(
                environment.engine,
                "before_cursor_execute",
                observe_current_state_reads,
            )

        assert isinstance(replay, AdmissionConfirmed)
        assert replay.source == "original_replay"
        assert replay.submission_id == original.submission_id
        assert _joint_receipt_task_ids(replay.receipt, operation) == tuple(
            task.task_id for task in original.ordered_tasks
        )
        assert observed.producer_sessions == [first_caller, replay_caller]
        assert len(observed.a2_calls) == 2
        assert observed.a2_calls[1][0] is replay_caller
        assert type(observed.a2_calls[1][1]) is AdmissionReplay
        assert len(observed.graph_plans) == 1
        assert observed.fee_sessions == [first_caller]
        assert observed.verifier_sessions == []
        assert current_state_reads == []
        assert tuple(environment.server_ids.values) == ids_before_replay
        assert _joint_crypto_counts(environment.crypto_state) == crypto_before_replay
        assert replay_caller.commit_calls == replay_caller.rollback_calls == 0
        with Session(environment.engine) as independent:
            assert _joint_count(independent, TaskSubmissionRow.__table__) == 1
            assert _joint_count(independent, CreditLogRow.__table__) == 1
            balance = independent.execute(
                select(UserCreditRow.__table__.c.credits).where(
                    UserCreditRow.__table__.c.user_id == _G2_JOINT_ACTOR_ID
                )
            ).scalar_one()
            assert balance == 497
    finally:
        first_caller.close()
        if replay_caller is not None:
            replay_caller.rollback()
            replay_caller.close()
        environment.engine.dispose()


def test_real_fee_refusal_preserves_caller_rollback_responsibility(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    environment = _joint_environment(
        tmp_path,
        model_source=ModelSource.SYSTEM,
        operation=AdmissionOperation.IMAGE_SINGLE.value,
    )
    caller = _joint_caller(environment.engine)
    observed = _observe_joint_public_paths(
        monkeypatch,
        authentication_sessions=environment.authentication_sessions,
    )
    real_stage_fees = admission_staging.stage_unit_fees
    fee_interpositions: list[Session] = []

    def make_wallet_insufficient(
        session: Session,
        *,
        actor_id: str,
        graph: OperationGraphPlan,
        id_factory: Any,
        now: datetime,
    ) -> object:
        fee_interpositions.append(session)
        session.execute(
            update(user_credits)
            .where(user_credits.c.user_id == actor_id)
            .values(credits=0)
        )
        return real_stage_fees(
            session,
            actor_id=actor_id,
            graph=graph,
            id_factory=id_factory,
            now=now,
        )

    monkeypatch.setattr(admission_staging, "stage_unit_fees", make_wallet_insufficient)
    try:
        refusal = _joint_prepare_and_stage(
            environment.binding,
            caller,
            operation=AdmissionOperation.IMAGE_SINGLE.value,
            raw_key="joint-image-single-insufficient-wallet",
            request=_joint_request(AdmissionOperation.IMAGE_SINGLE.value),
        )

        assert isinstance(refusal, AdmissionStageRefusal)
        assert refusal.code == "insufficient_credits"
        assert refusal.caller_must_abort is True
        assert fee_interpositions == [caller]
        assert observed.producer_sessions == [caller]
        assert observed.a2_calls[0][0] is caller
        assert len(observed.a2_calls) == len(observed.graph_plans) == 1
        assert observed.fee_sessions == [caller]
        assert _joint_count(caller, TaskSubmissionRow.__table__) == 1
        assert _joint_count(caller, AITaskRow.__table__) == 1
        assert _joint_count(caller, BillingUnitRow.__table__) == 1
        assert _joint_count(caller, ExecutionStepRow.__table__) == 1
        assert _joint_count(caller, prepared_payloads) == 1
        assert _joint_count(caller, CreditLogRow.__table__) == 0
        assert caller.commit_calls == caller.rollback_calls == 0

        caller.rollback()
        assert caller.rollback_calls == 1
        with Session(environment.engine) as independent:
            assert _joint_count(independent, TaskSubmissionRow.__table__) == 0
            assert _joint_count(independent, AITaskRow.__table__) == 0
            assert _joint_count(independent, BillingUnitRow.__table__) == 0
            assert _joint_count(independent, ExecutionStepRow.__table__) == 0
            assert _joint_count(independent, prepared_payloads) == 0
            assert _joint_count(independent, CreditLogRow.__table__) == 0
            balance = independent.execute(
                select(UserCreditRow.__table__.c.credits).where(
                    UserCreditRow.__table__.c.user_id == _G2_JOINT_ACTOR_ID
                )
            ).scalar_one()
            assert balance == 500
    finally:
        caller.close()
        environment.engine.dispose()
