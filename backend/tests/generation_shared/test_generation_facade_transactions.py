from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.errors import GenerationError
from haoai_backend.generation_contracts.records import GenerationResultReceipt
from haoai_backend.generation_contracts.types import (
    ClaimAuthority,
    ContinuationClaimAuthority,
    GenerationActor,
    OwnerTaskScope,
    TargetSnapshot,
    TextResult,
    ValidatedGenerationResult,
)
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
    ResultEvidenceRow,
)
from haoai_backend.generation_core.schema import (
    ApplicationRecordRow,
    FoundationBase,
    GenerationResultReceiptRow,
    LocalContinuationWorkRow,
)
from haoai_backend.series_data import stage_frame_result
from haoai_backend.series_data.tables import (
    chapters,
    metadata as series_metadata,
    series,
    storyboard_assets,
    users,
)
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.task_observation import GenerationResultApplicationFacade
from haoai_backend.personal_production.notes.tables import (
    metadata as notes_metadata,
    personal_production_notes,
    storyboard_media_states,
)


def _id() -> str:
    return str(uuid4())


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class _LiveClaim:
    def is_set(self) -> bool:
        return False


@dataclass
class FrameSeed:
    engine: object
    session: Session
    owner_id: str
    task_id: str
    unit_id: str
    step_id: str
    submission_id: str
    evidence_id: str
    result_ref: str
    chapter_id: str
    series_id: str
    storyboard_id: str
    original_frames: list[dict[str, object]]
    target: TargetSnapshot
    authority: ClaimAuthority
    validated: ValidatedGenerationResult
    receipt: GenerationResultReceipt


