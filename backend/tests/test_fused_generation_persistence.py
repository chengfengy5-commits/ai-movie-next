from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, event, insert, select, update
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.dml import Update

from haoai_backend.admin_system_models.crypto import LegacyFernet
from haoai_backend.authentication.tables import (
    metadata as authentication_metadata,
    user_credits,
    users,
)
from haoai_backend.chat_data.tables import chat_messages, metadata as chat_metadata
from haoai_backend.fused_generation.domain import (
    FUSED_DELIVERY_PENDING_PROGRESS_MESSAGE,
    FUSED_MEDIA_STORAGE_UNKNOWN_REASON,
    FUSED_MEDIA_STORAGE_UNKNOWN_PROGRESS_MESSAGE,
    FusedGenerationInlineImage,
    FUSED_UNKNOWN_PROGRESS_MESSAGE,
    FUSED_UNKNOWN_REASON,
    FusedFrameTarget,
    FusedGenerationUnknown,
    FusedServerImageModel,
    PreparedFusedGeneration,
)
from haoai_backend.fused_generation.errors import (
    FusedGenerationPaymentRequired,
    FusedGenerationUnavailable,
)
from haoai_backend.fused_generation.persistence import SqlAlchemyFusedGenerationUnitOfWork
from haoai_backend.fused_generation.execution import FusedGenerationExecution
from haoai_backend.fused_generation.persistence import fused_generation_uow_factory
from haoai_backend.fused_generation.ownership import FUSED_SERVER_CONTROL_MARKER
from haoai_backend.generation_catalog.tables import (
    metadata as catalog_metadata,
    model_configs,
    prompt_configs,
    system_models,
    system_prompts,
)
from haoai_backend.generation_core.base_schema import AITaskRow, Base, CreditLogRow
from haoai_backend.generation_core.schema import FusedGenerationSubmissionRow
from haoai_backend.generation_contracts import StoredMedia
from haoai_backend.series_data.tables import (
    chapters,
    characters,
    metadata as series_metadata,
    props,
    series,
)
from haoai_backend.series_exchange.tables import fused_assets, metadata as exchange_metadata


NOW = datetime(2026, 10, 15, 12, 0, 0)
SECRET = "database-provider-secret"


