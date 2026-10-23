from __future__ import annotations

from dataclasses import replace

import pytest

from haoai_backend.generation_catalog.admission_types import (
    AdmissionContextError,
    AdmissionOperation,
    build_admission_context,
)
from haoai_backend.generation_contracts.types import AdmissionFingerprint, GenerationActor
from haoai_backend.shared.identity import TrustedActor


def actor(user_id: str = "user-1") -> GenerationActor:
    return GenerationActor(user_id=user_id, trusted_account_identity=TrustedActor(user_id))


def single_request(**overrides: object) -> dict[str, object]:
    request: dict[str, object] = {
        "model_id": "model-1",
        "prompt": "draw a scene",
        "target": {"kind": "standalone"},
        "size": None,
        "reference_images": [],
    }
    request.update(overrides)
    return request


def batch_request(assets: list[dict[str, object]]) -> dict[str, object]:
    return {"chapter_id": "chapter-1", "assets": assets}


def batch_asset(asset_id: str, *, references: list[str] | None = None) -> dict[str, object]:
    return {
        "type": "character",
        "id": asset_id,
        "name": asset_id,
        "description": "portrait",
        "model_id": "image-1",
        "prompt_id": "",
        "size": None,
        "reference_images": references or [],
    }


def test_raw_key_is_exact_while_request_fingerprint_remains_request_scoped() -> None:
    spaced = build_admission_context(
        actor(), AdmissionOperation.IMAGE_SINGLE, single_request(), raw_key=" key "
    )
    trimmed = build_admission_context(
        actor(), AdmissionOperation.IMAGE_SINGLE, single_request(), raw_key="key"
    )

    assert spaced.raw_key is not None and spaced.raw_key.raw == " key "
    assert trimmed.raw_key is not None and trimmed.raw_key.raw == "key"
    assert spaced.fingerprint == trimmed.fingerprint


@pytest.mark.parametrize("key", [None, "", " \t\n", "x" * 256])
def test_media_idempotency_key_rejects_blank_absent_or_256_characters(key: object) -> None:
    with pytest.raises(AdmissionContextError):
        build_admission_context(actor(), "image.single", single_request(), raw_key=key)  # type: ignore[arg-type]


def test_media_idempotency_key_accepts_255_characters_without_trimming() -> None:
    key = " " + "x" * 253 + " "
    context = build_admission_context(actor(), "image.single", single_request(), raw_key=key)
    assert context.raw_key is not None
    assert context.raw_key.raw == key


def test_non_media_operation_rejects_a_client_idempotency_key() -> None:
    with pytest.raises(AdmissionContextError, match="does not accept"):
        build_admission_context(
            actor(),
            "frame.review",
            {"chapter_id": "chapter-1", "frame_index": 0},
            raw_key="unused",
        )


def test_item_and_reference_order_are_part_of_the_complete_fingerprint() -> None:
    first = build_admission_context(
        actor(),
        "image.batch",
        batch_request(
            [
                batch_asset("asset-a", references=["https://cdn.example/a?sig=one", "https://cdn.example/b"]),
                batch_asset("asset-b"),
            ]
        ),
        raw_key="batch-key",
    )
    items_reordered = build_admission_context(
        actor(),
        "image.batch",
        batch_request(
            [batch_asset("asset-b"), batch_asset("asset-a", references=["https://cdn.example/a?sig=one", "https://cdn.example/b"])]
        ),
        raw_key="batch-key",
    )
    references_reordered = build_admission_context(
        actor(),
        "image.batch",
        batch_request(
            [
                batch_asset("asset-a", references=["https://cdn.example/b", "https://cdn.example/a?sig=one"]),
                batch_asset("asset-b"),
            ]
        ),
        raw_key="batch-key",
    )

    assert first.fingerprint is not None
    assert items_reordered.fingerprint is not None
    assert references_reordered.fingerprint is not None
    assert len({
        first.fingerprint.ordered_nonsecret_payload_digest,
        items_reordered.fingerprint.ordered_nonsecret_payload_digest,
        references_reordered.fingerprint.ordered_nonsecret_payload_digest,
    }) == 3


def test_legacy_reference_normalization_trims_only_outer_whitespace_and_keeps_query_bytes() -> None:
    context = build_admission_context(
        actor(),
        "image.single",
        single_request(reference_images=["  https://cdn.example/file?X-Amz-Signature=a%2Fb  "]),
        raw_key="key",
    )

    assert context.canonical_request["reference_images"] == (
        "https://cdn.example/file?X-Amz-Signature=a%2Fb",
    )
    assert "X-Amz-Signature=a%2Fb" in context.request["reference_images"][0]


