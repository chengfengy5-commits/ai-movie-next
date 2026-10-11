"""Read-only readiness checks for foundation and generation schema state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from haoai_backend.generation_core.schema import (
    GENERATION_MIGRATION_HISTORY_TABLE,
    GENERATION_SCHEMA_CHECKSUM,
    GENERATION_SCHEMA_VERSION,
    check_foundation_schema,
    check_generation_record_schema,
    check_generation_version_set,
    check_migration_history_schema,
    sqlalchemy_catalog,
)


MIGRATION_HISTORY_TABLE = GENERATION_MIGRATION_HISTORY_TABLE


@dataclass(frozen=True, slots=True)
class ReadinessIssue:
    code: str
    detail: str


@dataclass(frozen=True, slots=True)
class GenerationReadiness:
    status: Literal["ready", "not_ready"]
    issues: tuple[ReadinessIssue, ...]


def _issues_from_checks(*checks) -> list[ReadinessIssue]:
    return [
        ReadinessIssue(issue.code, f"{issue.table}: {issue.detail}")
        for check in checks
        for issue in check.issues
    ]


def check_generation_readiness(session: Session) -> GenerationReadiness:
    """Check current schema through the supplied Session without flushing or writing."""

    issues: list[ReadinessIssue] = []
    try:
        with session.no_autoflush:
            connection = session.connection()
            catalog = sqlalchemy_catalog(connection)
            foundation = check_foundation_schema(catalog)
            issues.extend(_issues_from_checks(foundation))
            if issues:
                return GenerationReadiness("not_ready", tuple(issues))

            records = check_generation_record_schema(catalog)
            history = check_migration_history_schema(catalog)
            issues.extend(_issues_from_checks(records, history))
            if issues:
                return GenerationReadiness("not_ready", tuple(issues))

            rows = connection.execute(
                text(
                    f"SELECT version, checksum FROM {MIGRATION_HISTORY_TABLE}"
                )
            ).all()
            versions = tuple(str(row[0]) for row in rows)
            version_check = check_generation_version_set(versions)
            issues.extend(_issues_from_checks(version_check))
            if issues:
                return GenerationReadiness("not_ready", tuple(issues))

            current_rows = [row for row in rows if str(row[0]) == GENERATION_SCHEMA_VERSION]
            if len(current_rows) != 1 or str(current_rows[0][1]) != GENERATION_SCHEMA_CHECKSUM:
                issues.append(
                    ReadinessIssue(
                        "generation_migration_checksum_mismatch",
                        GENERATION_SCHEMA_VERSION,
                    )
                )
    except SQLAlchemyError as error:
        issues.append(ReadinessIssue("catalog_unavailable", type(error).__name__))

    return GenerationReadiness("ready" if not issues else "not_ready", tuple(issues))


__all__ = (
    "ReadinessIssue",
    "GenerationReadiness",
    "check_generation_readiness",
)