@pytest.fixture
def fused_database(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'fused-generation.sqlite'}", future=True)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    authentication_metadata.create_all(engine)
    Base.metadata.create_all(engine)
    FusedGenerationSubmissionRow.__table__.create(engine)
    series_metadata.create_all(engine, tables=[series, chapters, characters, props])
    catalog_metadata.create_all(
        engine,
        tables=[system_models, model_configs, system_prompts, prompt_configs],
    )
    chat_metadata.create_all(engine, tables=[chat_messages])
    exchange_metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False, future=True)
    with engine.begin() as connection:
        connection.execute(
            insert(users).values(
                id="owner",
                username="owner",
                email="owner@example.test",
                hashed_password="unused-local-fixture",
                is_superuser=False,
                membership_type="premium",
                membership_expires_at=None,
                avatar_url=None,
                bio=None,
                created_at=NOW - timedelta(days=3),
                password_updated_at=NOW - timedelta(days=3),
            )
        )
        connection.execute(
            insert(user_credits).values(
                id="credit-owner",
                user_id="owner",
                credits=12,
                created_at=NOW - timedelta(days=3),
                updated_at=NOW - timedelta(days=3),
            )
        )
        connection.execute(
            insert(series).values(
                id="series-a",
                user_id="owner",
                name="剧集A",
                description="",
                image_url=None,
                style_prompt_id=None,
                team_id=None,
                claimed_by=None,
                claimed_at=None,
                created_at=NOW - timedelta(days=2),
                updated_at=NOW - timedelta(days=2),
            )
        )
        connection.execute(
            insert(chapters).values(
                id="chapter-a",
                series_id="series-a",
                title="第一章",
                content=json.dumps([{"text": "镜头A", "fused": []}], ensure_ascii=False),
                order=0,
                created_at=NOW - timedelta(days=1),
                updated_at=NOW - timedelta(days=1),
            )
        )
        connection.execute(
            insert(characters).values(
                id="character-a",
                series_id="series-a",
                name="林岚",
                aliases=None,
                canonical_key=None,
                gender=None,
                age=None,
                role=None,
                appearance=None,
                description=None,
                image_url="https://assets.test/lin.png",
                audio_url=None,
                voice_ref=None,
                created_at=NOW - timedelta(days=1),
                updated_at=NOW - timedelta(days=1),
            )
        )
        connection.execute(
            insert(system_models).values(
                id="system-model",
                category="text",  # The legacy resolver did not filter by category.
                name="系统模型",
                provider="provider-a",
                api_key=SECRET,
                model="image-model-v1",
                base_url="https://provider.test/api",
                cost_per_call=5,
                description=None,
                params="{}",
                is_active=True,
                sort_order=1,
                created_at=NOW - timedelta(days=3),
                updated_at=NOW - timedelta(days=3),
            )
        )
        connection.execute(
            insert(model_configs).values(
                id="owned-model",
                user_id="owner",
                category="video",
                name="自有模型",
                provider="provider-owned",
                api_key="owned-secret",
                model="owned-image-model",
                base_url=None,
                sort_order=1,
                params="{}",
                created_at=NOW - timedelta(days=2),
                updated_at=NOW - timedelta(days=2),
            )
        )
        connection.execute(
            insert(system_prompts).values(
                id="system-prompt",
                category="fusion",
                name="系统提示词",
                system_prompt="保持画风",
                user_prompt="突出表情",
                is_active=True,
                sort_order=1,
                created_at=NOW - timedelta(days=3),
                updated_at=NOW - timedelta(days=3),
            )
        )
        connection.execute(
            insert(prompt_configs).values(
                id="owned-prompt",
                user_id="owner",
                category="fusion",
                name="个人提示词",
                system_prompt="个人系统",
                user_prompt="个人用户",
                sort_order=1,
                created_at=NOW - timedelta(days=1),
                updated_at=NOW - timedelta(days=1),
            )
        )
    try:
        yield engine, factory
    finally:
        engine.dispose()


def _model(*, system: bool = True) -> FusedServerImageModel:
    return FusedServerImageModel(
        id="system-model" if system else "owned-model",
        name="系统模型" if system else "自有模型",
        cost_per_call=5 if system else 0,
        provider="provider-a",
        wire_model="image-model-v1",
        api_key=SECRET if system else "owned-secret",
        is_system=system,
    )


def _prepared(*, target: FusedFrameTarget | None = None) -> PreparedFusedGeneration:
    return PreparedFusedGeneration(
        task_id="task-a",
        message_id="message-a" if target is not None else None,
        user_id="owner",
        series_id="series-a",
        chapter_id="chapter-a" if target is not None else None,
        member_type="character",
        members=({"kind": "asset", "id": "character-a", "name": "林岚"},),
        name="角色融合 1合1",
        size="2K",
        prompt="【角色资产对照】\n图片1 = 林岚",
        reference_images=("https://assets.test/lin.png",),
        model_id="system-model",
        model_name="系统模型",
        credit_cost=5,
        server_model=_model(),
        target_frame=target,
    )


def _stage_task(factory, prepared):
    uow = SqlAlchemyFusedGenerationUnitOfWork(factory())
    try:
        uow.stage_request(prepared, prepared.user_id)
        uow.commit()
    finally:
        uow.close()


def test_recovery_scan_and_receipt_read_project_only_small_safe_fields(fused_database):
    engine, factory = fused_database
    statements: list[str] = []

    def capture_statement(_connection, _cursor, statement, _parameters, _context, _many):
        if "fused_generation_submissions" in statement.lower():
            statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", capture_statement)
    uow = SqlAlchemyFusedGenerationUnitOfWork(factory())
    try:
        assert uow.recoverable_durable_submissions(limit=32) == ()
        assert uow.find_durable_submission_receipt("owner", "missing-key") is None
    finally:
        uow.rollback()
        uow.close()
        event.remove(engine, "before_cursor_execute", capture_statement)

    assert len(statements) == 2
    assert "encrypted_" not in statements[0]
    assert "request_nonce" not in statements[0]
    assert "result_digest" not in statements[0]
    assert "encrypted_" not in statements[1]
    assert "request_nonce" not in statements[1]
    assert "result_digest" not in statements[1]


