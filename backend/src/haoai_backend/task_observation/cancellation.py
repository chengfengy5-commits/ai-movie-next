"""SQLAlchemy adapter for the independent task-cancellation transaction."""

from __future__ import annotations

from typing import cast

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .ports import CancellationConnectionFactory


_CANCEL_TASK_STATEMENT = text(
    "UPDATE ai_tasks SET status='cancelling' "
    "WHERE id=:id AND status IN ('queued','processing')"
)


class SqlTaskCancellationWriter:
    """Run the source-compatible status transition in a supplied transaction.

    The caller supplies an already-configured independent connection factory,
    typically Engine.begin. The context manager owns commit and rollback; this
    adapter neither discovers an Engine from a request Session nor retries an
    operation whose commit acknowledgement is unknown.
    """

    def __init__(self, connection_factory: CancellationConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def request_cancel(self, task_id: str) -> bool:
        """Commit the conditional status update and report whether it matched."""
        with self._connection_factory() as raw_connection:
            connection = cast(Connection, raw_connection)
            result = connection.execute(
                _CANCEL_TASK_STATEMENT,
                {"id": task_id},
            )
            return (result.rowcount or 0) > 0
