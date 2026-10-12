from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from haoai_backend.asset_data import digest_asset_target
from haoai_backend.chat_data import apply_web_text
from haoai_backend.generation_contracts.errors import GenerationError
from haoai_backend.generation_contracts.types import (
    CanvasApplyAuthority,
    CommittedStreamDebitReceipt,
    CommittedWebStartCapability,
    ContinuationClaimAuthority,
    GenerationActor,
    GenerationApplicationCommand,
    ModelReference,
    OwnerTaskScope,
    StreamDebitReceipt,
    StreamFeeAuthority,
    TargetSnapshot,
    TextResult,
    WebStartCapability,
    WebStreamAuthority,
)
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
    ResultEvidenceRow,
    UserCreditRow,
)
from haoai_backend.generation_core.schema import (
    ApplicationRecordRow,
    GenerationResultReceiptRow,
    LocalContinuationWorkRow,
    StreamFeeRecordRow,
    UnitFeeDecisionRow,
)
from haoai_backend.personal_production.notes.tables import (
    personal_production_notes,
    storyboard_media_states,
)
from haoai_backend.series_data import stage_frame_result
from haoai_backend.series_data.tables import (
    chapters,
    characters,
    chat_messages,
    storyboard_assets,
)
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.task_observation import (
    GenerationResultApplicationFacade,
    expected_target_digest,
    application_key_for,
    target_scope_for_snapshot,
)

from test_generation_facade_transactions import (
    FrameSeed,
    _watch_caller_session,
    _digest,
    _read_chapter,
    _seed_frame_result,
)


class _NoCancellation:
    def is_set(self) -> bool:
        return False


def _continuation_authority(
    seeded: FrameSeed, *, work_id: str | None = None, phase_version: int = 0
) -> ContinuationClaimAuthority:
    with Session(seeded.engine) as observer:
        work = observer.execute(
            select(LocalContinuationWorkRow).where(
                LocalContinuationWorkRow.result_ref == seeded.result_ref
            )
        ).scalar_one()
    return ContinuationClaimAuthority(
        owner_scope=seeded.authority.scope,
        unit_id=seeded.unit_id,
        work_id=work_id or work.work_id,
        result_ref=seeded.result_ref,
        phase_version=phase_version,
        worker_instance=seeded.authority.worker_instance,
        token=seeded.authority.token,
        execution_generation=seeded.authority.execution_generation,
        lease_until=seeded.authority.lease_until,
    )


def _continuation_effect_snapshot(seeded) -> tuple[object, ...]:
    models = (
        AITaskRow,
        BillingUnitRow,
        ExecutionStepRow,
        ExternalSubmissionRow,
        ResultEvidenceRow,
        GenerationResultReceiptRow,
        ApplicationRecordRow,
        LocalContinuationWorkRow,
        UnitFeeDecisionRow,
        CreditLogRow,
        UserCreditRow,
        StreamFeeRecordRow,
    )
    tables = (
        chapters,
        storyboard_assets,
        personal_production_notes,
        storyboard_media_states,
    )
    with Session(seeded.engine) as observer:
        model_rows = tuple(
            tuple(
                tuple(
                    (column.key, getattr(row, column.key))
                    for column in model.__table__.columns
                )
                for row in observer.execute(
                    select(model).order_by(*model.__table__.primary_key.columns)
                ).scalars()
            )
            for model in models
        )
        table_rows = tuple(
            tuple(
                tuple(row.items())
                for row in observer.execute(
                    select(table).order_by(*table.primary_key.columns)
                ).mappings()
            )
            for table in tables
        )
    return model_rows + table_rows


def test_committed_continuation_replay_keeps_edits_and_has_no_repeat_effects(
    tmp_path: Path,
) -> None:
    seeded = _seed_frame_result(tmp_path)
    authority = _continuation_authority(seeded)
    facade = GenerationResultApplicationFacade(seeded.session)

    first = facade.apply_worker_result(
        seeded.session, authority, seeded.validated, seeded.target
    )
    assert first.kind == "applied"
    seeded.session.commit()

    with Session(seeded.engine) as observer:
        work = observer.execute(
            select(LocalContinuationWorkRow).where(
                LocalContinuationWorkRow.result_ref == seeded.result_ref
            )
        ).scalar_one()
        assert work.phase == "fee_pending" and work.phase_version == 1
        assert work.application_ref == first.application_ref

    same_caller_replay = facade.apply_worker_result(
        seeded.session, authority, seeded.validated, seeded.target
    )
    assert same_caller_replay.kind == "already_applied"
    assert same_caller_replay.application_ref == first.application_ref
    seeded.session.rollback()

    with Session(seeded.engine) as fresh_caller:
        fresh_replay = GenerationResultApplicationFacade(fresh_caller).apply_worker_result(
            fresh_caller, authority, seeded.validated, seeded.target
        )
        assert fresh_replay.kind == "already_applied"
        assert fresh_replay.application_ref == first.application_ref
        fresh_caller.rollback()

    later_edit = [{"text": "用户后续编辑", "storyboard": [seeded.storyboard_id]}]
    with Session(seeded.engine) as editor:
        editor.execute(
            update(chapters)
            .where(chapters.c.id == seeded.chapter_id)
            .values(content=json.dumps(later_edit, ensure_ascii=False))
        )
        editor.execute(
            update(storyboard_assets)
            .where(storyboard_assets.c.id == seeded.storyboard_id)
            .values(description="用户后续编辑")
        )
        editor.commit()

    before_replays = _continuation_effect_snapshot(seeded)
    same_caller_after_edit = facade.apply_worker_result(
        seeded.session, authority, seeded.validated, seeded.target
    )
    assert same_caller_after_edit.kind == "already_applied"
    assert same_caller_after_edit.application_ref == first.application_ref
    seeded.session.rollback()

    with Session(seeded.engine) as fresh_caller:
        fresh_after_edit = GenerationResultApplicationFacade(fresh_caller).apply_worker_result(
            fresh_caller, authority, seeded.validated, seeded.target
        )
        assert fresh_after_edit.kind == "already_applied"
        assert fresh_after_edit.application_ref == first.application_ref
        fresh_caller.rollback()

    assert _continuation_effect_snapshot(seeded) == before_replays
    assert _read_chapter(seeded.engine, seeded.chapter_id) == later_edit
    with Session(seeded.engine) as observer:
        assert observer.execute(
            select(storyboard_assets.c.description).where(
                storyboard_assets.c.id == seeded.storyboard_id
            )
        ).scalar_one() == "用户后续编辑"

    seeded.session.close()
    seeded.engine.dispose()


