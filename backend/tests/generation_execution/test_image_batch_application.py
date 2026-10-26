from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

import test_batch_execution_snapshot_reader as batch_fixtures
import haoai_backend.asset_data as asset_data_package
from haoai_backend.asset_data.tables import ASSET_TABLES
from haoai_backend.generation_contracts.types import StoredMedia
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    ResultEvidenceRow,
    UserCreditRow,
)
from haoai_backend.generation_core.image_batch_recovery import SqlAlchemyImageBatchRecovery
from haoai_backend.generation_core.image_batch_results import SqlAlchemyImageBatchResults
from haoai_backend.generation_core.schema import ApplicationRecordRow, GenerationResultReceiptRow
from haoai_backend.personal_production.notes.media import digest_media_identity
from haoai_backend.personal_production.notes.tables import (
    metadata as notes_metadata,
    personal_production_notes,
    storyboard_media_states,
)
from generation_core.test_image_batch_intents import (
    _batch_environment,
    _claims,
    _intents,
)

joint_fixtures = batch_fixtures.joint_fixtures


_NOW = datetime(2026, 10, 16, 2, 0, tzinfo=timezone.utc)


def _complete_first_item(
    tmp_path, monkeypatch, before_complete=None, before_apply=None
):
    environment, staged = _batch_environment(tmp_path, monkeypatch)
    if before_complete is not None:
        before_complete(environment)
    claim = _claims(environment).claim_next("batch-image", "worker-batch-application")
    assert claim is not None
    prepared = _intents(environment).prepare(claim)
    assert prepared.disposition == "committed"
    assert prepared.intent is not None
    assert prepared.verified_input is not None
    ids = iter(("batch-application-result", "batch-application-evidence"))
    results = SqlAlchemyImageBatchResults(
        lambda: Session(environment.engine), lambda: _NOW, lambda: next(ids)
    )
    if before_apply is not None:
        before_apply(environment, claim, results)
    receipt = results.complete(
        claim,
        prepared.intent,
        prepared.verified_input,
        StoredMedia(
            locator="https://media.example.test/batch/selected.png",
            content_digest="a" * 64,
            media_type="image/png",
            size_bytes=128,
        ),
    )
    return environment, staged, claim, results, receipt


def _financial_state(environment, owner_id: str) -> tuple[object, ...]:
    with Session(environment.engine) as session:
        balance = session.execute(
            select(UserCreditRow.__table__.c.credits).where(
                UserCreditRow.__table__.c.user_id == owner_id
            )
        ).scalar_one()
        ledger = session.execute(
            select(
                CreditLogRow.__table__.c.id,
                CreditLogRow.__table__.c.amount,
                CreditLogRow.__table__.c.business_key,
            )
            .where(CreditLogRow.__table__.c.user_id == owner_id)
            .order_by(CreditLogRow.__table__.c.id)
        ).all()
    return balance, tuple(tuple(row) for row in ledger)


def test_completed_batch_result_fills_only_its_original_empty_asset(
    tmp_path, monkeypatch
) -> None:
    environment, _staged, claim, results, receipt = _complete_first_item(
        tmp_path, monkeypatch
    )
    assert receipt is not None
    outcome = results.apply_completed(claim.scope, receipt.result_ref)
    assert outcome.kind == "already_applied", outcome.detail

    with Session(environment.engine) as session:
        task = session.execute(
            select(ASSET_TABLES["character"].c.image_url).where(
                ASSET_TABLES["character"].c.id == "joint-character-1"
            )
        ).scalar_one()
        application = session.execute(
            select(ApplicationRecordRow.__table__).where(
                ApplicationRecordRow.result_ref == receipt.result_ref
            )
        ).mappings().one()
        evidence_status = session.execute(
            select(ResultEvidenceRow.__table__.c.application_status).where(
                ResultEvidenceRow.__table__.c.id == receipt.evidence_id
            )
        ).scalar_one()

    assert task == receipt.recoverable_locator
    assert application["owner_id"] == claim.scope.user_id
    assert application["task_id"] == claim.scope.task_id
    assert application["delivery_status"] == "applied"
    assert evidence_status == "applied"
    environment.engine.dispose()