def test_system_model_charge_is_atomic_and_owned_model_is_free(fused_database):
    engine, factory = fused_database
    uow = SqlAlchemyFusedGenerationUnitOfWork(factory())
    try:
        system_model = uow.resolve_and_charge_model("owner", "system-model")
        assert system_model.id == "system-model"
        assert system_model.cost_per_call == 5
        assert system_model.provider == "provider-a"
        assert SECRET not in repr(system_model)

        owned_model = uow.resolve_and_charge_model("owner", "owned-model")
        assert owned_model.is_system is False
        assert owned_model.cost_per_call == 0
        assert owned_model.id == "owned-model"
    finally:
        uow.close()

    with engine.connect() as connection:
        assert connection.execute(select(user_credits.c.credits)).scalar_one() == 7
        logs = connection.execute(select(CreditLogRow.__table__)).mappings().all()
    assert [(row["amount"], row["type"], row["description"]) for row in logs] == [
        (-5, "usage", "使用 系统模型 消耗积分")
    ]


def test_system_model_insufficient_credits_returns_402_without_partial_log(fused_database):
    engine, factory = fused_database
    with engine.begin() as connection:
        connection.execute(update(user_credits).values(credits=4))

    uow = SqlAlchemyFusedGenerationUnitOfWork(factory())
    with pytest.raises(FusedGenerationPaymentRequired, match="需要 5 积分") as error:
        uow.resolve_and_charge_model("owner", "system-model")
    assert error.value.status_code == 402
    uow.rollback()
    uow.close()

    with engine.connect() as connection:
        assert connection.execute(select(user_credits.c.credits)).scalar_one() == 4
        assert connection.execute(select(CreditLogRow.id)).first() is None


@pytest.mark.parametrize(
    "decrypt_api_key",
    [LegacyFernet(None).decrypt, LegacyFernet("incorrect-local-secret").decrypt],
    ids=["missing-secret", "incorrect-secret"],
)
def test_encrypted_model_key_failure_is_503_before_any_charge_or_task(
    fused_database,
    decrypt_api_key,
):
    engine, factory = fused_database
    with engine.begin() as connection:
        connection.execute(
            update(system_models)
            .where(system_models.c.id == "system-model")
            .values(api_key=LegacyFernet(SECRET).encrypt(SECRET))
        )

    uow = SqlAlchemyFusedGenerationUnitOfWork(
        factory(),
        decrypt_api_key=decrypt_api_key,
    )
    with pytest.raises(FusedGenerationUnavailable) as error:
        uow.resolve_and_charge_model("owner", "system-model")
    assert error.value.status_code == 503
    uow.rollback()
    uow.close()

    with engine.connect() as connection:
        balance = connection.execute(select(user_credits.c.credits)).scalar_one()
        logs = connection.execute(select(CreditLogRow.__table__)).mappings().all()
        tasks = connection.execute(select(AITaskRow.__table__)).mappings().all()
        messages = connection.execute(select(chat_messages)).mappings().all()
    assert balance == 12
    assert logs == []
    assert tasks == []
    assert messages == []


