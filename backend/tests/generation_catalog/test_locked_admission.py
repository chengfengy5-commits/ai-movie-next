from __future__ import annotations

import json
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator

import pytest
from sqlalchemy import create_engine, event, insert, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, registry, sessionmaker

from haoai_backend.authentication.tables import metadata as authentication_metadata, user_credits, users
from haoai_backend.generation_catalog.admission import AdmissionPolicy, prepare_admission
from haoai_backend.generation_catalog.admission_types import (
    AdmissionPreparationFacts,
    AdmissionRefusal,
    AdmissionReplay,
    build_admission_context,
)
from haoai_backend.generation_catalog.tables import (
    ASSET_TABLES,
    chapters,
    metadata as catalog_metadata,
    model_configs,
    system_models,
)
from haoai_backend.generation_contracts.types import GenerationActor
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    Base,
    BillingUnitRow,
    TaskSubmissionRow,
)
from haoai_backend.series_access.tables import metadata as series_access_metadata, series, team_members
from haoai_backend.shared.identity import TrustedActor


NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)


@pytest.fixture

def engine() -> Iterator[Engine]:
    database = create_engine("sqlite+pysqlite:///:memory:")
    authentication_metadata.create_all(database)
    series_access_metadata.create_all(database)
    catalog_metadata.create_all(database)
    Base.metadata.create_all(database)
    yield database
    database.dispose()


def actor(user_id: str = "actor") -> GenerationActor:
    return GenerationActor(user_id=user_id, trusted_account_identity=TrustedActor(user_id))


def image_single(model_id: str = "image-model", *, target: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "model_id": model_id,
        "prompt": "A quiet room",
        "target": target or {"kind": "standalone"},
        "size": None,
        "reference_images": [],
    }


def _add_account(
    database: Engine,
    user_id: str = "actor",
    *,
    membership: str = "premium",
    expires_at: datetime | None = None,
    credits: int = 100,
) -> None:
    with database.begin() as connection:
        connection.execute(
            insert(users).values(
                id=user_id,
                username=user_id,
                email=f"{user_id}@example.test",
                hashed_password="not-a-real-password-hash",
                is_superuser=False,
                membership_type=membership,
                membership_expires_at=expires_at,
                created_at=NOW.replace(tzinfo=None),
            )
        )
        connection.execute(
            insert(user_credits).values(
                id=f"wallet-{user_id}",
                user_id=user_id,
                credits=credits,
                created_at=NOW.replace(tzinfo=None),
                updated_at=NOW.replace(tzinfo=None),
            )
        )


def _add_model(
    database: Engine,
    model_id: str,
    category: str,
    *,
    owner_id: str | None = None,
    active: bool = True,
    cost: int = 8,
    api_key: str = "test-secret-credential",
    params: dict[str, object] | None = None,
) -> None:
    values = {
        "id": model_id,
        "category": category,
        "name": f"Public {model_id}",
        "provider": "test-provider",
        "api_key": api_key,
        "model": f"wire-{model_id}",
        "base_url": "https://private.example.test/v1",
        "params": json.dumps(params or {}),
        "created_at": NOW.replace(tzinfo=None),
        "updated_at": NOW.replace(tzinfo=None),
    }
    if owner_id is None:
        values.update(cost_per_call=cost, is_active=active, sort_order=0)
        table = system_models
    else:
        values.update(user_id=owner_id, sort_order=0)
        table = model_configs
    with database.begin() as connection:
        connection.execute(insert(table).values(**values))


def _add_series_chapter(
    database: Engine,
    *,
    owner_id: str = "actor",
    series_id: str = "series-1",
    chapter_id: str = "chapter-1",
    team_id: str | None = None,
    claimed_by: str | None = None,
    content: str = "[]",
) -> None:
    with database.begin() as connection:
        connection.execute(
            insert(series).values(
                id=series_id,
                user_id=owner_id,
                team_id=team_id,
                claimed_by=claimed_by,
            )
        )
        connection.execute(
            insert(chapters).values(
                id=chapter_id,
                series_id=series_id,
                title="Chapter",
                content=content,
                **{"order": 1},
                created_at=NOW.replace(tzinfo=None),
                updated_at=NOW.replace(tzinfo=None),
            )
        )