@pytest.mark.parametrize(
    "revocation",
    [
        "task_cancelled",
        "unit_cancelled",
        "token_changed",
        "generation_changed",
        "lease_expired",
        "wrong_work",
        "wrong_phase",
        "wrong_version",
        "authority_phase_version_changed",
        "work_execution_generation_changed",
        "application_binding_changed",
    ],
)
def test_committed_continuation_replay_still_requires_current_fence_and_phase(
    tmp_path: Path, revocation: str
) -> None:
    from datetime import timedelta

    from sqlalchemy import update

    seeded = _seed_frame_result(tmp_path)
    authority = _continuation_authority(seeded)
    first = stage_frame_result(seeded.session, authority, seeded.validated, seeded.target)
    assert first.kind == "applied"
    seeded.session.commit()

    with Session(seeded.engine) as mutator:
        if revocation == "task_cancelled":
            mutator.execute(
                update(AITaskRow)
                .where(AITaskRow.id == seeded.task_id)
                .values(user_cancelled_at=datetime.now(timezone.utc).replace(tzinfo=None))
            )
        elif revocation == "unit_cancelled":
            mutator.execute(
                update(BillingUnitRow)
                .where(BillingUnitRow.id == seeded.unit_id)
                .values(user_cancelled_at=datetime.now(timezone.utc).replace(tzinfo=None))
            )
        elif revocation == "token_changed":
            mutator.execute(
                update(AITaskRow)
                .where(AITaskRow.id == seeded.task_id)
                .values(claim_token="replacement-token")
            )
        elif revocation == "generation_changed":
            mutator.execute(
                update(AITaskRow)
                .where(AITaskRow.id == seeded.task_id)
                .values(execution_generation=authority.execution_generation + 1)
            )
        elif revocation == "lease_expired":
            mutator.execute(
                update(AITaskRow)
                .where(AITaskRow.id == seeded.task_id)
                .values(lease_until=(datetime.now(timezone.utc) - timedelta(seconds=1)).replace(tzinfo=None))
            )
        elif revocation == "wrong_phase":
            mutator.execute(
                update(LocalContinuationWorkRow)
                .where(LocalContinuationWorkRow.work_id == authority.work_id)
                .values(phase="finalize_pending", phase_version=2)
            )
        elif revocation == "wrong_version":
            mutator.execute(
                update(LocalContinuationWorkRow)
                .where(LocalContinuationWorkRow.work_id == authority.work_id)
                .values(phase_version=2)
            )
        elif revocation == "work_execution_generation_changed":
            mutator.execute(
                update(LocalContinuationWorkRow)
                .where(LocalContinuationWorkRow.work_id == authority.work_id)
                .values(last_execution_generation=authority.execution_generation + 1)
            )
        elif revocation == "application_binding_changed":
            wrong_application_ref = str(uuid4())
            mutator.add(
                ApplicationRecordRow(
                    application_ref=wrong_application_ref,
                    result_ref=seeded.result_ref,
                    owner_id=seeded.owner_id,
                    task_id=seeded.task_id,
                    unit_id=seeded.unit_id,
                    target_scope=(
                        target_scope_for_snapshot(seeded.target) + ":other"
                    ),
                    result_digest=seeded.receipt.content_digest,
                    delivery_status="applied",
                    before_target_digest=expected_target_digest(seeded.target),
                    after_target_digest="other-target-digest",
                    committed_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )
            )
            mutator.flush()
            mutator.execute(
                update(LocalContinuationWorkRow)
                .where(LocalContinuationWorkRow.work_id == authority.work_id)
                .values(application_ref=wrong_application_ref)
            )
        mutator.commit()

    if revocation == "wrong_work":
        replay_authority = _continuation_authority(seeded, work_id=str(uuid4()))
    elif revocation == "authority_phase_version_changed":
        replay_authority = _continuation_authority(seeded, phase_version=3)
    else:
        replay_authority = authority
    before_replay = _continuation_effect_snapshot(seeded)
    with pytest.raises(GenerationError):
        stage_frame_result(
            seeded.session, replay_authority, seeded.validated, seeded.target
        )
    seeded.session.rollback()
    assert _continuation_effect_snapshot(seeded) == before_replay
    assert _read_chapter(seeded.engine, seeded.chapter_id)[0]["text"] == "新的分镜内容"

    seeded.session.close()
    seeded.engine.dispose()


