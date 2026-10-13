from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import Float, Integer, String, Text, create_engine, insert
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.admin_system_config.persistence import (
    SqlAlchemyAdminSystemConfigUnitOfWork,
)
from haoai_backend.admin_system_config.tables import (
    SystemConfigRow,
    metadata,
    system_configs,
)


class ObservedSession(Session):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.commit_count = 0
        self.refresh_count = 0
        self.query_count = 0
        self.close_count = 0

    def commit(self) -> None:
        self.commit_count += 1
        super().commit()

    def refresh(self, instance: Any, *args: Any, **kwargs: Any) -> None:
        self.refresh_count += 1
        super().refresh(instance, *args, **kwargs)

    def query(self, *entities: Any, **kwargs: Any):
        self.query_count += 1
        return super().query(*entities, **kwargs)

    def close(self) -> None:
        self.close_count += 1
        super().close()


def make_database(path: Path):
    engine = create_engine(f"sqlite:///{path}")
    metadata.create_all(engine)
    maker = sessionmaker(engine, class_=ObservedSession, expire_on_commit=False)

    def make_session() -> ObservedSession:
        return maker()

    return engine, make_session


EXPECTED_COLUMNS = (
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


def test_private_mapping_has_all_legacy_columns_and_types() -> None:
    assert tuple(system_configs.columns.keys()) == EXPECTED_COLUMNS
    assert len(EXPECTED_COLUMNS) == 29
    assert isinstance(system_configs.c.id.type, String)
    assert system_configs.c.id.type.length == 36
    assert isinstance(system_configs.c.storyboard_cost_multiplier.type, Integer)
    assert isinstance(system_configs.c.aistarslab_credit_multiplier.type, Float)
    assert isinstance(system_configs.c.membership_benefits.type, Text)
    for column in system_configs.columns:
        assert column.nullable is False


def test_real_sqlite_first_detail_and_update_use_one_row_and_exact_transactions(
    tmp_path: Path,
) -> None:
    engine, make_session = make_database(tmp_path / "system-config.sqlite")
    try:
        with make_session() as seed:
            seed.add_all(
                [
                    SystemConfigRow(
                        id="config-a",
                        site_title="A",
                        oss_access_key_secret="hidden-secret",
                        zhangyuge_billing_pat="hidden-pat",
                    ),
                    SystemConfigRow(id="config-b", site_title="B"),
                ]
            )
            seed.commit()

        session = make_session()
        unit = SqlAlchemyAdminSystemConfigUnitOfWork(session)
        first = unit.first_config()
        assert first is not None
        assert first.id in {"config-a", "config-b"}
        assert first.site_title in {"A", "B"}
        assert session.query_count == 1
        assert session.commit_count == 0
        assert session.refresh_count == 0

        detail = unit.get_config(first.id)
        assert detail is not None
        assert detail.id == first.id
        assert session.query_count == 2
        assert session.commit_count == 0

        row = unit.load_config_for_update(first.id)
        assert row is not None
        unit.set_config_value(row, "site_title", "Updated")
        unit.set_config_value(row, "worker_max_concurrency", "0")
        unit.commit()
        updated = unit.refresh_config(row)
        assert updated.site_title == "Updated"
        assert updated.worker_max_concurrency == "0"
        assert session.query_count == 3
        assert session.commit_count == 1
        assert session.refresh_count == 1
        session.close()
        assert session.close_count == 1
    finally:
        engine.dispose()
