"""Strict readers for signed HaoAI Group control observations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from haoai_backend.generation_credentials.domain import HmacProofAuthority

_OBSERVATION_PURPOSE = "haoai-group-poll-observation-v1"
_EVIDENCE_KIND = "haoai_poll_observation_v1"
_IMAGE_DOWNLOAD_MARKERS = frozenset(
    {
        "image_download",
        "failed to download the provided image",
        "downloading the image",
    }
)
_AUTH_UNAVAILABLE_MARKERS = frozenset(
    {
        "auth_unavailable",
        "no auth available",
        "no available auth candidates",
        "all candidates are unavailable",
    }
)


def verified_group_observation_fact(
    row: Mapping[str, object], authority: HmacProofAuthority | None
) -> Mapping[str, object] | None:
    if (
        type(authority) is not HmacProofAuthority
        or row.get("evidence_kind") != _EVIDENCE_KIND
        or row.get("validity") != "unknown"
        or not isinstance(row.get("result_location"), str)
    ):
        return None
    try:
        envelope = json.loads(str(row["result_location"]))
        fact = envelope["fact"]
        proof = envelope["proof"]
        canonical = _canonical_json(fact)
        if (
            not isinstance(fact, dict)
            or not isinstance(proof, dict)
            or hashlib.sha256(canonical).hexdigest() != row.get("payload_digest")
            or proof.get("key_id") != authority.key_id
            or not authority.verify(
                _OBSERVATION_PURPOSE,
                canonical,
                proof.get("signature"),
            )
        ):
            return None
        return fact
    except (KeyError, TypeError, ValueError, UnicodeError):
        return None


def is_signed_auth_unavailable_observation(
    row: Mapping[str, object], authority: HmacProofAuthority | None
) -> bool:
    fact = verified_group_observation_fact(row, authority)
    if fact is None:
        return False
    markers = fact.get("auth_unavailable_markers")
    return bool(
        fact.get("source_fact") == "http_error"
        and isinstance(markers, list)
        and markers
        and all(marker in _AUTH_UNAVAILABLE_MARKERS for marker in markers)
    )


def is_retryable_reference_failure_observation(
    row: Mapping[str, object],
    authority: HmacProofAuthority | None,
    *,
    owner_id: str,
    task_id: str,
    unit_id: str,
    group_key: str,
    step_id: str,
    submission_id: str,
    attempt_id: str,
    external_id: str,
    request_digest: str,
    reservation_id: str,
    sequence: int,
) -> bool:
    fact = verified_group_observation_fact(row, authority)
    if fact is None:
        return False
    markers = fact.get("image_download_markers")
    return bool(
        row.get("step_id") == step_id
        and row.get("submission_id") == submission_id
        and row.get("evidence_key") == f"haoai-poll-v1:{reservation_id}"
        and fact.get("schema") == _EVIDENCE_KIND
        and fact.get("owner_id") == owner_id
        and fact.get("task_id") == task_id
        and fact.get("unit_id") == unit_id
        and fact.get("group_key") == group_key
        and fact.get("step_id") == step_id
        and fact.get("submission_id") == submission_id
        and fact.get("attempt_id") == attempt_id
        and fact.get("external_id") == external_id
        and fact.get("request_digest") == request_digest
        and fact.get("reservation_id") == reservation_id
        and fact.get("sequence") == sequence
        and fact.get("source_fact") == "source_failed"
        and fact.get("source_status") == "failed"
        and isinstance(markers, list)
        and bool(markers)
        and all(marker in _IMAGE_DOWNLOAD_MARKERS for marker in markers)
        and row.get("result_digest") == fact.get("body_sha256")
    )


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


__all__ = (
    "is_retryable_reference_failure_observation",
    "is_signed_auth_unavailable_observation",
    "verified_group_observation_fact",
)
