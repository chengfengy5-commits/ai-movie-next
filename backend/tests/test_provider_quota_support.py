from __future__ import annotations

from collections import defaultdict, deque
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

from haoai_backend.provider_quota.ports import ProviderQuotaHttpResponse


class FakeCipher:
    def encrypt(self, plain_text: str) -> str:
        return f"cipher:{plain_text}" if plain_text else ""

    def decrypt(self, cipher_text: str) -> str:
        return cipher_text.removeprefix("cipher:") if cipher_text else ""


class FakeProviderQuotaTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, str], Any, float]] = []
        self.responses: dict[tuple[str, str], deque[ProviderQuotaHttpResponse]] = defaultdict(deque)

    def enqueue(
        self,
        method: str,
        url: str,
        *,
        status: int = 200,
        payload: Any = None,
        text: str | None = None,
        headers: dict[str, str | tuple[str, ...]] | None = None,
        cookies: dict[str, str] | None = None,
    ) -> None:
        import json

        body = text if text is not None else json.dumps(payload or {})
        self.responses[(method.upper(), url)].append(
            ProviderQuotaHttpResponse(
                status=status,
                text=body,
                headers=headers or {},
                cookies=cookies or {},
            )
        )

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers=None,
        json_body=None,
        timeout_seconds: float,
    ) -> ProviderQuotaHttpResponse:
        key = (method.upper(), url)
        self.calls.append(
            (key[0], key[1], dict(headers or {}), json_body, timeout_seconds)
        )
        if not self.responses[key]:
            raise AssertionError(f"unexpected provider request: {key}")
        return self.responses[key].popleft()


class FakeAuthenticationService:
    def __init__(self, events: list[tuple[Any, ...]] | None = None) -> None:
        self.events = events if events is not None else []

    @contextmanager
    def authenticated(self, authorization: str, *, require_membership: bool):
        from haoai_backend.authentication.errors import credentials_error

        self.events.append(("auth-open", authorization, require_membership))
        if authorization == "Bearer admin":
            principal = SimpleNamespace(
                user=SimpleNamespace(id="admin", is_superuser=True)
            )
        elif authorization == "Bearer member":
            principal = SimpleNamespace(
                user=SimpleNamespace(id="member", is_superuser=False)
            )
        else:
            raise credentials_error()
        try:
            yield principal
        finally:
            self.events.append(("auth-close", authorization))
