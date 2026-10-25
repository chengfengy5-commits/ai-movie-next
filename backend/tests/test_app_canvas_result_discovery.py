"""Real JWT/SQLite boundary checks for Canvas result discovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from test_app_chat_media_admission_integration import (
    _assert_last_lookup_session_is_read_only,
    _build_system,
    _canvas_lookup_write_state,
    _get,
)


def test_real_http_discovers_an_empty_owned_asset_target_page(tmp_path: Path) -> None:
    system = _build_system(tmp_path)
    try:
        before = _canvas_lookup_write_state(system.engine)
        response = _get(
            system,
            "/api/chapters/joint-chapter-1/canvas-owned-results"
            "?target_kind=asset_image&asset_type=character&asset_id=joint-character-1",
        )

        assert response.status_code == 200, response.text
        assert response.json() == {
            "chapter_id": "joint-chapter-1",
            "target": {
                "kind": "asset_image",
                "chapter_id": "joint-chapter-1",
                "asset_type": "character",
                "asset_id": "joint-character-1",
            },
            "items": [],
            "next_cursor": None,
        }
        _assert_last_lookup_session_is_read_only(system)
        assert _canvas_lookup_write_state(system.engine) == before
    finally:
        system.engine.dispose()


@pytest.mark.parametrize(
    "query",
    (
        "target_kind=asset_image&asset_type=character&asset_id=joint-character-1&extra=x",
        "target_kind=asset_image&target_kind=asset_image&asset_type=character&asset_id=joint-character-1",
        "target_kind=frame_preview&storyboard_asset_id=frame-1&identity_mode=strict-no-id-v1&frame_index=0&expected_chapter_digest="
        + "0" * 64,
        "target_kind=frame_preview&identity_mode=strict-no-id-v1&frame_index=-1&expected_chapter_digest="
        + "0" * 64,
        "target_kind=frame_preview&identity_mode=strict-no-id-v1&frame_index=9007199254740992&expected_chapter_digest="
        + "0" * 64,
        "target_kind=frame_preview&identity_mode=strict-no-id-v1&frame_index="
        + "9" * 5000
        + "&expected_chapter_digest="
        + "0" * 64,
        "target_kind=frame_preview&identity_mode=strict-no-id-v1&frame_index=0&expected_chapter_digest="
        + "A" * 64,
        "target_kind=asset_image&asset_type=character&asset_id=bad%2Fid",
        "target_kind=asset_image&asset_type=character&asset_id=..",
        "target_kind=asset_image&asset_type=character&asset_id=joint-character-1&limit="
        + "9" * 5000,
    ),
)
def test_real_http_rejects_invalid_discovery_query_before_business_session(
    tmp_path: Path,
    query: str,
) -> None:
    system = _build_system(tmp_path)
    try:
        response = _get(
            system,
            f"/api/chapters/joint-chapter-1/canvas-owned-results?{query}",
        )

        assert response.status_code == 422, response.text
        assert system.business_sessions == []
        assert system.auth_sessions
    finally:
        system.engine.dispose()


def test_real_http_preserves_plus_in_a_valid_asset_identifier(tmp_path: Path) -> None:
    system = _build_system(tmp_path)
    try:
        response = _get(
            system,
            "/api/chapters/joint-chapter-1/canvas-owned-results"
            "?target_kind=asset_image&asset_type=character&asset_id=joint%2Bcharacter",
        )

        assert response.status_code == 200, response.text
        assert response.json()["target"]["asset_id"] == "joint+character"
        assert response.json()["items"] == []
    finally:
        system.engine.dispose()


def test_real_http_requires_an_existing_current_chapter(tmp_path: Path) -> None:
    system = _build_system(tmp_path)
    try:
        response = _get(
            system,
            "/api/chapters/missing-chapter/canvas-owned-results"
            "?target_kind=asset_image&asset_type=character&asset_id=asset-1",
        )

        assert response.status_code == 404, response.text
        assert response.json() == {"detail": "章节不存在"}
        assert system.business_sessions
        _assert_last_lookup_session_is_read_only(system)
    finally:
        system.engine.dispose()


def test_real_http_authenticates_before_discovery_query_validation(
    tmp_path: Path,
) -> None:
    system = _build_system(tmp_path)
    try:
        response = _get(
            system,
            "/api/chapters/joint-chapter-1/canvas-owned-results?unknown=x",
            token="invalid-token",
        )

        assert response.status_code == 401, response.text
        assert system.business_sessions == []
    finally:
        system.engine.dispose()


@pytest.mark.parametrize(
    "schema_defect",
    ("missing_page_index", "missing_migration_history", "corrupt_v2_checksum"),
)
def test_real_http_refuses_discovery_when_schema_readiness_fails(
    tmp_path: Path,
    schema_defect: str,
) -> None:
    system = _build_system(tmp_path)
    try:
        with system.engine.begin() as connection:
            if schema_defect == "missing_page_index":
                connection.exec_driver_sql(
                    "DROP INDEX ix_generation_result_targets_page"
                )
            elif schema_defect == "missing_migration_history":
                connection.exec_driver_sql("DROP TABLE generation_schema_migrations")
            else:
                connection.exec_driver_sql(
                    "UPDATE generation_schema_migrations SET checksum = ? "
                    "WHERE version = ?",
                    (
                        "0" * 64,
                        "0002_canvas_result_targets",
                    ),
                )

        before = _canvas_lookup_write_state(system.engine)
        response = _get(
            system,
            "/api/chapters/joint-chapter-1/canvas-owned-results"
            "?target_kind=asset_image&asset_type=character&asset_id=joint-character-1",
        )

        assert response.status_code == 503, response.text
        assert response.json() == {"detail": "画布结果发现数据结构尚未就绪"}
        _assert_last_lookup_session_is_read_only(system)
        assert _canvas_lookup_write_state(system.engine) == before

        with system.engine.connect() as connection:
            if schema_defect == "missing_page_index":
                index_names = {
                    row[0]
                    for row in connection.exec_driver_sql(
                        "SELECT name FROM sqlite_master WHERE type = 'index'"
                    )
                }
                assert "ix_generation_result_targets_page" not in index_names
            elif schema_defect == "missing_migration_history":
                table_names = {
                    row[0]
                    for row in connection.exec_driver_sql(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                assert "generation_schema_migrations" not in table_names
            else:
                checksum = connection.exec_driver_sql(
                    "SELECT checksum FROM generation_schema_migrations "
                    "WHERE version = ?",
                    ("0002_canvas_result_targets",),
                ).scalar_one()
                assert checksum == "0" * 64
    finally:
        system.engine.dispose()
