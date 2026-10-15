from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime
from weakref import ref

import pytest

from sqlalchemy import create_engine, insert, select, update
from sqlalchemy.orm import Session

from haoai_backend.chat_data.tables import chapters, chat_messages, metadata as chat_metadata
from haoai_backend.generation_catalog.admission_types import (
    AdmissionPreparationFacts,
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
from haoai_backend.generation_contracts.types import (
    AdmissionFingerprint,
    EndpointPolicy,
    FrozenExecutionSnapshot,
    FrozenGrantSnapshot,
    ModelReference,
    TargetSnapshot,
)
from haoai_backend.generation_credentials import snapshot_producer
from haoai_backend.generation_credentials.domain import PreparedPayloadStaged
from haoai_backend.generation_credentials.snapshot_producer import (
    LegacyMessageProjection,
    PayloadVerificationResult,
    RequestDataProjection,
    SnapshotPayloadItem,
    SnapshotPayloadsStaged,
)
from haoai_backend.generation_core.admission_receipts import (
    AdmissionCommitUnknown,
    AdmissionConfirmed,
    AdmissionStaged,
    StagedTaskExpectation,
    lookup_staged_admission,
    lookup_unknown_commit,
    retain_unknown_commit,
    verify_original_replay,
)
from haoai_backend.generation_core.admission_staging import (
    MediaSnapshotProducerBinding,
)
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    Base,
    BillingUnitRow,
    CreditLogRow,
    ExecutionStepRow,
    TaskSubmissionRow,
    UserCreditRow,
    UserRow,
)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class _ProducerSpy:
    def __init__(self, *, status: str = "verified") -> None:
        self.status = status
        self.calls: list[tuple[Session, object]] = []


_producer_spies: dict[int, _ProducerSpy] = {}


def _verify_staged_payloads_spy(
    producer: object, read_session: Session, expected: SnapshotPayloadsStaged
) -> PayloadVerificationResult:
    spy = _producer_spies[id(producer)]
    spy.calls.append((read_session, expected))
    item_count = len(expected.items) if spy.status == "verified" else 0
    return PayloadVerificationResult(status=spy.status, item_count=item_count)


@pytest.fixture(autouse=True)
def _install_producer_verifier_spy(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        snapshot_producer.MediaSnapshotProducer,
        "verify_staged_payloads",
        _verify_staged_payloads_spy,
    )
    yield
    _producer_spies.clear()


def _producer_spy_for(producer: object) -> _ProducerSpy:
    return _producer_spies[id(producer)]