def test_failure_finalization_refunds_once_and_duplicate_terminal_delivery_is_noop(
    fused_database,
):
    engine, factory = fused_database
    target = FusedFrameTarget(
        chapter_id="chapter-a",
        frame_index=0,
        identity=json.dumps(
            {"text": "镜头A"},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    prepared = _prepared(target=target)
    _stage_task(factory, prepared)

    claim_uow = SqlAlchemyFusedGenerationUnitOfWork(factory())
    claim = claim_uow.claim_task(
        prepared.task_id, "claim-a", expected_user_id=prepared.user_id
    )
    assert claim is not None and claim.cancelled is False
    claim_uow.commit()
    claim_uow.close()

    for _ in range(2):
        uow = SqlAlchemyFusedGenerationUnitOfWork(factory())
        uow.finish_task(
            prepared,
            "claim-a",
            image_url=None,
            error_message="上游暂时不可用",
        )
        uow.commit()
        uow.close()

    with engine.connect() as connection:
        task = connection.execute(select(AITaskRow.__table__)).mappings().one()
        wallet = connection.execute(select(user_credits.c.credits)).scalar_one()
        logs = connection.execute(
            select(CreditLogRow.__table__).where(CreditLogRow.task_id == prepared.task_id)
        ).mappings().all()
        asset = connection.execute(select(fused_assets)).mappings().one()
        chapter_content = connection.execute(select(chapters.c.content)).scalar_one()
    assert task["status"] == "failed"
    assert wallet == 17
    assert [(row["amount"], row["type"], row["business_key"]) for row in logs] == [
        (5, "refund", "fused-refund:task-a")
    ]
    assert asset["status"] == "failed"
    assert asset["error_message"] == "上游暂时不可用"
    assert json.loads(chapter_content)[0]["fused"] == [asset["id"]]


def test_unknown_retention_requires_owner_token_and_marked_fused_task_and_blocks_late_finish(
    fused_database,
):
    engine, factory = fused_database
    prepared = _prepared()
    _stage_task(factory, prepared)

    claim_uow = SqlAlchemyFusedGenerationUnitOfWork(factory())
    claim = claim_uow.claim_task(
        prepared.task_id, "claim-a", expected_user_id=prepared.user_id
    )
    assert claim is not None and claim.cancelled is False
    claim_uow.commit()
    claim_uow.close()

    uow = SqlAlchemyFusedGenerationUnitOfWork(factory())
    assert not uow.retain_unknown(prepared.task_id, "other-user", "claim-a", "private")
    assert not uow.retain_unknown(prepared.task_id, prepared.user_id, "wrong-token", "private")
    assert uow.retain_unknown(prepared.task_id, prepared.user_id, "claim-a", "upstream secret")
    uow.commit()
    uow.close()

    claim_again = SqlAlchemyFusedGenerationUnitOfWork(factory())
    assert claim_again.claim_task(
        prepared.task_id, "claim-b", expected_user_id=prepared.user_id
    ) is None
    claim_again.rollback()
    claim_again.close()

    late_finish = SqlAlchemyFusedGenerationUnitOfWork(factory())
    late_finish.finish_task(
        prepared,
        "claim-a",
        image_url="https://provider.test/late-success.png",
        error_message=None,
    )
    late_finish.commit()
    late_finish.close()

    with engine.connect() as connection:
        task = connection.execute(select(AITaskRow.__table__)).mappings().one()
        assets = connection.execute(select(fused_assets)).mappings().all()
        wallet = connection.execute(select(user_credits.c.credits)).scalar_one()
        logs = connection.execute(select(CreditLogRow.__table__)).mappings().all()
    assert task["status"] == "processing"
    assert task["claim_token"] == "claim-a"
    assert task["recovery_status"] == "provider_result_unknown"
    assert task["result"] == FUSED_UNKNOWN_REASON
    assert task["progress_message"] == FUSED_UNKNOWN_PROGRESS_MESSAGE
    assert assets == []
    assert wallet == 12
    assert logs == []


def test_unknown_retention_accepts_existing_cancelling_status_and_preserves_it(
    fused_database,
):
    engine, factory = fused_database
    prepared = replace(_prepared(), task_id="task-cancelling")
    _stage_task(factory, prepared)

    claim_uow = SqlAlchemyFusedGenerationUnitOfWork(factory())
    claim = claim_uow.claim_task(
        prepared.task_id, "claim-c", expected_user_id=prepared.user_id
    )
    assert claim is not None
    claim_uow.commit()
    claim_uow.close()

    with engine.begin() as connection:
        connection.execute(
            update(AITaskRow.__table__)
            .where(AITaskRow.id == prepared.task_id)
            .values(status="cancelling")
        )

    late_inline_finish = SqlAlchemyFusedGenerationUnitOfWork(factory())
    assert not late_inline_finish.finish_task(
        prepared,
        "claim-c",
        image_url="https://media.example.test/fused/owned.png",
        error_message=None,
        preserve_if_cancelled=True,
    )
    late_inline_finish.commit()
    late_inline_finish.close()

    uow = SqlAlchemyFusedGenerationUnitOfWork(factory())
    assert uow.retain_unknown(
        prepared.task_id, prepared.user_id, "claim-c", FUSED_UNKNOWN_REASON
    )
    uow.commit()
    uow.close()

    with engine.connect() as connection:
        task = connection.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == prepared.task_id)
        ).mappings().one()
        assets = connection.execute(select(fused_assets)).mappings().all()
        wallet = connection.execute(select(user_credits.c.credits)).scalar_one()
        logs = connection.execute(select(CreditLogRow.__table__)).mappings().all()
    assert task["status"] == "cancelling"
    assert task["claim_token"] == "claim-c"
    assert task["recovery_status"] == "provider_result_unknown"
    assert assets == []
    assert wallet == 12
    assert logs == []