def test_user_image_arriving_before_apply_wins_and_original_success_remains(
    tmp_path, monkeypatch
) -> None:
    user_image = "https://media.example.test/user/kept.png"
    financial_before: list[tuple[object, ...]] = []

    def write_user_image_before_apply(environment, claim, results):
        financial_before.append(_financial_state(environment, claim.scope.user_id))
        original_apply = results.apply_completed

        def apply_after_user_edit(scope, result_ref, *, stop_signal=None):
            with Session(environment.engine) as session, session.begin():
                session.execute(
                    update(ASSET_TABLES["character"])
                    .where(ASSET_TABLES["character"].c.id == "joint-character-1")
                    .values(image_url=user_image)
                )
            return original_apply(scope, result_ref, stop_signal=stop_signal)

        monkeypatch.setattr(results, "apply_completed", apply_after_user_edit)

    environment, _staged, claim, _results, receipt = _complete_first_item(
        tmp_path, monkeypatch, before_apply=write_user_image_before_apply
    )
    assert receipt is not None
    with Session(environment.engine) as session:
        image_url = session.execute(
            select(ASSET_TABLES["character"].c.image_url).where(
                ASSET_TABLES["character"].c.id == "joint-character-1"
            )
        ).scalar_one()
        application = session.execute(
            select(ApplicationRecordRow.__table__).where(
                ApplicationRecordRow.result_ref == receipt.result_ref
            )
        ).mappings().one()
        evidence = session.execute(
            select(ResultEvidenceRow.__table__.c.application_status).where(
                ResultEvidenceRow.__table__.c.id == receipt.evidence_id
            )
        ).scalar_one()
        task_status = session.execute(
            select(AITaskRow.__table__.c.status).where(
                AITaskRow.__table__.c.id == claim.scope.task_id
            )
        ).scalar_one()
        unit = session.execute(
            select(BillingUnitRow.__table__).where(
                BillingUnitRow.__table__.c.task_id == claim.scope.task_id
            )
        ).mappings().one()

    assert image_url == user_image
    assert application["delivery_status"] == "target_conflict"
    assert evidence == "rejected"
    assert task_status == "completed"
    assert unit["result_status"] == "succeeded"
    assert unit["refunded_amount"] == 0
    assert _financial_state(environment, claim.scope.user_id) == financial_before[0]
    environment.engine.dispose()