def _producer_contract(
    *,
    session: Session,
    status: str = "verified",
    parent_id: str,
    actor_id: str,
    digest: str,
    quote: int,
    attempt_id: str,
    payload_id: str,
    protected_ref: str,
) -> tuple[MediaSnapshotProducerBinding, SnapshotPayloadsStaged, _ProducerSpy]:
    # This spy checks delegation of the original typed value; AEAD and HMAC
    # verification remain the production producer's responsibility.
    producer = _ProducerSpy(status=status)
    prepared_fingerprint = AdmissionFingerprint(
        schema_version=CONTEXT_SCHEMA_VERSION,
        ordered_nonsecret_payload_digest=digest,
        actor_id=actor_id,
        operation="image.batch",
    )
    display_name = "Display model"
    model_id = "model-1"
    size = "1024x1024"
    target_snapshot = TargetSnapshot(
        kind="asset_message",
        chapter_id="chapter-1",
        series_id="series-1",
        asset_type="character",
        asset_id="asset-1",
    )
    model = ModelSelection(
        catalog_id=model_id,
        category=ModelCategory.IMAGE,
        source=ModelSource.SYSTEM,
        display_name=display_name,
        wire_model="wire-image-model",
        quote=ServerQuote(
            category=ModelCategory.IMAGE,
            source=ModelSource.SYSTEM,
            amount=quote,
            effective_parameters={"size": size},
        ),
    )
    preparation = AdmissionPreparationFacts(
        operation="image.batch",
        fingerprint=prepared_fingerprint,
        phase="prepared",
        items=(
            PreparedItemFacts(
                index=0,
                model=model,
                credential_configured=True,
                target=LockedTargetFacts(
                    kind="asset_batch",
                    series_id="series-1",
                    chapter_id="chapter-1",
                    asset_type="character",
                    asset_id="asset-1",
                ),
                prompt=None,
                quote_amount=quote,
                effective_parameters={"size": size},
            ),
        ),
        quoted_total=quote,
        required_balance=quote,
        available_balance=100,
        reserved_amount=0,
        executable=False,
    )
    reference = f"{protected_ref}#references"
    snapshot = FrozenExecutionSnapshot(
        model_ref=ModelReference(
            owner_id="system",
            provider="provider-x",
            model_id=model_id,
        ),
        model_category="image",
        model_name="wire-image-model",
        model_params={"size": size},
        prompt_ref=None,
        effective_prompt="frozen prompt",
        effective_context="",
        server_quote=quote,
        reference_snapshot_ref=reference,
        target_snapshot=target_snapshot,
        credential_binding_ref="credential-binding-1",
        grant_snapshot=FrozenGrantSnapshot(
            actor_id=actor_id,
            series_id="series-1",
            access_provenance=("trusted-fixture",),
        ),
        adapter_config_versions=(("provider-x", "adapter-v1"),),
        endpoint_policy=EndpointPolicy(provider_origin="https://provider.invalid"),
    )
    context = {
        "modelId": model_id,
        "promptId": "",
        "size": size,
        "refImagesCount": 2,
        "refImagesDigest": "d" * 64,
    }
    user_content = f"__CTX__:{json.dumps(context, ensure_ascii=False)}\n原始描述"
    projection = RequestDataProjection(
        operation="image.batch",
        submission_id=parent_id,
        request_digest=digest,
        rule_digest="b" * 64,
        mode="batch_image",
        model_id=model_id,
        model_source="system",
        model_owner_id=None,
        prompt_id=None,
        size=size,
        chapter_id="chapter-1",
        asset_type="character",
        asset_id="asset-1",
        asset_name="original asset name",
        reference_images_version=1,
        reference_images_count=2,
        reference_images_digest="d" * 64,
        prepared_payload_id=payload_id,
        protected_ref=protected_ref,
        target_ref=f"{protected_ref}#target",
        model_ref=f"{protected_ref}#model",
        grant_ref=f"{protected_ref}#grant",
        target_snapshot=target_snapshot,
    )
    item = SnapshotPayloadItem(
        index=0,
        server_attempt_id=attempt_id,
        prepared_payload_id=payload_id,
        protected_ref=protected_ref,
        payload_staged=PreparedPayloadStaged(prepared_payload_id=payload_id, reused=False),
        snapshot=snapshot,
        legacy_message_projection=LegacyMessageProjection(
            user_content=user_content,
            assistant_content="正在生成图片...",
            assistant_wire_content=None,
            model_name=display_name,
            chapter_id="chapter-1",
            asset_type="character",
            asset_id="asset-1",
            asset_name="original asset name",
        ),
        request_data_projection=projection,
    )
    staged_result = SnapshotPayloadsStaged(
        parent_attempt_id=parent_id,
        actor_id=actor_id,
        operation="image.batch",
        graph_version="image-batch-v1",
        fingerprint=digest,
        manifest_digest="e" * 64,
        staging_session_ref=ref(session),
        preparation=preparation,
        items=(item,),
    )
    real_producer = object.__new__(snapshot_producer.MediaSnapshotProducer)
    _producer_spies[id(real_producer)] = producer
    return (
        MediaSnapshotProducerBinding(
            _producer=real_producer,
            _module=snapshot_producer,
            _server_id_factory=lambda: "unused",
        ),
        staged_result,
        producer,
    )


