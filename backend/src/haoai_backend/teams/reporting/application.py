"""Read-only reporting use cases and permission ordering."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from typing import Any, TypeVar

from haoai_backend.shared.identity import TrustedActor
from haoai_backend.teams.errors import TeamForbidden, TeamNotFound, TeamsUnavailable
from haoai_backend.teams.policy import has_team_permission

from .ports import ReportingUnitOfWork, ReportingUnitOfWorkFactory
from .task_payload import collect_task_context, project_task_items
from .usage import (
    aggregate_usage_rows,
    avatar_url_for_email,
    fill_usage_members,
    member_model_names,
    order_export_models,
    parse_date_range,
)


T = TypeVar("T")


def _open_uow(factory: ReportingUnitOfWorkFactory | None) -> ReportingUnitOfWork:
    if factory is None:
        raise TeamsUnavailable()
    uow = factory()
    try:
        uow.ensure_clean()
    except Exception:
        try:
            uow.rollback()
        finally:
            uow.close()
        raise
    return uow


def _run_read_only(
    factory: ReportingUnitOfWorkFactory | None,
    operation: Callable[[ReportingUnitOfWork], T],
) -> T:
    uow = _open_uow(factory)
    try:
        return operation(uow)
    except Exception:
        uow.rollback()
        raise
    finally:
        uow.close()


def _require_team_permission(
    uow: ReportingUnitOfWork,
    team_id: str,
    user_id: str,
    permission: str,
) -> None:
    membership = uow.load_membership(team_id, user_id)
    if membership is None:
        raise TeamForbidden()
    permission_membership = uow.load_membership(team_id, user_id)
    if permission_membership is None or not has_team_permission(
        permission_membership.role,
        permission_membership.permissions,
        permission,
    ):
        raise TeamForbidden("没有执行该操作的权限")


def _require_team(
    uow: ReportingUnitOfWork,
    team_id: str,
) -> None:
    if uow.load_team(team_id) is None:
        raise TeamNotFound()


def member_ai_tasks(
    factory: ReportingUnitOfWorkFactory | None,
    actor: TrustedActor,
    team_id: str,
    user_id: str,
    page: int = 1,
    page_size: int = 10,
) -> dict[str, Any]:
    def operation(uow: ReportingUnitOfWork) -> dict[str, Any]:
        _require_team_permission(uow, team_id, actor.user_id, "view_tasks")
        if uow.load_membership(team_id, user_id) is None:
            raise TeamNotFound("该成员不在团队中")

        persistence = uow
        tasks, total = persistence.list_member_tasks(user_id, page, page_size)
        message_ids = [
            task["message_id"] for task in tasks if task.get("message_id")
        ]
        messages = persistence.load_messages(message_ids)
        _, chapter_ids, asset_ids = collect_task_context(tasks, messages)
        chapters = persistence.load_chapter_titles(list(chapter_ids))
        assets = {
            kind: persistence.load_asset_names(kind, identifiers)
            for kind, identifiers in asset_ids.items()
        }
        result = {
            "total": total,
            "tasks": project_task_items(tasks, messages, chapters, assets),
            "page": page,
            "page_size": page_size,
        }
        result["credits"] = persistence.load_user_credit(user_id)
        return result

    return _run_read_only(factory, operation)


def team_usage(
    factory: ReportingUnitOfWorkFactory | None,
    actor: TrustedActor,
    team_id: str,
    start_time: str | None = None,
    end_time: str | None = None,
) -> dict[str, Any]:
    def operation(uow: ReportingUnitOfWork) -> dict[str, Any]:
        _require_team_permission(uow, team_id, actor.user_id, "view_usage")
        _require_team(uow, team_id)
        start, end = parse_date_range(start_time, end_time)
        persistence = uow
        series = persistence.list_team_series(team_id, newest_first=True)
        if not series:
            return {
                "items": [],
                "total_calls": 0,
                "total_credits": 0,
                "total_failed": 0,
            }

        series_ids = [row["id"] for row in series]
        rows = persistence.load_team_usage_rows(series_ids, start, end)
        chapter_rows = persistence.load_chapter_scope(series_ids, start, end)
        chapter_scope: dict[Any, dict[Any, set[Any]]] = {}
        for user_id, series_id, chapter_id in chapter_rows:
            chapter_scope.setdefault(series_id, {}).setdefault(user_id, set()).add(chapter_id)

        rows_by_series: dict[str, list[Sequence[Any]]] = defaultdict(list)
        for row in rows:
            rows_by_series.setdefault(row[0], []).append(row)
        user_ids = {row[2] for row in rows if row[2]}
        users = persistence.load_users(list(user_ids)) if user_ids else {}

        items: list[dict[str, Any]] = []
        total_calls = 0
        total_credits: Any = 0
        total_failed = 0
        for current_series in series:
            calls, credits, failed_calls, by_model, by_member = aggregate_usage_rows(
                rows_by_series.get(current_series["id"], [])
            )
            if calls == 0 and failed_calls == 0:
                continue
            fill_usage_members(by_member, users)
            scoped_users = chapter_scope.get(current_series["id"], {})
            for member in by_member:
                member["chapter_count"] = len(scoped_users.get(member["user_id"], set()))
            total_calls += calls
            total_credits += credits
            total_failed += failed_calls
            items.append(
                {
                    "series_id": current_series["id"],
                    "series_name": current_series["name"],
                    "updated_at": current_series["updated_at"],
                    "total_calls": calls,
                    "total_credits": credits,
                    "failed_calls": failed_calls,
                    "by_model": by_model,
                    "by_member": by_member,
                }
            )

        return {
            "items": items,
            "total_calls": total_calls,
            "total_credits": total_credits,
            "total_failed": total_failed,
        }

    return _run_read_only(factory, operation)


def team_series_usage(
    factory: ReportingUnitOfWorkFactory | None,
    actor: TrustedActor,
    team_id: str,
    series_id: str,
    start_time: str | None = None,
    end_time: str | None = None,
) -> dict[str, Any]:
    def operation(uow: ReportingUnitOfWork) -> dict[str, Any]:
        _require_team_permission(uow, team_id, actor.user_id, "view_usage")
        persistence = uow
        series = persistence.load_team_series(team_id, series_id)
        if series is None:
            raise TeamNotFound("剧集不存在或不属于该团队")
        start, end = parse_date_range(start_time, end_time)
        rows = persistence.load_series_usage_rows(series_id, start, end)
        chapters = persistence.list_series_chapters(series_id)
        rows_by_chapter: dict[str, list[Sequence[Any]]] = defaultdict(list)
        for row in rows:
            rows_by_chapter.setdefault(row[0], []).append(row)

        user_ids = {row[2] for row in rows if row[2]}
        users = persistence.load_users(list(user_ids)) if user_ids else {}

        series_calls, series_credits, series_failed, series_models, series_members = (
            aggregate_usage_rows(rows)
        )
        fill_usage_members(series_members, users)

        chapter_items: list[dict[str, Any]] = []
        for chapter in chapters:
            calls, credits, failed_calls, by_model, by_member = aggregate_usage_rows(
                rows_by_chapter.get(chapter["id"], [])
            )
            if calls == 0 and failed_calls == 0:
                continue
            fill_usage_members(by_member, users)
            chapter_items.append(
                {
                    "chapter_id": chapter["id"],
                    "chapter_title": chapter["title"],
                    "calls": calls,
                    "credits": credits,
                    "failed_calls": failed_calls,
                    "by_model": by_model,
                    "by_member": by_member,
                }
            )

        return {
            "series_id": series["id"],
            "series_name": series["name"],
            "series_total": {
                "calls": series_calls,
                "credits": series_credits,
                "failed_calls": series_failed,
                "by_model": series_models,
                "by_member": series_members,
            },
            "chapters": chapter_items,
        }

    return _run_read_only(factory, operation)


def team_model_usage(
    factory: ReportingUnitOfWorkFactory | None,
    actor: TrustedActor,
    team_id: str,
    model_name: str,
    start_time: str | None = None,
    end_time: str | None = None,
) -> dict[str, Any]:
    def operation(uow: ReportingUnitOfWork) -> dict[str, Any]:
        _require_team_permission(uow, team_id, actor.user_id, "view_usage")
        _require_team(uow, team_id)
        start, end = parse_date_range(start_time, end_time)
        persistence = uow
        series = persistence.list_team_series(team_id, newest_first=False)
        if not series:
            return {
                "model_name": model_name,
                "total_calls": 0,
                "total_credits": 0,
                "failed_calls": 0,
                "by_series": [],
                "by_member": [],
            }

        rows = persistence.load_model_usage_rows(
            [row["id"] for row in series],
            model_name,
            start,
            end,
        )
        series_names = {row["id"]: row["name"] for row in series}
        by_series: dict[Any, dict[str, Any]] = defaultdict(
            lambda: {
                "series_id": "",
                "series_name": "",
                "calls": 0,
                "credits": 0,
                "failed_calls": 0,
            }
        )
        by_member: dict[Any, dict[str, Any]] = defaultdict(
            lambda: {
                "user_id": "",
                "calls": 0,
                "credits": 0,
                "failed_calls": 0,
            }
        )
        total_calls = 0
        total_credits: Any = 0
        total_failed = 0

        for series_id, user_id, status, calls, credits in rows:
            series_item = by_series[series_id]
            series_item["series_id"] = series_id
            series_item["series_name"] = series_names.get(series_id, "未知剧集")
            member_item = by_member[user_id]
            member_item["user_id"] = user_id
            if status == "failed":
                total_failed += calls
                series_item["failed_calls"] += calls
                member_item["failed_calls"] += calls
            else:
                total_calls += calls
                total_credits += credits
                series_item["calls"] += calls
                series_item["credits"] += credits
                member_item["calls"] += calls
                member_item["credits"] += credits

        sorted_series = sorted(
            by_series.values(),
            key=lambda item: (-item["calls"], -item["credits"]),
        )
        sorted_members = sorted(
            by_member.values(),
            key=lambda item: (-item["calls"], -item["credits"]),
        )
        user_ids = {member["user_id"] for member in sorted_members if member["user_id"]}
        users = persistence.load_users(list(user_ids)) if user_ids else {}
        fill_usage_members(sorted_members, users)

        return {
            "model_name": model_name,
            "total_calls": total_calls,
            "total_credits": total_credits,
            "failed_calls": total_failed,
            "by_series": sorted_series,
            "by_member": sorted_members,
        }

    return _run_read_only(factory, operation)


def team_usage_export(
    factory: ReportingUnitOfWorkFactory | None,
    actor: TrustedActor,
    team_id: str,
    start_time: str | None = None,
    end_time: str | None = None,
) -> dict[str, Any]:
    def operation(uow: ReportingUnitOfWork) -> dict[str, Any]:
        _require_team_permission(uow, team_id, actor.user_id, "view_usage")
        _require_team(uow, team_id)
        persistence = uow
        series = persistence.list_team_series(team_id, newest_first=False)
        if not series:
            return {
                "rows": [],
                "members": [],
                "total_calls": 0,
                "total_credits": 0,
                "total_failed": 0,
            }

        start, end = parse_date_range(start_time, end_time)
        raw = persistence.load_export_usage_rows(
            [row["id"] for row in series],
            start,
            end,
        )
        user_ids = {row[0] for row in raw if row[0]}
        users = persistence.load_users(list(user_ids)) if user_ids else {}
        series_names = {row["id"]: row["name"] for row in series}
        chapter_ids = {row[2] for row in raw if row[2]}
        chapter_titles = persistence.load_chapter_titles(list(chapter_ids))

        detail: dict[tuple[Any, ...], dict[str, Any]] = defaultdict(
            lambda: {"calls": 0, "credits": 0, "failed_calls": 0}
        )
        member_aggregates: dict[Any, dict[str, Any]] = defaultdict(
            lambda: {
                "user_id": "",
                "chapters": set(),
                "models": set(),
                "calls": 0,
                "credits": 0,
                "failed_calls": 0,
            }
        )
        all_models: set[str] = set()
        for user_id, series_id, chapter_id, model_name, status, calls, credits in raw:
            normalized_model = model_name or "未知模型"
            all_models.add(normalized_model)
            detail_row = detail[(user_id, series_id, chapter_id, normalized_model)]
            member = member_aggregates[user_id]
            member["user_id"] = user_id
            member["chapters"].add(chapter_id)
            member["models"].add(normalized_model)
            if status == "failed":
                detail_row["failed_calls"] += calls
                member["failed_calls"] += calls
            else:
                detail_row["calls"] += calls
                detail_row["credits"] += credits
                member["calls"] += calls
                member["credits"] += credits

        model_type_by_name: dict[str, str] = {}
        for model_name, model_type in persistence.load_export_model_types(list(all_models)):
            model_type_by_name.setdefault(model_name, model_type or "")
        ordered_models = order_export_models(all_models, model_type_by_name)

        rows: list[dict[str, Any]] = []
        for (user_id, series_id, chapter_id, model_name), values in detail.items():
            user = users.get(user_id)
            rows.append(
                {
                    "user_id": user_id,
                    "username": user.username if user else user_id,
                    "avatar_url": (
                        avatar_url_for_email(user.email) if user else None
                    ),
                    "series_id": series_id,
                    "series_name": series_names.get(series_id, ""),
                    "chapter_id": chapter_id,
                    "chapter_title": chapter_titles.get(chapter_id, ""),
                    "model_name": model_name,
                    "calls": values["calls"],
                    "credits": values["credits"],
                    "failed_calls": values["failed_calls"],
                }
            )
        rows.sort(
            key=lambda row: (
                row["series_name"],
                row["chapter_title"],
                row["username"],
                row["model_name"],
            )
        )

        members: list[dict[str, Any]] = []
        for user_id, values in member_aggregates.items():
            user = users.get(user_id)
            members.append(
                {
                    "user_id": user_id,
                    "username": user.username if user else user_id,
                    "avatar_url": avatar_url_for_email(user.email) if user else None,
                    "chapter_count": len(values["chapters"]),
                    "models": member_model_names(values["models"]),
                    "calls": values["calls"],
                    "credits": values["credits"],
                    "failed_calls": values["failed_calls"],
                }
            )
        members.sort(key=lambda member: (-member["calls"], member["username"]))

        return {
            "rows": rows,
            "members": members,
            "model_order": ordered_models,
            "total_calls": sum(member["calls"] for member in members),
            "total_credits": sum(member["credits"] for member in members),
            "total_failed": sum(member["failed_calls"] for member in members),
        }

    return _run_read_only(factory, operation)