@pytest.mark.parametrize(
    ("target_change", "expected_kind"),
    [("stable_identity_removed", "target_conflict"), ("asset_deleted", "target_deleted")],
)
def test_committed_continuation_replay_rejects_missing_target_identity(
    tmp_path: Path, target_change: str, expected_kind: str
) -> None:
    seeded = _seed_frame_result(tmp_path)
    authority = _continuation_authority(seeded)
    first = stage_frame_result(seeded.session, authority, seeded.validated, seeded.target)
    assert first.kind == "applied"
    seeded.session.commit()

    with Session(seeded.engine) as editor:
        if target_change == "stable_identity_removed":
            editor.execute(
                update(chapters)
                .where(chapters.c.id == seeded.chapter_id)
                .values(content=json.dumps([{"text": "目标身份已删除", "storyboard": []}]))
            )
        else:
            editor.execute(
                delete(storyboard_assets).where(storyboard_assets.c.id == seeded.storyboard_id)
            )
        editor.commit()

    before_replay = _continuation_effect_snapshot(seeded)
    replay = stage_frame_result(
        seeded.session, authority, seeded.validated, seeded.target
    )
    assert replay.kind == expected_kind
    seeded.session.rollback()
    assert _continuation_effect_snapshot(seeded) == before_replay

    seeded.session.close()
    seeded.engine.dispose()


def test_exact_committed_worker_replay_precedes_stale_snapshot_compare_and_set(tmp_path: Path) -> None:
    seeded = _seed_frame_result(tmp_path)
    first = stage_frame_result(seeded.session, seeded.authority, seeded.validated, seeded.target)
    assert first.kind == "applied"
    seeded.session.commit()

    later_edit = [{"text": "用户后续编辑", "storyboard": [seeded.storyboard_id]}]
    with Session(seeded.engine) as editor:
        editor.execute(
            update(chapters)
            .where(chapters.c.id == seeded.chapter_id)
            .values(content=json.dumps(later_edit, ensure_ascii=False))
        )
        editor.commit()

    replay = GenerationResultApplicationFacade(seeded.session).apply_worker_result(
        seeded.session, seeded.authority, seeded.validated, seeded.target
    )

    assert replay.kind == "already_applied"
    assert replay.application_ref == first.application_ref
    assert _read_chapter(seeded.engine, seeded.chapter_id) == later_edit
    seeded.session.rollback()
    seeded.session.close()
    seeded.engine.dispose()



def test_committed_frame_replay_requires_the_stable_frame_to_still_exist(tmp_path: Path) -> None:
    seeded = _seed_frame_result(tmp_path)
    first = stage_frame_result(seeded.session, seeded.authority, seeded.validated, seeded.target)
    assert first.kind == "applied"
    seeded.session.commit()

    edited_without_target = [{"text": "用户删除了原分镜身份", "storyboard": []}]
    with Session(seeded.engine) as editor:
        editor.execute(
            update(chapters)
            .where(chapters.c.id == seeded.chapter_id)
            .values(content=json.dumps(edited_without_target, ensure_ascii=False))
        )
        editor.commit()

    replay = GenerationResultApplicationFacade(seeded.session).apply_worker_result(
        seeded.session, seeded.authority, seeded.validated, seeded.target
    )

    assert replay.kind == "target_conflict"
    seeded.session.rollback()
    assert _read_chapter(seeded.engine, seeded.chapter_id) == edited_without_target
    with Session(seeded.engine) as observer:
        apps = observer.execute(select(ApplicationRecordRow)).scalars().all()
        assert len(apps) == 1 and apps[0].application_ref == first.application_ref
    seeded.session.close()
    seeded.engine.dispose()


def test_stable_storyboard_id_allows_unrelated_chapter_change_without_rewriting_siblings(tmp_path: Path) -> None:
    seeded = _seed_frame_result(tmp_path)
    target_frame = seeded.original_frames[0]
    sibling = {"text": "新添的其他分镜", "storyboard": []}
    current_frames = [target_frame, sibling]
    with Session(seeded.engine) as editor:
        editor.execute(
            update(chapters)
            .where(chapters.c.id == seeded.chapter_id)
            .values(content=json.dumps(current_frames, ensure_ascii=False))
        )
        editor.commit()

    outcome = stage_frame_result(seeded.session, seeded.authority, seeded.validated, seeded.target)

    assert outcome.kind == "applied"
    seeded.session.commit()
    final_frames = _read_chapter(seeded.engine, seeded.chapter_id)
    assert final_frames[0]["storyboard"] == [seeded.storyboard_id]
    assert final_frames[0]["text"] == "新的分镜内容"
    assert final_frames[1] == sibling
    with Session(seeded.engine) as observer:
        asset = observer.execute(
            select(storyboard_assets.c.description).where(
                storyboard_assets.c.id == seeded.storyboard_id
            )
        ).scalar_one()
        assert asset == "新的分镜内容"
    seeded.session.close()
    seeded.engine.dispose()


