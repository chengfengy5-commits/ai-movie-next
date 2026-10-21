"""Atomic source application of authenticated frame.optimize results."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import event, select, update

from haoai_backend.generation_core.base_schema import AITaskRow
from haoai_backend.generation_core.schema import (
    ApplicationRecordRow,
    LocalContinuationWorkRow,
    UnitFeeDecisionRow,
)
from haoai_backend.personal_production.notes.tables import (
    personal_production_notes,
    storyboard_media_states,
)
from haoai_backend.series_data.tables import chapters, storyboard_assets
from generation_execution.frame_optimize_execution_helpers import (
    admit_batch,
    apply_current,
    application_adapter,
    claim_adapter,
    claim_one,
    continuation_claim_adapter,
    fee_adapter,
    intent_adapter,
    persist_result,
    prepare_item,
    rows_for,
)


def _finish_result_and_fee(case, claim, item, prepared, text: str):
    receipt = persist_result(case, claim, prepared, text=text)
    assert not isinstance(receipt, str)
    work = next(row for row in rows_for(case)["work"] if row.unit_id == item.unit_id)
    fee = fee_adapter(case).resolve_current_fee(claim, work.work_id)
    assert fee.disposition in {"charged", "no_charge"}
    return receipt, fee


def _source_snapshot(case):
    with case.sessions() as session:
        chapter = session.execute(
            select(chapters.c.content).where(chapters.c.id == "joint-chapter-1")
        ).scalar_one()
        assets = session.execute(
            select(storyboard_assets).where(storyboard_assets.c.chapter_id == "joint-chapter-1")
        ).mappings().all()
        media = session.execute(
            select(storyboard_media_states).where(
                storyboard_media_states.c.chapter_id == "joint-chapter-1"
            )
        ).mappings().all()
        notes = session.execute(
            select(personal_production_notes).where(
                personal_production_notes.c.chapter_id == "joint-chapter-1"
            )
        ).mappings().all()
    return {
        "chapter": chapter,
        "assets": [dict(row) for row in assets],
        "media": [dict(row) for row in media],
        "notes": [dict(row) for row in notes],
    }


def test_three_unit_delivery_appends_real_completion_order_and_keeps_task_live(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(2, 0, 1), include_source_tables=True)
    try:
        claim = claim_one(case)
        task_before = rows_for(case)["task"]
        applied = []

        delivery = ((1, 0), (0, 2), (2, 1))
        items_by_frame = {}
        for order, (selection_order, frame_index) in enumerate(delivery):
            item, prepared = prepare_item(
                case, claim, selection_order, intents=intent_adapter(case)
            )
            assert item.frame_index == frame_index
            assert prepared.disposition == "committed", (
                f"intent order {order} for frame {frame_index}: {prepared.disposition}"
            )
            text = f"optimized {frame_index}"
            receipt, fee = _finish_result_and_fee(case, claim, item, prepared, text)
            assert fee.result_ref == receipt.result_ref

            outcome = apply_current(case, claim, item.unit_id)
            assert outcome.kind == "applied"
            assert outcome.application_ref
            applied.append((frame_index, text, outcome.application_ref))
            items_by_frame[frame_index] = item

            replay = apply_current(case, claim, item.unit_id)
            assert replay.kind == "already_applied"
            assert replay.application_ref == outcome.application_ref

            rows = rows_for(case)
            current_task = rows["task"]
            assert current_task["status"] == "processing"
            assert current_task["claim_token"] == task_before["claim_token"]
            assert current_task["execution_generation"] == task_before["execution_generation"]
            assert current_task["lease_until"] == task_before["lease_until"]
            assert json.loads(current_task["result"]) == [
                {"index": index, "optimized_text": output}
                for index, output, _application_ref in applied
            ]
            assert len(rows["applications"]) == order + 1
            assert len(rows["receipts"]) == len(rows["evidence"]) == len(rows["work"]) == order + 1
            assert len(rows["fees"]) == len(rows["creditLogs"]) == order + 1
            assert rows["wallet"][0]["credits"] == 500 - 3 * (order + 1)
            assert rows["task"]["credit_cost"] == 3 * (order + 1)
            assert rows["task"]["billing_status"] == "charged"
            assert rows["task"]["result"] is not None
            assert rows["task"]["status"] == "processing"

            with case.sessions() as session:
                chapter = json.loads(
                    session.execute(select(chapters.c.content).where(chapters.c.id == "joint-chapter-1")).scalar_one()
                )
                for index, output, _ref in applied:
                    assert chapter[index]["text"] == output
                    assert chapter[index]["original_text"] == f"requested frame {index}"
                    assert chapter[index]["kept"] == index
                assets = session.execute(
                    select(storyboard_assets.c.id, storyboard_assets.c.description).order_by(storyboard_assets.c.id)
                ).all()
                assert dict(assets)[f"opt-storyboard-{frame_index}"] == text

        rows = rows_for(case)
        assert all(row.delivery_status == "applied" for row in rows["applications"])
        assert len({row.application_ref for row in rows["applications"]}) == 3
        assert all(row.phase == "finalize_pending" and row.phase_version == 2 for row in rows["work"])
        assert all(row.application_ref is None for row in rows["fees"])
        assert all(row["application_status"] == "applied" for row in rows["evidence"])
        assert all(row["execution_status"] == "applied" for row in rows["steps"])
        assert rows["task"]["status"] == "processing"
        first_item = items_by_frame[0]
        assert apply_current(case, claim, first_item.unit_id).kind == "already_applied"
    finally:
        case.engine.dispose()


def test_no_id_application_requires_reconstructed_full_chapter_and_preserves_paid_stop(tmp_path):
    case = admit_batch(
        tmp_path, request_indexes=(0, 1), include_source_tables=True, no_storyboard_ids=True
    )
    try:
        claim = claim_one(case)
        first, first_prepared = prepare_item(case, claim, order=0)
        _finish_result_and_fee(case, claim, first, first_prepared, "first optimized")
        first_outcome = apply_current(case, claim, first.unit_id)
        assert first_outcome.kind == "applied"

        with case.engine.begin() as connection:
            content = connection.execute(
                select(chapters.c.content).where(chapters.c.id == "joint-chapter-1")
            ).scalar_one()
            frames = json.loads(content)
            frames[1]["text"] = "human edit after admission"
            connection.execute(
                update(chapters).where(chapters.c.id == "joint-chapter-1").values(
                    content=json.dumps(frames, ensure_ascii=False)
                )
            )

        second, second_prepared = prepare_item(case, claim, order=1)
        second_receipt, second_fee = _finish_result_and_fee(
            case, claim, second, second_prepared, "must not overwrite human edit"
        )
        outcome = apply_current(case, claim, second.unit_id)
        assert outcome.kind == "target_conflict"

        rows = rows_for(case)
        task_result = json.loads(rows["task"]["result"])
        assert task_result == [{"index": 0, "optimized_text": "first optimized"}]
        assert rows["task"]["status"] == "processing"
        assert rows["task"]["credit_cost"] == 6
        assert rows["wallet"][0]["credits"] == 494
        assert len(rows["applications"]) == 1
        assert len(rows["fees"]) == len(rows["creditLogs"]) == 2
        stopped = next(row for row in rows["work"] if row.unit_id == second.unit_id)
        assert stopped.phase == "stopped_delivery_failed"
        assert stopped.phase_version == 2
        assert stopped.terminal_reason == "frame_optimize_target_conflict"
        assert stopped.application_ref is None
        assert second_fee.result_ref == second_receipt.result_ref
        second_evidence = next(row for row in rows["evidence"] if row["submission_id"] == second_receipt.submission_id)
        second_step = next(row for row in rows["steps"] if row["billing_unit_id"] == second.unit_id)
        assert second_evidence["application_status"] == "pending"
        assert second_step["execution_status"] == "result_observed"
        with case.sessions() as session:
            current = json.loads(
                session.execute(select(chapters.c.content).where(chapters.c.id == "joint-chapter-1")).scalar_one()
            )
            assert current[0]["text"] == "first optimized"
            assert current[1]["text"] == "human edit after admission"
    finally:
        case.engine.dispose()


def test_no_id_fresh_applications_and_replay_preserve_later_human_edit(tmp_path):
    case = admit_batch(
        tmp_path,
        frame_count=3,
        request_indexes=(0, 1, 2),
        include_source_tables=True,
        no_storyboard_ids=True,
    )
    try:
        claim = claim_one(case)
        applied = []
        for order, frame_index in enumerate((0, 1)):
            item, prepared = prepare_item(case, claim, order=order)
            output = f"no-id optimized {frame_index}"
            _finish_result_and_fee(case, claim, item, prepared, output)
            outcome = apply_current(case, claim, item.unit_id)
            assert outcome.kind == "applied"
            applied.append((frame_index, output))

        rows = rows_for(case)
        first_work = next(row for row in rows["work"] if row.unit_id == rows["receipts"][0].unit_id)
        replay = application_adapter(case).deliver_current(claim, first_work.work_id)
        assert replay.kind == "already_applied"

        with case.engine.begin() as connection:
            raw = connection.execute(
                select(chapters.c.content).where(chapters.c.id == "joint-chapter-1")
            ).scalar_one()
            frames = json.loads(raw)
            frames[2]["text"] = "human edit after two no-ID applications"
            connection.execute(
                update(chapters)
                .where(chapters.c.id == "joint-chapter-1")
                .values(content=json.dumps(frames, ensure_ascii=False))
            )

        third, prepared = prepare_item(case, claim, order=2)
        _finish_result_and_fee(case, claim, third, prepared, "must not overwrite the human edit")
        outcome = apply_current(case, claim, third.unit_id)
        assert outcome.kind == "target_conflict"

        rows = rows_for(case)
        assert json.loads(rows["task"]["result"]) == [
            {"index": index, "optimized_text": output} for index, output in applied
        ]
        assert rows["task"]["status"] == "processing"
        assert rows["task"]["credit_cost"] == 9
        assert rows["wallet"][0]["credits"] == 491
        assert len(rows["applications"]) == 2
        assert len(rows["fees"]) == len(rows["creditLogs"]) == 3
        with case.sessions() as session:
            current = json.loads(
                session.execute(
                    select(chapters.c.content).where(chapters.c.id == "joint-chapter-1")
                ).scalar_one()
            )
        assert current[0]["text"] == "no-id optimized 0"
        assert current[1]["text"] == "no-id optimized 1"
        assert current[2]["text"] == "human edit after two no-ID applications"
    finally:
        case.engine.dispose()


def test_application_transaction_exception_rolls_back_source_and_notes_writes(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(0,), include_source_tables=True)
    try:
        claim = claim_one(case)
        item, prepared = prepare_item(case, claim)
        receipt, fee = _finish_result_and_fee(case, claim, item, prepared, "must roll back")
        before = _source_snapshot(case)
        app_table = ApplicationRecordRow.__table__.name.lower()
        injected = {"raised": False}

        def fail_before_application_insert(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().lower().startswith("insert") and app_table in statement.lower():
                injected["raised"] = True
                raise RuntimeError("injected failure after source and notes staging")

        event.listen(case.engine, "before_cursor_execute", fail_before_application_insert)
        try:
            outcome = apply_current(case, claim, item.unit_id)
        finally:
            event.remove(case.engine, "before_cursor_execute", fail_before_application_insert)

        assert injected["raised"] is True
        assert outcome.kind == "write_failed"
        assert _source_snapshot(case) == before
        rows = rows_for(case)
        assert rows["applications"] == []
        assert len(rows["fees"]) == len(rows["creditLogs"]) == 1
        assert rows["fees"][0].generation_result_ref == receipt.result_ref
        assert rows["fees"][0].unit_id == item.unit_id
        assert rows["fees"][0].application_ref is None
        assert rows["wallet"][0]["credits"] == 497
        assert rows["task"]["result"] is None
        assert rows["work"][0].phase == "delivery_pending"
        assert rows["evidence"][0]["application_status"] == "pending"
        assert rows["steps"][0]["execution_status"] == "result_observed"
    finally:
        case.engine.dispose()


def test_final_fresh_lease_fence_rolls_back_source_and_notes_writes(tmp_path):
    case = admit_batch(tmp_path, request_indexes=(0,), include_source_tables=True)
    try:
        claim = claim_one(case)
        item, prepared = prepare_item(case, claim)
        receipt, _fee = _finish_result_and_fee(case, claim, item, prepared, "fence must stop apply")
        before = _source_snapshot(case)
        chapter_table = chapters.name.lower()
        injected = {"advanced": False}

        def expire_lease_after_source_write(_connection, _cursor, statement, _parameters, _context, _many):
            if (
                not injected["advanced"]
                and statement.lstrip().lower().startswith("update")
                and chapter_table in statement.lower()
            ):
                injected["advanced"] = True
                case.clock[0] = claim.lease_until

        event.listen(case.engine, "after_cursor_execute", expire_lease_after_source_write)
        try:
            outcome = apply_current(case, claim, item.unit_id)
        finally:
            event.remove(case.engine, "after_cursor_execute", expire_lease_after_source_write)

        assert injected["advanced"] is True
        assert outcome.kind == "write_failed"
        assert _source_snapshot(case) == before
        rows = rows_for(case)
        assert rows["applications"] == []
        assert len(rows["receipts"]) == len(rows["fees"]) == len(rows["creditLogs"]) == 1
        assert rows["receipts"][0].result_ref == receipt.result_ref
        assert rows["wallet"][0]["credits"] == 497
        assert rows["task"]["status"] == "processing"
        assert rows["task"]["result"] is None
        assert rows["work"][0].phase == "delivery_pending"
        assert rows["evidence"][0]["application_status"] == "pending"
        assert rows["steps"][0]["execution_status"] == "result_observed"
    finally:
        case.engine.dispose()


@pytest.mark.parametrize(
    "corruption",
    (
        "task_result",
        "work_application_ref",
        "work_fee_decision_ref",
        "work_result_ref",
        "application_result_ref",
        "fee_application_ref",
    ),
)
def test_partial_or_mislinked_application_graph_is_rejected(tmp_path, corruption):
    case = admit_batch(
        tmp_path,
        frame_count=3,
        request_indexes=(0, 1, 2),
        include_source_tables=True,
    )
    try:
        claim = claim_one(case)
        items = []
        for order in range(3):
            item, prepared = prepare_item(case, claim, order=order)
            _finish_result_and_fee(case, claim, item, prepared, f"graph result {order}")
            items.append(item)
            if order < 2:
                assert apply_current(case, claim, item.unit_id).kind == "applied"
        rows = rows_for(case)
        apps_by_unit = {row.unit_id: row for row in rows["applications"]}
        works_by_unit = {row.unit_id: row for row in rows["work"]}
        receipts_by_unit = {row.unit_id: row for row in rows["receipts"]}
        first_app = apps_by_unit[items[0].unit_id]
        second_app = apps_by_unit[items[1].unit_id]
        first_work = works_by_unit[items[0].unit_id]
        second_receipt = receipts_by_unit[items[1].unit_id]

        with case.engine.begin() as connection:
            if corruption == "task_result":
                connection.execute(
                    update(AITaskRow).where(AITaskRow.id == case.task_id).values(result="[]")
                )
            elif corruption == "work_application_ref":
                connection.execute(
                    update(LocalContinuationWorkRow)
                    .where(LocalContinuationWorkRow.work_id == first_work.work_id)
                    .values(application_ref=second_app.application_ref)
                )
            elif corruption == "work_fee_decision_ref":
                connection.execute(
                    update(LocalContinuationWorkRow)
                    .where(LocalContinuationWorkRow.work_id == first_work.work_id)
                    .values(fee_decision_ref=items[1].unit_id)
                )
            elif corruption == "work_result_ref":
                connection.execute(
                    update(LocalContinuationWorkRow)
                    .where(LocalContinuationWorkRow.work_id == first_work.work_id)
                    .values(result_ref=second_receipt.result_ref)
                )
            elif corruption == "application_result_ref":
                connection.execute(
                    update(ApplicationRecordRow)
                    .where(ApplicationRecordRow.application_ref == first_app.application_ref)
                    .values(result_ref=second_receipt.result_ref)
                )
            else:
                connection.execute(
                    update(UnitFeeDecisionRow)
                    .where(UnitFeeDecisionRow.unit_id == items[0].unit_id)
                    .values(application_ref=second_app.application_ref)
                )

        replay = apply_current(case, claim, items[2].unit_id)
        assert replay.kind == "write_failed"
        corrupted_rows = rows_for(case)
        assert len(corrupted_rows["applications"]) == 2
        assert len(corrupted_rows["fees"]) == len(corrupted_rows["creditLogs"]) == 3
        assert corrupted_rows["wallet"][0]["credits"] == 491
        third_work = next(row for row in corrupted_rows["work"] if row.unit_id == items[2].unit_id)
        assert third_work.phase == "delivery_pending"
        assert corrupted_rows["task"]["status"] == "processing"
    finally:
        case.engine.dispose()


def test_paid_delivery_drains_after_a_sibling_stops_for_insufficient_credits(tmp_path):
    case = admit_batch(
        tmp_path,
        frame_count=3,
        request_indexes=(2, 0),
        wallet_credits=3,
        include_source_tables=True,
    )
    try:
        generation_claim = claim_one(case)
        first, first_prepared = prepare_item(case, generation_claim, order=0)
        _finish_result_and_fee(case, generation_claim, first, first_prepared, "paid sibling")

        second, second_prepared = prepare_item(case, generation_claim, order=1)
        persist_result(case, generation_claim, second_prepared, text="insufficient sibling")
        second_work = next(
            row for row in rows_for(case)["work"] if row.unit_id == second.unit_id
        )
        second_fee = fee_adapter(case).resolve_current_fee(generation_claim, second_work.work_id)
        assert second_fee.disposition == "stopped_insufficient"
        assert rows_for(case)["task"]["recovery_status"] == "stopped_insufficient"

        case.clock[0] += timedelta(seconds=61)
        continuations = continuation_claim_adapter(case, token="paid-delivery-drain")
        continuation = continuations.claim_continuation("batch-optimize", "drain-worker")
        assert continuation is not None
        assert continuation.unit_id == first.unit_id

        outcome = application_adapter(
            case, continuation_claims=continuations
        ).consume_delivery(continuation)
        assert outcome.kind == "applied"

        assert continuations.release_continuation(continuation) is True
        rows = rows_for(case)
        assert rows["task"]["status"] == "reconciling"
        assert rows["task"]["recovery_status"] == "stopped_insufficient"
        assert rows["task"]["credit_cost"] == 3
        assert rows["wallet"][0]["credits"] == 0
        assert json.loads(rows["task"]["result"]) == [{
            "index": 2,
            "optimized_text": "paid sibling",
        }]
        assert len(rows["applications"]) == 1
        assert len(rows["fees"]) == 2
        first_work = next(row for row in rows["work"] if row.unit_id == first.unit_id)
        second_work = next(row for row in rows["work"] if row.unit_id == second.unit_id)
        assert first_work.phase == "finalize_pending"
        assert second_work.phase == "stopped_insufficient"
        assert second_work.fee_decision_ref == second.unit_id
    finally:
        case.engine.dispose()


def test_fee_confirmation_accepts_atomic_application_that_advanced_work(tmp_path):
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    case = admit_batch(tmp_path, request_indexes=(0,), include_source_tables=True)
    try:
        claim = claim_one(case)
        item, prepared = prepare_item(case, claim)
        receipt = persist_result(case, claim, prepared, text="apply before fee confirmation")
        assert not isinstance(receipt, str)
        work = next(row for row in rows_for(case)["work"] if row.unit_id == item.unit_id)
        application = application_adapter(case)
        state = {"advanced": False, "outcome": None}

        def apply_after_fee_commit(session):
            if session.bind is case.engine and not state["advanced"]:
                state["advanced"] = True
                state["outcome"] = apply_current(
                    case, claim, item.unit_id, adapter=application
                )

        event.listen(Session, "after_commit", apply_after_fee_commit)
        try:
            fee = fee_adapter(case).resolve_current_fee(claim, work.work_id)
        finally:
            event.remove(Session, "after_commit", apply_after_fee_commit)

        assert state["advanced"] is True
        assert state["outcome"].kind == "applied"
        assert fee.disposition == "charged"
        rows = rows_for(case)
        assert len(rows["applications"]) == 1
        assert len(rows["fees"]) == len(rows["creditLogs"]) == 1
        assert rows["wallet"][0]["credits"] == 497
        assert rows["task"]["status"] == "processing"
        assert json.loads(rows["task"]["result"]) == [
            {"index": 0, "optimized_text": "apply before fee confirmation"}
        ]
    finally:
        case.engine.dispose()


def test_legacy_original_text_truthiness_is_preserved_for_optimize(tmp_path):
    case = admit_batch(
        tmp_path,
        request_indexes=(0, 1),
        include_source_tables=True,
        original_text_by_index={0: " ", 1: ""},
    )
    try:
        claim = claim_one(case)
        for order in range(2):
            item, prepared = prepare_item(case, claim, order=order)
            _finish_result_and_fee(case, claim, item, prepared, f"truthy {order}")
            assert apply_current(case, claim, item.unit_id).kind == "applied"
        with case.sessions() as session:
            frames = json.loads(
                session.execute(select(chapters.c.content).where(chapters.c.id == "joint-chapter-1")).scalar_one()
            )
        assert frames[0]["original_text"] == " "
        assert frames[1]["original_text"] == "requested frame 1"
    finally:
        case.engine.dispose()


def test_application_commit_acknowledgement_loss_confirms_original_write(tmp_path):
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    case = admit_batch(tmp_path, request_indexes=(0,), include_source_tables=True)
    try:
        claim = claim_one(case)
        item, prepared = prepare_item(case, claim)
        _finish_result_and_fee(case, claim, item, prepared, "committed application")
        work = next(row for row in rows_for(case)["work"] if row.unit_id == item.unit_id)
        adapter = application_adapter(case, token="application-ack-claim")
        state = {"lose": True}

        def lose_ack_once(session):
            if session.bind is case.engine and state["lose"]:
                state["lose"] = False
                raise RuntimeError("fixture lost P5 commit acknowledgement")

        event.listen(Session, "after_commit", lose_ack_once)
        try:
            outcome = adapter.deliver_current(claim, work.work_id)
        finally:
            event.remove(Session, "after_commit", lose_ack_once)
        assert state["lose"] is False
        assert outcome.kind == "applied"
        rows = rows_for(case)
        assert len(rows["applications"]) == 1
        assert len(rows["creditLogs"]) == 1
        assert rows["wallet"][0]["credits"] == 497
        assert json.loads(rows["task"]["result"]) == [
            {"index": 0, "optimized_text": "committed application"}
        ]
    finally:
        case.engine.dispose()


def test_application_ack_loss_does_not_confirm_reordered_task_result(tmp_path):
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    case = admit_batch(
        tmp_path,
        request_indexes=(0, 1),
        include_source_tables=True,
    )
    try:
        claim = claim_one(case)
        first, first_prepared = prepare_item(case, claim, order=0)
        _finish_result_and_fee(case, claim, first, first_prepared, "original first")
        assert apply_current(case, claim, first.unit_id).kind == "applied"

        second, second_prepared = prepare_item(case, claim, order=1)
        _finish_result_and_fee(case, claim, second, second_prepared, "original second")
        state = {"changed": False}

        def reorder_committed_result_and_lose_ack(session):
            if session.bind is not case.engine or state["changed"]:
                return
            state["changed"] = True
            with case.engine.begin() as connection:
                raw = connection.execute(
                    select(AITaskRow.__table__.c.result).where(
                        AITaskRow.__table__.c.id == case.task_id
                    )
                ).scalar_one()
                entries = json.loads(raw)
                assert entries == [
                    {"index": 0, "optimized_text": "original first"},
                    {"index": 1, "optimized_text": "original second"},
                ]
                entries.reverse()
                connection.execute(
                    update(AITaskRow.__table__)
                    .where(AITaskRow.__table__.c.id == case.task_id)
                    .values(result=json.dumps(entries, separators=(",", ":")))
                )
            raise RuntimeError("injected lost P5 acknowledgement after result reorder")

        event.listen(Session, "after_commit", reorder_committed_result_and_lose_ack)
        try:
            outcome = apply_current(case, claim, second.unit_id)
        finally:
            event.remove(Session, "after_commit", reorder_committed_result_and_lose_ack)

        assert state["changed"] is True
        assert outcome.kind == "write_failed"
        rows = rows_for(case)
        assert json.loads(rows["task"]["result"]) == [
            {"index": 1, "optimized_text": "original second"},
            {"index": 0, "optimized_text": "original first"},
        ]
        assert len(rows["applications"]) == 2
        assert len(rows["fees"]) == len(rows["creditLogs"]) == 2
        assert rows["wallet"][0]["credits"] == 494
        assert all(row.phase == "finalize_pending" for row in rows["work"])
    finally:
        case.engine.dispose()


@pytest.mark.parametrize("corruption", ("media_revision_after", "notes_revision_pair"))
def test_progress_rejects_application_with_missing_source_revision_pair(
    tmp_path, corruption
):
    from haoai_backend.generation_core.frame_optimize_execution import lock_frame_optimize_batch
    from haoai_backend.generation_core.frame_optimize_progress import lock_frame_optimize_progress

    case = admit_batch(
        tmp_path,
        request_indexes=(0, 1),
        include_source_tables=True,
    )
    try:
        claim = claim_one(case)
        first, first_prepared = prepare_item(case, claim, order=0)
        _finish_result_and_fee(case, claim, first, first_prepared, "source revision baseline")
        assert apply_current(case, claim, first.unit_id).kind == "applied"
        first_app = next(
            row for row in rows_for(case)["applications"] if row.unit_id == first.unit_id
        )

        with case.engine.begin() as connection:
            values = (
                {"media_revision_after": None}
                if corruption == "media_revision_after"
                else {"notes_revision_before": None, "notes_revision_after": None}
            )
            connection.execute(
                update(ApplicationRecordRow)
                .where(ApplicationRecordRow.application_ref == first_app.application_ref)
                .values(**values)
            )

        with case.sessions() as session:
            graph = lock_frame_optimize_batch(
                session,
                case.actor_id,
                case.task_id,
                crypto=case.capabilities.crypto,
                proof_authority=case.capabilities.proof_authority,
            )
            assert graph is not None
            assert lock_frame_optimize_progress(session, graph) is None

        before = rows_for(case)
        second, prepared = prepare_item(case, claim, order=1)
        after = rows_for(case)
        assert second.frame_index == 1
        assert prepared.disposition != "committed"
        assert prepared.intent is None
        assert len(after["applications"]) == len(before["applications"]) == 1
        assert len(after["fees"]) == len(after["creditLogs"]) == 1
        assert after["wallet"] == before["wallet"]
        assert after["task"]["result"] == before["task"]["result"]
    finally:
        case.engine.dispose()


def test_progress_rejects_application_with_mismatched_full_frame_digest(tmp_path):
    from sqlalchemy import update

    from haoai_backend.generation_core.frame_optimize_execution import lock_frame_optimize_batch
    from haoai_backend.generation_core.frame_optimize_progress import lock_frame_optimize_progress

    case = admit_batch(tmp_path, request_indexes=(0,), include_source_tables=True)
    try:
        claim = claim_one(case)
        item, prepared = prepare_item(case, claim)
        _finish_result_and_fee(case, claim, item, prepared, "verified then tampered")
        assert apply_current(case, claim, item.unit_id).kind == "applied"
        app = rows_for(case)["applications"][0]
        with case.engine.begin() as connection:
            connection.execute(
                update(ApplicationRecordRow)
                .where(ApplicationRecordRow.application_ref == app.application_ref)
                .values(after_target_digest="0" * 64)
            )
        with case.sessions() as session:
            graph = lock_frame_optimize_batch(
                session,
                case.actor_id,
                case.task_id,
                crypto=case.capabilities.crypto,
                proof_authority=case.capabilities.proof_authority,
            )
            assert graph is not None
            assert lock_frame_optimize_progress(session, graph) is None
    finally:
        case.engine.dispose()


@pytest.mark.parametrize("marker_scope", ("selected", "sibling"))
def test_application_rejects_any_unit_cancel_marker(tmp_path, marker_scope):
    from haoai_backend.generation_core.base_schema import BillingUnitRow

    case = admit_batch(
        tmp_path,
        frame_count=2,
        request_indexes=(0, 1),
        include_source_tables=True,
    )
    try:
        claim = claim_one(case)
        item, prepared = prepare_item(case, claim, order=0)
        receipt, fee = _finish_result_and_fee(
            case, claim, item, prepared, "cancel marker must fence P5"
        )
        assert fee.result_ref == receipt.result_ref

        units = rows_for(case)["units"]
        sibling_id = next(row["id"] for row in units if row["id"] != item.unit_id)
        marked_unit_id = item.unit_id if marker_scope == "selected" else sibling_id
        with case.engine.begin() as connection:
            changed = connection.execute(
                update(BillingUnitRow)
                .where(BillingUnitRow.id == marked_unit_id)
                .values(
                    user_cancelled_at=case.clock[0],
                    cancellation_reason="user_request",
                )
            )
            assert changed.rowcount == 1

        before = rows_for(case)
        assert before["task"]["user_cancelled_at"] is None
        assert next(row for row in before["units"] if row["id"] == marked_unit_id)[
            "user_cancelled_at"
        ] is not None

        outcome = apply_current(case, claim, item.unit_id)
        after = rows_for(case)
        assert outcome.kind == "write_failed", (
            f"{marker_scope} Unit cancellation marker was ignored: "
            f"outcome={outcome.kind}, applications={len(after['applications'])}, "
            f"work_phase={after['work'][0].phase}"
        )
        assert after["task"]["user_cancelled_at"] is None
        assert after["applications"] == []
        assert len(after["fees"]) == len(after["creditLogs"]) == 1
        assert after["wallet"][0]["credits"] == 497
        assert after["work"][0].phase == "delivery_pending"
        assert after["task"]["result"] is None
    finally:
        case.engine.dispose()


def test_application_final_fence_rejects_unit_cancel_marker_added_after_source_stage(
    tmp_path,
):
    from haoai_backend.generation_core.base_schema import BillingUnitRow

    case = admit_batch(
        tmp_path,
        frame_count=2,
        request_indexes=(0, 1),
        include_source_tables=True,
    )
    try:
        claim = claim_one(case)
        item, prepared = prepare_item(case, claim)
        receipt, fee = _finish_result_and_fee(
            case, claim, item, prepared, "final cancellation fence"
        )
        source_before = _source_snapshot(case)
        sibling_id = next(
            row["id"] for row in rows_for(case)["units"] if row["id"] != item.unit_id
        )
        state = {"marked": False}

        def cancel_after_chapter_write(
            _connection, _cursor, statement, _parameters, _context, _many
        ):
            if (
                not state["marked"]
                and statement.lstrip().lower().startswith("update")
                and chapters.name.lower() in statement.lower()
            ):
                state["marked"] = True
                _connection.execute(
                    update(BillingUnitRow)
                    .where(BillingUnitRow.id == sibling_id)
                    .values(
                        user_cancelled_at=case.clock[0],
                        cancellation_reason="user_request",
                    )
                )

        event.listen(case.engine, "after_cursor_execute", cancel_after_chapter_write)
        try:
            outcome = apply_current(case, claim, item.unit_id)
        finally:
            event.remove(case.engine, "after_cursor_execute", cancel_after_chapter_write)

        assert state["marked"] is True
        assert outcome.kind == "write_failed"
        assert _source_snapshot(case) == source_before
        rows = rows_for(case)
        assert rows["applications"] == []
        assert len(rows["receipts"]) == len(rows["fees"]) == len(rows["creditLogs"]) == 1
        assert rows["fees"][0].generation_result_ref == receipt.result_ref
        assert rows["fees"][0].application_ref is None
        assert rows["wallet"][0]["credits"] == 497
        assert rows["task"]["result"] is None
        assert rows["work"][0].phase == "delivery_pending"
        assert next(row for row in rows["units"] if row["id"] == sibling_id)[
            "user_cancelled_at"
        ] is None
        assert fee.disposition == "charged"
    finally:
        case.engine.dispose()


@pytest.mark.parametrize("marker_timing", ("before_claim", "after_claim"))
def test_independent_delivery_recovery_rejects_sibling_unit_cancel_marker(
    tmp_path, marker_timing
):
    from haoai_backend.generation_core.base_schema import BillingUnitRow

    case = admit_batch(tmp_path, request_indexes=(0, 1), include_source_tables=True)
    try:
        generation_claims = claim_adapter(case)
        claim = generation_claims.claim_next("batch-optimize", "generation-worker")
        assert claim is not None
        item, prepared = prepare_item(case, claim, order=0)
        _finish_result_and_fee(case, claim, item, prepared, "recovery must be fenced")
        assert generation_claims.release_claim(claim) is True

        continuations = continuation_claim_adapter(case, token=f"cancelled-unit-{marker_timing}")
        continuation = None
        if marker_timing == "after_claim":
            continuation = continuations.claim_continuation(
                "batch-optimize", "delivery-recovery-worker"
            )
            assert continuation is not None
        sibling_id = next(
            row["id"] for row in rows_for(case)["units"] if row["id"] != item.unit_id
        )
        with case.engine.begin() as connection:
            connection.execute(
                update(BillingUnitRow)
                .where(BillingUnitRow.id == sibling_id)
                .values(
                    user_cancelled_at=case.clock[0],
                    cancellation_reason="user_request",
                )
            )

        if marker_timing == "before_claim":
            continuation = continuations.claim_continuation(
                "batch-optimize", "delivery-recovery-worker"
            )
        rows = rows_for(case)
        if marker_timing == "before_claim":
            assert continuation is None
            assert rows["task"]["status"] == "reconciling"
            assert rows["task"]["claim_token"] is None
            assert rows["task"]["lease_until"] is None
            assert rows["task"]["execution_generation"] == claim.execution_generation
        else:
            assert continuation is not None
            assert rows["task"]["status"] == "processing"
            assert rows["task"]["claim_token"] is not None
            assert rows["task"]["execution_generation"] == continuation.execution_generation
            outcome = application_adapter(
                case, continuation_claims=continuations
            ).consume_delivery(continuation)
            assert outcome.kind == "write_failed"
            rows = rows_for(case)
        assert rows["applications"] == []
        assert len(rows["fees"]) == len(rows["creditLogs"]) == 1
        assert rows["wallet"][0]["credits"] == 497
        assert rows["work"][0].phase == "delivery_pending"
    finally:
        case.engine.dispose()


def test_existing_application_remains_readable_after_late_unit_cancel_and_expired_lease(tmp_path):
    from haoai_backend.generation_core.base_schema import BillingUnitRow

    case = admit_batch(tmp_path, request_indexes=(0, 1), include_source_tables=True)
    try:
        claim = claim_one(case)
        item, prepared = prepare_item(case, claim, order=0)
        receipt, fee = _finish_result_and_fee(
            case, claim, item, prepared, "committed before late marker"
        )
        applied = apply_current(case, claim, item.unit_id)
        assert applied.kind == "applied"

        with case.engine.begin() as connection:
            connection.execute(
                update(BillingUnitRow)
                .where(BillingUnitRow.id == item.unit_id)
                .values(
                    user_cancelled_at=case.clock[0],
                    cancellation_reason="user_request",
                )
            )
        case.clock[0] = claim.lease_until

        work = next(row for row in rows_for(case)["work"] if row.unit_id == item.unit_id)
        replay = application_adapter(case).deliver_current(claim, work.work_id)
        rows = rows_for(case)
        assert replay.kind == "already_applied"
        assert replay.application_ref == applied.application_ref
        assert len(rows["applications"]) == 1
        assert len(rows["fees"]) == len(rows["creditLogs"]) == 1
        assert rows["fees"][0].generation_result_ref == receipt.result_ref
        assert rows["wallet"][0]["credits"] == 497
        assert rows["task"]["result"] == '[{"index":0,"optimized_text":"committed before late marker"}]'
        assert fee.disposition == "charged"
    finally:
        case.engine.dispose()
