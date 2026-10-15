from __future__ import annotations

import pytest

from haoai_backend.storyboard_generation.two_pass_billing import (
    parse_generated_frames,
    validate_idempotency_key,
)


def test_idempotency_key_requires_nonblank_bounded_value() -> None:
    assert validate_idempotency_key("confirmation-1") == "confirmation-1"
    for value in (None, "", "   ", "x" * 256):
        with pytest.raises(ValueError):
            validate_idempotency_key(value)


def test_step_two_parser_rejects_missing_or_incomplete_frames() -> None:
    segments = [{"index": 0, "text": "one"}, {"index": 1, "text": "two"}]
    assert parse_generated_frames("frame one_::~RECORD::~_frame two", segments) == [
        {"text": "frame one", "duration": 5},
        {"text": "frame two", "duration": 5},
    ]
    for value in ("", "only one", "frame one_::~RECORD::~_", "_::~RECORD::~_frame two"):
        with pytest.raises(ValueError):
            parse_generated_frames(value, segments)
