from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Awaitable, Protocol
from urllib.parse import urlsplit

_MAX_DNS_ANSWERS = 64
_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z", re.IGNORECASE)
_NUMERIC_HOST_LABEL = re.compile(r"(?:[0-9]+|0x[0-9a-f]+)\Z", re.IGNORECASE)


class UnsafeRemoteURL(ValueError):
    """A remote target does not satisfy the public-media URL policy."""


class AsyncGetAddrInfo(Protocol):
    def __call__(
        self,
        host: str,
        port: int,
        *,
        family: int,
        type: int,
    ) -> Awaitable[Sequence[object]]: ...


@dataclass(frozen=True, slots=True)
class ParsedPublicURL:
    """An accepted URL with its original bytes and parsed connection authority."""

    url: str
    scheme: str
    hostname: str
    port: int
    literal_address: str | None


@dataclass(frozen=True, slots=True)
class CheckedPublicEndpoint:
    """One checked numeric endpoint, retaining the URL hostname for TLS SNI."""

    hostname: str
    host: str
    port: int
    family: int
    proto: int
    flags: int
    sockaddr: tuple[str, int] | tuple[str, int, int, int]


@dataclass(frozen=True, slots=True)
class ResolvedPublicURL:
    """An original URL paired with the complete checked answer for this lookup."""

    target: ParsedPublicURL
    endpoints: tuple[CheckedPublicEndpoint, ...]


class PublicFetchPolicy:
    """Validate public HTTP URLs and resolve them through an injected async capability.

    This unit returns checked numeric endpoints but does not perform HTTP or prove
    that a later transport pins its connection to one of those endpoints.
    """

    def __init__(self, getaddrinfo: AsyncGetAddrInfo) -> None:
        if not callable(getaddrinfo):
            raise TypeError("getaddrinfo capability must be callable")
        self._getaddrinfo = getaddrinfo

    def validate_url(self, url: str) -> ParsedPublicURL:
        return parse_public_http_url(url)

    async def resolve(
        self,
        url: str,
        *,
        family: int = socket.AF_UNSPEC,
    ) -> ResolvedPublicURL:
        if (
            not isinstance(family, int)
            or isinstance(family, bool)
            or family not in {socket.AF_UNSPEC, socket.AF_INET, socket.AF_INET6}
        ):
            raise UnsafeRemoteURL("目标地址族无效")

        target = parse_public_http_url(url)
        if target.literal_address is not None:
            address = ipaddress.ip_address(target.literal_address)
            address_family = socket.AF_INET if address.version == 4 else socket.AF_INET6
            if family not in {socket.AF_UNSPEC, address_family}:
                raise UnsafeRemoteURL("目标地址族与 URL 主机不匹配")
            endpoint = _endpoint_for_address(target.hostname, target.port, address)
            return ResolvedPublicURL(target=target, endpoints=(endpoint,))

        try:
            answers = await self._getaddrinfo(
                target.hostname,
                target.port,
                family=family,
                type=socket.SOCK_STREAM,
            )
        except Exception:
            raise UnsafeRemoteURL("目标域名解析失败") from None

        if not isinstance(answers, Sequence) or isinstance(answers, (str, bytes)):
            raise UnsafeRemoteURL("目标域名解析结果无效")
        if not answers:
            raise UnsafeRemoteURL("目标域名没有可用地址")
        if len(answers) > _MAX_DNS_ANSWERS:
            raise UnsafeRemoteURL("目标域名解析结果过多")

        endpoints: list[CheckedPublicEndpoint] = []
        seen: set[tuple[int, str]] = set()
        for answer in answers:
            endpoint = _endpoint_from_answer(target, answer, requested_family=family)
            key = (endpoint.family, endpoint.host)
            if key in seen:
                continue
            seen.add(key)
            endpoints.append(endpoint)

        if not endpoints:
            raise UnsafeRemoteURL("目标域名没有可用地址")
        return ResolvedPublicURL(target=target, endpoints=tuple(endpoints))


def parse_public_http_url(url: str) -> ParsedPublicURL:
    """Validate URL structure and literal addresses without normalizing the URL."""
    if not isinstance(url, str) or not url:
        raise UnsafeRemoteURL("URL 格式不正确")
    if "#" in url or "\\" in url or any(
        character.isspace() or ord(character) < 0x20 or ord(character) == 0x7F
        for character in url
    ):
        raise UnsafeRemoteURL("URL 格式不正确")

    try:
        parsed = urlsplit(url)
        explicit_port = parsed.port
    except (UnicodeError, ValueError):
        raise UnsafeRemoteURL("URL 格式不正确") from None

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise UnsafeRemoteURL("仅支持带主机名的 HTTP(S) URL")
    if "@" in parsed.netloc or parsed.username is not None or parsed.password is not None:
        raise UnsafeRemoteURL("URL 不允许包含账号信息")
    if explicit_port not in {None, 80, 443}:
        raise UnsafeRemoteURL("仅允许访问标准 HTTP/HTTPS 端口")

    hostname = _hostname_from_authority(parsed.netloc)
    if not hostname or parsed.hostname is None or "%" in hostname:
        raise UnsafeRemoteURL("URL 主机名无效")

    port = explicit_port or (80 if parsed.scheme == "http" else 443)
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        if _looks_like_numeric_address(hostname):
            raise UnsafeRemoteURL("URL 主机名无效") from None
        _validate_dns_hostname(hostname)
        literal_address = None
    else:
        if not address.is_global:
            raise UnsafeRemoteURL("禁止访问内网、回环或保留地址")
        literal_address = str(address)

    return ParsedPublicURL(
        url=url,
        scheme=parsed.scheme,
        hostname=hostname,
        port=port,
        literal_address=literal_address,
    )


