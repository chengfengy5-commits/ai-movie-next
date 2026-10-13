from __future__ import annotations

import builtins
import os
import sys

import pytest

from haoai_backend.admin_statistics.balance import BALANCE_URL, RequestsBalanceReader
from haoai_backend.admin_statistics.http import build_admin_statistics_router


FAKE_TOKEN = "statistics-fake-token"


def test_balance_transport_is_lazy_exact_and_preserves_json_values(monkeypatch):
    calls = []
    class Response:
        ok = True
        def json(self):
            return {"balance": "not-normalized"}
    reader = RequestsBalanceReader(lambda *args, **kwargs: calls.append((args, kwargs)) or Response())
    assert reader.read_balance("") is None
    assert calls == []
    assert reader.read_balance(FAKE_TOKEN) == "not-normalized"
    assert calls == [(
        (BALANCE_URL,),
        {"headers": {"Authorization": f"Bearer {FAKE_TOKEN}"}, "timeout": 3},
    )]

    class MissingBalanceResponse:
        ok = True
        def json(self):
            return {}
    assert RequestsBalanceReader(lambda *_a, **_k: MissingBalanceResponse()).read_balance(FAKE_TOKEN) == 0

    class UnavailableResponse:
        ok = False
    assert RequestsBalanceReader(lambda *_a, **_k: UnavailableResponse()).read_balance(FAKE_TOKEN) is None


def test_router_and_reader_construction_do_not_import_requests_or_read_environment(monkeypatch):
    original_import = builtins.__import__
    def guarded_import(name, *args, **kwargs):
        if name == "requests":
            raise AssertionError("requests is only imported for a truthy token")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setenv("MUSE_TOKEN", "must-not-be-read")
    reader = RequestsBalanceReader()
    router = build_admin_statistics_router(
        authentication_service=None,
        session_factory=None,
        balance_reader=reader,
    )
    assert router.routes
    assert reader.read_balance("") is None
