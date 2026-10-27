from __future__ import annotations

import json

import pytest
from sqlalchemy import select, update

from backend.tests import test_server_media_storage as server_fixtures
from haoai_backend.authentication.tables import user_credits
from haoai_backend.fused_generation.durable_application import (
    load_durable_prepared_snapshot,
    submit_durable_fused_generation,
)
from haoai_backend.fused_generation import durable_application
from haoai_backend.fused_generation.errors import FusedGenerationBadRequest
from haoai_backend.fused_generation.persistence import fused_generation_uow_factory
from haoai_backend.fused_generation.persistence import SqlAlchemyFusedGenerationUnitOfWork
from haoai_backend.generation_core.base_schema import AITaskRow, CreditLogRow
from haoai_backend.generation_core.schema import FusedGenerationSubmissionRow
from haoai_backend.series_data.tables import chapters
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.fused_generation.schemas import FusedAssetGenerateRequest


def test_http_target_guard_uses_real_storyboard_relationship_before_fee(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response_body, _ = server_fixtures._fused_provider_response("url")
    provider_client, _ = server_fixtures._install_fused_fake_transports(monkeypatch, response_body)
    configuration = server_fixtures._fused_server_configuration(tmp_path)
    sdk_session, sdk_config, _ = server_fixtures._sdk_factories()
    host = server_fixtures.create_server_host(configuration, sdk_factories=(sdk_session, sdk_config))
    token = server_fixtures._seed_fused_server_database(
        host.app.state.server_engine,
        fused_configuration=host.app.state.server_fused_generation_configuration,
    )
    expected = {"storyboard": ["shot-a"], "text": "A", "unknown": {"order": [1, True]}}
    body = {
        **server_fixtures._fused_request_body(),
        "chapter_id": "chapter-fused-server",
        "frame_index": 0,
        "storyboard_asset_id": "shot-a",
        "expected_frame": expected,
    }

    try:
        for index, content in enumerate((
            [{"storyboard": ["shot-a"], "text": "B", "unknown": {"order": [1, True]}}],
            [],
            [expected, expected],
            [{"storyboard": "shot-a", "text": "A", "unknown": {"order": [1, True]}}],
        )):
            with host.app.state.server_engine.begin() as connection:
                connection.execute(
                    update(chapters).where(chapters.c.id == "chapter-fused-server").values(
                        content=json.dumps(content, ensure_ascii=False, separators=(",", ":"))
                    )
                )
            rejected = server_fixtures._post_fused_generation(
                host.app,
                token,
                body,
                idempotency_key=f"target-proof-rejected-{index}-case-1128",
            )
            assert rejected.status_code == 409, rejected.text

        with host.app.state.server_engine.connect() as connection:
            balance = connection.execute(
                select(user_credits.c.credits).where(user_credits.c.user_id == "user-server-test")
            ).scalar_one()
            debit_logs = connection.execute(
                select(CreditLogRow.__table__.c.type, CreditLogRow.__table__.c.amount)
            ).all()
            tasks = connection.execute(select(AITaskRow.__table__)).mappings().all()
            submissions = connection.execute(select(FusedGenerationSubmissionRow.__table__)).mappings().all()
        assert balance == 20
        assert debit_logs == []
        assert tasks == []
        assert submissions == []
        assert provider_client.calls == []

        reordered = [
            {"storyboard": ["shot-b"], "text": "B"},
            {**expected, "fused": [{"id": "prior-result"}]},
        ]
        with host.app.state.server_engine.begin() as connection:
            connection.execute(
                update(chapters).where(chapters.c.id == "chapter-fused-server").values(
                    content=json.dumps(reordered, ensure_ascii=False, separators=(",", ":"))
                )
            )
        accepted = server_fixtures._post_fused_generation(
            host.app,
            token,
            body,
            idempotency_key="target-proof-reordered-accepted-case-1128",
        )
        assert accepted.status_code == 200, accepted.text

        with host.app.state.server_engine.connect() as connection:
            balance = connection.execute(
                select(user_credits.c.credits).where(user_credits.c.user_id == "user-server-test")
            ).scalar_one()
            debit_logs = connection.execute(
                select(CreditLogRow.__table__.c.type, CreditLogRow.__table__.c.amount)
            ).all()
            row = connection.execute(select(FusedGenerationSubmissionRow.__table__)).mappings().one()
        prepared = load_durable_prepared_snapshot(
            row,
            crypto=host.app.state.server_fused_generation_configuration.credential_crypto,
            proof_authority=host.app.state.server_fused_generation_configuration.proof_authority,
        )
        assert prepared is not None and prepared.target_frame is not None
        assert prepared.target_frame.frame_index == 1
        assert json.loads(prepared.target_frame.identity) == {"storyboard": ["shot-a"], "text": "A", "unknown": {"order": [1, True]}}
        assert balance == 16
        assert [(entry.type, entry.amount) for entry in debit_logs] == [("usage", -4)]
        assert len(provider_client.calls) == 1
    finally:
        host.close()


@pytest.mark.parametrize("stage", ["accepted", "fee_committed", "snapshot_ready", "rejected_after_charge"])
def test_existing_legacy_key_replays_durable_fact_after_schema_expansion(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    configuration = server_fixtures._fused_server_configuration(tmp_path)
    sdk_session, sdk_config, _ = server_fixtures._sdk_factories()
    host = server_fixtures.create_server_host(configuration, sdk_factories=(sdk_session, sdk_config))
    fused_configuration = host.app.state.server_fused_generation_configuration
    server_fixtures._seed_fused_server_database(
        host.app.state.server_engine,
        fused_configuration=fused_configuration,
    )
    uow_factory = fused_generation_uow_factory(
        host.app.state.server_session_factory,
        decrypt_api_key=server_fixtures.LegacyFernet(server_fixtures._SECRET).decrypt,
    )
    body = server_fixtures._fused_request_body()
    request = FusedAssetGenerateRequest.model_validate(body)
    key = f"legacy-replay-{stage}-case-1128"
    try:
        original_accept = SqlAlchemyFusedGenerationUnitOfWork.accept_durable_submission
        original_resolve_prompt = durable_application._resolve_prompt
        if stage == "snapshot_ready":
            monkeypatch.setattr(
                SqlAlchemyFusedGenerationUnitOfWork,
                "accept_durable_submission",
                lambda self, prepared, *, response_status=200, response_json: False,
            )
        if stage == "rejected_after_charge":
            monkeypatch.setattr(
                durable_application,
                "_resolve_prompt",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    FusedGenerationBadRequest("合成提示词拒绝")
                ),
            )
        original = submit_durable_fused_generation(
            uow_factory,
            registry=fused_configuration.credential_registry,
            crypto=fused_configuration.credential_crypto,
            proof_authority=fused_configuration.proof_authority,
            actor=TrustedActor("user-server-test"),
            request=request,
            idempotency_key=key,
        )
        if stage == "snapshot_ready":
            monkeypatch.setattr(
                SqlAlchemyFusedGenerationUnitOfWork,
                "accept_durable_submission",
                original_accept,
            )
        if stage == "rejected_after_charge":
            monkeypatch.setattr(durable_application, "_resolve_prompt", original_resolve_prompt)
        assert original.status_code in {200, 202, 400}
        with host.app.state.server_engine.begin() as connection:
            row = connection.execute(select(FusedGenerationSubmissionRow.__table__)).mappings().one()
            expected_initial_stage = {
                "rejected_after_charge": "rejected_after_charge",
                "snapshot_ready": "snapshot_ready",
                "fee_committed": "accepted",
                "accepted": "accepted",
            }[stage]
            assert row["stage"] == expected_initial_stage
            if stage in {"fee_committed", "snapshot_ready"}:
                connection.execute(
                    update(FusedGenerationSubmissionRow.__table__)
                    .where(FusedGenerationSubmissionRow.__table__.c.submission_id == row["submission_id"])
                    .values(stage=stage)
                )

        # Revalidation under the expanded Pydantic schema adds no None proof fields
        # to the historical canonical digest.
        expanded_request = FusedAssetGenerateRequest.model_validate(body)
        replay = submit_durable_fused_generation(
            uow_factory,
            registry=fused_configuration.credential_registry,
            crypto=fused_configuration.credential_crypto,
            proof_authority=fused_configuration.proof_authority,
            actor=TrustedActor("user-server-test"),
            request=expanded_request,
            idempotency_key=key,
        )
        with host.app.state.server_engine.connect() as connection:
            balance = connection.execute(
                select(user_credits.c.credits).where(user_credits.c.user_id == "user-server-test")
            ).scalar_one()
            debit_logs = connection.execute(
                select(CreditLogRow.__table__.c.type, CreditLogRow.__table__.c.amount)
            ).all()
            rows = connection.execute(select(FusedGenerationSubmissionRow.__table__)).mappings().all()
        assert len(rows) == 1
        assert rows[0]["task_id"] == row["task_id"]
        assert rows[0]["request_digest"] == row["request_digest"]
        assert balance == 16
        assert [(entry.type, entry.amount) for entry in debit_logs] == [("usage", -4)]
        if stage == "accepted":
            assert replay.body == original.body
        elif stage == "fee_committed":
            assert replay.status_code == 202 and replay.body["status"] == "charged_incomplete"
        elif stage == "snapshot_ready":
            assert replay.status_code in {200, 202} and replay.body.get("task_id") == row["task_id"]
        else:
            assert replay.status_code == original.status_code == 400
            assert replay.body == original.body
    finally:
        host.close()
