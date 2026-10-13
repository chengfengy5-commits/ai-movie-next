"""SQLite tests for full legacy system-config lookup and fallback behavior."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, insert, update
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.upload_files.persistence import (
    SqlAlchemyUploadDirectoryProvider,
    metadata,
    system_configs,
)


class ObservedSession(Session):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1
        super().close()


def test_maps_the_complete_legacy_system_config_row() -> None:
    assert tuple(system_configs.columns.keys()) == (
        "id",
        "muse_token",
        "site_title",
        "site_tagline",
        "storyboard_cost_multiplier",
        "membership_benefits",
        "payments_enabled",
        "tutorial_url",
        "purchase_qr_code_url",
        "purchase_contact_info",
        "oss_access_key_id",
        "oss_access_key_secret",
        "oss_bucket_name",
        "oss_endpoint",
        "fc_transfer_url",
        "fc_transfer_url_cn",
        "oss_haoai_match_domain",
        "oss_haoai_replace_domain",
        "team_created_limit",
        "team_joined_limit",
        "team_member_limit",
        "invite_code_ttl_hours",
        "chapter_lock_idle_minutes",
        "aistarslab_credit_multiplier",
        "zhangyuge_billing_pat",
        "frontend_url",
        "allowed_origins",
        "upload_dir",
        "worker_max_concurrency",
    )


def test_database_directory_is_read_per_call_and_session_is_closed(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'system-config.sqlite'}", future=True)
    metadata.create_all(engine)
    sessions: list[ObservedSession] = []
    maker = sessionmaker(
        engine,
        class_=ObservedSession,
        future=True,
    )

    def session_factory() -> ObservedSession:
        session = maker()
        sessions.append(session)
        return session

    first_directory = tmp_path / "first"
    second_directory = tmp_path / "second"
    with engine.begin() as connection:
        connection.execute(
            insert(system_configs).values(
                id="config",
                upload_dir=str(first_directory),
            )
        )

    provider = SqlAlchemyUploadDirectoryProvider(
        session_factory,
        fallback_directory=tmp_path / "fallback",
    )
    assert provider.get_upload_directory() == str(first_directory)
    with engine.begin() as connection:
        connection.execute(
            update(system_configs)
            .where(system_configs.c.id == "config")
            .values(upload_dir=str(second_directory))
        )
    assert provider.get_upload_directory() == str(second_directory)
    assert [session.close_count for session in sessions] == [1, 1]
    engine.dispose()


def test_missing_row_and_sql_failure_use_explicit_fallback_and_close_session(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'missing-config-table.sqlite'}", future=True)
    sessions: list[ObservedSession] = []
    maker = sessionmaker(
        engine,
        class_=ObservedSession,
        future=True,
    )

    def session_factory() -> ObservedSession:
        session = maker()
        sessions.append(session)
        return session

    fallback = tmp_path / "fallback-uploads"
    provider = SqlAlchemyUploadDirectoryProvider(session_factory, fallback)

    assert provider.get_upload_directory() == fallback
    assert len(sessions) == 1
    assert sessions[0].close_count == 1
    engine.dispose()


def test_empty_configuration_table_uses_fallback_and_closes_session(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'empty-config.sqlite'}", future=True)
    metadata.create_all(engine)
    sessions: list[ObservedSession] = []
    maker = sessionmaker(engine, class_=ObservedSession, future=True)

    def session_factory() -> ObservedSession:
        session = maker()
        sessions.append(session)
        return session

    fallback = tmp_path / "fallback-uploads"
    provider = SqlAlchemyUploadDirectoryProvider(session_factory, fallback)

    assert provider.get_upload_directory() == fallback
    assert len(sessions) == 1
    assert sessions[0].close_count == 1
    engine.dispose()


def test_session_factory_connection_failure_uses_fallback(tmp_path: Path) -> None:
    fallback = tmp_path / "fallback-uploads"

    def fail_to_connect() -> Session:
        raise OSError("test connection failure")

    provider = SqlAlchemyUploadDirectoryProvider(fail_to_connect, fallback)

    assert provider.get_upload_directory() == fallback
