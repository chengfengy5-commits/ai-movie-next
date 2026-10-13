from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest

from haoai_backend.redemption_codes import application
from haoai_backend.redemption_codes.domain import (
    GenerateRedemptionCodesCommand,
    NewCreditLog,
    NewRedemptionCode,
    NewUserCredit,
    RedemptionCode,
    RedemptionCodesActor,
    RedeemRedemptionCodeCommand,
    UserCredit,
)
from haoai_backend.redemption_codes.errors import (
    RedemptionCodeAlreadyUsed,
    RedemptionCodeExpired,
    RedemptionCodeNotFound,
    RedemptionCodeTypeUnknown,
    RedemptionCodesBadRequest,
    RedemptionCodesForbidden,
)

NOW = datetime(2026, 10, 13, 12, 0)
ADMIN = RedemptionCodesActor("admin", True, "free", None)
MEMBER = RedemptionCodesActor("member", False, "free", None)


class FalseyCallable:
    def __init__(self, values):
        self.values = iter(values)
        self.calls = 0

    def __bool__(self):
        return False

    def __call__(self):
        self.calls += 1
        return next(self.values)


class MemoryUnitOfWork:
    def __init__(self, *, existing_codes=(), record=None, wallet_balance=None):
        self.existing_codes = set(existing_codes)
        self.record = record
        self.wallet_exists = wallet_balance is not None
        self.wallet_balance = wallet_balance
        self.events: list[tuple[Any, ...]] = []
        self.generated: dict[str, NewRedemptionCode] = {}
        self.commit_count = 0
        self.rollback_count = 0
        self.close_count = 0
        self.refresh_ids: list[str] = []
        self.credit_logs: list[NewCreditLog] = []
        self.commit_error: BaseException | None = None

    def code_exists(self, code: str) -> bool:
        self.events.append(("code_exists", code))
        return code in self.existing_codes

    def add_generated_code(self, code: NewRedemptionCode) -> None:
        self.events.append(("add_generated", code.code))
        self.generated[code.id] = code

    def refresh_generated_code(self, code_id: str) -> RedemptionCode | None:
        self.events.append(("refresh", code_id))
        self.refresh_ids.append(code_id)
        row = self.generated.get(code_id)
        if row is None:
            return None
        return RedemptionCode(
            id=row.id,
            code=row.code,
            type=row.type,
            value=row.value,
            is_used=False,
            used_by=None,
            used_at=None,
            created_by=row.created_by,
            expires_at=row.expires_at,
            created_at=NOW,
        )

    def load_code(self, code: str) -> RedemptionCode | None:
        self.events.append(("load_code", code))
        if self.record is None or self.record.code != code:
            return None
        return self.record

    def mark_code_used(self, code_id: str, *, user_id: str, used_at: datetime) -> None:
        self.events.append(("mark_used", code_id, user_id, used_at))

    def set_membership(
        self,
        user_id: str,
        *,
        membership_type: str,
        membership_expires_at: datetime,
    ) -> None:
        self.events.append(("set_membership", user_id, membership_type, membership_expires_at))

    def load_user_credit(self, user_id: str) -> UserCredit | None:
        self.events.append(("load_credit", user_id))
        if not self.wallet_exists:
            return None
        return UserCredit("credit-id", user_id, self.wallet_balance)

    def add_user_credit(self, credit: NewUserCredit) -> None:
        self.events.append(("add_credit", credit.id, credit.user_id, credit.credits))
        self.wallet_exists = True
        self.wallet_balance = credit.credits

    def flush(self) -> None:
        self.events.append(("flush",))

    def set_user_credit_balance(self, user_id: str, credits: int) -> None:
        self.events.append(("set_credit_balance", user_id, credits))
        self.wallet_balance = credits

    def add_credit_log(self, log: NewCreditLog) -> None:
        self.events.append(("add_credit_log", log))
        self.credit_logs.append(log)

    def user_credit_balance(self, user_id: str) -> int:
        self.events.append(("read_credit_balance", user_id))
        assert self.commit_count == 1
        return self.wallet_balance

    def commit(self) -> None:
        self.events.append(("commit",))
        self.commit_count += 1
        if self.commit_error is not None:
            raise self.commit_error

    def rollback(self) -> None:
        self.events.append(("rollback",))
        self.rollback_count += 1

    def close(self) -> None:
        self.events.append(("close",))
        self.close_count += 1


def make_code(
    *,
    code_type: str = "membership",
    value: int = 5,
    is_used: bool = False,
    expires_at: datetime | None = None,
) -> RedemptionCode:
    return RedemptionCode(
        id="code-id",
        code="HAOAI-TEST-0001",
        type=code_type,
        value=value,
        is_used=is_used,
        used_by=None,
        used_at=None,
        created_by="admin",
        expires_at=expires_at,
        created_at=NOW,
    )