def _seed_frame_result(
    tmp_path: Path, *, target_frame: dict[str, object] | None = None, prior_preview: str | None = None
) -> FrameSeed:
    engine = create_engine(f"sqlite:///{tmp_path / 'generation-facade.sqlite'}")
    existing_tables = [table for table in series_metadata.sorted_tables if table.name != "ai_tasks"]
    series_metadata.create_all(engine, tables=existing_tables)
    FoundationBase.metadata.create_all(engine)
    notes_metadata.create_all(
        engine,
        tables=[storyboard_media_states, personal_production_notes],
    )

    now = datetime.now(timezone.utc)
    naive_now = now.replace(tzinfo=None)
    owner_id, task_id, unit_id = _id(), _id(), _id()
    step_id, submission_id, evidence_id, result_ref = (_id() for _ in range(4))
    chapter_id, series_id, storyboard_id = _id(), _id(), _id()
    old_frame = dict(target_frame or {"text": "原文", "storyboard": [storyboard_id]})
    if prior_preview is not None:
        old_frame["preview"] = prior_preview
    frames: list[dict[str, object]] = [old_frame]
    target = TargetSnapshot(
        kind="frame_text",
        chapter_id=chapter_id,
        series_id=series_id,
        stable_storyboard_asset_id=storyboard_id,
        expected_text_digest=_digest(old_frame["text"]),
        media_identity=storyboard_id,
        full_frame_digest=_digest(old_frame),
        full_chapter_digest=_digest(frames),
    )
    target_payload = asdict(target)
    frozen_input = {"target_snapshot": target_payload}
    content = "新的分镜内容"
    content_digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    deadline = naive_now + timedelta(hours=2)

    session = Session(engine, expire_on_commit=False)
    session.execute(users.insert().values(id=owner_id, username="owner", is_superuser=False))
    session.execute(
        series.insert().values(
            id=series_id,
            user_id=owner_id,
            name="series",
            created_at=naive_now,
            updated_at=naive_now,
        )
    )
    session.execute(
        chapters.insert().values(
            id=chapter_id,
            series_id=series_id,
            title="chapter",
            content=json.dumps(frames, ensure_ascii=False),
            order=1,
            created_at=naive_now,
            updated_at=naive_now,
        )
    )
    session.execute(
        storyboard_assets.insert().values(
            id=storyboard_id,
            series_id=series_id,
            chapter_id=chapter_id,
            frame_index=0,
            name="frame 1",
            description="原文",
            image_url=None,
            created_at=naive_now,
            updated_at=naive_now,
        )
    )
    session.add(
        AITaskRow(
            id=task_id,
            user_id=owner_id,
            type="ai-review",
            status="processing",
            claimed_by="worker-1",
            claim_token="claim-token",
            execution_generation=4,
            lease_until=naive_now + timedelta(minutes=10),
            recovery_status="ready",
            billing_status="unbilled",
            message_id=_id(),
            credit_cost=0,
            request_data=json.dumps(frozen_input),
            created_at=naive_now,
            updated_at=naive_now,
        )
    )
    session.flush()
    session.add(
        BillingUnitRow(
            id=unit_id,
            task_id=task_id,
            item_key="review-0",
            operation="frame.review",
            step_graph_version="frame-review-v1",
            input_digest="input-digest",
            quoted_amount=10,
            debited_amount=0,
            refunded_amount=0,
            billing_status="unbilled",
            result_status="succeeded",
            first_intent_at=now,
            deadline_at=deadline,
        )
    )
    session.flush()
    session.add(
        ExecutionStepRow(
            id=step_id,
            billing_unit_id=unit_id,
            step_key="generate",
            dependencies=[],
            input_digest="input-digest",
            input_snapshot=frozen_input,
            required=True,
            step_order=0,
            execution_status="result_observed",
            created_at=now,
            updated_at=now,
        )
    )
    session.add(
        ExternalSubmissionRow(
            id=submission_id,
            step_id=step_id,
            submission_key=f"submission:{task_id}",
            provider="test-provider",
            request_digest="input-digest",
            request_snapshot=frozen_input,
            intent_at=now,
            submitted_at=now,
            external_task_id="external-1",
            submission_status="submitted",
            deadline_at=deadline,
        )
    )
    session.flush()
    session.add(
        ResultEvidenceRow(
            id=evidence_id,
            step_id=step_id,
            submission_id=submission_id,
            evidence_key=f"evidence:{result_ref}",
            evidence_kind="provider_result",
            payload_digest="provider-payload-digest",
            result_location="managed://results/one",
            result_digest=content_digest,
            received_at=now,
            validity="valid",
            application_status="pending",
        )
    )
    session.add(
        GenerationResultReceiptRow(
            result_ref=result_ref,
            owner_id=owner_id,
            task_id=task_id,
            unit_id=unit_id,
            operation="frame.review",
            step_id=step_id,
            submission_id=submission_id,
            evidence_id=evidence_id,
            selected_attempt_id="attempt-1",
            recoverable_locator="managed://results/one",
            content_digest=content_digest,
            validation_version="validator-v1",
            validated_at=naive_now,
            generation_fulfilled_at=naive_now,
            late_disposition=None,
            policy_stop_disposition=None,
            automatic_application_allowed=True,
        )
    )
    session.add(
        LocalContinuationWorkRow(
            work_id=_id(),
            owner_id=owner_id,
            task_id=task_id,
            unit_id=unit_id,
            operation="frame.review",
            policy_version="local_continuation_v1",
            result_ref=result_ref,
            content_digest=content_digest,
            generation_fulfilled_at=naive_now,
            original_first_intent_at=naive_now,
            original_deadline_at=deadline,
            phase_order=["delivery_pending", "fee_pending", "finalize_pending"],
            phase="delivery_pending",
            phase_version=0,
            fee_decision_ref=None,
            application_ref=None,
            terminal_reason=None,
            last_execution_generation=None,
        )
    )
    session.commit()

    actor_scope = OwnerTaskScope(owner_id, task_id, "frame.review", TrustedActor(owner_id))
    authority = ClaimAuthority(
        scope=actor_scope,
        worker_instance="worker-1",
        token="claim-token",
        execution_generation=4,
        lease_until=now + timedelta(minutes=10),
        local_lost_signal=_LiveClaim(),
    )
    validated = ValidatedGenerationResult(
        result_ref=result_ref,
        observation_ref="observation-1",
        validation_version="validator-v1",
        recoverable_locator="managed://results/one",
        content_digest=content_digest,
        validated_at=now,
        generation_fulfilled_at=now,
        timely_success=True,
        selected_attempt_id="attempt-1",
        safe_output=TextResult(content),
        automatic_application_allowed=True,
    )
    receipt = GenerationResultReceipt(
        result_ref=result_ref,
        owner_id=owner_id,
        task_id=task_id,
        unit_id=unit_id,
        operation="frame.review",
        step_id=step_id,
        submission_id=submission_id,
        evidence_id=evidence_id,
        selected_attempt_id="attempt-1",
        recoverable_locator="managed://results/one",
        content_digest=content_digest,
        validation_version="validator-v1",
        validated_at=now,
        generation_fulfilled_at=now,
        late_disposition=None,
        policy_stop_disposition=None,
        automatic_application_allowed=True,
    )
    return FrameSeed(
        engine, session, owner_id, task_id, unit_id, step_id, submission_id,
        evidence_id, result_ref, chapter_id, series_id, storyboard_id, frames,
        target, authority, validated, receipt,
    )


