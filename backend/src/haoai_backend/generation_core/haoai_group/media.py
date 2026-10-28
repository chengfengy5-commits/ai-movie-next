"""Own and verify completed HaoAI Group media before canonical selection."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Callable, Literal, Protocol
from urllib.parse import quote

from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from haoai_backend.generation_contracts.records import GenerationResultReceipt
from haoai_backend.generation_contracts.types import (
    ClaimAuthority,
    CommittedIntent,
    ExternalOperationRef,
    ProviderObservation,
    ReferenceInput,
)
from haoai_backend.generation_core.base_schema import ResultEvidenceRow
from haoai_backend.generation_core.haoai_group.persistence import (
    LockedHaoaiGroupGraph,
    lock_haoai_group_graph,
)
from haoai_backend.generation_core.haoai_group.evidence import (
    verified_group_observation_fact,
)
from haoai_backend.generation_core.haoai_group.polls import (
    AcceptedPollObservation,
    HaoaiGroupPollGrant,
    HaoaiGroupPollOutcome,
    HaoaiGroupPollReplayGrant,
    _poll_record_from_row,
    observation_body_digest,
)
from haoai_backend.generation_core.haoai_group.results import (
    SqlAlchemyHaoaiGroupResults,
    _accepted_completed_observation,
    _binding_matches,
    _claim_live,
    _completed_input_shapes_match,
    _current_group_owner,
    _has_group_work,
    _has_policy_stop_event,
    _intent_matches_group,
    _valid_id,
)
from haoai_backend.generation_core.schema import PollReservationRow
from haoai_backend.generation_runtime.registry import HAOAI_ATTEMPT_GROUP
from haoai_backend.generation_credentials.domain import HmacProofAuthority
from haoai_backend.generation_credentials.haoai_group_execution_snapshot import (
    VerifiedHaoaiGroupExecutionInput,
)
from haoai_backend.media_storage.owned_write_values import (
    OwnedObjectMaterial,
    OwnedObjectPlan,
    OwnedObjectPutResult,
    OwnedObjectVerification,
    OwnedObjectWritePermit,
)
from haoai_backend.media_uploads.domain import MAX_VIDEO_BYTES

SessionFactory = Callable[[], Session]
DbClock = Callable[[], datetime]
IdFactory = Callable[[], str]

_PLAN_SCHEMA = "haoai_group_owned_media_plan_v2"
_PLAN_KIND = "haoai_group_media_plan_v2"
_PLAN_PURPOSE = "haoai-group-owned-media-plan-v2"
_PLAN_KEY = "haoai-group-media-plan-v1:"
_PUT_STARTED_SCHEMA = "haoai_group_owned_media_put_started_v2"
_PUT_STARTED_KIND = "haoai_group_media_put_started_v2"
_PUT_STARTED_PURPOSE = "haoai-group-owned-media-put-started-v2"
_PUT_STARTED_KEY = "haoai-group-media-put-started-v1:"
_VIDEO_CONTENT_TYPES = frozenset(
    {
        "video/mp4",
        "video/webm",
        "video/quicktime",
        "video/x-m4v",
        "video/x-msvideo",
        "video/x-matroska",
        "video/mpeg",
        "video/avi",
    }
)


class HaoaiBoundContentProvider(Protocol):
    def fetch_provider_content(
        self,
        snapshot: object,
        external_ref: ExternalOperationRef,
    ) -> ReferenceInput: ...


class HaoaiGroupOwnedMediaStore(Protocol):
    def prepare_owned_bytes(
        self,
        input: ReferenceInput,
        object_policy: object,
        *,
        owner_ref: str,
        submission_ref: str,
        slot: str,
        provider_result_digest: str,
    ) -> OwnedObjectMaterial | object: ...

    def put_planned_media(
        self,
        material: OwnedObjectMaterial,
        permit: OwnedObjectWritePermit,
    ) -> OwnedObjectPutResult | object: ...

    def verify_planned_media(self, plan: OwnedObjectPlan) -> OwnedObjectVerification: ...


@dataclass(frozen=True, slots=True)
class HaoaiGroupMediaOutcome:
    disposition: Literal["completed", "pending", "ownership_lost"]
    receipt: GenerationResultReceipt | None = None


@dataclass(frozen=True, slots=True)
class _MediaControlState:
    status: Literal["absent", "ready", "conflict"]
    plan: OwnedObjectPlan | None = None
    plan_fact_digest: str | None = None
    put_started: bool = False
    put_authorized_here: bool = False
    original_scope: Mapping[str, object] | None = None


@dataclass(slots=True)
class SqlAlchemyHaoaiGroupMedia:
    """Persist an immutable owned plan before a single PUT and recover by GET only."""

    session_factory: SessionFactory
    db_clock: DbClock
    proof_authority: HmacProofAuthority | None
    result_consumer: SqlAlchemyHaoaiGroupResults
    id_factory: IdFactory = field(default_factory=lambda: _new_uuid_id)

    def materialize(
        self,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified_input: VerifiedHaoaiGroupExecutionInput,
        grant: HaoaiGroupPollGrant,
        poll_outcome: HaoaiGroupPollOutcome,
        provider: HaoaiBoundContentProvider,
        media_store: HaoaiGroupOwnedMediaStore,
    ) -> HaoaiGroupMediaOutcome:
        if not _completed_input_shapes_match(
            claim, intent, verified_input, grant, poll_outcome
        ):
            return HaoaiGroupMediaOutcome("pending")
        if type(self.proof_authority) is not HmacProofAuthority:
            return HaoaiGroupMediaOutcome("pending")

        authorization = self.result_consumer.authorize_media_materialization(
            claim, intent, verified_input, grant, poll_outcome
        )
        if authorization != "ready":
            return HaoaiGroupMediaOutcome(authorization)

        state = self._read_control_state(
            claim, intent, verified_input, grant, poll_outcome
        )
        if state.status == "conflict":
            return HaoaiGroupMediaOutcome("pending")

        material: OwnedObjectMaterial | None = None
        plan = state.plan
        if plan is None or not state.put_started:
            source = self._read_original_content(
                provider, verified_input, grant.external_ref
            )
            if source is None:
                return HaoaiGroupMediaOutcome("pending")

            if plan is None:
                try:
                    prepared = media_store.prepare_owned_bytes(
                        source,
                        {"max_bytes": MAX_VIDEO_BYTES},
                        owner_ref=claim.scope.user_id,
                        submission_ref=intent.external_submission_id,
                        slot="primary",
                        provider_result_digest=poll_outcome.observation.payload_digest,
                    )
                except Exception:
                    return HaoaiGroupMediaOutcome("pending")
                if type(prepared) is not OwnedObjectMaterial:
                    return HaoaiGroupMediaOutcome("pending")
                material = prepared
                persisted = self._persist_plan(
                    claim,
                    intent,
                    verified_input,
                    grant,
                    poll_outcome,
                    prepared.plan,
                )
                if persisted.status != "ready" or persisted.plan is None:
                    return HaoaiGroupMediaOutcome("pending")
                plan = persisted.plan
                state = persisted
            else:
                if not _material_matches_plan(source.content, source.media_type, plan):
                    return HaoaiGroupMediaOutcome("pending")
                try:
                    material = OwnedObjectMaterial(
                        plan=plan,
                        content=source.content,
                    )
                except (AttributeError, TypeError, ValueError):
                    return HaoaiGroupMediaOutcome("pending")

        if plan is None:
            return HaoaiGroupMediaOutcome("pending")

        if not state.put_started:
            if material is None or material.plan != plan:
                return HaoaiGroupMediaOutcome("pending")
            authorization = self.result_consumer.authorize_media_materialization(
                claim, intent, verified_input, grant, poll_outcome
            )
            if authorization != "ready":
                return HaoaiGroupMediaOutcome(authorization)
            started = self._persist_put_started(
                claim,
                intent,
                verified_input,
                grant,
                poll_outcome,
                plan,
                state.plan_fact_digest,
                state.original_scope,
            )
            if started.status != "ready":
                return HaoaiGroupMediaOutcome("pending")
            if not started.put_authorized_here:
                # An existing marker means a previous process may have sent the PUT.
                state = started
            else:
                permit = _OnePlannedWritePermit(
                    plan,
                    lambda: self._authorize_put(
                        claim,
                        intent,
                        verified_input,
                        grant,
                        poll_outcome,
                        plan,
                        state.plan_fact_digest,
                    ),
                )
                try:
                    media_store.put_planned_media(material, permit)
                except Exception:
                    # A persisted start marker forbids another PUT even on an unknown result.
                    pass
                state = started

        try:
            verification = media_store.verify_planned_media(plan)
        except Exception:
            return HaoaiGroupMediaOutcome("pending")
        if (
            type(verification) is not OwnedObjectVerification
            or verification.status != "verified"
            or verification.stored_media is None
        ):
            return HaoaiGroupMediaOutcome("pending")

        outcome = self.result_consumer.complete(
            claim,
            intent,
            verified_input,
            grant,
            poll_outcome,
            plan,
            verification,
        )
        return HaoaiGroupMediaOutcome(outcome.disposition, outcome.receipt)

    def recover_completed_materialization(
        self,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified_input: VerifiedHaoaiGroupExecutionInput,
        external_ref: ExternalOperationRef,
        provider: HaoaiBoundContentProvider,
        media_store: HaoaiGroupOwnedMediaStore,
    ) -> HaoaiGroupMediaOutcome:
        """Resume owned media from the original accepted signed observation only."""

        replay = self._read_completed_replay(
            claim, intent, verified_input, external_ref
        )
        if replay is None:
            return HaoaiGroupMediaOutcome("pending")
        grant, outcome = replay
        return self.materialize(
            claim,
            intent,
            verified_input,
            grant,
            outcome,
            provider,
            media_store,
        )

    def materialize_controlled_late(
        self,
        candidate: object,
        provider: HaoaiBoundContentProvider,
        media_store: HaoaiGroupOwnedMediaStore,
        *,
        registry: object,
        crypto: object,
        candidate_reader: Callable[[Session, object], object | None],
        authorize: Callable[[], bool],
    ) -> HaoaiGroupMediaOutcome:
        """Materialize a terminal Group's original response under late-only proof."""

        from .late import HaoaiGroupLateCandidate, same_late_candidate

        if (
            type(candidate) is not HaoaiGroupLateCandidate
            or type(self.proof_authority) is not HmacProofAuthority
            or not callable(candidate_reader)
            or not callable(authorize)
            or not authorize()
        ):
            return HaoaiGroupMediaOutcome("pending")

        current = self._read_late_candidate(candidate, candidate_reader)
        if not same_late_candidate(candidate, current):
            return HaoaiGroupMediaOutcome("pending")
        state = self._read_late_control_state(candidate)
        if state.status == "conflict":
            return HaoaiGroupMediaOutcome("pending")

        if state.plan is not None and state.put_started:
            replay = self.result_consumer.confirm_existing_controlled_late(
                candidate,
                state.plan,
                registry=registry,
                crypto=crypto,
            )
            if replay is not None:
                return HaoaiGroupMediaOutcome(replay.disposition, replay.receipt)

        material: OwnedObjectMaterial | None = None
        plan = state.plan
        if plan is None or not state.put_started:
            if not authorize():
                return HaoaiGroupMediaOutcome("pending")
            source = self._read_original_content(
                provider, candidate.verified_input, candidate.external_ref
            )
            if source is None or not authorize():
                return HaoaiGroupMediaOutcome("pending")

            if plan is None:
                try:
                    prepared = media_store.prepare_owned_bytes(
                        source,
                        {"max_bytes": MAX_VIDEO_BYTES},
                        owner_ref=candidate.controlled_scope.owner_scope.user_id,
                        submission_ref=candidate.intent.external_submission_id,
                        slot="primary",
                        provider_result_digest=candidate.poll_outcome.observation.payload_digest,
                    )
                except Exception:
                    return HaoaiGroupMediaOutcome("pending")
                if type(prepared) is not OwnedObjectMaterial:
                    return HaoaiGroupMediaOutcome("pending")
                material = prepared
                persisted = self._persist_late_plan(
                    candidate,
                    prepared.plan,
                    candidate_reader,
                )
                if persisted.status != "ready" or persisted.plan is None:
                    return HaoaiGroupMediaOutcome("pending")
                plan = persisted.plan
                state = persisted
            else:
                if not _material_matches_plan(source.content, source.media_type, plan):
                    return HaoaiGroupMediaOutcome("pending")
                try:
                    material = OwnedObjectMaterial(
                        plan=plan,
                        content=source.content,
                    )
                except (AttributeError, TypeError, ValueError):
                    return HaoaiGroupMediaOutcome("pending")

        if plan is None:
            return HaoaiGroupMediaOutcome("pending")

        if not state.put_started:
            if material is None or material.plan != plan or not authorize():
                return HaoaiGroupMediaOutcome("pending")
            started = self._persist_late_put_started(
                candidate,
                plan,
                state.plan_fact_digest,
                state.original_scope,
                candidate_reader,
            )
            if started.status != "ready":
                return HaoaiGroupMediaOutcome("pending")
            if started.put_authorized_here:
                permit = _OnePlannedWritePermit(
                    plan,
                    lambda: self._authorize_late_put(
                        candidate,
                        plan,
                        candidate_reader,
                        authorize,
                    ),
                )
                try:
                    media_store.put_planned_media(material, permit)
                except Exception:
                    # The immutable start marker turns an unknown result into GET-only recovery.
                    pass

        if not authorize():
            return HaoaiGroupMediaOutcome("pending")
        try:
            verification = media_store.verify_planned_media(plan)
        except Exception:
            return HaoaiGroupMediaOutcome("pending")
        if (
            type(verification) is not OwnedObjectVerification
            or verification.status != "verified"
            or verification.stored_media is None
            or not _late_plan_verification_matches(
                candidate, plan, verification
            )
            or not authorize()
        ):
            return HaoaiGroupMediaOutcome("pending")

        outcome = self.result_consumer.complete_controlled_late(
            candidate,
            plan,
            verification,
            registry=registry,
            crypto=crypto,
        )
        return HaoaiGroupMediaOutcome(outcome.disposition, outcome.receipt)

    def _read_late_candidate(
        self,
        expected: object,
        candidate_reader: Callable[[Session, object], object | None],
    ) -> object | None:
        try:
            with self.session_factory() as session:
                with session.begin():
                    return candidate_reader(
                        session, expected.controlled_scope.owner_scope
                    )
        except Exception:
            return None

    def _read_late_control_state(self, candidate: object) -> _MediaControlState:
        try:
            with self.session_factory() as session:
                return self._read_late_control_rows(session, candidate)
        except Exception:
            return _MediaControlState("conflict")

    def _read_late_control_rows(
        self,
        session: Session,
        candidate: object,
    ) -> _MediaControlState:
        from .late import HaoaiGroupLateCandidate

        if type(candidate) is not HaoaiGroupLateCandidate:
            return _MediaControlState("conflict")
        submission_id = candidate.intent.external_submission_id
        plan_row = session.execute(
            select(ResultEvidenceRow.__table__)
            .where(
                ResultEvidenceRow.__table__.c.evidence_key == _PLAN_KEY + submission_id
            )
            .with_for_update()
        ).mappings().one_or_none()
        put_row = session.execute(
            select(ResultEvidenceRow.__table__)
            .where(
                ResultEvidenceRow.__table__.c.evidence_key
                == _PUT_STARTED_KEY + submission_id
            )
            .with_for_update()
        ).mappings().one_or_none()
        if plan_row is None:
            return (
                _MediaControlState("absent")
                if put_row is None
                else _MediaControlState("conflict")
            )
        fact, digest = _decode_signed_control(
            plan_row,
            evidence_kind=_PLAN_KIND,
            purpose=_PLAN_PURPOSE,
            expected_key=_PLAN_KEY + submission_id,
            schema=_PLAN_SCHEMA,
            authority=self.proof_authority,
        )
        scope_fact = _late_scope_fact(candidate)
        if (
            fact is None
            or digest is None
            or plan_row.get("step_id") != candidate.intent.step_id
            or plan_row.get("submission_id") != submission_id
            or not _late_scope_fact_matches(fact, scope_fact)
            or not isinstance(fact.get("plan"), dict)
        ):
            return _MediaControlState("conflict")
        try:
            plan = OwnedObjectPlan(**fact["plan"])
        except (TypeError, ValueError):
            return _MediaControlState("conflict")
        if not _late_plan_matches_scope(plan, candidate):
            return _MediaControlState("conflict")
        original_scope = _original_scope_from_fact(fact)
        if put_row is None:
            return _MediaControlState(
                "ready", plan, digest, False, False, original_scope
            )
        put_fact, _put_digest = _decode_signed_control(
            put_row,
            evidence_kind=_PUT_STARTED_KIND,
            purpose=_PUT_STARTED_PURPOSE,
            expected_key=_PUT_STARTED_KEY + submission_id,
            schema=_PUT_STARTED_SCHEMA,
            authority=self.proof_authority,
        )
        if (
            put_fact is None
            or put_row.get("step_id") != candidate.intent.step_id
            or put_row.get("submission_id") != submission_id
            or not _late_scope_fact_matches(put_fact, scope_fact)
            or _original_scope_from_fact(put_fact) != original_scope
            or put_fact.get("plan_fact_digest") != digest
            or put_fact.get("write_id") != plan.write_id
        ):
            return _MediaControlState("conflict")
        return _MediaControlState(
            "ready", plan, digest, True, False, original_scope
        )

    def _persist_late_plan(
        self,
        candidate: object,
        proposed: OwnedObjectPlan,
        candidate_reader: Callable[[Session, object], object | None],
    ) -> _MediaControlState:
        from .late import same_late_candidate

        try:
            with self.session_factory() as session:
                with session.begin():
                    current = candidate_reader(
                        session, candidate.controlled_scope.owner_scope
                    )
                    if not same_late_candidate(candidate, current):
                        return _MediaControlState("conflict")
                    existing = self._read_late_control_rows(session, candidate)
                    if existing.status != "absent":
                        return existing
                    if not _late_plan_matches_scope(proposed, candidate):
                        return _MediaControlState("conflict")
                    evidence_id = self._new_id()
                    if evidence_id is None:
                        return _MediaControlState("conflict")
                    fact = {
                        **_late_scope_fact(candidate),
                        "schema": _PLAN_SCHEMA,
                        "plan": asdict(proposed),
                    }
                    _insert_signed_control(
                        session,
                        evidence_id=evidence_id,
                        evidence_key=_PLAN_KEY + candidate.intent.external_submission_id,
                        evidence_kind=_PLAN_KIND,
                        purpose=_PLAN_PURPOSE,
                        schema=_PLAN_SCHEMA,
                        fact=fact,
                        step_id=candidate.intent.step_id,
                        submission_id=candidate.intent.external_submission_id,
                        now=self._now(),
                        authority=self.proof_authority,
                    )
                    state = self._read_late_control_rows(session, candidate)
                    return (
                        _MediaControlState(
                            "ready",
                            state.plan,
                            state.plan_fact_digest,
                            False,
                            False,
                            state.original_scope,
                        )
                        if state.status == "ready" and state.plan == proposed
                        else _MediaControlState("conflict")
                    )
        except IntegrityError:
            return self._read_late_control_state(candidate)
        except Exception:
            return _MediaControlState("conflict")

    def _persist_late_put_started(
        self,
        candidate: object,
        plan: OwnedObjectPlan,
        plan_fact_digest: str | None,
        original_scope: Mapping[str, object] | None,
        candidate_reader: Callable[[Session, object], object | None],
    ) -> _MediaControlState:
        from .late import same_late_candidate

        if not isinstance(plan_fact_digest, str) or original_scope is None:
            return _MediaControlState("conflict")
        try:
            with self.session_factory() as session:
                with session.begin():
                    current_candidate = candidate_reader(
                        session, candidate.controlled_scope.owner_scope
                    )
                    if not same_late_candidate(candidate, current_candidate):
                        return _MediaControlState("conflict")
                    current = self._read_late_control_rows(session, candidate)
                    if (
                        current.status != "ready"
                        or current.plan != plan
                        or current.plan_fact_digest != plan_fact_digest
                        or current.original_scope != original_scope
                    ):
                        return _MediaControlState("conflict")
                    if current.put_started:
                        return current
                    evidence_id = self._new_id()
                    if evidence_id is None:
                        return _MediaControlState("conflict")
                    fact = {
                        **original_scope,
                        "schema": _PUT_STARTED_SCHEMA,
                        "plan_fact_digest": plan_fact_digest,
                        "write_id": plan.write_id,
                    }
                    _insert_signed_control(
                        session,
                        evidence_id=evidence_id,
                        evidence_key=_PUT_STARTED_KEY
                        + candidate.intent.external_submission_id,
                        evidence_kind=_PUT_STARTED_KIND,
                        purpose=_PUT_STARTED_PURPOSE,
                        schema=_PUT_STARTED_SCHEMA,
                        fact=fact,
                        step_id=candidate.intent.step_id,
                        submission_id=candidate.intent.external_submission_id,
                        now=self._now(),
                        authority=self.proof_authority,
                    )
                    state = self._read_late_control_rows(session, candidate)
                    if state.status != "ready" or not state.put_started:
                        return _MediaControlState("conflict")
                    return _MediaControlState(
                        "ready",
                        state.plan,
                        state.plan_fact_digest,
                        True,
                        True,
                        state.original_scope,
                    )
        except IntegrityError:
            return self._read_late_control_state(candidate)
        except Exception:
            return _MediaControlState("conflict")

    def _authorize_late_put(
        self,
        candidate: object,
        plan: OwnedObjectPlan,
        candidate_reader: Callable[[Session, object], object | None],
        authorize: Callable[[], bool],
    ) -> bool:
        from .late import same_late_candidate

        if not authorize():
            return False
        try:
            with self.session_factory() as session:
                with session.begin():
                    current = candidate_reader(
                        session, candidate.controlled_scope.owner_scope
                    )
                    if not same_late_candidate(candidate, current):
                        return False
                    state = self._read_late_control_rows(session, candidate)
                    return bool(
                        state.status == "ready"
                        and state.plan == plan
                        and state.put_started
                    )
        except Exception:
            return False

    def _read_completed_replay(
        self,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified_input: VerifiedHaoaiGroupExecutionInput,
        external_ref: ExternalOperationRef,
    ) -> tuple[HaoaiGroupPollReplayGrant, HaoaiGroupPollOutcome] | None:
        if (
            type(claim) is not ClaimAuthority
            or type(intent) is not CommittedIntent
            or type(verified_input) is not VerifiedHaoaiGroupExecutionInput
            or type(external_ref) is not ExternalOperationRef
            or type(self.proof_authority) is not HmacProofAuthority
            or external_ref.create_outcome != "submitted"
            or external_ref.provider != "haoai"
            or external_ref.intent_id != intent.external_submission_id
            or external_ref.attempt_id != intent.attempt_id
            or claim.scope.task_id != verified_input.task_id
            or claim.scope.user_id != verified_input.snapshot.grant_snapshot.actor_id
        ):
            return None
        try:
            with self.session_factory() as session:
                with session.begin():
                    graph = lock_haoai_group_graph(
                        session, claim.scope.user_id, claim.scope.task_id
                    )
                    now = self._now()
                    if (
                        graph is None
                        or not _intent_matches_group(graph, intent, verified_input)
                        or not _claim_live(graph.task, claim, now)
                        or not _current_group_owner(graph, claim, intent, now)
                        or _has_group_work(session, graph)
                        or _has_policy_stop_event(session, graph)
                    ):
                        return None
                    reservation_row = session.execute(
                        select(PollReservationRow.__table__)
                        .where(
                            PollReservationRow.__table__.c.unit_id
                            == verified_input.unit_id,
                            PollReservationRow.__table__.c.group_key
                            == graph.binding.group_key,
                            PollReservationRow.__table__.c.step_id == intent.step_id,
                            PollReservationRow.__table__.c.state == "accepted",
                        )
                        .order_by(PollReservationRow.__table__.c.sequence.desc())
                        .limit(1)
                    ).mappings().one_or_none()
                    if (
                        reservation_row is None
                        or not isinstance(
                            reservation_row.get("observation_ref"), str
                        )
                    ):
                        return None
                    reservation = _poll_record_from_row(reservation_row)
                    observation_row = session.execute(
                        select(ResultEvidenceRow.__table__).where(
                            ResultEvidenceRow.__table__.c.id
                            == reservation_row.get("observation_ref"),
                            ResultEvidenceRow.__table__.c.evidence_key
                            == f"haoai-poll-v1:{reservation.reservation_id}",
                            ResultEvidenceRow.__table__.c.step_id == intent.step_id,
                            ResultEvidenceRow.__table__.c.submission_id
                            == intent.external_submission_id,
                        )
                    ).mappings().one_or_none()
                    if observation_row is None:
                        return None
                    fact = verified_group_observation_fact(
                        observation_row, self.proof_authority
                    )
                    if not isinstance(fact, Mapping):
                        return None
                    received_at = _aware_datetime_from_iso(fact.get("received_at"))
                    if (
                        received_at is None
                        or fact.get("source_fact") != "source_completed"
                        or fact.get("source_status") not in {"completed", "done"}
                        or reservation.state != "accepted"
                        or reservation.external_id != external_ref.external_id
                        or reservation.payload_digest != intent.request_digest
                    ):
                        return None
                    payload = _safe_replayed_payload(fact)
                    if payload is None:
                        return None
                    observation = ProviderObservation(
                        owner_scope=claim.scope,
                        intent_id=intent.external_submission_id,
                        attempt_id=intent.attempt_id,
                        external_ref=external_ref,
                        dedupe_key=hashlib.sha256(
                            f"{reservation.reservation_id}\0{observation_row.get('result_digest')}".encode(
                                "utf-8"
                            )
                        ).hexdigest(),
                        payload_digest=str(observation_row.get("result_digest")),
                        received_at=received_at,
                        status="completed",
                        payload=payload,
                    )
                    grant = HaoaiGroupPollReplayGrant(
                        reservation=AcceptedPollObservation(
                            reservation=reservation,
                            confirmed_at=received_at,
                        ),
                        claim=claim,
                        intent=intent,
                        verified_input=verified_input,
                        external_ref=external_ref,
                    )
                    outcome = HaoaiGroupPollOutcome(
                        "accepted", observation, str(observation_row["id"])
                    )
                    shape_ok = _completed_input_shapes_match(
                        claim, intent, verified_input, grant, outcome
                    )
                    binding_ok = _binding_matches(
                        graph, claim, intent, verified_input, grant
                    )
                    accepted_ok = _accepted_completed_observation(
                        session,
                        graph,
                        claim,
                        intent,
                        verified_input,
                        grant,
                        outcome,
                        self.proof_authority,
                    )
                    if not (shape_ok and binding_ok and accepted_ok):
                        return None
                    return grant, outcome
        except Exception:
            return None

    def _authorize_put(
        self,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified_input: VerifiedHaoaiGroupExecutionInput,
        grant: HaoaiGroupPollGrant,
        poll_outcome: HaoaiGroupPollOutcome,
        plan: OwnedObjectPlan,
        plan_fact_digest: str | None,
    ) -> bool:
        """Recheck the live owner and immutable plan at the write permit boundary."""

        if not isinstance(plan_fact_digest, str):
            return False
        try:
            with self.session_factory() as session:
                with session.begin():
                    graph = self._lock_current_graph(
                        session,
                        claim,
                        intent,
                        verified_input,
                        grant,
                        poll_outcome,
                    )
                    if graph is None:
                        return False
                    state = self._read_control_rows(
                        session,
                        claim,
                        intent,
                        verified_input,
                        grant,
                        poll_outcome,
                    )
                    return bool(
                        state.status == "ready"
                        and state.plan == plan
                        and state.plan_fact_digest == plan_fact_digest
                        and state.put_started
                    )
        except Exception:
            return False

    def _read_original_content(
        self,
        provider: HaoaiBoundContentProvider,
        verified_input: VerifiedHaoaiGroupExecutionInput,
        external_ref: ExternalOperationRef,
    ) -> ReferenceInput | None:
        try:
            source = provider.fetch_provider_content(
                verified_input.snapshot,
                external_ref,
            )
        except Exception:
            return None
        if (
            type(source) is not ReferenceInput
            or source.kind != "bytes"
            or source.locator is not None
            or type(source.content) is not bytes
            or not source.content
            or len(source.content) > MAX_VIDEO_BYTES
            or source.provider_operation_ref is not None
            or not isinstance(source.media_type, str)
            or source.media_type not in _VIDEO_CONTENT_TYPES
        ):
            return None
        return source

    def _persist_plan(
        self,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified_input: VerifiedHaoaiGroupExecutionInput,
        grant: HaoaiGroupPollGrant,
        poll_outcome: HaoaiGroupPollOutcome,
        proposed: OwnedObjectPlan,
    ) -> _MediaControlState:
        key = _PLAN_KEY + intent.external_submission_id
        try:
            with self.session_factory() as session:
                with session.begin():
                    graph = self._lock_current_graph(
                        session,
                        claim,
                        intent,
                        verified_input,
                        grant,
                        poll_outcome,
                    )
                    if graph is None:
                        return _MediaControlState("conflict")
                    row = session.execute(
                        select(ResultEvidenceRow.__table__).where(
                            ResultEvidenceRow.__table__.c.evidence_key == key
                        )
                    ).mappings().one_or_none()
                    if row is not None:
                        return self._decode_plan_state(
                            row,
                            session,
                            claim,
                            intent,
                            verified_input,
                            grant,
                            poll_outcome,
                        )
                    if not _plan_matches_scope(
                        proposed,
                        claim,
                        intent,
                        poll_outcome.observation,
                    ):
                        return _MediaControlState("conflict")
                    fact = _plan_fact(
                        proposed,
                        claim,
                        intent,
                        verified_input,
                        grant,
                        poll_outcome,
                    )
                    evidence_id = self._new_id()
                    if evidence_id is None:
                        return _MediaControlState("conflict")
                    _insert_signed_control(
                        session,
                        evidence_id=evidence_id,
                        evidence_key=key,
                        evidence_kind=_PLAN_KIND,
                        purpose=_PLAN_PURPOSE,
                        schema=_PLAN_SCHEMA,
                        fact=fact,
                        step_id=intent.step_id,
                        submission_id=intent.external_submission_id,
                        now=self._now(),
                        authority=self.proof_authority,
                    )
                    row = session.execute(
                        select(ResultEvidenceRow.__table__).where(
                            ResultEvidenceRow.__table__.c.evidence_key == key
                        )
                    ).mappings().one_or_none()
                    if row is None:
                        return _MediaControlState("conflict")
                    return self._decode_plan_state(
                        row, session, claim, intent, verified_input, grant, poll_outcome
                    )
        except IntegrityError:
            return self._read_control_state(
                claim, intent, verified_input, grant, poll_outcome
            )
        except Exception:
            return _MediaControlState("conflict")

    def _persist_put_started(
        self,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified_input: VerifiedHaoaiGroupExecutionInput,
        grant: HaoaiGroupPollGrant,
        poll_outcome: HaoaiGroupPollOutcome,
        plan: OwnedObjectPlan,
        plan_fact_digest: str | None,
        original_scope: Mapping[str, object] | None,
    ) -> _MediaControlState:
        if not isinstance(plan_fact_digest, str) or original_scope is None:
            return _MediaControlState("conflict")
        key = _PUT_STARTED_KEY + intent.external_submission_id
        try:
            with self.session_factory() as session:
                with session.begin():
                    graph = self._lock_current_graph(
                        session,
                        claim,
                        intent,
                        verified_input,
                        grant,
                        poll_outcome,
                    )
                    if graph is None:
                        return _MediaControlState("conflict")
                    current = self._read_control_rows(
                        session, claim, intent, verified_input, grant, poll_outcome
                    )
                    if (
                        current.status != "ready"
                        or current.plan != plan
                        or current.plan_fact_digest != plan_fact_digest
                        or current.original_scope != original_scope
                    ):
                        return _MediaControlState("conflict")
                    row = session.execute(
                        select(ResultEvidenceRow.__table__).where(
                            ResultEvidenceRow.__table__.c.evidence_key == key
                        )
                    ).mappings().one_or_none()
                    if row is not None:
                        state = self._decode_put_started_state(
                            row,
                            session,
                            claim,
                            intent,
                            verified_input,
                            grant,
                            poll_outcome,
                            plan_fact_digest,
                            original_scope,
                            plan.write_id,
                        )
                        if state.status == "ready":
                            return state
                        return _MediaControlState("conflict")
                    fact = _put_started_fact(
                        plan_fact_digest,
                        plan,
                        original_scope,
                    )
                    evidence_id = self._new_id()
                    if evidence_id is None:
                        return _MediaControlState("conflict")
                    _insert_signed_control(
                        session,
                        evidence_id=evidence_id,
                        evidence_key=key,
                        evidence_kind=_PUT_STARTED_KIND,
                        purpose=_PUT_STARTED_PURPOSE,
                        schema=_PUT_STARTED_SCHEMA,
                        fact=fact,
                        step_id=intent.step_id,
                        submission_id=intent.external_submission_id,
                        now=self._now(),
                        authority=self.proof_authority,
                    )
                    row = session.execute(
                        select(ResultEvidenceRow.__table__).where(
                            ResultEvidenceRow.__table__.c.evidence_key == key
                        )
                    ).mappings().one_or_none()
                    if row is None:
                        return _MediaControlState("conflict")
                    return self._decode_put_started_state(
                        row,
                        session,
                        claim,
                        intent,
                        verified_input,
                        grant,
                        poll_outcome,
                        plan_fact_digest,
                        original_scope,
                        plan.write_id,
                        put_authorized_here=True,
                    )
        except IntegrityError:
            return self._read_control_state(
                claim, intent, verified_input, grant, poll_outcome
            )
        except Exception:
            return _MediaControlState("conflict")

    def _read_control_state(
        self,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified_input: VerifiedHaoaiGroupExecutionInput,
        grant: HaoaiGroupPollGrant,
        poll_outcome: HaoaiGroupPollOutcome,
    ) -> _MediaControlState:
        try:
            with self.session_factory() as session:
                return self._read_control_rows(
                    session, claim, intent, verified_input, grant, poll_outcome
                )
        except Exception:
            return _MediaControlState("conflict")

    def _read_control_rows(
        self,
        session: Session,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified_input: VerifiedHaoaiGroupExecutionInput,
        grant: HaoaiGroupPollGrant,
        poll_outcome: HaoaiGroupPollOutcome,
    ) -> _MediaControlState:
        plan_row = session.execute(
            select(ResultEvidenceRow.__table__).where(
                ResultEvidenceRow.__table__.c.evidence_key
                == _PLAN_KEY + intent.external_submission_id
            ).with_for_update()
        ).mappings().one_or_none()
        put_row = session.execute(
            select(ResultEvidenceRow.__table__).where(
                ResultEvidenceRow.__table__.c.evidence_key
                == _PUT_STARTED_KEY + intent.external_submission_id
            ).with_for_update()
        ).mappings().one_or_none()
        if plan_row is None:
            return (
                _MediaControlState("absent")
                if put_row is None
                else _MediaControlState("conflict")
            )
        state = self._decode_plan_state(
            plan_row, session, claim, intent, verified_input, grant, poll_outcome
        )
        if state.status != "ready":
            return state
        if put_row is None:
            return state
        if state.plan is None or state.plan_fact_digest is None:
            return _MediaControlState("conflict")
        started = self._decode_put_started_state(
            put_row,
            session,
            claim,
            intent,
            verified_input,
            grant,
            poll_outcome,
            state.plan_fact_digest,
            state.original_scope,
            state.plan.write_id,
        )
        if started.status != "ready":
            return started
        return _MediaControlState(
            "ready",
            state.plan,
            state.plan_fact_digest,
            True,
            False,
            state.original_scope,
        )

    def _decode_plan_state(
        self,
        row: Mapping[str, object],
        session: Session,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified_input: VerifiedHaoaiGroupExecutionInput,
        grant: HaoaiGroupPollGrant,
        poll_outcome: HaoaiGroupPollOutcome,
    ) -> _MediaControlState:
        fact, digest = _decode_signed_control(
            row,
            evidence_kind=_PLAN_KIND,
            purpose=_PLAN_PURPOSE,
            expected_key=_PLAN_KEY + intent.external_submission_id,
            schema=_PLAN_SCHEMA,
            authority=self.proof_authority,
        )
        if fact is None or digest is None:
            return _MediaControlState("conflict")
        if (
            row.get("step_id") != intent.step_id
            or row.get("submission_id") != intent.external_submission_id
        ):
            return _MediaControlState("conflict")
        if not _scope_fact_matches(
            session,
            fact,
            claim,
            intent,
            verified_input,
            grant,
            self.proof_authority,
        ):
            return _MediaControlState("conflict")
        raw_plan = fact.get("plan")
        if not isinstance(raw_plan, dict):
            return _MediaControlState("conflict")
        try:
            plan = OwnedObjectPlan(**raw_plan)
        except (TypeError, ValueError):
            return _MediaControlState("conflict")
        if not _plan_matches_scope(
            plan, claim, intent, fact.get("provider_result_digest")
        ):
            return _MediaControlState("conflict")
        return _MediaControlState(
            "ready",
            plan,
            digest,
            False,
            False,
            _original_scope_from_fact(fact),
        )

    def _decode_put_started_state(
        self,
        row: Mapping[str, object],
        session: Session,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified_input: VerifiedHaoaiGroupExecutionInput,
        grant: HaoaiGroupPollGrant,
        poll_outcome: HaoaiGroupPollOutcome,
        plan_fact_digest: str,
        original_scope: Mapping[str, object] | None,
        expected_write_id: str,
        *,
        put_authorized_here: bool = False,
    ) -> _MediaControlState:
        fact, _digest = _decode_signed_control(
            row,
            evidence_kind=_PUT_STARTED_KIND,
            purpose=_PUT_STARTED_PURPOSE,
            expected_key=_PUT_STARTED_KEY + intent.external_submission_id,
            schema=_PUT_STARTED_SCHEMA,
            authority=self.proof_authority,
        )
        if (
            fact is None
            or original_scope is None
            or row.get("step_id") != intent.step_id
            or row.get("submission_id") != intent.external_submission_id
            or not _scope_fact_matches(
                session,
                fact,
                claim,
                intent,
                verified_input,
                grant,
                self.proof_authority,
            )
            or _original_scope_from_fact(fact) != original_scope
            or fact.get("plan_fact_digest") != plan_fact_digest
            or fact.get("write_id") != expected_write_id
        ):
            return _MediaControlState("conflict")
        return _MediaControlState(
            "ready",
            None,
            plan_fact_digest,
            True,
            put_authorized_here,
            original_scope,
        )

    def _lock_current_graph(
        self,
        session: Session,
        claim: ClaimAuthority,
        intent: CommittedIntent,
        verified_input: VerifiedHaoaiGroupExecutionInput,
        grant: HaoaiGroupPollGrant,
        poll_outcome: HaoaiGroupPollOutcome,
    ) -> LockedHaoaiGroupGraph | None:
        graph = lock_haoai_group_graph(
            session, claim.scope.user_id, claim.scope.task_id
        )
        now = self._now()
        if (
            graph is None
            or not _binding_matches(graph, claim, intent, verified_input, grant)
            or not _claim_live(graph.task, claim, now)
            or not _current_group_owner(graph, claim, intent, now)
            or not _accepted_completed_observation(
                session,
                graph,
                claim,
                intent,
                verified_input,
                grant,
                poll_outcome,
                self.proof_authority,
            )
            or _has_group_work(session, graph)
            or _has_policy_stop_event(session, graph)
        ):
            return None
        return graph

    def _now(self) -> datetime:
        now = self.db_clock()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("HaoAI Group media clock must be timezone-aware")
        return now.astimezone(timezone.utc)

    def _new_id(self) -> str | None:
        try:
            value = self.id_factory()
        except Exception:
            return None
        return value if _valid_id(value) else None


