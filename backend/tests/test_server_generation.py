"""Database time and lazy construction for the opt-in host generation ports."""

from __future__ import annotations

from datetime import timezone

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from backend.server.generation import _database_clock


def test_database_clock_is_lazy_aware_utc_and_closes_each_session(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'generation-clock.sqlite'}")
    session_factory = sessionmaker(engine, expire_on_commit=False, future=True)
    checkouts = 0
    checkins = 0

    def checked_out(*_args) -> None:
        nonlocal checkouts
        checkouts += 1

    def checked_in(*_args) -> None:
        nonlocal checkins
        checkins += 1

    event.listen(engine, "checkout", checked_out)
    event.listen(engine, "checkin", checked_in)
    try:
        database_clock = _database_clock(session_factory)
        assert checkouts == checkins == 0
        first = database_clock()
        second = database_clock()
        assert first.tzinfo is not None
        assert first.utcoffset() == timezone.utc.utcoffset(first)
        assert second.tzinfo is not None
        assert second.utcoffset() == timezone.utc.utcoffset(second)
        assert checkouts == checkins == 2
    finally:
        engine.dispose()
