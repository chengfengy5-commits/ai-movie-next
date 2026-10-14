from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, insert, select, update
from sqlalchemy.orm import Session

from haoai_backend.authentication.tables import (
    metadata as authentication_metadata,
    user_credits,
    users,
)
from haoai_backend.generation_catalog.tables import (
    metadata as catalog_metadata,
    model_configs,
    system_models,
)
from haoai_backend.generation_contracts import (
    CommittedStreamDebitReceipt,
    GenerationActor,
    GenerationError,
    ModelReference,
    StreamFeeAuthority,
    UnknownCommit,
)
from haoai_backend.generation_core.base_schema import (
    Base as FoundationBase,
    CreditLogRow,
    UserCreditRow,
)
from haoai_backend.generation_core.schema import StreamFeeRecordRow
from haoai_backend.generation_core.stream_fees import (
    InsufficientStreamCredits,
    confirm_stream_commit,
    lookup_stream_debit,
    stage_stream_debit,
)
from haoai_backend.shared.identity import TrustedActor


OWNER_ID = "00000000-0000-4000-8000-000000000001"
OTHER_OWNER_ID = "00000000-0000-4000-8000-000000000002"
MODEL_ID = "00000000-0000-4000-8000-000000000010"
NOW = datetime(2026, 10, 14, 3, 0, tzinfo=timezone.utc)


@pytest.fixture

def engine():
    value = create_engine("sqlite+pysqlite:///:memory:")
    authentication_metadata.create_all(value)
    FoundationBase.metadata.create_all(value)
    catalog_metadata.create_all(value)
    with Session(value) as session:
        _seed_owner(session, OWNER_ID, credits=25)
        _seed_system_model(session, MODEL_ID, cost=7)
        session.commit()
    try:
        yield value
    finally:
        value.dispose()


def _seed_owner(
    session: Session,
    owner_id: str,
    *,
    credits: int,
    membership_type: str = "premium",
    expires_at: datetime | None = None,
) -> None:
    created_at = NOW.replace(tzinfo=None)
    session.execute(
        insert(users).values(
            id=owner_id,
            username=f"user-{owner_id[-1]}",
            email=f"{owner_id[-1]}@example.invalid",
            hashed_password="test-only",
            is_superuser=False,
            membership_type=membership_type,
            membership_expires_at=expires_at,
            created_at=created_at,
        )
    )
    session.execute(
        insert(user_credits).values(
            id=str(uuid4()),
            user_id=owner_id,
            credits=credits,
            created_at=created_at,
            updated_at=created_at,
        )
    )


def _seed_system_model(
    session: Session,
    model_id: str,
    *,
    cost: int,
    active: bool = True,
    provider: str = "provider-x",
) -> None:
    created_at = NOW.replace(tzinfo=None)
    session.execute(
        insert(system_models).values(
            id=model_id,
            category="text",
            name="Test text model",
            provider=provider,
            api_key="test-only-secret",
            model="wire-model-x",
            base_url=None,
            cost_per_call=cost,
            description=None,
            params=None,
            is_active=active,
            sort_order=0,
            created_at=created_at,
            updated_at=created_at,
        )
    )


def _seed_byok_model(session: Session, owner_id: str, model_id: str) -> None:
    created_at = NOW.replace(tzinfo=None)
    session.execute(
        insert(model_configs).values(
            id=model_id,
            user_id=owner_id,
            category="text",
            name="Test BYOK model",
            provider="byok-provider",
            api_key="test-only-secret",
            model="byok-wire-model",
            base_url=None,
            sort_order=0,
            params=None,
            created_at=created_at,
            updated_at=created_at,
        )
    )


def _authority(
    *,
    owner_id: str = OWNER_ID,
    attempt_id: str | None = None,
    model_id: str = MODEL_ID,
    model_owner: str = "system",
    provider: str = "provider-x",
    quote: int = 7,
    disposition: str = "debit",
) -> StreamFeeAuthority:
    return StreamFeeAuthority(
        actor=GenerationActor(
            user_id=owner_id,
            trusted_account_identity=TrustedActor(owner_id),
            new_admission_allowed=True,
        ),
        server_attempt_id=attempt_id or str(uuid4()),
        operation="text.stream",
        frozen_model_ref=ModelReference(
            owner_id=model_owner,
            provider=provider,
            model_id=model_id,
        ),
        frozen_quote=quote,
        disposition=disposition,  # type: ignore[arg-type]
    )


def _session_factory(engine):
    return lambda: Session(engine)


def _wallet_balance(session: Session, owner_id: str = OWNER_ID) -> int:
    return session.execute(
        select(UserCreditRow.credits).where(UserCreditRow.user_id == owner_id)
    ).scalar_one()