def _seed_completed_asset_application(seeded, now: datetime) -> tuple[CanvasApplyAuthority, GenerationApplicationCommand, str]:
    actor = GenerationActor(seeded.owner_id, TrustedActor(seeded.owner_id))
    naive_now = now.replace(tzinfo=None)
    asset_id = str(uuid4())
    row = {
        "id": asset_id,
        "series_id": seeded.series_id,
        "name": "hero",
        "aliases": None,
        "canonical_key": None,
        "gender": None,
        "age": None,
        "role": None,
        "appearance": None,
        "description": "original description",
        "image_url": None,
        "audio_url": None,
        "voice_ref": None,
    }
    seeded.session.execute(
        characters.insert().values(
            **row,
            created_at=naive_now,
            updated_at=naive_now,
        )
    )
    target = TargetSnapshot(
        kind="canvas_asset_image",
        series_id=seeded.series_id,
        asset_type="character",
        asset_id=asset_id,
        expected_asset_image_url=None,
        same_series_target_digest=digest_asset_target("character", row),
    )
    payload = {"target_snapshot": asdict(target)}
    task = seeded.session.get(AITaskRow, seeded.task_id)
    task.type = "image-single"
    task.status = "completed"
    task.request_data = json.dumps(payload)
    seeded.session.get(BillingUnitRow, seeded.unit_id).operation = "image.single"
    seeded.session.get(ExecutionStepRow, seeded.step_id).input_snapshot = payload
    seeded.session.get(ExternalSubmissionRow, seeded.submission_id).request_snapshot = payload
    from haoai_backend.generation_core.schema import GenerationResultReceiptRow, LocalContinuationWorkRow

    seeded.session.get(GenerationResultReceiptRow, seeded.result_ref).operation = "image.single"
    seeded.session.execute(
        delete(LocalContinuationWorkRow).where(
            LocalContinuationWorkRow.result_ref == seeded.result_ref
        )
    )
    seeded.session.flush()
    authority = CanvasApplyAuthority(
        actor=actor,
        task_id=seeded.task_id,
        result_ref=seeded.result_ref,
        operation="image.single",
        target=target,
        result_digest=seeded.receipt.content_digest,
    )
    command = GenerationApplicationCommand(
        task_id=seeded.task_id,
        result_ref=seeded.result_ref,
        expected_target=target,
        application_key=application_key_for(
            seeded.result_ref, target_scope_for_snapshot(target)
        ),
    )
    return authority, command, asset_id


def test_canvas_asset_apply_is_same_transaction_and_exact_replay_ignores_old_digest(tmp_path: Path) -> None:
    seeded = _seed_frame_result(tmp_path)
    authority, command, asset_id = _seed_completed_asset_application(
        seeded, datetime.now(timezone.utc)
    )
    seeded.session.commit()

    facade = GenerationResultApplicationFacade(seeded.session)
    invalid_key = facade.apply_canvas_media(
        seeded.session, authority, replace(command, application_key="wrong-key")
    )
    assert invalid_key.kind == "target_conflict"
    unchanged = seeded.session.execute(
        select(characters.c.image_url).where(characters.c.id == asset_id)
    ).scalar_one()
    assert unchanged is None

    first = facade.apply_canvas_media(seeded.session, authority, command)
    assert first.kind == "applied"
    assert seeded.session.in_transaction()
    seeded.session.commit()
    seeded.session.close()

    with Session(seeded.engine) as editor:
        editor.execute(
            update(characters)
            .where(characters.c.id == asset_id)
            .values(description="edited after successful application")
        )
        editor.commit()

    replay_session = Session(seeded.engine, expire_on_commit=False)
    replay_facade = GenerationResultApplicationFacade(replay_session)
    loaded = replay_facade.load_owned_completed_result(
        replay_session, authority.actor, seeded.task_id, seeded.result_ref
    )
    assert not isinstance(loaded, str) and loaded.operation == "image.single"
    applied_record = replay_facade.lookup_applied_result(
        OwnerTaskScope(
            seeded.owner_id, seeded.task_id, "image.single", authority.actor.trusted_account_identity
        ),
        seeded.result_ref,
    )
    assert applied_record is not None and applied_record.application_ref == first.application_ref
    replay = replay_facade.apply_canvas_media(replay_session, authority, command)

    assert replay.kind == "already_applied"
    assert replay.application_ref == first.application_ref
    with Session(seeded.engine) as observer:
        asset = observer.execute(
            select(characters.c.image_url, characters.c.description).where(characters.c.id == asset_id)
        ).one()
        assert asset.image_url == "managed://results/one"
        assert asset.description == "edited after successful application"
    replay_session.rollback()
    with Session(seeded.engine) as editor:
        editor.execute(delete(characters).where(characters.c.id == asset_id))
        editor.commit()

    deleted_target_replay = replay_facade.apply_canvas_media(replay_session, authority, command)

    assert deleted_target_replay.kind == "target_deleted"
    replay_session.rollback()
    with Session(seeded.engine) as observer:
        assert observer.execute(select(characters.c.id).where(characters.c.id == asset_id)).first() is None
        apps = observer.execute(select(ApplicationRecordRow)).scalars().all()
        assert len(apps) == 1 and apps[0].application_ref == first.application_ref
    replay_session.close()
    seeded.engine.dispose()


