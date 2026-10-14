from __future__ import annotations

import json
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
from haoai_backend.fused_generation.domain import FusedFrameTarget, FusedServerImageModel, PreparedFusedGeneration
from haoai_backend.fused_generation.errors import (
    FusedGenerationPaymentRequired,
    FusedGenerationUnavailable,
)
from haoai_backend.fused_generation.persistence import SqlAlchemyFusedGenerationUnitOfWork
from haoai_backend.generation_catalog.tables import (
    metadata as catalog_metadata,
    model_configs,
    prompt_configs,
    system_models,
    system_prompts,
)
from haoai_backend.generation_core.base_schema import AITaskRow, Base, CreditLogRow
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
    claim = claim_uow.claim_task(prepared.task_id, "claim-a")
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
            super().execute(
                update(AITaskRow.__table__)
                .where(AITaskRow.id == self.task_id)
                .values(user_cancelled_at=NOW)
            )
        return super().execute(statement, *args, **kwargs)


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
    claim = uow.claim_task(prepared.task_id, "claim-a")
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
    claim = uow.claim_task(prepared.task_id, "claim-a")
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
