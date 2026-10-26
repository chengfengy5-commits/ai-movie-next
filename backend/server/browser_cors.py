"""Startup-selected CORS policy for the explicit server host."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Callable, Sequence
from typing import Any

from sqlalchemy import Column, MetaData, Table, Text, select
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from haoai_backend.browser_cors import configure_browser_cors


logger = logging.getLogger(__name__)

_SYSTEM_CONFIG_METADATA = MetaData()
_SYSTEM_CONFIG = Table(
    "system_configs",
    _SYSTEM_CONFIG_METADATA,
    Column("allowed_origins", Text),
)
_DATABASE_READ_TIMEOUT_SECONDS = 10.0


class ServerBrowserCorsApp:
    """Read the database policy once before startup and delegate all ASGI traffic."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        session_factory: Callable[[], Any],
        fallback_origins: Sequence[str] = (),
        read_timeout_seconds: float = _DATABASE_READ_TIMEOUT_SECONDS,
    ) -> None:
        if not callable(session_factory):
            raise TypeError("server CORS session factory must be callable")
        if (
            isinstance(read_timeout_seconds, bool)
            or not isinstance(read_timeout_seconds, (int, float))
            or not math.isfinite(read_timeout_seconds)
            or read_timeout_seconds <= 0
        ):
            raise ValueError("server CORS read timeout must be finite and positive")

        self._inner_app = app
        self._session_factory = session_factory
        self._fallback_origins = tuple(fallback_origins)
        self._fallback_app = configure_browser_cors(
            app,
            allowed_origins=self._fallback_origins,
        )
        self._serving_app = self._fallback_app
        self._read_timeout_seconds = float(read_timeout_seconds)
        self._pending_reads: set[asyncio.Task[object]] = set()

    @property
    def pending_read_count(self) -> int:
        """Count asyncio read bridges retained by this ASGI wrapper."""

        return len(self._pending_reads)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "lifespan":
            await self._serving_app(scope, receive, send)
            return

        startup_resolved = False

        async def receive_after_policy_selection() -> Message:
            nonlocal startup_resolved
            message = await receive()
            if message.get("type") == "lifespan.startup" and not startup_resolved:
                startup_resolved = True
                self._serving_app = await self._select_startup_app()
            return message

        await self._inner_app(scope, receive_after_policy_selection, send)

    async def _select_startup_app(self) -> ASGIApp:
        bridge = asyncio.create_task(asyncio.to_thread(self._read_database_origins))
        self._pending_reads.add(bridge)
        bridge.add_done_callback(self._consume_read)
        completed, _pending = await asyncio.wait(
            (bridge,),
            timeout=self._read_timeout_seconds,
        )
        if bridge not in completed:
            logger.warning("Browser CORS database read timed out; using fallback policy")
            return self._fallback_app

        try:
            database_value = bridge.result()
        except Exception:
            logger.warning("Browser CORS database read failed; using fallback policy")
            return self._fallback_app

        if database_value is None or (
            isinstance(database_value, str) and not database_value.strip()
        ):
            return self._fallback_app

        if not isinstance(database_value, str):
            logger.error("Invalid database ALLOWED_ORIGINS; browser CORS disabled")
            return self._inner_app

        origins = tuple(
            origin.strip()
            for origin in database_value.split(",")
            if origin.strip()
        )
        if not origins:
            logger.error("Invalid database ALLOWED_ORIGINS; browser CORS disabled")
            return self._inner_app

        try:
            return configure_browser_cors(
                self._inner_app,
                allowed_origins=origins,
            )
        except (TypeError, ValueError):
            logger.error("Invalid database ALLOWED_ORIGINS; browser CORS disabled")
            return self._inner_app

    def _read_database_origins(self) -> object:
        session = self._session_factory()
        try:
            return session.execute(
                select(_SYSTEM_CONFIG.c.allowed_origins).limit(1)
            ).scalar_one_or_none()
        finally:
            try:
                session.rollback()
            finally:
                session.close()

    def _consume_read(self, task: asyncio.Task[object]) -> None:
        self._pending_reads.discard(task)
        if task.cancelled():
            return
        try:
            task.exception()
        except BaseException:
            pass


__all__ = ("ServerBrowserCorsApp",)