class _OnePlannedWritePermit:
    def __init__(self, plan: OwnedObjectPlan, authorize: Callable[[], bool]) -> None:
        self._plan = plan
        self._authorize = authorize
        self._claimed = False

    def claim(self, plan: OwnedObjectPlan) -> bool:
        if self._claimed or plan != self._plan:
            return False
        try:
            if not self._authorize():
                return False
        except Exception:
            return False
        self._claimed = True
        return True


def _plan_fact(
    plan: OwnedObjectPlan,
    claim: ClaimAuthority,
    intent: CommittedIntent,
    verified_input: VerifiedHaoaiGroupExecutionInput,
    grant: HaoaiGroupPollGrant,
    poll_outcome: HaoaiGroupPollOutcome,
) -> dict[str, object]:
    return {
        **_scope_fact(claim, intent, verified_input, grant, poll_outcome),
        "schema": _PLAN_SCHEMA,
        "plan": asdict(plan),
    }


def _put_started_fact(
    plan_fact_digest: str,
    plan: OwnedObjectPlan,
    original_scope: Mapping[str, object],
) -> dict[str, object]:
    return {
        **original_scope,
        "schema": _PUT_STARTED_SCHEMA,
        "plan_fact_digest": plan_fact_digest,
        "write_id": plan.write_id,
    }