def _hostname_from_authority(authority: str) -> str:
    if authority.startswith("["):
        closing_bracket = authority.find("]")
        if closing_bracket < 0:
            raise UnsafeRemoteURL("URL 主机名无效")
        remainder = authority[closing_bracket + 1 :]
        if remainder and (not remainder.startswith(":") or not remainder[1:]):
            raise UnsafeRemoteURL("URL 端口无效")
        return authority[1:closing_bracket]

    if ":" not in authority:
        return authority
    if authority.count(":") != 1:
        raise UnsafeRemoteURL("IPv6 主机名必须使用方括号")
    hostname, port_text = authority.rsplit(":", 1)
    if not port_text:
        raise UnsafeRemoteURL("URL 端口无效")
    return hostname


def _validate_dns_hostname(hostname: str) -> None:
    if not hostname or hostname.startswith("."):
        raise UnsafeRemoteURL("URL 主机名无效")
    candidate = hostname[:-1] if hostname.endswith(".") else hostname
    if not candidate or candidate.endswith("."):
        raise UnsafeRemoteURL("URL 主机名无效")
    try:
        ascii_hostname = candidate.encode("idna").decode("ascii")
    except UnicodeError:
        raise UnsafeRemoteURL("URL 主机名无效") from None
    if len(ascii_hostname) > 253:
        raise UnsafeRemoteURL("URL 主机名无效")

    labels = ascii_hostname.split(".")
    if any(not _HOST_LABEL.fullmatch(label) for label in labels):
        raise UnsafeRemoteURL("URL 主机名无效")
    if _NUMERIC_HOST_LABEL.fullmatch(labels[-1]):
        raise UnsafeRemoteURL("URL 主机名无效")


def _looks_like_numeric_address(hostname: str) -> bool:
    candidate = hostname[:-1] if hostname.endswith(".") else hostname
    labels = candidate.split(".")
    return bool(labels) and all(_NUMERIC_HOST_LABEL.fullmatch(label) for label in labels)


def _endpoint_for_address(
    hostname: str,
    port: int,
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    *,
    proto: int = socket.IPPROTO_TCP,
) -> CheckedPublicEndpoint:
    if not address.is_global:
        raise UnsafeRemoteURL("禁止访问内网、回环或保留地址")
    if isinstance(address, ipaddress.IPv4Address):
        family = socket.AF_INET
        sockaddr: tuple[str, int] | tuple[str, int, int, int] = (str(address), port)
    else:
        family = socket.AF_INET6
        sockaddr = (str(address), port, 0, 0)
    return CheckedPublicEndpoint(
        hostname=hostname,
        host=str(address),
        port=port,
        family=family,
        proto=proto,
        flags=socket.AI_NUMERICHOST,
        sockaddr=sockaddr,
    )


def _endpoint_from_answer(
    target: ParsedPublicURL,
    answer: object,
    *,
    requested_family: int,
) -> CheckedPublicEndpoint:
    if not isinstance(answer, (tuple, list)) or len(answer) != 5:
        raise UnsafeRemoteURL("目标域名解析结果无效")
    family, socktype, proto, canonical_name, sockaddr = answer
    if (
        not isinstance(family, int)
        or isinstance(family, bool)
        or family not in {socket.AF_INET, socket.AF_INET6}
        or (requested_family != socket.AF_UNSPEC and family != requested_family)
        or not isinstance(socktype, int)
        or isinstance(socktype, bool)
        or socktype != socket.SOCK_STREAM
        or isinstance(proto, bool)
        or not isinstance(proto, int)
        or not isinstance(canonical_name, str)
    ):
        raise UnsafeRemoteURL("目标域名解析结果无效")
    if not isinstance(sockaddr, tuple):
        raise UnsafeRemoteURL("目标域名解析结果无效")

    expected_length = 2 if family == socket.AF_INET else 4
    if len(sockaddr) != expected_length:
        raise UnsafeRemoteURL("目标域名解析结果无效")
    address_value, resolved_port = sockaddr[:2]
    if not isinstance(address_value, str) or type(resolved_port) is not int:
        raise UnsafeRemoteURL("目标域名解析结果无效")
    if resolved_port != target.port:
        raise UnsafeRemoteURL("目标域名解析结果无效")
    if family == socket.AF_INET6 and (
        type(sockaddr[2]) is not int
        or type(sockaddr[3]) is not int
        or sockaddr[2] != 0
        or sockaddr[3] != 0
    ):
        raise UnsafeRemoteURL("目标域名解析结果无效")
    if "%" in address_value:
        raise UnsafeRemoteURL("目标域名解析结果无效")

    try:
        address = ipaddress.ip_address(address_value)
    except ValueError:
        raise UnsafeRemoteURL("目标域名解析结果无效") from None
    expected_version = 4 if family == socket.AF_INET else 6
    if address.version != expected_version:
        raise UnsafeRemoteURL("目标域名解析结果无效")
    return _endpoint_for_address(
        target.hostname,
        target.port,
        address,
        proto=proto,
    )