def test_full_request_context_is_deeply_immutable_and_hidden_from_repr() -> None:
    secret_url = "https://cdn.example/file?token=secret-value"
    context = build_admission_context(
        actor(),
        "image.batch",
        batch_request([batch_asset("asset-a", references=[secret_url])]),
        raw_key="idempotency-secret",
    )

    nested = context.request["assets"][0]
    assert isinstance(nested["reference_images"], tuple)
    with pytest.raises(TypeError):
        nested["name"] = "changed"  # type: ignore[index]
    rendered = repr(context)
    assert "secret-value" not in rendered
    assert "idempotency-secret" not in rendered
    assert "cdn.example" not in rendered


def test_client_cannot_supply_trusted_actor_or_financial_facts() -> None:
    for field in ("actor_id", "trusted_actor", "is_system", "principal", "skip_credit_check"):
        request = single_request()
        request[field] = "forged"
        with pytest.raises(AdmissionContextError, match="trusted admission facts"):
            build_admission_context(actor(), "image.single", request, raw_key="key")


def test_rebuilt_equal_context_is_accepted_and_tampered_facts_are_rejected() -> None:
    context = build_admission_context(actor(), "image.single", single_request(), raw_key="key")
    rebuilt = build_admission_context(actor(), "image.single", single_request(), raw_key="key")
    assert rebuilt == context
    assert context.validate() is None

    assert context.fingerprint is not None
    bad_fingerprint = replace(context.fingerprint, actor_id="another-user")
    with pytest.raises(AdmissionContextError, match="fingerprint"):
        replace(context, fingerprint=bad_fingerprint).validate()

    changed_target = replace(
        context,
        canonical_request={
            **context.canonical_request,
            "target": {"kind": "standalone"},
            "model_id": "forged-model",
        },
    )
    with pytest.raises(AdmissionContextError, match="canonical request"):
        changed_target.validate()


def test_fingerprint_binds_actor_and_operation_even_when_request_body_matches() -> None:
    first = build_admission_context(actor("user-1"), "image.single", single_request(), raw_key="key")
    second = build_admission_context(actor("user-2"), "image.single", single_request(), raw_key="key")
    video = build_admission_context(
        actor("user-1"),
        "video.single",
        {**single_request(), "duration": 5, "resolution": None},
        raw_key="key",
    )

    assert first.fingerprint is not None and second.fingerprint is not None
    assert first.fingerprint.ordered_nonsecret_payload_digest == second.fingerprint.ordered_nonsecret_payload_digest
    assert first.fingerprint.actor_id != second.fingerprint.actor_id
    assert video.fingerprint is not None and video.fingerprint.operation == "video.single"


def test_single_and_batch_reference_count_and_per_reference_size_boundaries() -> None:
    refs_64 = [f"https://cdn.example/{index}" for index in range(64)]
    accepted = build_admission_context(
        actor(), "image.single", single_request(reference_images=refs_64), raw_key="key"
    )
    assert len(accepted.canonical_request["reference_images"]) == 64

    with pytest.raises(AdmissionContextError, match="safe count"):
        build_admission_context(
            actor(),
            "image.single",
            single_request(reference_images=[*refs_64, "https://cdn.example/64"]),
            raw_key="key",
        )

    exactly_8_mib = "x" * (8 * 1024 * 1024)
    accepted_large = build_admission_context(
        actor(), "image.single", single_request(reference_images=[exactly_8_mib]), raw_key="key"
    )
    assert len(accepted_large.canonical_request["reference_images"][0]) == 8 * 1024 * 1024
    with pytest.raises(AdmissionContextError, match="8 MiB"):
        build_admission_context(
            actor(),
            "image.single",
            single_request(reference_images=[exactly_8_mib + "x"]),
            raw_key="key",
        )


def test_reference_total_byte_boundary_is_32_mib() -> None:
    eight_mib = "x" * (8 * 1024 * 1024)
    build_admission_context(
        actor(),
        "image.batch",
        batch_request([batch_asset(f"asset-{index}", references=[eight_mib]) for index in range(4)]),
        raw_key="key",
    )
    with pytest.raises(AdmissionContextError, match="32 MiB"):
        build_admission_context(
            actor(),
            "image.batch",
            batch_request(
                [batch_asset(f"asset-{index}", references=[eight_mib]) for index in range(5)]
            ),
            raw_key="key",
        )


