"""Read-only administrator query use cases with explicit session ownership."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from .domain import AdminStoryboardChatQueriesActor
from .errors import (
    AdminStoryboardChatQueriesForbidden,
    AdminStoryboardChatQueriesUnavailable,
    ChatMessageNotFound,
    StoryboardAssetNotFound,
)
from .ports import AdminStoryboardChatQueriesUnitOfWork, UnitOfWorkFactory
from .schemas import (
    ChatMessageDetailPayload,
    ChatMessageListPayload,
    StoryboardAssetListPayload,
    StoryboardAssetPayload,
    chat_message_detail_payload,
    chat_message_list_item,
    storyboard_asset_payload,
)


@contextmanager
def _open(
    factory: UnitOfWorkFactory | None,
) -> Iterator[AdminStoryboardChatQueriesUnitOfWork]:
    if factory is None:
        raise AdminStoryboardChatQueriesUnavailable()
    unit_of_work = factory()
    try:
        yield unit_of_work
    except BaseException:
        try:
            unit_of_work.rollback()
        except BaseException:
            pass
        try:
            unit_of_work.close()
        except BaseException:
            pass
        raise
    else:
        unit_of_work.close()


def _require_admin(actor: AdminStoryboardChatQueriesActor) -> None:
    if not actor.is_superuser:
        raise AdminStoryboardChatQueriesForbidden()


def list_storyboard_assets(
    factory: UnitOfWorkFactory | None,
    actor: AdminStoryboardChatQueriesActor,
    *,
    skip: int = 0,
    limit: int = 10,
    sort_field: str = "created_at",
    sort_order: str = "desc",
) -> StoryboardAssetListPayload:
    _require_admin(actor)
    with _open(factory) as unit_of_work:
        records, total = unit_of_work.list_storyboard_assets(
            sort_field=sort_field,
            sort_order=sort_order,
            skip=skip,
            limit=limit,
        )
        return {
            "data": [storyboard_asset_payload(record) for record in records],
            "total": total,
        }


def get_storyboard_asset(
    factory: UnitOfWorkFactory | None,
    actor: AdminStoryboardChatQueriesActor,
    asset_id: str,
) -> StoryboardAssetPayload:
    _require_admin(actor)
    with _open(factory) as unit_of_work:
        record = unit_of_work.get_storyboard_asset(asset_id)
        if record is None:
            raise StoryboardAssetNotFound()
        return storyboard_asset_payload(record)


def list_chat_messages(
    factory: UnitOfWorkFactory | None,
    actor: AdminStoryboardChatQueriesActor,
    *,
    skip: int = 0,
    limit: int = 10,
    sort_field: str = "created_at",
    sort_order: str = "desc",
) -> ChatMessageListPayload:
    _require_admin(actor)
    with _open(factory) as unit_of_work:
        records, total = unit_of_work.list_chat_messages(
            sort_field=sort_field,
            sort_order=sort_order,
            skip=skip,
            limit=limit,
        )
        return {
            "data": [chat_message_list_item(record) for record in records],
            "total": total,
        }


def get_chat_message(
    factory: UnitOfWorkFactory | None,
    actor: AdminStoryboardChatQueriesActor,
    message_id: str,
) -> ChatMessageDetailPayload:
    _require_admin(actor)
    with _open(factory) as unit_of_work:
        record = unit_of_work.get_chat_message(message_id)
        if record is None:
            raise ChatMessageNotFound()
        return chat_message_detail_payload(record)
