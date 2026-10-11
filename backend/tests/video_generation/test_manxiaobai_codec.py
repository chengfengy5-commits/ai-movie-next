from __future__ import annotations

import json
import unittest
from collections.abc import Mapping

from haoai_backend.generation_providers.video.manxiaobai_codec import (
    DEFAULT_VIDEO_MODEL,
    VIDEO_COLLECTION_PATH,
    ManxiaobaiCreateResponse,
    ManxiaobaiHTTPResponseError,
    ManxiaobaiQueryResponse,
    ManxiaobaiResponseParseError,
    PreparedReferenceError,
    build_manxiaobai_video_payload,
    parse_manxiaobai_create_response,
    parse_manxiaobai_query_response,
)


class BuildManxiaobaiVideoPayloadTests(unittest.TestCase):
    def test_legacy_json_path_and_text_video_defaults(self) -> None:
        payload = build_manxiaobai_video_payload("A quiet harbor")

        self.assertEqual(VIDEO_COLLECTION_PATH, "/v1/videos")
        self.assertEqual(
            payload,
            {
                "model": DEFAULT_VIDEO_MODEL,
                "prompt": "A quiet harbor",
                "seconds": "6",
                "aspect_ratio": "16:9",
                "resolution": "720p",
            },
        )
        self.assertNotIn("input_reference", payload)

    def test_original_wire_model_and_falsey_defaults_are_preserved(self) -> None:
        payload = build_manxiaobai_video_payload(
            "Reference-driven scene",
            wire_model="grok-video-1.0-custom",
            duration=0,
            size="",
            resolution="",
        )

        self.assertEqual(payload["model"], "grok-video-1.0-custom")
        self.assertEqual(payload["seconds"], "6")
        self.assertEqual(payload["aspect_ratio"], "16:9")
        self.assertEqual(payload["resolution"], "720p")

    def test_truthy_duration_and_dimensions_keep_legacy_wire_shapes(self) -> None:
        payload = build_manxiaobai_video_payload(
            "A moving train",
            wire_model="grok-video-1.0",
            duration=8,
            size="9:16",
            resolution="1080p",
        )

        self.assertEqual(payload["seconds"], "8")
        self.assertEqual(payload["aspect_ratio"], "9:16")
        self.assertEqual(payload["resolution"], "1080p")

    def test_model_containing_15_keeps_only_the_first_reference_scalar(self) -> None:
        first = "https://cdn.example.test/first.png?version=1"
        second = "https://cdn.example.test/second.png"
        payload = build_manxiaobai_video_payload(
            "Animate the still",
            wire_model="grok-video-1.5-preview",
            reference_images=[first, second],
        )

        self.assertEqual(payload["model"], "grok-video-1.5-preview")
        self.assertEqual(payload["input_reference"], first)
        self.assertIsInstance(payload["input_reference"], str)

    def test_other_models_keep_at_most_seven_references_as_a_list(self) -> None:
        references = [f"https://cdn.example.test/{index}.png" for index in range(8)]
        payload = build_manxiaobai_video_payload(
            "Animate the collection",
            wire_model="grok-video-1.0",
            reference_images=references,
        )

        self.assertEqual(payload["input_reference"], references[:7])
        self.assertIsInstance(payload["input_reference"], list)
        self.assertEqual(len(payload["input_reference"]), 7)

    def test_one_prepared_reference_uses_a_scalar(self) -> None:
        reference = "https://cdn.example.test/one.png"
        payload = build_manxiaobai_video_payload(
            "Animate one image", reference_images=[reference]
        )
        self.assertEqual(payload["input_reference"], reference)

    def test_codec_rejects_unprepared_or_non_url_references(self) -> None:
        invalid_references = (
            "data:image/png;base64,AAAA",
            "/tmp/source.png",
            "ftp://cdn.example.test/image.png",
            "https://",
            "https://user:password@cdn.example.test/image.png",
            "https://cdn.example.test:invalid/image.png",
            "https://cdn.example.test/image.png\n",
            b"image-bytes",
        )
        for reference in invalid_references:
            with self.subTest(reference=reference):
                with self.assertRaises(PreparedReferenceError):
                    build_manxiaobai_video_payload(
                        "Prompt",
                        reference_images=[reference],  # type: ignore[list-item]
                    )

    def test_reference_container_must_be_a_sequence_not_a_single_string(self) -> None:
        single_url = "https://cdn.example.test/one.png"
        with self.assertRaises(PreparedReferenceError):
            build_manxiaobai_video_payload(
                "Prompt", reference_images=single_url  # type: ignore[arg-type]
            )