def _read_chapter(engine: object, chapter_id: str) -> list[dict[str, object]]:
    with Session(engine) as observer:
        value = observer.execute(select(chapters.c.content).where(chapters.c.id == chapter_id)).scalar_one()
    return json.loads(value)


def test_worker_application_waits_for_the_callers_commit(tmp_path: Path) -> None:
    seeded = _seed_frame_result(tmp_path)
    facade = GenerationResultApplicationFacade(seeded.session)

    outcome = facade.apply_worker_result(
        seeded.session, seeded.authority, seeded.validated, seeded.target
    )

    assert outcome.kind == "applied"
    assert seeded.session.in_transaction()
    assert seeded.session.get(AITaskRow, seeded.task_id).status == "processing"
    assert seeded.session.get(ApplicationRecordRow, outcome.application_ref) is not None
    assert _read_chapter(seeded.engine, seeded.chapter_id)[0]["text"] == "原文"

    seeded.session.commit()

    assert _read_chapter(seeded.engine, seeded.chapter_id)[0]["text"] == "新的分镜内容"
    with Session(seeded.engine) as observer:
        app = observer.get(ApplicationRecordRow, outcome.application_ref)
        evidence = observer.get(ResultEvidenceRow, seeded.evidence_id)
        work = observer.execute(
            select(LocalContinuationWorkRow).where(LocalContinuationWorkRow.result_ref == seeded.result_ref)
        ).scalar_one()
        assert app is not None and app.delivery_status == "applied"
        assert evidence is not None and evidence.application_status == "applied"
        assert work.phase == "fee_pending" and work.phase_version == 1

    seeded.session.close()
    seeded.engine.dispose()


def test_continuation_first_application_requires_the_original_phase_version(
    tmp_path: Path,
) -> None:
    seeded = _seed_frame_result(tmp_path)
    work = seeded.session.execute(
        select(LocalContinuationWorkRow).where(
            LocalContinuationWorkRow.result_ref == seeded.result_ref
        )
    ).scalar_one()
    authority = ContinuationClaimAuthority(
        owner_scope=seeded.authority.scope,
        unit_id=seeded.unit_id,
        work_id=work.work_id,
        result_ref=seeded.result_ref,
        phase_version=work.phase_version + 1,
        worker_instance=seeded.authority.worker_instance,
        token=seeded.authority.token,
        execution_generation=seeded.authority.execution_generation,
        lease_until=seeded.authority.lease_until,
    )

    with pytest.raises(GenerationError, match="phase version"):
        stage_frame_result(seeded.session, authority, seeded.validated, seeded.target)

    seeded.session.rollback()
    assert _read_chapter(seeded.engine, seeded.chapter_id) == seeded.original_frames
    with Session(seeded.engine) as observer:
        assert observer.execute(select(ApplicationRecordRow)).scalars().all() == []
        assert observer.get(ResultEvidenceRow, seeded.evidence_id).application_status == "pending"
        current_work = observer.execute(
            select(LocalContinuationWorkRow).where(
                LocalContinuationWorkRow.result_ref == seeded.result_ref
            )
        ).scalar_one()
        assert current_work.phase == "delivery_pending" and current_work.phase_version == 0

    seeded.session.close()
    seeded.engine.dispose()


