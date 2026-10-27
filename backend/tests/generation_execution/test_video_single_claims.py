from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import test_admission_staging as joint_fixtures
from sqlalchemy import event, select, update
from sqlalchemy.orm import Session
from sqlalchemy.sql.dml import Update

from haoai_backend.generation_catalog.domain import ModelSource
from haoai_backend.generation_catalog.tables import model_configs, system_models
from haoai_backend.generation_core.admission_receipts import (
    AdmissionConfirmed,
    AdmissionStaged,
    lookup_staged_admission,
)
from haoai_backend.generation_core.base_schema import (
    AITaskRow,
    BillingUnitRow,
    CreditLogRow,
    ExecutionStepRow,
    TaskSubmissionRow,
    UserCreditRow,
)
from haoai_backend.generation_core.video_single_claims import (
    SqlAlchemyVideoSingleClaims,
)


_NOW = datetime(2026, 10, 16, 2, 0, tzinfo=timezone.utc)


def _video_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    model_source: ModelSource = ModelSource.SYSTEM,
    wallet_credits: int | None = 500,
    provider: str = "xai",
):
    monkeypatch.setattr(joint_fixtures, "_G2_JOINT_PROVIDER", provider)
    insert_catalog_model = joint_fixtures._joint_insert_catalog_model

    def insert_video_model(connection, source: ModelSource) -> None:
        insert_catalog_model(connection, source)
        table = system_models if source is ModelSource.SYSTEM else model_configs
        connection.execute(
            update(table)
            .where(table.c.id == joint_fixtures._G2_JOINT_MODEL_ID)
            .values(
                category="video",
                model=(
                    "47:seedance-2.0"
                    if provider == "aistarslab"
                    else "grok-imagine-video"
                ),
                base_url=(
                    "https://provider.example.test/openapi"
                    if provider == "aistarslab"
                    else "https://provider.example.test/v2"
                ),
                params=json.dumps(
                    {
                        "billing_mode": "per_second",
                        "per_second_cost": 1,
                        "duration": {"enabled": True, "default": 5, "options": [5]},
                        "resolution": {
                            "enabled": True,
                            "default": "720p",
                            "options": ["720p"],
                        },
                        "image_size": {
                            "enabled": True,
                            "default": "16:9",
                            "options": ["16:9"],
                        },
                    }
                ),
            )
        )

    monkeypatch.setattr(
        joint_fixtures,
        "_joint_insert_catalog_model",
        insert_video_model,
    )
    return joint_fixtures._joint_environment(
        tmp_path,
        model_source=model_source,
        operation="video.single",
        wallet_credits=wallet_credits,
    )


def _video_request(provider: str) -> dict[str, object]:
    return {
        "model_id": joint_fixtures._G2_JOINT_MODEL_ID,
        "prompt": "A quiet room at sunset",
        "target": {"kind": "standalone"},
        "size": "16:9",
        "duration": 5,
        "resolution": "720p",
        "reference_images": [
            "https://ref.example.test/image-a?ordered=1",
            "https://ref.example.test/image-b?ordered=2",
            "https://ref.example.test/image-a?ordered=1",
        ],
        "reference_audios": (
            [
                "https://ref.example.test/audio-a?ordered=1",
                "https://ref.example.test/audio-b?ordered=2",
                "https://ref.example.test/audio-a?ordered=1",
            ]
            if provider == "aistarslab"
            else []
        ),
        "reference_videos": (
            [
                "https://ref.example.test/video-a?ordered=1",
                "https://ref.example.test/video-b?ordered=2",
                "https://ref.example.test/video-a?ordered=1",
            ]
            if provider == "aistarslab"
            else []
        ),
    }


def _admit_video_task(environment, *, key: str) -> tuple[str, str]:
    caller = joint_fixtures._joint_caller(environment.engine)
    try:
        staged = joint_fixtures._joint_prepare_and_stage(
            environment.binding,
            caller,
            operation="video.single",
            raw_key=key,
            request=_video_request(joint_fixtures._G2_JOINT_PROVIDER),
        )
        if not isinstance(staged, AdmissionStaged):
            raise AssertionError("authenticated producer did not stage video.single")
        task_ids = tuple(item.task_id for item in staged.ordered_tasks)
        caller.commit()
        with Session(environment.engine) as reader:
            confirmed = lookup_staged_admission(reader, staged)
            if not isinstance(confirmed, AdmissionConfirmed):
                raise AssertionError(
                    "independent session did not confirm durable video stage"
                )
            parent = reader.execute(
                select(TaskSubmissionRow.__table__).where(
                    TaskSubmissionRow.__table__.c.id == staged.submission_id
                )
            ).mappings().one_or_none()
        if (
            parent is None
            or parent["task_ids"] != [*task_ids]
            or len(task_ids) != 1
        ):
            raise AssertionError(
                "durable video.single parent must contain its sole task"
            )
        return task_ids[0], staged.submission_id
    finally:
        if caller.in_transaction():
            caller.rollback()
        caller.close()