class ParseManxiaobaiCreateResponseTests(unittest.TestCase):
    def test_create_prefers_id_then_task_id_and_keeps_raw_fields(self) -> None:
        response = parse_manxiaobai_create_response(
            b'{"id":"video-1","task_id":"task-2","request_id":"xai-3",'
            b'"metadata":{"nested":[1,2]}}'
        )

        self.assertEqual(response.source_id, "video-1")
        self.assertEqual(response.raw_fields["task_id"], "task-2")
        self.assertEqual(response.raw_fields["request_id"], "xai-3")
        self.assertEqual(response.raw_fields["metadata"]["nested"], (1, 2))
        self.assertIsInstance(response.raw_fields, Mapping)
        with self.assertRaises(TypeError):
            response.raw_fields["id"] = "changed"  # type: ignore[index]
        with self.assertRaises(TypeError):
            response.raw_fields["metadata"]["new"] = True  # type: ignore[index]

    def test_create_falls_back_to_task_id_and_does_not_alias_request_id(self) -> None:
        fallback = parse_manxiaobai_create_response(
            '{"id":"","task_id":"task-2","request_id":"xai-3"}'
        )
        request_id_only = parse_manxiaobai_create_response(
            b'{"request_id":"xai-3"}'
        )

        self.assertEqual(fallback.source_id, "task-2")
        self.assertIsNone(request_id_only.source_id)

    def test_create_rejects_non_200_malformed_and_non_object_responses(self) -> None:
        with self.assertRaises(ManxiaobaiHTTPResponseError) as error:
            parse_manxiaobai_create_response(b'{"id":"ignored"}', status_code=201)
        self.assertEqual(error.exception.status_code, 201)

        for body in (b"not-json", b"[]", b"42", b"{\"value\":NaN}"):
            with self.subTest(body=body):
                with self.assertRaises(ManxiaobaiResponseParseError):
                    parse_manxiaobai_create_response(body)


    def test_create_record_constructor_defensively_freezes_mapping_and_tuple_inputs(
        self,
    ) -> None:
        source_id = {"parts": ("video", {"id": "original"})}
        nested_metadata = {"labels": ("source",)}
        raw_fields = {"id": source_id, "metadata": (nested_metadata,)}
        record = ManxiaobaiCreateResponse(source_id=source_id, raw_fields=raw_fields)

        source_id["parts"][1]["id"] = "mutated"
        nested_metadata["labels"] = ("source", "mutated")
        raw_fields["extra"] = "mutated"

        self.assertEqual(record.source_id["parts"][1]["id"], "original")
        self.assertEqual(record.raw_fields["metadata"][0]["labels"], ("source",))
        self.assertNotIn("extra", record.raw_fields)
        with self.assertRaises(TypeError):
            record.source_id["parts"][1]["id"] = "changed"  # type: ignore[index]
        with self.assertRaises(TypeError):
            record.raw_fields["metadata"][0]["new"] = True  # type: ignore[index]
        labels = record.raw_fields["metadata"][0]["labels"]
        with self.assertRaises(AttributeError):
            labels.append("changed")  # type: ignore[attr-defined]

