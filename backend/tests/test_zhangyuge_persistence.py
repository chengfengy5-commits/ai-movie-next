from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, insert, select
from sqlalchemy.orm import Session

from haoai_backend.zhangyuge.persistence import SqlAlchemyZhangyugeUnitOfWork
from haoai_backend.zhangyuge.tables import metadata, system_configs, system_models


def test_private_mapping_reads_model_credentials_and_inserts_legacy_config_defaults(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'zhangyuge.sqlite'}")
    metadata.create_all(engine)
    with Session(engine) as session:
        session.execute(
            insert(system_models).values(
                id="model-1",
                provider="zhangyuge",
                is_active=True,
                api_key="fake-encrypted-key",
                base_url="https://gateway.example/v1",
            )
        )
        session.commit()

    session = Session(engine)
    uow = SqlAlchemyZhangyugeUnitOfWork(session)
    credentials = uow.active_model_credentials()
    config = uow.first_system_config()
    assert [(item.api_key, item.base_url) for item in credentials] == [
        ("fake-encrypted-key", "https://gateway.example/v1")
    ]
    assert config is None

    config = uow.add_system_config("config-1")
    uow.set_billing_pat(config, "fake-encrypted-pat")
    uow.commit()
    uow.close()

    with Session(engine) as stored:
        row = stored.execute(
            select(system_configs.c.id, system_configs.c.zhangyuge_billing_pat)
        ).one()
    assert row == ("config-1", "fake-encrypted-pat")
    engine.dispose()
