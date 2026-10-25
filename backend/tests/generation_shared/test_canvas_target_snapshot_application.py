from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from haoai_backend.generation_catalog.admission_types import AdmissionOperation
from haoai_backend.generation_contracts.records import GenerationResultReceipt
from haoai_backend.generation_contracts.types import (
    CanvasApplyAuthority,
    GenerationActor,
    GenerationApplicationCommand,
    StoredMedia,
)
from haoai_backend.generation_core.admission_receipts import AdmissionStaged
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    CreditLogRow,
    UserCreditRow,
)
from haoai_backend.generation_core.schema import ApplicationRecordRow
from haoai_backend.generation_credentials import snapshot_producer
from haoai_backend.generation_runtime.assembly import (
    GenerationWorkerConfiguration,
    build_generation_worker_runtime,
)
from haoai_backend.personal_production.notes.tables import (
    metadata as notes_metadata,
    personal_production_notes,
    storyboard_media_states,
)
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.task_observation import (
    GenerationResultApplicationFacade,
    application_key_for,
    target_scope_for_snapshot,
)

_TEST_DIRECTORIES = (
    Path(__file__).resolve().parents[1] / "generation_core",
    Path(__file__).resolve().parents[1] / "generation_execution",
)
_ADDED_TEST_PATHS = [
    str(directory) for directory in _TEST_DIRECTORIES if str(directory) not in sys.path
]
sys.path[:0] = _ADDED_TEST_PATHS
try:
    import test_admission_staging as admission_staging_module
    from test_admission_staging import (
        ASSET_TABLES,
        _G2_JOINT_ACTOR_ID,
        _G2_JOINT_NOW,
        _joint_caller,
        _joint_prepare_and_stage,
        _joint_request,
    )
    from test_image_single_worker import (
        _authenticated_environment,
        _provider_body,
        _runtime_configuration,
    )
finally:
    for path in _ADDED_TEST_PATHS:
        sys.path.remove(path)


_STORYBOARD_ID = "joint-canvas-frame-1"
_SIBLING_STORYBOARD_ID = "joint-canvas-frame-2"
_LATE_STORYBOARD_ID = "joint-canvas-late-stable-id"
_STRICT_NO_ID_MODE = "strict-no-id-v1"


def _canonical_json_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _no_id_application_state(engine) -> dict[str, object]:
    with Session(engine) as reader:
        chapter_content = reader.execute(
            select(admission_staging_module.catalog_chapters.c.content).where(
                admission_staging_module.catalog_chapters.c.id == "joint-chapter-1"
            )
        ).scalar_one()
        return {
            "chapter_content": chapter_content,
            "applications": reader.execute(
                select(func.count()).select_from(ApplicationRecordRow)
            ).scalar_one(),
            "media_states": reader.execute(
                select(func.count()).select_from(storyboard_media_states)
            ).scalar_one(),
            "notes": reader.execute(
                select(func.count()).select_from(personal_production_notes)
            ).scalar_one(),
            "credit_logs": reader.execute(
                select(func.count()).select_from(CreditLogRow)
            ).scalar_one(),
            "credits": reader.execute(
                select(UserCreditRow.credits).where(
                    UserCreditRow.user_id == _G2_JOINT_ACTOR_ID
                )
            ).scalar_one(),
        }


