from __future__ import annotations

import pytest

from haoai_backend.series_data.application import get_series_list
from haoai_backend.shared.identity import TrustedActor


class DirtyUnitOfWork:
    def __init__(self) -> None:
        self.closed = False

    def ensure_clean(self) -> None:
        raise RuntimeError("session is not clean")

    def close(self) -> None:
        self.closed = True


def test_open_closes_unit_of_work_when_cleanliness_check_fails() -> None:
    unit_of_work = DirtyUnitOfWork()

    with pytest.raises(RuntimeError, match="session is not clean"):
        get_series_list(lambda: unit_of_work, TrustedActor("user"))

    assert unit_of_work.closed