def _seed_web_stream(seeded) -> tuple[WebStreamAuthority, str, str]:
    message_id = seeded.session.get(AITaskRow, seeded.task_id).message_id
    now = datetime.now(timezone.utc)
    naive_now = now.replace(tzinfo=None)
    attempt_id = "stream-attempt-1"
    token = "single-use-token"
    seeded.session.execute(
        chat_messages.insert().values(
            id=message_id,
            chapter_id=seeded.chapter_id,
            frame_index=0,
            asset_type=None,
            asset_id=None,
            chat_mode="series",
            role="assistant",
            content="draft answer",
            model_name="test-model",
            created_at=naive_now,
        )
    )
    seeded.session.add(
        StreamFeeRecordRow(
            server_attempt_id=attempt_id,
            owner_id=seeded.owner_id,
            operation="text.stream",
            frozen_model_provenance="test-provider/test-model",
            quoted_amount=0,
            disposition="zero",
            actual_debited_amount=0,
            original_debit_log_id=None,
            original_business_key=None,
            settlement_state="committed",
            start_state="start_exposed",
            start_token=token,
            task_id=seeded.task_id,
            message_id=message_id,
            created_at=naive_now,
            updated_at=naive_now,
        )
    )
    actor = GenerationActor(seeded.owner_id, TrustedActor(seeded.owner_id))
    fee_authority = StreamFeeAuthority(
        actor=actor,
        server_attempt_id=attempt_id,
        operation="text.stream",
        frozen_model_ref=ModelReference(seeded.owner_id, "test-provider", "test-model"),
        frozen_quote=0,
        disposition="zero",
    )
    receipt = StreamDebitReceipt(
        actor_id=seeded.owner_id,
        server_attempt_id=attempt_id,
        operation="text.stream",
        quoted_amount=0,
        disposition="zero",
        actual_debited_amount=0,
        original_debit_log_id=None,
        original_business_key=None,
        commit_state="committed",
        task_id=seeded.task_id,
        message_id=message_id,
    )
    authority = WebStreamAuthority(
        actor=actor,
        fee_authority=fee_authority,
        confirmed_receipt=CommittedStreamDebitReceipt(receipt, now),
        task_id=seeded.task_id,
        message_id=message_id,
        start_capability=CommittedWebStartCapability(
            WebStartCapability(attempt_id, token), now
        ),
        cancellation_signal=_NoCancellation(),
    )
    seeded.session.commit()
    return authority, attempt_id, message_id


def test_web_text_bridge_checks_original_start_and_stages_message_in_callers_session(tmp_path: Path) -> None:
    seeded = _seed_frame_result(tmp_path)
    authority, _attempt_id, message_id = _seed_web_stream(seeded)

    outcome = apply_web_text(seeded.session, authority, TextResult("completed answer"))

    assert outcome.kind == "applied"
    with Session(seeded.engine) as observer:
        content = observer.execute(
            select(chat_messages.c.content).where(chat_messages.c.id == message_id)
        ).scalar_one()
        assert content == "draft answer"
    seeded.session.commit()
    with Session(seeded.engine) as observer:
        content = observer.execute(
            select(chat_messages.c.content).where(chat_messages.c.id == message_id)
        ).scalar_one()
        assert content == "completed answer"
    seeded.session.close()
    seeded.engine.dispose()


@pytest.mark.parametrize("revocation", ["stream", "task"])
def test_web_text_bridge_refreshes_cached_stream_and_task_authority(
    tmp_path: Path, revocation: str
) -> None:
    seeded = _seed_frame_result(tmp_path)
    authority, attempt_id, message_id = _seed_web_stream(seeded)
    cached_stream = seeded.session.get(StreamFeeRecordRow, attempt_id)
    cached_task = seeded.session.get(AITaskRow, seeded.task_id)
    assert cached_stream is not None and cached_stream.start_token == "single-use-token"
    assert cached_task is not None and cached_task.status == "processing"

    with Session(seeded.engine) as revoker:
        if revocation == "stream":
            revoker.execute(
                update(StreamFeeRecordRow)
                .where(StreamFeeRecordRow.server_attempt_id == attempt_id)
                .values(start_token="replaced-start-token")
            )
        else:
            revoker.execute(
                update(AITaskRow).where(AITaskRow.id == seeded.task_id).values(status="failed")
            )
        revoker.commit()

    assert cached_stream.start_token == "single-use-token"
    assert cached_task.status == "processing"
    outcome = apply_web_text(seeded.session, authority, TextResult("completed answer"))

    assert outcome.kind == "write_failed"
    seeded.session.rollback()
    with Session(seeded.engine) as observer:
        assert observer.execute(
            select(chat_messages.c.content).where(chat_messages.c.id == message_id)
        ).scalar_one() == "draft answer"
        assert observer.execute(
            select(ApplicationRecordRow.application_ref).where(ApplicationRecordRow.result_ref == seeded.result_ref)
        ).all() == []
        persisted_stream = observer.get(StreamFeeRecordRow, attempt_id)
        persisted_task = observer.get(AITaskRow, seeded.task_id)
        assert persisted_stream is not None
        assert persisted_task is not None
        if revocation == "stream":
            assert persisted_stream.start_token == "replaced-start-token"
            assert persisted_task.status == "processing"
        else:
            assert persisted_stream.start_token == "single-use-token"
            assert persisted_task.status == "failed"
    seeded.session.close()
    seeded.engine.dispose()