def test_generate_checks_collisions_and_refreshes_each_persisted_row():
    uow = MemoryUnitOfWork(existing_codes={"HAOAI-TAKEN-0001"})
    generator = FalseyCallable(
        ["HAOAI-TAKEN-0001", "HAOAI-NEW-0001", "HAOAI-NEW-0002"]
    )
    response = application.generate_redemption_codes(
        lambda: uow,
        ADMIN,
        GenerateRedemptionCodesCommand("credits", 12, 2, ""),
        code_generator=generator,
    )

    assert response["count"] == 2
    assert response["type"] == "credits" and response["value"] == 12
    assert [code["code"] for code in response["codes"]] == [
        "HAOAI-NEW-0001",
        "HAOAI-NEW-0002",
    ]
    assert all(code["expires_at"] is None for code in response["codes"])
    assert generator.calls == 3
    assert [event[0] for event in uow.events] == [
        "code_exists",
        "code_exists",
        "add_generated",
        "code_exists",
        "add_generated",
        "commit",
        "refresh",
        "refresh",
        "close",
    ]
    assert uow.commit_count == 1
    assert uow.refresh_ids == [code["id"] for code in response["codes"]]
    assert uow.rollback_count == 0 and uow.close_count == 1


def test_pure_application_projection_keeps_aware_offset_before_persistence():
    uow = MemoryUnitOfWork()
    response = application.generate_redemption_codes(
        lambda: uow,
        ADMIN,
        GenerateRedemptionCodesCommand(
            "credits",
            9,
            1,
            "2029-02-03T04:05:06+05:00",
        ),
        code_generator=lambda: "HAOAI-OFFSET-0001",
    )

    assert response["codes"][0]["expires_at"] == "2029-02-03T04:05:06+05:00Z"


def test_generate_invalid_iso_rolls_back_and_non_admin_never_opens_business_uow():
    uow = MemoryUnitOfWork()
    with pytest.raises(RedemptionCodesBadRequest) as raised:
        application.generate_redemption_codes(
            lambda: uow,
            ADMIN,
            GenerateRedemptionCodesCommand("membership", 2, 1, "not-an-iso-date"),
            code_generator=lambda: "HAOAI-UNUSED-0001",
        )
    assert raised.value.status_code == 400
    assert raised.value.detail == "expires_at 格式不正确，请使用 ISO 格式"
    assert uow.commit_count == 0 and uow.rollback_count == 1 and uow.close_count == 1
    assert not any(event[0] == "code_exists" for event in uow.events)

    factory_calls = 0

    def factory():
        nonlocal factory_calls
        factory_calls += 1
        raise AssertionError("unauthorized application call must not open a UOW")

    with pytest.raises(RedemptionCodesForbidden):
        application.generate_redemption_codes(
            factory,
            MEMBER,
            GenerateRedemptionCodesCommand("credits", 1, 1, None),
        )
    assert factory_calls == 0


@pytest.mark.parametrize(
    ("membership_type", "old_expiry", "expected_expiry"),
    [
        ("premium", NOW + timedelta(days=30), NOW + timedelta(days=35)),
        ("premium", NOW - timedelta(days=30), NOW - timedelta(days=25)),
        ("premium", None, NOW + timedelta(seconds=2, days=5)),
        ("free", NOW + timedelta(days=100), NOW + timedelta(seconds=2, days=5)),
    ],
)
def test_membership_uses_actor_snapshot_and_preserves_source_clock_order(
    membership_type: str,
    old_expiry: datetime | None,
    expected_expiry: datetime,
):
    actor = RedemptionCodesActor("member", False, membership_type, old_expiry)
    uow = MemoryUnitOfWork(
        record=make_code(expires_at=NOW + timedelta(days=1))
    )
    clock = FalseyCallable([NOW, NOW + timedelta(seconds=1), NOW + timedelta(seconds=2)])

    response = application.redeem_redemption_code(
        lambda: uow,
        actor,
        RedeemRedemptionCodeCommand(" haoai-test-0001 "),
        clock=clock,
    )

    assert response["type"] == "membership" and response["value"] == 5
    assert response["detail"] == f"会员有效期至：{expected_expiry.strftime('%Y-%m-%d %H:%M')}"
    assert clock.calls == 3
    assert uow.events[0] == ("load_code", "HAOAI-TEST-0001")
    assert uow.events[1] == ("mark_used", "code-id", "member", NOW + timedelta(seconds=1))
    assert uow.events[2] == (
        "set_membership",
        "member",
        "premium",
        expected_expiry,
    )
    assert uow.commit_count == 1 and uow.rollback_count == 0 and uow.close_count == 1