def test_known_provider_url_retention_preserves_cancelling_task_and_blocks_late_finish(
    fused_database,
):
    engine, factory = fused_database
    prepared = replace(_prepared(), task_id="task-known-url-cancelling")
    _stage_task(factory, prepared)

    claim_uow = SqlAlchemyFusedGenerationUnitOfWork(factory())
    claim = claim_uow.claim_task(
        prepared.task_id,
        "claim-known-url",
        expected_user_id=prepared.user_id,
    )
    assert claim is not None
    claim_uow.commit()
    claim_uow.close()

    with engine.begin() as connection:
        connection.execute(
            update(AITaskRow.__table__)
            .where(AITaskRow.id == prepared.task_id)
            .values(status="cancelling")
        )

    uow = SqlAlchemyFusedGenerationUnitOfWork(factory())
    assert uow.retain_known_media_result(
        prepared.task_id,
        prepared.user_id,
        "claim-known-url",
        "https://provider.example.test/known-result.png?opaque=synthetic",
    )
    uow.commit()
    uow.close()

    late_finish = SqlAlchemyFusedGenerationUnitOfWork(factory())
    assert not late_finish.finish_task(
        prepared,
        "claim-known-url",
        image_url="https://cdn.example.test/fused/late.png",
        error_message=None,
        preserve_if_cancelled=True,
    )
    late_finish.commit()
    late_finish.close()

    with engine.connect() as connection:
        task = connection.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == prepared.task_id)
        ).mappings().one()
        assets = connection.execute(select(fused_assets)).mappings().all()
        wallet = connection.execute(select(user_credits.c.credits)).scalar_one()
        logs = connection.execute(select(CreditLogRow.__table__)).mappings().all()
    assert task["status"] == "cancelling"
    assert task["claim_token"] == "claim-known-url"
    assert task["recovery_status"] == "provider_result_unknown"
    assert task["result"] == "https://provider.example.test/known-result.png?opaque=synthetic"
    assert task["progress_message"] == FUSED_DELIVERY_PENDING_PROGRESS_MESSAGE
    assert assets == []
    assert wallet == 12
    assert logs == []


def test_unknown_retention_does_not_take_ownership_of_unmarked_legacy_fused_task(
    fused_database,
):
    engine, factory = fused_database
    prepared = replace(_prepared(), task_id="legacy-fused")
    _stage_task(factory, prepared)
    with engine.begin() as connection:
        connection.execute(
            update(AITaskRow.__table__)
            .where(AITaskRow.id == prepared.task_id)
            .values(request_data="{}")
        )

    runtime = UnknownRuntime()
    execution = FusedGenerationExecution(runtime, fused_generation_uow_factory(factory))
    asyncio.run(execution.execute(prepared))

    with engine.connect() as connection:
        task = connection.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == prepared.task_id)
        ).mappings().one()
        assets = connection.execute(select(fused_assets)).mappings().all()
        wallet = connection.execute(select(user_credits.c.credits)).scalar_one()
        logs = connection.execute(select(CreditLogRow.__table__)).mappings().all()
    assert runtime.calls == 0
    assert task["status"] == "processing"
    assert task["claim_token"] is None
    assert task["recovery_status"] == "ready"
    assert task["request_data"] == "{}"
    assert assets == []
    assert wallet == 12
    assert logs == []