def test_caller_rollback_removes_source_media_notes_work_and_application(tmp_path: Path) -> None:
    seeded = _seed_frame_result(tmp_path)

    outcome = stage_frame_result(seeded.session, seeded.authority, seeded.validated, seeded.target)
    assert outcome.kind == "applied"
    assert seeded.session.get(ApplicationRecordRow, outcome.application_ref) is not None
    assert seeded.session.execute(
        select(storyboard_media_states.c.id).where(
            storyboard_media_states.c.chapter_id == seeded.chapter_id
        )
    ).first() is not None

    seeded.session.rollback()

    assert _read_chapter(seeded.engine, seeded.chapter_id)[0]["text"] == "原文"
    with Session(seeded.engine) as observer:
        assert observer.get(ApplicationRecordRow, outcome.application_ref) is None
        evidence = observer.get(ResultEvidenceRow, seeded.evidence_id)
        work = observer.execute(
            select(LocalContinuationWorkRow).where(LocalContinuationWorkRow.result_ref == seeded.result_ref)
        ).scalar_one()
        state_count = observer.execute(
            select(storyboard_media_states.c.id).where(
                storyboard_media_states.c.chapter_id == seeded.chapter_id
            )
        ).all()
        assert evidence is not None and evidence.application_status == "pending"
        assert work.phase == "delivery_pending" and work.phase_version == 0
        assert state_count == []

    seeded.session.close()
    seeded.engine.dispose()


def test_worker_fence_and_stale_target_are_rejected_without_source_changes(tmp_path: Path) -> None:
    seeded = _seed_frame_result(tmp_path)
    stale_authority = replace(seeded.authority, token="retired-token")

    with pytest.raises(GenerationError, match="fence is no longer current"):
        stage_frame_result(seeded.session, stale_authority, seeded.validated, seeded.target)
    seeded.session.rollback()

    changed = [{"text": "用户已编辑", "storyboard": [seeded.storyboard_id]}]
    with Session(seeded.engine) as editor:
        editor.execute(
            update(chapters)
            .where(chapters.c.id == seeded.chapter_id)
            .values(content=json.dumps(changed, ensure_ascii=False))
        )
        editor.commit()

    outcome = stage_frame_result(seeded.session, seeded.authority, seeded.validated, seeded.target)

    assert outcome.kind == "stale"
    seeded.session.rollback()
    assert _read_chapter(seeded.engine, seeded.chapter_id) == changed
    with Session(seeded.engine) as observer:
        assert observer.execute(select(ApplicationRecordRow)).scalars().all() == []
        assert observer.get(ResultEvidenceRow, seeded.evidence_id).application_status == "pending"

    seeded.session.close()
    seeded.engine.dispose()


def test_deleted_stable_target_is_reported_without_a_partial_write(tmp_path: Path) -> None:
    seeded = _seed_frame_result(tmp_path)
    with Session(seeded.engine) as editor:
        editor.execute(delete(storyboard_assets).where(storyboard_assets.c.id == seeded.storyboard_id))
        editor.commit()

    outcome = stage_frame_result(seeded.session, seeded.authority, seeded.validated, seeded.target)

    assert outcome.kind == "target_deleted"
    seeded.session.rollback()
    assert _read_chapter(seeded.engine, seeded.chapter_id) == seeded.original_frames

    seeded.session.close()
    seeded.engine.dispose()


def _watch_caller_session(monkeypatch: pytest.MonkeyPatch, caller: Session) -> dict[str, object]:
    from haoai_backend.personal_production.notes.persistence import SqlAlchemyNotesUnitOfWork

    events: dict[str, object] = {
        "other_sessions": [],
        "commit": 0,
        "rollback": 0,
        "close": 0,
        "uow_sessions": [],
    }
    session_type = type(caller)
    original_init = session_type.__init__

    def tracked_init(self, *args, **kwargs):
        if self is not caller:
            events["other_sessions"].append(self)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(session_type, "__init__", tracked_init)
    for name in ("commit", "rollback", "close"):
        original = getattr(session_type, name)

        def tracked_lifecycle(self, *args, _name=name, _original=original, **kwargs):
            if self is caller:
                events[_name] += 1
            return _original(self, *args, **kwargs)

        monkeypatch.setattr(session_type, name, tracked_lifecycle)

    original_uow_init = SqlAlchemyNotesUnitOfWork.__init__

    def tracked_uow_init(self, session, series_access_check):
        events["uow_sessions"].append(session)
        original_uow_init(self, session, series_access_check)

    monkeypatch.setattr(SqlAlchemyNotesUnitOfWork, "__init__", tracked_uow_init)
    return events


