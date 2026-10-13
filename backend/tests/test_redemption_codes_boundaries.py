from __future__ import annotations

import ast
import re
from pathlib import Path

from fastapi import FastAPI
from pydantic import ValidationError

from haoai_backend.redemption_codes.code_generator import generate_code_string
from haoai_backend.redemption_codes.http import build_redemption_codes_router
from haoai_backend.redemption_codes.schemas import GenerateRequest, RedeemRequest

PACKAGE = Path(__file__).parents[1] / "src" / "haoai_backend" / "redemption_codes"


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_domain_ports_and_application_are_framework_and_orm_free():
    for filename in ("domain.py", "ports.py", "application.py"):
        modules = imported_modules(PACKAGE / filename)
        assert not any(
            name == "fastapi"
            or name.startswith("fastapi.")
            or name == "sqlalchemy"
            or name.startswith("sqlalchemy.")
            or name == "app"
            or name.startswith("app.")
            or name.startswith("haoai_backend.app")
            for name in modules
        )
    for path in PACKAGE.glob("*.py"):
        assert not any(module.startswith("app.") for module in imported_modules(path))


def test_inert_router_registers_exact_two_routes_without_startup_or_factory_calls():
    calls = 0

    def unused_session_factory():
        nonlocal calls
        calls += 1
        raise AssertionError("router construction must not open a business session")

    router = build_redemption_codes_router(
        authentication_service=None,
        session_factory=unused_session_factory,
    )
    routes = [route for route in router.routes if route.path.startswith("/api/redemption-codes")]
    assert {
        (method, route.path)
        for route in routes
        for method in route.methods
    } == {
        ("POST", "/api/redemption-codes/generate"),
        ("POST", "/api/redemption-codes/redeem"),
    }
    assert all(route.response_model is not None for route in routes)
    app = FastAPI()
    app.include_router(router)
    assert app.router.on_startup == []
    assert calls == 0


def test_legacy_request_schema_constraints_and_default_count():
    generated = GenerateRequest(type="membership", value=30)
    assert generated.count == 1 and generated.expires_at is None
    assert GenerateRequest(type="credits", value=4, count=1000).count == 1000
    assert RedeemRequest(code=" ").code == " "
    for body in (
        {"type": "other", "value": 1},
        {"type": "credits", "value": 0},
        {"type": "credits", "value": 1, "count": 0},
        {"type": "credits", "value": 1, "count": 1001},
    ):
        try:
            GenerateRequest.model_validate(body)
        except ValidationError:
            continue
        raise AssertionError(f"invalid legacy request unexpectedly accepted: {body}")
    for code in ("", "x" * 33):
        try:
            RedeemRequest.model_validate({"code": code})
        except ValidationError:
            continue
        raise AssertionError(f"invalid redemption code unexpectedly accepted: {code!r}")


def test_generated_codes_keep_the_legacy_format():
    assert re.fullmatch(r"HAOAI-[A-Z0-9]{4}-[A-Z0-9]{4}", generate_code_string())
