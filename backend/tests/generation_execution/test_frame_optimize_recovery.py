"""Unknown provider results remain observations, never receipts or charges."""

from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy import event
from sqlalchemy.orm import Session

from generation_execution.frame_optimize_execution_helpers import (
    admit_batch,
    claim_one,
    prepare_item,
    recovery_adapter,
    rows_for,
)
from haoai_backend.generation_core.frame_optimize_recovery import (
    SqlAlchemyFrameOptimizeRecovery,
)


def test_unknown_result_is_persisted_as_observation_without_application(tmp_path) -> None:
    case = admit_batch(tmp_path, frame_count=1, request_indexes=(0,))
    try:
        claim = claim_one(case)
        _item, prepared = prepare_item(case, claim)
        assert prepared.intent is not None
        assert prepared.verified_batch is not None
        assert prepared.verified_item is not None

        recovery_adapter(case).record_observation(
            claim,
            prepared.intent,
            prepared.verified_batch,
            prepared.verified_item,
            code="provider_outcome_unknown",
            request_started=True,
            http_status=503,
            observed_text="late but unselected text",
        )

        state = rows_for(case)
        assert len(state["evidence"]) == 1
        observation = state["evidence"][0]
        assert observation["source"] == "worker"
        assert observation["evidence_kind"] == "frame_optimize_observation_v1"
        assert observation["validity"] == "unknown"
        payload = json.loads(observation["result_location"])
        assert payload == {
            "code": "provider_outcome_unknown",
            "http_status": 503,
            "observed_text": "late but unselected text",
            "request_started": True,
        }
        assert state["receipts"] == []
        assert state["fees"] == []
        assert state["applications"] == []
        assert state["work"] == []
    finally:
        case.engine.dispose()


def test_repeated_unknown_observation_keeps_original_evidence_and_projects_live_claim(
    tmp_path,
) -> None:
    case = admit_batch(tmp_path, frame_count=1, request_indexes=(0,))
    try:
        claim = claim_one(case)
        _item, prepared = prepare_item(case, claim)
        assert prepared.intent is not None
        assert prepared.verified_batch is not None
        assert prepared.verified_item is not None
        recovery = recovery_adapter(case)
        observation = {
            "code": "provider_outcome_unknown",
            "request_started": True,
            "http_status": 503,
            "observed_text": "provider response not selected",
        }

        assert recovery.record_observation(
            claim, prepared.intent, prepared.verified_batch, prepared.verified_item, **observation
        ) == "pending"
        before = rows_for(case)
        original_evidence_id = before["evidence"][0]["id"]
        case.clock[0] += timedelta(seconds=1)

        assert recovery.record_observation(
            claim, prepared.intent, prepared.verified_batch, prepared.verified_item, **observation
        ) == "pending"
        after = rows_for(case)

        assert len(after["evidence"]) == 1
        assert after["evidence"][0]["id"] == original_evidence_id
        assert after["task"]["updated_at"] != before["task"]["updated_at"]
        assert after["task"]["status"] == "processing"
        assert after["task"]["claimed_by"] == claim.worker_instance
        assert after["task"]["claim_token"] == claim.token
        assert after["task"]["execution_generation"] == claim.execution_generation
        assert after["receipts"] == []
        assert after["work"] == []
        assert after["fees"] == []
        assert after["applications"] == []
    finally:
        case.engine.dispose()


def test_repeated_observation_confirms_original_evidence_after_commit_ack_loss(
    tmp_path, monkeypatch
) -> None:
    case = admit_batch(tmp_path, frame_count=1, request_indexes=(0,))
    try:
        claim = claim_one(case)
        _item, prepared = prepare_item(case, claim)
        assert prepared.intent is not None
        assert prepared.verified_batch is not None
        assert prepared.verified_item is not None
        recovery = recovery_adapter(case)
        observation = {
            "code": "provider_outcome_unknown",
            "request_started": True,
            "http_status": 503,
            "observed_text": "provider response not selected",
        }
        recovery.record_observation(
            claim, prepared.intent, prepared.verified_batch, prepared.verified_item, **observation
        )
        before = rows_for(case)
        original_evidence_id = before["evidence"][0]["id"]
        case.clock[0] += timedelta(seconds=1)

        confirmed_ids: list[str] = []
        original_confirm = SqlAlchemyFrameOptimizeRecovery._confirm_observation

        def record_confirmation(self, evidence_key, evidence_id, *args):
            if self is recovery:
                confirmed_ids.append(evidence_id)
            return original_confirm(self, evidence_key, evidence_id, *args)

        monkeypatch.setattr(
            SqlAlchemyFrameOptimizeRecovery,
            "_confirm_observation",
            record_confirmation,
        )
        state = {"lose_ack": True}

        def lose_commit_ack(session: Session) -> None:
            if session.bind is case.engine and state["lose_ack"]:
                state["lose_ack"] = False
                raise RuntimeError("synthetic observation commit acknowledgement loss")

        event.listen(Session, "after_commit", lose_commit_ack)
        try:
            recovery.record_observation(
                claim,
                prepared.intent,
                prepared.verified_batch,
                prepared.verified_item,
                **observation,
            )
        finally:
            event.remove(Session, "after_commit", lose_commit_ack)

        after = rows_for(case)
        assert confirmed_ids == [original_evidence_id]
        assert len(after["evidence"]) == 1
        assert after["evidence"][0]["id"] == original_evidence_id
        assert after["task"]["updated_at"] != before["task"]["updated_at"]
        assert after["receipts"] == []
        assert after["work"] == []
        assert after["fees"] == []
        assert after["applications"] == []
    finally:
        case.engine.dispose()