def test_positive_debit_flushes_exact_taskless_proof_and_confirms_after_commit(engine) -> None:
    authority = _authority()
    session = Session(engine)
    staged = stage_stream_debit(
        session,
        authority,
        7,
        now=NOW,
        id_factory=lambda: "00000000-0000-4000-8000-000000000099",
    )

    assert staged.receipt.commit_state == "staged"
    assert staged.receipt.actual_debited_amount == 7
    assert staged.receipt.original_business_key == f"stream:debit:{authority.server_attempt_id}"
    assert _wallet_balance(session) == 18
    assert session.in_transaction()

    log = session.execute(select(CreditLogRow)).scalar_one()
    assert log.amount == -7
    assert log.user_id == OWNER_ID
    assert log.type == "usage"
    assert log.business_key == staged.receipt.original_business_key
    assert log.task_id is None
    assert log.billing_unit_id is None
    assert log.related_debit_id is None
    ledger_id = log.id

    record = session.get(StreamFeeRecordRow, authority.server_attempt_id)
    assert record is not None
    assert record.settlement_state == "committed"
    assert record.actual_debited_amount == 7
    assert record.original_debit_log_id == log.id
    assert record.task_id is None
    assert record.message_id is None
    session.commit()
    session.close()

    confirmed = confirm_stream_commit(
        authority,
        authority.server_attempt_id,
        None,
        session_factory=_session_factory(engine),
        now=NOW,
    )
    assert isinstance(confirmed, CommittedStreamDebitReceipt)
    assert confirmed.receipt.commit_state == "committed"
    assert confirmed.receipt.original_debit_log_id == ledger_id


def test_caller_rollback_removes_wallet_log_and_private_record_together(engine) -> None:
    authority = _authority()
    session = Session(engine)
    stage_stream_debit(session, authority, 7, now=NOW)
    assert _wallet_balance(session) == 18
    session.rollback()
    session.close()

    with Session(engine) as check:
        assert _wallet_balance(check) == 25
        assert check.execute(select(CreditLogRow.id)).scalars().all() == []
        assert check.execute(select(StreamFeeRecordRow.server_attempt_id)).scalars().all() == []


def test_exact_replay_does_not_debit_twice_and_tampered_proof_is_rejected(engine) -> None:
    authority = _authority()
    with Session(engine) as first:
        stage_stream_debit(first, authority, 7, now=NOW)
        first.commit()

    with Session(engine) as replay:
        receipt = stage_stream_debit(
            replay,
            authority,
            7,
            now=NOW + timedelta(seconds=1),
            id_factory=lambda: pytest.fail("replay must not allocate another debit log"),
        )
        assert receipt.receipt.commit_state == "staged"
        replay.commit()

    with Session(engine) as check:
        assert _wallet_balance(check) == 18
        assert len(check.execute(select(CreditLogRow.id)).scalars().all()) == 1
        check.execute(
            update(CreditLogRow)
            .where(CreditLogRow.business_key == f"stream:debit:{authority.server_attempt_id}")
            .values(amount=-6)
        )
        check.commit()

    assert lookup_stream_debit(
        authority,
        authority.server_attempt_id,
        session_factory=_session_factory(engine),
    ) == "unknown"
    assert isinstance(
        confirm_stream_commit(
            authority,
            authority.server_attempt_id,
            None,
            session_factory=_session_factory(engine),
        ),
        UnknownCommit,
    )


def test_foreign_owner_cannot_replay_another_owners_attempt(engine) -> None:
    attempt_id = str(uuid4())
    original = _authority(attempt_id=attempt_id)
    with Session(engine) as first:
        stage_stream_debit(first, original, 7, now=NOW)
        first.commit()
        _seed_owner(first, OTHER_OWNER_ID, credits=40)
        first.commit()

    foreign = _authority(owner_id=OTHER_OWNER_ID, attempt_id=attempt_id)
    with Session(engine) as second:
        with pytest.raises(GenerationError):
            stage_stream_debit(second, foreign, 7, now=NOW)
        second.rollback()

    with Session(engine) as check:
        assert _wallet_balance(check, OWNER_ID) == 18
        assert _wallet_balance(check, OTHER_OWNER_ID) == 40
        assert len(check.execute(select(CreditLogRow.id)).scalars().all()) == 1


def test_expired_member_and_inactive_model_are_rejected_before_any_debit(engine) -> None:
    expired = _authority()
    with Session(engine) as session:
        session.execute(
            update(users)
            .where(users.c.id == OWNER_ID)
            .values(membership_expires_at=(NOW - timedelta(seconds=1)).replace(tzinfo=None))
        )
        session.commit()

    with Session(engine) as session:
        with pytest.raises(GenerationError):
            stage_stream_debit(session, expired, 7, now=NOW)
        session.rollback()

    with Session(engine) as session:
        session.execute(
            update(system_models)
            .where(system_models.c.id == MODEL_ID)
            .values(is_active=False)
        )
        session.execute(
            update(users)
            .where(users.c.id == OWNER_ID)
            .values(membership_expires_at=None)
        )
        session.commit()

    with Session(engine) as session:
        with pytest.raises(GenerationError):
            stage_stream_debit(session, expired, 7, now=NOW)
        session.rollback()

    with Session(engine) as check:
        assert _wallet_balance(check) == 25
        assert check.execute(select(CreditLogRow.id)).scalars().all() == []
        assert check.execute(select(StreamFeeRecordRow.server_attempt_id)).scalars().all() == []