def _seed_batch(
    session: Session, *, quote: int, verifier_status: str = "verified"
) -> AdmissionStaged:
    owner_id = "owner-1"
    submission_id = "parent-attempt-1"
    task_id = "task-1"
    unit_id = "unit-1"
    step_id = "step-1"
    child_attempt_id = "child-attempt-1"
    payload_id = "payload-A"
    payload_ref = f"prepared-payload-v1/{len(payload_id)}:{payload_id}"
    context_message_id = "message-user-1"
    assistant_message_id = "message-assistant-1"
    raw_key = "request-key-1"
    request_digest = "a" * 64
    rule_digest = "b" * 64
    snapshot_digest = "c" * 64
    references_digest = "d" * 64
    now = datetime(2026, 10, 12, 8, 0, 0)
    context = {
        "modelId": "model-1",
        "promptId": "",
        "size": "1024x1024",
        "refImagesCount": 2,
        "refImagesDigest": references_digest,
    }
    user_content = f"__CTX__:{json.dumps(context, ensure_ascii=False)}\n原始描述"
    message_digest = _sha256(user_content)
    target_snapshot = {
        "kind": "asset_message",
        "chapter_id": "chapter-1",
        "series_id": "series-1",
        "stable_storyboard_asset_id": None,
        "asset_type": "character",
        "asset_id": "asset-1",
        "compat_frame_index": None,
        "expected_text_digest": None,
        "media_identity": None,
        "content_digest": None,
        "notes_revision": None,
        "full_frame_digest": None,
        "full_chapter_digest": None,
        "same_series_target_digest": None,
    }
    request_data = {
        "via": "worker",
        "mode": "batch_image",
        "operation": "image.batch",
        "submission_id": submission_id,
        "parent_attempt_id": submission_id,
        "server_attempt_id": child_attempt_id,
        "prepared_payload_id": payload_id,
        "protected_ref": payload_ref,
        "billing_unit_id": unit_id,
        "item_id": child_attempt_id,
        "unit_key": child_attempt_id,
        "step_graph_version": "image-batch-v1",
        "request_digest": request_digest,
        "rule_digest": rule_digest,
        "snapshot_digest": snapshot_digest,
        "model_id": "model-1",
        "model_source": "system",
        "model_owner_id": None,
        "model_display_name": "Display model",
        "prompt_id": None,
        "size": "1024x1024",
        "reference_images_version": 1,
        "reference_images_count": 2,
        "reference_images_digest": references_digest,
        "target_ref": f"{payload_ref}#target",
        "model_ref": f"{payload_ref}#model",
        "grant_ref": f"{payload_ref}#grant",
        "reference_ref": f"{payload_ref}#references",
        "target_snapshot": target_snapshot,
        "chapter_id": "chapter-1",
        "asset_type": "character",
        "asset_id": "asset-1",
        "asset_name": "original asset name",
        "context_message_id": context_message_id,
        "context_message_sha256": message_digest,
    }
    request_data_json = json.dumps(
        request_data, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )

    session.execute(insert(UserRow.__table__), [{"id": owner_id}])
    session.execute(
        insert(chapters), [{"id": "chapter-1", "series_id": "series-1"}]
    )
    session.execute(
        insert(TaskSubmissionRow.__table__),
        [
            {
                "id": submission_id,
                "user_id": owner_id,
                "operation": "image.batch",
                "idempotency_key": raw_key,
                "request_digest": request_digest,
                "task_ids": [task_id],
                "status": "accepted",
                "created_at": now,
            }
        ],
    )
    session.execute(
        insert(AITaskRow.__table__),
        [
            {
                "id": task_id,
                "user_id": owner_id,
                "type": "batch-image",
                "status": "queued",
                "message_id": assistant_message_id,
                "credit_cost": quote,
                "billing_status": "charged" if quote else "no_charge",
                "request_data": request_data_json,
                "model_name": "Display model",
                "created_at": now,
                "updated_at": now,
            }
        ],
    )
    session.execute(
        insert(BillingUnitRow.__table__),
        [
            {
                "id": unit_id,
                "task_id": task_id,
                "item_key": child_attempt_id,
                "operation": "image.batch",
                "step_graph_version": "image-batch-v1",
                "input_digest": request_digest,
                "quoted_amount": quote,
                "debited_amount": quote,
                "refunded_amount": 0,
                "billing_status": "charged" if quote else "no_charge",
                "result_status": "pending",
                "created_at": now,
                "updated_at": now,
            }
        ],
    )
    session.execute(
        insert(ExecutionStepRow.__table__),
        [
            {
                "id": step_id,
                "billing_unit_id": unit_id,
                "step_key": "generate",
                "dependencies": [],
                "input_digest": request_digest,
                "input_snapshot": {
                    "server_attempt_id": child_attempt_id,
                    "prepared_payload_id": payload_id,
                    "protected_ref": payload_ref,
                    "snapshot_digest": snapshot_digest,
                    "ordered_manifest_digest": "e" * 64,
                },
                "required": True,
                "step_order": 0,
                "execution_status": "prepared",
                "created_at": now,
                "updated_at": now,
            }
        ],
    )
    session.execute(
        insert(chat_messages),
        [
            {
                "id": context_message_id,
                "chapter_id": "chapter-1",
                "frame_index": None,
                "asset_type": "character",
                "asset_id": "asset-1",
                "chat_mode": "image",
                "role": "user",
                "content": user_content,
                "model_name": None,
                "created_at": now,
            },
            {
                "id": assistant_message_id,
                "chapter_id": "chapter-1",
                "frame_index": None,
                "asset_type": "character",
                "asset_id": "asset-1",
                "chat_mode": "image",
                "role": "assistant",
                "content": "正在生成图片...",
                "model_name": "Display model",
                "created_at": now,
            },
        ],
    )
    ledger_id = None
    business_key = None
    balance_after = None
    if quote:
        ledger_id = "ledger-1"
        business_key = f"debit:{unit_id}"
        balance_after = 17
        session.execute(
            insert(CreditLogRow.__table__),
            [
                {
                    "id": ledger_id,
                    "user_id": owner_id,
                    "amount": -quote,
                    "balance_after": balance_after,
                    "type": "usage",
                    "description": "AI task batch-image billing unit debit",
                    "task_id": task_id,
                    "business_key": business_key,
                    "billing_unit_id": unit_id,
                    "related_debit_id": None,
                    "created_at": now,
                }
            ],
        )
    producer_binding, producer_staged_result, _ = _producer_contract(
        session=session,
        status=verifier_status,
        parent_id=submission_id,
        actor_id=owner_id,
        digest=request_digest,
        quote=quote,
        attempt_id=child_attempt_id,
        payload_id=payload_id,
        protected_ref=payload_ref,
    )
    return AdmissionStaged(
        owner_id=owner_id,
        operation="image.batch",
        submission_id=submission_id,
        parent_attempt_id=submission_id,
        graph_version="image-batch-v1",
        raw_key=raw_key,
        request_digest=request_digest,
        ordered_tasks=(
            StagedTaskExpectation(
                task_id=task_id,
                unit_id=unit_id,
                step_ids=(step_id,),
                server_attempt_id=child_attempt_id,
                prepared_payload_id=payload_id,
                protected_ref=payload_ref,
                quoted_amount=quote,
                task_type="batch-image",
                model_display_name="Display model",
                request_data=request_data_json,
                message_ids=(context_message_id, assistant_message_id),
                ledger_id=ledger_id,
                business_key=business_key,
                balance_after=balance_after,
            ),
        ),
        producer_binding=producer_binding,
        producer_staged_result=producer_staged_result,
        staging_session_identity=id(session),
    )


