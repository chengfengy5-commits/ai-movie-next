from __future__ import annotations

import pytest

from haoai_backend.teams.errors import JoinQuotaExceeded
from haoai_backend.teams.quota import InMemoryJoinQuota


class FakeEpochClock:
    def __init__(self, value: float) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def test_join_quota_is_anchored_to_first_hit_and_does_not_renew_on_limit():
    clock = FakeEpochClock(100)
    quota = InMemoryJoinQuota(clock)

    for _ in range(10):
        quota.check("192.0.2.10", "/api/teams/join")

    assert quota.snapshot()[("192.0.2.10", "/api/teams/join")] == (10, 3700)
    with pytest.raises(JoinQuotaExceeded) as error:
        quota.check("192.0.2.10", "/api/teams/join")
    assert error.value.status_code == 429
    assert error.value.detail == "Rate limit exceeded: 10 per 1 hour"
    assert quota.snapshot()[("192.0.2.10", "/api/teams/join")] == (10, 3700)

    clock.value = 3600
    with pytest.raises(JoinQuotaExceeded):
        quota.check("192.0.2.10", "/api/teams/join")
    clock.value = 3699.999
    with pytest.raises(JoinQuotaExceeded):
        quota.check("192.0.2.10", "/api/teams/join")

    clock.value = 3700
    quota.check("192.0.2.10", "/api/teams/join")
    assert quota.snapshot()[("192.0.2.10", "/api/teams/join")] == (1, 7300)


def test_join_quota_keys_are_isolated_by_ip_and_path():
    clock = FakeEpochClock(100)
    quota = InMemoryJoinQuota(clock)

    for _ in range(10):
        quota.check("192.0.2.10", "/api/teams/join")

    quota.check("192.0.2.11", "/api/teams/join")
    quota.check("192.0.2.10", "/api/teams/other")

    assert quota.snapshot()[("192.0.2.11", "/api/teams/join")] == (1, 3700)
    assert quota.snapshot()[("192.0.2.10", "/api/teams/other")] == (1, 3700)
