"""Shared owned-SQL fixtures for the frame.optimize execution adapters."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.generation_contracts.types import ClaimAuthority
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
    TaskSubmissionRow,
    UserCreditRow,
)
from haoai_backend.generation_core.frame_optimize_admission import admit_frame_optimize
from haoai_backend.generation_core.frame_optimize_claims import SqlAlchemyFrameOptimizeClaims
from haoai_backend.generation_core.frame_optimize_execution import lock_frame_optimize_batch
from haoai_backend.generation_core.frame_optimize_intents import SqlAlchemyFrameOptimizeIntents
from haoai_backend.generation_core.schema import (
    ApplicationRecordRow,
    GenerationResultReceiptRow,
    LocalContinuationWorkRow,
    UnitFeeDecisionRow,
)
from haoai_backend.generation_credentials.frame_optimize_snapshot_reader import (
    PersistedFrameOptimizeExecutionBinding,
    read_original_frame_optimize_execution_batch,
)
from haoai_backend.generation_credentials.persistence import prepared_payloads
from generation_core.test_admission_staging import (
    ModelSource,
    _G2_JOINT_ACTOR_ID,
    _G2_JOINT_NOW,
    _joint_database,
    _joint_registered_capabilities,
)
from generation_execution.test_frame_optimize_admission import (
    _admit,
    _build_optimize_case,
    _reader_binding,
)


class IdSequence:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.values: list[str] = []

    def __call__(self) -> str:
        value = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self.prefix}:{len(self.values) + 1}"))
        self.values.append(value)
        return value


@dataclass(slots=True)
class FrameOptimizeCase:
    engine: Engine
    sessions: sessionmaker[Session]
    capabilities: object
    crypto_state: dict[str, object]
    admission_ids: object
    task_id: str
    request_indexes: tuple[int, ...]
    clock: list[datetime]

    @property
    def actor_id(self) -> str:
        return _G2_JOINT_ACTOR_ID


def admit_batch(
    tmp_path: Path,
    *,
    request_indexes: tuple[int, ...] = (2, 0, 1),
    model_source: ModelSource = ModelSource.SYSTEM,
    prompt_source: ModelSource | None = ModelSource.SYSTEM,
    prompt_owner_id: str | None = None,
    wallet_credits: int | None = 500,
) -> FrameOptimizeCase:
    engine, sessions, capabilities, crypto_state, ids, request = _build_optimize_case(
        tmp_path,
        frame_count=len(request_indexes),
        request_indexes=request_indexes,
        model_source=model_source,
        prompt_source=prompt_source,
        prompt_owner_id=prompt_owner_id,
        wallet_credits=wallet_credits,
    )
    receipt = _admit(engine, sessions, capabilities, ids, request)
    return FrameOptimizeCase(
        engine=engine,
        sessions=sessions,
        capabilities=capabilities,
        crypto_state=crypto_state,
        admission_ids=ids,
        task_id=str(receipt["task_id"]),
        request_indexes=request_indexes,
        clock=[_G2_JOINT_NOW],
    )


def claim_adapter(
    case: FrameOptimizeCase,
    *,
    token: str = "frame-optimize-claim-token",
    lease_seconds: int = 60,
):
    return SqlAlchemyFrameOptimizeClaims(
        session_factory=case.sessions,
        db_clock=lambda: case.clock[0],
        crypto=case.capabilities.crypto,
        proof_authority=case.capabilities.proof_authority,
        id_factory=lambda: token,
        lease_seconds=lease_seconds,
    )


def intent_adapter(case: FrameOptimizeCase, *, prefix: str = "opt-intent"):
    return SqlAlchemyFrameOptimizeIntents(
        session_factory=case.sessions,
        db_clock=lambda: case.clock[0],
        crypto=case.capabilities.crypto,
        proof_authority=case.capabilities.proof_authority,
        id_factory=IdSequence(prefix),
    )


def claim_one(case: FrameOptimizeCase, *, token: str = "frame-optimize-claim-token") -> ClaimAuthority:
    claim = claim_adapter(case, token=token).claim_next("batch-optimize", "optimize-test-worker")
    assert claim is not None
    assert claim.scope.task_id == case.task_id
    assert claim.scope.user_id == case.actor_id
    assert claim.scope.operation == "frame.optimize"
    return claim


def reopen_batch(case: FrameOptimizeCase, session: Session | None = None):
    owns_session = session is None
    active_session = session or case.sessions()
    try:
        task = active_session.get(AITaskRow, case.task_id)
        assert task is not None
        binding = _reader_binding(active_session, task)
        return read_original_frame_optimize_execution_batch(
            active_session,
            binding,
            crypto=case.capabilities.crypto,
            proof_authority=case.capabilities.proof_authority,
        )
    finally:
        if owns_session:
            active_session.close()


def locked_batch(case: FrameOptimizeCase, session: Session | None = None):
    owns_session = session is None
    active_session = session or case.sessions()
    try:
        graph = lock_frame_optimize_batch(
            active_session,
            case.actor_id,
            case.task_id,
            crypto=case.capabilities.crypto,
            proof_authority=case.capabilities.proof_authority,
        )
        if owns_session:
            active_session.rollback()
        return graph
    finally:
        if owns_session:
            active_session.close()


def rows_for(case: FrameOptimizeCase) -> dict[str, object]:
    with case.sessions() as session:
        task = session.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == case.task_id)
        ).mappings().one()
        parent = session.execute(
            select(TaskSubmissionRow.__table__).where(
                TaskSubmissionRow.task_ids == [case.task_id]
            )
        ).mappings().one()
        units = session.execute(
            select(BillingUnitRow.__table__).where(
                BillingUnitRow.task_id == case.task_id
            )
        ).mappings().all()
        steps = session.execute(
            select(ExecutionStepRow.__table__).where(
                ExecutionStepRow.billing_unit_id.in_([row["id"] for row in units])
            )
        ).mappings().all()
        external = session.execute(
            select(ExternalSubmissionRow.__table__).where(
                ExternalSubmissionRow.step_id.in_([row["id"] for row in steps])
            )
        ).mappings().all()
        payloads = session.execute(
            select(prepared_payloads).where(
                prepared_payloads.c.actor_id == case.actor_id,
                prepared_payloads.c.operation == "frame.optimize",
            )
        ).mappings().all()
        wallet = session.execute(
            select(UserCreditRow.__table__).where(UserCreditRow.user_id == case.actor_id)
        ).mappings().all()
        return {
            "task": dict(task),
            "parent": dict(parent),
            "units": [dict(row) for row in units],
            "steps": [dict(row) for row in steps],
            "external": [dict(row) for row in external],
            "payloads": [dict(row) for row in payloads],
            "wallet": [dict(row) for row in wallet],
            "creditLogs": session.execute(select(CreditLogRow)).scalars().all(),
            "fees": session.execute(select(UnitFeeDecisionRow)).scalars().all(),
            "receipts": session.execute(select(GenerationResultReceiptRow)).scalars().all(),
            "applications": session.execute(select(ApplicationRecordRow)).scalars().all(),
            "work": session.execute(select(LocalContinuationWorkRow)).scalars().all(),
        }


def assert_only_original_graph(
    case: FrameOptimizeCase,
    *,
    external_count: int,
    payload_count: int | None = None,
) -> None:
    rows = rows_for(case)
    task = rows["task"]
    assert task["type"] == "batch-optimize"
    assert task["credit_cost"] == 0
    assert task["result"] is None
    assert len(rows["units"]) == len(rows["steps"]) == len(case.request_indexes)
    assert len(rows["payloads"]) == (
        len(case.request_indexes) if payload_count is None else payload_count
    )
    assert len(rows["external"]) == external_count
    assert rows["creditLogs"] == []
    assert rows["fees"] == []
    assert rows["receipts"] == []
    assert rows["applications"] == []
    assert rows["work"] == []
    assert len(rows["wallet"]) == (0 if not rows["wallet"] else 1)
    if rows["wallet"]:
        assert rows["wallet"][0]["credits"] == 500


__all__ = [
    "FrameOptimizeCase",
    "IdSequence",
    "admit_batch",
    "assert_only_original_graph",
    "claim_adapter",
    "claim_one",
    "intent_adapter",
    "locked_batch",
    "reopen_batch",
    "rows_for",
]
