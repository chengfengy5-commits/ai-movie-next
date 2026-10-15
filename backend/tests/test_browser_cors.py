from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from haoai_backend.browser_cors import configure_browser_cors


ALLOWED_ORIGIN = "https://app.example.com"
OTHER_ORIGIN = "https://other.example.com"
DIAGNOSTIC_TOKEN = "diagnostic-token-must-not-be-reflected"


def request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async def send() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://backend.test",
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


def make_app() -> FastAPI:
    app = FastAPI()
    app.state.ok_calls = 0

    @app.get("/ok")
    async def ok():
        app.state.ok_calls += 1
        return {"status": "ok"}

    @app.get("/runtime-unavailable")
    async def runtime_unavailable():
        raise HTTPException(status_code=503, detail="认证服务尚未接线")

    @app.get("/explode")
    async def explode():
        raise RuntimeError("unexpected failure")

    return app


def test_empty_configuration_returns_the_original_app_without_cors() -> None:
    app = make_app()

    configured = configure_browser_cors(app)

    assert configured is app
    response = request(
        configured,
        "GET",
        "/ok",
        headers={"Origin": ALLOWED_ORIGIN},
    )
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
    preflight = request(
        configured,
        "OPTIONS",
        "/ok",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert preflight.status_code == 405
    assert "access-control-allow-origin" not in preflight.headers


def test_allowed_origin_receives_exact_cors_headers_without_credentials() -> None:
    app = configure_browser_cors(make_app(), allowed_origins=[ALLOWED_ORIGIN])

    response = request(
        app,
        "GET",
        "/ok",
        headers={"Origin": ALLOWED_ORIGIN, "Authorization": f"Bearer {DIAGNOSTIC_TOKEN}"},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert "origin" in response.headers["vary"].lower()
    assert "access-control-allow-credentials" not in response.headers
    assert DIAGNOSTIC_TOKEN not in response.text


def test_runtime_503_remains_readable_to_an_allowed_browser_origin() -> None:
    app = configure_browser_cors(make_app(), allowed_origins=[ALLOWED_ORIGIN])

    response = request(
        app,
        "GET",
        "/runtime-unavailable",
        headers={"Origin": ALLOWED_ORIGIN},
    )

    assert response.status_code == 503
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert "认证服务尚未接线" in response.text


def test_outer_cors_wrapper_adds_headers_to_unhandled_500() -> None:
    app = configure_browser_cors(make_app(), allowed_origins=[ALLOWED_ORIGIN])

    response = request(app, "GET", "/explode", headers={"Origin": ALLOWED_ORIGIN})

    assert response.status_code == 500
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN


def test_no_store_preflight_allows_only_the_explicit_request_headers() -> None:
    inner_app = make_app()
    app = configure_browser_cors(inner_app, allowed_origins=[ALLOWED_ORIGIN])

    response = request(
        app,
        "OPTIONS",
        "/ok",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization,content-type,cache-control,pragma",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert set(response.headers["access-control-allow-headers"].lower().split(", ")) >= {
        "authorization",
        "content-type",
        "cache-control",
        "pragma",
    }
    assert "access-control-allow-credentials" not in response.headers
    assert inner_app.state.ok_calls == 0


def test_idempotent_ai_preflight_allows_auth_json_and_no_store_headers() -> None:
    app = configure_browser_cors(make_app(), allowed_origins=[ALLOWED_ORIGIN])

    response = request(
        app,
        "OPTIONS",
        "/ok",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": (
                "authorization,content-type,idempotency-key,cache-control,pragma"
            ),
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    allowed_headers = set(
        response.headers["access-control-allow-headers"].lower().split(", ")
    )
    assert {
        "authorization",
        "content-type",
        "idempotency-key",
        "cache-control",
        "pragma",
    } <= allowed_headers


@pytest.mark.parametrize(
    "origin",
    [
        OTHER_ORIGIN,
        "null",
        "http://localhost:5181",
    ],
)
def test_other_origins_do_not_receive_allow_origin(origin: str) -> None:
    app = configure_browser_cors(make_app(), allowed_origins=[ALLOWED_ORIGIN])

    response = request(
        app,
        "GET",
        "/ok",
        headers={"Origin": origin},
    )

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") != origin


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "OPTIONS"])
def test_allowed_methods_pass_preflight(method: str) -> None:
    app = configure_browser_cors(make_app(), allowed_origins=[ALLOWED_ORIGIN])

    response = request(
        app,
        "OPTIONS",
        "/ok",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": method,
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN


def test_disallowed_preflight_headers_are_rejected() -> None:
    app = configure_browser_cors(make_app(), allowed_origins=[ALLOWED_ORIGIN])

    response = request(
        app,
        "OPTIONS",
        "/ok",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-unapproved",
        },
    )

    assert response.status_code == 400
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN


def test_disallowed_preflight_method_is_rejected() -> None:
    app = configure_browser_cors(make_app(), allowed_origins=[ALLOWED_ORIGIN])

    response = request(
        app,
        "OPTIONS",
        "/ok",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "PATCH",
        },
    )

    assert response.status_code == 400


@pytest.mark.parametrize(
    "origin",
    [
        "*",
        "null",
        "",
        "ftp://app.example.com",
        "https://user:password@app.example.com",
        "https://app.example.com/path",
        "https://app.example.com?mode=1",
        "https://app.example.com#section",
        "https://app.example.com:invalid",
        "https://app.example.com:",
        "https://app example.com",
    ],
)
def test_invalid_allowed_origin_is_rejected(origin: str) -> None:
    with pytest.raises(ValueError):
        configure_browser_cors(make_app(), allowed_origins=[origin])


def test_unicode_hostname_is_rejected_instead_of_idna_remapped() -> None:
    with pytest.raises(ValueError):
        configure_browser_cors(make_app(), allowed_origins=["https://faß.example.com"])


def test_ascii_punycode_origin_matches_only_the_explicit_origin() -> None:
    origin = "https://xn--fa-hia.example.com"
    app = configure_browser_cors(make_app(), allowed_origins=[origin])

    matching_response = request(app, "GET", "/ok", headers={"Origin": origin})
    remapped_response = request(
        app,
        "GET",
        "/ok",
        headers={"Origin": "https://fass.example.com"},
    )

    assert matching_response.headers["access-control-allow-origin"] == origin
    assert "access-control-allow-origin" not in remapped_response.headers


def test_default_ports_are_normalized_and_external_https_origins_are_supported() -> None:
    app = configure_browser_cors(
        make_app(),
        allowed_origins=["HTTPS://App.Example.Com:443", "http://localhost:80"],
    )

    response = request(
        app,
        "GET",
        "/ok",
        headers={"Origin": ALLOWED_ORIGIN},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN

