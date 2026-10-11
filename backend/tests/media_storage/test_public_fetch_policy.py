from __future__ import annotations

import asyncio
import socket
import unittest

from haoai_backend.media_storage.public_fetch_policy import (
    PublicFetchPolicy,
    UnsafeRemoteURL,
    parse_public_http_url,
)


def _answer(address: str, port: int = 443) -> tuple[int, int, int, str, tuple[object, ...]]:
    if ":" in address:
        return (
            socket.AF_INET6,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            "",
            (address, port, 0, 0),
        )
    return (
        socket.AF_INET,
        socket.SOCK_STREAM,
        socket.IPPROTO_TCP,
        "",
        (address, port),
    )


class PublicFetchPolicyTests(unittest.TestCase):
    def test_original_signed_url_and_hostname_survive_validation_and_resolution(self) -> None:
        url = (
            "https://Images.Example.com:443/a%2Fb?"
            "X-Amz-Signature=a%2Fb%2Bc%3D&part=1&part=2"
        )
        calls: list[tuple[str, int, int, int]] = []

        async def getaddrinfo(host: str, port: int, *, family: int, type: int):
            calls.append((host, port, family, type))
            return [_answer("8.8.8.8", port), _answer("2001:4860:4860::8888", port)]

        policy = PublicFetchPolicy(getaddrinfo)
        parsed = policy.validate_url(url)
        resolved = asyncio.run(policy.resolve(url))

        self.assertEqual(parsed.url, url)
        self.assertEqual(parsed.hostname, "Images.Example.com")
        self.assertEqual(parsed.port, 443)
        self.assertEqual(resolved.target.url, url)
        self.assertEqual(resolved.target.hostname, "Images.Example.com")
        self.assertEqual(
            calls,
            [("Images.Example.com", 443, socket.AF_UNSPEC, socket.SOCK_STREAM)],
        )
        self.assertEqual(
            [(endpoint.hostname, endpoint.host, endpoint.port) for endpoint in resolved.endpoints],
            [
                ("Images.Example.com", "8.8.8.8", 443),
                ("Images.Example.com", "2001:4860:4860::8888", 443),
            ],
        )
        self.assertEqual(resolved.endpoints[0].sockaddr, ("8.8.8.8", 443))
        self.assertEqual(resolved.endpoints[1].sockaddr, ("2001:4860:4860::8888", 443, 0, 0))
        self.assertEqual(resolved.endpoints[0].flags, socket.AI_NUMERICHOST)

    def test_implicit_and_explicit_standard_ports_are_preserved(self) -> None:
        cases = (
            ("http://images.example/image.png", 80),
            ("https://images.example:443/image.png", 443),
            ("https://images.example:80/image.png", 80),
            ("http://images.example:443/image.png", 443),
        )
        for url, expected_port in cases:
            with self.subTest(url=url):
                parsed = parse_public_http_url(url)
                self.assertEqual(parsed.url, url)
                self.assertEqual(parsed.port, expected_port)

    def test_requested_address_family_is_used_for_each_dns_lookup(self) -> None:
        calls: list[tuple[str, int, int, int]] = []

        async def getaddrinfo(host: str, port: int, *, family: int, type: int):
            calls.append((host, port, family, type))
            return [_answer("2001:4860:4860::8888", port)]

        resolved = asyncio.run(
            PublicFetchPolicy(getaddrinfo).resolve(
                "https://images.example/a",
                family=socket.AF_INET6,
            )
        )
        self.assertEqual(
            calls,
            [("images.example", 443, socket.AF_INET6, socket.SOCK_STREAM)],
        )
        self.assertEqual([endpoint.family for endpoint in resolved.endpoints], [socket.AF_INET6])

    def test_public_ip_literals_skip_dns_and_private_literals_fail(self) -> None:
        calls: list[str] = []

        async def getaddrinfo(host: str, port: int, *, family: int, type: int):
            calls.append(host)
            return [_answer("8.8.8.8", port)]

        policy = PublicFetchPolicy(getaddrinfo)
        ipv4 = asyncio.run(policy.resolve("https://8.8.8.8/signed?x=a%2Fb"))
        ipv6_url = "https://[2001:4860:4860::8888]:443/image.png"
        ipv6 = asyncio.run(policy.resolve(ipv6_url))

        self.assertEqual(ipv4.target.url, "https://8.8.8.8/signed?x=a%2Fb")
        self.assertEqual(ipv4.endpoints[0].host, "8.8.8.8")
        self.assertEqual(ipv6.target.url, ipv6_url)
        self.assertEqual(ipv6.endpoints[0].host, "2001:4860:4860::8888")
        self.assertEqual(calls, [])
        with self.assertRaises(UnsafeRemoteURL):
            asyncio.run(
                policy.resolve("https://[2001:4860:4860::8888]/image.png", family=socket.AF_INET)
            )
        for url in (
            "http://127.0.0.1/image.png",
            "http://10.0.0.1/image.png",
            "https://[::1]/image.png",
            "https://[fd00::1]/image.png",
            "https://[2001:db8::1]/image.png",
        ):
            with self.subTest(url=url), self.assertRaises(UnsafeRemoteURL):
                parse_public_http_url(url)

    def test_malformed_credentials_fragments_and_ambiguous_hosts_are_rejected(self) -> None:
        invalid_urls = (
            "ftp://images.example/image.png",
            "https:///missing-host",
            "https://user:secret@images.example/image.png",
            "https://@images.example/image.png",
            "https://images.example/image.png#part",
            "https://images.example/image.png#",
            "https://images.example:8080/image.png",
            "https://images.example:/image.png",
            "https://images.example:65536/image.png",
            "https://images.example:bad/image.png",
            "https://exa mple.example/image.png",
            "https://127.1/image.png",
            "https://0x7f000001/image.png",
            "https://127.0.0.1./image.png",
            "https://[2001:4860::1%25en0]/image.png",
            "https://images.example\\@127.0.0.1/image.png",
        )
        for url in invalid_urls:
            with self.subTest(url=url), self.assertRaises(UnsafeRemoteURL):
                parse_public_http_url(url)

    def test_mixed_public_and_private_answer_fails_whole_lookup(self) -> None:
        async def getaddrinfo(host: str, port: int, *, family: int, type: int):
            return [_answer("8.8.8.8", port), _answer("10.0.0.7", port)]

        with self.assertRaises(UnsafeRemoteURL):
            asyncio.run(PublicFetchPolicy(getaddrinfo).resolve("https://images.example/a"))

    def test_answers_are_deduplicated_by_family_and_address(self) -> None:
        async def getaddrinfo(host: str, port: int, *, family: int, type: int):
            return [
                _answer("8.8.8.8", port),
                _answer("8.8.8.8", port),
                _answer("2001:4860:4860::8888", port),
            ]

        resolved = asyncio.run(PublicFetchPolicy(getaddrinfo).resolve("https://images.example/a"))
        self.assertEqual([endpoint.host for endpoint in resolved.endpoints], [
            "8.8.8.8",
            "2001:4860:4860::8888",
        ])

    def test_empty_invalid_and_oversized_dns_answers_fail_safely(self) -> None:
        invalid_answers = (
            [],
            [_answer("not-an-ip")],
            [_answer("8.8.8.8", 80)],
            [(socket.AF_INET, socket.SOCK_DGRAM, 0, "", ("8.8.8.8", 443))],
            [(float(socket.AF_INET), socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))],
            [(socket.AF_INET, float(socket.SOCK_STREAM), 6, "", ("8.8.8.8", 443))],
            [(socket.AF_INET, socket.SOCK_STREAM, "tcp", "", ("8.8.8.8", 443))],
            [(socket.AF_INET, socket.SOCK_STREAM, 6, None, ("8.8.8.8", 443))],
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("2001:4860:4860::8888", 443))],
            [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:4860:4860::8888", 443, 0, 2))],
            [_answer("192.0.2.10")],
            [_answer("8.8.8.8")] * 65,
        )
        for answers in invalid_answers:
            async def getaddrinfo(host: str, port: int, *, family: int, type: int):
                return answers

            with self.subTest(answers=answers[:1]), self.assertRaises(UnsafeRemoteURL):
                asyncio.run(PublicFetchPolicy(getaddrinfo).resolve("https://images.example/a"))

    def test_dns_error_is_redacted_and_cancellation_propagates(self) -> None:
        secret_url = "https://images.example/a?token=never-log-this"

        async def fail_resolution(host: str, port: int, *, family: int, type: int):
            raise OSError("resolver saw never-log-this")

        with self.assertRaises(UnsafeRemoteURL) as raised:
            asyncio.run(PublicFetchPolicy(fail_resolution).resolve(secret_url))
        self.assertNotIn("never-log-this", str(raised.exception))

        async def cancel_resolution(host: str, port: int, *, family: int, type: int):
            raise asyncio.CancelledError

        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(PublicFetchPolicy(cancel_resolution).resolve(secret_url))

    def test_each_call_resolves_again_and_rejects_a_changed_private_answer(self) -> None:
        answers = [_answer("8.8.8.8"), _answer("10.1.2.3")]
        calls: list[str] = []

        async def getaddrinfo(host: str, port: int, *, family: int, type: int):
            calls.append(host)
            return [answers[len(calls) - 1]]

        policy = PublicFetchPolicy(getaddrinfo)
        first = asyncio.run(policy.resolve("https://images.example/a"))
        self.assertEqual(first.endpoints[0].host, "8.8.8.8")
        with self.assertRaises(UnsafeRemoteURL):
            asyncio.run(policy.resolve("https://images.example/a"))
        self.assertEqual(calls, ["images.example", "images.example"])

    def test_construction_is_inert_and_requires_explicit_resolver(self) -> None:
        calls: list[str] = []

        async def getaddrinfo(host: str, port: int, *, family: int, type: int):
            calls.append(host)
            return [_answer("8.8.8.8", port)]

        policy = PublicFetchPolicy(getaddrinfo)
        self.assertEqual(calls, [])
        with self.assertRaises(TypeError):
            PublicFetchPolicy(None)  # type: ignore[arg-type]
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
