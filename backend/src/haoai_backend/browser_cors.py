"""Explicit, outermost CORS composition for browser clients."""

from __future__ import annotations

from collections.abc import Sequence
from ipaddress import IPv4Address, IPv6Address
from urllib.parse import urlsplit

from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp


_ALLOWED_METHODS = ("GET", "POST", "PUT", "DELETE", "OPTIONS")
_ALLOWED_HEADERS = (
    "Authorization",
    "Content-Type",
    "Idempotency-Key",
    "Cache-Control",
    "Pragma",
)


def _normalize_origin(origin: str) -> str:
    if (
        not isinstance(origin, str)
        or not origin
        or origin != origin.strip()
        or any(
            ord(character) <= 0x20
            or ord(character) == 0x7F
            or character.isspace()
            for character in origin
        )
        or "?" in origin
        or "#" in origin
    ):
        raise ValueError("allowed origins must be valid serialized HTTP(S) origins")

    try:
        parsed = urlsplit(origin)
        scheme = parsed.scheme.lower()
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise ValueError("allowed origins must be valid serialized HTTP(S) origins") from error

    if (
        scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.netloc.endswith(":")
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
        or hostname is None
        or "%" in hostname
    ):
        raise ValueError("allowed origins must be valid serialized HTTP(S) origins")

    if not hostname.isascii():
        raise ValueError("allowed origins must use an ASCII hostname in browser serialization")

    if ":" in hostname:
        try:
            normalized_host = f"[{IPv6Address(hostname).compressed}]"
        except ValueError as error:
            raise ValueError("allowed origins must contain a valid host") from error
    else:
        try:
            normalized_host = str(IPv4Address(hostname))
        except ValueError:
            if hostname and all(character in "0123456789." for character in hostname):
                raise ValueError("allowed origins must contain a valid host")
            normalized_host = hostname.lower()

            labels = (
                normalized_host[:-1].split(".")
                if normalized_host.endswith(".")
                else normalized_host.split(".")
            )
            if (
                not normalized_host
                or len(normalized_host) > 253
                or any(
                    not label
                    or len(label) > 63
                    or label.startswith("-")
                    or label.endswith("-")
                    or any(
                        not character.isascii()
                        or not (character.isalnum() or character == "-")
                        for character in label
                    )
                    for label in labels
                )
            ):
                raise ValueError("allowed origins must contain a valid host")

    default_port = 80 if scheme == "http" else 443
    authority = normalized_host
    if port is not None and port != default_port:
        authority = f"{authority}:{port}"

    return f"{scheme}://{authority}"


def configure_browser_cors(
    app: ASGIApp,
    *,
    allowed_origins: Sequence[str] = (),
) -> ASGIApp:
    """Wrap an ASGI app with narrowly scoped browser CORS.

    The empty default keeps the original app and its behavior unchanged. Callers
    must pass explicit serialized HTTP(S) origins when exposing the API to a
    browser hosted on another origin.
    """
    if isinstance(allowed_origins, str):
        raise ValueError("allowed_origins must be a sequence of origins")

    origins = tuple(dict.fromkeys(_normalize_origin(origin) for origin in allowed_origins))
    if not origins:
        return app

    return CORSMiddleware(
        app=app,
        allow_origins=list(origins),
        allow_credentials=False,
        allow_methods=list(_ALLOWED_METHODS),
        allow_headers=list(_ALLOWED_HEADERS),
        max_age=600,
    )