@pytest.mark.parametrize("surface", ("notes", "series", "asset"))
def test_public_media_adapters_map_missing_chapters_without_taking_session_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, surface: str
) -> None:
    from haoai_backend.asset_data.errors import AssetDataNotFound
    from haoai_backend.personal_production.notes import stage_media_reconciliation
    from haoai_backend.personal_production.notes.errors import NotesNotFound
    from haoai_backend.series_data.domain import ChapterRecord
    from haoai_backend.series_data.errors import SeriesDataNotFound

    seeded = _seed_frame_result(tmp_path)
    now = datetime.now(timezone.utc)
    missing_chapter = ChapterRecord(
        id=_id(),
        series_id=seeded.series_id,
        title="missing",
        content="[]",
        order=2,
        created_at=now,
        updated_at=now,
    )
    actor = TrustedActor(seeded.owner_id)
    if surface == "notes":
        operation = lambda: stage_media_reconciliation(seeded.session, missing_chapter.id)
        expected_error = NotesNotFound
    elif surface == "series":
        from haoai_backend.series_data.media_writes import reconcile_source_media_state

        operation = lambda: reconcile_source_media_state(seeded.session, missing_chapter, actor)
        expected_error = SeriesDataNotFound
    else:
        from haoai_backend.asset_data.media_writes import reconcile_source_media_state

        operation = lambda: reconcile_source_media_state(seeded.session, missing_chapter.id, actor)
        expected_error = AssetDataNotFound

    events = _watch_caller_session(monkeypatch, seeded.session)
    with pytest.raises(expected_error) as raised:
        operation()

    assert raised.value.detail == "章节不存在"
    assert seeded.session.in_transaction()
    assert events == {
        "other_sessions": [],
        "commit": 0,
        "rollback": 0,
        "close": 0,
        "uow_sessions": [seeded.session],
    }
    monkeypatch.undo()
    seeded.session.rollback()
    seeded.session.close()
    seeded.engine.dispose()


@pytest.mark.parametrize("surface", ("notes", "series", "asset"))
def test_public_media_adapters_preserve_coordinator_error_identity_and_session_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, surface: str
) -> None:
    from haoai_backend.personal_production.notes import facade as notes_facade
    from haoai_backend.personal_production.notes.errors import ChapterMediaStateError
    from haoai_backend.series_data.domain import ChapterRecord

    seeded = _seed_frame_result(tmp_path)
    now = datetime.now(timezone.utc)
    chapter = ChapterRecord(
        id=seeded.chapter_id,
        series_id=seeded.series_id,
        title="chapter",
        content=seeded.original_frames,
        order=1,
        created_at=now,
        updated_at=now,
    )
    actor = TrustedActor(seeded.owner_id)
    sentinel = ChapterMediaStateError("coordinator state must pass through unchanged")

    def fail_coordinator(*args, **kwargs):
        raise sentinel

    monkeypatch.setattr(notes_facade, "reconcile_chapter_media_state", fail_coordinator)
    if surface == "notes":
        operation = lambda: notes_facade.stage_media_reconciliation(
            seeded.session, seeded.chapter_id
        )
    elif surface == "series":
        from haoai_backend.series_data.media_writes import reconcile_source_media_state

        operation = lambda: reconcile_source_media_state(seeded.session, chapter, actor)
    else:
        from haoai_backend.asset_data.media_writes import reconcile_source_media_state

        operation = lambda: reconcile_source_media_state(seeded.session, seeded.chapter_id, actor)

    events = _watch_caller_session(monkeypatch, seeded.session)
    with pytest.raises(ChapterMediaStateError) as raised:
        operation()

    assert raised.value is sentinel
    assert seeded.session.in_transaction()
    assert events == {
        "other_sessions": [],
        "commit": 0,
        "rollback": 0,
        "close": 0,
        "uow_sessions": [seeded.session],
    }
    monkeypatch.undo()
    seeded.session.rollback()
    seeded.session.close()
    seeded.engine.dispose()



