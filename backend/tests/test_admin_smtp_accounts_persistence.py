from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, insert, select
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.admin_smtp_accounts.daily_reset import DailyResetState, SqlAlchemyDailyReset
from haoai_backend.admin_smtp_accounts.persistence import SqlAlchemyAdminSmtpAccountUnitOfWork
from haoai_backend.admin_smtp_accounts.tables import metadata, smtp_accounts


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


def test_real_sqlite_crud_has_one_commit_and_refresh_only_on_create(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'smtp.sqlite'}")
    metadata.create_all(engine)
    maker = sessionmaker(engine, class_=ObservedSession, expire_on_commit=False)
    session = maker()
    uow = SqlAlchemyAdminSmtpAccountUnitOfWork(session)
    try:
        row = uow.add_account("smtp-1", {
            "host": "smtp.qq.com", "port": 465, "user": "owner@example.test",
            "password": "ciphertext", "daily_sent": 3, "is_active": 1,
        })
        uow.commit()
        assert session.commits == 1
        uow.refresh(row)
        assert session.refreshes == 1
        assert row.id == "smtp-1" and row.password == "ciphertext"

        loaded = uow.get_account("smtp-1")
        assert loaded is not None and loaded.user == "owner@example.test"
        uow.set_account_value(loaded, "port", 587)
        uow.commit()
        assert session.commits == 2 and session.refreshes == 1

        listed = uow.list_accounts()
        assert [item.id for item in listed] == ["smtp-1"]
        assert listed[0].port == 587 and listed[0].daily_sent == 3

        uow.delete_account(loaded)
        uow.commit()
        assert session.commits == 3 and session.refreshes == 1
        assert uow.get_account("smtp-1") is None
    finally:
        uow.close()
        assert session.closed
        engine.dispose()


def test_daily_rollover_uses_independent_session_and_advances_date_on_failure(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'rollover.sqlite'}")
    metadata.create_all(engine)
    maker = sessionmaker(engine, class_=ObservedSession, expire_on_commit=False)
    seed = maker()
    seed.execute(insert(smtp_accounts).values(
        id="smtp-1", host="smtp.qq.com", port=465, user="owner@example.test",
        password="cipher", daily_sent=9, is_active=1,
        created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    ))
    seed.commit()
    seed.close()

    current = [date(2026, 10, 12)]
    state = DailyResetState(lambda: current[0])
    sessions = []
    def factory():
        session = maker()
        sessions.append(session)
        return session
    reset = SqlAlchemyDailyReset(factory, state=state)
    reset.reset_if_needed()
    assert sessions == []
    current[0] += timedelta(days=1)
    reset.reset_if_needed()
    assert len(sessions) == 1
    assert sessions[0].commits == 1 and sessions[0].closed
    with Session(engine) as check:
        assert check.execute(select(smtp_accounts.c.daily_sent)).scalar_one() == 0
    assert state.today == current[0]
    engine.dispose()