def _scope_fact(
    claim: ClaimAuthority,
    intent: CommittedIntent,
    verified_input: VerifiedHaoaiGroupExecutionInput,
    grant: HaoaiGroupPollGrant,
    poll_outcome: HaoaiGroupPollOutcome,
) -> dict[str, object]:
    reservation = grant.reservation.reservation
    observation = poll_outcome.observation
    assert observation is not None
    return {
        "owner_id": claim.scope.user_id,
        "task_id": claim.scope.task_id,
        "unit_id": verified_input.unit_id,
        "group_key": reservation.group_key,
        "step_id": intent.step_id,
        "submission_id": intent.external_submission_id,
        "attempt_id": intent.attempt_id,
        "provider": intent.provider,
        "external_id": grant.external_ref.external_id,
        "request_digest": intent.request_digest,
        "reservation_id": reservation.reservation_id,
        "reservation_sequence": reservation.sequence,
        "observation_id": poll_outcome.observation_id,
        "provider_result_digest": observation.payload_digest,
        "received_at": observation.received_at.astimezone(timezone.utc).isoformat(),
    }


def _scope_fact_matches(
    session: Session,
    fact: Mapping[str, object],
    claim: ClaimAuthority,
    intent: CommittedIntent,
    verified_input: VerifiedHaoaiGroupExecutionInput,
    grant: HaoaiGroupPollGrant,
    authority: HmacProofAuthority | None,
) -> bool:
    reservation = grant.reservation.reservation
    expected = {
        "owner_id": claim.scope.user_id,
        "task_id": claim.scope.task_id,
        "unit_id": verified_input.unit_id,
        "group_key": reservation.group_key,
        "step_id": intent.step_id,
        "submission_id": intent.external_submission_id,
        "attempt_id": intent.attempt_id,
        "provider": intent.provider,
        "external_id": grant.external_ref.external_id,
        "request_digest": intent.request_digest,
    }
    if not all(fact.get(key) == value for key, value in expected.items()):
        return False
    return _verified_original_completed_observation(
        session, fact, authority, intent
    )


