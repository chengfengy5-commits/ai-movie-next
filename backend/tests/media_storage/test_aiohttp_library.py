from __future__ import annotations

import asyncio
import concurrent.futures
import socket
import ssl
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from shutil import which
from urllib.parse import urlsplit

import aiohttp
from yarl import URL

from haoai_backend.media_storage.aiohttp_library import AiohttpMatureHttpLibrary
from haoai_backend.media_storage.pinned_http import NumericDialAddress


class _FixedNumericResolver:
    def __init__(self) -> None:
        self.closed = False
        self.calls: list[tuple[str, int, int]] = []

    async def resolve(
        self, host: str, port: int, *, family: int = socket.AF_UNSPEC
    ) -> tuple[NumericDialAddress, ...]:
        self.calls.append((host, port, family))
        return (
            NumericDialAddress(
                family=socket.AF_INET,
                host="127.0.0.1",
                port=port,
                proto=socket.IPPROTO_TCP,
                flags=socket.AI_NUMERICHOST,
                sockaddr=("127.0.0.1", port),
            ),
        )

    async def close(self) -> None:
        self.closed = True


def _library(*, ssl_context: ssl.SSLContext | None = None):
    connector_calls: list[tuple[object, object, bool]] = []

    def connector_factory(*, resolver: object, ssl: object, use_dns_cache: bool):
        connector_calls.append((resolver, ssl, use_dns_cache))
        selected_ssl = ssl_context if ssl_context is not None else ssl
        return aiohttp.TCPConnector(
            resolver=resolver,
            ssl=selected_ssl,
            use_dns_cache=use_dns_cache,
        )

    library = AiohttpMatureHttpLibrary(
        url_factory=lambda raw_url, *, encoded: URL(raw_url, encoded=encoded),
        connector_factory=connector_factory,
        session_factory=aiohttp.ClientSession,
        timeout_factory=aiohttp.ClientTimeout,
        cookie_jar_factory=aiohttp.DummyCookieJar,
    )
    return library, connector_calls


class AiohttpLibraryTests(unittest.IsolatedAsyncioTestCase):
    async def _request(
        self,
        url: str,
        *,
        ssl_context: ssl.SSLContext | None = None,
    ):
        library, connector_calls = _library(ssl_context=ssl_context)
        parsed = urlsplit(url)
        resolver = _FixedNumericResolver()
        connector = library.connector(
            resolver,
            tls_hostname=parsed.hostname or "",
            verify_tls=True,
            use_dns_cache=False,
        )
        session = library.session(
            connector,
            trust_env=False,
            cookies_enabled=False,
            auto_decompress=True,
            connector_owned_by_session=False,
        )
        try:
            target = library.encoded_url(url, preserve_encoded=True)
            response = await session.get(
                target,
                headers={"Host": parsed.netloc},
                timeout_seconds=3,
                allow_redirects=False,
            )
            try:
                body = b"".join([chunk async for chunk in response.iter_chunks(65536)])
                return response.status, dict(response.headers), body, resolver, connector_calls
            finally:
                await response.close()
        finally:
            await session.close()
            await connector.close()
            await resolver.close()

    async def test_real_connector_preserves_request_target_and_dials_numeric_address(self) -> None:
        observed: list[bytes] = []

        async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            request = await reader.readuntil(b"\r\n\r\n")
            observed.append(request)
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\n"
                b"Content-Length: 3\r\nConnection: close\r\n\r\npng"
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        try:
            port = server.sockets[0].getsockname()[1]
            url = f"http://origin.test:{port}/a%2fb?sig=a%2Fb&sig=two"
            status, headers, body, resolver, connector_calls = await self._request(url)
            request_line, host_line = observed[0].split(b"\r\n", 2)[:2]
            self.assertEqual(status, 200)
            self.assertEqual(body, b"png")
            self.assertEqual(request_line, b"GET /a%2fb?sig=a%2Fb&sig=two HTTP/1.1")
            self.assertEqual(host_line, f"Host: origin.test:{port}".encode("ascii"))
            self.assertEqual(headers["Content-Type"], "image/png")
            self.assertEqual(resolver.calls, [("origin.test", port, socket.AF_UNSPEC)])
            self.assertEqual(connector_calls[0][1], True)
            self.assertIs(connector_calls[0][2], False)
            self.assertTrue(resolver.closed)
        finally:
            server.close()
            await server.wait_closed()

    async def test_tls_uses_original_sni_and_rejects_wrong_certificate_hostname(self) -> None:
        openssl = which("openssl")
        if openssl is None:
            self.fail("OpenSSL is required for the local TLS fixture")
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            cert = directory / "origin.pem"
            key = directory / "origin-key.pem"
            process = subprocess.Popen(
                [
                    openssl,
                    "req",
                    "-x509",
                    "-batch",
                    "-newkey",
                    "rsa:2048",
                    "-nodes",
                    "-keyout",
                    str(key),
                    "-out",
                    str(cert),
                    "-days",
                    "1",
                    "-subj",
                    "/CN=origin.test",
                    "-addext",
                    "subjectAltName=DNS:origin.test",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            out, err = process.communicate()
            print(
                f"owned_test_certificate pid={process.pid} wait={process.returncode} "
                f"stdout={out!r} stderr={err!r}"
            )
            self.assertEqual(process.returncode, 0, err)

            server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            server_context.load_cert_chain(certfile=cert, keyfile=key)
            sni_names: list[str | None] = []
            server_context.set_servername_callback(
                lambda _ssl, name, _context: sni_names.append(name)
            )
            observed: list[bytes] = []

            async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
                request = await reader.readuntil(b"\r\n\r\n")
                observed.append(request)
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\n"
                    b"Content-Length: 3\r\nConnection: close\r\n\r\npng"
                )
                await writer.drain()
                writer.close()
                await writer.wait_closed()

            server = await asyncio.start_server(
                handler, "127.0.0.1", 0, ssl=server_context
            )
            client_context = ssl.create_default_context(cafile=str(cert))
            try:
                port = server.sockets[0].getsockname()[1]
                good_url = f"https://origin.test:{port}/signed?x=a%2Fb"
                status, _headers, body, _resolver, _calls = await self._request(
                    good_url, ssl_context=client_context
                )
                self.assertEqual((status, body), (200, b"png"))
                self.assertIn("origin.test", sni_names)
                self.assertEqual(
                    observed[0].split(b"\r\n", 2)[0],
                    b"GET /signed?x=a%2Fb HTTP/1.1",
                )

                wrong_url = f"https://wrong.test:{port}/signed?x=1"
                with self.assertRaises(aiohttp.ClientConnectorCertificateError):
                    await self._request(wrong_url, ssl_context=client_context)
                self.assertIn("wrong.test", sni_names)
                self.assertEqual(len(observed), 1)
            finally:
                server.close()
                await server.wait_closed()

    async def test_encoding_loss_and_redirect_or_authority_changes_fail_before_request(self) -> None:
        class BadUrl:
            raw_path_qs = "/path?sig=rewritten"

        library = AiohttpMatureHttpLibrary(
            url_factory=lambda _raw_url, *, encoded: BadUrl(),
            connector_factory=lambda **_kwargs: object(),
            session_factory=lambda **_kwargs: object(),
            timeout_factory=lambda **_kwargs: object(),
            cookie_jar_factory=object,
        )
        with self.assertRaisesRegex(ValueError, "changed the encoded request target"):
            library.encoded_url(
                "https://example.test/path?sig=original%2fvalue",
                preserve_encoded=True,
            )


if __name__ == "__main__":
    unittest.main()
