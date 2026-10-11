from __future__ import annotations

import json
import unittest
from collections.abc import Mapping

from haoai_backend.generation_providers.video.haoai_codec import (
    DEFAULT_HAOAI_VIDEO_MODEL,
    DEFAULT_MAX_REFERENCE_IMAGES,
    HaoaiCreateResponse,
    HaoaiQueryResponse,
    build_haoai_video_create_payload,
    haoai_video_create_url,
    haoai_video_query_url,
    normalize_haoai_video_base_url,
    parse_haoai_create_response,
    parse_haoai_query_response,
)


class HaoaiVideoPayloadTests(unittest.TestCase):
    def test_default_payload_and_falsey_legacy_defaults(self) -> None:
        self.assertEqual(DEFAULT_MAX_REFERENCE_IMAGES, 9)
        self.assertEqual(
            build_haoai_video_create_payload("make a clip", duration=0, size="", resolution=""),
            {
                "model": DEFAULT_HAOAI_VIDEO_MODEL,
                "prompt": "make a clip",
                "seconds": 4,
                "aspect_ratio": "16:9",
            },
        )

    def test_truthy_duration_is_int_and_optional_fields_are_preserved(self) -> None:
        self.assertEqual(
            build_haoai_video_create_payload(
                "prompt", model="wire-model", duration=5.9, size="9:16", resolution="1080p"
            ),
            {
                "model": "wire-model",
                "prompt": "prompt",
                "seconds": 5,
                "aspect_ratio": "9:16",
                "resolution": "1080p",
            },
        )

    def test_truthy_non_integer_duration_keeps_int_conversion_error(self) -> None:
        with self.assertRaises(ValueError):
            build_haoai_video_create_payload("prompt", duration="5.9")

    def test_single_prepared_reference_uses_input_reference_and_keeps_bytes(self) -> None:
        reference = "https://cdn.example/video-frame.jpg?token=keep%2Fme&x=1"
        payload = build_haoai_video_create_payload("prompt", reference_images=[reference])
        self.assertEqual(payload["input_reference"], {"image_url": reference})
        self.assertNotIn("reference_images", payload)
        self.assertEqual(payload["prompt"], "prompt")

    def test_multiple_references_use_url_only_entries_and_exact_prompt_suffix(self) -> None:
        references = ["https://one.test/a?q=1", "https://two.test/b?x=2", "https://three.test/c"]
        payload = build_haoai_video_create_payload(
            "story", reference_images=references, max_reference_images=2
        )
        self.assertEqual(
            payload["reference_images"],
            [{"url": references[0]}, {"url": references[1]}],
        )
        self.assertEqual(payload["prompt"], "story\n\n参考图：<IMAGE_1> <IMAGE_2>")
        self.assertNotIn("input_reference", payload)
        self.assertFalse(any("role" in item for item in payload["reference_images"]))

    def test_default_reference_cap_is_nine_and_non_string_entries_are_ignored(self) -> None:
        references = [f"https://images.test/{index}" for index in range(10)]
        payload = build_haoai_video_create_payload("p", reference_images=references)
        self.assertEqual(len(payload["reference_images"]), DEFAULT_MAX_REFERENCE_IMAGES)
        self.assertEqual(payload["reference_images"][-1], {"url": references[8]})
        single = build_haoai_video_create_payload("p", reference_images=[object(), "prepared"])
        self.assertEqual(single["input_reference"], {"image_url": "prepared"})

    def test_prompt_suffix_is_not_added_for_zero_or_one_selected_reference(self) -> None:
        empty = build_haoai_video_create_payload("p", reference_images=[])
        self.assertNotIn("参考图：", empty["prompt"])
        self.assertNotIn(
            "参考图：",
            build_haoai_video_create_payload("p", reference_images=["only"])["prompt"],
        )


class HaoaiVideoEndpointTests(unittest.TestCase):
    def test_default_base_and_terminal_v1_are_normalized(self) -> None:
        self.assertEqual(normalize_haoai_video_base_url(None), "https://cpa.haoai.icu")
        self.assertEqual(
            haoai_video_create_url("https://proxy.example/root/v1///"),
            "https://proxy.example/root/openai/v1/videos",
        )
        self.assertEqual(
            haoai_video_create_url("https://proxy.example/root/v1/v1"),
            "https://proxy.example/root/v1/openai/v1/videos",
        )

    def test_known_original_id_is_interpolated_into_native_query_path(self) -> None:
        self.assertEqual(
            haoai_video_query_url("https://proxy.example/v1", "id-123"),
            "https://proxy.example/openai/v1/videos/id-123",
        )


