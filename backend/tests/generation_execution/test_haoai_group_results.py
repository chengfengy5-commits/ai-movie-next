from __future__ import annotations

import hashlib
import json

import test_haoai_group_polls as group_fixtures
import test_video_single_claims as video_fixtures
from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from haoai_backend.chat_data.tables import chat_messages
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
    ResultEvidenceRow,
)
from haoai_backend.generation_core.haoai_group.polls import HaoaiGroupPollGrant
from haoai_backend.generation_core.haoai_group.media import SqlAlchemyHaoaiGroupMedia
from haoai_backend.generation_core.haoai_group.results import (
    HaoaiGroupResultOutcome,
    SqlAlchemyHaoaiGroupResults,
)
from haoai_backend.generation_core.haoai_group.polls import (
    HaoaiGroupPollGrant,
)
from haoai_backend.generation_core.schema import (
    ExecutionAttemptGroupRow,
    GenerationResultReceiptRow,
)
from haoai_backend.generation_contracts.types import StoredMedia
from haoai_backend.generation_contracts.types import ReferenceInput
from haoai_backend.media_storage.owned_write_values import (
    OwnedObjectPlan,
    OwnedObjectMaterial,
    OwnedObjectPutResult,
    OwnedObjectVerification,
)


def _owned_result(plan_digest: str, owner_id: str, submission_id: str):
    content = b"synthetic-owned-video"
    digest = hashlib.sha256(content).hexdigest()
    object_key = "haoai/group/result.mp4"
    public_base = "https://media.example/owned"
    plan = OwnedObjectPlan(
        write_id="a" * 32,
        owner_ref=owner_id,
        submission_ref=submission_id,
        slot="primary",
        provider_result_digest=plan_digest,
        material_sha256=digest,
        media_type="video/mp4",
        size_bytes=len(content),
        config_fingerprint="b" * 64,
        config_version=1,
        bucket="video-output",
        object_key=object_key,
        public_base=public_base,
    )
    stored = StoredMedia(
        locator=f"{public_base}/{object_key}",
        content_digest=digest,
        media_type="video/mp4",
        size_bytes=len(content),
    )
    verification = OwnedObjectVerification(
        status="verified",
        stored_media=stored,
        observed_sha256=digest,
        observed_size_bytes=len(content),
    )
    return plan, verification