def _add_asset(
    database: Engine,
    asset_type: str,
    asset_id: str,
    *,
    series_id: str = "series-1",
    chapter_id: str = "chapter-1",
) -> None:
    table = ASSET_TABLES[asset_type]
    values: dict[str, object] = {
        "id": asset_id,
        "series_id": series_id,
        "description": f"Description {asset_id}",
        "image_url": None,
        "updated_at": NOW.replace(tzinfo=None),
    }
    if asset_type == "scene":
        values.update(title=f"Name {asset_id}")
    else:
        values.update(name=f"Name {asset_id}")
    if asset_type == "storyboard":
        values.update(chapter_id=chapter_id, frame_index=0)
    with database.begin() as connection:
        connection.execute(insert(table).values(**values))


def _context(
    operation: str,
    request: dict[str, object],
    *,
    user_id: str = "actor",
    raw_key: str | None = None,
):
    if raw_key is None and operation in {"image.single", "video.single", "image.batch"}:
        raw_key = "test-admission-key"
    return build_admission_context(actor(user_id), operation, request, raw_key=raw_key)


@contextmanager
def _capture_sql(database: Engine) -> Iterator[tuple[list[str], list[str]]]:
    compiled: list[str] = []
    executed: list[str] = []

    def before_execute(connection, clauseelement, multiparams, params, execution_options):
        compiled.append(str(clauseelement.compile(dialect=postgresql.dialect())))

    def before_cursor_execute(connection, cursor, statement, parameters, context, executemany):
        executed.append(statement)

    event.listen(database, "before_execute", before_execute)
    event.listen(database, "before_cursor_execute", before_cursor_execute)
    try:
        yield compiled, executed
    finally:
        event.remove(database, "before_execute", before_execute)
        event.remove(database, "before_cursor_execute", before_cursor_execute)


def _locked_table(sql: str) -> str | None:
    if not re.search(r"\bFOR (?:NO KEY UPDATE|KEY SHARE|UPDATE|SHARE)\b", sql, re.IGNORECASE):
        return None
    match = re.search(r"\bFROM\s+([\w\"]+)", sql, re.IGNORECASE)
    return match.group(1).strip('"') if match else None


def _assert_read_only(statements: list[str]) -> None:
    assert not any(
        re.match(r"\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|DROP|ALTER)\b", sql, re.IGNORECASE)
        for sql in statements
    )


def _add_single_receipt(database: Engine, *, user_id: str, operation: str, raw_key: str, digest: str) -> None:
    task_id = "receipt-task"
    stamp = NOW.replace(tzinfo=None)
    with database.begin() as connection:
        connection.execute(
            insert(TaskSubmissionRow.__table__).values(
                id="submission-1",
                user_id=user_id,
                operation=operation,
                idempotency_key=raw_key,
                request_digest=digest,
                task_ids=[task_id],
                status="accepted",
                created_at=stamp,
            )
        )
        connection.execute(
            insert(AITaskRow.__table__).values(
                id=task_id,
                user_id=user_id,
                type="image",
                status="completed",
                claimed_by=None,
                lease_until=None,
                execution_generation=0,
                claim_token=None,
                recovery_status="ready",
                billing_status="charged",
                user_cancelled_at=None,
                cancellation_reason=None,
                message_id="message-1",
                credit_cost=8,
                result=None,
                request_data=None,
                model_name="Public image-model",
                progress=100,
                progress_message=None,
                external_task_id=None,
                external_provider=None,
                created_at=stamp,
                updated_at=stamp,
            )
        )
        connection.execute(
            insert(BillingUnitRow.__table__).values(
                id="billing-unit-1",
                task_id=task_id,
                item_key="single",
                operation=operation,
                step_graph_version="legacy",
                input_digest=digest,
                quoted_amount=8,
                debited_amount=8,
                refunded_amount=0,
                billing_status="charged",
                result_status="succeeded",
                first_intent_at=None,
                deadline_at=None,
                success_confirmed_at=stamp,
                user_cancelled_at=None,
                cancellation_reason=None,
                quote_id=None,
                quote_unit_key=None,
                candidate_digest=None,
                created_at=stamp,
                updated_at=stamp,
            )
        )