def test_batch_count_description_and_aggregate_text_limits() -> None:
    build_admission_context(
        actor(),
        "image.batch",
        batch_request([batch_asset(f"asset-{index}") for index in range(64)]),
        raw_key="key",
    )
    with pytest.raises(AdmissionContextError, match="count"):
        build_admission_context(
            actor(),
            "image.batch",
            batch_request([batch_asset(f"asset-{index}") for index in range(65)]),
            raw_key="key",
        )

    build_admission_context(
        actor(),
        "image.batch",
        batch_request([batch_asset("asset-a") | {"description": "x" * 16_000}]),
        raw_key="key",
    )
    with pytest.raises(AdmissionContextError, match="16000"):
        build_admission_context(
            actor(),
            "image.batch",
            batch_request([batch_asset("asset-a") | {"description": "x" * 16_001}]),
            raw_key="key",
        )

    many_descriptions = [
        batch_asset(f"asset-{index}") | {"description": "x" * 16_000}
        for index in range(16)
    ]
    with pytest.raises(AdmissionContextError, match="256000"):
        build_admission_context(actor(), "image.batch", batch_request(many_descriptions), raw_key="key")


def test_optimize_frame_and_concurrency_boundaries_and_duplicate_index() -> None:
    request = {
        "chapter_id": "chapter-1",
        "model_id": "text-model",
        "frames": [{"index": index, "text": "text"} for index in range(30)],
        "concurrency": 3,
    }
    lowest = build_admission_context(actor(), "frame.optimize", request)
    assert lowest.canonical_request["concurrency"] == 3

    highest = build_admission_context(
        actor(), "frame.optimize", {**request, "frames": [{"index": 0, "text": "text"}], "concurrency": 10}
    )
    assert highest.canonical_request["concurrency"] == 10

    with pytest.raises(AdmissionContextError, match="1 to 30"):
        build_admission_context(
            actor(), "frame.optimize", {**request, "frames": [{"index": i, "text": "x"} for i in range(31)]}
        )
    with pytest.raises(AdmissionContextError, match="duplicate index"):
        build_admission_context(
            actor(), "frame.optimize", {**request, "frames": [{"index": 0, "text": "a"}, {"index": 0, "text": "b"}]}
        )
    for concurrency in (2, 11):
        with pytest.raises(AdmissionContextError, match="range"):
            build_admission_context(
                actor(),
                "frame.optimize",
                {**request, "frames": [{"index": 0, "text": "text"}], "concurrency": concurrency},
            )


def test_optimize_accepts_complete_long_chapter_context_for_late_frame() -> None:
    chapter_texts = [f"frame-{index}" for index in range(60)]
    context = build_admission_context(
        actor(),
        "frame.optimize",
        {
            "chapter_id": "chapter-long",
            "model_id": "text-model",
            "frames": [{"index": 50, "text": "target-frame"}],
            "chapter_texts": chapter_texts,
            "concurrency": 3,
        },
    )

    assert context.canonical_request["chapter_texts"] == tuple(chapter_texts)