@pytest.mark.parametrize("existing_media_state", [False, True], ids=["cold-state", "existing-state"])
def test_canvas_frame_preview_stages_media_revision_with_source_in_one_transaction(
    tmp_path: Path, existing_media_state: bool
) -> None:
    prior_preview = None if existing_media_state else "managed://prior-preview"
    seeded = _seed_frame_result(tmp_path, prior_preview=prior_preview)
    if existing_media_state:
        naive_now = datetime.now(timezone.utc).replace(tzinfo=None)
        seeded.session.execute(
            storyboard_media_states.insert().values(
                id=str(uuid4()),
                chapter_id=seeded.chapter_id,
                storyboard_asset_id=seeded.storyboard_id,
                media_revision=1,
                asset_image_digest=None,
                preview_digest=None,
                source_valid=True,
                created_at=naive_now,
                updated_at=naive_now,
            )
        )
    target = TargetSnapshot(
        kind="canvas_frame_preview",
        chapter_id=seeded.chapter_id,
        series_id=seeded.series_id,
        stable_storyboard_asset_id=seeded.storyboard_id,
        media_identity=seeded.storyboard_id,
        full_frame_digest=_digest(seeded.original_frames[0]),
        full_chapter_digest=_digest(seeded.original_frames),
    )
    payload = {"target_snapshot": asdict(target)}
    task = seeded.session.get(AITaskRow, seeded.task_id)
    task.type = "image-single"
    task.status = "completed"
    task.request_data = json.dumps(payload)
    seeded.session.get(BillingUnitRow, seeded.unit_id).operation = "image.single"
    seeded.session.get(ExecutionStepRow, seeded.step_id).input_snapshot = payload
    seeded.session.get(ExternalSubmissionRow, seeded.submission_id).request_snapshot = payload
    from haoai_backend.generation_core.schema import GenerationResultReceiptRow, LocalContinuationWorkRow

    seeded.session.get(GenerationResultReceiptRow, seeded.result_ref).operation = "image.single"
    seeded.session.execute(
        delete(LocalContinuationWorkRow).where(
            LocalContinuationWorkRow.result_ref == seeded.result_ref
        )
    )
    actor = GenerationActor(seeded.owner_id, TrustedActor(seeded.owner_id))
    authority = CanvasApplyAuthority(
        actor=actor,
        task_id=seeded.task_id,
        result_ref=seeded.result_ref,
        operation="image.single",
        target=target,
        result_digest=seeded.receipt.content_digest,
    )
    command = GenerationApplicationCommand(
        task_id=seeded.task_id,
        result_ref=seeded.result_ref,
        expected_target=target,
        application_key=application_key_for(
            seeded.result_ref, target_scope_for_snapshot(target)
        ),
    )
    seeded.session.commit()

    outcome = GenerationResultApplicationFacade(seeded.session).apply_canvas_media(
        seeded.session, authority, command
    )

    assert outcome.kind == "applied"
    assert seeded.session.in_transaction()
    assert _read_chapter(seeded.engine, seeded.chapter_id)[0].get("preview") == prior_preview
    with Session(seeded.engine) as observer:
        staged_before = observer.execute(
            select(storyboard_media_states.c.media_revision).where(
                storyboard_media_states.c.chapter_id == seeded.chapter_id
            )
        ).scalars().all()
        assert staged_before == ([1] if existing_media_state else [])
    seeded.session.commit()
    assert _read_chapter(seeded.engine, seeded.chapter_id)[0]["preview"] == "managed://results/one"
    with Session(seeded.engine) as observer:
        state = observer.execute(
            select(storyboard_media_states.c.media_revision).where(
                storyboard_media_states.c.chapter_id == seeded.chapter_id,
                storyboard_media_states.c.storyboard_asset_id == seeded.storyboard_id,
            )
        ).scalar_one()
        application = observer.get(ApplicationRecordRow, outcome.application_ref)
        assert state == 2
        assert application is not None
        expected_before = 1 if existing_media_state else None
        assert application.media_revision_before == expected_before
        assert application.media_revision_after == 2
    seeded.session.close()
    seeded.engine.dispose()