class ParseManxiaobaiQueryResponseTests(unittest.TestCase):
    def test_top_status_wins_and_nested_status_is_the_falsey_fallback(self) -> None:
        top = parse_manxiaobai_query_response(
            b'{"status":"processing","data":{"status":"done"}}'
        )
        nested = parse_manxiaobai_query_response(
            b'{"status":"","data":{"status":"done"}}'
        )

        self.assertEqual(top.raw_status, "processing")
        self.assertEqual(top.source_state, "pending")
        self.assertEqual(nested.raw_status, "done")
        self.assertEqual(nested.source_state, "completed")

    def test_completed_status_spellings_match_the_source_exactly(self) -> None:
        for status in ("completed", "done", "success", "succeeded", "SUCCESS"):
            with self.subTest(status=status):
                result = parse_manxiaobai_query_response(
                    json.dumps({"status": status}).encode()
                )
                self.assertEqual(result.source_state, "completed")
                self.assertTrue(result.bound_content_needed)

        lowercase_success = parse_manxiaobai_query_response(b'{"status":"success"}')
        uppercase_only = parse_manxiaobai_query_response(b'{"status":"Success"}')
        self.assertEqual(lowercase_success.source_state, "completed")
        self.assertEqual(uppercase_only.source_state, "pending")

    def test_non_string_status_values_remain_immutable_unknown_source_values(
        self,
    ) -> None:
        cases = (
            (b'{"status":{"state":"future"}}', {"state": "future"}),
            (b'{"status":["future"]}', ("future",)),
            (b'{"status":42}', 42),
        )
        for body, expected_status in cases:
            with self.subTest(expected_status=expected_status):
                result = parse_manxiaobai_query_response(body)
                self.assertEqual(result.raw_status, expected_status)
                self.assertEqual(result.source_state, "pending")

    def test_video_locator_precedence_is_video_then_result_then_metadata(self) -> None:
        cases = (
            (
                {
                    "status": "done",
                    "video": "https://video.example.test/video.mp4?sig=1",
                    "result_url": "https://result.example.test/video.mp4",
                    "metadata": {"url": "https://metadata.example.test/video.mp4"},
                },
                "https://video.example.test/video.mp4?sig=1",
            ),
            (
                {
                    "status": "done",
                    "video": {"url": "https://video.example.test/object.mp4"},
                    "result_url": "https://result.example.test/video.mp4",
                    "metadata": {"url": "https://metadata.example.test/video.mp4"},
                },
                "https://video.example.test/object.mp4",
            ),
            (
                {
                    "status": "done",
                    "result_url": "https://result.example.test/video.mp4",
                    "metadata": {"url": "https://metadata.example.test/video.mp4"},
                },
                "https://result.example.test/video.mp4",
            ),
            (
                {
                    "status": "done",
                    "metadata": {"url": "https://metadata.example.test/video.mp4"},
                },
                "https://metadata.example.test/video.mp4",
            ),
        )
        for raw, expected_url in cases:
            with self.subTest(expected_url=expected_url):
                result = parse_manxiaobai_query_response(
                    json.dumps(raw, separators=(",", ":"))
                )
                self.assertEqual(result.video_url, expected_url)
                self.assertEqual(result.source_state, "completed")
                self.assertFalse(result.bound_content_needed)

    def test_video_presence_and_code_follow_legacy_completion_order(self) -> None:
        cases = (
            (b'{"code":0,"video":"video.mp4"}', "completed", True),
            (b'{"code":200,"result_url":"result.mp4"}', "completed", True),
            (b'{"status":"processing","video":{}}', "pending", False),
            (b'{"code":200}', "pending", False),
            (b'{"status":"failed","code":1,"video":"video.mp4"}', "completed", True),
        )
        for body, expected_state, expected_has_video in cases:
            with self.subTest(body=body):
                result = parse_manxiaobai_query_response(body)
                self.assertEqual(result.source_state, expected_state)
                self.assertEqual(result.has_video, expected_has_video)

    def test_completed_without_url_marks_only_source_bound_content_need(self) -> None:
        result = parse_manxiaobai_query_response(
            b'{"status":"completed","video":{}}'
        )

        self.assertEqual(result.source_state, "completed")
        self.assertFalse(result.has_video)
        self.assertEqual(result.video_url, "")
        self.assertTrue(result.bound_content_needed)
        self.assertIsNone(result.failure_signal)

    def test_failed_status_and_code_preserve_source_error_precedence(self) -> None:
        cases = (
            (
                b'{"status":"failed","error":{"message":"nested"},'
                b'"fail_reason":"reason","message":"message"}',
                "nested",
            ),
            (
                b'{"status":"FAILURE","error":{},"fail_reason":"reason",'
                b'"message":"message"}',
                "reason",
            ),
            (b'{"code":1,"message":"message"}', "message"),
            (b'{"status":"failed","message":""}', ""),
            (b'{"status":"failed","message":null}', None),
            (b'{"status":"failed","message":0}', 0),
            (b'{"status":"failed","message":false}', False),
            (b'{"status":"failed"}', "未知错误"),
        )
        for body, expected_signal in cases:
            with self.subTest(expected_signal=expected_signal):
                result = parse_manxiaobai_query_response(body)
                self.assertEqual(result.source_state, "failed")
                self.assertEqual(result.failure_signal, expected_signal)

    def test_raw_status_code_and_nested_error_fields_are_recursively_immutable(
        self,
    ) -> None:
        result = parse_manxiaobai_query_response(
            b'{"status":"failed","code":1,"error":{"message":"source",'
            b'"details":{"items":["a","b"]}}}'
        )

        self.assertEqual(result.raw_status, "failed")
        self.assertEqual(result.raw_code, 1)
        self.assertEqual(
            result.raw_fields["error"]["details"]["items"], ("a", "b")
        )
        with self.assertRaises(TypeError):
            result.raw_fields["error"]["message"] = "changed"  # type: ignore[index]
        with self.assertRaises(TypeError):
            result.raw_fields["error"]["details"]["new"] = True  # type: ignore[index]
        with self.assertRaises(AttributeError):
            result.raw_fields["error"]["details"]["items"].append(
                "changed"
            )  # type: ignore[attr-defined]


    def test_query_record_constructor_freezes_each_raw_json_field_and_mapping(
        self,
    ) -> None:
        raw_status = {"nested": ("queued",)}
        raw_code = (0, {"values": (1, 2)})
        video_url = {"locator": ("https://video.example.test/out.mp4",)}
        failure_signal = {"details": ("source failure",)}
        raw_fields = {
            "status": raw_status,
            "code": raw_code,
            "error": (failure_signal,),
        }
        record = ManxiaobaiQueryResponse(
            source_state="failed",
            raw_status=raw_status,
            raw_code=raw_code,
            has_video=False,
            video_url=video_url,
            bound_content_needed=False,
            failure_signal=failure_signal,
            raw_fields=raw_fields,
        )

        raw_status["nested"] = ("queued", "mutated")
        raw_code[1]["values"] = (1, 2, 3)
        video_url["locator"] = ("https://video.example.test/out.mp4", "mutated")
        failure_signal["details"] = ("source failure", "mutated")
        raw_fields["extra"] = True

        self.assertEqual(record.raw_status["nested"], ("queued",))
        self.assertEqual(record.raw_code[1]["values"], (1, 2))
        self.assertEqual(
            record.video_url["locator"],
            ("https://video.example.test/out.mp4",),
        )
        self.assertEqual(record.failure_signal["details"], ("source failure",))
        self.assertNotIn("extra", record.raw_fields)
        with self.assertRaises(TypeError):
            record.raw_status["nested"] = ("changed",)  # type: ignore[index]
        with self.assertRaises(TypeError):
            record.raw_code[1]["values"] = (9,)  # type: ignore[index]
        with self.assertRaises(TypeError):
            record.video_url["locator"][0] = "changed"  # type: ignore[index]
        with self.assertRaises(TypeError):
            record.failure_signal["details"][0] = "changed"  # type: ignore[index]
        details = record.raw_fields["error"][0]["details"]
        with self.assertRaises(AttributeError):
            details.append("changed")  # type: ignore[attr-defined]

    def test_query_rejects_non_200_malformed_and_non_object_responses(self) -> None:
        with self.assertRaises(ManxiaobaiHTTPResponseError) as error:
            parse_manxiaobai_query_response(b'{"status":"done"}', status_code=503)
        self.assertEqual(error.exception.status_code, 503)

        for body in (b"not-json", b"null", b"[]", b'"value"'):
            with self.subTest(body=body):
                with self.assertRaises(ManxiaobaiResponseParseError):
                    parse_manxiaobai_query_response(body)


if __name__ == "__main__":
    unittest.main()
