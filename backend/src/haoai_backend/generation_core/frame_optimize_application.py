"""Apply authenticated frame.optimize results through the original delivery lease."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.records import GenerationResultReceipt
from haoai_backend.generation_contracts.types import (
    ApplicationOutcome,
    ClaimAuthority,
    ContinuationClaimAuthority,
)
from haoai_backend.generation_core.base_schema import AITaskRow
from haoai_backend.generation_core.frame_optimize_application_facts import (
    parse_partial_results,
    reconstruct_expected_chapter,
)
from haoai_backend.generation_core.frame_optimize_continuation_claims import (
    SqlAlchemyFrameOptimizeContinuationClaims,
)
from haoai_backend.generation_core.frame_optimize_progress import (
    decode_optimize_text,
    lock_frame_optimize_progress,
)
from haoai_backend.generation_core.frame_optimize_execution import lock_frame_optimize_batch
from haoai_backend.generation_core.persistence import GenerationRecordRepository
from haoai_backend.generation_core.schema import LocalContinuationWorkRow
from haoai_backend.generation_credentials.domain import CredentialCrypto, HmacProofAuthority
from haoai_backend.task_observation.generation import _database_now

SessionFactory = Callable[[], Session]
DbClock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class _ApplicationWrite:
    owner_id: str
    task_id: str
    unit_id: str
    work_id: str
    result_ref: str
    frame_index: int
    optimized_text: str
    application_ref: str | None
    terminal_reason: str | None
    expected_result_prefix: tuple[tuple[int, str], ...]


@dataclass(slots=True)
class SqlAlchemyFrameOptimizeApplications:
    """Consume one already-fee-resolved result inside a caller-owned SQL transaction."""

    session_factory: SessionFactory
    db_clock: DbClock
    crypto: CredentialCrypto
    proof_authority: HmacProofAuthority
    continuation_claims: SqlAlchemyFrameOptimizeContinuationClaims

    def __post_init__(self) -> None:
        if not callable(self.session_factory) or not callable(self.db_clock):
            raise TypeError("application session and database clock must be explicit")
        if type(self.crypto) is not CredentialCrypto or type(self.proof_authority) is not HmacProofAuthority:
            raise TypeError("registered frame.optimize verification capabilities are required")
        if not isinstance(self.continuation_claims, SqlAlchemyFrameOptimizeContinuationClaims):
            raise TypeError("frame.optimize continuation claims are required")

    def deliver_current(self, generation_claim: ClaimAuthority, work_id: str) -> ApplicationOutcome:
        """Bind and consume a paid result without exposing a broad delivery authority."""
        if not isinstance(generation_claim, ClaimAuthority):
            return _outcome("write_failed", "", detail="generation claim is invalid")
        staged: list[_ApplicationWrite] = []
        result = _outcome("write_failed", "", detail="delivery lease or result phase is not current")
        confirm_existing = False
        try:
            with self.session_factory() as session:
                with session.begin():
                    claim = self.continuation_claims.bind_current_delivery(
                        session, generation_claim, work_id
                    )
                    if claim is None:
                        confirm_existing = True
                    else:
                        result = self._consume_in_session(session, claim, staged)
        except Exception:
            if staged:
                return self._confirm_write(staged[0]) or _outcome(
                    "write_failed", staged[0].result_ref, detail="application commit could not be confirmed"
                )
            return result
        if confirm_existing:
            return self._confirm_existing(
                generation_claim.scope.user_id,
                generation_claim.scope.task_id,
                work_id,
            ) or result
        if staged:
            confirmed = self._confirm_write(staged[0])
            return confirmed or result
        return result

    def consume_delivery(self, claim: ContinuationClaimAuthority) -> ApplicationOutcome:
        """Consume an independently leased delivery continuation."""
        staged: list[_ApplicationWrite] = []
        result = _outcome(
            "write_failed",
            getattr(claim, "result_ref", ""),
            detail="delivery lease or result phase is not current",
        )
        if not isinstance(claim, ContinuationClaimAuthority):
            return result
        try:
            with self.session_factory() as session:
                with session.begin():
                    result = self._consume_in_session(session, claim, staged)
        except Exception:
            if staged:
                return self._confirm_write(staged[0]) or _outcome(
                    "write_failed", staged[0].result_ref, detail="application commit could not be confirmed"
                )
            return result
        if staged:
            return self._confirm_write(staged[0]) or result
        return result

    def _consume_in_session(
        self,
        session: Session,
        claim: ContinuationClaimAuthority,
        staged: list[_ApplicationWrite],
    ) -> ApplicationOutcome:
        now = self._now()
        locked = self.continuation_claims.lock_current_delivery_claim(session, claim, now)
        if locked is None:
            return _outcome(
                "write_failed", claim.result_ref, detail="delivery lease or result phase is not current"
            )
        if locked.work.get("phase") == "finalize_pending":
            return _outcome(
                "already_applied",
                claim.result_ref,
                application_ref=locked.work.get("application_ref"),
            )
        if (
            locked.work.get("phase") != "delivery_pending"
            or not locked.receipt.get("automatic_application_allowed")
        ):
            return _outcome("write_failed", claim.result_ref, detail="result is not eligible for delivery")
        item = locked.graph.items_by_unit.get(claim.unit_id)
        receipt = GenerationRecordRepository(session).find_result_receipt(
            claim.owner_scope.user_id, claim.owner_scope.task_id, claim.result_ref
        )
        output = decode_optimize_text(
            locked.receipt.get("recoverable_locator"), locked.receipt.get("content_digest")
        )
        if item is None or receipt is None or output is None or receipt.operation != "frame.optimize":
            return _outcome("write_failed", claim.result_ref, detail="authenticated result is unavailable")

        applied_outputs = {
            fact.item.frame_index: fact.optimized_text
            for fact in locked.progress.applied_by_unit.values()
        }
        expected_chapter = reconstruct_expected_chapter(locked.graph.verified, applied_outputs)
        if expected_chapter is None:
            return _outcome(
                "write_failed", claim.result_ref, detail="authenticated chapter baseline is unavailable"
            )

        from haoai_backend.series_data.generation import stage_frame_optimize_source

        source_outcome = stage_frame_optimize_source(
            session,
            receipt,
            item.snapshot.target_snapshot,
            item.requested_text,
            expected_chapter,
            output,
        )
        if source_outcome.kind in {"target_conflict", "target_deleted", "stale"}:
            reason = (
                "frame_optimize_target_deleted"
                if source_outcome.kind == "target_deleted"
                else "frame_optimize_target_conflict"
            )
            if not self.continuation_claims.confirm_live_delivery_fence(session, claim):
                raise _ApplicationFenceLost
            self._stop_delivery(session, locked, claim, reason)
            staged.append(
                _ApplicationWrite(
                    claim.owner_scope.user_id,
                    claim.owner_scope.task_id,
                    claim.unit_id,
                    claim.work_id,
                    claim.result_ref,
                    item.frame_index,
                    output,
                    None,
                    reason,
                    locked.progress.partial_results,
                )
            )
            return _outcome("target_conflict", claim.result_ref, detail=source_outcome.detail)
        if source_outcome.kind != "applied" or not source_outcome.application_ref:
            return source_outcome

        if not self.continuation_claims.confirm_live_delivery_fence(session, claim):
            raise _ApplicationFenceLost
        from haoai_backend.series_data.generation import _mark_worker_result_applied

        _mark_worker_result_applied(session, receipt)
        work_record = GenerationRecordRepository(session).find_continuation_work(
            claim.owner_scope.user_id, claim.owner_scope.task_id, claim.work_id
        )
        if work_record is None:
            raise _ApplicationFenceLost
        advanced = replace(
            work_record,
            phase="finalize_pending",
            phase_version=2,
            application_ref=source_outcome.application_ref,
            terminal_reason=None,
            last_execution_generation=claim.execution_generation,
        )
        records = GenerationRecordRepository(session)
        if records.advance_continuation_work(
            advanced, expected_phase_version=1
        ).status != "updated":
            raise _ApplicationFenceLost

        result_entries = list(locked.progress.partial_results)
        result_entries.append((item.frame_index, output))
        expected_results = tuple(result_entries)
        partial_result = json.dumps(
            [{"index": index, "optimized_text": text} for index, text in result_entries],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        self._append_task_result(session, locked.graph.task, claim, partial_result)
        staged.append(
            _ApplicationWrite(
                claim.owner_scope.user_id,
                claim.owner_scope.task_id,
                claim.unit_id,
                claim.work_id,
                claim.result_ref,
                item.frame_index,
                output,
                source_outcome.application_ref,
                None,
                expected_results,
            )
        )
        return source_outcome

    def _append_task_result(self, session, task, claim, value: str) -> None:
        table = AITaskRow.__table__
        old_result = task.get("result")
        result_match = table.c.result.is_(None) if old_result is None else table.c.result == old_result
        now = self._now()
        if now is None or not self.continuation_claims.confirm_live_delivery_fence(session, claim):
            raise _ApplicationFenceLost
        changed = session.execute(
            update(table)
            .where(
                table.c.id == claim.owner_scope.task_id,
                table.c.user_id == claim.owner_scope.user_id,
                table.c.type == "batch-optimize",
                table.c.status == "processing",
                table.c.claimed_by == claim.worker_instance,
                table.c.claim_token == claim.token,
                table.c.execution_generation == claim.execution_generation,
                table.c.lease_until == _db_time(claim.lease_until),
                table.c.lease_until > _db_time(now),
                table.c.user_cancelled_at.is_(None),
                result_match,
            )
            .values(result=value, updated_at=_db_time(now))
        )
        if changed.rowcount != 1:
            raise _ApplicationFenceLost

    def _stop_delivery(self, session, locked, claim, reason: str) -> None:
        changed = session.execute(
            update(LocalContinuationWorkRow.__table__)
            .where(
                LocalContinuationWorkRow.work_id == claim.work_id,
                LocalContinuationWorkRow.owner_id == claim.owner_scope.user_id,
                LocalContinuationWorkRow.task_id == claim.owner_scope.task_id,
                LocalContinuationWorkRow.unit_id == claim.unit_id,
                LocalContinuationWorkRow.result_ref == claim.result_ref,
                LocalContinuationWorkRow.phase == "delivery_pending",
                LocalContinuationWorkRow.phase_version == 1,
                LocalContinuationWorkRow.fee_decision_ref == claim.unit_id,
                LocalContinuationWorkRow.application_ref.is_(None),
            )
            .values(
                phase="stopped_delivery_failed",
                phase_version=2,
                terminal_reason=reason,
                last_execution_generation=claim.execution_generation,
            )
        )
        if changed.rowcount != 1:
            raise _ApplicationFenceLost

    def _confirm_write(self, expected: _ApplicationWrite) -> ApplicationOutcome | None:
        try:
            with self.session_factory() as session:
                with session.begin():
                    graph = lock_frame_optimize_batch(
                        session,
                        expected.owner_id,
                        expected.task_id,
                        crypto=self.crypto,
                        proof_authority=self.proof_authority,
                    )
                    progress = lock_frame_optimize_progress(session, graph) if graph else None
                    if progress is None:
                        return None
                    if (
                        len(progress.partial_results) < len(expected.expected_result_prefix)
                        or progress.partial_results[: len(expected.expected_result_prefix)]
                        != expected.expected_result_prefix
                    ):
                        return None
                    work = progress.works_by_unit.get(expected.unit_id)
                    if work is None or work.get("work_id") != expected.work_id:
                        return None
                    if expected.terminal_reason is not None:
                        if (
                            work.get("phase") != "stopped_delivery_failed"
                            or work.get("phase_version") != 2
                            or work.get("terminal_reason") != expected.terminal_reason
                            or expected.unit_id in progress.applications_by_unit
                        ):
                            return None
                        return _outcome(
                            "target_conflict", expected.result_ref, detail=expected.terminal_reason
                        )
                    fact = progress.applied_by_unit.get(expected.unit_id)
                    if (
                        fact is None
                        or fact.application.get("application_ref") != expected.application_ref
                        or fact.optimized_text != expected.optimized_text
                        or fact.item.frame_index != expected.frame_index
                        or work.get("phase") != "finalize_pending"
                        or work.get("phase_version") != 2
                    ):
                        return None
                    return _outcome(
                        "applied", expected.result_ref, application_ref=expected.application_ref
                    )
        except Exception:
            return None

    def _confirm_existing(self, owner_id: str, task_id: str, work_id: str) -> ApplicationOutcome | None:
        try:
            with self.session_factory() as session:
                with session.begin():
                    graph = lock_frame_optimize_batch(
                        session,
                        owner_id,
                        task_id,
                        crypto=self.crypto,
                        proof_authority=self.proof_authority,
                    )
                    progress = lock_frame_optimize_progress(session, graph) if graph else None
                    if progress is None:
                        return None
                    found = next(
                        (
                            (unit_id, work)
                            for unit_id, work in progress.works_by_unit.items()
                            if work.get("work_id") == work_id
                        ),
                        None,
                    )
                    if found is None:
                        return None
                    unit_id, work = found
                    fact = progress.applied_by_unit.get(unit_id)
                    if (
                        fact is None
                        or work.get("phase") != "finalize_pending"
                        or work.get("phase_version") != 2
                    ):
                        return None
                    return _outcome(
                        "already_applied",
                        str(fact.application.get("result_ref")),
                        application_ref=str(fact.application.get("application_ref")),
                    )
        except Exception:
            return None

    def _now(self) -> datetime | None:
        try:
            value = self.db_clock()
        except Exception:
            return None
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            return None
        return value.astimezone(timezone.utc)


class _ApplicationFenceLost(RuntimeError):
    pass


def _outcome(kind: str, result_ref: str, *, application_ref=None, detail=None) -> ApplicationOutcome:
    return ApplicationOutcome(kind, result_ref, application_ref, None, detail)


def _db_time(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)


__all__ = ["SqlAlchemyFrameOptimizeApplications"]
