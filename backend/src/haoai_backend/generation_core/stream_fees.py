"""Stage and verify the first taskless text-stream debit transaction.

The caller owns the staging Session and its transaction. Confirmation and
lookup use a fresh, short-lived Session supplied by the caller so an uncertain
commit is never mistaken for a rollback or charged a second time.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import json
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from haoai_backend.authentication.tables import user_credits
from haoai_backend.generation_catalog import (
    CatalogError,
    ModelCategory,
    ModelSource,
    resolve_model,
)
from haoai_backend.generation_catalog.persistence import AdmissionPersistence
from haoai_backend.generation_catalog.tables import model_configs, system_models
from haoai_backend.generation_contracts import (
    CommittedStreamDebitReceipt,
    GenerationActor,
    GenerationError,
    ModelReference,
    StagedStreamDebitReceipt,
    StreamDebitReceipt,
    StreamFeeAuthority,
    StreamFeeRecord,
    UnknownCommit,
)
from haoai_backend.generation_core.base_schema import CreditLogRow
from haoai_backend.generation_core.fee_phases import (
    StreamFeePlanInput,
    build_fee_phase_plan,
)
from haoai_backend.generation_core.persistence import GenerationRecordRepository
from haoai_backend.generation_core.schema import StreamFeeRecordRow
from haoai_backend.shared.identity import TrustedActor


SessionFactory = Callable[[], Session]
IdFactory = Callable[[], str]
StreamDebitLookup = CommittedStreamDebitReceipt | Literal["proven_absent", "unknown"]

_OPERATION = "text.stream"
_DEBIT_KEY_PREFIX = "stream:debit:"
_MODEL_PROVENANCE_VERSION = "model-ref:v1:"


class InsufficientStreamCredits(Exception):
    """The locked wallet cannot cover the original system-model quote."""

    def __init__(self) -> None:
        super().__init__("积分不足，无法提交流式生成请求")


def stage_stream_debit(
    session: Session,
    authority: StreamFeeAuthority,
    frozen_quote: int,
    *,
    now: datetime | None = None,
    id_factory: IdFactory | None = None,
) -> StagedStreamDebitReceipt:
    """Stage wallet, usage log, and private fee proof in the caller's TX.

    This function only flushes. It never commits, rolls back, or closes the
    supplied Session. The authority and quote must come from the trusted
    server-side frozen model selection; a Task cost is not accepted here.
    """

    _validate_authority(authority, require_admission=True)
    if type(frozen_quote) is not int or frozen_quote != authority.frozen_quote:
        raise _invalid("流式报价必须与可信冻结报价完全一致")
    if id_factory is None:
        id_factory = _new_id
    if not callable(id_factory):
        raise _invalid("流式流水标识生成器不可用")
    staged_at = _as_utc(now)
    _stream_plan(authority)

    actor_id = authority.actor.user_id
    catalog_store = AdmissionPersistence(session)
    with session.no_autoflush:
        account = catalog_store.lock_account(actor_id)
        if account is None or account.user_id != actor_id:
            raise _forbidden("无法验证当前账号")

        existing_row = session.get(StreamFeeRecordRow, authority.server_attempt_id)
        if existing_row is not None:
            original = _read_original_receipt(
                session,
                authority,
                authority.server_attempt_id,
            )
            if original is None:
                raise _conflict("该流式 attempt 已绑定不同或不完整的原始扣款证明")
            return StagedStreamDebitReceipt(_with_commit_state(original, "staged"))

        if not _active_member(account, now=staged_at):
            raise _forbidden("需要有效会员才能提交流式生成请求")

        quote_amount = _validate_locked_model_and_quote(
            session,
            catalog_store,
            authority,
        )
        if quote_amount != frozen_quote:
            raise _invalid("冻结报价与当前锁定模型目录的服务端报价不一致")

        business_key = _business_key(authority.server_attempt_id)
        existing_log = session.execute(
            select(CreditLogRow.id).where(CreditLogRow.business_key == business_key)
        ).scalar_one_or_none()
        if existing_log is not None:
            raise _conflict("该流式扣款键已存在，但没有匹配的原始费用记录")

        actual_debit = 0
        ledger_id: str | None = None
        ledger_key: str | None = None
        if authority.disposition == "debit":
            balance = catalog_store.lock_wallet_balance(actor_id)
            if balance < quote_amount:
                raise InsufficientStreamCredits()
            updated_balance = session.execute(
                update(user_credits)
                .where(
                    user_credits.c.user_id == actor_id,
                    user_credits.c.credits >= quote_amount,
                )
                .values(credits=user_credits.c.credits - quote_amount)
                .returning(user_credits.c.credits)
            ).scalar_one_or_none()
            if updated_balance is None:
                raise InsufficientStreamCredits()

            ledger_id = _server_id(id_factory)
            ledger_key = business_key
            actual_debit = quote_amount
            session.execute(
                insert(CreditLogRow.__table__).values(
                    id=ledger_id,
                    user_id=actor_id,
                    amount=-quote_amount,
                    balance_after=updated_balance,
                    type="usage",
                    description="taskless text stream debit",
                    task_id=None,
                    business_key=ledger_key,
                    billing_unit_id=None,
                    related_debit_id=None,
                    created_at=staged_at.replace(tzinfo=None),
                )
            )
        elif authority.disposition == "skipped":
            balance = catalog_store.lock_wallet_balance(actor_id)
            if balance < quote_amount:
                raise InsufficientStreamCredits()

        record = StreamFeeRecord(
            server_attempt_id=authority.server_attempt_id,
            owner_id=actor_id,
            operation=_OPERATION,
            frozen_model_provenance=_model_provenance(authority.frozen_model_ref),
            quoted_amount=quote_amount,
            disposition=authority.disposition,
            actual_debited_amount=actual_debit,
            original_debit_log_id=ledger_id,
            original_business_key=ledger_key,
            # The row state describes its persisted settlement state. The DTO
            # below still says "staged" until the outer Session commit is
            # known to have succeeded.
            settlement_state="committed",
            start_state="ready_to_start",
            start_token=None,
            task_id=None,
            message_id=None,
            created_at=staged_at,
            updated_at=staged_at,
        )
        result = GenerationRecordRepository(session).stage_record(record)
        if result.status != "created":
            raise _conflict("无法为该流式 attempt 建立唯一费用证明")

    return StagedStreamDebitReceipt(
        StreamDebitReceipt(
            actor_id=actor_id,
            server_attempt_id=authority.server_attempt_id,
            operation=_OPERATION,
            quoted_amount=quote_amount,
            disposition=authority.disposition,
            actual_debited_amount=actual_debit,
            original_debit_log_id=ledger_id,
            original_business_key=ledger_key,
            commit_state="staged",
        )
    )


def confirm_stream_commit(
    authority: StreamFeeAuthority,
    original_attempt: str,
    commit_result: UnknownCommit | None,
    *,
    session_factory: SessionFactory,
    now: datetime | None = None,
) -> CommittedStreamDebitReceipt | UnknownCommit:
    """Confirm only a known successful outer commit against a fresh read TX.

    ``None`` means the caller observed its commit call return successfully. An
    UnknownCommit value is never overridden by a success-shaped flag; callers
    must use lookup_stream_debit after an uncertain commit.
    """

    phase = "stream-debit-confirm"
    if not _authority_is_well_formed(authority) or original_attempt != authority.server_attempt_id:
        return UnknownCommit(original_attempt, phase)
    if commit_result is not None:
        if type(commit_result) is UnknownCommit:
            return UnknownCommit(original_attempt, commit_result.phase)
        return UnknownCommit(original_attempt, phase)
    if not callable(session_factory):
        return UnknownCommit(original_attempt, phase)

    try:
        with session_factory() as read_session:
            if _session_is_not_fresh(read_session):
                return UnknownCommit(original_attempt, phase)
            receipt = _read_original_receipt(read_session, authority, original_attempt)
    except SQLAlchemyError:
        return UnknownCommit(original_attempt, phase)
    if receipt is None:
        return UnknownCommit(original_attempt, phase)

    confirmed_at = _as_utc(now)
    return CommittedStreamDebitReceipt(
        _with_commit_state(receipt, "committed"),
        confirmed_at=confirmed_at,
    )


def lookup_stream_debit(
    authority: StreamFeeAuthority,
    original_attempt: str,
    *,
    session_factory: SessionFactory,
) -> StreamDebitLookup:
    """Read an original debit proof in a fresh Session without re-debiting.

    A missing or mismatched row is ``unknown``. This S0 consumer has no
    authoritative rollback-resolution input, so it never reports absence as
    proven and never performs a debit or refund during lookup.
    """

    if not _authority_is_well_formed(authority) or original_attempt != authority.server_attempt_id:
        return "unknown"
    if not callable(session_factory):
        return "unknown"
    try:
        with session_factory() as read_session:
            if _session_is_not_fresh(read_session):
                return "unknown"
            receipt = _read_original_receipt(read_session, authority, original_attempt)
    except SQLAlchemyError:
        return "unknown"
    if receipt is None:
        return "unknown"
    return CommittedStreamDebitReceipt(
        _with_commit_state(receipt, "committed"),
        confirmed_at=datetime.now(timezone.utc),
    )


def _session_is_not_fresh(session: Session) -> bool:
    return bool(
        session.in_transaction()
        or session.new
        or session.dirty
        or session.deleted
    )


def _validate_locked_model_and_quote(
    session: Session,
    catalog_store: AdmissionPersistence,
    authority: StreamFeeAuthority,
) -> int:
    actor_id = authority.actor.user_id
    model_ref = authority.frozen_model_ref
    catalog = catalog_store.lock_catalog(
        user_id=actor_id,
        model_ids=(model_ref.model_id,),
        prompt_ids=(),
        include_auto_text=False,
    )
    try:
        selection = resolve_model(
            catalog,
            user_id=actor_id,
            model_id=model_ref.model_id,
            category=ModelCategory.TEXT,
        )
    except CatalogError as exc:
        raise _forbidden("指定的文本模型当前不可用") from exc

    if selection.catalog_id != model_ref.model_id:
        raise _forbidden("冻结模型标识与锁定模型目录不一致")
    if selection.source is ModelSource.SYSTEM:
        expected_owner = "system"
        provider = session.execute(
            select(system_models.c.provider).where(
                system_models.c.id == model_ref.model_id,
                system_models.c.category == ModelCategory.TEXT.value,
                system_models.c.is_active.is_(True),
            )
        ).scalar_one_or_none()
    else:
        expected_owner = actor_id
        provider = session.execute(
            select(model_configs.c.provider).where(
                model_configs.c.id == model_ref.model_id,
                model_configs.c.user_id == actor_id,
                model_configs.c.category == ModelCategory.TEXT.value,
            )
        ).scalar_one_or_none()

    if model_ref.owner_id != expected_owner or provider != model_ref.provider:
        raise _forbidden("冻结模型来源与当前锁定模型目录不一致")

    quote_amount = selection.quote.amount
    if type(quote_amount) is not int:
        raise _invalid("服务端模型报价必须是整数")
    if selection.source is ModelSource.USER:
        if authority.disposition != "byok" or quote_amount != 0:
            raise _invalid("BYOK 文本模型必须使用零费用 disposition")
    elif quote_amount > 0:
        if authority.disposition not in {"debit", "skipped"}:
            raise _invalid("正价系统文本模型必须匹配扣款或余额检查 disposition")
    elif authority.disposition != "zero":
        raise _invalid("零价系统文本模型必须使用 zero disposition")

    return quote_amount


def _read_original_receipt(
    session: Session,
    authority: StreamFeeAuthority,
    original_attempt: str,
) -> StreamDebitReceipt | None:
    if original_attempt != authority.server_attempt_id:
        return None

    with session.no_autoflush:
        record = GenerationRecordRepository(session).find_stream_fee_record(
            authority.actor.user_id,
            original_attempt,
        )
        row = session.get(StreamFeeRecordRow, original_attempt)
        if record is None or row is None:
            return None

        expected_business_key = _business_key(original_attempt)
        expected_actual = authority.frozen_quote if authority.disposition == "debit" else 0
        if (
            record.server_attempt_id != original_attempt
            or record.owner_id != authority.actor.user_id
            or record.operation != _OPERATION
            or record.frozen_model_provenance != _model_provenance(authority.frozen_model_ref)
            or record.quoted_amount != authority.frozen_quote
            or record.disposition != authority.disposition
            or record.actual_debited_amount != expected_actual
            or record.task_id is not None
            or record.message_id is not None
        ):
            return None

        key_logs = session.execute(
            select(CreditLogRow).where(CreditLogRow.business_key == expected_business_key)
        ).scalars().all()
        if expected_actual == 0:
            if (
                record.original_debit_log_id is not None
                or record.original_business_key is not None
                or key_logs
            ):
                return None
        else:
            if (
                record.original_debit_log_id is None
                or record.original_business_key != expected_business_key
                or len(key_logs) != 1
            ):
                return None
            log = key_logs[0]
            if (
                log.id != record.original_debit_log_id
                or log.user_id != authority.actor.user_id
                or log.type != "usage"
                or log.amount != -expected_actual
                or log.business_key != expected_business_key
                or log.task_id is not None
                or log.billing_unit_id is not None
                or log.related_debit_id is not None
            ):
                return None

    return StreamDebitReceipt(
        actor_id=authority.actor.user_id,
        server_attempt_id=original_attempt,
        operation=_OPERATION,
        quoted_amount=authority.frozen_quote,
        disposition=authority.disposition,
        actual_debited_amount=expected_actual,
        original_debit_log_id=record.original_debit_log_id,
        original_business_key=record.original_business_key,
        commit_state="committed",
        task_id=None,
        message_id=None,
    )


def _validate_authority(authority: StreamFeeAuthority, *, require_admission: bool) -> None:
    if not _authority_is_well_formed(authority):
        raise _invalid("流式费用 authority 不完整或不可信")
    if require_admission and authority.actor.new_admission_allowed is not True:
        raise _forbidden("当前可信 actor 不允许创建流式准入")


def _authority_is_well_formed(authority: object) -> bool:
    if type(authority) is not StreamFeeAuthority:
        return False
    actor = authority.actor
    if (
        type(actor) is not GenerationActor
        or type(actor.trusted_account_identity) is not TrustedActor
        or not _nonblank(actor.user_id)
        or actor.trusted_account_identity.user_id != actor.user_id
        or type(actor.new_admission_allowed) not in {bool, type(None)}
    ):
        return False
    model_ref = authority.frozen_model_ref
    if (
        type(model_ref) is not ModelReference
        or not _nonblank(model_ref.owner_id)
        or not _nonblank(model_ref.provider)
        or not _nonblank(model_ref.model_id)
        or model_ref.owner_id not in {"system", actor.user_id}
    ):
        return False
    if not isinstance(authority.operation, str) or authority.operation != _OPERATION:
        return False
    if type(authority.frozen_quote) is not int or authority.frozen_quote < 0:
        return False
    if not isinstance(authority.disposition, str) or authority.disposition not in {
        "debit", "skipped", "byok", "zero"
    }:
        return False
    if authority.disposition in {"debit", "skipped"} and authority.frozen_quote <= 0:
        return False
    if authority.disposition in {"byok", "zero"} and authority.frozen_quote != 0:
        return False
    try:
        parsed_attempt = UUID(authority.server_attempt_id)
    except (AttributeError, TypeError, ValueError):
        return False
    return str(parsed_attempt) == authority.server_attempt_id


def _stream_plan(authority: StreamFeeAuthority) -> None:
    build_fee_phase_plan(
        _OPERATION,
        stream=StreamFeePlanInput(
            server_attempt_id=authority.server_attempt_id,
            server_quote=authority.frozen_quote,
            disposition=authority.disposition,
        ),
    )


def _active_member(account: object, *, now: datetime) -> bool:
    if account.is_superuser:
        return True
    if account.membership_type != "premium":
        return False
    expires_at = account.membership_expires_at
    if expires_at is None:
        return True
    if not isinstance(expires_at, datetime):
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    else:
        expires_at = expires_at.astimezone(timezone.utc)
    return now <= expires_at


def _model_provenance(model_ref: ModelReference) -> str:
    payload = {
        "owner_id": model_ref.owner_id,
        "provider": model_ref.provider,
        "model_id": model_ref.model_id,
    }
    return _MODEL_PROVENANCE_VERSION + json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _business_key(server_attempt_id: str) -> str:
    return _DEBIT_KEY_PREFIX + server_attempt_id


def _server_id(id_factory: IdFactory) -> str:
    value = id_factory()
    try:
        parsed = UUID(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise _invalid("服务端扣款流水标识必须是 UUID") from exc
    if str(parsed) != value:
        raise _invalid("服务端扣款流水标识必须是标准 UUID")
    return value


def _new_id() -> str:
    return str(uuid4())


def _with_commit_state(
    receipt: StreamDebitReceipt,
    state: Literal["staged", "committed", "unknown"],
) -> StreamDebitReceipt:
    return StreamDebitReceipt(
        actor_id=receipt.actor_id,
        server_attempt_id=receipt.server_attempt_id,
        operation=receipt.operation,
        quoted_amount=receipt.quoted_amount,
        disposition=receipt.disposition,
        actual_debited_amount=receipt.actual_debited_amount,
        original_debit_log_id=receipt.original_debit_log_id,
        original_business_key=receipt.original_business_key,
        commit_state=state,
        task_id=receipt.task_id,
        message_id=receipt.message_id,
    )


def _as_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, datetime):
        raise _invalid("流式费用时间必须是 datetime")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _nonblank(value: object) -> bool:
    return isinstance(value, str) and bool(value) and value == value.strip()


def _invalid(detail: str) -> GenerationError:
    return GenerationError("invalid_generation_request", detail)


def _forbidden(detail: str) -> GenerationError:
    return GenerationError("membership_or_resource_forbidden", detail)


def _conflict(detail: str) -> GenerationError:
    return GenerationError("inflight_conflict", detail)


__all__ = (
    "InsufficientStreamCredits",
    "SessionFactory",
    "StreamDebitLookup",
    "confirm_stream_commit",
    "lookup_stream_debit",
    "stage_stream_debit",
)