@pytest.mark.parametrize("existing_media_state", [False, True], ids=["cold-state", "existing-state"])
def test_canvas_preview_notes_cas_failure_rolls_back_every_staged_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing_media_state: bool
) -> None:
    from haoai_backend.personal_production.notes.persistence import SqlAlchemyNotesUnitOfWork
    from haoai_backend.personal_production.notes.tables import personal_production_notes

    prior_preview = None if existing_media_state else "managed://prior-preview"
    seeded = _seed_frame_result(tmp_path, prior_preview=prior_preview)
    if existing_media_state:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        media_state_id = str(uuid4())
        seeded.session.execute(
            storyboard_media_states.insert().values(
                id=media_state_id,
                chapter_id=seeded.chapter_id,
                storyboard_asset_id=seeded.storyboard_id,
                media_revision=1,
                asset_image_digest=None,
                preview_digest=None,
                source_valid=True,
                created_at=now,
                updated_at=now,
            )
        )
    else:
        media_state_id = None

    approved_notes = {
        seeded.storyboard_id: {
            "status": "approved",
            "approved_media_revision": 1,
            "reviewer_note": "preserve this approval",
            "future_field": {"score": 7},
        }
    }
    notes_id = str(uuid4())
    seeded.session.execute(
        personal_production_notes.insert().values(
            id=notes_id,
            chapter_id=seeded.chapter_id,
            user_id=seeded.owner_id,
            revision=1,
            frame_notes=approved_notes,
            resume_frame_id=seeded.storyboard_id,
        )
    )

    target = TargetSnapshot(
        kind="canvas_frame_preview",
        chapter_id=seeded.chapter_id,
        series_id=seeded.series_id,
        stable_storyboard_asset_id=seeded.storyboard_id,
        media_identity=seeded.storyboard_id,
        full_frame_digest=_digest(seeded.original_frames[0]),
        full_chapter_digest=_digest(seeded.original_frames),
    )
    payload = {"target_snapshot": asdict(target)}
    task = seeded.session.get(AITaskRow, seeded.task_id)
    task.type = "image-single"
    task.status = "completed"
    task.request_data = json.dumps(payload)
    seeded.session.get(BillingUnitRow, seeded.unit_id).operation = "image.single"
    seeded.session.get(ExecutionStepRow, seeded.step_id).input_snapshot = payload
    seeded.session.get(ExternalSubmissionRow, seeded.submission_id).request_snapshot = payload
    seeded.session.get(GenerationResultReceiptRow, seeded.result_ref).operation = "image.single"
    seeded.session.execute(
        delete(LocalContinuationWorkRow).where(
            LocalContinuationWorkRow.result_ref == seeded.result_ref
        )
    )
    actor = GenerationActor(seeded.owner_id, TrustedActor(seeded.owner_id))
    authority = CanvasApplyAuthority(
        actor=actor,
        task_id=seeded.task_id,
        result_ref=seeded.result_ref,
        operation="image.single",
        target=target,
        result_digest=seeded.receipt.content_digest,
    )
    command = GenerationApplicationCommand(
        task_id=seeded.task_id,
        result_ref=seeded.result_ref,
        expected_target=target,
        application_key=application_key_for(
            seeded.result_ref, target_scope_for_snapshot(target)
        ),
    )
    seeded.session.commit()

    events = _watch_caller_session(monkeypatch, seeded.session)
    original_update = SqlAlchemyNotesUnitOfWork.update_personal_notes
    cas_attempts: list[tuple[str, int, bool]] = []

    def lose_notes_compare_and_set(
        self,
        record_id: str,
        expected_revision: int,
        next_revision: int,
        frame_notes: dict[str, object],
        resume_frame_id: str | None,
    ) -> bool:
        assert self._session is seeded.session
        staged_chapter = seeded.session.execute(
            select(chapters.c.content).where(chapters.c.id == seeded.chapter_id)
        ).scalar_one()
        assert json.loads(staged_chapter)[0]["preview"] == "managed://results/one"
        staged_media_revisions = seeded.session.execute(
            select(storyboard_media_states.c.media_revision).where(
                storyboard_media_states.c.chapter_id == seeded.chapter_id,
                storyboard_media_states.c.storyboard_asset_id == seeded.storyboard_id,
            )
        ).scalars().all()
        assert staged_media_revisions == [2]
        updated = original_update(
            self,
            record_id,
            expected_revision + 1,
            next_revision,
            frame_notes,
            resume_frame_id,
        )
        cas_attempts.append((record_id, expected_revision, updated))
        return updated

    monkeypatch.setattr(
        SqlAlchemyNotesUnitOfWork,
        "update_personal_notes",
        lose_notes_compare_and_set,
    )
    from haoai_backend.personal_production.notes.errors import ChapterMediaStateError

    with pytest.raises(
        ChapterMediaStateError,
        match="personal notes changed concurrently during media reconciliation",
    ):
        GenerationResultApplicationFacade(seeded.session).apply_canvas_media(
            seeded.session, authority, command
        )

    assert cas_attempts == [(notes_id, 1, False)]
    assert seeded.session.in_transaction()
    assert events["other_sessions"] == []
    assert events["commit"] == 0
    assert events["rollback"] == 0
    assert events["close"] == 0
    assert events["uow_sessions"] == [seeded.session]

    monkeypatch.undo()
    seeded.session.rollback()
    assert _read_chapter(seeded.engine, seeded.chapter_id) == seeded.original_frames
    with Session(seeded.engine) as observer:
        note = observer.execute(
            select(
                personal_production_notes.c.revision,
                personal_production_notes.c.frame_notes,
                personal_production_notes.c.resume_frame_id,
            ).where(personal_production_notes.c.id == notes_id)
        ).mappings().one()
        assert dict(note) == {
            "revision": 1,
            "frame_notes": approved_notes,
            "resume_frame_id": seeded.storyboard_id,
        }
        media_rows = observer.execute(
            select(
                storyboard_media_states.c.id,
                storyboard_media_states.c.media_revision,
                storyboard_media_states.c.asset_image_digest,
                storyboard_media_states.c.preview_digest,
                storyboard_media_states.c.source_valid,
            ).where(storyboard_media_states.c.chapter_id == seeded.chapter_id)
        ).mappings().all()
        if existing_media_state:
            assert [dict(row) for row in media_rows] == [
                {
                    "id": media_state_id,
                    "media_revision": 1,
                    "asset_image_digest": None,
                    "preview_digest": None,
                    "source_valid": True,
                }
            ]
        else:
            assert media_rows == []
        assert observer.execute(
            select(ApplicationRecordRow.application_ref).where(
                ApplicationRecordRow.result_ref == seeded.result_ref
            )
        ).first() is None
        assert observer.execute(
            select(ResultEvidenceRow.application_status).where(
                ResultEvidenceRow.id == seeded.evidence_id
            )
        ).scalar_one() == "pending"

    seeded.session.close()
    seeded.engine.dispose()


