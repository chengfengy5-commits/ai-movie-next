"""Pure normalization for explicitly managed media URL origins."""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import SplitResult, unquote, urlsplit


_Origin = tuple[str, str, int]
_OriginValue = str | _Origin


def _origin_from_parts(parsed: SplitResult) -> _Origin | None:
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    hostname = parsed.hostname
    if not hostname:
        return None
    effective_port = port if port is not None else (443 if parsed.scheme == "https" else 80)
    return parsed.scheme, hostname.lower().rstrip("."), effective_port


def _has_valid_percent_escapes(value: str) -> bool:
    index = 0
    while True:
        index = value.find("%", index)
        if index < 0:
            return True
        if index + 2 >= len(value) or any(
            char not in "0123456789abcdefABCDEF"
            for char in value[index + 1 : index + 3]
        ):
            return False
        index += 3


def _parse_origin(value: str) -> tuple[_Origin, SplitResult] | None:
    if not value or any(ord(char) <= 32 or ord(char) == 127 for char in value):
        return None
    if "\\" in value or "?" in value or "#" in value:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.path not in {"", "/"} or "%" in parsed.netloc:
        return None
    origin = _origin_from_parts(parsed)
    if origin is None:
        return None
    return origin, parsed


def _configured_origin(value: object) -> tuple[_Origin, str] | None:
    if isinstance(value, str):
        parsed_origin = _parse_origin(value)
        if parsed_origin is None:
            return None
        origin, parsed = parsed_origin
        return origin, f"{parsed.scheme}://{parsed.netloc}"

    if not isinstance(value, tuple) or len(value) != 3:
        return None
    scheme, hostname, port = value
    if (
        not isinstance(scheme, str)
        or not isinstance(hostname, str)
        or type(port) is not int
        or not 0 <= port <= 65535
    ):
        return None
    scheme = scheme.lower()
    hostname = hostname.lower().rstrip(".")
    if (
        scheme not in {"http", "https"}
        or not hostname
        or any(ord(char) <= 32 or ord(char) == 127 for char in hostname)
        or any(char in hostname for char in "/\\@?#%[]")
    ):
        return None

    formatted_host = f"[{hostname}]" if ":" in hostname else hostname
    candidate = f"{scheme}://{formatted_host}:{port}"
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return None
    origin = _origin_from_parts(parsed)
    if origin != (scheme, hostname, port):
        return None
    default_port = 443 if scheme == "https" else 80
    suffix = "" if port == default_port else f":{port}"
    return origin, f"{scheme}://{formatted_host}{suffix}"


def _valid_managed_path(path: str) -> bool:
    if not path.startswith("/") or "?" in path or "#" in path:
        return False
    decoded_path = unquote(path)
    parts = decoded_path.lstrip("/").split("/")
    return bool(parts) and all(part not in {"", ".", ".."} for part in parts)


def _parse_managed_url(value: object) -> tuple[_Origin, SplitResult] | None:
    if not isinstance(value, str) or not value:
        return None
    if any(ord(char) <= 32 or ord(char) == 127 for char in value):
        return None
    if "\\" in value or "#" in value or not _has_valid_percent_escapes(value):
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if "%" in parsed.netloc:
        return None
    origin = _origin_from_parts(parsed)
    if origin is None or not _valid_managed_path(parsed.path):
        return None
    return origin, parsed


def normalize_managed_url(
    url: str,
    explicit_origin_map: Mapping[_OriginValue, _OriginValue],
) -> str | None:
    """Keep current managed URLs or map an explicitly allowlisted legacy origin.

    The function is intentionally pure. It performs no network or DNS lookup,
    and returns ``None`` for URLs outside the explicit managed-origin set.
    ``explicit_origin_map`` accepts origin strings or parsed
    ``(scheme, host, effective_port)`` tuples.
    """

    parsed_url = _parse_managed_url(url)
    if parsed_url is None or not isinstance(explicit_origin_map, Mapping):
        return None
    source_origin, parsed = parsed_url

    mappings: list[tuple[_Origin, _Origin, str]] = []
    try:
        for legacy_value, current_value in explicit_origin_map.items():
            legacy = _configured_origin(legacy_value)
            current = _configured_origin(current_value)
            if legacy is not None and current is not None:
                mappings.append((legacy[0], current[0], current[1]))
    except (AttributeError, TypeError, ValueError):
        return None

    current_origins = {current_origin for _, current_origin, _ in mappings}
    if source_origin in current_origins:
        return url

    for legacy_origin, _, current_prefix in mappings:
        if source_origin == legacy_origin:
            scheme_marker = url.find("://")
            if scheme_marker < 0:
                return None
            authority_start = scheme_marker + 3
            suffix_start = authority_start + len(parsed.netloc)
            return f"{current_prefix}{url[suffix_start:]}"
    return None