def test_haoai_group_result_commits_owned_receipt_and_replays_without_new_fee(
    tmp_path,
    monkeypatch,
):
    environment = video_fixtures._video_environment(
        monkeypatch, tmp_path, provider="haoai"
    )
    try:
        task_id = group_fixtures._admit(environment, "haoai-group-result-owned-001")
        message_id = "haoai-group-result-message"
        with environment.engine.begin() as connection:
            connection.execute(
                insert(chat_messages).values(
                    id=message_id,
                    chapter_id="joint-chapter-1",
                    frame_index=None,
                    asset_type=None,
                    asset_id=None,
                    chat_mode="chat",
                    role="assistant",
                    content="正在生成视频...",
                    model_name=None,
                    created_at=video_fixtures._NOW.replace(tzinfo=None),
                )
            )
            connection.execute(
                update(AITaskRow.__table__)
                .where(AITaskRow.__table__.c.id == task_id)
                .values(message_id=message_id)
            )
        clock = [video_fixtures._NOW]
        claim, _, polls, resumed, external_ref = group_fixtures._setup_group(
            environment, task_id, clock
        )
        grant = polls.reserve_query(
            claim, resumed.intent, resumed.verified_input, external_ref
        )
        assert isinstance(grant, HaoaiGroupPollGrant)
        provider, _ = group_fixtures._provider(
            claim,
            resumed,
            body=(
                b'{"status":"completed","video_url":'
                b'"https://cdn.example/source-video.mp4"}'
            ),
        )
        poll_outcome = polls.query_and_accept(grant, provider)
        assert poll_outcome.disposition == "accepted"
        assert poll_outcome.observation is not None
        plan, verification = _owned_result(
            poll_outcome.observation.payload_digest,
            claim.scope.user_id,
            resumed.intent.external_submission_id,
        )
        assert environment.capabilities.proof_authority is not None
        results = SqlAlchemyHaoaiGroupResults(
            session_factory=lambda: Session(environment.engine),
            db_clock=lambda: clock[0],
            proof_authority=environment.capabilities.proof_authority,
        )

        content = b"synthetic-owned-video"

        class _ContentProvider:
            _binding = getattr(provider, "_binding")

            def query_existing(self, snapshot, external_ref):
                return provider.query_existing(snapshot, external_ref)

            def fetch_provider_content(self, _snapshot, _external_ref):
                return ReferenceInput(
                    kind="bytes",
                    locator=None,
                    content=content,
                    media_type="video/mp4",
                )

        class _OwnedStore:
            def __init__(self):
                self.remote: dict[str, bytes] = {}
                self.put_count = 0
                self.verify_count = 0

            def prepare_owned_bytes(
                self,
                _source,
                _object_policy,
                *,
                owner_ref,
                submission_ref,
                slot,
                provider_result_digest,
            ):
                assert plan.owner_ref == owner_ref
                assert plan.submission_ref == submission_ref
                assert plan.slot == slot
                assert plan.provider_result_digest == provider_result_digest
                return OwnedObjectMaterial(plan=plan, content=content)

            def put_planned_media(self, material, permit):
                self.put_count += 1
                if permit.claim(material.plan):
                    self.remote[material.plan.object_key] = material.content
                return OwnedObjectPutResult("stored")

            def verify_planned_media(self, owned_plan):
                self.verify_count += 1
                if self.remote.get(owned_plan.object_key) != content:
                    return OwnedObjectVerification("not_found_pending")
                return verification

        before_debits = _usage_log_ids(environment.engine, resumed.verified_input.unit_id)
        media = SqlAlchemyHaoaiGroupMedia(
            session_factory=lambda: Session(environment.engine),
            db_clock=lambda: clock[0],
            proof_authority=environment.capabilities.proof_authority,
            result_consumer=results,
        )
        store = _OwnedStore()
        first = media.materialize(
            claim,
            resumed.intent,
            resumed.verified_input,
            grant,
            poll_outcome,
            _ContentProvider(),
            store,
        )
        replay = results.complete(
            claim,
            resumed.intent,
            resumed.verified_input,
            grant,
            poll_outcome,
            plan,
            verification,
        )

        assert first.disposition == "completed"
        assert first.receipt is not None
        assert replay.disposition == "completed"
        assert replay.receipt == first.receipt
        assert store.put_count == 1
        assert store.verify_count == 1
        assert _usage_log_ids(environment.engine, resumed.verified_input.unit_id) == before_debits

        with Session(environment.engine) as reader:
            evidence = reader.execute(
                select(ResultEvidenceRow.__table__).where(
                    ResultEvidenceRow.__table__.c.evidence_key
                    == f"haoai-group-result-v1:{resumed.intent.external_submission_id}"
                )
            ).mappings().one()
            receipt_rows = reader.execute(
                select(GenerationResultReceiptRow.__table__).where(
                    GenerationResultReceiptRow.result_ref == first.receipt.result_ref
                )
            ).mappings().all()
            task = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == task_id)
            ).mappings().one()
            unit = reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.id == resumed.verified_input.unit_id
                )
            ).mappings().one()
            group = reader.execute(
                select(ExecutionAttemptGroupRow.__table__).where(
                    ExecutionAttemptGroupRow.unit_id == resumed.verified_input.unit_id
                )
            ).mappings().one()
            step = reader.execute(
                select(ExecutionStepRow.__table__).where(
                    ExecutionStepRow.id == resumed.intent.step_id
                )
            ).mappings().one()
            submission = reader.execute(
                select(ExternalSubmissionRow.__table__).where(
                    ExternalSubmissionRow.id == resumed.intent.external_submission_id
                )
            ).mappings().one()
            message = reader.execute(
                select(chat_messages.c.content).where(
                    chat_messages.c.id == task["message_id"],
                    chat_messages.c.role == "assistant",
                )
            ).scalar_one_or_none()

        assert evidence["evidence_kind"] == "provider_video"
        assert evidence["validity"] == "valid"
        assert evidence["result_location"] == verification.stored_media.locator
        assert len(receipt_rows) == 1
        assert task["status"] == "completed"
        assert json.loads(task["result"]) == {
            "kind": "video",
            "url": verification.stored_media.locator,
            "result_ref": first.receipt.result_ref,
            "content_digest": verification.stored_media.content_digest,
        }
        assert unit["result_status"] == "succeeded"
        assert unit["billing_status"] in {"charged", "no_charge"}
        assert unit["refunded_amount"] == 0
        assert group["status"] == "succeeded"
        assert group["selected_evidence_id"] == evidence["id"]
        assert group["outstanding_reservation_ref"] is None
        assert step["execution_status"] == "applied"
        assert submission["submission_status"] == "completed"
        assert message == verification.stored_media.locator
    finally:
        environment.engine.dispose()