def test_inline_storage_authorization_rechecks_owner_marker_status_and_token(fused_database):
    engine, factory = fused_database
    prepared = replace(_prepared(), task_id="inline-storage-authorization")
    _stage_task(factory, prepared)

    claim_uow = SqlAlchemyFusedGenerationUnitOfWork(factory())
    claim = claim_uow.claim_task(
        prepared.task_id,
        "claim-inline",
        expected_user_id=prepared.user_id,
    )
    assert claim is not None
    claim_uow.commit()
    claim_uow.close()

    authorized = SqlAlchemyFusedGenerationUnitOfWork(factory())
    assert authorized.authorize_media_storage(
        prepared.task_id, prepared.user_id, "claim-inline"
    )
    assert not authorized.authorize_media_storage(
        prepared.task_id, "other-owner", "claim-inline"
    )
    assert not authorized.authorize_media_storage(
        prepared.task_id, prepared.user_id, "wrong-token"
    )
    authorized.rollback()
    authorized.close()

    with engine.begin() as connection:
        connection.execute(
            update(AITaskRow.__table__)
            .where(AITaskRow.id == prepared.task_id)
            .values(request_data="{}")
        )
    unmarked = SqlAlchemyFusedGenerationUnitOfWork(factory())
    assert not unmarked.authorize_media_storage(
        prepared.task_id, prepared.user_id, "claim-inline"
    )
    unmarked.rollback()
    unmarked.close()

    with engine.begin() as connection:
        connection.execute(
            update(AITaskRow.__table__)
            .where(AITaskRow.id == prepared.task_id)
            .values(status="cancelling")
        )
    cancelling = SqlAlchemyFusedGenerationUnitOfWork(factory())
    assert not cancelling.authorize_media_storage(
        prepared.task_id, prepared.user_id, "claim-inline"
    )
    cancelling.rollback()
    cancelling.close()


def test_execution_does_not_claim_fused_task_for_wrong_prepared_owner(fused_database):
    engine, factory = fused_database
    prepared = replace(_prepared(), task_id="wrong-prepared-owner")
    _stage_task(factory, prepared)
    runtime = UnknownRuntime()
    execution = FusedGenerationExecution(runtime, fused_generation_uow_factory(factory))

    asyncio.run(execution.execute(replace(prepared, user_id="other-owner")))

    with engine.connect() as connection:
        task = connection.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == prepared.task_id)
        ).mappings().one()
        assets = connection.execute(select(fused_assets)).mappings().all()
        wallet = connection.execute(select(user_credits.c.credits)).scalar_one()
        logs = connection.execute(select(CreditLogRow.__table__)).mappings().all()
    assert runtime.calls == 0
    assert task["user_id"] == prepared.user_id
    assert task["status"] == "processing"
    assert task["claim_token"] is None
    assert task["recovery_status"] == "ready"
    assert (
        json.loads(task["request_data"])["server_controlled_execution"]
        == FUSED_SERVER_CONTROL_MARKER
    )
    assert assets == []
    assert wallet == 12
    assert logs == []


class RetainCommitAckUnknownSession(Session):
    def execute(self, statement, *args, **kwargs):
        result = super().execute(statement, *args, **kwargs)
        if (
            isinstance(statement, Update)
            and statement.table.name == "ai_tasks"
            and any(
                getattr(key, "key", key) == "recovery_status"
                for key in statement._values
            )
        ):
            self.info["raise_after_retain_commit"] = True
        return result

    def commit(self) -> None:
        super().commit()
        if self.info.pop("raise_after_retain_commit", False):
            raise RuntimeError("retention commit acknowledgement is unknown")