class HaoaiCreateResponseTests(unittest.TestCase):
    def test_request_id_wins_and_raw_id_fields_are_kept(self) -> None:
        parsed = parse_haoai_create_response(
            200, '{"request_id":"request-1","id":"id-1","status":"queued"}'
        )
        self.assertEqual(parsed.body_kind, "object")
        self.assertEqual(parsed.external_id, "request-1")
        self.assertEqual(parsed.external_id_source, "request_id")
        self.assertEqual(parsed.raw_id, "id-1")
        self.assertTrue(parsed.status_present)
        self.assertEqual(parsed.raw_status, "queued")

    def test_falsey_request_id_falls_back_to_id_and_falsey_fields_remain_visible(self) -> None:
        parsed = parse_haoai_create_response(200, '{"request_id":0,"id":"id-2","status":false}')
        self.assertTrue(parsed.request_id_present)
        self.assertEqual(parsed.raw_request_id, 0)
        self.assertEqual(parsed.external_id, "id-2")
        self.assertFalse(parsed.raw_status)

    def test_missing_id_falsey_id_and_absent_fields_remain_distinguishable(self) -> None:
        absent = parse_haoai_create_response(200, '{"status":"queued"}')
        present_falsey = parse_haoai_create_response(200, '{"request_id":"","id":null}')
        self.assertFalse(absent.request_id_present)
        self.assertFalse(absent.id_present)
        self.assertIsNone(absent.external_id)
        self.assertTrue(present_falsey.request_id_present)
        self.assertTrue(present_falsey.id_present)
        self.assertEqual(present_falsey.raw_request_id, "")
        self.assertIsNone(present_falsey.raw_id)
        self.assertIsNone(present_falsey.external_id)

    def test_malformed_non_object_and_non_200_responses_stay_distinct(self) -> None:
        malformed = parse_haoai_create_response(200, "{")
        scalar = parse_haoai_create_response(200, "[]")
        http_error = parse_haoai_create_response(503, '{"id":"ignored"}')
        self.assertEqual(malformed.body_kind, "malformed_json")
        self.assertEqual(scalar.body_kind, "non_object")
        self.assertEqual(http_error.body_kind, "http_error")
        self.assertIsNone(http_error.external_id)

    def test_exported_create_record_recursively_freezes_direct_json_inputs(self) -> None:
        original = {"nested": [{"value": 1}]}
        record = HaoaiCreateResponse(
            200,
            "object",
            request_id_present=True,
            raw_request_id=original,
            external_id=original,
        )
        original["nested"][0]["value"] = 2
        self.assertEqual(record.raw_request_id["nested"][0]["value"], 1)
        with self.assertRaises(TypeError):
            record.raw_request_id["nested"][0]["value"] = 3
        self.assertIsInstance(record.raw_request_id, Mapping)