def test_signed_query_rotation_and_frame_text_do_not_increment_media_revision(tmp_path: Path) -> None:
    seeded = _seed_frame_result(tmp_path)
    from haoai_backend.personal_production.notes.facade import stage_media_reconciliation

    original_image_url = "https://media.example.test/storyboard.png?signature=old"
    rotated_image_url = "https://media.example.test/storyboard.png?signature=new"
    with Session(seeded.engine) as editor:
        editor.execute(
            update(storyboard_assets)
            .where(storyboard_assets.c.id == seeded.storyboard_id)
            .values(image_url=original_image_url)
        )
        editor.commit()

    chapter_content = json.dumps(seeded.original_frames, ensure_ascii=False)
    initialized = stage_media_reconciliation(
        seeded.session,
        seeded.chapter_id,
        chapter_content_override=chapter_content,
        focus_storyboard_asset_id=seeded.storyboard_id,
    )
    assert initialized.media_revision_after == 1
    seeded.session.commit()

    with Session(seeded.engine) as editor:
        editor.execute(
            update(storyboard_assets)
            .where(storyboard_assets.c.id == seeded.storyboard_id)
            .values(image_url=rotated_image_url)
        )
        editor.commit()

    outcome = stage_frame_result(seeded.session, seeded.authority, seeded.validated, seeded.target)

    assert outcome.kind == "applied"
    seeded.session.commit()
    with Session(seeded.engine) as observer:
        state = observer.execute(
            select(storyboard_media_states.c.media_revision).where(
                storyboard_media_states.c.chapter_id == seeded.chapter_id,
                storyboard_media_states.c.storyboard_asset_id == seeded.storyboard_id,
            )
        ).scalar_one()
        application = observer.get(ApplicationRecordRow, outcome.application_ref)
        image_url = observer.execute(
            select(storyboard_assets.c.image_url).where(storyboard_assets.c.id == seeded.storyboard_id)
        ).scalar_one()
        assert image_url == rotated_image_url
        assert state == 1
        assert application is not None
        assert application.media_revision_before == 1 and application.media_revision_after == 1
    seeded.session.close()
    seeded.engine.dispose()


def test_owned_completed_result_reader_accepts_a_valid_targetless_video_receipt(tmp_path: Path) -> None:
    seeded = _seed_frame_result(tmp_path)
    task = seeded.session.get(AITaskRow, seeded.task_id)
    task.type = "video-single"
    task.status = "completed"
    task.request_data = "{}"
    seeded.session.get(BillingUnitRow, seeded.unit_id).operation = "video.single"
    seeded.session.get(ExecutionStepRow, seeded.step_id).input_snapshot = {}
    seeded.session.get(ExternalSubmissionRow, seeded.submission_id).request_snapshot = {}
    from haoai_backend.generation_core.schema import GenerationResultReceiptRow, LocalContinuationWorkRow

    seeded.session.get(GenerationResultReceiptRow, seeded.result_ref).operation = "video.single"
    seeded.session.execute(
        delete(LocalContinuationWorkRow).where(
            LocalContinuationWorkRow.result_ref == seeded.result_ref
        )
    )
    seeded.session.commit()

    actor = GenerationActor(seeded.owner_id, TrustedActor(seeded.owner_id))
    loaded = GenerationResultApplicationFacade(seeded.session).load_owned_completed_result(
        seeded.session, actor, seeded.task_id, seeded.result_ref
    )

    assert not isinstance(loaded, str)
    assert loaded.operation == "video.single"
    seeded.session.rollback()
    seeded.session.close()
    seeded.engine.dispose()