def _original_scope_from_fact(fact: Mapping[str, object]) -> dict[str, object]:
    keys = (
        "owner_id",
        "task_id",
        "unit_id",
        "group_key",
        "step_id",
        "submission_id",
        "attempt_id",
        "provider",
        "external_id",
        "request_digest",
        "reservation_id",
        "reservation_sequence",
        "observation_id",
        "provider_result_digest",
        "received_at",
    )
    return {key: fact.get(key) for key in keys}


def _safe_replayed_payload(fact: Mapping[str, object]) -> dict[str, object] | None:
    if (
        fact.get("schema") != "haoai_poll_observation_v1"
        or fact.get("source_fact") != "source_completed"
        or fact.get("source_status") not in {"completed", "done"}
    ):
        return None
    payload: dict[str, object] = {
        "source_fact": "source_completed",
        "source_status": fact.get("source_status"),
    }
    for name, allowed in (
        ("content_status", {"bound_content_needed", "unsafe_locator"}),
        ("content_path_suffix", {"/content"}),
        ("locator_source", {"video_url", "video", "url"}),
    ):
        value = fact.get(name)
        if value is not None:
            if value not in allowed:
                return None
            payload[name] = value
    return payload


def _verified_original_completed_observation(
    session: Session,
    fact: Mapping[str, object],
    authority: HmacProofAuthority | None,
    intent: CommittedIntent,
) -> bool:
    reservation_id = fact.get("reservation_id")
    sequence = fact.get("reservation_sequence")
    observation_id = fact.get("observation_id")
    digest = fact.get("provider_result_digest")
    if (
        not isinstance(reservation_id, str)
        or type(sequence) is not int
        or not isinstance(observation_id, str)
        or not isinstance(digest, str)
    ):
        return False
    reservation = session.execute(
        select(PollReservationRow.__table__).where(
            PollReservationRow.__table__.c.reservation_id == reservation_id,
            PollReservationRow.__table__.c.unit_id == fact.get("unit_id"),
            PollReservationRow.__table__.c.group_key == fact.get("group_key"),
            PollReservationRow.__table__.c.sequence == sequence,
        )
    ).mappings().one_or_none()
    evidence = session.execute(
        select(ResultEvidenceRow.__table__).where(
            ResultEvidenceRow.__table__.c.id == observation_id,
            ResultEvidenceRow.__table__.c.evidence_key
            == f"haoai-poll-v1:{reservation_id}",
            ResultEvidenceRow.__table__.c.step_id == fact.get("step_id"),
            ResultEvidenceRow.__table__.c.submission_id == fact.get("submission_id"),
        )
    ).mappings().one_or_none()
    if reservation is None or evidence is None:
        return False
    observed = verified_group_observation_fact(evidence, authority)
    received_at = fact.get("received_at")
    if not isinstance(observed, Mapping) or not isinstance(received_at, str):
        return False
    try:
        received = datetime.fromisoformat(received_at)
    except ValueError:
        return False
    if received.tzinfo is None:
        return False
    received_utc = received.astimezone(timezone.utc)
    deadline = intent.deadline_at.astimezone(timezone.utc)
    reserved_at = _aware_datetime_or_none(reservation.get("reserved_at"))
    timeout_at = _aware_datetime_or_none(reservation.get("timeout_at"))
    if reserved_at is None or timeout_at is None:
        return False
    return bool(
        reservation.get("state") == "accepted"
        and reservation.get("observation_ref") == observation_id
        and reservation.get("attempt_id") == fact.get("attempt_id")
        and reservation.get("step_id") == fact.get("step_id")
        and reservation.get("submission_id") == fact.get("submission_id")
        and reservation.get("external_id") == fact.get("external_id")
        and reservation.get("payload_digest") == fact.get("request_digest")
        and evidence.get("source") == "worker"
        and evidence.get("validity") == "unknown"
        and evidence.get("application_status") == "pending"
        and evidence.get("applied_at") is None
        and evidence.get("payload_digest")
        == hashlib.sha256(_canonical_json(observed)).hexdigest()
        and evidence.get("result_digest")
        == observation_body_digest(str(evidence.get("result_location", "")))
        and evidence.get("received_at") == _database_datetime(received_utc)
        and observed.get("schema") == "haoai_poll_observation_v1"
        and observed.get("policy_version")
        == HAOAI_ATTEMPT_GROUP.policy_version
        and observed.get("registry_version")
        == HAOAI_ATTEMPT_GROUP.registry_version
        and observed.get("owner_id") == fact.get("owner_id")
        and observed.get("task_id") == fact.get("task_id")
        and observed.get("unit_id") == fact.get("unit_id")
        and observed.get("group_key") == fact.get("group_key")
        and observed.get("step_id") == fact.get("step_id")
        and observed.get("submission_id") == fact.get("submission_id")
        and observed.get("attempt_id") == fact.get("attempt_id")
        and observed.get("external_id") == fact.get("external_id")
        and observed.get("request_digest") == fact.get("request_digest")
        and observed.get("reservation_id") == reservation_id
        and observed.get("sequence") == sequence
        and observed.get("reservation_token_digest")
        == hashlib.sha256(str(reservation.get("token", "")).encode("utf-8")).hexdigest()
        and observed.get("source_fact") == "source_completed"
        and observed.get("source_status") in {"completed", "done"}
        and observed.get("body_sha256") == digest
        and observed.get("received_at") == received_utc.isoformat()
        and received_utc < timeout_at
        and _database_datetime(reserved_at)
        == _database_datetime_from_iso(observed.get("reserved_at"))
        and _database_datetime(timeout_at)
        == _database_datetime_from_iso(observed.get("timeout_at"))
        and _valid_id(observation_id)
        and received_utc < deadline
    )


