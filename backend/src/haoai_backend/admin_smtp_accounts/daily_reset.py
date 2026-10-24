"""Thread-safe local-day state matching the legacy SMTP daily-count rollover."""

from __future__ import annotations

from datetime import date
from threading import RLock
from typing import Any

from sqlalchemy import update

from .ports import DateClock, SessionFactory
from .tables import smtp_accounts


class DailyResetState:
    def __init__(self, date_clock: DateClock = date.today) -> None:
        self._date_clock = date_clock
        self._today = date_clock()
        self._counts: dict[str, int] = {}
        self._lock = RLock()

    @property
    def today(self) -> date:
        with self._lock:
            return self._today

    def cached_count(self, account_id: str) -> int:
        with self._lock:
            return self._counts.get(account_id, 0)

    def set_cached_count(self, account_id: str, value: int) -> None:
        with self._lock:
            self._counts[account_id] = value

    def increment_cached_count(self, account_id: str) -> int:
        with self._lock:
            value = self._counts.get(account_id, 0) + 1
            self._counts[account_id] = value
            return value

    def reset_if_needed(self, reset_all: Any) -> None:
        with self._lock:
            today = self._date_clock()
            if today == self._today:
                return
            reset_all()
            self._counts.clear()
            self._today = today


class SqlAlchemyDailyReset:
    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        state: DailyResetState | None = None,
    ) -> None:
        self._session_factory = session_factory
        self.state = state or DailyResetState()

    def reset_if_needed(self) -> None:
        self.state.reset_if_needed(self._reset_all)

    def _reset_all(self) -> None:
        session = self._session_factory()
        try:
            session.execute(update(smtp_accounts).values(daily_sent=0))
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()
