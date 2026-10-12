from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine, insert, select
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.types import (
    EndpointPolicy,
    FrozenExecutionSnapshot,
    FrozenGrantSnapshot,
    ModelReference,
    TargetSnapshot,
)
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    Base,
    BillingUnitRow,
    CreditLogRow,
    UserCreditRow,
    UserRow,
)
from haoai_backend.generation_core.graph_planning import (
    GraphItemInput,
    build_operation_graph_plan,
)
from haoai_backend.generation_core.unit_fees import (
    InsufficientAdmissionCredits,
    stage_unit_fees,
)


def _digest(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _snapshot(actor_id: str, index: int, quote: int) -> FrozenExecutionSnapshot:
    return FrozenExecutionSnapshot(
        model_ref=ModelReference(owner_id=actor_id, provider="provider-x", model_id="model-x"),
        model_category="image",
        model_name="Display model",
        model_params={"size": "1024x1024"},
        prompt_ref=None,
        effective_prompt="frozen prompt",
        effective_context="",
        server_quote=quote,
        reference_snapshot_ref=f"references-{index}",
        target_snapshot=TargetSnapshot(
            kind="asset_message",
            chapter_id="chapter-1",
            asset_type="character",
            asset_id=f"asset-{index}",
        ),
        credential_binding_ref=f"binding-{index}",
        grant_snapshot=FrozenGrantSnapshot(actor_id=actor_id),
        adapter_config_versions=(("provider-x", "adapter-v1"),),
        endpoint_policy=EndpointPolicy(provider_origin="https://provider.invalid"),
    )


def _database() -> tuple[Session, object]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine), engine


def _seed_graph_rows(session: Session, quotes: tuple[int, ...]) -> object:
    actor_id = "owner-1"
    session.execute(insert(UserRow.__table__), [{"id": actor_id}])
    inputs = []
    for index, quote in enumerate(quotes):
        inputs.append(
            GraphItemInput(
                item_id=f"item-{index}",
                target_id=f"asset-{index}",
                task_id=f"task-{index}",
                unit_id=f"unit-{index}",
                step_ids=(f"step-{index}",),
                request_digest=_digest(f"request-{index}"),
                rule_digest=_digest(f"rule-{index}"),
                message_id=f"message-{index}",
                snapshot=_snapshot(actor_id, index, quote),
            )
        )
    graph = build_operation_graph_plan(
        "image.batch", graph_version="image-batch-v1", items=inputs
    )
    now = datetime(2026, 10, 12, 8, 0, 0)
    session.execute(
        insert(AITaskRow.__table__),
        [
            {
                "id": task.task_id,
                "user_id": actor_id,
                "type": "batch-image",
                "status": "queued",
                "message_id": f"message-{index}",
                "credit_cost": quote,
                "billing_status": "unbilled",
                "created_at": now,
                "updated_at": now,
            }
            for index, (task, quote) in enumerate(zip(graph.tasks, quotes, strict=True))
        ],
    )
    session.execute(
        insert(BillingUnitRow.__table__),
        [
            {
                "id": unit.unit_id,
                "task_id": unit.task_id,
                "item_key": unit.unit_key,
                "operation": "image.batch",
                "step_graph_version": "image-batch-v1",
                "input_digest": graph.items[index].request_digest,
                "quoted_amount": unit.quoted_amount,
                "debited_amount": 0,
                "refunded_amount": 0,
                "billing_status": "unbilled",
                "result_status": "pending",
                "created_at": now,
                "updated_at": now,
            }
            for index, unit in enumerate(graph.units)
        ],
    )
    return graph


def test_positive_batch_uses_one_conditional_debit_and_ordered_owned_ledgers() -> None:
    session, engine = _database()
    try:
        graph = _seed_graph_rows(session, (2, 3))
        session.execute(
            insert(UserCreditRow.__table__),
            [{"id": "wallet-1", "user_id": "owner-1", "credits": 20}],
        )
        ids = iter(("ledger-0", "ledger-1"))

        result = stage_unit_fees(
            session,
            actor_id="owner-1",
            graph=graph,
            id_factory=lambda: next(ids),
            now=datetime(2026, 10, 12, 8, 1, 0),
        )

        wallet_balance = session.execute(
            select(UserCreditRow.__table__.c.credits).where(
                UserCreditRow.__table__.c.user_id == "owner-1"
            )
        ).scalar_one()
        logs = session.execute(
            select(
                CreditLogRow.__table__.c.id,
                CreditLogRow.__table__.c.business_key,
                CreditLogRow.__table__.c.amount,
                CreditLogRow.__table__.c.balance_after,
                CreditLogRow.__table__.c.billing_unit_id,
            ).order_by(CreditLogRow.__table__.c.id)
        ).mappings().all()

        assert result.total_debited == 5
        assert result.wallet_balance_after == 15
        assert wallet_balance == 15
        assert [(unit.balance_after, unit.business_key) for unit in result.units] == [
            (18, "debit:unit-0"),
            (15, "debit:unit-1"),
        ]
        assert [
            (row["id"], row["business_key"], row["amount"], row["balance_after"], row["billing_unit_id"])
            for row in logs
        ] == [
            ("ledger-0", "debit:unit-0", -2, 18, "unit-0"),
            ("ledger-1", "debit:unit-1", -3, 15, "unit-1"),
        ]
    finally:
        session.close()
        engine.dispose()


def test_zero_quote_does_not_create_a_wallet_or_ledger() -> None:
    session, engine = _database()
    try:
        graph = _seed_graph_rows(session, (0,))

        result = stage_unit_fees(
            session,
            actor_id="owner-1",
            graph=graph,
            id_factory=lambda: pytest.fail("zero quote must not allocate ledger IDs"),
            now=datetime(2026, 10, 12, 8, 1, 0),
        )

        wallet_count = session.execute(select(UserCreditRow.__table__.c.id)).all()
        ledger_count = session.execute(select(CreditLogRow.__table__.c.id)).all()
        assert result.total_debited == 0
        assert result.wallet_balance_after is None
        assert result.units[0].billing_status == "no_charge"
        assert result.units[0].business_key is None
        assert wallet_count == []
        assert ledger_count == []
    finally:
        session.close()
        engine.dispose()


def test_insufficient_balance_has_no_wallet_or_ledger_effect() -> None:
    session, engine = _database()
    try:
        graph = _seed_graph_rows(session, (2, 3))
        session.execute(
            insert(UserCreditRow.__table__),
            [{"id": "wallet-1", "user_id": "owner-1", "credits": 4}],
        )
        ids = iter(("ledger-0", "ledger-1"))

        with pytest.raises(InsufficientAdmissionCredits):
            stage_unit_fees(
                session,
                actor_id="owner-1",
                graph=graph,
                id_factory=lambda: next(ids),
                now=datetime(2026, 10, 12, 8, 1, 0),
            )

        assert session.execute(
            select(UserCreditRow.__table__.c.credits).where(
                UserCreditRow.__table__.c.user_id == "owner-1"
            )
        ).scalar_one() == 4
        assert session.execute(select(CreditLogRow.__table__.c.id)).all() == []
    finally:
        session.close()
        engine.dispose()
