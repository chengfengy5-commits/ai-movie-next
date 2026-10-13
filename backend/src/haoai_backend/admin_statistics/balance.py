"""Lazy HTTP adapter for the fixed loopback upstream balance endpoint."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


BALANCE_URL = "http://127.0.0.1:8765/v1/cash-accounts/mine"


class RequestsBalanceReader:
    def __init__(self, transport: Callable[..., Any] | None = None) -> None:
        self._transport = transport

    def read_balance(self, token: str | None) -> Any:
        if not token:
            return None
        transport = self._transport
        if transport is None:
            import requests

            transport = requests.get
        response = transport(
            BALANCE_URL,
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        if response.ok:
            return response.json().get("balance", 0)
        return None
