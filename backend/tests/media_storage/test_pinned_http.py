from __future__ import annotations

import asyncio
import socket
import unittest
from collections.abc import AsyncIterator, Mapping
from urllib.parse import urlsplit

from haoai_backend.generation_contracts import PreparedReference, ReferenceInput, TransferFailure
from haoai_backend.media_storage.pinned_http import (
    EncodedRequestTarget,
    PinnedPublicMediaFetcher,
)
from haoai_backend.media_storage.public_fetch_policy import PublicFetchPolicy
from haoai_backend.media_storage.transfer_types import MediaTypeRule, SafeMediaPolicy


Address = tuple[int, int, int, str, tuple[str, int]]
PUBLIC: tuple[str, int] = ("8.8.8.8", 443)
MEDIA_POLICY = SafeMediaPolicy((MediaTypeRule("image/png", ".png"),))


class FakeIterator:
    def __init__(
        self,
        chunks: tuple[bytes, ...],
        error: BaseException | None = None,
        close_error: BaseException | None = None,
    ) -> None:
        self.chunks = list(chunks)
        self.error = error
        self.close_error = close_error
        self.closed = False

    def __aiter__(self) -> FakeIterator:
        return self

    async def __anext__(self) -> bytes:
        if self.error is not None:
            error, self.error = self.error, None
            raise error
        if not self.chunks:
            raise StopAsyncIteration
        return self.chunks.pop(0)

    async def aclose(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


class FakeResponse:
    def __init__(
        self,
        status: int = 200,
        headers: Mapping[str, str] | None = None,
        chunks: tuple[bytes, ...] = (b"png",),
        *,
        await_iterator: bool = False,
        iterator_error: BaseException | None = None,
        iterator_close_error: BaseException | None = None,
        response_close_error: BaseException | None = None,
    ) -> None:
        self.status = status
        self.headers = headers or {"Content-Type": "image/png", "Content-Length": "3"}
        self.iterator = FakeIterator(chunks, iterator_error, iterator_close_error)
        self.await_iterator = await_iterator
        self.response_close_error = response_close_error
        self.close_count = 0
        self.chunk_size: int | None = None

    def iter_chunks(self, chunk_size: int) -> AsyncIterator[bytes]:
        self.chunk_size = chunk_size
        if not self.await_iterator:
            return self.iterator

        async def make_iterator() -> AsyncIterator[bytes]:
            return self.iterator

        return make_iterator()  # type: ignore[return-value]

    async def close(self) -> None:
        self.close_count += 1
        if self.response_close_error is not None:
            raise self.response_close_error


class FakeConnector:
    def __init__(self, resolver: CheckedNumericResolver) -> None:
        self.resolver = resolver
        self.close_count = 0

    async def close(self) -> None:
        self.close_count += 1


class FakeSession:
    def __init__(self, connector: FakeConnector, response: FakeResponse) -> None:
        self.connector = connector
        self.response = response
        self.close_count = 0
        self.request: tuple[EncodedRequestTarget, dict[str, str], float, bool] | None = None
        self.dialed = ()
        self.get_count = 0

    async def get(
        self,
        target: EncodedRequestTarget,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
        allow_redirects: bool,
    ) -> FakeResponse:
        self.get_count += 1
        self.request = (target, dict(headers), timeout_seconds, allow_redirects)
        parsed = urlsplit(target.raw_url)
        self.dialed = await self.connector.resolver.resolve(
            target.tls_hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
        )
        return self.response

    async def close(self) -> None:
        self.close_count += 1


class FakeHttpLibrary:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.connector_value: FakeConnector | None = None
        self.session_value: FakeSession | None = None
        self.options: tuple[str | bool, ...] | None = None

    def encoded_url(self, raw_url: str, *, preserve_encoded: bool) -> EncodedRequestTarget:
        parsed = urlsplit(raw_url)
        return EncodedRequestTarget(raw_url, parsed.netloc, parsed.hostname or "", preserve_encoded)

    def connector(
        self,
        resolver: CheckedNumericResolver,
        *,
        tls_hostname: str,
        verify_tls: bool,
        use_dns_cache: bool,
    ) -> FakeConnector:
        self.options = (tls_hostname, verify_tls, use_dns_cache)
        self.connector_value = FakeConnector(resolver)
        return self.connector_value

    def session(
        self,
        connector: FakeConnector,
        *,
        trust_env: bool,
        cookies_enabled: bool,
        auto_decompress: bool,
        connector_owned_by_session: bool,
    ) -> FakeSession:
        self.options = (
            *self.options,
            trust_env,
            cookies_enabled,
            auto_decompress,
            connector_owned_by_session,
        )
        self.session_value = FakeSession(connector, self.response)
        return self.session_value


def _policy_with(answers: tuple[Address, ...] = ()) -> PublicFetchPolicy:
    if not answers:
        answers = ((socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", PUBLIC),)

    async def getaddrinfo(host: str, port: int, *, family: int, type: int) -> tuple[Address, ...]:
        return tuple(
            (item[0], item[1], item[2], item[3], (item[4][0], port))
            for item in answers
        )

    return PublicFetchPolicy(getaddrinfo)


class PinnedHttpTests(unittest.IsolatedAsyncioTestCase):
    async def test_keeps_encoded_query_host_sni_and_numeric_dial_separate(self) -> None:
        response = FakeResponse(await_iterator=True)
        library = FakeHttpLibrary(response)
        fetcher = PinnedPublicMediaFetcher(_policy_with(), MEDIA_POLICY, http_library=library)
        url = "https://Example.test/a%2fb?sig=a%2Fb&sig=two"

        result = await fetcher.fetch_public(
            ReferenceInput(kind="public_http", locator=url),
            max_bytes=20,
            timeout_seconds=12,
        )

        self.assertIsInstance(result, PreparedReference)
        self.assertEqual(result.content, b"png")
        self.assertEqual(result.media_type, "image/png")
        self.assertEqual(library.session_value.request[0].raw_url, url)
        self.assertEqual(library.session_value.request[0].authority, "Example.test")
        self.assertEqual(library.session_value.request[1], {"Host": "Example.test"})
        self.assertEqual(library.session_value.request[2:], (12, False))
        self.assertEqual(library.session_value.dialed[0].host, "8.8.8.8")
        self.assertEqual(library.session_value.dialed[0].flags, socket.AI_NUMERICHOST)
        self.assertEqual(response.chunk_size, 64 * 1024)
        self.assertEqual(library.options, ("Example.test", True, False, False, False, True, False))
        self.assertTrue(response.iterator.closed)
        self.assertEqual(response.close_count, 1)
        self.assertEqual(library.session_value.close_count, 1)
        self.assertEqual(library.connector_value.close_count, 1)

    async def test_rejects_mixed_dns_answers_before_connector_and_redirect_body(self) -> None:
        private = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.0.0.8", 443))
        response = FakeResponse(status=302, headers={"Location": "http://127.0.0.1/admin"})
        library = FakeHttpLibrary(response)
        fetcher = PinnedPublicMediaFetcher(
            _policy_with(
                ((socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", PUBLIC), private)
            ),
            MEDIA_POLICY,
            http_library=library,
        )

        result = await fetcher.fetch_public(
            ReferenceInput(kind="public_http", locator="https://example.test/image.png"),
            max_bytes=20,
            timeout_seconds=12,
        )

        self.assertIsInstance(result, TransferFailure)
        self.assertEqual(result.code, "unsafe_url")
        self.assertIsNone(library.connector_value)
        self.assertEqual(response.close_count, 0)

    async def test_idna_ipv6_and_explicit_standard_ports_keep_connection_identity(self) -> None:
        urls = (
            ("https://bücher.example:443/a%2Fb", "bücher.example:443", socket.AF_INET),
            ("http://example.test:80/a", "example.test:80", socket.AF_INET),
            ("https://[2606:4700:4700::1111]:443/a", "[2606:4700:4700::1111]:443", socket.AF_INET6),
        )
        for url, expected_host, expected_family in urls:
            with self.subTest(url=url):
                response = FakeResponse()
                library = FakeHttpLibrary(response)
                fetcher = PinnedPublicMediaFetcher(
                    _policy_with(), MEDIA_POLICY, http_library=library
                )
                result = await fetcher.fetch_public(
                    ReferenceInput(kind="public_http", locator=url),
                    max_bytes=20,
                    timeout_seconds=10,
                )
                self.assertIsInstance(result, PreparedReference)
                self.assertEqual(library.session_value.request[1], {"Host": expected_host})
                self.assertEqual(library.session_value.dialed[0].family, expected_family)

    async def test_missing_http_capability_is_typed_and_does_not_resolve_dns(self) -> None:
        calls = 0

        async def getaddrinfo(host: str, port: int, *, family: int, type: int):
            nonlocal calls
            calls += 1
            return ((socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", PUBLIC),)

        fetcher = PinnedPublicMediaFetcher(
            PublicFetchPolicy(getaddrinfo), MEDIA_POLICY, http_library=None
        )
        result = await fetcher.fetch_public(
            ReferenceInput(kind="public_http", locator="https://example.test/p.png"),
            max_bytes=10,
            timeout_seconds=5,
        )

        self.assertIsInstance(result, TransferFailure)
        self.assertEqual(result.code, "transport_unavailable")
        self.assertEqual(calls, 0)

    async def test_redirect_and_partial_responses_are_terminal_without_reading_body(self) -> None:
        for status, headers in (
            (302, {"Location": "http://127.0.0.1/admin"}),
            (206, {"Content-Type": "image/png"}),
        ):
            with self.subTest(status=status):
                response = FakeResponse(status=status, headers=headers, chunks=(b"partial",))
                library = FakeHttpLibrary(response)
                fetcher = PinnedPublicMediaFetcher(
                    _policy_with(), MEDIA_POLICY, http_library=library
                )

                result = await fetcher.fetch_public(
                    ReferenceInput(kind="public_http", locator="https://example.test/image.png"),
                    max_bytes=100,
                    timeout_seconds=10,
                )

                self.assertIsInstance(result, TransferFailure)
                self.assertEqual(result.code, "http_status")
                self.assertEqual(library.session_value.get_count, 1)
                self.assertFalse(response.iterator.closed)
                self.assertEqual(response.close_count, 1)
                self.assertEqual(library.session_value.close_count, 1)

    async def test_active_mime_and_declared_oversize_fail_before_body_read(self) -> None:
        cases = (
            ({"Content-Type": "image/svg+xml"}, "unsupported_media_type"),
            ({"Content-Type": "image/png", "Content-Length": "21"}, "reference_too_large"),
        )
        for headers, expected_code in cases:
            with self.subTest(headers=headers):
                response = FakeResponse(headers=headers, chunks=(b"payload",))
                library = FakeHttpLibrary(response)
                fetcher = PinnedPublicMediaFetcher(
                    _policy_with(), MEDIA_POLICY, http_library=library
                )
                result = await fetcher.fetch_public(
                    ReferenceInput(kind="public_http", locator="https://example.test/image.png"),
                    max_bytes=20,
                    timeout_seconds=10,
                )
                self.assertIsInstance(result, TransferFailure)
                self.assertEqual(result.code, expected_code)
                self.assertEqual(response.iterator.chunks, [b"payload"])
                self.assertEqual(response.close_count, 1)

    async def test_explicit_safe_fallback_handles_only_missing_and_generic_mime(self) -> None:
        fallback = MediaTypeRule("image/png", ".png")
        policy = SafeMediaPolicy(
            (fallback,),
            missing_fallback=fallback,
            generic_fallback=fallback,
        )
        for headers in (
            {},
            {"Content-Type": "application/octet-stream"},
        ):
            with self.subTest(headers=headers):
                response = FakeResponse(headers=headers or {"X-Test": "no content type"})
                library = FakeHttpLibrary(response)
                fetcher = PinnedPublicMediaFetcher(_policy_with(), policy, http_library=library)
                result = await fetcher.fetch_public(
                    ReferenceInput(kind="public_http", locator="https://example.test/image"),
                    max_bytes=20,
                    timeout_seconds=10,
                )
                self.assertIsInstance(result, PreparedReference)
                self.assertEqual(result.media_type, "image/png")

        fail_closed = SafeMediaPolicy((fallback,))
        for content_type in (
            None,
            "application/octet-stream",
            "application/x-unknown",
            "image/svg+xml",
        ):
            with self.subTest(content_type=content_type, configured=True):
                response = FakeResponse(
                    headers={"X-Test": "no content type"}
                    if content_type is None
                    else {"Content-Type": content_type}
                )
                fetcher = PinnedPublicMediaFetcher(
                    _policy_with(), fail_closed, http_library=FakeHttpLibrary(response)
                )
                result = await fetcher.fetch_public(
                    ReferenceInput(kind="public_http", locator="https://example.test/image"),
                    max_bytes=20,
                    timeout_seconds=10,
                )
                self.assertIsInstance(result, TransferFailure)
                self.assertEqual(result.code, "unsupported_media_type")

        active_overrides_fallback = SafeMediaPolicy(
            (fallback,), missing_fallback=fallback, generic_fallback=fallback
        )
        for content_type in (
            "image/svg+xml", "text/html; charset=utf-8", "application/x-unknown"
        ):
            with self.subTest(content_type=content_type, configured=False):
                response = FakeResponse(headers={"Content-Type": content_type})
                fetcher = PinnedPublicMediaFetcher(
                    _policy_with(), active_overrides_fallback,
                    http_library=FakeHttpLibrary(response),
                )
                result = await fetcher.fetch_public(
                    ReferenceInput(kind="public_http", locator="https://example.test/image"),
                    max_bytes=20,
                    timeout_seconds=10,
                )
                self.assertIsInstance(result, TransferFailure)
                self.assertEqual(result.code, "unsupported_media_type")

    async def test_preserves_body_cancellation_identity_and_attempts_every_close(self) -> None:
        cancellation = asyncio.CancelledError("body A")
        close_cancellation = asyncio.CancelledError("close B")
        response = FakeResponse(
            iterator_error=cancellation,
            iterator_close_error=close_cancellation,
            response_close_error=close_cancellation,
        )
        library = FakeHttpLibrary(response)
        fetcher = PinnedPublicMediaFetcher(_policy_with(), MEDIA_POLICY, http_library=library)

        with self.assertRaises(asyncio.CancelledError) as caught:
            await fetcher.fetch_public(
                ReferenceInput(kind="public_http", locator="https://example.test/image.png"),
                max_bytes=20,
                timeout_seconds=10,
            )

        self.assertIs(caught.exception, cancellation)
        self.assertTrue(response.iterator.closed)
        self.assertEqual(response.close_count, 1)
        self.assertEqual(library.session_value.close_count, 1)
        self.assertEqual(library.connector_value.close_count, 1)


if __name__ == "__main__":
    unittest.main()