def _usage_log_ids(engine, unit_id: str) -> tuple[str, ...]:
    with Session(engine) as reader:
        rows = reader.execute(
            select(CreditLogRow.id)
            .where(
                CreditLogRow.billing_unit_id == unit_id,
                CreditLogRow.type == "usage",
            )
            .order_by(CreditLogRow.id)
        ).scalars().all()
    return tuple(rows)


def test_haoai_group_owned_media_persists_plan_and_recovers_unknown_put_with_get_only(
    tmp_path,
    monkeypatch,
):
    environment = video_fixtures._video_environment(
        monkeypatch, tmp_path, provider="haoai"
    )
    try:
        task_id = group_fixtures._admit(environment, "haoai-group-media-owned-001")
        clock = [video_fixtures._NOW]
        claim, _, polls, resumed, external_ref = group_fixtures._setup_group(
            environment, task_id, clock
        )
        grant = polls.reserve_query(
            claim, resumed.intent, resumed.verified_input, external_ref
        )
        assert isinstance(grant, HaoaiGroupPollGrant)
        query_provider, _ = group_fixtures._provider(
            claim,
            resumed,
            body=(
                b'{"status":"completed","video_url":'
                b'"https://cdn.example/source-video.mp4"}'
            ),
        )
        poll_outcome = polls.query_and_accept(grant, query_provider)
        assert poll_outcome.disposition == "accepted"
        assert poll_outcome.observation is not None

        provider_binding = group_fixtures.provider_fixtures.HaoaiExecutionBinding(
            owner_scope=claim.scope,
            snapshot=resumed.verified_input.snapshot,
            intent=resumed.intent,
            credential_binding=resumed.verified_input.credential_binding,
        )
        content_provider, content_counts = (
            group_fixtures.provider_fixtures.make_provider(
                response=group_fixtures.provider_fixtures.FakeResponse(
                    200,
                    b"synthetic-owned-video",
                    headers={"Content-Type": "video/mp4"},
                ),
                binding=provider_binding,
                credential=resumed.verified_input.provider_credential,
            )
        )

        class _OwnedStore:
            def __init__(self):
                self.prepared: list[OwnedObjectMaterial] = []
                self.put_count = 0
                self.verify_count = 0
                self.remote: dict[str, bytes] = {}
                self.verify_unavailable_count = 1
                self.permit_claimed: list[bool] = []

            def prepare_owned_bytes(
                self,
                source,
                object_policy,
                *,
                owner_ref,
                submission_ref,
                slot,
                provider_result_digest,
            ):
                assert type(source) is ReferenceInput
                assert source.content is not None
                assert object_policy == {"max_bytes": 50 * 1024 * 1024}
                content = source.content
                digest = hashlib.sha256(content).hexdigest()
                plan = OwnedObjectPlan(
                    write_id="c" * 32,
                    owner_ref=owner_ref,
                    submission_ref=submission_ref,
                    slot=slot,
                    provider_result_digest=provider_result_digest,
                    material_sha256=digest,
                    media_type=source.media_type,
                    size_bytes=len(content),
                    config_fingerprint="d" * 64,
                    config_version=1,
                    bucket="video-output",
                    object_key="haoai/group/media-plan.mp4",
                    public_base="https://media.example/owned",
                )
                material = OwnedObjectMaterial(plan=plan, content=content)
                self.prepared.append(material)
                return material

            def put_planned_media(self, material, permit):
                self.put_count += 1
                claimed = permit.claim(material.plan)
                self.permit_claimed.append(claimed)
                if claimed:
                    self.remote[material.plan.object_key] = material.content
                return OwnedObjectPutResult("unknown")

            def verify_planned_media(self, plan):
                self.verify_count += 1
                if self.verify_unavailable_count:
                    self.verify_unavailable_count -= 1
                    return OwnedObjectVerification(
                        "unavailable", safe_detail="synthetic read unavailable"
                    )
                content = self.remote.get(plan.object_key)
                if content is None:
                    return OwnedObjectVerification("not_found_pending")
                digest = hashlib.sha256(content).hexdigest()
                stored = StoredMedia(
                    locator=f"{plan.public_base}/{plan.object_key}",
                    content_digest=digest,
                    media_type=plan.media_type,
                    size_bytes=len(content),
                )
                return OwnedObjectVerification(
                    "verified",
                    stored_media=stored,
                    observed_sha256=digest,
                    observed_size_bytes=len(content),
                )

        result_consumer = SqlAlchemyHaoaiGroupResults(
            session_factory=lambda: Session(environment.engine),
            db_clock=lambda: clock[0],
            proof_authority=environment.capabilities.proof_authority,
        )
        media = SqlAlchemyHaoaiGroupMedia(
            session_factory=lambda: Session(environment.engine),
            db_clock=lambda: clock[0],
            proof_authority=environment.capabilities.proof_authority,
            result_consumer=result_consumer,
        )
        store = _OwnedStore()

        uncertain = media.materialize(
            claim,
            resumed.intent,
            resumed.verified_input,
            grant,
            poll_outcome,
            content_provider,
            store,
        )
        assert uncertain.disposition == "pending"
        assert store.put_count == 1
        assert store.permit_claimed == [True]
        assert store.verify_count == 1

        recovered = media.materialize(
            claim,
            resumed.intent,
            resumed.verified_input,
            grant,
            poll_outcome,
            content_provider,
            store,
        )
        assert recovered.disposition == "completed"
        assert recovered.receipt is not None
        assert store.put_count == 1
        assert store.verify_count == 2
        assert len(content_counts["client"].calls) == 1
        assert content_counts["client"].calls[0]["method"] == "GET"
        assert content_counts["client"].calls[0]["url"].endswith(
            f"/openai/v1/videos/{external_ref.external_id}/content"
        )

        with Session(environment.engine) as reader:
            controls = reader.execute(
                select(ResultEvidenceRow.__table__).where(
                    ResultEvidenceRow.__table__.c.evidence_kind.in_(
                        {
                            "haoai_group_media_plan_v2",
                            "haoai_group_media_put_started_v2",
                        }
                    )
                )
            ).mappings().all()
            task = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == task_id)
            ).mappings().one()
        assert {row["evidence_kind"] for row in controls} == {
            "haoai_group_media_plan_v2",
            "haoai_group_media_put_started_v2",
        }
        assert len(controls) == 2
        assert all(
            resumed.verified_input.provider_credential.secret
            not in row["result_location"]
            and "cdn.example/source-video.mp4" not in row["result_location"]
            for row in controls
        )
        assert task["status"] == "completed"
    finally:
        environment.engine.dispose()