def test_exact_existing_receipt_replays_for_expired_member_before_new_catalog_or_wallet(engine: Engine) -> None:
    _add_account(engine, expires_at=NOW - timedelta(days=1), credits=0)
    context = _context("image.single", image_single(), raw_key=" original key ")
    assert context.fingerprint is not None
    _add_single_receipt(
        engine,
        user_id="actor",
        operation="image.single",
        raw_key=" original key ",
        digest=context.fingerprint.ordered_nonsecret_payload_digest,
    )

    with Session(engine) as session, _capture_sql(engine) as (compiled, executed):
        result = prepare_admission(session, context, now=NOW)

    assert isinstance(result, AdmissionReplay)
    assert result.receipt.task_id == "receipt-task"
    assert result.receipt.status == "completed"
    sql = "\n".join(executed).lower()
    assert "system_models" not in sql
    assert "model_configs" not in sql
    assert "user_credits" not in sql
    _assert_read_only(executed)


def test_same_raw_key_with_changed_request_conflicts_before_receipt_materialization(engine: Engine) -> None:
    _add_account(engine, expires_at=NOW - timedelta(days=1), credits=0)
    original = _context("image.single", image_single(), raw_key=" key ")
    assert original.fingerprint is not None
    _add_single_receipt(
        engine,
        user_id="actor",
        operation="image.single",
        raw_key=" key ",
        digest=original.fingerprint.ordered_nonsecret_payload_digest,
    )
    changed = _context("image.single", image_single() | {"prompt": "changed"}, raw_key=" key ")

    with Session(engine) as session, _capture_sql(engine) as (_, executed):
        result = prepare_admission(session, changed, now=NOW)

    assert isinstance(result, AdmissionRefusal)
    assert (result.status_code, result.code) == (409, "idempotency_conflict")
    assert not any("ai_tasks" in sql.lower() or "billing_units" in sql.lower() for sql in executed)
    _assert_read_only(executed)


def test_six_request_paths_keep_their_distinct_fee_phases_and_never_reserve(engine: Engine) -> None:
    _add_account(engine)
    _add_model(engine, "image-model", "image", cost=7)
    _add_model(engine, "video-model", "video", cost=11)
    _add_model(engine, "text-model", "text", cost=3)
    _add_series_chapter(engine, content="[{}, {}]")
    _add_asset(engine, "character", "character-1")

    cases = [
        (
            "image.single",
            image_single(),
            "single_admission",
            7,
        ),
        (
            "video.single",
            image_single("video-model") | {"duration": 5, "resolution": None},
            "single_admission",
            11,
        ),
        (
            "image.batch",
            {
                "chapter_id": "chapter-1",
                "assets": [
                    {
                        "type": "character",
                        "id": "character-1",
                        "name": "character-1",
                        "description": "portrait",
                        "model_id": "image-model",
                        "prompt_id": "",
                        "size": None,
                        "reference_images": [],
                    }
                ],
            },
            "batch_admission",
            7,
        ),
        (
            "frame.review",
            {"chapter_id": "chapter-1", "frame_index": 0, "model_id": "text-model"},
            "post_apply",
            3,
        ),
        (
            "frame.optimize",
            {
                "chapter_id": "chapter-1",
                "model_id": "text-model",
                "frames": [{"index": 0, "text": "first"}, {"index": 1, "text": "second"}],
                "concurrency": 3,
            },
            "pre_apply",
            3,
        ),
        (
            "text.stream",
            {"model_id": "text-model", "messages": [{"role": "user", "content": "hello"}]},
            "stream_pre_task",
            3,
        ),
    ]

    for operation, request, phase, required in cases:
        with Session(engine) as session, _capture_sql(engine) as (_, executed):
            result = prepare_admission(session, _context(operation, request), now=NOW)
        assert isinstance(result, AdmissionPreparationFacts), (operation, result)
        assert result.phase == phase
        assert result.required_balance == required
        assert result.reserved_amount == 0
        assert result.credential_binding_status == "binding_unverified"
        assert result.executable is False
        assert result.quoted_total == (6 if operation == "frame.optimize" else required)
        assert len(result.items) == (2 if operation == "frame.optimize" else 1)
        _assert_read_only(executed)