class UnknownRuntime:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, request):
        self.calls += 1
        return FusedGenerationUnknown()


def test_real_sqlite_retain_commit_ack_unknown_is_not_retried_or_finalized(
    fused_database,
):
    engine, _ = fused_database
    prepared = _prepared()
    _stage_task(
        sessionmaker(engine, expire_on_commit=False, future=True),
        prepared,
    )
    ack_unknown_factory = sessionmaker(
        engine,
        class_=RetainCommitAckUnknownSession,
        expire_on_commit=False,
        future=True,
    )
    runtime = UnknownRuntime()
    execution = FusedGenerationExecution(
        runtime,
        fused_generation_uow_factory(ack_unknown_factory),
    )

    asyncio.run(execution.execute(prepared))
    asyncio.run(execution.execute(prepared))

    with engine.connect() as connection:
        task = connection.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == prepared.task_id)
        ).mappings().one()
        assets = connection.execute(select(fused_assets)).mappings().all()
        wallet = connection.execute(select(user_credits.c.credits)).scalar_one()
        logs = connection.execute(select(CreditLogRow.__table__)).mappings().all()
    assert runtime.calls == 1
    assert task["status"] == "processing"
    assert task["claim_token"]
    assert task["recovery_status"] == "provider_result_unknown"
    assert task["result"] == FUSED_UNKNOWN_REASON
    assert task["progress_message"] == FUSED_UNKNOWN_PROGRESS_MESSAGE
    assert assets == []
    assert wallet == 12
    assert logs == []


_CANCEL_INJECTIONS: list[str] = []


class CancelBeforeTerminalSession(Session):
    task_id = ""
    injected = False

    def execute(self, statement, *args, **kwargs):
        if (
            not self.injected
            and isinstance(statement, Update)
            and statement.table.name == "ai_tasks"
            and any(getattr(key, "key", key) == "status" for key in statement._values)
        ):
            self.injected = True
            _CANCEL_INJECTIONS.append(self.task_id)
            super().execute(
                update(AITaskRow.__table__)
                .where(AITaskRow.id == self.task_id)
                .values(user_cancelled_at=NOW)
            )
        return super().execute(statement, *args, **kwargs)


class InlineImageRuntime:
    def __init__(self) -> None:
        self.calls = 0
        self.image = FusedGenerationInlineImage(
            content=b"synthetic inline image bytes",
            media_type="image/png",
        )

    async def generate(self, _request):
        self.calls += 1
        return self.image


class InlineImageStorage:
    def __init__(self) -> None:
        self.calls = 0

    def store_inline_image(self, image):
        self.calls += 1
        return StoredMedia(
            locator="https://media.example.test/fused/owned.png",
            content_digest=hashlib.sha256(image.content).hexdigest(),
            media_type=image.media_type,
            size_bytes=len(image.content),
        )


