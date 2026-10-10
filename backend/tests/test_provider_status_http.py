"""Owned aiohttp client and response behavior on a local loopback server."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from aiohttp import web

from haoai_backend.provider_status import create_provider_status_service
from haoai_backend.provider_status.http import (
    create_aiohttp_provider_status_http_client_factory,
)
from provider_status_support import FakeConfigurationSession, make_configuration, make_runtime


async def start_server(app: web.Application) -> tuple[web.AppRunner, str]:
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    assert site._server is not None
    port = site._server.sockets[0].getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    print(f"provider_status_loopback_bound {base_url}", flush=True)
    return runner, base_url


def build_local_query(base_url: str, factory: Any):
    session = FakeConfigurationSession(
        system_configuration=make_configuration(
            "xai", base_url=base_url, api_key="loopback-test-key"
        )
    )
    query = create_provider_status_service(make_runtime(session, factory)).prepare("xai")
    assert session.closed
    assert query is not None
    return query


def test_actual_loopback_get_closes_response_and_client_on_success_and_non_200() -> None:
    async def run() -> None:
        requests: list[tuple[str, str]] = []

        async def success(request: web.Request) -> web.Response:
            requests.append((request.path, request.headers.get("Authorization", "")))
            return web.json_response(
                {"status": "done", "video": {"url": "http://127.0.0.1/video.mp4"}}
            )

        async def unavailable(request: web.Request) -> web.Response:
            requests.append((request.path, request.headers.get("Authorization", "")))
            return web.json_response({"status": "completed"}, status=503)

        app = web.Application()
        app.router.add_get("/v1/videos/success-id", success)
        app.router.add_get("/v1/videos/unavailable-id", unavailable)
        runner, base_url = await start_server(app)
        base_factory = create_aiohttp_provider_status_http_client_factory()
        contexts = []

        def tracking_factory(use_ipv4: bool):
            context = base_factory(use_ipv4)
            contexts.append(context)
            return context

        try:
            query = build_local_query(base_url, tracking_factory)
            status, payload = await query.poll_once("success-id")
            assert status == "completed"
            assert query.extract_result(payload) == "http://127.0.0.1/video.mp4"
            assert contexts[-1].closed
            assert contexts[-1].response_contexts[0].closed

            status, payload = await query.poll_once("unavailable-id")
            assert (status, payload) == ("pending", {})
            assert contexts[-1].closed
            assert contexts[-1].response_contexts[0].closed
            assert requests == [
                ("/v1/videos/success-id", "Bearer loopback-test-key"),
                ("/v1/videos/unavailable-id", "Bearer loopback-test-key"),
            ]
        finally:
            await runner.cleanup()
            print("provider_status_loopback_cleanup complete", flush=True)

    asyncio.run(run())


def test_bad_json_and_cancellation_close_owned_http_resources() -> None:
    async def run() -> None:
        response_started = asyncio.Event()
        release_handler = asyncio.Event()

        async def bad_json(_request: web.Request) -> web.Response:
            return web.Response(text="not-json", content_type="application/json")

        async def slow_json(request: web.Request) -> web.StreamResponse:
            response = web.StreamResponse(
                status=200,
                headers={"Content-Type": "application/json"},
            )
            await response.prepare(request)
            await response.write(b'{"status":')
            response_started.set()
            await release_handler.wait()
            await response.write(b'"done"}')
            return response

        app = web.Application()
        app.router.add_get("/v1/videos/bad-json", bad_json)
        app.router.add_get("/v1/videos/slow-json", slow_json)
        runner, base_url = await start_server(app)
        base_factory = create_aiohttp_provider_status_http_client_factory()
        contexts = []

        def tracking_factory(use_ipv4: bool):
            context = base_factory(use_ipv4)
            contexts.append(context)
            return context

        try:
            query = build_local_query(base_url, tracking_factory)
            with pytest.raises(ValueError):
                await query.poll_once("bad-json")
            assert contexts[-1].closed
            assert contexts[-1].response_contexts[0].closed

            request_task = asyncio.create_task(query.poll_once("slow-json"))
            await asyncio.wait_for(response_started.wait(), timeout=2)
            request_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request_task
            release_handler.set()
            await asyncio.sleep(0)
            assert contexts[-1].closed
            assert contexts[-1].response_contexts[0].closed
        finally:
            release_handler.set()
            await runner.cleanup()
            print("provider_status_loopback_cleanup complete", flush=True)

    asyncio.run(run())


def test_aiohttp_adapter_applies_timeout_and_closes_client_context() -> None:
    async def run() -> None:
        request_started = asyncio.Event()
        release_handler = asyncio.Event()

        async def slow_response(request: web.Request) -> web.StreamResponse:
            request_started.set()
            await release_handler.wait()
            return web.Response(text="late")

        app = web.Application()
        app.router.add_get("/slow", slow_response)
        runner, base_url = await start_server(app)
        context = create_aiohttp_provider_status_http_client_factory()(False)

        try:
            with pytest.raises(asyncio.TimeoutError):
                async with context as client:
                    async with client.get(
                        f"{base_url}/slow",
                        headers={},
                        timeout_seconds=0.1,
                    ):
                        pass
            assert context.closed
            await asyncio.wait_for(request_started.wait(), timeout=2)
        finally:
            release_handler.set()
            await runner.cleanup()
            print("provider_status_loopback_cleanup complete", flush=True)

    asyncio.run(run())