def _late_scope_fact(candidate: object) -> dict[str, object]:
    reservation = candidate.reservation
    observation = candidate.poll_outcome.observation
    if observation is None:
        raise ValueError("controlled late media requires its signed completion")
    return {
        "owner_id": candidate.controlled_scope.owner_scope.user_id,
        "task_id": candidate.controlled_scope.owner_scope.task_id,
        "unit_id": candidate.verified_input.unit_id,
        "group_key": candidate.controlled_scope.group_key,
        "step_id": candidate.intent.step_id,
        "submission_id": candidate.intent.external_submission_id,
        "attempt_id": candidate.intent.attempt_id,
        "provider": candidate.intent.provider,
        "external_id": candidate.external_ref.external_id,
        "request_digest": candidate.intent.request_digest,
        "reservation_id": reservation.reservation_id,
        "reservation_sequence": reservation.sequence,
        "observation_id": candidate.poll_outcome.observation_id,
        "provider_result_digest": observation.payload_digest,
        "received_at": observation.received_at.astimezone(timezone.utc).isoformat(),
    }


def _late_scope_fact_matches(
    fact: Mapping[str, object], expected: Mapping[str, object]
) -> bool:
    return all(fact.get(key) == value for key, value in expected.items())


def _late_plan_matches_scope(plan: OwnedObjectPlan, candidate: object) -> bool:
    observation = candidate.poll_outcome.observation
    return bool(
        type(plan) is OwnedObjectPlan
        and observation is not None
        and plan.owner_ref == candidate.controlled_scope.owner_scope.user_id
        and plan.submission_ref == candidate.intent.external_submission_id
        and plan.slot == "primary"
        and plan.provider_result_digest == observation.payload_digest
        and type(plan.size_bytes) is int
        and 0 < plan.size_bytes <= MAX_VIDEO_BYTES
        and plan.media_type in _VIDEO_CONTENT_TYPES
    )