def _seed_two_item_batch(session: Session) -> AdmissionStaged:
    staged = _seed_batch(session, quote=3)
    first = staged.ordered_tasks[0]
    first_data = json.loads(first.request_data)
    second_data = dict(first_data)
    second_data.update(
        {
            "server_attempt_id": "child-attempt-2",
            "prepared_payload_id": "payload-B",
            "protected_ref": "prepared-payload-v1/9:payload-B",
            "billing_unit_id": "unit-2",
            "item_id": "child-attempt-2",
            "unit_key": "child-attempt-2",
            "target_ref": "prepared-payload-v1/9:payload-B#target",
            "model_ref": "prepared-payload-v1/9:payload-B#model",
            "grant_ref": "prepared-payload-v1/9:payload-B#grant",
            "reference_ref": "prepared-payload-v1/9:payload-B#references",
            "asset_id": "asset-2",
            "asset_name": "second asset",
            "context_message_id": "message-user-2",
        }
    )
    second_data["target_snapshot"] = dict(second_data["target_snapshot"])
    second_data["target_snapshot"]["asset_id"] = "asset-2"
    context = {
        "modelId": "model-1",
        "promptId": "",
        "size": "1024x1024",
        "refImagesCount": 2,
        "refImagesDigest": "d" * 64,
    }
    user_content = f"__CTX__:{json.dumps(context, ensure_ascii=False)}\n第二项描述"
    second_data["context_message_sha256"] = _sha256(user_content)
    second_request_data = json.dumps(
        second_data, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )

    task_table = AITaskRow.__table__
    unit_table = BillingUnitRow.__table__
    step_table = ExecutionStepRow.__table__
    log_table = CreditLogRow.__table__
    message_table = chat_messages
    task = dict(
        session.execute(select(task_table).where(task_table.c.id == first.task_id))
        .mappings()
        .one()
    )
    task.update(
        {
            "id": "task-2",
            "message_id": "message-assistant-2",
            "request_data": second_request_data,
        }
    )
    unit = dict(
        session.execute(select(unit_table).where(unit_table.c.id == first.unit_id))
        .mappings()
        .one()
    )
    unit.update({"id": "unit-2", "task_id": "task-2", "item_key": "child-attempt-2"})
    step = dict(
        session.execute(select(step_table).where(step_table.c.id == first.step_ids[0]))
        .mappings()
        .one()
    )
    step["id"] = "step-2"
    step["billing_unit_id"] = "unit-2"
    step["input_snapshot"] = {
        "server_attempt_id": "child-attempt-2",
        "prepared_payload_id": "payload-B",
        "protected_ref": "prepared-payload-v1/9:payload-B",
        "snapshot_digest": "c" * 64,
        "ordered_manifest_digest": "e" * 64,
    }
    messages = session.execute(
        select(message_table).where(
            message_table.c.id.in_(("message-user-1", "message-assistant-1"))
        )
    ).mappings().all()
    second_messages = []
    for row in messages:
        message = dict(row)
        message["id"] = (
            "message-user-2"
            if row["role"] == "user"
            else "message-assistant-2"
        )
        message["asset_id"] = "asset-2"
        if row["role"] == "user":
            message["content"] = user_content
        second_messages.append(message)

    session.execute(
        update(TaskSubmissionRow.__table__)
        .where(TaskSubmissionRow.__table__.c.id == staged.submission_id)
        .values(task_ids=[first.task_id, "task-2"])
    )
    session.execute(insert(task_table), [task])
    session.execute(insert(unit_table), [unit])
    session.execute(insert(step_table), [step])
    session.execute(insert(message_table), second_messages)
    session.execute(
        insert(log_table),
        [
            {
                "id": "ledger-2",
                "user_id": staged.owner_id,
                "amount": -3,
                "balance_after": 14,
                "type": "usage",
                "description": "AI task batch-image billing unit debit",
                "task_id": "task-2",
                "business_key": "debit:unit-2",
                "billing_unit_id": "unit-2",
                "related_debit_id": None,
                "created_at": datetime(2026, 10, 12, 8, 0, 0),
            }
        ],
    )

    second = StagedTaskExpectation(
        task_id="task-2",
        unit_id="unit-2",
        step_ids=("step-2",),
        server_attempt_id="child-attempt-2",
        prepared_payload_id="payload-B",
        protected_ref="prepared-payload-v1/9:payload-B",
        quoted_amount=3,
        task_type="batch-image",
        model_display_name="Display model",
        request_data=second_request_data,
        message_ids=("message-user-2", "message-assistant-2"),
        ledger_id="ledger-2",
        business_key="debit:unit-2",
        balance_after=14,
    )
    original_payloads = staged.producer_staged_result
    original_item = original_payloads.items[0]
    second_payload_id = "payload-B"
    second_protected_ref = "prepared-payload-v1/9:payload-B"
    second_target = replace(
        original_item.snapshot.target_snapshot,
        asset_id="asset-2",
    )
    second_item = replace(
        original_item,
        index=1,
        server_attempt_id="child-attempt-2",
        prepared_payload_id=second_payload_id,
        protected_ref=second_protected_ref,
        payload_staged=PreparedPayloadStaged(
            prepared_payload_id=second_payload_id,
            reused=False,
        ),
        snapshot=replace(
            original_item.snapshot,
            target_snapshot=second_target,
            reference_snapshot_ref=f"{second_protected_ref}#references",
        ),
        legacy_message_projection=replace(
            original_item.legacy_message_projection,
            user_content=user_content,
            asset_id="asset-2",
            asset_name="second asset",
        ),
        request_data_projection=replace(
            original_item.request_data_projection,
            asset_id="asset-2",
            asset_name="second asset",
            prepared_payload_id=second_payload_id,
            protected_ref=second_protected_ref,
            target_ref=f"{second_protected_ref}#target",
            model_ref=f"{second_protected_ref}#model",
            grant_ref=f"{second_protected_ref}#grant",
            target_snapshot=second_target,
        ),
    )
    first_facts = original_payloads.preparation.items[0]
    second_facts = replace(
        first_facts,
        index=1,
        target=replace(first_facts.target, asset_id="asset-2"),
    )
    preparation = replace(
        original_payloads.preparation,
        items=(first_facts, second_facts),
        quoted_total=6,
        required_balance=6,
    )
    producer_staged_result = replace(
        original_payloads,
        preparation=preparation,
        items=(
            original_item,
            second_item,
        ),
    )
    return replace(
        staged,
        ordered_tasks=(first, second),
        producer_staged_result=producer_staged_result,
    )


