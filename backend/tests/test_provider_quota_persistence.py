from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, insert, select
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.provider_quota.persistence import SqlAlchemyProviderQuotaUnitOfWork
from haoai_backend.provider_quota.tables import metadata, provider_quota_accounts


class ObservedSession(Session):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.commits = self.refreshes = 0
        self.closed = False

    def commit(self):
        self.commits += 1
        return super().commit()

    def refresh(self, instance, *args, **kwargs):
        self.refreshes += 1
        return super().refresh(instance, *args, **kwargs)

    def close(self):
        self.closed = True
        return super().close()


def test_sqlite_uow_uses_legacy_columns_sorting_and_crud(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'provider-quota.sqlite'}")
    metadata.create_all(engine)
    maker = sessionmaker(engine, class_=ObservedSession, expire_on_commit=False)
    session = maker()
    uow = SqlAlchemyProviderQuotaUnitOfWork(session)
    created_at = datetime(2026, 10, 1)
    try:
        uow.add_account(
            "quota-later",
            {
                "name": "later",
                "provider_type": "aliyun",
                "base_url": "https://business.aliyuncs.com",
                "username": "cipher:id",
                "password": "cipher:secret",
                "is_active": 1,
                "sort_order": 2,
                "created_at": created_at,
                "updated_at": created_at,
            },
        )
        uow.add_account(
            "quota-first",
            {
                "name": "first",
                "provider_type": "new_api",
                "base_url": "https://gateway.example",
                "username": "cipher:user",
                "password": "cipher:password",
                "is_active": 1,
                "sort_order": 1,
                "created_at": created_at,
                "updated_at": created_at,
            },
        )
        uow.commit()
        assert session.commits == 1

        accounts = uow.list_accounts()
        assert [account.id for account in accounts] == ["quota-first", "quota-later"]
        assert set(accounts[0].__dataclass_fields__) == {
            "id", "name", "provider_type", "base_url", "username", "password",
            "is_active", "sort_order", "created_at", "updated_at",
        }

        account = uow.get_account("quota-first")
        assert account is not None
        uow.set_account_value(account, "password", "cipher:rotated")
        uow.commit()
        assert session.commits == 2
        uow.refresh(account)
        assert session.refreshes == 1 and account.password == "cipher:rotated"

        uow.delete_account(account)
        uow.commit()
        assert session.commits == 3
        assert uow.get_account("quota-first") is None

        with Session(engine) as check:
            stored = check.execute(
                select(provider_quota_accounts.c.password).where(
                    provider_quota_accounts.c.id == "quota-later"
                )
            ).scalar_one()
        assert stored == "cipher:secret"
    finally:
        uow.close()
        assert session.closed
        engine.dispose()