class HaoaiQueryResponseTests(unittest.TestCase):
    def test_completed_statuses_and_media_locator_precedence(self) -> None:
        cases = [
            (
                '{"status":"completed","video_url":"top","video":{"url":"nested"},"url":"last"}',
                "top",
                "video_url",
            ),
            ('{"status":"done","video":{"url":"nested"},"url":"last"}', "nested", "video.url"),
            (
                '{"status":"completed","video":"video-string","url":"last"}',
                "video-string",
                "video_string",
            ),
            ('{"status":"done","url":"last"}', "last", "url"),
        ]
        for body, locator, source in cases:
            with self.subTest(body=body):
                parsed = parse_haoai_query_response(200, body)
                self.assertTrue(parsed.matches_completion_status)
                self.assertEqual(parsed.media_locator, locator)
                self.assertEqual(parsed.media_locator_source, source)
                self.assertFalse(parsed.completed_without_locator)

    def test_falsey_locator_falls_through_and_no_locator_stays_raw(self) -> None:
        parsed = parse_haoai_query_response(
            200, '{"status":"completed","video_url":"","video":{"url":null},"url":false}'
        )
        self.assertTrue(parsed.matches_completion_status)
        self.assertFalse(parsed.media_locator)
        self.assertTrue(parsed.completed_without_locator)
        self.assertTrue(parsed.video_url_present)
        self.assertTrue(parsed.video_present)
        self.assertTrue(parsed.url_present)
        self.assertFalse(hasattr(parsed, "result"))

    def test_unknown_and_malformed_statuses_are_not_promoted(self) -> None:
        unknown = parse_haoai_query_response(200, '{"status":"in_progress","progress":0}')
        malformed = parse_haoai_query_response(200, "not-json")
        scalar = parse_haoai_query_response(200, "null")
        self.assertEqual(unknown.raw_status, "in_progress")
        self.assertFalse(unknown.matches_completion_status)
        self.assertFalse(unknown.matches_failed_status)
        self.assertTrue(unknown.progress_present)
        self.assertEqual(unknown.raw_progress, 0)
        self.assertEqual(malformed.body_kind, "malformed_json")
        self.assertEqual(scalar.body_kind, "non_object")

    def test_failed_error_message_uses_source_dict_and_top_level_fallbacks(self) -> None:
        missing_message = parse_haoai_query_response(200, '{"status":"failed","error":{}}')
        missing_error = parse_haoai_query_response(200, '{"status":"failed"}')
        string_error = parse_haoai_query_response(200, '{"status":"failed","error":"oops"}')
        self.assertTrue(missing_message.matches_failed_status)
        self.assertEqual(missing_message.error_message, "未知错误")
        self.assertEqual(missing_error.error_message, "未知错误")
        self.assertEqual(string_error.error_message, "oops")

    def test_falsey_error_message_values_are_preserved(self) -> None:
        for value in (None, False, 0, ""):
            body = json.dumps({"status": "failed", "error": {"message": value}})
            with self.subTest(value=value):
                parsed = parse_haoai_query_response(200, body)
                self.assertEqual(parsed.error_message, value)
                self.assertTrue(parsed.error_present)
        for value in (None, False, 0, ""):
            body = json.dumps({"status": "failed", "error": value})
            with self.subTest(top_level=value):
                self.assertEqual(parse_haoai_query_response(200, body).error_message, value)

    def test_http200_image_download_markers_are_raw_protocol_facts(self) -> None:
        cases = [
            ("image_download_interrupted", "image_download"),
            ("Failed to download the provided image", "failed to download the provided image"),
            ("Downloading the image failed", "downloading the image"),
        ]
        for message, marker in cases:
            with self.subTest(marker=marker):
                parsed = parse_haoai_query_response(
                    200, json.dumps({"status": "failed", "error": {"message": message}})
                )
                self.assertEqual(parsed.image_download_markers, (marker,))
                self.assertFalse(hasattr(parsed, "retry"))

    def test_non_200_auth_markers_are_classified_without_retaining_body(self) -> None:
        markers = (
            "auth_unavailable",
            "no auth available",
            "no available auth candidates",
            "all candidates are unavailable",
        )
        for marker in markers:
            with self.subTest(marker=marker):
                parsed = parse_haoai_query_response(503, f"gateway: {marker}")
                self.assertEqual(parsed.body_kind, "http_error")
                self.assertEqual(parsed.auth_unavailable_markers, (marker,))
                self.assertFalse(hasattr(parsed, "raw_body"))
        plain = parse_haoai_query_response(503, "upstream busy")
        self.assertEqual(plain.auth_unavailable_markers, ())
        beyond_source_excerpt = parse_haoai_query_response(503, "x" * 500 + "no auth available")
        self.assertEqual(beyond_source_excerpt.auth_unavailable_markers, ())

    def test_exported_query_record_recursively_freezes_direct_json_inputs(self) -> None:
        original = {"detail": [{"code": [1, 2]}]}
        marker_source = ["image_download"]
        record = HaoaiQueryResponse(
            200,
            "object",
            raw_status=original,
            raw_error=original,
            error_message=original,
            media_locator=original,
            image_download_markers=marker_source,
        )
        marker_source.append("new marker")
        self.assertEqual(record.image_download_markers, ("image_download",))
        original["detail"][0]["code"].append(3)
        self.assertEqual(record.raw_status["detail"][0]["code"], (1, 2))
        self.assertEqual(record.media_locator["detail"][0]["code"], (1, 2))
        with self.assertRaises(TypeError):
            record.raw_error["detail"][0]["new"] = True
        with self.assertRaises(AttributeError):
            record.raw_error["detail"][0]["code"].append(4)


if __name__ == "__main__":
    unittest.main()