def _claim_adapter(engine, clock, token: str) -> SqlAlchemyVideoSingleClaims:
    return SqlAlchemyVideoSingleClaims(
        session_factory=lambda: Session(engine),
        db_clock=lambda: clock[0],
        id_factory=lambda: token,
    )


def _financial_snapshot(engine):
    with Session(engine) as reader:
        credits = tuple(
            tuple(row)
            for row in reader.execute(
                select(UserCreditRow.user_id, UserCreditRow.credits).order_by(
                    UserCreditRow.user_id
                )
            )
        )
        logs = reader.execute(
            select(CreditLogRow.__table__).order_by(CreditLogRow.id)
        ).mappings().all()
        units = reader.execute(
            select(
                BillingUnitRow.__table__.c.id,
                BillingUnitRow.__table__.c.task_id,
                BillingUnitRow.__table__.c.quoted_amount,
                BillingUnitRow.__table__.c.debited_amount,
                BillingUnitRow.__table__.c.refunded_amount,
                BillingUnitRow.__table__.c.billing_status,
            ).order_by(BillingUnitRow.id)
        ).all()
    return (
        credits,
        tuple(
            tuple(row[column.name] for column in CreditLogRow.__table__.columns)
            for row in logs
        ),
        tuple(tuple(row) for row in units),
    )


def _read_task_graph(engine, task_id: str):
    with Session(engine) as reader:
        task = dict(
            reader.execute(
                select(AITaskRow.__table__).where(AITaskRow.id == task_id)
            ).mappings().one()
        )
        request_data = json.loads(task["request_data"])
        parent = dict(
            reader.execute(
                select(TaskSubmissionRow.__table__).where(
                    TaskSubmissionRow.id == request_data["submission_id"]
                )
            ).mappings().one()
        )
        units = [
            dict(row)
            for row in reader.execute(
                select(BillingUnitRow.__table__).where(
                    BillingUnitRow.task_id == task_id
                )
            ).mappings()
        ]
        steps = (
            [
                dict(row)
                for row in reader.execute(
                    select(ExecutionStepRow.__table__).where(
                        ExecutionStepRow.billing_unit_id == units[0]["id"]
                    )
                ).mappings()
            ]
            if units
            else []
        )
    return task, parent, units, steps


def _update_graph(
    engine,
    task_id: str,
    *,
    task_values: dict[str, object] | None = None,
    parent_values: dict[str, object] | None = None,
    unit_values: dict[str, object] | None = None,
    step_values: dict[str, object] | None = None,
) -> None:
    task, parent, units, steps = _read_task_graph(engine, task_id)
    with engine.begin() as connection:
        if task_values:
            connection.execute(
                update(AITaskRow.__table__)
                .where(AITaskRow.id == task_id)
                .values(**task_values)
            )
        if parent_values:
            connection.execute(
                update(TaskSubmissionRow.__table__)
                .where(TaskSubmissionRow.id == parent["id"])
                .values(**parent_values)
            )
        if unit_values:
            assert len(units) == 1
            connection.execute(
                update(BillingUnitRow.__table__)
                .where(BillingUnitRow.id == units[0]["id"])
                .values(**unit_values)
            )
        if step_values:
            assert len(steps) == 1
            connection.execute(
                update(ExecutionStepRow.__table__)
                .where(ExecutionStepRow.id == steps[0]["id"])
                .values(**step_values)
            )


class _ClaimCommitThenUnknownSession(Session):
    def execute(self, statement, *args, **kwargs):
        result = super().execute(statement, *args, **kwargs)
        if isinstance(statement, Update) and statement.table is AITaskRow.__table__:
            self.info["claim_commit_unknown"] = True
        return result