@pytest.mark.parametrize(
    ("source_change", "expected_application"),
    (
        ("unchanged", "applied"),
        ("manual_target_edit", "target_conflict"),
        ("sibling_edit", "target_conflict"),
        ("sibling_reorder", "target_conflict"),
        ("frame_inserted", "target_conflict"),
        ("frame_deleted", "target_conflict"),
        ("target_acquired_stable_id", "target_conflict"),
    ),
)
def test_real_strict_no_id_canvas_snapshot_reaches_owned_receipt_and_writer(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    source_change: str,
    expected_application: str,
) -> None:
    """Exercise strict no-ID admission through durable worker and P5 application."""
    monkeypatch.setattr(admission_staging_module, "_G2_JOINT_PROVIDER", "openai")
    environment = _authenticated_environment(monkeypatch, tmp_path)
    target_frame = {
        "unknown_target": {"nested": ["目标雪景", {"enabled": True, "镜头": 2}]},
        "text": "原始无ID目标",
        "storyboard": [None, ""],
    }
    sibling_frame = {
        "unknown_sibling": {"nested": ["兄弟雨景"]},
        "text": "兄弟帧",
        "storyboard": [],
    }
    chapter_frames = [sibling_frame, target_frame]
    raw_chapter = json.dumps(
        chapter_frames,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    stamp = _G2_JOINT_NOW.replace(tzinfo=None)
    try:
        # Keep the source free of storyboard assets: this target has no stable identity.
        with environment.engine.begin() as connection:
            connection.exec_driver_sql(
                "ALTER TABLE storyboard_assets ADD COLUMN created_at DATETIME"
            )
            connection.execute(
                update(admission_staging_module.catalog_chapters)
                .where(
                    admission_staging_module.catalog_chapters.c.id
                    == "joint-chapter-1"
                )
                .values(content=raw_chapter)
            )

        notes_metadata.create_all(
            environment.engine,
            tables=[storyboard_media_states, personal_production_notes],
        )
        captured_payloads = []
        original_producer_stage = snapshot_producer.MediaSnapshotProducer.prepare_and_stage

        def capture_producer_result(producer, caller, **kwargs):
            result = original_producer_stage(producer, caller, **kwargs)
            if isinstance(result, snapshot_producer.SnapshotPayloadsStaged):
                captured_payloads.append(result)
            return result

        monkeypatch.setattr(
            snapshot_producer.MediaSnapshotProducer,
            "prepare_and_stage",
            capture_producer_result,
        )

        generation_request = _joint_request(AdmissionOperation.IMAGE_SINGLE.value)
        generation_request["reference_images"] = []
        generation_request["target"] = {
            "kind": "canvas_apply",
            "destination": {
                "kind": "frame_preview",
                "chapter_id": "joint-chapter-1",
                "identity_mode": _STRICT_NO_ID_MODE,
                "frame_index": 1,
                "expected_chapter_digest": _canonical_json_digest(chapter_frames),
            },
        }
        caller = _joint_caller(environment.engine)
        try:
            staged = _joint_prepare_and_stage(
                environment.binding,
                caller,
                operation=AdmissionOperation.IMAGE_SINGLE.value,
                raw_key="strict-no-id-canvas-target-application",
                request=generation_request,
            )
            assert isinstance(staged, AdmissionStaged)
            assert len(captured_payloads) == 1
            frozen_target = captured_payloads[0].items[0].snapshot.target_snapshot
            assert frozen_target.kind == "canvas_frame_preview"
            assert frozen_target.stable_storyboard_asset_id is None
            assert frozen_target.compat_frame_index == 1
            assert frozen_target.full_chapter_digest == _canonical_json_digest(
                chapter_frames
            )
            assert frozen_target.full_frame_digest == _canonical_json_digest(
                target_frame
            )
            locked_target = captured_payloads[0].preparation.items[0].target
            assert locked_target.identity_mode == _STRICT_NO_ID_MODE
            assert locked_target.expected_chapter_digest == _canonical_json_digest(
                chapter_frames
            )
            assert locked_target.canonical_chapter_digest == _canonical_json_digest(
                chapter_frames
            )
            assert locked_target.chapter_digest == hashlib.sha256(
                raw_chapter.encode("utf-8")
            ).hexdigest()
            task_id = staged.ordered_tasks[0].task_id
            caller.commit()
        finally:
            caller.close()

        stored_media: list[StoredMedia] = []
        runtime_config, provider_calls = _runtime_configuration(
            environment,
            _provider_body(),
            stored_media,
        )
        runtime = build_generation_worker_runtime(
            GenerationWorkerConfiguration(runtime_config, environment.capabilities)
        )
        worker_outcome = runtime.run_claimed_once("image-single")
        assert worker_outcome is not None
        assert worker_outcome != "pending"
        assert worker_outcome != "ownership_lost"
        assert worker_outcome.result_ref
        assert len(provider_calls) == 1
        assert len(stored_media) == 1

        if source_change != "unchanged":
            changed_frames = json.loads(raw_chapter)
            if source_change == "manual_target_edit":
                changed_frames[1]["text"] = "人工修改目标"
            elif source_change == "sibling_edit":
                changed_frames[0]["text"] = "人工修改兄弟帧"
            elif source_change == "sibling_reorder":
                changed_frames.reverse()
            elif source_change == "frame_inserted":
                changed_frames.insert(0, {"text": "新插入帧"})
            elif source_change == "frame_deleted":
                changed_frames.pop(0)
            elif source_change == "target_acquired_stable_id":
                changed_frames[1]["storyboard"] = [_LATE_STORYBOARD_ID]
            else:
                raise AssertionError(f"unexpected source change: {source_change}")
            changed_content = json.dumps(
                changed_frames,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            with environment.engine.begin() as connection:
                connection.execute(
                    update(admission_staging_module.catalog_chapters)
                    .where(
                        admission_staging_module.catalog_chapters.c.id
                        == "joint-chapter-1"
                    )
                    .values(content=changed_content)
                )

        before_application = _no_id_application_state(environment.engine)
        actor = GenerationActor(
            _G2_JOINT_ACTOR_ID,
            TrustedActor(_G2_JOINT_ACTOR_ID),
        )
        apply_session = Session(environment.engine, expire_on_commit=False)
        try:
            facade = GenerationResultApplicationFacade(apply_session)
            canonical_receipt = facade.load_owned_completed_result(
                apply_session,
                actor,
                task_id,
                worker_outcome.result_ref,
            )
            assert isinstance(canonical_receipt, GenerationResultReceipt)
            authority = CanvasApplyAuthority(
                actor=actor,
                task_id=task_id,
                result_ref=canonical_receipt.result_ref,
                operation=canonical_receipt.operation,
                target=frozen_target,
                result_digest=canonical_receipt.content_digest,
            )
            scope = target_scope_for_snapshot(frozen_target)
            assert len(scope) <= 255
            assert (
                f"strict-no-id|chapter|{frozen_target.full_chapter_digest}|index|1"
                in scope
            )
            command = GenerationApplicationCommand(
                task_id=task_id,
                result_ref=canonical_receipt.result_ref,
                expected_target=frozen_target,
                application_key=application_key_for(
                    canonical_receipt.result_ref,
                    scope,
                ),
            )
            outcome = facade.apply_canvas_media(apply_session, authority, command)
            assert outcome.kind == expected_application

            if expected_application == "target_conflict":
                apply_session.rollback()
                assert outcome.application_ref is None
                assert _no_id_application_state(environment.engine) == before_application
            else:
                application_ref = outcome.application_ref
                assert application_ref
                apply_session.commit()

                with Session(environment.engine) as reader:
                    chapter_content = reader.execute(
                        select(admission_staging_module.catalog_chapters.c.content).where(
                            admission_staging_module.catalog_chapters.c.id
                            == "joint-chapter-1"
                        )
                    ).scalar_one()
                    frames = json.loads(chapter_content)
                    safe_target = json.loads(
                        reader.execute(
                            select(AITaskRow.request_data).where(
                                AITaskRow.id == task_id
                            )
                        ).scalar_one()
                    )["target_snapshot"]

                assert frames[1]["text"] == "原始无ID目标"
                assert frames[1]["preview"] == stored_media[0].locator
                assert frames[1]["storyboard"] == [None, ""]
                assert frames[0] == sibling_frame
                assert safe_target["stable_storyboard_asset_id"] is None
                assert safe_target["compat_frame_index"] == 1
                assert safe_target["full_chapter_digest"] == _canonical_json_digest(
                    chapter_frames
                )
                after_first_apply = _no_id_application_state(environment.engine)
                assert after_first_apply["applications"] == 1
                assert after_first_apply["media_states"] == 0
                assert after_first_apply["notes"] == 0

                immediate_replay = facade.apply_canvas_media(
                    apply_session,
                    authority,
                    command,
                )
                assert immediate_replay.kind == "already_applied"
                assert immediate_replay.application_ref == application_ref
                apply_session.rollback()
                assert _no_id_application_state(environment.engine) == after_first_apply

                edited_after_apply = json.loads(
                    after_first_apply["chapter_content"]
                )
                edited_after_apply[1]["text"] = "应用后人工编辑"
                post_apply_manual_edit = json.dumps(
                    edited_after_apply,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                with environment.engine.begin() as connection:
                    connection.execute(
                        update(admission_staging_module.catalog_chapters)
                        .where(
                            admission_staging_module.catalog_chapters.c.id
                            == "joint-chapter-1"
                        )
                        .values(content=post_apply_manual_edit)
                    )
                before_replay = _no_id_application_state(environment.engine)
                replay = facade.apply_canvas_media(
                    apply_session,
                    authority,
                    command,
                )
                assert replay.kind == "already_applied"
                assert replay.application_ref == application_ref
                apply_session.rollback()
                assert _no_id_application_state(environment.engine) == before_replay
        finally:
            apply_session.close()
    finally:
        environment.engine.dispose()


@pytest.mark.parametrize(
    ("source_change", "expected_application"),
    (
        ("unchanged", "applied"),
        ("manual_target_edit", "stale"),
        ("sibling_reorder", "applied"),
    ),
)
def test_real_canvas_snapshot_reaches_owned_receipt_and_writer(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    source_change: str,
    expected_application: str,
) -> None:
    """Exercise admission, producer, durable receipt reader, and P5 writer together."""
    monkeypatch.setattr(admission_staging_module, "_G2_JOINT_PROVIDER", "openai")
    environment = _authenticated_environment(monkeypatch, tmp_path)
    try:
        target_frame = {
            "unknown": {"nested": ["雪夜", {"enabled": True, "镜头": 2}]},
            "text": "旧画面",
            "storyboard": [_STORYBOARD_ID],
        }
        sibling_frame = {
            "unknown_sibling": {"nested": ["雨天"]},
            "storyboard": [_SIBLING_STORYBOARD_ID],
            "text": "兄弟分镜",
        }
        raw_chapter = (
            '[ { "unknown" : {"nested" : ["雪夜", {"enabled": true, "镜头": 2}]}, '
            '"text" : "旧画面", "storyboard" : ["joint-canvas-frame-1"] }, '
            '{ "unknown_sibling" : {"nested" : ["雨天"]}, '
            '"storyboard" : ["joint-canvas-frame-2"], "text" : "兄弟分镜" } ]'
        )
        stamp = _G2_JOINT_NOW.replace(tzinfo=None)

        # The catalog fixture omits a column selected by the canonical P5 table
        # projection; add that nullable compatibility column for this isolated test.
        with environment.engine.begin() as connection:
            connection.exec_driver_sql(
                "ALTER TABLE storyboard_assets ADD COLUMN created_at DATETIME"
            )
            connection.execute(
                update(admission_staging_module.catalog_chapters)
                .where(admission_staging_module.catalog_chapters.c.id == "joint-chapter-1")
                .values(content=raw_chapter)
            )
            for frame_index, (storyboard_id, frame_name) in enumerate(
                (
                    (_STORYBOARD_ID, "Target frame"),
                    (_SIBLING_STORYBOARD_ID, "Sibling frame"),
                )
            ):
                connection.execute(
                    ASSET_TABLES["storyboard"].insert().values(
                        id=storyboard_id,
                        series_id="joint-series-1",
                        chapter_id="joint-chapter-1",
                        frame_index=frame_index,
                        name=frame_name,
                        description="Fixture storyboard frame",
                        image_url=None,
                        updated_at=stamp,
                    )
                )

        notes_metadata.create_all(
            environment.engine,
            tables=[storyboard_media_states, personal_production_notes],
        )
        captured_payloads = []
        original_producer_stage = snapshot_producer.MediaSnapshotProducer.prepare_and_stage

        def capture_producer_result(producer, caller, **kwargs):
            result = original_producer_stage(producer, caller, **kwargs)
            if isinstance(result, snapshot_producer.SnapshotPayloadsStaged):
                captured_payloads.append(result)
            return result

        monkeypatch.setattr(
            snapshot_producer.MediaSnapshotProducer,
            "prepare_and_stage",
            capture_producer_result,
        )

        generation_request = _joint_request(AdmissionOperation.IMAGE_SINGLE.value)
        generation_request["reference_images"] = []
        generation_request["target"] = {
            "kind": "canvas_apply",
            "destination": {
                "kind": "frame_preview",
                "chapter_id": "joint-chapter-1",
                "storyboard_asset_id": _STORYBOARD_ID,
            },
        }
        caller = _joint_caller(environment.engine)
        try:
            staged = _joint_prepare_and_stage(
                environment.binding,
                caller,
                operation=AdmissionOperation.IMAGE_SINGLE.value,
                raw_key="canvas-target-snapshot-application",
                request=generation_request,
            )
            assert isinstance(staged, AdmissionStaged)
            assert len(captured_payloads) == 1
            frozen_target = captured_payloads[0].items[0].snapshot.target_snapshot
            assert frozen_target.kind == "canvas_frame_preview"
            assert frozen_target.stable_storyboard_asset_id == _STORYBOARD_ID
            task_id = staged.ordered_tasks[0].task_id
            caller.commit()
        finally:
            caller.close()

        stored_media: list[StoredMedia] = []
        runtime_config, provider_calls = _runtime_configuration(
            environment,
            _provider_body(),
            stored_media,
        )
        runtime = build_generation_worker_runtime(
            GenerationWorkerConfiguration(runtime_config, environment.capabilities)
        )
        worker_outcome = runtime.run_claimed_once("image-single")
        assert worker_outcome is not None
        assert worker_outcome != "pending"
        assert worker_outcome != "ownership_lost"
        assert worker_outcome.result_ref
        assert len(provider_calls) == 1
        assert len(stored_media) == 1

        if source_change == "manual_target_edit":
            manually_changed_frame = {**target_frame, "text": "人工改稿"}
            manually_changed_content = json.dumps(
                [manually_changed_frame, sibling_frame],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            with environment.engine.begin() as connection:
                connection.execute(
                    update(admission_staging_module.catalog_chapters)
                    .where(
                        admission_staging_module.catalog_chapters.c.id
                        == "joint-chapter-1"
                    )
                    .values(content=manually_changed_content)
                )
        elif source_change == "sibling_reorder":
            reordered_content = json.dumps(
                [sibling_frame, target_frame],
                ensure_ascii=False,
                indent=2,
            )
            with environment.engine.begin() as connection:
                connection.execute(
                    update(admission_staging_module.catalog_chapters)
                    .where(
                        admission_staging_module.catalog_chapters.c.id
                        == "joint-chapter-1"
                    )
                    .values(content=reordered_content)
                )

        actor = GenerationActor(
            _G2_JOINT_ACTOR_ID,
            TrustedActor(_G2_JOINT_ACTOR_ID),
        )
        apply_session = Session(environment.engine, expire_on_commit=False)
        try:
            facade = GenerationResultApplicationFacade(apply_session)
            canonical_receipt = facade.load_owned_completed_result(
                apply_session,
                actor,
                task_id,
                worker_outcome.result_ref,
            )
            assert isinstance(canonical_receipt, GenerationResultReceipt)
            authority = CanvasApplyAuthority(
                actor=actor,
                task_id=task_id,
                result_ref=canonical_receipt.result_ref,
                operation=canonical_receipt.operation,
                target=frozen_target,
                result_digest=canonical_receipt.content_digest,
            )
            scope = target_scope_for_snapshot(frozen_target)
            command = GenerationApplicationCommand(
                task_id=task_id,
                result_ref=canonical_receipt.result_ref,
                expected_target=frozen_target,
                application_key=application_key_for(
                    canonical_receipt.result_ref,
                    scope,
                ),
            )
            outcome = facade.apply_canvas_media(apply_session, authority, command)
            assert outcome.kind == expected_application

            if expected_application == "applied":
                application_ref = outcome.application_ref
                assert application_ref
                apply_session.commit()

                replay = facade.apply_canvas_media(apply_session, authority, command)
                assert replay.kind == "already_applied"
                assert replay.application_ref == application_ref
                apply_session.rollback()
            else:
                apply_session.rollback()
                assert outcome.application_ref is None

            with Session(environment.engine) as reader:
                chapter_content = reader.execute(
                    select(admission_staging_module.catalog_chapters.c.content).where(
                        admission_staging_module.catalog_chapters.c.id
                        == "joint-chapter-1"
                    )
                ).scalar_one()
                frames = json.loads(chapter_content)
                application_count = reader.execute(
                    select(func.count()).select_from(ApplicationRecordRow)
                ).scalar_one()
                task = reader.execute(
                    select(AITaskRow).where(AITaskRow.id == task_id)
                ).scalar_one()
                safe_target = json.loads(task.request_data)["target_snapshot"]

            if source_change == "manual_target_edit":
                assert frames[0]["text"] == "人工改稿"
                assert "preview" not in frames[0]
                assert application_count == 0
            else:
                target_index = 1 if source_change == "sibling_reorder" else 0
                sibling_index = 0 if source_change == "sibling_reorder" else 1
                assert frames[target_index]["text"] == "旧画面"
                assert frames[target_index]["preview"] == stored_media[0].locator
                assert frames[sibling_index] == sibling_frame
                assert application_count == 1
                assert frozen_target.full_frame_digest == _canonical_json_digest(
                    target_frame
                )
                assert safe_target["full_frame_digest"] == frozen_target.full_frame_digest
            assert task.status == "completed"
        finally:
            apply_session.close()
    finally:
        environment.engine.dispose()