def test_batch_returns_request_order_but_locks_assets_in_stable_type_id_order(engine: Engine) -> None:
    _add_account(engine)
    _add_model(engine, "image-model", "image", cost=5, api_key="do-not-return-this")
    _add_series_chapter(engine, owner_id="series-owner", team_id="team-1")
    with engine.begin() as connection:
        connection.execute(
            insert(team_members).values(
                id="member-1",
                team_id="team-1",
                user_id="actor",
                role="editor",
                permissions="[]",
            )
        )
    _add_asset(engine, "scene", "scene-1")
    _add_asset(engine, "character", "character-1")
    request = {
        "chapter_id": "chapter-1",
        "assets": [
            {"type": "scene", "id": "scene-1", "name": "Scene", "description": "scene prompt", "model_id": "image-model", "prompt_id": "", "size": None, "reference_images": []},
            {"type": "character", "id": "character-1", "name": "Person", "description": "person prompt", "model_id": "image-model", "prompt_id": "", "size": None, "reference_images": []},
        ],
    }

    with Session(engine) as session, _capture_sql(engine) as (compiled, executed):
        result = prepare_admission(session, _context("image.batch", request, raw_key="batch-key"), now=NOW)

    assert isinstance(result, AdmissionPreparationFacts)
    assert [item.index for item in result.items] == [0, 1]
    assert [item.target.asset_id for item in result.items] == ["scene-1", "character-1"]
    assert result.quoted_total == 10
    assert result.required_balance == 10
    assert "do-not-return-this" not in repr(result)
    assert "private.example.test" not in repr(result)
    _assert_read_only(executed)

    locked_tables = [table for statement in compiled if (table := _locked_table(statement))]
    assert locked_tables == [
        "users",
        "task_submissions",
        "series",
        "team_members",
        "chapters",
        "characters",
        "scenes",
        "system_models",
        "model_configs",
        "user_credits",
    ]
    series_sql = next(statement for statement in compiled if _locked_table(statement) == "series")
    assert "FOR NO KEY UPDATE OF SERIES" in series_sql.upper()


def test_nth_invalid_batch_model_rejects_without_wallet_read_or_partial_result(engine: Engine) -> None:
    _add_account(engine)
    _add_model(engine, "image-model", "image", cost=4)
    _add_series_chapter(engine)
    _add_asset(engine, "character", "character-1")
    _add_asset(engine, "scene", "scene-1")
    request = {
        "chapter_id": "chapter-1",
        "assets": [
            {"type": "character", "id": "character-1", "name": "A", "description": "valid", "model_id": "image-model", "prompt_id": "", "size": None, "reference_images": []},
            {"type": "scene", "id": "scene-1", "name": "B", "description": "invalid later item", "model_id": "missing-model", "prompt_id": "", "size": None, "reference_images": []},
        ],
    }

    with Session(engine) as session, _capture_sql(engine) as (_, executed):
        result = prepare_admission(session, _context("image.batch", request), now=NOW)

    assert isinstance(result, AdmissionRefusal)
    assert result.code == "catalog_selection_failed"
    assert not any("user_credits" in sql.lower() for sql in executed)
    _assert_read_only(executed)


def test_malformed_locked_catalog_returns_safe_refusal_before_wallet_read(engine: Engine) -> None:
    _add_account(engine)
    _add_model(engine, "image-model", "unsupported-category")

    with Session(engine) as session, _capture_sql(engine) as (_, executed):
        result = prepare_admission(session, _context("image.single", image_single()), now=NOW)

    assert isinstance(result, AdmissionRefusal)
    assert (result.status_code, result.code) == (409, "catalog_state_invalid")
    assert not any("user_credits" in sql.lower() for sql in executed)
    _assert_read_only(executed)