def test_cancel_committed_between_read_and_terminal_compare_wins(fused_database):
    engine, factory = fused_database
    target = FusedFrameTarget(
        chapter_id="chapter-a",
        frame_index=0,
        identity=json.dumps(
            {"text": "镜头A"},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    prepared = _prepared(target=target)
    _stage_task(factory, prepared)
    uow = SqlAlchemyFusedGenerationUnitOfWork(factory())
    claim = uow.claim_task(
        prepared.task_id, "claim-a", expected_user_id=prepared.user_id
    )
    assert claim is not None
    uow.commit()
    uow.close()

    raced_factory = sessionmaker(
        engine,
        class_=CancelBeforeTerminalSession,
        expire_on_commit=False,
        future=True,
    )
    session = raced_factory()
    session.task_id = prepared.task_id
    raced = SqlAlchemyFusedGenerationUnitOfWork(session)
    raced.finish_task(prepared, "claim-a", image_url="https://provider.test/result.png", error_message=None)
    raced.commit()
    raced.close()

    with engine.connect() as connection:
        task = connection.execute(select(AITaskRow.__table__)).mappings().one()
        asset = connection.execute(select(fused_assets)).mappings().one()
        chapter_content = connection.execute(select(chapters.c.content)).scalar_one()
    assert session.injected
    assert task["status"] == "failed"
    assert task["result"] == "任务被取消"
    assert asset["status"] == "failed"
    assert asset["image_url"] is None
    assert asset["error_message"] == "任务被取消"
    assert json.loads(chapter_content)[0]["fused"] == [asset["id"]]


def test_inline_image_terminal_cancel_race_is_retained_without_refund(fused_database):
    engine, factory = fused_database
    prepared = _prepared()
    _stage_task(factory, prepared)

    CancelBeforeTerminalSession.task_id = prepared.task_id
    CancelBeforeTerminalSession.injected = False
    _CANCEL_INJECTIONS.clear()
    raced_factory = sessionmaker(
        engine,
        class_=CancelBeforeTerminalSession,
        expire_on_commit=False,
        future=True,
    )
    runtime = InlineImageRuntime()
    storage = InlineImageStorage()
    execution = FusedGenerationExecution(
        runtime,
        fused_generation_uow_factory(raced_factory),
        inline_image_storage=storage,
    )
    asyncio.run(execution.execute(prepared))
    asyncio.run(execution.execute(prepared))

    with engine.connect() as connection:
        task = connection.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == prepared.task_id)
        ).mappings().one()
        assets = connection.execute(select(fused_assets)).mappings().all()
        wallet = connection.execute(select(user_credits.c.credits)).scalar_one()
        logs = connection.execute(select(CreditLogRow.__table__)).mappings().all()
    assert runtime.calls == storage.calls == 1
    assert _CANCEL_INJECTIONS == [prepared.task_id]
    assert task["status"] == "processing"
    assert task["claim_token"]
    assert task["user_cancelled_at"] == NOW
    assert task["recovery_status"] == "provider_result_unknown"
    assert task["result"] == FUSED_MEDIA_STORAGE_UNKNOWN_REASON
    assert task["progress_message"] == FUSED_MEDIA_STORAGE_UNKNOWN_PROGRESS_MESSAGE
    assert assets == []
    assert wallet == 12
    assert logs == []


class ChangeChapterBeforeCompareSession(Session):
    injected = False

    def execute(self, statement, *args, **kwargs):
        if (
            not self.injected
            and isinstance(statement, Update)
            and statement.table.name == "chapters"
        ):
            self.injected = True
            super().execute(
                update(chapters)
                .where(chapters.c.id == "chapter-a")
                .values(content=json.dumps([{"text": "并发编辑", "fused": []}], ensure_ascii=False))
            )
        return super().execute(statement, *args, **kwargs)


def test_frame_content_compare_preserves_concurrent_chapter_edit_and_keeps_asset_recoverable(
    fused_database,
):
    engine, factory = fused_database
    target = FusedFrameTarget(
        chapter_id="chapter-a",
        frame_index=0,
        identity=json.dumps({"text": "镜头A"}, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )
    prepared = _prepared(target=target)
    _stage_task(factory, prepared)
    uow = SqlAlchemyFusedGenerationUnitOfWork(factory())
    claim = uow.claim_task(
        prepared.task_id, "claim-a", expected_user_id=prepared.user_id
    )
    assert claim is not None
    uow.commit()
    uow.close()

    raced_factory = sessionmaker(
        engine,
        class_=ChangeChapterBeforeCompareSession,
        expire_on_commit=False,
        future=True,
    )
    session = raced_factory()
    raced = SqlAlchemyFusedGenerationUnitOfWork(session)
    raced.finish_task(prepared, "claim-a", image_url="https://provider.test/result.png", error_message=None)
    raced.commit()
    raced.close()

    with engine.connect() as connection:
        current_chapter = connection.execute(select(chapters.c.content)).scalar_one()
        message = connection.execute(select(chat_messages.c.content)).scalar_one()
        asset = connection.execute(select(fused_assets)).mappings().one()
    assert session.injected
    assert json.loads(current_chapter) == [{"text": "并发编辑", "fused": []}]
    assert asset["status"] == "completed"
    assert asset["image_url"] == "https://provider.test/result.png"
    assert "目标分镜已变化" in message