def _database() -> tuple[Session, object]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    chat_metadata.create_all(engine)
    return Session(engine), engine


def test_independent_lookup_replays_original_order_and_asset_name() -> None:
    writer, engine = _database()
    reader = Session(engine)
    try:
        staged = _seed_batch(writer, quote=3)
        writer.commit()

        result = lookup_staged_admission(reader, staged)

        assert isinstance(result, AdmissionConfirmed)
        assert result.source == "durable_lookup"
        assert result.receipt.tasks[0].task_id == "task-1"
        assert result.receipt.tasks[0].asset_name == "original asset name"
        assert result.receipt.tasks[0].asset_id == "asset-1"
        assert result.receipt.tasks[0].message_id == "message-assistant-1"
    finally:
        reader.close()
        writer.close()
        engine.dispose()


def test_new_admission_requires_exact_manifest_digest_but_original_replay_accepts_legacy() -> None:
    writer, engine = _database()
    reader = Session(engine)
    try:
        staged = _seed_batch(writer, quote=3)
        step_table = ExecutionStepRow.__table__
        legacy_snapshot = dict(
            writer.execute(
                select(step_table.c.input_snapshot).where(step_table.c.id == "step-1")
            ).scalar_one()
        )
        legacy_snapshot.pop("ordered_manifest_digest")
        writer.execute(
            update(step_table)
            .where(step_table.c.id == "step-1")
            .values(input_snapshot=legacy_snapshot)
        )
        writer.commit()

        confirmed = verify_original_replay(
            reader,
            owner_id=staged.owner_id,
            operation=staged.operation,
            raw_key=staged.raw_key,
            request_digest=staged.request_digest,
        )
        assert isinstance(confirmed, AdmissionConfirmed)

        # The same legacy row can still replay its original receipt, but cannot
        # confirm a newly staged write as an executable snapshot.
        writer.rollback()
        writer.execute(
            update(step_table)
            .where(step_table.c.id == "step-1")
            .values(input_snapshot={
                **legacy_snapshot,
                "ordered_manifest_digest": "f" * 64,
            })
        )
        writer.commit()
        rejected = lookup_staged_admission(reader, staged)
        assert not isinstance(rejected, AdmissionConfirmed)
        assert rejected.code == "receipt_unverified"
    finally:
        reader.close()
        writer.close()
        engine.dispose()


@pytest.mark.parametrize(
    "mutation",
    ("missing", "invalid_type", "invalid_length", "unknown_key"),
)
def test_new_admission_requires_well_formed_exact_manifest_digest(mutation: str) -> None:
    writer, engine = _database()
    reader = Session(engine)
    try:
        staged = _seed_batch(writer, quote=3)
        step_table = ExecutionStepRow.__table__
        snapshot = dict(
            writer.execute(
                select(step_table.c.input_snapshot).where(step_table.c.id == "step-1")
            ).scalar_one()
        )
        if mutation == "missing":
            snapshot.pop("ordered_manifest_digest")
        elif mutation == "invalid_type":
            snapshot["ordered_manifest_digest"] = 17
        elif mutation == "invalid_length":
            snapshot["ordered_manifest_digest"] = "e" * 63
        else:
            snapshot["unrecognized"] = "extra"
        writer.execute(
            update(step_table)
            .where(step_table.c.id == "step-1")
            .values(input_snapshot=snapshot)
        )
        writer.commit()

        result = lookup_staged_admission(reader, staged)

        assert not isinstance(result, AdmissionConfirmed)
        assert result.code == "receipt_unverified"
    finally:
        reader.close()
        writer.close()
        engine.dispose()


