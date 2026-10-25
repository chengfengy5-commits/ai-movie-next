"""Focused HTTP and caller-session matrix for indexed Canvas results."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import event, insert, inspect, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from haoai_backend.asset_data.generation import digest_asset_target
from haoai_backend.asset_data.tables import ASSET_TABLES, storyboard_assets
from haoai_backend.authentication.tables import users as authentication_users
from haoai_backend.generation_contracts.canvas_targets import (
    CANVAS_ASSET_TARGET,
    CANVAS_FRAME_TARGET,
    canonical_target_snapshot_digest,
    target_snapshot_mapping,
)
from haoai_backend.generation_contracts.records import GenerationResultReceipt
from haoai_backend.generation_contracts.types import TargetSnapshot
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
    ResultEvidenceRow,
    UserRow,
)
from haoai_backend.generation_core.persistence import GenerationRecordRepository
from haoai_backend.generation_core.schema import (
    GenerationResultTargetRow,
)
from haoai_backend.generation_catalog.tables import (
    ASSET_TABLES as CATALOG_ASSET_TABLES,
    chapters as catalog_chapters,
)
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.task_observation import canvas_result_discovery
from haoai_backend.task_observation.canvas_result_discovery import (
    backfill_canvas_result_targets,
)
from test_app_chat_media_admission_integration import (
    _G2_JOINT_ACTOR_ID,
    _G2_JOINT_NOW,
    _assert_last_lookup_session_is_read_only,
    _build_system,
    _canvas_lookup_write_state,
    _get,
)


def _uuid() -> str:
    return str(uuid4())


def _canonical_json_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _ensure_current_asset_columns(engine: Engine) -> None:
    inspector = inspect(engine)
    table_models = [*ASSET_TABLES.values(), storyboard_assets]
    with engine.begin() as connection:
        for table in table_models:
            existing = {column["name"] for column in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                column_type = column.type.compile(dialect=engine.dialect)
                connection.exec_driver_sql(
                    f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column_type}'
                )
                existing.add(column.name)


def _seed_canvas_sources(engine: Engine) -> tuple[list[dict[str, object]], TargetSnapshot]:
    _ensure_current_asset_columns(engine)
    now = _G2_JOINT_NOW.replace(tzinfo=None)
    frames: list[dict[str, object]] = [
        {"text": "稳定目标画面", "storyboard": ["matrix-board-1"]},
        {"text": "可重排画面", "storyboard": ["matrix-board-2"]},
    ]
    raw_chapter = json.dumps(
        frames,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    with engine.begin() as connection:
        connection.execute(
            update(catalog_chapters)
            .where(catalog_chapters.c.id == "joint-chapter-1")
            .values(content=raw_chapter)
        )
        for index, frame_id in enumerate(("matrix-board-1", "matrix-board-2")):
            connection.execute(
                insert(storyboard_assets).values(
                    id=frame_id,
                    series_id="joint-series-1",
                    chapter_id="joint-chapter-1",
                    frame_index=index,
                    name=f"Matrix frame {index}",
                    description=None,
                    image_url=None,
                    created_at=now,
                    updated_at=now,
                )
            )
        prop_table = CATALOG_ASSET_TABLES["prop"]
        connection.execute(
            insert(prop_table).values(
                id="matrix-prop-1",
                series_id="joint-series-1",
                name="Matrix prop",
                description="Fixture prop description",
                image_url=None,
                updated_at=now,
            )
        )

    stable_target = TargetSnapshot(
        kind=CANVAS_FRAME_TARGET,
        chapter_id="joint-chapter-1",
        series_id="joint-series-1",
        stable_storyboard_asset_id="matrix-board-1",
        compat_frame_index=0,
        full_chapter_digest=_canonical_json_digest(frames),
        full_frame_digest=_canonical_json_digest(frames[0]),
    )
    return frames, stable_target


def _asset_target(
    engine: Engine,
    asset_type: str,
    asset_id: str,
    *,
    chapter_id: str = "joint-chapter-1",
    series_id: str = "joint-series-1",
) -> TargetSnapshot:
    table = ASSET_TABLES[asset_type]
    with engine.connect() as connection:
        row = connection.execute(
            select(table).where(
                table.c.id == asset_id,
                table.c.series_id == series_id,
            )
        ).mappings().one()
    return TargetSnapshot(
        kind=CANVAS_ASSET_TARGET,
        chapter_id=chapter_id,
        series_id=series_id,
        asset_type=asset_type,
        asset_id=asset_id,
        expected_asset_image_url=row.get("image_url"),
        same_series_target_digest=digest_asset_target(asset_type, row),
    )


def _seed_canonical_receipt(
    engine: Engine,
    target: TargetSnapshot | None,
    *,
    result_ref: str | None = None,
    owner_id: str = _G2_JOINT_ACTOR_ID,
    fulfilled_at=None,
    request_data_override: str | None = None,
) -> dict[str, str]:
    """Seed a controlled canonical receipt through the production repository."""

    result_ref = result_ref or _uuid()
    task_id, unit_id, step_id = _uuid(), _uuid(), _uuid()
    submission_id, evidence_id = _uuid(), _uuid()
    now = fulfilled_at or _G2_JOINT_NOW
    target_mapping = None if target is None else target_snapshot_mapping(target)
    request_data = request_data_override
    if request_data is None:
        request_root = (
            {"target": {"kind": "standalone"}}
            if target_mapping is None
            else {"target_snapshot": target_mapping}
        )
        request_data = json.dumps(
            request_root,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
    snapshots = (
        {}
        if target_mapping is None
        else {"target_snapshot": target_mapping}
    )
    digest = hashlib.sha256(result_ref.encode("utf-8")).hexdigest()

    with Session(engine, expire_on_commit=False) as session:
        if session.get(UserRow, owner_id) is None:
            session.execute(
                insert(authentication_users).values(
                    id=owner_id,
                    username=f"fixture-{owner_id}",
                    email=f"{owner_id}@fixture.example.test",
                    hashed_password="synthetic-fixture-only",
                    is_superuser=False,
                    membership_type="free",
                    created_at=now.replace(tzinfo=None),
                )
            )
        task = AITaskRow(
            id=task_id,
            user_id=owner_id,
            type="image-single",
            status="completed",
            billing_status="unbilled",
            message_id=_uuid(),
            request_data=request_data,
            created_at=now.replace(tzinfo=None),
            updated_at=now.replace(tzinfo=None),
        )
        unit = BillingUnitRow(
            id=unit_id,
            task_id=task_id,
            item_key="result-" + result_ref,
            operation="image.single",
            step_graph_version="image-single-v1",
            input_digest="a" * 64,
            quoted_amount=0,
            billing_status="unbilled",
            result_status="succeeded",
            first_intent_at=now,
            deadline_at=now + timedelta(hours=1),
            success_confirmed_at=now,
        )
        step = ExecutionStepRow(
            id=step_id,
            billing_unit_id=unit_id,
            step_key="image.generate",
            dependencies=[],
            input_digest="b" * 64,
            input_snapshot=snapshots,
            required=True,
            step_order=0,
            execution_status="result_observed",
        )
        submission = ExternalSubmissionRow(
            id=submission_id,
            step_id=step_id,
            submission_key="fixture-" + submission_id,
            provider="controlled-fixture",
            request_digest="c" * 64,
            request_snapshot=snapshots,
            intent_at=now,
            submitted_at=now,
            external_task_id="fixture-remote-" + submission_id,
            submission_status="succeeded",
            deadline_at=now + timedelta(hours=1),
        )
        evidence = ResultEvidenceRow(
            id=evidence_id,
            step_id=step_id,
            submission_id=submission_id,
            evidence_key="fixture-evidence-" + evidence_id,
            evidence_kind="provider_result",
            payload_digest=digest,
            result_location="managed://synthetic/canvas-matrix/" + result_ref,
            result_digest=digest,
            received_at=now,
            validity="valid",
            application_status="pending",
        )
        session.add_all((task, unit, step, submission, evidence))
        session.flush()
        receipt = GenerationResultReceipt(
            result_ref=result_ref,
            owner_id=owner_id,
            task_id=task_id,
            unit_id=unit_id,
            operation="image.single",
            step_id=step_id,
            submission_id=submission_id,
            evidence_id=evidence_id,
            selected_attempt_id="fixture-attempt-" + result_ref,
            recoverable_locator="managed://synthetic/canvas-matrix/" + result_ref,
            content_digest=digest,
            validation_version="controlled-canonical-fixture-v1",
            validated_at=now,
            generation_fulfilled_at=now,
            late_disposition=None,
            policy_stop_disposition=None,
            automatic_application_allowed=True,
        )
        written = GenerationRecordRepository(session).stage_record(receipt)
        assert written.status == "created", written
        if target is not None:
            projection = session.get(GenerationResultTargetRow, result_ref)
            assert projection is not None
            assert projection.target_snapshot_digest == canonical_target_snapshot_digest(
                target
            )
        session.commit()

    return {
        "result_ref": result_ref,
        "task_id": task_id,
        "unit_id": unit_id,
        "step_id": step_id,
        "submission_id": submission_id,
        "evidence_id": evidence_id,
    }


def _discovery_path(target: TargetSnapshot, *, limit: int | None = None) -> str:
    if target.kind == CANVAS_FRAME_TARGET:
        selector = "target_kind=frame_preview&storyboard_asset_id=" + str(
            target.stable_storyboard_asset_id
        )
    else:
        selector = (
            "target_kind=asset_image&asset_type="
            + str(target.asset_type)
            + "&asset_id="
            + str(target.asset_id)
        )
    if limit is not None:
        selector += f"&limit={limit}"
    return f"/api/chapters/{target.chapter_id}/canvas-owned-results?{selector}"


def _assert_ready_candidate(system, path: str, target: TargetSnapshot) -> dict[str, object]:
    before = _canvas_lookup_write_state(system.engine)
    response = _get(system, path)
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["chapter_id"] == target.chapter_id
    assert page["target"]["chapter_id"] == target.chapter_id
    assert len(page["items"]) == 1
    item = page["items"][0]
    assert item["eligibility"] == "ready"
    assert item["expected_target"] == target_snapshot_mapping(target)
    assert len(item["application_key"]) == 64
    assert item["application"] is None
    _assert_last_lookup_session_is_read_only(system)
    assert _canvas_lookup_write_state(system.engine) == before
    return item


def _get_read_only(system, path: str):
    before = _canvas_lookup_write_state(system.engine)
    response = _get(system, path)
    _assert_last_lookup_session_is_read_only(system)
    assert _canvas_lookup_write_state(system.engine) == before
    return response


def test_real_http_revalidates_stable_frame_and_all_asset_candidate_kinds(
    tmp_path: Path,
) -> None:
    system = _build_system(tmp_path)
    try:
        frames, stable_target = _seed_canvas_sources(system.engine)
        targets = {
            "frame": stable_target,
            "character": _asset_target(system.engine, "character", "joint-character-1"),
            "scene": _asset_target(system.engine, "scene", "joint-scene-1"),
            "prop": _asset_target(system.engine, "prop", "matrix-prop-1"),
        }
        for index, target in enumerate(targets.values()):
            _seed_canonical_receipt(
                system.engine,
                target,
                result_ref=f"ready-candidate-{index:02d}",
            )

        for target in targets.values():
            _assert_ready_candidate(system, _discovery_path(target), target)

        reordered = [frames[1], frames[0]]
        with system.engine.begin() as connection:
            connection.execute(
                update(catalog_chapters)
                .where(catalog_chapters.c.id == "joint-chapter-1")
                .values(content=json.dumps(reordered, ensure_ascii=False, separators=(",", ":")))
            )
            connection.execute(
                update(storyboard_assets)
                .where(storyboard_assets.c.id == "matrix-board-1")
                .values(frame_index=1)
            )
        _assert_ready_candidate(system, _discovery_path(stable_target), stable_target)

        duplicate = [frames[1], frames[0], frames[0]]
        with system.engine.begin() as connection:
            connection.execute(
                update(catalog_chapters)
                .where(catalog_chapters.c.id == "joint-chapter-1")
                .values(content=json.dumps(duplicate, ensure_ascii=False, separators=(",", ":")))
            )
        duplicate_response = _get_read_only(system, _discovery_path(stable_target))
        assert duplicate_response.status_code == 200, duplicate_response.text
        assert duplicate_response.json()["items"][0]["eligibility"] == "stale"

        edited = [frames[1], {**frames[0], "text": "已人工编辑"}]
        with system.engine.begin() as connection:
            connection.execute(
                update(catalog_chapters)
                .where(catalog_chapters.c.id == "joint-chapter-1")
                .values(content=json.dumps(edited, ensure_ascii=False, separators=(",", ":")))
            )
        stale_response = _get_read_only(system, _discovery_path(stable_target))
        assert stale_response.status_code == 200, stale_response.text
        assert stale_response.json()["items"][0]["eligibility"] == "stale"

        with system.engine.begin() as connection:
            connection.execute(
                update(catalog_chapters)
                .where(catalog_chapters.c.id == "joint-chapter-1")
                .values(content=json.dumps([frames[1]], ensure_ascii=False, separators=(",", ":")))
            )
        deleted_response = _get_read_only(system, _discovery_path(stable_target))
        assert deleted_response.status_code == 200, deleted_response.text
        assert deleted_response.json()["items"][0]["eligibility"] == "target_deleted"

        character_target = targets["character"]
        character_table = ASSET_TABLES["character"]
        with system.engine.begin() as connection:
            connection.execute(
                update(character_table)
                .where(character_table.c.id == character_target.asset_id)
                .values(image_url="https://fixtures.example.test/updated-image.png")
            )
        image_changed = _get_read_only(system, _discovery_path(character_target))
        assert image_changed.status_code == 200, image_changed.text
        assert image_changed.json()["items"][0]["eligibility"] == "stale"

        with system.engine.begin() as connection:
            connection.execute(
                update(character_table)
                .where(character_table.c.id == character_target.asset_id)
                .values(image_url=None, description="人工修改了完整素材快照")
            )
        full_target_changed = _get_read_only(system, _discovery_path(character_target))
        assert full_target_changed.status_code == 200, full_target_changed.text
        assert full_target_changed.json()["items"][0]["eligibility"] == "stale"

        with system.engine.begin() as connection:
            connection.execute(
                character_table.delete().where(character_table.c.id == character_target.asset_id)
            )
        asset_deleted = _get_read_only(system, _discovery_path(character_target))
        assert asset_deleted.status_code == 200, asset_deleted.text
        assert asset_deleted.json()["items"][0]["eligibility"] == "target_deleted"
    finally:
        system.engine.dispose()


def test_real_http_isolates_receipt_owner_scope_and_unready_projections(
    tmp_path: Path,
) -> None:
    system = _build_system(tmp_path)
    try:
        _seed_canvas_sources(system.engine)
        target = _asset_target(system.engine, "character", "joint-character-1")
        owned = _seed_canonical_receipt(
            system.engine, target, result_ref="isolation-owned"
        )
        _seed_canonical_receipt(
            system.engine,
            target,
            result_ref="isolation-foreign-owner",
            owner_id="matrix-foreign-owner",
        )
        _seed_canonical_receipt(
            system.engine,
            replace(target, chapter_id="matrix-other-chapter"),
            result_ref="isolation-foreign-chapter",
        )
        _seed_canonical_receipt(
            system.engine,
            replace(target, series_id="matrix-other-series"),
            result_ref="isolation-foreign-series",
        )
        task_mismatch = _seed_canonical_receipt(
            system.engine, target, result_ref="isolation-task-mismatch"
        )
        projection_bad = _seed_canonical_receipt(
            system.engine, target, result_ref="isolation-projection-mismatch"
        )
        timestamp_bad = _seed_canonical_receipt(
            system.engine, target, result_ref="isolation-timestamp-mismatch"
        )
        malformed = _seed_canonical_receipt(
            system.engine, target, result_ref="isolation-malformed-request"
        )
        with system.engine.begin() as connection:
            connection.execute(
                update(GenerationResultTargetRow)
                .where(
                    GenerationResultTargetRow.result_ref
                    == task_mismatch["result_ref"]
                )
                .values(task_id=owned["task_id"])
            )
            connection.execute(
                update(GenerationResultTargetRow)
                .where(
                    GenerationResultTargetRow.result_ref
                    == projection_bad["result_ref"]
                )
                .values(target_snapshot_digest="0" * 64)
            )
            connection.execute(
                update(GenerationResultTargetRow)
                .where(
                    GenerationResultTargetRow.result_ref
                    == timestamp_bad["result_ref"]
                )
                .values(
                    generation_fulfilled_at=(
                        _G2_JOINT_NOW - timedelta(days=1)
                    ).replace(tzinfo=None)
                )
            )
            connection.execute(
                update(AITaskRow)
                .where(AITaskRow.id == malformed["task_id"])
                .values(request_data="{malformed")
            )

        response = _get_read_only(system, _discovery_path(target))
        assert response.status_code == 200, response.text
        items = {item["result_ref"]: item for item in response.json()["items"]}
        assert set(items) == {
            owned["result_ref"],
            projection_bad["result_ref"],
            timestamp_bad["result_ref"],
            malformed["result_ref"],
        }
        assert items[owned["result_ref"]]["eligibility"] == "ready"
        for result_ref in (
            projection_bad["result_ref"],
            timestamp_bad["result_ref"],
            malformed["result_ref"],
        ):
            item = items[result_ref]
            assert item["eligibility"] == "unready"
            assert item["expected_target"] is None
            assert item["application_key"] is None
            assert item["application"] is None
        canonical_completed_at = datetime.fromisoformat(
            items[timestamp_bad["result_ref"]]["completed_at"]
        )
        assert canonical_completed_at.astimezone(timezone.utc) == (
            _G2_JOINT_NOW.astimezone(timezone.utc)
        )
    finally:
        system.engine.dispose()


def _retarget_cursor(cursor: str, **changes: object) -> str:
    payload = json.loads(
        base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
    )
    for key, value in changes.items():
        payload[key] = value
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(encoded).rstrip(b"=").decode("ascii")


def test_real_http_uses_tie_safe_keyset_pages_and_bounded_index_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    system = _build_system(tmp_path)
    try:
        _seed_canvas_sources(system.engine)
        target = _asset_target(system.engine, "character", "joint-character-1")
        result_refs = [f"ordered-result-{index:03d}" for index in range(5)]
        for result_ref in result_refs:
            _seed_canonical_receipt(
                system.engine,
                target,
                result_ref=result_ref,
                fulfilled_at=_G2_JOINT_NOW,
            )

        page_path = _discovery_path(target, limit=2)
        page_queries: list[tuple[str, object]] = []
        owned_result_reads: list[tuple[str, str]] = []
        original_load_owned = canvas_result_discovery.load_owned_completed_result

        def record_owned_result_read(session, actor, task_id, result_ref):
            owned_result_reads.append((task_id, result_ref))
            return original_load_owned(session, actor, task_id, result_ref)

        monkeypatch.setattr(
            canvas_result_discovery,
            "load_owned_completed_result",
            record_owned_result_read,
        )

        def capture_page_query(
            _connection,
            _cursor,
            statement: str,
            parameters,
            _context,
            _executemany: bool,
        ) -> None:
            if "from generation_result_targets" in statement.lower():
                page_queries.append((statement, parameters))

        before = _canvas_lookup_write_state(system.engine)
        event.listen(system.engine, "before_cursor_execute", capture_page_query)
        try:
            first_response = _get(system, page_path)
        finally:
            event.remove(system.engine, "before_cursor_execute", capture_page_query)

        assert first_response.status_code == 200, first_response.text
        _assert_last_lookup_session_is_read_only(system)
        assert _canvas_lookup_write_state(system.engine) == before
        first_page = first_response.json()
        assert len(first_page["items"]) == 2
        assert first_page["next_cursor"]
        assert len(owned_result_reads) == 2

        second_response = _get_read_only(
            system,
            page_path + "&cursor=" + first_page["next_cursor"],
        )
        assert second_response.status_code == 200, second_response.text
        second_page = second_response.json()
        assert len(second_page["items"]) == 2
        assert second_page["next_cursor"]
        assert len(owned_result_reads) == 4

        last_response = _get_read_only(
            system,
            page_path + "&cursor=" + second_page["next_cursor"],
        )
        assert last_response.status_code == 200, last_response.text
        last_page = last_response.json()
        assert len(last_page["items"]) == 1
        assert last_page["next_cursor"] is None
        assert len(owned_result_reads) == 5

        actual_refs = [
            item["result_ref"]
            for page in (first_page, second_page, last_page)
            for item in page["items"]
        ]
        assert actual_refs == sorted(result_refs, reverse=True)
        assert len(actual_refs) == len(set(actual_refs))
        assert page_queries
        page_sql, parameters = page_queries[0]
        normalized_page_sql = " ".join(page_sql.lower().split())
        assert "limit" in normalized_page_sql
        assert "request_data" not in normalized_page_sql
        if " offset " in normalized_page_sql:
            assert normalized_page_sql.endswith(" limit ? offset ?")
            assert tuple(parameters)[-2:] == (3, 0)
        else:
            assert normalized_page_sql.endswith(" limit ?")
            assert tuple(parameters)[-1] == 3
        with system.engine.connect() as connection:
            plan = connection.exec_driver_sql(
                "EXPLAIN QUERY PLAN " + page_sql,
                parameters,
            ).all()
        plan_details = " ".join(str(row[3]) for row in plan)
        assert "ix_generation_result_targets_page" in plan_details

        cursor = first_page["next_cursor"]
        invalid_contexts = (
            (_retarget_cursor(cursor, actor_id="another-caller"), _discovery_path(target, limit=2)),
            (_retarget_cursor(cursor, chapter_id="another-chapter"), _discovery_path(target, limit=2)),
            (
                cursor,
                page_path.replace("asset_id=joint-character-1", "asset_id=another-asset"),
            ),
            ("AAAA", page_path),
        )
        for invalid_cursor, request_path in invalid_contexts:
            before_invalid = _canvas_lookup_write_state(system.engine)
            response = _get_read_only(
                system,
                request_path + "&cursor=" + invalid_cursor,
            )
            assert response.status_code == 422, response.text
            assert _canvas_lookup_write_state(system.engine) == before_invalid

        malformed_before = len(system.business_sessions)
        malformed_response = _get(system, page_path + "&cursor=***")
        assert malformed_response.status_code == 422, malformed_response.text
        assert len(system.business_sessions) == malformed_before
        assert _canvas_lookup_write_state(system.engine) == before
    finally:
        system.engine.dispose()


def test_backfill_is_owned_bounded_conflict_safe_and_never_implicit_in_get(
    tmp_path: Path,
) -> None:
    system = _build_system(tmp_path)
    try:
        _seed_canvas_sources(system.engine)
        target = _asset_target(system.engine, "character", "joint-character-1")
        first = _seed_canonical_receipt(
            system.engine, target, result_ref="backfill-001"
        )
        noncanvas = _seed_canonical_receipt(
            system.engine, None, result_ref="backfill-002"
        )
        conflict = _seed_canonical_receipt(
            system.engine, target, result_ref="backfill-003"
        )
        unready = _seed_canonical_receipt(
            system.engine, target, result_ref="backfill-004"
        )
        _seed_canonical_receipt(
            system.engine,
            target,
            result_ref="foreign-backfill-001",
            owner_id="matrix-foreign-backfill-owner",
        )
        with system.engine.begin() as connection:
            connection.execute(
                GenerationResultTargetRow.__table__.delete().where(
                    GenerationResultTargetRow.result_ref == first["result_ref"]
                )
            )
            connection.execute(
                update(GenerationResultTargetRow)
                .where(
                    GenerationResultTargetRow.result_ref == conflict["result_ref"]
                )
                .values(target_identity="asset|character|tampered")
            )
            connection.execute(
                update(AITaskRow)
                .where(AITaskRow.id == unready["task_id"])
                .values(request_data="{invalid")
            )
            connection.execute(
                GenerationResultTargetRow.__table__.delete().where(
                    GenerationResultTargetRow.result_ref == unready["result_ref"]
                )
            )

        before_get = _canvas_lookup_write_state(system.engine)
        response = _get_read_only(system, _discovery_path(target))
        assert response.status_code == 200, response.text
        assert response.json()["items"] == []
        assert _canvas_lookup_write_state(system.engine) == before_get

        actor = TrustedActor(_G2_JOINT_ACTOR_ID)
        pending_session = Session(system.engine, expire_on_commit=False)
        pending_session.add(
            GenerationResultTargetRow(
                result_ref=first["result_ref"],
                owner_id=_G2_JOINT_ACTOR_ID,
                task_id=first["task_id"],
                series_id=target.series_id,
                chapter_id=target.chapter_id,
                target_kind=target.kind,
                target_identity="asset|character|pending-conflict",
                target_snapshot_digest="0" * 64,
                generation_fulfilled_at=_G2_JOINT_NOW.replace(tzinfo=None),
            )
        )
        try:
            pending_report = backfill_canvas_result_targets(
                pending_session,
                actor,
                limit=2,
            )
            assert pending_report.scanned == 2
            assert pending_report.projected == 0
            assert pending_report.already_present == 0
            assert pending_report.unready == 0
            assert pending_report.conflict == 1
            assert pending_report.ignored_noncanvas == 1
            assert pending_report.next_cursor == noncanvas["result_ref"]
            assert pending_report.has_more
            assert len(pending_session.new) == 1
        finally:
            pending_session.rollback()
            pending_session.close()

        with Session(system.engine) as invalid_limit_session:
            with pytest.raises(ValueError, match="limit"):
                backfill_canvas_result_targets(
                    invalid_limit_session,
                    actor,
                    limit=0,
                )
        with Session(system.engine) as boundary_session:
            with pytest.raises(ValueError, match="limit"):
                backfill_canvas_result_targets(
                    boundary_session,
                    actor,
                    limit=51,
                )

        with Session(system.engine) as conflict_page_session:
            conflict_page = backfill_canvas_result_targets(
                conflict_page_session,
                actor,
                after_result_ref=noncanvas["result_ref"],
                limit=1,
            )
            assert conflict_page.scanned == 1
            assert conflict_page.projected == 0
            assert conflict_page.conflict == 1
            assert conflict_page.next_cursor == conflict["result_ref"]
            assert conflict_page.has_more

        with Session(system.engine) as unready_page_session:
            unready_page = backfill_canvas_result_targets(
                unready_page_session,
                actor,
                after_result_ref=conflict["result_ref"],
                limit=50,
            )
            assert unready_page.scanned == 1
            assert unready_page.unready == 1
            assert unready_page.conflict == 0
            assert unready_page.next_cursor is None
            assert not unready_page.has_more

        with Session(system.engine) as repair_session:
            report = backfill_canvas_result_targets(
                repair_session,
                actor,
                limit=50,
            )
            assert report.scanned == 4
            assert report.projected == 1
            assert report.already_present == 0
            assert report.unready == 1
            assert report.conflict == 1
            assert report.ignored_noncanvas == 1
            assert report.next_cursor is None
            assert not report.has_more
            repair_session.commit()

        with Session(system.engine) as replay_session:
            replay = backfill_canvas_result_targets(
                replay_session,
                actor,
                limit=50,
            )
            assert replay.projected == 0
            assert replay.already_present == 1
            assert replay.unready == 1
            assert replay.conflict == 1
            assert replay.ignored_noncanvas == 1
            assert not replay_session.new
        assert _canvas_lookup_write_state(system.engine)[2] == before_get[2] + 1
    finally:
        system.engine.dispose()