def test_malformed_wallet_balance_returns_safe_refusal(engine: Engine) -> None:
    _add_account(engine, credits=-1)
    _add_model(engine, "image-model", "image")

    with Session(engine) as session, _capture_sql(engine) as (_, executed):
        result = prepare_admission(session, _context("image.single", image_single()), now=NOW)

    assert isinstance(result, AdmissionRefusal)
    assert (result.status_code, result.code) == (409, "wallet_state_invalid")
    _assert_read_only(executed)


def test_foreign_claim_without_team_membership_rejects_even_when_actor_owns_series(engine: Engine) -> None:
    _add_account(engine)
    _add_model(engine, "image-model", "image")
    _add_series_chapter(engine, claimed_by="another-user")
    _add_asset(engine, "character", "character-1")
    request = {
        "chapter_id": "chapter-1",
        "assets": [
            {"type": "character", "id": "character-1", "name": "A", "description": "portrait", "model_id": "image-model", "prompt_id": "", "size": None, "reference_images": []}
        ],
    }

    with Session(engine) as session, _capture_sql(engine) as (_, executed):
        result = prepare_admission(session, _context("image.batch", request), now=NOW)

    assert isinstance(result, AdmissionRefusal)
    assert (result.status_code, result.code) == (403, "claimed_series_denied")
    assert not any("system_models" in sql.lower() or "user_credits" in sql.lower() for sql in executed)
    _assert_read_only(executed)


def test_pending_orm_work_is_neither_flushed_nor_lifecycle_managed_by_admission(engine: Engine) -> None:
    _add_account(engine)
    _add_model(engine, "image-model", "image")

    class CountingSession(Session):
        flush_calls = 0
        commit_calls = 0
        rollback_calls = 0

        def flush(self, *args, **kwargs):
            self.flush_calls += 1
            return super().flush(*args, **kwargs)

        def commit(self):
            self.commit_calls += 1
            return super().commit()

        def rollback(self):
            self.rollback_calls += 1
            return super().rollback()

    session = sessionmaker(engine, class_=CountingSession)()
    pending = AITaskRow(
        id="still-pending",
        user_id="actor",
        type="image",
        status="queued",
        message_id="pending-message",
        credit_cost=0,
        progress=0,
        created_at=NOW.replace(tzinfo=None),
        updated_at=NOW.replace(tzinfo=None),
    )
    session.add(pending)
    with _capture_sql(engine) as (_, executed):
        result = prepare_admission(session, _context("image.single", image_single()), now=NOW)
    try:
        assert isinstance(result, AdmissionPreparationFacts)
        assert pending in session.new
        assert (session.flush_calls, session.commit_calls, session.rollback_calls) == (0, 0, 0)
        _assert_read_only(executed)
    finally:
        session.close()


def test_fresh_core_account_read_ignores_stale_orm_membership_state(engine: Engine) -> None:
    _add_account(engine)
    _add_model(engine, "image-model", "image")
    mapped = registry()

    class StaleUser:
        pass

    mapped.map_imperatively(StaleUser, users)
    with Session(engine) as session:
        stale = session.get(StaleUser, "actor")
        assert stale.membership_type == "premium"
        session.execute(
            update(users)
            .where(users.c.id == "actor")
            .values(membership_type="free")
            .execution_options(synchronize_session=False)
        )
        assert stale.membership_type == "premium"
        result = prepare_admission(session, _context("image.single", image_single()), now=NOW)
    mapped.dispose()

    assert isinstance(result, AdmissionRefusal)
    assert (result.status_code, result.code) == (403, "membership_required")


def test_positive_concurrency_policy_checks_account_before_refusing(engine: Engine) -> None:
    with Session(engine) as session, _capture_sql(engine) as (_, executed):
        result = prepare_admission(
            session,
            _context("image.single", image_single()),
            now=NOW,
            policy=AdmissionPolicy(max_concurrent_tasks=1),
        )

    assert isinstance(result, AdmissionRefusal)
    assert (result.status_code, result.code) == (401, "account_unavailable")
    assert any("users" in sql.lower() for sql in executed)
    _assert_read_only(executed)



