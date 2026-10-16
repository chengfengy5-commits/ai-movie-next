"""End-to-end SQL coverage for the opt-in registered frame review path."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import event, insert, select, update
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.app import create_app
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.tables import user_sessions, users
from haoai_backend.authentication.tokens import JoseTokenCodec
from haoai_backend.chat_text_generation import ChatTextGenerationConfiguration
from haoai_backend.chat_text_generation.ports import FrameReviewAdmissionConfiguration
from haoai_backend.generation_core import frame_review_admission
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    ExecutionStepRow,
    TaskSubmissionRow,
    UserCreditRow,
)
from haoai_backend.generation_core.schema import LocalContinuationWorkRow
from haoai_backend.generation_catalog.tables import (
    chapters,
    storyboard_assets,
    system_models,
)
from haoai_backend.generation_credentials.application import (
    CapturedCredentialBinding,
    CredentialRegistry,
)
from haoai_backend.generation_credentials.domain import (
    CredentialScope,
    PreparedPayloadStaged,
    RegistryConflict,
)
from haoai_backend.generation_credentials.persistence import (
    credential_principals,
    prepared_payloads,
)
from haoai_backend.prompts.tables import system_prompts

from generation_core.test_admission_staging import (
    _G2_JOINT_ACTOR_ID,
    _G2_JOINT_MODEL_ID,
    _G2_JOINT_NOW,
    _G2_JOINT_PROVIDER_SECRET,
    _joint_database,
    _joint_registered_capabilities,
    _joint_rotate_system_credential,
)


_JWT_SECRET = "frame-review-admission-test-only"
_SESSION_ID = "frame-review-session"
_TOKEN_ID = "frame-review-jti"
_CHAPTER_CONTENT = [
    {"text": "原始分镜正文", "storyboard": ["review-storyboard-1"], "custom": {"keep": True}},
    {"text": "非目标帧", "storyboard": ["review-storyboard-2"]},
]


class _Ids:
    def __init__(self) -> None:
        self._counter = 0

    def __call__(self) -> str:
        self._counter += 1
        return f"frame-review-id-{self._counter}"


def _review_body() -> dict[str, object]:
    return {
        "chapter_id": "joint-chapter-1",
        "frame_index": 0,
        "prompt_id": "review-prompt-1",
        "model_id": _G2_JOINT_MODEL_ID,
        "message_id": "source-message-1",
        "error_reason": "请核实动作方向",
        "asset_context": "角色站在门口",
        "prev_context": "角色刚走到走廊尽头",
        "enable_thinking": True,
    }


async def _post_review(
    app,
    token: str,
    body: dict[str, object] | None = None,
) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://frame-review.test",
    ) as client:
        return await client.post(
            "/api/chat/ai-review",
            headers={"Authorization": f"Bearer {token}"},
            json=_review_body() if body is None else body,
        )


def _build_app(
    tmp_path: Path,
    monkeypatch,
    *,
    chapter_content: list[object] | None = None,
    storyboard_asset_ids: tuple[str, ...] = (
        "review-storyboard-1",
        "review-storyboard-2",
    ),
    max_user_concurrency: int = 0,
    cost_per_call: int = 3,
    wallet_credits: int | None = 500,
    rotate_credentials: bool = False,
):
    import generation_core.test_admission_staging as admission_fixtures

    monkeypatch.setattr(admission_fixtures, "_G2_JOINT_PROVIDER", "openai")
    engine = _joint_database(
        tmp_path,
        model_source=admission_fixtures.ModelSource.SYSTEM,
        wallet_credits=wallet_credits,
    )
    stamp = _G2_JOINT_NOW.replace(tzinfo=None)
    selected_chapter_content = _CHAPTER_CONTENT if chapter_content is None else chapter_content
    with engine.begin() as connection:
        connection.execute(
            update(system_models)
            .where(system_models.c.id == _G2_JOINT_MODEL_ID)
            .values(
                category="text",
                provider="openai",
                model="review-wire-model",
                params=json.dumps({"temperature": 0.2, "max_tokens": 800}),
                cost_per_call=cost_per_call,
            )
        )
        connection.execute(
            update(admission_fixtures.catalog_chapters)
            .where(admission_fixtures.catalog_chapters.c.id == "joint-chapter-1")
            .values(content=json.dumps(selected_chapter_content, ensure_ascii=False))
        )
        for index, storyboard_id in enumerate(storyboard_asset_ids):
            connection.execute(
                insert(storyboard_assets).values(
                    id=storyboard_id,
                    series_id="joint-series-1",
                    chapter_id="joint-chapter-1",
                    frame_index=index,
                    name=f"Review frame {index}",
                    description=None,
                    image_url=None,
                    updated_at=stamp,
                )
            )
        connection.execute(
            insert(system_prompts).values(
                id="review-prompt-1",
                category="ai-review",
                name="Review prompt",
                system_prompt="请审阅分镜文本。",
                user_prompt="请保持人物和场景连续。",
                sort_order=0,
                is_active=True,
                created_at=stamp,
                updated_at=stamp,
            )
        )
        connection.execute(
            insert(user_sessions).values(
                id=_SESSION_ID,
                user_id=_G2_JOINT_ACTOR_ID,
                jti=_TOKEN_ID,
                user_agent="frame review local integration",
                ip_address="127.0.0.1",
                device_name="synthetic fixture",
                is_active=True,
                created_at=stamp,
                last_seen_at=stamp,
                revoked_at=None,
            )
        )

    capabilities, _crypto_state = _joint_registered_capabilities(
        engine,
        model_source=admission_fixtures.ModelSource.SYSTEM,
    )
    if rotate_credentials:
        capabilities = _joint_rotate_system_credential(
            SimpleNamespace(
                engine=engine,
                capabilities=capabilities,
                crypto_state=_crypto_state,
            )
        )
        # The rotation helper deliberately mutates current catalog and membership
        # facts for its resolver tests. Restore the review fixture while retaining
        # the legitimate rotated credential head and matching API-key identity.
        with engine.begin() as connection:
            connection.execute(
                update(system_models)
                .where(system_models.c.id == _G2_JOINT_MODEL_ID)
                .values(
                    name="Public joint-fixture model",
                    model="review-wire-model",
                    params=json.dumps({"temperature": 0.2, "max_tokens": 800}),
                    cost_per_call=cost_per_call,
                )
            )
            connection.execute(
                update(users)
                .where(users.c.id == _G2_JOINT_ACTOR_ID)
                .values(membership_type="premium", membership_expires_at=None)
            )
    sessions = sessionmaker(engine, expire_on_commit=False)
    token_codec = JoseTokenCodec(_JWT_SECRET)
    authentication_runtime = AuthenticationRuntime(
        session_factory=sessions,
        secret_key=_JWT_SECRET,
        token_codec=token_codec,
    )
    ids = _Ids()
    app = create_app(
        session_factory=sessions,
        authentication_runtime=authentication_runtime,
        chat_text_generation_configuration=ChatTextGenerationConfiguration(
            runtime=None,
            max_user_concurrency=max_user_concurrency,
            server_id_factory=ids,
            clock=lambda: _G2_JOINT_NOW,
            frame_review_admission=FrameReviewAdmissionConfiguration(capabilities),
        ),
    )
    token = token_codec.encode(
        {
            "sub": _G2_JOINT_ACTOR_ID,
            "jti": _TOKEN_ID,
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        }
    )
    return engine, app, token, capabilities, _crypto_state


def test_opted_in_review_post_persists_one_frozen_post_apply_graph_without_debit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, app, token, _, _ = _build_app(tmp_path, monkeypatch)
    try:
        async def send() -> tuple[httpx.Response, httpx.Response, httpx.Response]:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://frame-review.test",
            ) as client:
                response = await client.post(
                    "/api/chat/ai-review",
                    headers={"Authorization": f"Bearer {token}"},
                    json=_review_body(),
                )
                if response.status_code != 200:
                    return response, response, response
                headers = {"Authorization": f"Bearer {token}"}
                status = await client.get(
                    f"/api/chat/ai-review/{response.json()['task_id']}", headers=headers
                )
                counts = await client.get(
                    "/api/chat/ai-review/counts",
                    params={"message_id": "source-message-1"},
                    headers=headers,
                )
                return response, status, counts

        response, status, counts = asyncio.run(send())
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["task_id"]
        assert body["model_name"] == "Public joint-fixture model"
        assert body["is_system"] is True
        assert body["credit_cost"] == 3
        assert status.status_code == counts.status_code == 200
        assert status.json()["id"] == body["task_id"]
        assert status.json()["type"] == "ai-review"
        assert status.json()["status"] == "queued"
        assert status.json()["credit_cost"] == 0
        assert counts.json() == {"counts": {"review-prompt-1": 1}}

        with engine.connect() as connection:
            tasks = connection.execute(select(AITaskRow.__table__)).mappings().all()
            units = connection.execute(select(BillingUnitRow.__table__)).mappings().all()
            steps = connection.execute(select(ExecutionStepRow.__table__)).mappings().all()
            submissions = connection.execute(select(TaskSubmissionRow.__table__)).mappings().all()
            wallet = connection.execute(
                select(UserCreditRow.__table__.c.credits)
            ).scalar_one()
            logs = connection.execute(select(CreditLogRow.__table__)).mappings().all()
            work = connection.execute(
                select(LocalContinuationWorkRow.__table__)
            ).mappings().all()
            payload_rows = connection.execute(select(prepared_payloads)).mappings().all()

        assert len(tasks) == len(units) == len(steps) == len(submissions) == 1
        task = tasks[0]
        unit = units[0]
        step = steps[0]
        assert task["id"] == body["task_id"]
        assert task["type"] == "ai-review" and task["status"] == "queued"
        assert task["credit_cost"] == 0
        request_data = json.loads(task["request_data"])
        assert request_data["operation"] == "frame.review"
        assert request_data["graph_version"] == "frame-review-v1"
        assert request_data["source_message_id"] == "source-message-1"
        assert request_data["prompt_id"] == "review-prompt-1"
        assert "请核实动作方向" not in task["request_data"]
        assert "原始分镜正文" not in task["request_data"]
        assert _G2_JOINT_PROVIDER_SECRET not in task["request_data"]
        expected_text_digest = hashlib.sha256(
            json.dumps(
                "原始分镜正文",
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        assert request_data["target_snapshot"]["expected_text_digest"] == expected_text_digest
        assert unit["operation"] == "frame.review"
        assert unit["quoted_amount"] == 3
        assert unit["billing_status"] == "unbilled"
        assert unit["debited_amount"] == 0
        assert step["step_key"] == "generate" and step["required"] is True
        assert step["execution_status"] == "prepared"
        assert wallet == 500
        assert logs == []
        assert work == []
        assert len(payload_rows) == 1
        assert payload_rows[0]["operation"] == "frame.review"
        assert payload_rows[0]["ciphertext"]
        assert _G2_JOINT_PROVIDER_SECRET.encode("utf-8") not in payload_rows[0]["ciphertext"]
    finally:
        engine.dispose()


def test_review_persists_both_independent_snapshot_digests_and_v1_aad_bytes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import generation_core.test_admission_staging as admission_fixtures

    original_crypto_builder = admission_fixtures._joint_local_dummy_crypto
    sealed_review_inputs: list[tuple[str, bytes]] = []

    def recording_crypto_builder(secrets_by_reference):
        crypto, state = original_crypto_builder(secrets_by_reference)
        original_seal = crypto.seal

        def record_review_aad(plaintext: str, associated_data: bytes):
            try:
                decoded = json.loads(plaintext)
            except (TypeError, ValueError):
                decoded = None
            if isinstance(decoded, dict) and decoded.get("schema") == "frame-review-snapshot-v1":
                sealed_review_inputs.append((plaintext, associated_data))
            return original_seal(plaintext, associated_data)

        return replace(crypto, seal=record_review_aad), state

    monkeypatch.setattr(
        admission_fixtures,
        "_joint_local_dummy_crypto",
        recording_crypto_builder,
    )
    engine, app, token, _, _ = _build_app(tmp_path, monkeypatch)
    sealed_review_inputs.clear()
    try:
        response = asyncio.run(_post_review(app, token))
        assert response.status_code == 200, response.text
        with engine.connect() as connection:
            task = connection.execute(
                select(AITaskRow.__table__).where(
                    AITaskRow.__table__.c.id == response.json()["task_id"]
                )
            ).mappings().one()
            unit = connection.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.__table__.c.task_id == response.json()["task_id"]
                )
            ).mappings().one()
            step = connection.execute(
                select(ExecutionStepRow.__table__).where(
                    ExecutionStepRow.__table__.c.billing_unit_id
                    == unit["id"]
                )
            ).mappings().one()
            payload = connection.execute(select(prepared_payloads)).mappings().one()

        request_data = json.loads(task["request_data"])
        step_snapshot = step["input_snapshot"]
        assert set(step_snapshot) == {
            "server_attempt_id",
            "prepared_payload_id",
            "protected_ref",
            "snapshot_digest",
            "manifest_snapshot_digest",
        }
        assert step_snapshot["snapshot_digest"] == request_data["snapshot_digest"]
        assert step_snapshot["manifest_snapshot_digest"] == request_data[
            "manifest_snapshot_digest"
        ]
        assert request_data["snapshot_digest"] != request_data["manifest_snapshot_digest"]
        assert len(sealed_review_inputs) == 1

        plaintext, actual_associated_data = sealed_review_inputs[0]
        protected_payload = json.loads(plaintext)
        execution_snapshot = protected_payload["execution_snapshot"]
        def canonical(value: object) -> bytes:
            return json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        expected_manifest_digest = hashlib.sha256(canonical(execution_snapshot)).hexdigest()
        assert expected_manifest_digest == request_data["manifest_snapshot_digest"]
        assert protected_payload["manifest"]["snapshot_digest"] == expected_manifest_digest

        manifest = {
            "purpose": "prepared-frame-review-input-v1",
            "owner_id": task["user_id"],
            "operation": "frame.review",
            "submission_id": request_data["submission_id"],
            "task_id": task["id"],
            "unit_id": unit["id"],
            "step_id": step["id"],
            "server_attempt_id": request_data["server_attempt_id"],
            "prepared_payload_id": request_data["prepared_payload_id"],
            "request_digest": request_data["request_digest"],
            "rule_digest": request_data["rule_digest"],
            "snapshot_digest": expected_manifest_digest,
            "target_ref": payload["target_ref"],
            "model_ref": payload["model_ref"],
            "grant_ref": payload["grant_ref"],
        }
        expected_aad = {
            "purpose": "protected-frame-review-input-v1",
            **manifest,
            "scope": {
                "source": payload["source"],
                "row_id": payload["row_id"],
                "owner_or_system": payload["owner_or_system"],
                "provider": payload["provider"],
            },
            "binding_id": payload["binding_id"],
            "binding_version": payload["binding_version"],
            "principal_id": payload["principal_id"],
            "schema_version": payload["schema_version"],
            "projector_version": payload["projector_version"],
            "reference_policy_version": payload["reference_policy_version"],
            "target_ref": payload["target_ref"],
            "model_ref": payload["model_ref"],
            "grant_ref": payload["grant_ref"],
        }
        assert expected_aad["purpose"] == "prepared-frame-review-input-v1"
        assert actual_associated_data == canonical(expected_aad)
    finally:
        engine.dispose()


def test_payload_stage_failure_rolls_back_every_admission_row(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, app, token, _, _ = _build_app(tmp_path, monkeypatch)
    original = CredentialRegistry.stage_prepared_payload

    def stage_then_refuse(self, session, payload, *, payload_id):
        result = original(self, session, payload, payload_id=payload_id)
        assert isinstance(result, PreparedPayloadStaged)
        return RegistryConflict("test_refusal", "synthetic stage refusal")

    monkeypatch.setattr(CredentialRegistry, "stage_prepared_payload", stage_then_refuse)
    try:
        response = asyncio.run(_post_review(app, token))
        assert response.status_code == 503
        with engine.connect() as connection:
            assert connection.execute(select(AITaskRow.__table__)).all() == []
            assert connection.execute(select(BillingUnitRow.__table__)).all() == []
            assert connection.execute(select(ExecutionStepRow.__table__)).all() == []
            assert connection.execute(select(TaskSubmissionRow.__table__)).all() == []
            assert connection.execute(select(prepared_payloads)).all() == []
            assert connection.execute(
                select(UserCreditRow.__table__.c.credits)
            ).scalar_one() == 500
            assert connection.execute(select(CreditLogRow.__table__)).all() == []
    finally:
        engine.dispose()


def test_ambiguous_stable_storyboard_refuses_admission_without_partial_rows(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, app, token, _, _ = _build_app(tmp_path, monkeypatch)
    with engine.begin() as connection:
        connection.execute(
            update(chapters)
            .where(chapters.c.id == "joint-chapter-1")
            .values(
                content=json.dumps(
                    [
                        {"text": "原始分镜正文", "storyboard": ["review-storyboard-1"]},
                        {"text": "重复目标", "storyboard": ["review-storyboard-1"]},
                    ],
                    ensure_ascii=False,
                )
            )
        )
    try:
        response = asyncio.run(_post_review(app, token))
        assert response.status_code == 503
        with engine.connect() as connection:
            assert connection.execute(select(AITaskRow.__table__)).all() == []
            assert connection.execute(select(BillingUnitRow.__table__)).all() == []
            assert connection.execute(select(ExecutionStepRow.__table__)).all() == []
            assert connection.execute(select(TaskSubmissionRow.__table__)).all() == []
            assert connection.execute(select(prepared_payloads)).all() == []
            assert connection.execute(
                select(UserCreditRow.__table__.c.credits)
            ).scalar_one() == 500
    finally:
        engine.dispose()


def test_commit_acknowledgement_loss_confirms_graph_without_reinsertion(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, app, token, _, _ = _build_app(tmp_path, monkeypatch)
    staged_sessions: list[Session] = []
    original_stage = frame_review_admission._stage_review
    original_commit = Session.commit
    commit_error_injected = False

    def capture_writer(session, **kwargs):
        staged = original_stage(session, **kwargs)
        staged_sessions.append(session)
        return staged

    def commit_then_lose_ack(session):
        nonlocal commit_error_injected
        result = original_commit(session)
        if staged_sessions and session is staged_sessions[0] and not commit_error_injected:
            commit_error_injected = True
            raise RuntimeError("synthetic commit acknowledgement loss")
        return result

    monkeypatch.setattr(frame_review_admission, "_stage_review", capture_writer)
    monkeypatch.setattr(Session, "commit", commit_then_lose_ack)
    try:
        response = asyncio.run(_post_review(app, token))
        assert commit_error_injected is True
        assert response.status_code == 200, response.text
        with engine.connect() as connection:
            assert len(connection.execute(select(AITaskRow.__table__)).all()) == 1
            assert len(connection.execute(select(BillingUnitRow.__table__)).all()) == 1
            assert len(connection.execute(select(ExecutionStepRow.__table__)).all()) == 1
            assert len(connection.execute(select(TaskSubmissionRow.__table__)).all()) == 1
            assert len(connection.execute(select(prepared_payloads)).all()) == 1
            assert connection.execute(
                select(UserCreditRow.__table__.c.credits)
            ).scalar_one() == 500
            assert connection.execute(select(CreditLogRow.__table__)).all() == []
    finally:
        engine.dispose()


def test_commit_acknowledgement_does_not_confirm_a_changed_manifest_digest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, app, token, _, _ = _build_app(tmp_path, monkeypatch)
    staged_sessions: list[Session] = []
    staged_reviews: list[object] = []
    original_stage = frame_review_admission._stage_review
    original_commit = Session.commit
    commit_error_injected = False

    def capture_writer(session, **kwargs):
        staged = original_stage(session, **kwargs)
        staged_sessions.append(session)
        staged_reviews.append(staged)
        return staged

    def commit_corrupt_step_then_lose_ack(session):
        nonlocal commit_error_injected
        if staged_sessions and session is staged_sessions[0] and not commit_error_injected:
            staged = staged_reviews[0]
            step = session.execute(
                select(ExecutionStepRow).where(ExecutionStepRow.id == staged.step_id)
            ).scalar_one()
            corrupted_snapshot = dict(step.input_snapshot)
            corrupted_snapshot["manifest_snapshot_digest"] = "0" * 64
            step.input_snapshot = corrupted_snapshot
            original_commit(session)
            commit_error_injected = True
            raise RuntimeError("synthetic commit acknowledgement loss")
        return original_commit(session)

    monkeypatch.setattr(frame_review_admission, "_stage_review", capture_writer)
    monkeypatch.setattr(Session, "commit", commit_corrupt_step_then_lose_ack)
    try:
        response = asyncio.run(_post_review(app, token))
        assert commit_error_injected is True
        assert response.status_code == 503
        with engine.connect() as connection:
            assert len(connection.execute(select(AITaskRow.__table__)).all()) == 1
            assert len(connection.execute(select(BillingUnitRow.__table__)).all()) == 1
            step = connection.execute(
                select(ExecutionStepRow.__table__)
            ).mappings().one()
            assert step["input_snapshot"]["manifest_snapshot_digest"] == "0" * 64
            assert connection.execute(
                select(UserCreditRow.__table__.c.credits)
            ).scalar_one() == 500
            assert connection.execute(select(CreditLogRow.__table__)).all() == []
    finally:
        engine.dispose()


def test_legitimate_credential_rotation_can_be_captured_and_admitted(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, app, token, capabilities, crypto_state = _build_app(
        tmp_path,
        monkeypatch,
        rotate_credentials=True,
    )
    scope = CredentialScope(
        source="system",
        row_id=_G2_JOINT_MODEL_ID,
        owner_or_system="system",
        provider="openai",
    )
    read_sessions = sessionmaker(engine, expire_on_commit=False)
    secret_resolution_counts = tuple(
        crypto_state[key]
        for key in ("unseal_calls", "secret_handle_calls", "legacy_value_calls")
    )
    try:
        with read_sessions() as session:
            captured = CredentialRegistry(capabilities).capture_original_binding(
                session, scope
            )

        assert isinstance(captured, CapturedCredentialBinding)
        assert captured.binding.binding_version == "2"
        assert captured.binding.original_principal_id
        assert captured.binding.rotation_provenance == "joint-fixture-rotation-proof-2"
        assert not hasattr(captured, "secret")
        assert tuple(
            crypto_state[key]
            for key in ("unseal_calls", "secret_handle_calls", "legacy_value_calls")
        ) == secret_resolution_counts

        with engine.connect() as connection:
            principal_record_id = connection.execute(
                select(credential_principals.c.record_id)
            ).scalar_one()
        assert principal_record_id == "joint-fixture-record-1"

        response = asyncio.run(_post_review(app, token))
        assert response.status_code == 200, response.text
        with engine.connect() as connection:
            tasks = connection.execute(select(AITaskRow.__table__)).mappings().all()
            units = connection.execute(select(BillingUnitRow.__table__)).mappings().all()
            payload_rows = connection.execute(select(prepared_payloads)).mappings().all()
            wallet = connection.execute(
                select(UserCreditRow.__table__.c.credits)
            ).scalar_one()
            logs = connection.execute(select(CreditLogRow.__table__)).all()
        assert len(tasks) == len(units) == len(payload_rows) == 1
        assert wallet == 500
        assert logs == []
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "invalid_evidence",
    ["wrong_subject", "disabled", "missing_rotation_proof"],
)
def test_rotated_capture_refuses_invalid_principal_evidence(
    tmp_path: Path,
    monkeypatch,
    invalid_evidence: str,
) -> None:
    engine, _app, _token, capabilities, _crypto_state = _build_app(
        tmp_path,
        monkeypatch,
        rotate_credentials=True,
    )
    scope = CredentialScope(
        source="system",
        row_id=_G2_JOINT_MODEL_ID,
        owner_or_system="system",
        provider="openai",
    )
    if invalid_evidence == "missing_rotation_proof":
        source = capabilities.enrollment_source
        assert source is not None
        capabilities = replace(
            capabilities,
            enrollment_source=replace(source, rotation_attestations=()),
        )
    else:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "DROP TRIGGER trg_generation_credential_principals_immutable_identity_update"
            )
            if invalid_evidence == "wrong_subject":
                connection.execute(
                    update(credential_principals).values(
                        stable_account_subject="wrong-account-subject"
                    )
                )
            else:
                connection.execute(
                    update(credential_principals).values(source_state="revoked")
                )

    try:
        with sessionmaker(engine, expire_on_commit=False)() as session:
            captured = CredentialRegistry(capabilities).capture_original_binding(
                session, scope
            )
        assert captured is None
    finally:
        engine.dispose()


def test_no_id_frame_is_frozen_and_admitted_without_asset_lookup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    frames: list[object] = [
        {"text": "无稳定ID的原始帧", "custom": {"keep": True}},
        {"text": "另一个无ID帧"},
    ]
    engine, app, token, _, _ = _build_app(
        tmp_path,
        monkeypatch,
        chapter_content=frames,
        storyboard_asset_ids=(),
    )
    graph_items = []
    sql_statements: list[str] = []
    original_builder = frame_review_admission.build_operation_graph_plan

    def observe_graph(*args, **kwargs):
        graph_items.extend(kwargs["items"])
        return original_builder(*args, **kwargs)

    def observe_sql(_connection, _cursor, statement, _parameters, _context, _many):
        sql_statements.append(statement.lower())

    monkeypatch.setattr(frame_review_admission, "build_operation_graph_plan", observe_graph)
    event.listen(engine, "before_cursor_execute", observe_sql)
    try:
        response = asyncio.run(_post_review(app, token))
        assert response.status_code == 200, response.text
        with engine.connect() as connection:
            tasks = connection.execute(select(AITaskRow.__table__)).mappings().all()
            units = connection.execute(select(BillingUnitRow.__table__)).all()
            steps = connection.execute(select(ExecutionStepRow.__table__)).all()
            submissions = connection.execute(select(TaskSubmissionRow.__table__)).all()
            payload_rows = connection.execute(select(prepared_payloads)).all()
            wallet = connection.execute(
                select(UserCreditRow.__table__.c.credits)
            ).scalar_one()
            logs = connection.execute(select(CreditLogRow.__table__)).all()

        assert (
            len(tasks)
            == len(units)
            == len(steps)
            == len(submissions)
            == len(payload_rows)
            == 1
        )
        request_data = json.loads(tasks[0]["request_data"])
        target = request_data["target_snapshot"]
        assert target["stable_storyboard_asset_id"] is None
        assert target["compat_frame_index"] == 0
        assert target["expected_text_digest"] == _digest_json("无稳定ID的原始帧")
        assert target["full_frame_digest"] == _digest_json(frames[0])
        assert target["full_chapter_digest"] == _digest_json(frames)
        assert len(graph_items) == 1
        assert graph_items[0].target_id == request_data["target_ref"]
        assert graph_items[0].target_id.startswith("prepared-payload-v1/")
        assert not any(
            "storyboard_assets.id =" in statement
            for statement in sql_statements
        )
        assert wallet == 500
        assert logs == []
    finally:
        event.remove(engine, "before_cursor_execute", observe_sql)
        engine.dispose()


@pytest.mark.parametrize(
    ("target_storyboard_id", "move_asset_to_other_chapter"),
    (("missing-storyboard-id", False), ("review-storyboard-1", True)),
)
def test_nonempty_dangling_or_cross_chapter_storyboard_id_is_rejected(
    tmp_path: Path,
    monkeypatch,
    target_storyboard_id: str,
    move_asset_to_other_chapter: bool,
) -> None:
    frames = [
        {"text": "原始分镜正文", "storyboard": [target_storyboard_id]},
        {"text": "非目标帧", "storyboard": ["review-storyboard-2"]},
    ]
    engine, app, token, _, _ = _build_app(
        tmp_path,
        monkeypatch,
        chapter_content=frames,
    )
    if move_asset_to_other_chapter:
        with engine.begin() as connection:
            connection.execute(
                update(storyboard_assets)
                .where(storyboard_assets.c.id == target_storyboard_id)
                .values(chapter_id="other-chapter")
            )
    try:
        response = asyncio.run(_post_review(app, token))
        assert response.status_code == 503
        with engine.connect() as connection:
            assert connection.execute(select(AITaskRow.__table__)).all() == []
            assert connection.execute(select(BillingUnitRow.__table__)).all() == []
            assert connection.execute(select(ExecutionStepRow.__table__)).all() == []
            assert connection.execute(select(TaskSubmissionRow.__table__)).all() == []
            assert connection.execute(select(prepared_payloads)).all() == []
            assert connection.execute(
                select(UserCreditRow.__table__.c.credits)
            ).scalar_one() == 500
            assert connection.execute(select(CreditLogRow.__table__)).all() == []
    finally:
        engine.dispose()


def test_parent_concurrency_limit_applies_to_opted_in_review(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, app, token, _, _ = _build_app(
        tmp_path,
        monkeypatch,
        max_user_concurrency=1,
    )
    try:
        async def submit_twice() -> tuple[httpx.Response, httpx.Response]:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://frame-review.test",
            ) as client:
                headers = {"Authorization": f"Bearer {token}"}
                first = await client.post(
                    "/api/chat/ai-review", headers=headers, json=_review_body()
                )
                second = await client.post(
                    "/api/chat/ai-review", headers=headers, json=_review_body()
                )
                return first, second

        first, second = asyncio.run(submit_twice())
        assert first.status_code == 200, first.text
        assert second.status_code == 429, second.text
        with engine.connect() as connection:
            tasks = connection.execute(select(AITaskRow.__table__)).all()
            units = connection.execute(select(BillingUnitRow.__table__)).all()
            steps = connection.execute(select(ExecutionStepRow.__table__)).all()
            submissions = connection.execute(select(TaskSubmissionRow.__table__)).all()
            payload_rows = connection.execute(select(prepared_payloads)).all()
            wallet = connection.execute(
                select(UserCreditRow.__table__.c.credits)
            ).scalar_one()
            logs = connection.execute(select(CreditLogRow.__table__)).all()
        assert (
            len(tasks)
            == len(units)
            == len(steps)
            == len(submissions)
            == len(payload_rows)
            == 1
        )
        assert wallet == 500
        assert logs == []
    finally:
        engine.dispose()


def test_zero_cost_review_does_not_create_a_missing_wallet(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, app, token, _, _ = _build_app(
        tmp_path,
        monkeypatch,
        cost_per_call=0,
        wallet_credits=None,
    )
    try:
        response = asyncio.run(_post_review(app, token))
        assert response.status_code == 200, response.text
        with engine.connect() as connection:
            tasks = connection.execute(select(AITaskRow.__table__)).mappings().all()
            units = connection.execute(select(BillingUnitRow.__table__)).mappings().all()
            wallets = connection.execute(select(UserCreditRow.__table__)).all()
            logs = connection.execute(select(CreditLogRow.__table__)).all()
        assert len(tasks) == len(units) == 1
        assert units[0]["quoted_amount"] == 0
        assert wallets == []
        assert logs == []
    finally:
        engine.dispose()


def _digest_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
