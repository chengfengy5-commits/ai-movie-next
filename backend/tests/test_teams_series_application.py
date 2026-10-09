from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import event, insert, select, update
from sqlalchemy.orm import Session

from chapter_asset_replacement_support import create_owner_database, series as owner_series
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.teams import TeamSeriesCreate, TransferRequest
from haoai_backend.teams.errors import TeamBadRequest, TeamForbidden, TeamNotFound
from haoai_backend.teams.series.application import (
    claim_series,
    create_team_series,
    transfer_series_claim,
    unclaim_series,
)
from haoai_backend.teams.series.persistence import create_series_uow_factory


NOW = datetime(2026, 10, 9, 12, 0, 0)


@pytest.fixture
def owner_database(tmp_path):
    database = create_owner_database(tmp_path / "teams-series-application.sqlite")
    try:
        database.assert_foreign_keys_enabled()
        yield database
    finally:
        database.close()


def make_factory(database, *, series_id="series-created"):
    return create_series_uow_factory(
        database.session_factory,
        utc_clock=lambda: NOW,
        id_source=lambda: series_id,
    )


def read_series(database, series_id):
    with database.engine.connect() as connection:
        row = connection.execute(
            select(owner_series).where(owner_series.c.id == series_id)
        ).mappings().first()
    return dict(row) if row is not None else None


def test_claim_transfer_unclaim_and_create_preserve_actor_rules(owner_database):
    factory = make_factory(owner_database)
    author = TrustedActor("user-a")
    member = TrustedActor("user-b")

    with owner_database.engine.begin() as connection:
        connection.execute(
            update(owner_series)
            .where(owner_series.c.id == "series-a")
            .values(claimed_by="user-a", claimed_at=datetime(2026, 10, 8, 12, 0, 0))
        )

    with pytest.raises(TeamBadRequest, match="该剧集已被其他成员认领"):
        claim_series(factory, member, "team-a", "series-a")

    with pytest.raises(TeamForbidden, match="无权限取消该认领"):
        unclaim_series(factory, member, "team-a", "series-a")

    transferred = transfer_series_claim(
        factory,
        author,
        "team-a",
        "series-a",
        TransferRequest(user_id="user-b"),
    )
    assert transferred == {"message": "已转交认领", "claimed_by": "user-b"}
    assert read_series(owner_database, "series-a")["claimed_by"] == "user-b"

    removed = unclaim_series(factory, author, "team-a", "series-a")
    assert removed == {"message": "已取消认领"}
    saved = read_series(owner_database, "series-a")
    assert saved["claimed_by"] is None
    assert saved["claimed_at"] is None

    created = create_team_series(
        make_factory(owner_database, series_id="series-created"),
        member,
        "team-a",
        TeamSeriesCreate(
            name="  新剧集  ",
            description="保留描述",
            image_url="https://media.invalid/cover.png",
            style_prompt_id="style-a",
            claim=True,
        ),
    )
    assert created == {
        "id": "series-created",
        "name": "新剧集",
        "team_id": "team-a",
        "user_id": "user-b",
        "style_prompt_id": "style-a",
    }
    saved = read_series(owner_database, "series-created")
    assert saved["claimed_by"] == "user-b"
    assert saved["claimed_at"] == NOW
    assert saved["description"] == "保留描述"
    assert saved["image_url"] == "https://media.invalid/cover.png"


def test_claim_by_author_and_team_errors_keep_source_priority(owner_database):
    factory = make_factory(owner_database)
    author = TrustedActor("user-a")

    claimed = claim_series(factory, author, "team-a", "series-a")
    assert claimed == {
        "message": "认领成功",
        "claimed_by": "user-a",
        "claimed_at": NOW,
    }

    with pytest.raises(TeamNotFound, match="团队不存在"):
        claim_series(factory, author, "missing-team", "missing-series")


def test_team_owner_can_unclaim_or_transfer_an_unclaimed_other_series(owner_database):
    with owner_database.engine.begin() as connection:
        connection.execute(
            insert(owner_series).values(
                id="series-owned-by-member",
                user_id="user-b",
                name="成员的未认领剧集",
                description=None,
                image_url=None,
                style_prompt_id=None,
                team_id="team-a",
                claimed_by=None,
                claimed_at=None,
                created_at=NOW,
                updated_at=NOW,
            )
        )

    commits: list[bool] = []

    def after_commit(_session):
        commits.append(True)

    event.listen(Session, "after_commit", after_commit)
    try:
        unclaimed = unclaim_series(
            make_factory(owner_database),
            TrustedActor("user-a"),
            "team-a",
            "series-owned-by-member",
        )
        transferred = transfer_series_claim(
            make_factory(owner_database),
            TrustedActor("user-a"),
            "team-a",
            "series-owned-by-member",
            TransferRequest(user_id="user-b"),
        )
    finally:
        event.remove(Session, "after_commit", after_commit)

    assert unclaimed == {"message": "该剧集尚未被认领"}
    assert transferred == {"message": "已转交认领", "claimed_by": "user-b"}
    assert len(commits) == 1
    saved = read_series(owner_database, "series-owned-by-member")
    assert saved is not None
    assert saved["claimed_by"] == "user-b"
    assert saved["claimed_at"] == NOW


def test_allowed_unclaim_without_claim_returns_without_committing(owner_database):
    factory = make_factory(owner_database)
    before = owner_database.snapshot()
    commit_count = 0

    def after_commit(_session):
        nonlocal commit_count
        commit_count += 1


    event.listen(Session, "after_commit", after_commit)
    try:
        result = unclaim_series(
            factory,
            TrustedActor("user-a"),
            "team-a",
            "series-a",
        )
    finally:
        event.remove(Session, "after_commit", after_commit)

    assert result == {"message": "该剧集尚未被认领"}
    assert commit_count == 0
    assert owner_database.snapshot() == before