def test_stream_preserves_prompt_id_but_does_not_trust_server_fee_fields() -> None:
    context = build_admission_context(
        actor(),
        "text.stream",
        {"model_id": " model-1 ", "prompt_id": "prompt-1", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert context.canonical_request["model_id"] == "model-1"
    assert context.canonical_request["prompt_id"] == "prompt-1"

    with pytest.raises(AdmissionContextError, match="trusted admission facts"):
        build_admission_context(actor(), "text.stream", {"skip_credit_check": True})



@pytest.mark.parametrize(
    "assets",
    [
        [batch_asset("asset-a"), batch_asset("asset-a")],
        [
            batch_asset("asset-a"),
            batch_asset(" asset-a ") | {"type": " character "},
        ],
    ],
)
def test_batch_rejects_duplicate_canonical_targets(assets: list[dict[str, object]]) -> None:
    with pytest.raises(AdmissionContextError, match="duplicate"):
        build_admission_context(actor(), "image.batch", batch_request(assets), raw_key="key")


def test_batch_keeps_same_id_under_different_types_as_distinct_ordered_targets() -> None:
    context = build_admission_context(
        actor(),
        "image.batch",
        batch_request([batch_asset("shared-id"), batch_asset("shared-id") | {"type": "scene"}]),
        raw_key="key",
    )

    assert [asset["type"] for asset in context.canonical_request["assets"]] == [
        "character",
        "scene",
    ]
    assert [asset["id"] for asset in context.canonical_request["assets"]] == [
        "shared-id",
        "shared-id",
    ]


def test_batch_legacy_size_canonical_bytes_and_digests_preserve_empty_string() -> None:
    expected_digests = {
        None: "6378f3fead997d6df0b532988114ccb6a5110e13ee40f1921db6b8b520cc6e96",
        "": "9acb4988b907581d16268c24bb7ea4f4a13f5db5a437e7fdfce4a305f0e718eb",
        "   ": "9acb4988b907581d16268c24bb7ea4f4a13f5db5a437e7fdfce4a305f0e718eb",
        "large": "8afb145bffaf38d4ed43d9e082ba905e2164895544c42351202ebf90a586ec27",
    }
    for size, expected_digest in expected_digests.items():
        asset = batch_asset("asset-a") | {"size": size}
        context = build_admission_context(
            actor(), "image.batch", batch_request([asset]), raw_key="key"
        )
        assert context.canonical_request["assets"][0]["size"] == (
            None if size is None else size.strip()
        )
        assert context.fingerprint is not None
        assert context.fingerprint.ordered_nonsecret_payload_digest == expected_digest


def test_single_empty_size_keeps_legacy_optional_normalization() -> None:
    absent = build_admission_context(actor(), "image.single", single_request(size=None), raw_key="key")
    empty = build_admission_context(actor(), "image.single", single_request(size=""), raw_key="key")
    whitespace = build_admission_context(actor(), "image.single", single_request(size="  "), raw_key="key")

    assert absent.canonical_request["size"] is None
    assert empty.canonical_request["size"] is None
    assert whitespace.canonical_request["size"] is None
    assert absent.fingerprint == empty.fingerprint == whitespace.fingerprint


def test_batch_raw_field_limit_is_checked_before_outer_whitespace_is_trimmed() -> None:
    asset = batch_asset("asset-a") | {"name": f" {'n' * 255} "}

    with pytest.raises(AdmissionContextError, match="length"):
        build_admission_context(actor(), "image.batch", batch_request([asset]), raw_key="key")


def test_batch_description_limit_measures_raw_text_including_whitespace() -> None:
    exact = batch_asset("asset-a") | {"description": " " * 16_000}
    accepted = build_admission_context(
        actor(), "image.batch", batch_request([exact]), raw_key="key"
    )
    assert accepted.canonical_request["assets"][0]["description"] == ""

    over = batch_asset("asset-a") | {"description": " " * 16_001}
    with pytest.raises(AdmissionContextError, match="16000"):
        build_admission_context(actor(), "image.batch", batch_request([over]), raw_key="key")


def test_batch_raw_aggregate_text_limit_counts_whitespace_before_normalization() -> None:
    assets = [
        batch_asset(f"asset-{index}") | {"description": " " * 16_000}
        for index in range(17)
    ]

    with pytest.raises(AdmissionContextError, match="256000"):
        build_admission_context(actor(), "image.batch", batch_request(assets), raw_key="key")


def test_batch_reference_utf8_limit_counts_trimmed_outer_whitespace() -> None:
    reference = (" " * 9) + ("界" * 2_796_200)
    asset = batch_asset("asset-a", references=[reference])

    with pytest.raises(AdmissionContextError, match="8 MiB"):
        build_admission_context(actor(), "image.batch", batch_request([asset]), raw_key="key")


def test_batch_raw_total_reference_limit_counts_removable_whitespace() -> None:
    reference = (" " * 200) + ("x" * 6_710_700)
    assets = [
        batch_asset(f"asset-{index}", references=[reference])
        for index in range(5)
    ]

    with pytest.raises(AdmissionContextError, match="32 MiB"):
        build_admission_context(actor(), "image.batch", batch_request(assets), raw_key="key")


def test_batch_ordinary_outer_whitespace_still_normalizes_to_legacy_digest() -> None:
    padded_asset = batch_asset(
        "asset-a", references=[" https://cdn.example/ref?X=1 "]
    ) | {
        "type": " character ",
        "id": " asset-a ",
        "name": " Person ",
        "description": " portrait ",
        "model_id": " image-1 ",
        "prompt_id": "  ",
    }
    plain_asset = batch_asset(
        "asset-a", references=["https://cdn.example/ref?X=1"]
    ) | {"name": "Person"}
    padded = build_admission_context(
        actor(), "image.batch", batch_request([padded_asset]), raw_key="key"
    )
    plain = build_admission_context(
        actor(), "image.batch", batch_request([plain_asset]), raw_key="key"
    )

    assert padded.canonical_request == plain.canonical_request
    assert padded.fingerprint == plain.fingerprint