def _late_plan_verification_matches(
    candidate: object,
    plan: OwnedObjectPlan,
    verification: OwnedObjectVerification,
) -> bool:
    stored = verification.stored_media
    expected_locator = f"{plan.public_base.rstrip('/')}/{quote(plan.object_key, safe='/')}"
    return bool(
        type(verification) is OwnedObjectVerification
        and verification.status == "verified"
        and stored is not None
        and _late_plan_matches_scope(plan, candidate)
        and stored.locator == expected_locator
        and stored.content_digest == plan.material_sha256
        and verification.observed_sha256 == plan.material_sha256
        and stored.media_type == plan.media_type
        and stored.size_bytes == plan.size_bytes
        and verification.observed_size_bytes == plan.size_bytes
    )


def verify_persisted_late_media_write(
    session: Session,
    candidate: object,
    plan: OwnedObjectPlan,
    verification: OwnedObjectVerification,
    authority: HmacProofAuthority | None,
) -> bool:
    """Validate the unique signed plan and start marker for controlled late output."""

    if (
        not isinstance(session, Session)
        or type(authority) is not HmacProofAuthority
        or not _late_plan_verification_matches(candidate, plan, verification)
    ):
        return False
    return verify_persisted_late_media_control(
        session, candidate, plan, authority
    )