@pytest.mark.parametrize("mutation", ("mixed_legacy", "different_valid_digest"))
def test_original_batch_replay_rejects_mixed_or_conflicting_manifest_digests(
    mutation: str,
) -> None:
    writer, engine = _database()
    reader = Session(engine)
    try:
        staged = _seed_two_item_batch(writer)
        step_table = ExecutionStepRow.__table__
        snapshot = dict(
            writer.execute(
                select(step_table.c.input_snapshot).where(step_table.c.id == "step-2")
            ).scalar_one()
        )
        if mutation == "mixed_legacy":
            snapshot.pop("ordered_manifest_digest")
        else:
            snapshot["ordered_manifest_digest"] = "f" * 64
        writer.execute(
            update(step_table)
            .where(step_table.c.id == "step-2")
            .values(input_snapshot=snapshot)
        )
        writer.commit()

        result = verify_original_replay(
            reader,
            owner_id=staged.owner_id,
            operation=staged.operation,
            raw_key=staged.raw_key,
            request_digest=staged.request_digest,
        )

        assert not isinstance(result, AdmissionConfirmed)
        assert result.code == "receipt_unverified"
    finally:
        reader.close()
        writer.close()
        engine.dispose()


def test_unknown_commit_lookup_keeps_original_attempt_and_zero_fee_has_no_ledger() -> None:
    writer, engine = _database()
    reader = Session(engine)
    try:
        staged = _seed_batch(writer, quote=0)
        writer.execute(
            insert(UserCreditRow.__table__),
            [{"id": "wallet-existing", "user_id": "owner-1", "credits": 12}],
        )
        writer.commit()
        unknown = retain_unknown_commit(staged)

        result = lookup_unknown_commit(reader, unknown)
        logs = reader.execute(select(CreditLogRow.__table__.c.id)).all()

        assert isinstance(result, AdmissionConfirmed)
        assert result.submission_id == staged.parent_attempt_id
        assert result.receipt.tasks[0].asset_name == "original asset name"
        assert logs == []
        assert reader.execute(
            select(UserCreditRow.__table__.c.credits).where(
                UserCreditRow.__table__.c.user_id == "owner-1"
            )
        ).scalar_one() == 12
    finally:
        reader.close()
        writer.close()
        engine.dispose()


def test_unknown_commit_confirms_after_worker_progress_and_assistant_update() -> None:
    writer, engine = _database()
    reader = Session(engine)
    try:
        staged = _seed_batch(writer, quote=3)
        writer.execute(
            update(AITaskRow.__table__)
            .where(AITaskRow.__table__.c.id == "task-1")
            .values(status="completed")
        )
        writer.execute(
            update(BillingUnitRow.__table__)
            .where(BillingUnitRow.__table__.c.id == "unit-1")
            .values(result_status="succeeded")
        )
        writer.execute(
            update(ExecutionStepRow.__table__)
            .where(ExecutionStepRow.__table__.c.id == "step-1")
            .values(execution_status="applied")
        )
        writer.execute(
            update(chat_messages)
            .where(chat_messages.c.id == "message-assistant-1")
            .values(content="图片已完成")
        )
        writer.commit()

        result = lookup_unknown_commit(reader, retain_unknown_commit(staged))

        assert isinstance(result, AdmissionConfirmed)
        assert result.receipt.tasks[0].message_id == "message-assistant-1"
        spy = _producer_spy_for(staged.producer_binding._producer)
        assert spy.calls == [(reader, staged.producer_staged_result)]
    finally:
        reader.close()
        writer.close()
        engine.dispose()


def test_unknown_commit_confirms_after_a_valid_full_refund() -> None:
    writer, engine = _database()
    reader = Session(engine)
    try:
        staged = _seed_batch(writer, quote=3)
        writer.execute(
            update(AITaskRow.__table__)
            .where(AITaskRow.__table__.c.id == "task-1")
            .values(status="failed", billing_status="refunded")
        )
        writer.execute(
            update(BillingUnitRow.__table__)
            .where(BillingUnitRow.__table__.c.id == "unit-1")
            .values(refunded_amount=3, billing_status="refunded", result_status="failed")
        )
        writer.execute(
            update(ExecutionStepRow.__table__)
            .where(ExecutionStepRow.__table__.c.id == "step-1")
            .values(execution_status="failed")
        )
        writer.execute(
            insert(CreditLogRow.__table__),
            [
                {
                    "id": "ledger-refund-1",
                    "user_id": "owner-1",
                    "amount": 3,
                    "balance_after": 20,
                    "type": "refund",
                    "description": "original unit refund",
                    "task_id": "task-1",
                    "business_key": "refund:ledger-1",
                    "billing_unit_id": "unit-1",
                    "related_debit_id": "ledger-1",
                    "created_at": datetime(2026, 10, 12, 8, 2, 0),
                }
            ],
        )
        writer.commit()

        result = lookup_unknown_commit(reader, retain_unknown_commit(staged))

        assert isinstance(result, AdmissionConfirmed)
        assert result.receipt.tasks[0].asset_name == "original asset name"
    finally:
        reader.close()
        writer.close()
        engine.dispose()