def test_insufficient_balance_rejects_without_partial_financial_rows(engine) -> None:
    with Session(engine) as session:
        session.execute(
            update(user_credits)
            .where(user_credits.c.user_id == OWNER_ID)
            .values(credits=2)
        )
        session.commit()

    authority = _authority()
    with Session(engine) as session:
        with pytest.raises(InsufficientStreamCredits):
            stage_stream_debit(session, authority, 7, now=NOW)
        session.rollback()

    with Session(engine) as check:
        assert _wallet_balance(check) == 2
        assert check.execute(select(CreditLogRow.id)).scalars().all() == []
        assert check.execute(select(StreamFeeRecordRow.server_attempt_id)).scalars().all() == []


def test_skipped_byok_and_zero_are_balance_only_or_bookkeeping_only(engine) -> None:
    skipped = _authority(disposition="skipped")
    with Session(engine) as session:
        receipt = stage_stream_debit(session, skipped, 7, now=NOW)
        assert receipt.receipt.actual_debited_amount == 0
        assert receipt.receipt.original_debit_log_id is None
        assert receipt.receipt.original_business_key is None
        assert _wallet_balance(session) == 25
        session.commit()

    byok_model_id = "00000000-0000-4000-8000-000000000011"
    with Session(engine) as session:
        _seed_byok_model(session, OWNER_ID, byok_model_id)
        session.commit()
    byok = _authority(
        model_id=byok_model_id,
        model_owner=OWNER_ID,
        provider="byok-provider",
        quote=0,
        disposition="byok",
    )
    with Session(engine) as session:
        stage_stream_debit(session, byok, 0, now=NOW)
        session.commit()

    zero_model_id = "00000000-0000-4000-8000-000000000012"
    with Session(engine) as session:
        _seed_system_model(session, zero_model_id, cost=0)
        session.commit()
    zero = _authority(
        model_id=zero_model_id,
        quote=0,
        disposition="zero",
    )
    with Session(engine) as session:
        stage_stream_debit(session, zero, 0, now=NOW)
        session.commit()

    with Session(engine) as check:
        assert _wallet_balance(check) == 25
        assert check.execute(select(CreditLogRow.id)).scalars().all() == []
        rows = check.execute(select(StreamFeeRecordRow)).scalars().all()
        assert len(rows) == 3
        assert all(row.actual_debited_amount == 0 for row in rows)
        assert all(row.original_debit_log_id is None for row in rows)
        assert all(row.original_business_key is None for row in rows)
        assert all(row.task_id is None and row.message_id is None for row in rows)


def test_bool_is_not_accepted_as_integer_quote(engine) -> None:
    authority = _authority(quote=True)  # type: ignore[arg-type]
    with Session(engine) as session:
        with pytest.raises(GenerationError):
            stage_stream_debit(session, authority, True)  # type: ignore[arg-type]
        session.rollback()

    with Session(engine) as check:
        assert _wallet_balance(check) == 25
        assert check.execute(select(CreditLogRow.id)).scalars().all() == []


def test_commit_ack_loss_is_unknown_until_fresh_read_finds_original_proof(engine) -> None:
    authority = _authority()

    class CommitThenRaiseSession(Session):
        def commit(self) -> None:
            super().commit()
            raise RuntimeError("commit acknowledgment was lost")

    session = CommitThenRaiseSession(engine)
    stage_stream_debit(session, authority, 7, now=NOW)
    with pytest.raises(RuntimeError):
        session.commit()
    session.close()

    unknown = UnknownCommit(authority.server_attempt_id, "stream-debit")
    assert isinstance(
        confirm_stream_commit(
            authority,
            authority.server_attempt_id,
            unknown,
            session_factory=_session_factory(engine),
        ),
        UnknownCommit,
    )
    # A success-shaped flag is not commit evidence, even if the write survived.
    assert isinstance(
        confirm_stream_commit(
            authority,
            authority.server_attempt_id,
            True,  # type: ignore[arg-type]
            session_factory=_session_factory(engine),
        ),
        UnknownCommit,
    )
    lookup = lookup_stream_debit(
        authority,
        authority.server_attempt_id,
        session_factory=_session_factory(engine),
    )
    assert isinstance(lookup, CommittedStreamDebitReceipt)
    assert lookup.receipt.actual_debited_amount == 7
    assert lookup.receipt.original_business_key == f"stream:debit:{authority.server_attempt_id}"

    missing = _authority(attempt_id=str(uuid4()))
    assert lookup_stream_debit(
        missing,
        missing.server_attempt_id,
        session_factory=_session_factory(engine),
    ) == "unknown"