def verify_persisted_late_media_control(
    session: Session,
    candidate: object,
    plan: OwnedObjectPlan,
    authority: HmacProofAuthority | None,
) -> bool:
    """Validate the signed original plan and start marker without object-store I/O."""

    from .late import HaoaiGroupLateCandidate

    if (
        not isinstance(session, Session)
        or type(candidate) is not HaoaiGroupLateCandidate
        or type(plan) is not OwnedObjectPlan
        or type(authority) is not HmacProofAuthority
        or not _late_plan_matches_scope(plan, candidate)
    ):
        return False
    submission_id = candidate.intent.external_submission_id
    plan_row = session.execute(
        select(ResultEvidenceRow.__table__)
        .where(
            ResultEvidenceRow.__table__.c.evidence_key == _PLAN_KEY + submission_id
        )
        .with_for_update()
    ).mappings().one_or_none()
    put_row = session.execute(
        select(ResultEvidenceRow.__table__)
        .where(
            ResultEvidenceRow.__table__.c.evidence_key
            == _PUT_STARTED_KEY + submission_id
        )
        .with_for_update()
    ).mappings().one_or_none()
    if plan_row is None or put_row is None:
        return False
    plan_fact, plan_digest = _decode_signed_control(
        plan_row,
        evidence_kind=_PLAN_KIND,
        purpose=_PLAN_PURPOSE,
        expected_key=_PLAN_KEY + submission_id,
        schema=_PLAN_SCHEMA,
        authority=authority,
    )
    scope_fact = _late_scope_fact(candidate)
    if (
        plan_fact is None
        or plan_digest is None
        or plan_row.get("step_id") != candidate.intent.step_id
        or plan_row.get("submission_id") != submission_id
        or not _late_scope_fact_matches(plan_fact, scope_fact)
        or not isinstance(plan_fact.get("plan"), dict)
    ):
        return False
    try:
        persisted_plan = OwnedObjectPlan(**plan_fact["plan"])
    except (TypeError, ValueError):
        return False
    put_fact, _put_digest = _decode_signed_control(
        put_row,
        evidence_kind=_PUT_STARTED_KIND,
        purpose=_PUT_STARTED_PURPOSE,
        expected_key=_PUT_STARTED_KEY + submission_id,
        schema=_PUT_STARTED_SCHEMA,
        authority=authority,
    )
    return bool(
        persisted_plan == plan
        and _late_plan_matches_scope(persisted_plan, candidate)
        and put_fact is not None
        and put_row.get("step_id") == candidate.intent.step_id
        and put_row.get("submission_id") == submission_id
        and _late_scope_fact_matches(put_fact, scope_fact)
        and _original_scope_from_fact(put_fact)
        == _original_scope_from_fact(plan_fact)
        and put_fact.get("plan_fact_digest") == plan_digest
        and put_fact.get("write_id") == persisted_plan.write_id
    )