class _ClaimRaceSession(Session):
    def __init__(self, *args, race: str, task_id: str, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._race = race
        self._race_task_id = task_id
        self._race_applied = False

    def execute(self, statement, *args, **kwargs):
        if (
            not self._race_applied
            and isinstance(statement, Update)
            and statement.table is AITaskRow.__table__
        ):
            self._race_applied = True
            connection = self.connection()
            if self._race == "request_data":
                stored = connection.exec_driver_sql(
                    "SELECT request_data FROM ai_tasks WHERE id = ?",
                    (self._race_task_id,),
                ).scalar_one()
                changed = json.loads(stored)
                changed["synthetic_cas_change"] = True
                connection.exec_driver_sql(
                    "UPDATE ai_tasks SET request_data = ? WHERE id = ?",
                    (json.dumps(changed), self._race_task_id),
                )
            elif self._race == "generation":
                connection.exec_driver_sql(
                    "UPDATE ai_tasks SET execution_generation = "
                    "execution_generation + 1 WHERE id = ?",
                    (self._race_task_id,),
                )
            elif self._race == "cancel":
                connection.exec_driver_sql(
                    "UPDATE ai_tasks SET user_cancelled_at = ?, "
                    "cancellation_reason = ? WHERE id = ?",
                    (_NOW.replace(tzinfo=None), "user", self._race_task_id),
                )
            elif self._race == "terminal":
                connection.exec_driver_sql(
                    "UPDATE ai_tasks SET status = ?, result = ? WHERE id = ?",
                    ("completed", "already completed", self._race_task_id),
                )
            else:
                raise AssertionError(f"unknown synthetic claim race: {self._race}")
        return super().execute(statement, *args, **kwargs)


def test_claim_next_claims_one_durably_staged_video_single_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    environment = _video_environment(monkeypatch, tmp_path)
    request.addfinalizer(environment.engine.dispose)
    task_id, submission_id = _admit_video_task(
        environment,
        key="video-single-claim-real-producer",
    )
    before_financial = _financial_snapshot(environment.engine)
    claims = _claim_adapter(environment.engine, [_NOW], "video-single-first-token")
    assert claims.claim_next("image-single", "wrong-family-worker") is None
    assert claims.claim_next("video-single", "") is None

    claim = claims.claim_next("video-single", "video-worker-1")

    assert claim is not None
    assert claim.scope.operation == "video.single"
    assert claim.scope.task_id == task_id
    assert claim.worker_instance == "video-worker-1"
    assert claim.execution_generation == 1
    assert claim.token == "video-single-first-token"
    assert "video-single-first-token" not in repr(claim)
    with Session(environment.engine) as reader:
        task = reader.execute(
            select(AITaskRow.__table__).where(AITaskRow.id == task_id)
        ).mappings().one()
        parent = reader.execute(
            select(TaskSubmissionRow.__table__).where(
                TaskSubmissionRow.id == submission_id
            )
        ).mappings().one()
        unit = reader.execute(
            select(BillingUnitRow.__table__).where(BillingUnitRow.task_id == task_id)
        ).mappings().one()
        steps = reader.execute(
            select(ExecutionStepRow.__table__).where(
                ExecutionStepRow.billing_unit_id == unit["id"]
            )
        ).mappings().all()
    assert parent["task_ids"] == [task_id]
    assert parent["operation"] == "video.single"
    assert task["type"] == "video-single"
    assert task["status"] == "processing"
    assert task["claim_token"] == claim.token
    assert task["execution_generation"] == 1
    assert task["lease_until"] == (_NOW + timedelta(seconds=60)).replace(tzinfo=None)
    assert unit["operation"] == "video.single"
    assert unit["step_graph_version"] == "video-single-v1"
    assert len(steps) == 1
    assert steps[0]["step_key"] == "generate"
    assert steps[0]["step_order"] == 0
    assert steps[0]["required"] is True
    assert steps[0]["dependencies"] == []
    assert _financial_snapshot(environment.engine) == before_financial


def test_default_token_factory_claims_a_durably_staged_video_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    environment = _video_environment(monkeypatch, tmp_path)
    request.addfinalizer(environment.engine.dispose)
    task_id, _ = _admit_video_task(
        environment,
        key="video-single-default-token-factory",
    )
    before_financial = _financial_snapshot(environment.engine)
    claims = SqlAlchemyVideoSingleClaims(
        session_factory=lambda: Session(environment.engine),
        db_clock=lambda: _NOW,
    )

    claim = claims.claim_next("video-single", "video-worker-default-token")

    assert claim is not None
    assert claim.scope.task_id == task_id
    assert claim.token
    assert _financial_snapshot(environment.engine) == before_financial


def test_user_video_single_with_zero_quote_claims_without_a_wallet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    environment = _video_environment(
        monkeypatch,
        tmp_path,
        model_source=ModelSource.USER,
        wallet_credits=None,
    )
    request.addfinalizer(environment.engine.dispose)
    task_id, _ = _admit_video_task(environment, key="video-single-user-zero-quote")
    before_financial = _financial_snapshot(environment.engine)
    claims = _claim_adapter(environment.engine, [_NOW], "video-user-token")

    claim = claims.claim_next("video-single", "video-worker-user")

    assert claim is not None
    task, _, units, _ = _read_task_graph(environment.engine, task_id)
    assert task["credit_cost"] == 0
    assert units[0]["quoted_amount"] == 0
    assert _financial_snapshot(environment.engine) == before_financial


@pytest.mark.parametrize(
    "invalid_graph",
    (
        "parent_task_ids",
        "wrong_parent_family",
        "wrong_task_family",
        "attempt_group_version",
        "wrong_child_identity",
        "multiple_units",
        "multiple_steps",
    ),
)
def test_claim_rejects_nonordinary_video_graphs(
    invalid_graph: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    environment = _video_environment(monkeypatch, tmp_path)
    request.addfinalizer(environment.engine.dispose)
    task_id, _ = _admit_video_task(
        environment,
        key=f"video-single-invalid-{invalid_graph}",
    )
    _, parent, units, steps = _read_task_graph(environment.engine, task_id)
    with environment.engine.begin() as connection:
        if invalid_graph == "parent_task_ids":
            connection.execute(
                update(TaskSubmissionRow.__table__)
                .where(TaskSubmissionRow.id == parent["id"])
                .values(task_ids=[])
            )
        elif invalid_graph == "wrong_parent_family":
            connection.execute(
                update(TaskSubmissionRow.__table__)
                .where(TaskSubmissionRow.id == parent["id"])
                .values(operation="image.single")
            )
        elif invalid_graph == "wrong_task_family":
            connection.execute(
                update(AITaskRow.__table__)
                .where(AITaskRow.id == task_id)
                .values(type="image-single")
            )
        elif invalid_graph == "attempt_group_version":
            connection.execute(
                update(BillingUnitRow.__table__)
                .where(BillingUnitRow.id == units[0]["id"])
                .values(step_graph_version="video-haoai-attempt-group-v1")
            )
            for order, step_key in ((1, "generate_audio"), (2, "assemble_video")):
                connection.execute(
                    ExecutionStepRow.__table__.insert().values(
                        id=str(uuid4()),
                        billing_unit_id=units[0]["id"],
                        step_key=step_key,
                        dependencies=["generate"],
                        input_digest=steps[0]["input_digest"],
                        input_snapshot=None,
                        required=True,
                        step_order=order,
                        execution_status="prepared",
                        created_at=_NOW.replace(tzinfo=None),
                        updated_at=_NOW.replace(tzinfo=None),
                    )
                )
        elif invalid_graph == "wrong_child_identity":
            task = _read_task_graph(environment.engine, task_id)[0]
            changed = json.loads(task["request_data"])
            changed["server_attempt_id"] = f'{parent["id"]}:1'
            connection.execute(
                update(AITaskRow.__table__)
                .where(AITaskRow.id == task_id)
                .values(request_data=json.dumps(changed))
            )
        elif invalid_graph == "multiple_units":
            connection.execute(
                BillingUnitRow.__table__.insert().values(
                    id=str(uuid4()),
                    task_id=task_id,
                    item_key=f'{units[0]["item_key"]}:extra',
                    operation="video.single",
                    step_graph_version="video-single-v1",
                    input_digest=units[0]["input_digest"],
                    quoted_amount=0,
                    created_at=_NOW.replace(tzinfo=None),
                    updated_at=_NOW.replace(tzinfo=None),
                )
            )
        elif invalid_graph == "multiple_steps":
            connection.execute(
                ExecutionStepRow.__table__.insert().values(
                    id=str(uuid4()),
                    billing_unit_id=units[0]["id"],
                    step_key="moderation",
                    dependencies=["generate"],
                    input_digest=steps[0]["input_digest"],
                    input_snapshot=None,
                    required=True,
                    step_order=1,
                    execution_status="prepared",
                    created_at=_NOW.replace(tzinfo=None),
                    updated_at=_NOW.replace(tzinfo=None),
                )
            )
        else:
            raise AssertionError(f"unknown graph mutation: {invalid_graph}")

    claims = _claim_adapter(environment.engine, [_NOW], f"bad-{invalid_graph}-token")
    before_financial = _financial_snapshot(environment.engine)
    assert claims.claim_next("video-single", "video-worker-invalid") is None
    task, _, _, _ = _read_task_graph(environment.engine, task_id)
    assert task["claim_token"] is None
    assert task["execution_generation"] == 0
    assert _financial_snapshot(environment.engine) == before_financial


@pytest.mark.parametrize(
    "blocked_state",
    (
        "cancelled",
        "terminal",
        "active_lease",
        "first_intent",
        "deadline",
        "step_submitting",
        "unit_unknown",
    ),
)
def test_claim_refuses_cancelled_terminal_or_started_video_tasks(
    blocked_state: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    environment = _video_environment(monkeypatch, tmp_path)
    request.addfinalizer(environment.engine.dispose)
    task_id, _ = _admit_video_task(
        environment,
        key=f"video-single-blocked-{blocked_state}",
    )
    task_values: dict[str, object] | None = None
    unit_values: dict[str, object] | None = None
    step_values: dict[str, object] | None = None
    if blocked_state == "cancelled":
        task_values = {
            "status": "processing",
            "user_cancelled_at": _NOW.replace(tzinfo=None),
            "cancellation_reason": "user",
        }
    elif blocked_state == "terminal":
        task_values = {"status": "completed", "result": "already done"}
    elif blocked_state == "active_lease":
        task_values = {
            "status": "processing",
            "claimed_by": "other-worker",
            "claim_token": "other-token",
            "execution_generation": 1,
            "lease_until": (_NOW + timedelta(seconds=60)).replace(tzinfo=None),
        }
    elif blocked_state == "first_intent":
        unit_values = {"first_intent_at": _NOW.replace(tzinfo=None)}
    elif blocked_state == "deadline":
        unit_values = {
            "deadline_at": (_NOW + timedelta(seconds=90)).replace(tzinfo=None)
        }
    elif blocked_state == "step_submitting":
        step_values = {"execution_status": "submitting"}
    elif blocked_state == "unit_unknown":
        unit_values = {"result_status": "unknown"}
    _update_graph(
        environment.engine,
        task_id,
        task_values=task_values,
        unit_values=unit_values,
        step_values=step_values,
    )
    before_financial = _financial_snapshot(environment.engine)

    claims = _claim_adapter(
        environment.engine,
        [_NOW],
        f"blocked-{blocked_state}-token",
    )
    assert claims.claim_next("video-single", "video-worker-blocked") is None
    task, _, _, _ = _read_task_graph(environment.engine, task_id)
    assert task["claim_token"] == (
        "other-token" if blocked_state == "active_lease" else None
    )
    assert _financial_snapshot(environment.engine) == before_financial


def test_renewal_succeeds_after_progress_without_changing_the_original_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    environment = _video_environment(monkeypatch, tmp_path)
    request.addfinalizer(environment.engine.dispose)
    task_id, _ = _admit_video_task(environment, key="video-single-renew-progress")
    clock = [_NOW]
    claims = _claim_adapter(environment.engine, clock, "renew-progress-token")
    claim = claims.claim_next("video-single", "video-worker-renew")
    assert claim is not None
    deadline = _NOW + timedelta(seconds=90)
    _update_graph(
        environment.engine,
        task_id,
        unit_values={
            "first_intent_at": _NOW.replace(tzinfo=None),
            "deadline_at": deadline.replace(tzinfo=None),
        },
        step_values={"execution_status": "submitting"},
    )
    clock[0] = _NOW + timedelta(seconds=1)
    before_financial = _financial_snapshot(environment.engine)

    assert claims.renew_or_revoke(claim) == "renewed"

    task, _, units, steps = _read_task_graph(environment.engine, task_id)
    assert task["lease_until"] == (_NOW + timedelta(seconds=61)).replace(tzinfo=None)
    assert task["execution_generation"] == 1
    assert units[0]["deadline_at"] == deadline.replace(tzinfo=None)
    assert steps[0]["execution_status"] == "submitting"
    assert _financial_snapshot(environment.engine) == before_financial


@pytest.mark.parametrize("lost_state", ("cancelled", "terminal", "expired"))
def test_renewal_rejects_cancelled_terminal_or_expired_ownership(
    lost_state: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    environment = _video_environment(monkeypatch, tmp_path)
    request.addfinalizer(environment.engine.dispose)
    task_id, _ = _admit_video_task(
        environment,
        key=f"video-single-renew-lost-{lost_state}",
    )
    clock = [_NOW]
    claims = _claim_adapter(environment.engine, clock, f"renew-lost-{lost_state}")
    claim = claims.claim_next("video-single", "video-worker-renew-lost")
    assert claim is not None
    if lost_state == "cancelled":
        _update_graph(
            environment.engine,
            task_id,
            task_values={
                "user_cancelled_at": _NOW.replace(tzinfo=None),
                "cancellation_reason": "user",
            },
        )
    elif lost_state == "terminal":
        _update_graph(
            environment.engine,
            task_id,
            task_values={"status": "completed", "result": "complete"},
        )
    else:
        clock[0] = _NOW + timedelta(seconds=61)
    task_before, _, _, _ = _read_task_graph(environment.engine, task_id)
    before_financial = _financial_snapshot(environment.engine)

    assert claims.renew_or_revoke(claim) == "ownership_lost"

    task_after, _, _, _ = _read_task_graph(environment.engine, task_id)
    assert task_after["lease_until"] == task_before["lease_until"]
    assert task_after["execution_generation"] == task_before["execution_generation"]
    assert _financial_snapshot(environment.engine) == before_financial


@pytest.mark.parametrize(
    "release_state",
    ("expired", "cancelled", "terminal", "progressed_lost_signal"),
)
def test_release_cleans_only_its_claim_and_preserves_lifecycle_state(
    release_state: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    environment = _video_environment(monkeypatch, tmp_path)
    request.addfinalizer(environment.engine.dispose)
    task_id, _ = _admit_video_task(
        environment,
        key=f"video-single-release-{release_state}",
    )
    clock = [_NOW]
    claims = _claim_adapter(environment.engine, clock, f"release-{release_state}-token")
    claim = claims.claim_next("video-single", "video-worker-release")
    assert claim is not None
    if release_state == "cancelled":
        _update_graph(
            environment.engine,
            task_id,
            task_values={
                "status": "cancelled",
                "user_cancelled_at": _NOW.replace(tzinfo=None),
                "cancellation_reason": "user",
                "result": "cancelled result",
            },
        )
    elif release_state == "terminal":
        _update_graph(
            environment.engine,
            task_id,
            task_values={"status": "completed", "result": "completed result"},
            unit_values={"result_status": "succeeded"},
            step_values={"execution_status": "applied"},
        )
    elif release_state == "expired":
        clock[0] = _NOW + timedelta(seconds=61)
    else:
        _update_graph(
            environment.engine,
            task_id,
            unit_values={
                "first_intent_at": _NOW.replace(tzinfo=None),
                "deadline_at": (_NOW + timedelta(seconds=90)).replace(tzinfo=None),
            },
            step_values={"execution_status": "submitting"},
        )
        claim.local_lost_signal.set()
    before_task, _, before_units, before_steps = _read_task_graph(
        environment.engine, task_id
    )
    before_financial = _financial_snapshot(environment.engine)

    assert claims.release_claim(claim) is True

    after_task, _, after_units, after_steps = _read_task_graph(
        environment.engine, task_id
    )
    expected_status = (
        "queued"
        if before_task["status"] == "processing"
        and before_task["user_cancelled_at"] is None
        else before_task["status"]
    )
    assert after_task["status"] == expected_status
    assert after_task["claimed_by"] is None
    assert after_task["claim_token"] is None
    assert after_task["lease_until"] is None
    assert after_task["execution_generation"] == claim.execution_generation
    assert after_task["user_cancelled_at"] == before_task["user_cancelled_at"]
    assert after_task["cancellation_reason"] == before_task["cancellation_reason"]
    assert after_task["result"] == before_task["result"]
    assert after_task["request_data"] == before_task["request_data"]
    assert after_units == before_units
    assert after_steps == before_steps
    assert claims.release_claim(claim) is False
    assert claims.renew_or_revoke(claim) == "ownership_lost"
    assert _financial_snapshot(environment.engine) == before_financial


def test_expired_claim_can_be_reclaimed_but_old_token_cannot_touch_new_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    environment = _video_environment(monkeypatch, tmp_path)
    request.addfinalizer(environment.engine.dispose)
    task_id, _ = _admit_video_task(environment, key="video-single-stale-owner")
    clock = [_NOW]
    old_claims = _claim_adapter(environment.engine, clock, "stale-video-token")
    old_claim = old_claims.claim_next("video-single", "video-worker-old")
    assert old_claim is not None
    assert (
        _claim_adapter(environment.engine, clock, "concurrent-video-token").claim_next(
            "video-single", "video-worker-other"
        )
        is None
    )

    clock[0] = _NOW + timedelta(seconds=61)
    new_claims = _claim_adapter(environment.engine, clock, "new-video-token")
    new_claim = new_claims.claim_next("video-single", "video-worker-new")

    assert new_claim is not None
    assert new_claim.scope.task_id == task_id
    assert new_claim.execution_generation == old_claim.execution_generation + 1
    assert old_claims.renew_or_revoke(old_claim) == "ownership_lost"
    assert old_claims.release_claim(old_claim) is False
    task, _, _, _ = _read_task_graph(environment.engine, task_id)
    assert task["claimed_by"] == new_claim.worker_instance
    assert task["claim_token"] == new_claim.token
    assert task["execution_generation"] == new_claim.execution_generation
    assert new_claims.release_claim(new_claim) is True


@pytest.mark.parametrize("replace_owner_before_confirmation", (False, True))
def test_claim_commit_unknown_is_confirmed_only_from_its_exact_durable_claim(
    replace_owner_before_confirmation: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    environment = _video_environment(monkeypatch, tmp_path)
    request.addfinalizer(environment.engine.dispose)
    task_id, _ = _admit_video_task(environment, key="video-single-unknown-ack")

    def session_factory() -> Session:
        session = _ClaimCommitThenUnknownSession(
            bind=environment.engine,
            expire_on_commit=False,
        )

        def fail_after_claim_commit(committed: Session) -> None:
            if committed.info.pop("claim_commit_unknown", False):
                if replace_owner_before_confirmation:
                    with Session(environment.engine) as writer:
                        with writer.begin():
                            writer.execute(
                                update(AITaskRow.__table__)
                                .where(AITaskRow.id == task_id)
                                .values(
                                    claimed_by="replacement-worker",
                                    claim_token="replacement-token",
                                )
                            )
                raise RuntimeError("fixture lost claim commit acknowledgement")

        event.listen(session, "after_commit", fail_after_claim_commit)
        return session

    claims = SqlAlchemyVideoSingleClaims(
        session_factory=session_factory,
        db_clock=lambda: _NOW,
        id_factory=lambda: "unknown-ack-video-token",
    )

    claim = claims.claim_next("video-single", "video-worker-unknown-ack")

    task, _, _, _ = _read_task_graph(environment.engine, task_id)
    assert task["execution_generation"] == 1
    if replace_owner_before_confirmation:
        assert claim is None
        assert task["claimed_by"] == "replacement-worker"
        assert task["claim_token"] == "replacement-token"
    else:
        assert claim is not None
        assert task["claimed_by"] == claim.worker_instance
        assert task["claim_token"] == claim.token


@pytest.mark.parametrize("race", ("cancel", "terminal", "request_data", "generation"))
def test_claim_cas_rejects_changes_after_graph_read(
    race: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    environment = _video_environment(monkeypatch, tmp_path)
    request.addfinalizer(environment.engine.dispose)
    task_id, _ = _admit_video_task(environment, key=f"video-single-race-{race}")

    def race_session_factory() -> Session:
        return _ClaimRaceSession(
            bind=environment.engine,
            expire_on_commit=False,
            race=race,
            task_id=task_id,
        )

    claims = SqlAlchemyVideoSingleClaims(
        session_factory=race_session_factory,
        db_clock=lambda: _NOW,
        id_factory=lambda: f"race-{race}-video-token",
    )

    assert claims.claim_next("video-single", "video-worker-race") is None

    task, _, _, _ = _read_task_graph(environment.engine, task_id)
    assert task["claim_token"] is None
    assert task["claimed_by"] is None
    if race == "cancel":
        assert task["user_cancelled_at"] == _NOW.replace(tzinfo=None)
    elif race == "terminal":
        assert task["status"] == "completed"
    elif race == "request_data":
        assert json.loads(task["request_data"])["synthetic_cas_change"] is True
    else:
        assert task["execution_generation"] == 1
