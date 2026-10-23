"""The production aiohttp adapter is exercised only against loopback handlers."""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import web

from backend.server.http import AiohttpAsyncHttpClientFactory


def test_aiohttp_adapter_streams_disables_redirects_and_closes_after_timeout() -> None:
    async def run() -> None:
        state = {"redirect_target_calls": 0}

        async def redirect(_request: web.Request) -> web.Response:
            return web.Response(status=302, headers={"Location": "/redirect-target"})

        async def redirect_target(_request: web.Request) -> web.Response:
            state["redirect_target_calls"] += 1
            return web.Response(text="unexpected redirect")

        async def streamed(_request: web.Request) -> web.StreamResponse:
            response = web.StreamResponse(status=200)
            await response.prepare(_request)
            await response.write(b"first-")
            await asyncio.sleep(0.2)
            try:
                await response.write(b"second")
            except ConnectionResetError:
                pass
            return response

        application = web.Application()
        application.router.add_get("/redirect", redirect)
        application.router.add_get("/redirect-target", redirect_target)
        application.router.add_get("/stream", streamed)
        runner = web.AppRunner(application)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        assert site._server is not None
        port = site._server.sockets[0].getsockname()[1]
        base_url = f"http://127.0.0.1:{port}"
        factory = AiohttpAsyncHttpClientFactory()

        context = factory()
        async with context as client:
            with pytest.raises(ValueError, match="redirects are disabled"):
                await client.request(
                    "GET",
                    base_url + "/redirect",
                    timeout_seconds=1,
                    follow_redirects=True,
                )
            response = await client.request(
                "GET", base_url + "/redirect", timeout_seconds=1
            )
            assert response.status_code == 302
            assert [chunk async for chunk in response.iter_bytes(chunk_size=1024)] == []
            assert state["redirect_target_calls"] == 0

            timed_response = await client.request(
                "GET", base_url + "/stream", timeout_seconds=0.06
            )
            with pytest.raises(TimeoutError):
                async for _chunk in timed_response.iter_bytes(chunk_size=4):
                    pass
            assert timed_response._closed is True

            canceled_response = await client.request(
                "GET", base_url + "/stream", timeout_seconds=1
            )

            async def consume_response() -> None:
                async for _chunk in canceled_response.iter_bytes(chunk_size=4):
                    pass

            consumer = asyncio.create_task(consume_response())
            await asyncio.sleep(0.02)
            consumer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await consumer
            assert canceled_response._closed is True

        assert context._session is None
        await runner.cleanup()

    asyncio.run(run())