def test_storyboard_fill_reconciles_revision_and_revokes_only_its_approval(
    tmp_path, monkeypatch
) -> None:
    original_insert_targets = joint_fixtures._joint_insert_batch_targets
    original_request = joint_fixtures._joint_request
    board_ids = ("joint-storyboard-1", "joint-storyboard-2")
    previews = tuple(
        f"https://media.example.test/preview/{board_id}.png" for board_id in board_ids
    )
    timestamp = joint_fixtures._G2_JOINT_NOW.replace(tzinfo=None)

    def insert_storyboard_targets(connection):
        original_insert_targets(connection)
        connection.execute(
            update(joint_fixtures.catalog_chapters)
            .where(joint_fixtures.catalog_chapters.c.id == "joint-chapter-1")
            .values(
                content=json.dumps(
                    [
                        {"storyboard": [board_ids[index]], "preview": previews[index]}
                        for index in range(2)
                    ]
                )
            )
        )
        for index, board_id in enumerate(board_ids):
            connection.execute(
                insert(joint_fixtures.ASSET_TABLES["storyboard"]).values(
                    id=board_id,
                    series_id="joint-series-1",
                    chapter_id="joint-chapter-1",
                    frame_index=index,
                    name=f"Storyboard {index + 1}",
                    description=f"Storyboard fixture {index + 1}",
                    image_url=None,
                    updated_at=timestamp,
                )
            )

    def storyboard_request(operation: str):
        request = original_request(operation)
        if operation == "image.batch":
            for index, item in enumerate(request["assets"]):
                item["type"] = "storyboard"
                item["id"] = board_ids[index]
                item["name"] = f"Storyboard {index + 1}"
        return request

    monkeypatch.setattr(joint_fixtures, "_joint_insert_batch_targets", insert_storyboard_targets)
    monkeypatch.setattr(joint_fixtures, "_joint_request", storyboard_request)

    def seed_notes(environment):
        notes_metadata.create_all(
            environment.engine,
            tables=[storyboard_media_states, personal_production_notes],
        )
        with environment.engine.begin() as connection:
            for board_id, preview in zip(board_ids, previews, strict=True):
                connection.execute(
                    insert(storyboard_media_states).values(
                        id=f"media-state-{board_id}",
                        chapter_id="joint-chapter-1",
                        storyboard_asset_id=board_id,
                        media_revision=1,
                        asset_image_digest=None,
                        preview_digest=digest_media_identity(preview),
                        source_valid=True,
                        created_at=timestamp,
                        updated_at=timestamp,
                    )
                )
            connection.execute(
                insert(personal_production_notes).values(
                    id="batch-storyboard-notes",
                    chapter_id="joint-chapter-1",
                    user_id=joint_fixtures._G2_JOINT_ACTOR_ID,
                    revision=1,
                    frame_notes={
                        board_id: {
                            "status": "approved",
                            "approved_media_revision": 1,
                            "note": f"keep-{index}",
                        }
                        for index, board_id in enumerate(board_ids)
                    },
                    resume_frame_id=board_ids[1],
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )

    environment, _staged, claim, _results, receipt = _complete_first_item(
        tmp_path, monkeypatch, before_complete=seed_notes
    )
    assert receipt is not None
    # Read the selected target from its immutable producer projection.
    with Session(environment.engine) as session:
        task = session.execute(
            select(AITaskRow.__table__).where(
                AITaskRow.__table__.c.id == receipt.task_id
            )
        ).mappings().one()
        selected_target_id = json.loads(task["request_data"])["asset_id"]
        media_rows = session.execute(
            select(
                storyboard_media_states.c.storyboard_asset_id,
                storyboard_media_states.c.media_revision,
                storyboard_media_states.c.asset_image_digest,
            ).where(storyboard_media_states.c.chapter_id == "joint-chapter-1")
        ).mappings().all()
        notes = session.execute(
            select(personal_production_notes).where(
                personal_production_notes.c.chapter_id == "joint-chapter-1"
            )
        ).mappings().one()
        app = session.execute(
            select(ApplicationRecordRow.__table__).where(
                ApplicationRecordRow.result_ref == receipt.result_ref
            )
        ).mappings().one()

    state_by_asset = {row["storyboard_asset_id"]: row for row in media_rows}
    assert selected_target_id in board_ids
    assert state_by_asset[selected_target_id]["media_revision"] == 2
    assert state_by_asset[selected_target_id]["asset_image_digest"] is not None
    sibling_id = next(value for value in board_ids if value != selected_target_id)
    assert state_by_asset[sibling_id]["media_revision"] == 1
    assert notes["revision"] == 2
    assert notes["frame_notes"][selected_target_id]["status"] == "unmarked"
    assert notes["frame_notes"][selected_target_id]["needs_reconfirmation"] is True
    assert notes["frame_notes"][selected_target_id]["note"] in {"keep-0", "keep-1"}
    assert notes["frame_notes"][sibling_id]["status"] == "approved"
    assert notes["resume_frame_id"] == board_ids[1]
    assert app["media_revision_before"] == 1
    assert app["media_revision_after"] == 2
    assert app["notes_revision_before"] == 1
    assert app["notes_revision_after"] == 2
    environment.engine.dispose()


def test_pending_completed_application_is_recovered_without_reposting(
    tmp_path, monkeypatch
) -> None:
    original_stage = asset_data_package.stage_batch_asset_image_if_empty
    calls = 0

    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("synthetic local T2 interruption")
        return original_stage(*args, **kwargs)

    def fail_first_application(_environment, _claim, _results):
        monkeypatch.setattr(asset_data_package, "stage_batch_asset_image_if_empty", fail_once)

    environment, _staged, claim, results, receipt = _complete_first_item(
        tmp_path, monkeypatch, before_apply=fail_first_application
    )
    assert receipt is not None
    with Session(environment.engine) as session:
        evidence_before = session.execute(
            select(ResultEvidenceRow.__table__.c.application_status).where(
                ResultEvidenceRow.__table__.c.id == receipt.evidence_id
            )
        ).scalar_one()
        task_status = session.execute(
            select(AITaskRow.__table__.c.status).where(
                AITaskRow.__table__.c.id == claim.scope.task_id
            )
        ).scalar_one()
    assert evidence_before == "pending"
    assert task_status == "completed"

    monkeypatch.setattr(asset_data_package, "stage_batch_asset_image_if_empty", original_stage)
    recovery = SqlAlchemyImageBatchRecovery(
        lambda: Session(environment.engine), lambda: _NOW, results
    )
    assert recovery.scan_due() == ("retained",)
    assert recovery.scan_due() == ()
    with Session(environment.engine) as session:
        image_url = session.execute(
            select(ASSET_TABLES["character"].c.image_url).where(
                ASSET_TABLES["character"].c.id == "joint-character-1"
            )
        ).scalar_one()
        application_count = session.execute(
            select(ApplicationRecordRow.application_ref).where(
                ApplicationRecordRow.result_ref == receipt.result_ref
            )
        ).all()
        evidence_after = session.execute(
            select(ResultEvidenceRow.__table__.c.application_status).where(
                ResultEvidenceRow.__table__.c.id == receipt.evidence_id
            )
        ).scalar_one()
        saved_receipt = session.execute(
            select(GenerationResultReceiptRow.result_ref).where(
                GenerationResultReceiptRow.result_ref == receipt.result_ref
            )
        ).scalar_one()

    assert image_url == receipt.recoverable_locator
    assert len(application_count) == 1
    assert evidence_after == "applied"
    assert saved_receipt == receipt.result_ref
    assert calls == 1
    environment.engine.dispose()