@pytest.mark.parametrize(
    ("existing_balance", "expected_balance", "flush_count"),
    [(13, 20, 0), (None, 7, 1)],
)
def test_credit_redemption_flushes_only_new_wallet_and_reads_after_commit(
    existing_balance: int | None,
    expected_balance: int,
    flush_count: int,
):
    uow = MemoryUnitOfWork(
        record=make_code(code_type="credits", value=7),
        wallet_balance=existing_balance,
    )
    clock = FalseyCallable([NOW + timedelta(seconds=1)])

    response = application.redeem_redemption_code(
        lambda: uow,
        MEMBER,
        RedeemRedemptionCodeCommand("HAOAI-TEST-0001"),
        clock=clock,
    )

    assert response == {
        "message": "兑换成功！获得 7 积分",
        "type": "credits",
        "value": 7,
        "detail": f"当前积分余额：{expected_balance}",
    }
    assert clock.calls == 1
    assert sum(event[0] == "flush" for event in uow.events) == flush_count
    assert sum(event[0] == "commit" for event in uow.events) == 1
    assert uow.events.index(("commit",)) < uow.events.index(("read_credit_balance", "member"))
    assert len(uow.credit_logs) == 1
    log = uow.credit_logs[0]
    assert (log.amount, log.balance_after, log.type) == (7, expected_balance, "charge")
    assert log.description == "兑换码兑换：HAOAI-TEST-0001"
    assert uow.rollback_count == 0 and uow.close_count == 1


@pytest.mark.parametrize(
    ("record", "clock_values", "error_type", "status"),
    [
        (None, [], RedemptionCodeNotFound, 404),
        (
            make_code(is_used=True, expires_at=NOW - timedelta(seconds=1)),
            [],
            RedemptionCodeAlreadyUsed,
            400,
        ),
        (make_code(expires_at=NOW - timedelta(seconds=1)), [NOW], RedemptionCodeExpired, 400),
        (make_code(code_type="legacy-unknown"), [NOW], RedemptionCodeTypeUnknown, 500),
    ],
)
def test_redemption_validation_precedence_and_unknown_type_rollback(
    record: RedemptionCode | None,
    clock_values: list[datetime],
    error_type: type[Exception],
    status: int,
):
    uow = MemoryUnitOfWork(record=record)
    clock = FalseyCallable(clock_values)

    with pytest.raises(error_type) as raised:
        application.redeem_redemption_code(
            lambda: uow,
            MEMBER,
            RedeemRedemptionCodeCommand("HAOAI-TEST-0001"),
            clock=clock,
        )

    assert getattr(raised.value, "status_code") == status
    assert uow.commit_count == 0 and uow.rollback_count == 1 and uow.close_count == 1
    if record is None:
        assert clock.calls == 0
        assert not any(event[0] == "mark_used" for event in uow.events)
    elif record.is_used:
        assert clock.calls == 0
        assert not any(event[0] == "mark_used" for event in uow.events)
    elif record.expires_at is not None:
        assert clock.calls == 1
        assert not any(event[0] == "mark_used" for event in uow.events)
    else:
        assert clock.calls == 1
        assert any(event[0] == "mark_used" for event in uow.events)


def test_uncertain_commit_is_not_replayed():
    uow = MemoryUnitOfWork()
    original_error = OSError("commit acknowledgement lost")
    uow.commit_error = original_error
    generator = FalseyCallable(["HAOAI-ONCE-0001"])

    with pytest.raises(OSError) as raised:
        application.generate_redemption_codes(
            lambda: uow,
            ADMIN,
            GenerateRedemptionCodesCommand("credits", 1, 1, None),
            code_generator=generator,
        )

    assert raised.value is original_error
    assert generator.calls == 1 and uow.commit_count == 1
    assert uow.rollback_count == 1 and uow.close_count == 1
    assert not any(event[0] == "refresh" for event in uow.events)


def test_uncertain_credit_commit_is_not_replayed_or_read_back():
    uow = MemoryUnitOfWork(
        record=make_code(code_type="credits", value=6),
        wallet_balance=9,
    )
    original_error = OSError("commit acknowledgement lost")
    uow.commit_error = original_error
    clock = FalseyCallable([NOW + timedelta(seconds=1)])

    with pytest.raises(OSError) as raised:
        application.redeem_redemption_code(
            lambda: uow,
            MEMBER,
            RedeemRedemptionCodeCommand("HAOAI-TEST-0001"),
            clock=clock,
        )

    assert raised.value is original_error
    assert clock.calls == 1
    assert uow.commit_count == 1 and uow.rollback_count == 1 and uow.close_count == 1
    assert len(uow.credit_logs) == 1
    assert not any(event[0] == "read_credit_balance" for event in uow.events)
