"""A transient busy release must remain retryable through worker quiescence."""

from __future__ import annotations

from threading import Event, Lock

from sqlalchemy import select

from haoai_backend.generation_contracts.ports import GenerationRuntimeConfig
from haoai_backend.generation_core.base_schema import AITaskRow
from haoai_backend.generation_runtime.runtime import _ActiveClaim, _ConfiguredRuntime

from generation_execution.test_frame_review_continuation_transactions import (
    _persist_result,
    _scenario,
)


def _runtime_config(scenario) -> GenerationRuntimeConfig:
    return GenerationRuntimeConfig(
        session_factory=scenario["sessions"],
        db_clock=lambda: scenario["clock"][0],
        id_factory=scenario["ids"],
        http_client_factory=lambda: None,
        credential_resolver=lambda *_args: None,
        provider_factories=(),
        media_store_factory=lambda: None,
        local_muse_staging_dir=None,
        public_media_fetcher_factory=lambda: None,
        bound_provider_content_fetcher_factory=lambda: None,
        local_muse_capability_factory=lambda: None,
        r2_storage_factory=lambda: None,
        shutdown_timeout_seconds=1.0,
    )


def test_runtime_retries_one_busy_continuation_release_after_execution_quiesces(
    tmp_path, monkeypatch
) -> None:
    scenario = _persist_result(_scenario(tmp_path, monkeypatch))
    claims = scenario["continuation_claims"]
    claim = claims.claim_continuation(
        "ai-review", "frame-review-release-busy", lease_seconds=3600
    )
    assert claim is not None
    claims_type = type(claims)
    original_lock = claims_type._lock_claim_graph
    original_release = claims_type.release_continuation
    first_attempt = {"busy": True}
    release_observations: list[tuple[bool, bool, bool]] = []

    def skip_once(self, session, authority, **kwargs):
        if first_attempt["busy"]:
            first_attempt["busy"] = False
            return None
        return original_lock(self, session, authority, **kwargs)

    def observe_release(self, authority):
        released = original_release(self, authority)
        signal = self._signals.get(authority.token)
        release_observations.append(
            (released, signal is not None, signal.is_set() if signal is not None else False)
        )
        return released

    monkeypatch.setattr(claims_type, "_lock_claim_graph", skip_once)
    monkeypatch.setattr(claims_type, "release_continuation", observe_release)
    runtime = _ConfiguredRuntime(_runtime_config(scenario), ())
    active = _ActiveClaim(
        claims,
        claim,
        Event(),
        release_lock=Lock(),
        release_complete=Event(),
    )
    try:
        assert runtime._release(active)
        assert not first_attempt["busy"]
        assert release_observations[:2] == [(False, True, True), (True, False, False)]
        assert claims._signals == {}
        assert claims._release_failures == {}

        with scenario["engine"].connect() as connection:
            released = connection.execute(
                select(AITaskRow.__table__).where(
                    AITaskRow.id == claim.owner_scope.task_id
                )
            ).mappings().one()
        assert released["status"] == "queued"
        assert released["claimed_by"] is None
        assert released["claim_token"] is None
        assert released["lease_until"] is None

        next_claim = claims.claim_continuation(
            "ai-review", "frame-review-release-retry", lease_seconds=3600
        )
        assert next_claim is not None
        assert next_claim.token != claim.token
        assert not claims.release_continuation(claim)
        with scenario["engine"].connect() as connection:
            still_owned = connection.execute(
                select(AITaskRow.__table__).where(
                    AITaskRow.id == next_claim.owner_scope.task_id
                )
            ).mappings().one()
        assert still_owned["claim_token"] == next_claim.token
        assert claims.release_continuation(next_claim)
        assert claims._signals == {}
        assert claims._release_failures == {}
    finally:
        scenario["engine"].dispose()