def test_unknown_payload_verification_remains_unknown_and_uses_original_typed_value() -> None:
    writer, engine = _database()
    reader = Session(engine)
    try:
        staged = _seed_batch(writer, quote=3, verifier_status="unresolved")
        writer.commit()

        result = lookup_unknown_commit(reader, retain_unknown_commit(staged))

        assert isinstance(result, AdmissionCommitUnknown)
        assert result.staged is staged
        assert _producer_spy_for(staged.producer_binding._producer).calls == [
            (reader, staged.producer_staged_result)
        ]
    finally:
        reader.close()
        writer.close()
        engine.dispose()


def test_unknown_commit_rejects_refund_ledgers_above_the_original_debit() -> None:
    writer, engine = _database()
    reader = Session(engine)
    try:
        staged = _seed_batch(writer, quote=3)
        writer.execute(
            update(AITaskRow.__table__)
            .where(AITaskRow.__table__.c.id == "task-1")
            .values(status="failed", billing_status="refunded")
        )
        writer.execute(
            update(BillingUnitRow.__table__)
            .where(BillingUnitRow.__table__.c.id == "unit-1")
            .values(refunded_amount=3, billing_status="refunded", result_status="failed")
        )
        writer.execute(
            update(ExecutionStepRow.__table__)
            .where(ExecutionStepRow.__table__.c.id == "step-1")
            .values(execution_status="failed")
        )
        writer.execute(
            insert(CreditLogRow.__table__),
            [
                {
                    "id": "ledger-refund-a",
                    "user_id": "owner-1",
                    "amount": 2,
                    "balance_after": 19,
                    "type": "refund",
                    "description": "refund part A",
                    "task_id": "task-1",
                    "business_key": "refund:ledger-refund-a",
                    "billing_unit_id": "unit-1",
                    "related_debit_id": "ledger-1",
                    "created_at": datetime(2026, 10, 12, 8, 2, 0),
                },
                {
                    "id": "ledger-refund-b",
                    "user_id": "owner-1",
                    "amount": 2,
                    "balance_after": 21,
                    "type": "refund",
                    "description": "refund part B",
                    "task_id": "task-1",
                    "business_key": "refund:ledger-refund-b",
                    "billing_unit_id": "unit-1",
                    "related_debit_id": "ledger-1",
                    "created_at": datetime(2026, 10, 12, 8, 3, 0),
                },
            ],
        )
        writer.commit()

        result = lookup_unknown_commit(reader, retain_unknown_commit(staged))

        assert not isinstance(result, AdmissionConfirmed)
        assert result.code == "receipt_unverified"
        assert _producer_spy_for(staged.producer_binding._producer).calls == []
    finally:
        reader.close()
        writer.close()
        engine.dispose()


