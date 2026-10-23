from types import SimpleNamespace

import pytest

from haoai_backend.generation_core.frame_optimize_application_facts import (
    apply_optimized_text,
    parse_partial_results,
    reconstruct_expected_chapter,
)


def test_partial_results_preserve_actual_application_order():
    items_by_index = {0: object(), 2: object()}

    assert parse_partial_results(None, items_by_index) == ()
    assert parse_partial_results(
        '[{"index":2,"optimized_text":"second"},{"index":0,"optimized_text":"first"}]',
        items_by_index,
    ) == ((2, "second"), (0, "first"))


@pytest.mark.parametrize(
    "raw",
    (
        "[]",
        '[{"index":2,"optimized_text":"ok"},{"index":2,"optimized_text":"again"}]',
        '[{"index":9,"optimized_text":"outside graph"}]',
        '[{"index":0,"optimized_text":""}]',
        '[{"index":0,"optimized_text":"bad\\u0000text"}]',
        '[{"index":0,"optimized_text":"ok","extra":true}]',
        '{"index":0,"optimized_text":"wrong top-level"}',
    ),
)
def test_partial_results_reject_noncanonical_or_unbound_entries(raw):
    assert parse_partial_results(raw, {0: object(), 2: object()}) is None


def test_reconstruct_expected_chapter_applies_only_authenticated_indexes():
    baseline = (
        {"index": 0, "text": "before zero", "original_text": "kept zero", "other": {"n": 1}},
        {"index": 1, "text": "before one", "other": ["preserve"]},
        {"index": 2, "text": "before two", "original_text": "kept two"},
    )
    batch = SimpleNamespace(
        items=(
            SimpleNamespace(frame_index=2, database_chapter=baseline, requested_text="requested two"),
            SimpleNamespace(frame_index=0, database_chapter=baseline, requested_text="requested zero"),
        )
    )

    reconstructed = reconstruct_expected_chapter(batch, {2: "after two", 0: "after zero"})

    assert reconstructed == [
        {"index": 0, "text": "after zero", "original_text": "kept zero", "other": {"n": 1}},
        {"index": 1, "text": "before one", "other": ["preserve"]},
        {"index": 2, "text": "after two", "original_text": "kept two"},
    ]
    assert reconstruct_expected_chapter(batch, {1: "outside selected graph"}) is None


def test_apply_optimized_text_keeps_legacy_original_text_truthiness():
    assert apply_optimized_text({"text": "old", "original_text": " "}, "request", "new") == {
        "text": "new",
        "original_text": " ",
    }
    assert apply_optimized_text({"text": "old", "original_text": ""}, "request", "new") == {
        "text": "new",
        "original_text": "request",
    }