def _add_batch_receipt(
    database: Engine,
    *,
    user_id: str,
    operation: str,
    raw_key: str,
    digest: str,
    assets: list[dict[str, str]],
) -> None:
    task_ids = [f"batch-task-{index}" for index in range(len(assets))]
    stamp = NOW.replace(tzinfo=None)
    with database.begin() as connection:
        connection.execute(
            insert(TaskSubmissionRow.__table__).values(
                id="batch-submission",
                user_id=user_id,
                operation=operation,
                idempotency_key=raw_key,
                request_digest=digest,
                task_ids=task_ids,
                status="accepted",
                created_at=stamp,
            )
        )
        for index, (task_id, asset) in enumerate(zip(task_ids, assets, strict=True)):
            connection.execute(
                insert(AITaskRow.__table__).values(
                    id=task_id,
                    user_id=user_id,
                    type=asset["type"],
                    status="completed",
                    message_id=f"batch-message-{index}",
                    request_data=json.dumps(
                        {
                            "asset_type": asset["type"],
                            "asset_id": asset["id"],
                            "asset_name": asset["name"],
                        }
                    ),
                    credit_cost=0,
                    progress=100,
                    created_at=stamp,
                    updated_at=stamp,
                )
            )


def test_positive_policy_replays_existing_single_receipt_before_new_state(engine: Engine) -> None:
    _add_account(engine, expires_at=NOW - timedelta(days=1), credits=0)
    context = _context("image.single", image_single(), raw_key="original-single-key")
    assert context.fingerprint is not None
    _add_single_receipt(
        engine,
        user_id="actor",
        operation="image.single",
        raw_key="original-single-key",
        digest=context.fingerprint.ordered_nonsecret_payload_digest,
    )

    with Session(engine) as session, _capture_sql(engine) as (_, executed):
        result = prepare_admission(
            session,
            context,
            now=NOW,
            policy=AdmissionPolicy(max_concurrent_tasks=1),
        )

    assert isinstance(result, AdmissionReplay)
    assert result.receipt.task_id == "receipt-task"
    sql = "\n".join(executed).lower()
    assert "users" in sql and "task_submissions" in sql
    assert not any(name in sql for name in ("series", "chapters", "system_models", "model_configs", "user_credits"))
    _assert_read_only(executed)


def test_positive_policy_replays_existing_ordered_batch_before_new_state(engine: Engine) -> None:
    _add_account(engine, expires_at=NOW - timedelta(days=1), credits=0)
    assets = [
        {"type": "scene", "id": "scene-1", "name": "Scene"},
        {"type": "character", "id": "character-1", "name": "Person"},
    ]
    request_assets = [
        {
            **asset,
            "description": "portrait",
            "model_id": "image-model",
            "prompt_id": "",
            "size": None,
            "reference_images": [],
        }
        for asset in assets
    ]
    context = _context(
        "image.batch",
        {"chapter_id": "chapter-1", "assets": request_assets},
        raw_key="original-batch-key",
    )
    assert context.fingerprint is not None
    _add_batch_receipt(
        engine,
        user_id="actor",
        operation="image.batch",
        raw_key="original-batch-key",
        digest=context.fingerprint.ordered_nonsecret_payload_digest,
        assets=assets,
    )

    with Session(engine) as session, _capture_sql(engine) as (_, executed):
        result = prepare_admission(
            session,
            context,
            now=NOW,
            policy=AdmissionPolicy(max_concurrent_tasks=1),
        )

    assert isinstance(result, AdmissionReplay)
    assert [task.asset_id for task in result.receipt.tasks] == ["scene-1", "character-1"]
    sql = "\n".join(executed).lower()
    assert "users" in sql and "task_submissions" in sql
    assert not any(name in sql for name in ("series", "chapters", "system_models", "model_configs", "user_credits"))
    _assert_read_only(executed)