def test_original_reference_version_preserves_integer_wire_type() -> None:
    writer, engine = _database()
    reader = Session(engine)
    try:
        staged = _seed_batch(writer, quote=3)
        request_data = json.loads(staged.ordered_tasks[0].request_data)
        request_data["reference_images_version"] = True
        writer.execute(
            update(AITaskRow.__table__)
            .where(AITaskRow.__table__.c.id == "task-1")
            .values(
                request_data=json.dumps(
                    request_data, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
            )
        )
        writer.commit()

        result = lookup_unknown_commit(reader, retain_unknown_commit(staged))

        assert not isinstance(result, AdmissionConfirmed)
        assert result.code == "receipt_unverified"
        assert _producer_spy_for(staged.producer_binding._producer).calls == []
    finally:
        reader.close()
        writer.close()
        engine.dispose()


def test_original_replay_does_not_run_the_payload_crypto_verifier() -> None:
    writer, engine = _database()
    reader = Session(engine)
    try:
        staged = _seed_batch(writer, quote=3)
        writer.commit()

        result = verify_original_replay(
            reader,
            owner_id=staged.owner_id,
            operation=staged.operation,
            raw_key=staged.raw_key,
            request_digest=staged.request_digest,
        )

        assert isinstance(result, AdmissionConfirmed)
        assert _producer_spy_for(staged.producer_binding._producer).calls == []
    finally:
        reader.close()
        writer.close()
        engine.dispose()


def test_original_replay_rejects_grouped_task_unit_step_digest_mutation() -> None:
    writer, engine = _database()
    reader = Session(engine)
    try:
        staged = _seed_batch(writer, quote=3)
        changed_digest = "e" * 64
        request_data = json.loads(staged.ordered_tasks[0].request_data)
        request_data["request_digest"] = changed_digest
        writer.execute(
            update(AITaskRow.__table__)
            .where(AITaskRow.__table__.c.id == "task-1")
            .values(
                request_data=json.dumps(
                    request_data, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
            )
        )
        writer.execute(
            update(BillingUnitRow.__table__)
            .where(BillingUnitRow.__table__.c.id == "unit-1")
            .values(input_digest=changed_digest)
        )
        writer.execute(
            update(ExecutionStepRow.__table__)
            .where(ExecutionStepRow.__table__.c.id == "step-1")
            .values(input_digest=changed_digest)
        )
        writer.commit()

        result = verify_original_replay(
            reader,
            owner_id=staged.owner_id,
            operation=staged.operation,
            raw_key=staged.raw_key,
            request_digest=staged.request_digest,
        )

        assert not isinstance(result, AdmissionConfirmed)
        assert result.code == "receipt_unverified"
        assert _producer_spy_for(staged.producer_binding._producer).calls == []
    finally:
        reader.close()
        writer.close()
        engine.dispose()


def test_original_replay_rejects_reused_child_attempt_and_payload_in_batch() -> None:
    writer, engine = _database()
    reader = Session(engine)
    try:
        staged = _seed_two_item_batch(writer)
        first = json.loads(staged.ordered_tasks[0].request_data)
        second = json.loads(staged.ordered_tasks[1].request_data)
        for field in (
            "server_attempt_id",
            "item_id",
            "unit_key",
            "prepared_payload_id",
            "protected_ref",
            "target_ref",
            "model_ref",
            "grant_ref",
            "reference_ref",
        ):
            second[field] = first[field]
        writer.execute(
            update(AITaskRow.__table__)
            .where(AITaskRow.__table__.c.id == "task-2")
            .values(
                request_data=json.dumps(
                    second, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
            )
        )
        writer.execute(
            update(BillingUnitRow.__table__)
            .where(BillingUnitRow.__table__.c.id == "unit-2")
            .values(item_key=first["unit_key"])
        )
        writer.execute(
            update(ExecutionStepRow.__table__)
            .where(ExecutionStepRow.__table__.c.id == "step-2")
            .values(
                input_snapshot={
                    "server_attempt_id": first["server_attempt_id"],
                    "prepared_payload_id": first["prepared_payload_id"],
                    "protected_ref": first["protected_ref"],
                    "snapshot_digest": first["snapshot_digest"],
                }
            )
        )
        writer.commit()

        result = verify_original_replay(
            reader,
            owner_id=staged.owner_id,
            operation=staged.operation,
            raw_key=staged.raw_key,
            request_digest=staged.request_digest,
        )

        assert not isinstance(result, AdmissionConfirmed)
        assert result.code == "receipt_unverified"
        assert _producer_spy_for(staged.producer_binding._producer).calls == []
    finally:
        reader.close()
        writer.close()
        engine.dispose()


def test_replay_rejects_user_owner_on_a_system_model_projection() -> None:
    writer, engine = _database()
    reader = Session(engine)
    try:
        staged = _seed_batch(writer, quote=3)
        request_data = json.loads(staged.ordered_tasks[0].request_data)
        request_data["model_owner_id"] = staged.owner_id
        writer.execute(
            update(AITaskRow.__table__)
            .where(AITaskRow.__table__.c.id == "task-1")
            .values(
                request_data=json.dumps(
                    request_data,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        )
        writer.commit()

        result = verify_original_replay(
            reader,
            owner_id=staged.owner_id,
            operation=staged.operation,
            raw_key=staged.raw_key,
            request_digest=staged.request_digest,
        )

        assert not isinstance(result, AdmissionConfirmed)
        assert result.code == "receipt_unverified"
        assert _producer_spy_for(staged.producer_binding._producer).calls == []
    finally:
        reader.close()
        writer.close()
        engine.dispose()


def test_replay_paths_reject_task_and_assistant_label_rewrites() -> None:
    writer, engine = _database()
    replay_reader = Session(engine)
    unknown_reader = Session(engine)
    try:
        staged = _seed_batch(writer, quote=3)
        writer.execute(
            update(AITaskRow.__table__)
            .where(AITaskRow.__table__.c.id == "task-1")
            .values(model_name="Other model")
        )
        writer.execute(
            update(chat_messages)
            .where(chat_messages.c.id == "message-assistant-1")
            .values(model_name="Other model")
        )
        writer.commit()

        replay_result = verify_original_replay(
            replay_reader,
            owner_id=staged.owner_id,
            operation=staged.operation,
            raw_key=staged.raw_key,
            request_digest=staged.request_digest,
        )
        replay_reader.close()
        unknown_result = lookup_unknown_commit(
            unknown_reader, retain_unknown_commit(staged)
        )

        for result in (replay_result, unknown_result):
            assert not isinstance(result, AdmissionConfirmed)
            assert result.code == "receipt_unverified"
        assert _producer_spy_for(staged.producer_binding._producer).calls == []
    finally:
        unknown_reader.close()
        replay_reader.close()
        writer.close()
        engine.dispose()


def test_writer_session_cannot_confirm_its_own_uncommitted_rows() -> None:
    writer, engine = _database()
    try:
        staged = _seed_batch(writer, quote=3)

        result = lookup_staged_admission(writer, staged)

        assert not isinstance(result, AdmissionConfirmed)
        assert result.code == "receipt_unverified"
    finally:
        writer.close()
        engine.dispose()