@pytest.mark.parametrize("revocation", ["task", "unit", "work"])
def test_worker_authority_refreshes_cached_rows_after_another_session_revokes(
    tmp_path: Path, revocation: str
) -> None:
    seeded = _seed_frame_result(tmp_path)
    cached_task = seeded.session.get(AITaskRow, seeded.task_id)
    cached_unit = seeded.session.get(BillingUnitRow, seeded.unit_id)
    cached_work = seeded.session.execute(
        select(LocalContinuationWorkRow).where(LocalContinuationWorkRow.result_ref == seeded.result_ref)
    ).scalar_one()
    assert cached_task is not None and cached_task.user_cancelled_at is None
    assert cached_unit is not None and cached_unit.user_cancelled_at is None
    assert cached_work.phase == "delivery_pending" and cached_work.phase_version == 0

    with Session(seeded.engine) as revoker:
        if revocation == "task":
            revoker.execute(
                update(AITaskRow)
                .where(AITaskRow.id == seeded.task_id)
                .values(user_cancelled_at=datetime.now(timezone.utc).replace(tzinfo=None))
            )
        elif revocation == "unit":
            revoker.execute(
                update(BillingUnitRow)
                .where(BillingUnitRow.id == seeded.unit_id)
                .values(user_cancelled_at=datetime.now(timezone.utc).replace(tzinfo=None))
            )
        else:
            revoker.execute(
                update(LocalContinuationWorkRow)
                .where(LocalContinuationWorkRow.work_id == cached_work.work_id)
                .values(phase="fee_pending", phase_version=1)
            )
        revoker.commit()

    # The original Session still holds these exact ORM objects and stale values.
    assert cached_task.user_cancelled_at is None
    assert cached_unit.user_cancelled_at is None
    assert cached_work.phase == "delivery_pending" and cached_work.phase_version == 0
    with pytest.raises(GenerationError):
        stage_frame_result(seeded.session, seeded.authority, seeded.validated, seeded.target)
    seeded.session.rollback()

    assert _read_chapter(seeded.engine, seeded.chapter_id) == seeded.original_frames
    with Session(seeded.engine) as observer:
        assert observer.execute(select(ApplicationRecordRow)).scalars().all() == []
        assert observer.get(ResultEvidenceRow, seeded.evidence_id).application_status == "pending"
        work = observer.execute(
            select(LocalContinuationWorkRow).where(LocalContinuationWorkRow.result_ref == seeded.result_ref)
        ).scalar_one()
        assert work.phase == ("fee_pending" if revocation == "work" else "delivery_pending")
        assert observer.execute(
            select(storyboard_media_states.c.id).where(storyboard_media_states.c.chapter_id == seeded.chapter_id)
        ).all() == []
        assert observer.execute(
            select(personal_production_notes.c.id).where(personal_production_notes.c.chapter_id == seeded.chapter_id)
        ).all() == []

    seeded.session.close()
    seeded.engine.dispose()


def test_worker_lease_is_rechecked_after_the_target_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seeded = _seed_frame_result(tmp_path)
    import haoai_backend.task_observation.generation as generation_module

    fresh_database_time = generation_module._fresh_database_time
    calls = 0

    def expire_after_target_lock(session: Session) -> datetime:
        nonlocal calls
        calls += 1
        return seeded.authority.lease_until + timedelta(seconds=1)

    monkeypatch.setattr(generation_module, "_fresh_database_time", expire_after_target_lock)

    with pytest.raises(GenerationError, match="expired while waiting for the target lock"):
        stage_frame_result(seeded.session, seeded.authority, seeded.validated, seeded.target)

    assert calls == 1
    seeded.session.rollback()
    assert _read_chapter(seeded.engine, seeded.chapter_id) == seeded.original_frames
    with Session(seeded.engine) as observer:
        assert observer.execute(select(ApplicationRecordRow)).scalars().all() == []
        assert observer.get(ResultEvidenceRow, seeded.evidence_id).application_status == "pending"
    seeded.session.close()
    seeded.engine.dispose()