def test_haoai_group_canonical_complete_requires_persisted_original_media_plan(
    tmp_path,
    monkeypatch,
):
    environment = video_fixtures._video_environment(
        monkeypatch, tmp_path, provider="haoai"
    )
    try:
        task_id = group_fixtures._admit(
            environment, "haoai-group-result-reject-unplanned-001"
        )
        clock = [video_fixtures._NOW]
        claim, _, polls, resumed, external_ref = group_fixtures._setup_group(
            environment, task_id, clock
        )
        grant = polls.reserve_query(
            claim, resumed.intent, resumed.verified_input, external_ref
        )
        assert isinstance(grant, HaoaiGroupPollGrant)
        provider, _ = group_fixtures._provider(
            claim,
            resumed,
            body=(
                b'{"status":"completed","video_url":'
                b'"https://cdn.example/source-video.mp4"}'
            ),
        )
        observed = polls.query_and_accept(grant, provider)
        assert observed.observation is not None
        plan, verification = _owned_result(
            observed.observation.payload_digest,
            claim.scope.user_id,
            resumed.intent.external_submission_id,
        )
        assert environment.capabilities.proof_authority is not None
        results = SqlAlchemyHaoaiGroupResults(
            session_factory=lambda: Session(environment.engine),
            db_clock=lambda: clock[0],
            proof_authority=environment.capabilities.proof_authority,
        )

        outcome = results.complete(
            claim,
            resumed.intent,
            resumed.verified_input,
            grant,
            observed,
            plan,
            verification,
        )

        assert outcome.disposition == "pending"
        assert outcome.receipt is None
        with Session(environment.engine) as reader:
            task = reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == task_id)
            ).mappings().one()
            receipt_count = reader.execute(
                select(GenerationResultReceiptRow.__table__).where(
                    GenerationResultReceiptRow.task_id == task_id
                )
            ).mappings().all()
        assert task["status"] == "processing"
        assert receipt_count == []
    finally:
        environment.engine.dispose()