def _plan_matches_scope(
    plan: OwnedObjectPlan,
    claim: ClaimAuthority,
    intent: CommittedIntent,
    observation: ProviderObservation | str | None,
) -> bool:
    digest = (
        observation.payload_digest
        if isinstance(observation, ProviderObservation)
        else observation
    )
    return bool(
        type(plan) is OwnedObjectPlan
        and isinstance(digest, str)
        and len(digest) == 64
        and plan.owner_ref == claim.scope.user_id
        and plan.submission_ref == intent.external_submission_id
        and plan.slot == "primary"
        and plan.provider_result_digest == digest
        and type(plan.size_bytes) is int
        and 0 < plan.size_bytes <= MAX_VIDEO_BYTES
        and plan.media_type in _VIDEO_CONTENT_TYPES
    )


def _insert_signed_control(
    session: Session,
    *,
    evidence_id: str,
    evidence_key: str,
    evidence_kind: str,
    purpose: str,
    schema: str,
    fact: Mapping[str, object],
    step_id: str,
    submission_id: str,
    now: datetime,
    authority: HmacProofAuthority,
) -> None:
    canonical = _canonical_json(fact)
    payload_digest = hashlib.sha256(canonical).hexdigest()
    location = json.dumps(
        {
            "fact": fact,
            "proof": {
                "key_id": authority.key_id,
                "signature": authority.sign(purpose, canonical),
            },
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    session.execute(
        insert(ResultEvidenceRow.__table__).values(
            id=evidence_id,
            step_id=step_id,
            submission_id=submission_id,
            evidence_key=evidence_key,
            source="worker",
            evidence_kind=evidence_kind,
            payload_digest=payload_digest,
            result_location=location,
            result_digest=payload_digest,
            received_at=now.replace(tzinfo=None),
            validity="unknown",
            application_status="pending",
            applied_at=None,
        )
    )


def _decode_signed_control(
    row: Mapping[str, object],
    *,
    evidence_kind: str,
    purpose: str,
    expected_key: str,
    schema: str,
    authority: HmacProofAuthority | None,
) -> tuple[dict[str, object] | None, str | None]:
    if (
        type(authority) is not HmacProofAuthority
        or row.get("source") != "worker"
        or row.get("evidence_kind") != evidence_kind
        or row.get("evidence_key") != expected_key
        or row.get("validity") != "unknown"
        or row.get("application_status") != "pending"
        or row.get("applied_at") is not None
        or not isinstance(row.get("result_location"), str)
    ):
        return None, None
    try:
        envelope = json.loads(str(row["result_location"]))
        fact = envelope["fact"]
        proof = envelope["proof"]
        if not isinstance(fact, dict) or not isinstance(proof, dict):
            return None, None
        canonical = _canonical_json(fact)
        digest = hashlib.sha256(canonical).hexdigest()
        signature = proof.get("signature")
        if (
            fact.get("schema") != schema
            or digest != row.get("payload_digest")
            or digest != row.get("result_digest")
            or proof.get("key_id") != authority.key_id
            or not isinstance(signature, str)
            or not authority.verify(purpose, canonical, signature)
        ):
            return None, None
        return fact, digest
    except (KeyError, TypeError, ValueError, UnicodeError):
        return None, None


def verify_persisted_media_write(
    session: Session,
    claim: ClaimAuthority,
    intent: CommittedIntent,
    verified_input: VerifiedHaoaiGroupExecutionInput,
    grant: HaoaiGroupPollGrant,
    plan: OwnedObjectPlan,
    verification: OwnedObjectVerification,
    authority: HmacProofAuthority | None,
) -> bool:
    """Validate the immutable plan and one-write marker before canonical selection."""

    if type(authority) is not HmacProofAuthority:
        return False
    plan_row = session.execute(
        select(ResultEvidenceRow.__table__)
        .where(
            ResultEvidenceRow.__table__.c.evidence_key
            == _PLAN_KEY + intent.external_submission_id
        )
        .with_for_update()
    ).mappings().one_or_none()
    put_row = session.execute(
        select(ResultEvidenceRow.__table__)
        .where(
            ResultEvidenceRow.__table__.c.evidence_key
            == _PUT_STARTED_KEY + intent.external_submission_id
        )
        .with_for_update()
    ).mappings().one_or_none()
    if plan_row is None or put_row is None:
        return False
    plan_fact, plan_digest = _decode_signed_control(
        plan_row,
        evidence_kind=_PLAN_KIND,
        purpose=_PLAN_PURPOSE,
        expected_key=_PLAN_KEY + intent.external_submission_id,
        schema=_PLAN_SCHEMA,
        authority=authority,
    )
    if (
        plan_fact is None
        or plan_digest is None
        or plan_row.get("step_id") != intent.step_id
        or plan_row.get("submission_id") != intent.external_submission_id
        or not _scope_fact_matches(
            session,
            plan_fact,
            claim,
            intent,
            verified_input,
            grant,
            authority,
        )
    ):
        return False
    raw_plan = plan_fact.get("plan")
    if not isinstance(raw_plan, dict):
        return False
    try:
        persisted_plan = OwnedObjectPlan(**raw_plan)
    except (TypeError, ValueError):
        return False
    if (
        persisted_plan != plan
        or not _plan_matches_scope(
            persisted_plan,
            claim,
            intent,
            plan_fact.get("provider_result_digest"),
        )
    ):
        return False
    put_fact, _put_digest = _decode_signed_control(
        put_row,
        evidence_kind=_PUT_STARTED_KIND,
        purpose=_PUT_STARTED_PURPOSE,
        expected_key=_PUT_STARTED_KEY + intent.external_submission_id,
        schema=_PUT_STARTED_SCHEMA,
        authority=authority,
    )
    return bool(
        put_fact is not None
        and put_row.get("step_id") == intent.step_id
        and put_row.get("submission_id") == intent.external_submission_id
        and _scope_fact_matches(
            session,
            put_fact,
            claim,
            intent,
            verified_input,
            grant,
            authority,
        )
        and _original_scope_from_fact(put_fact)
        == _original_scope_from_fact(plan_fact)
        and put_fact.get("plan_fact_digest") == plan_digest
        and put_fact.get("write_id") == persisted_plan.write_id
        and type(verification) is OwnedObjectVerification
        and verification.status == "verified"
        and verification.stored_media is not None
        and verification.stored_media.content_digest
        == persisted_plan.material_sha256
        and verification.stored_media.size_bytes == persisted_plan.size_bytes
        and verification.stored_media.media_type == persisted_plan.media_type
        and verification.observed_sha256 == persisted_plan.material_sha256
        and verification.observed_size_bytes == persisted_plan.size_bytes
    )


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _aware_datetime_or_none(value: object) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _aware_datetime_from_iso(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _database_datetime_from_iso(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return _database_datetime(parsed) if parsed.tzinfo is not None else None


def _database_datetime(value: datetime) -> datetime:
    normalized = _aware_datetime_or_none(value)
    if normalized is None:
        raise ValueError("expected a datetime")
    return normalized.replace(tzinfo=None)


def _material_matches_plan(
    content: bytes,
    media_type: str | None,
    plan: OwnedObjectPlan,
) -> bool:
    return bool(
        type(content) is bytes
        and content
        and hashlib.sha256(content).hexdigest() == plan.material_sha256
        and len(content) == plan.size_bytes
        and media_type == plan.media_type
    )


def _new_uuid_id() -> str:
    return str(uuid.uuid4())


__all__ = (
    "HaoaiBoundContentProvider",
    "HaoaiGroupMediaOutcome",
    "HaoaiGroupOwnedMediaStore",
    "SqlAlchemyHaoaiGroupMedia",
    "verify_persisted_media_write",
)