def test_positive_policy_checks_capacity_before_catalog(engine: Engine) -> None:
    _add_account(engine)
    _add_model(engine, "image-model", "image")
    context = _context("image.single", image_single(), raw_key="new-request-key")

    with Session(engine) as session, _capture_sql(engine) as (_, executed):
        result = prepare_admission(
            session,
            context,
            now=NOW,
            policy=AdmissionPolicy(max_concurrent_tasks=1),
        )

    assert isinstance(result, AdmissionPreparationFacts)
    assert result.operation == "image.single"
    sql = "\n".join(executed).lower()
    assert "users" in sql and "task_submissions" in sql
    assert "ai_tasks" in sql
    assert "system_models" in sql and "user_credits" in sql
    _assert_read_only(executed)


def test_positive_policy_rejects_capacity_using_cross_family_in_flight_rows(
    engine: Engine,
) -> None:
    _add_account(engine)
    with engine.begin() as connection:
        connection.execute(
            insert(AITaskRow.__table__).values(
                id="existing-video-task",
                user_id="actor",
                type="video-single",
                status="reconciling",
                billing_status="legacy_unreconciled",
                message_id="existing-message",
            )
        )
    context = _context("image.single", image_single(), raw_key="over-capacity-key")

    with Session(engine) as session, _capture_sql(engine) as (_, executed):
        result = prepare_admission(
            session,
            context,
            now=NOW,
            policy=AdmissionPolicy(max_concurrent_tasks=1),
        )

    assert isinstance(result, AdmissionRefusal)
    assert (result.status_code, result.code) == (429, "concurrency_limit_reached")
    sql = "\n".join(executed).lower()
    assert "ai_tasks" in sql
    assert not any(name in sql for name in ("system_models", "model_configs", "user_credits"))
    _assert_read_only(executed)


def test_positive_policy_keeps_taskless_operation_unbound(engine: Engine) -> None:
    _add_account(engine)
    context = _context(
        "text.stream",
        {"model_id": "text-model", "messages": [{"role": "user", "content": "hello"}]},
    )

    with Session(engine) as session:
        result = prepare_admission(
            session,
            context,
            now=NOW,
            policy=AdmissionPolicy(max_concurrent_tasks=1),
        )

    assert isinstance(result, AdmissionRefusal)
    assert (result.status_code, result.code) == (503, "concurrency_policy_not_bound")


@pytest.mark.parametrize("limit", [-1, 0])
def test_nonpositive_concurrency_policy_is_disabled(engine: Engine, limit: int) -> None:
    _add_account(engine)
    _add_model(engine, "image-model", "image")

    with Session(engine) as session:
        result = prepare_admission(
            session,
            _context("image.single", image_single()),
            now=NOW,
            policy=AdmissionPolicy(max_concurrent_tasks=limit),
        )

    assert isinstance(result, AdmissionPreparationFacts)
    assert result.required_balance == 8


@pytest.mark.parametrize("size", ["", "   "])
def test_legacy_empty_batch_size_replays_expired_ordered_receipt(engine: Engine, size: str) -> None:
    _add_account(engine, expires_at=NOW - timedelta(days=1), credits=0)
    asset = {
        "type": "character",
        "id": "character-1",
        "name": "Person",
        "description": "portrait",
        "model_id": "image-model",
        "prompt_id": "",
        "size": size,
        "reference_images": [],
    }
    context = _context(
        "image.batch",
        {"chapter_id": "chapter-1", "assets": [asset]},
        raw_key="legacy-empty-size-key",
    )
    assert context.fingerprint is not None
    _add_batch_receipt(
        engine,
        user_id="actor",
        operation="image.batch",
        raw_key="legacy-empty-size-key",
        digest=context.fingerprint.ordered_nonsecret_payload_digest,
        assets=[{"type": "character", "id": "character-1", "name": "Person"}],
    )

    with Session(engine) as session, _capture_sql(engine) as (_, executed):
        result = prepare_admission(session, context, now=NOW)

    assert isinstance(result, AdmissionReplay)
    assert [task.asset_id for task in result.receipt.tasks] == ["character-1"]
    sql = "\n".join(executed).lower()
    assert "users" in sql and "task_submissions" in sql
    assert not any(name in sql for name in ("system_models", "model_configs", "user_credits"))
    _assert_read_only(executed)
