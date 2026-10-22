"""Shared owned-SQL fixtures for the frame.optimize execution adapters."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.generation_contracts.types import ClaimAuthority
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    ExecutionStepRow,
    ExternalSubmissionRow,
    ResultEvidenceRow,
    TaskSubmissionRow,
    UserCreditRow,
)
from haoai_backend.generation_core.frame_optimize_admission import admit_frame_optimize
from haoai_backend.generation_core.frame_optimize_claims import SqlAlchemyFrameOptimizeClaims
from haoai_backend.generation_core.frame_optimize_execution import lock_frame_optimize_batch
from haoai_backend.generation_core.frame_optimize_intents import SqlAlchemyFrameOptimizeIntents
from haoai_backend.generation_core.frame_optimize_results import SqlAlchemyFrameOptimizeResults
from haoai_backend.generation_core.frame_optimize_continuation_claims import (
    SqlAlchemyFrameOptimizeContinuationClaims,
)
from haoai_backend.generation_core.frame_optimize_fees import SqlAlchemyFrameOptimizeFees
from haoai_backend.generation_core.frame_optimize_finalization import (
    SqlAlchemyFrameOptimizeFinalization,
)
from haoai_backend.generation_core.frame_optimize_application import (
    SqlAlchemyFrameOptimizeApplications,
)
from haoai_backend.generation_contracts.types import TextResult
from haoai_backend.personal_production.notes.tables import metadata as notes_metadata
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
from haoai_backend.series_data.tables import chapters, storyboard_assets
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
    intent_adapter_count: int = 0
    result_adapter_count: int = 0
    fee_adapter_count: int = 0

    @property
    def actor_id(self) -> str:
        return _G2_JOINT_ACTOR_ID


def admit_batch(
    tmp_path: Path,
    *,
    frame_count: int | None = None,
    request_indexes: tuple[int, ...] = (2, 0, 1),
    model_source: ModelSource = ModelSource.SYSTEM,
    prompt_source: ModelSource | None = ModelSource.SYSTEM,
    prompt_owner_id: str | None = None,
    wallet_credits: int | None = 500,
    include_source_tables: bool = False,
    no_storyboard_ids: bool = False,
    original_text_by_index: dict[int, object] | None = None,
) -> FrameOptimizeCase:
    engine, sessions, capabilities, crypto_state, ids, request = _build_optimize_case(
        tmp_path,
        frame_count=frame_count or len(request_indexes),
        request_indexes=request_indexes,
        model_source=model_source,
        prompt_source=prompt_source,
        prompt_owner_id=prompt_owner_id,
        wallet_credits=wallet_credits,
    )
    if include_source_tables:
        # The admission fixture owns generation tables; source application also
        # needs the real notes/media metadata used by the same-session writer.
        notes_metadata.create_all(engine)
        with engine.begin() as connection:
            asset_columns = {
                row["name"]
                for row in connection.execute(text("PRAGMA table_info(storyboard_assets)")).mappings()
            }
            if "created_at" not in asset_columns:
                connection.execute(text("ALTER TABLE storyboard_assets ADD COLUMN created_at DATETIME"))
    if no_storyboard_ids or original_text_by_index:
        with engine.begin() as connection:
            chapter_row = connection.execute(
                select(chapters.c.content).where(chapters.c.id == "joint-chapter-1")
            ).scalar_one()
            frames = json.loads(chapter_row)
            for index, value in (original_text_by_index or {}).items():
                frames[index]["original_text"] = value
            if no_storyboard_ids:
                for frame in frames:
                    frame.pop("storyboard", None)
                connection.execute(
                    storyboard_assets.delete().where(storyboard_assets.c.chapter_id == "joint-chapter-1")
                )
            connection.execute(
                update(chapters)
                .where(chapters.c.id == "joint-chapter-1")
                .values(content=json.dumps(frames, ensure_ascii=False))
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
    sequence = case.intent_adapter_count
    case.intent_adapter_count += 1
    return SqlAlchemyFrameOptimizeIntents(
        session_factory=case.sessions,
        db_clock=lambda: case.clock[0],
        crypto=case.capabilities.crypto,
        proof_authority=case.capabilities.proof_authority,
        id_factory=IdSequence(f"{prefix}:{sequence}"),
    )


def result_adapter(case: FrameOptimizeCase, *, prefix: str = "opt-result"):
    sequence = case.result_adapter_count
    case.result_adapter_count += 1
    return SqlAlchemyFrameOptimizeResults(
        session_factory=case.sessions,
        db_clock=lambda: case.clock[0],
        crypto=case.capabilities.crypto,
        proof_authority=case.capabilities.proof_authority,
        id_factory=IdSequence(f"{prefix}:{sequence}"),
    )


def continuation_claim_adapter(
    case: FrameOptimizeCase,
    *,
    token: str = "opt-continuation-token",
    lease_seconds: int = 60,
):
    return SqlAlchemyFrameOptimizeContinuationClaims(
        session_factory=case.sessions,
        db_clock=lambda: case.clock[0],
        crypto=case.capabilities.crypto,
        proof_authority=case.capabilities.proof_authority,
        id_factory=lambda: token,
        lease_seconds=lease_seconds,
    )


def fee_adapter(case: FrameOptimizeCase, *, prefix: str = "opt-fee"):
    sequence = case.fee_adapter_count
    case.fee_adapter_count += 1
    prefix = f"{prefix}:{sequence}"
    claims = continuation_claim_adapter(case, token=f"{prefix}-claim")
    return SqlAlchemyFrameOptimizeFees(
        session_factory=case.sessions,
        db_clock=lambda: case.clock[0],
        crypto=case.capabilities.crypto,
        proof_authority=case.capabilities.proof_authority,
        continuation_claims=claims,
        id_factory=IdSequence(prefix),
    )


def application_adapter(
    case: FrameOptimizeCase,
    *,
    token: str = "opt-application-claim",
    continuation_claims=None,
):
    claims = continuation_claims or continuation_claim_adapter(case, token=token)
    return SqlAlchemyFrameOptimizeApplications(
        session_factory=case.sessions,
        db_clock=lambda: case.clock[0],
        crypto=case.capabilities.crypto,
        proof_authority=case.capabilities.proof_authority,
        continuation_claims=claims,
    )


def finalization_adapter(case: FrameOptimizeCase, *, continuation_claims=None):
    claims = continuation_claims or continuation_claim_adapter(
        case, token="opt-finalization-claim"
    )
    return SqlAlchemyFrameOptimizeFinalization(
        session_factory=case.sessions,
        db_clock=lambda: case.clock[0],
        crypto=case.capabilities.crypto,
        proof_authority=case.capabilities.proof_authority,
        continuation_claims=claims,
    )


def persist_result(case, claim, prepared, *, text: str = "optimized frame"):
    assert prepared.disposition == "committed"
    assert prepared.intent is not None
    assert prepared.verified_batch is not None
    assert prepared.verified_item is not None
    return result_adapter(case).persist_success(
        claim,
        prepared.intent,
        prepared.verified_batch,
        prepared.verified_item,
        TextResult(text),
    )


def prepare_item(
    case: FrameOptimizeCase,
    claim: ClaimAuthority,
    order: int = 0,
    *,
    intents=None,
):
    graph = locked_batch(case)
    assert graph is not None
    assert 0 <= order < len(graph.verified.items)
    item = graph.verified.items[order]
    prepared = (intents or intent_adapter(case)).prepare(claim, item.unit_id)
    return item, prepared


def persist_result_and_fee(case, claim, prepared, fees, *, text: str = "optimized frame"):
    receipt = persist_result(case, claim, prepared, text=text)
    assert not isinstance(receipt, str)
    state = rows_for(case)
    work = next(row for row in state["work"] if row.unit_id == prepared.verified_item.unit_id)
    fee = fees.resolve_current_fee(claim, work.work_id)
    return receipt, fee


def apply_current(case: FrameOptimizeCase, claim: ClaimAuthority, unit_id: str, *, adapter=None):
    work = next(row for row in rows_for(case)["work"] if row.unit_id == unit_id)
    return (adapter or application_adapter(case)).deliver_current(claim, work.work_id)


def finalize_current(
    case: FrameOptimizeCase, claim: ClaimAuthority, unit_id: str, *, adapter=None
):
    work = next(row for row in rows_for(case)["work"] if row.unit_id == unit_id)
    return (adapter or finalization_adapter(case)).finalize_current(claim, work.work_id)


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
            "evidence": [
                dict(row)
                for row in session.execute(
                    select(ResultEvidenceRow.__table__).where(
                        ResultEvidenceRow.submission_id.in_([row["id"] for row in external])
                    )
                ).mappings().all()
            ],
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
    "application_adapter",
    "apply_current",
    "assert_only_original_graph",
    "claim_adapter",
    "claim_one",
    "continuation_claim_adapter",
    "fee_adapter",
    "finalization_adapter",
    "finalize_current",
    "intent_adapter",
    "locked_batch",
    "prepare_item",
    "persist_result_and_fee",
    "persist_result",
    "result_adapter",
    "reopen_batch",
    "rows_for",
]
