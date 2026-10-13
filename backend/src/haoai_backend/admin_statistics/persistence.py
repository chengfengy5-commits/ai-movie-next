"""SQLAlchemy Core adapter for fixed legacy statistics aggregations."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from .ports import AdminStatisticsUnitOfWork
from .tables import (
    SystemConfigRow,
    ai_tasks,
    chapters,
    characters,
    credit_logs,
    props,
    scenes,
    series,
    user_credits,
    users,
)


class SqlAlchemyAdminStatisticsUnitOfWork(AdminStatisticsUnitOfWork):
    def __init__(self, session: Session) -> None:
        self._session = session

    def aggregate_statistics(self, now: datetime, month_start: datetime) -> dict[str, Any]:
        session = self._session
        total_users = session.query(func.count(users.c.id)).scalar() or 0
        premium_users = (
            session.query(func.count(users.c.id))
            .filter(users.c.membership_type == "premium")
            .scalar()
            or 0
        )
        active_members = (
            session.query(func.count(users.c.id))
            .filter(
                users.c.membership_type == "premium",
                or_(
                    users.c.membership_expires_at.is_(None),
                    users.c.membership_expires_at > now,
                ),
            )
            .scalar()
            or 0
        )
        new_users_month = (
            session.query(func.count(users.c.id))
            .filter(users.c.created_at >= month_start)
            .scalar()
            or 0
        )

        total_credits = (
            session.query(func.coalesce(func.sum(user_credits.c.credits), 0)).scalar()
            or 0
        )
        credits_used_month = (
            session.query(func.coalesce(func.sum(credit_logs.c.amount), 0))
            .filter(
                credit_logs.c.amount < 0,
                credit_logs.c.created_at >= month_start,
            )
            .scalar()
            or 0
        )
        credits_charged_month = (
            session.query(func.coalesce(func.sum(credit_logs.c.amount), 0))
            .filter(
                credit_logs.c.amount > 0,
                credit_logs.c.created_at >= month_start,
            )
            .scalar()
            or 0
        )

        total_tasks = session.query(func.count(ai_tasks.c.id)).scalar() or 0
        tasks_month = (
            session.query(func.count(ai_tasks.c.id))
            .filter(ai_tasks.c.created_at >= month_start)
            .scalar()
            or 0
        )
        tasks_completed = (
            session.query(func.count(ai_tasks.c.id))
            .filter(ai_tasks.c.status == "completed")
            .scalar()
            or 0
        )
        tasks_failed = (
            session.query(func.count(ai_tasks.c.id))
            .filter(ai_tasks.c.status == "failed")
            .scalar()
            or 0
        )

        total_series = session.query(func.count(series.c.id)).scalar() or 0
        total_chapters = session.query(func.count(chapters.c.id)).scalar() or 0
        total_characters = session.query(func.count(characters.c.id)).scalar() or 0
        total_scenes = session.query(func.count(scenes.c.id)).scalar() or 0
        total_props = session.query(func.count(props.c.id)).scalar() or 0

        return {
            "total_users": total_users,
            "premium_users": premium_users,
            "active_members": active_members,
            "new_users_month": new_users_month,
            "total_credits": total_credits,
            "credits_used_month": credits_used_month,
            "credits_charged_month": credits_charged_month,
            "total_tasks": total_tasks,
            "tasks_month": tasks_month,
            "tasks_completed": tasks_completed,
            "tasks_failed": tasks_failed,
            "total_series": total_series,
            "total_chapters": total_chapters,
            "total_characters": total_characters,
            "total_scenes": total_scenes,
            "total_props": total_props,
        }

    def get_muse_token(self) -> str | None:
        config = self._session.query(SystemConfigRow).first()
        return config.muse_token if config is not None else ""

    def rollback(self) -> None:
        self._session.rollback()

    def close(self) -> None:
        self._session.close()
