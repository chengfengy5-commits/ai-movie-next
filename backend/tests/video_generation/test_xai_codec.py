import json
import unittest
from collections.abc import Mapping

from haoai_backend.generation_providers.video.xai_codec import (
    XaiCreateResponse,
    XaiHTTPResponseError,
    XaiQueryResponse,
    XaiResponseParseError,
    build_video_payload,
    parse_create_response,
    parse_query_response,
)


class BuildVideoPayloadTests(unittest.TestCase):
    def test_text_to_video_preserves_falsey_defaults_and_omissions(self) -> None:
        payload = build_video_payload(
            model="",
            prompt="A quiet street",
            size="",
            duration=0,
            resolution="",
            reference_images=[],
        )

        self.assertEqual(
            payload,
            {
                "model": "grok-imagine-video",
                "prompt": "A quiet street",
                "aspect_ratio": "16:9",
            },
        )

    def test_single_reference_uses_image_object_and_fifteen_second_cap(self) -> None:
        payload = build_video_payload(
            model="grok-imagine-video-1.5-preview",
            prompt="A moving scene",
            size="9:16",
            duration=18,
            resolution="720p",
            reference_images=["https://media.example/first.png"],
        )

        self.assertEqual(
            payload,
            {
                "model": "grok-imagine-video-1.5-preview",
                "prompt": "A moving scene",
                "aspect_ratio": "9:16",
                "resolution": "720p",
                "image": {"url": "https://media.example/first.png"},
                "duration": 15,
            },
        )

    def test_multiple_refs_keep_wire_shape_and_default_duration(self) -> None:
        references = [f"https://media.example/{index}.png" for index in range(1, 10)]

        payload = build_video_payload(
            model="grok-imagine-video",
            prompt="A scene with references",
            duration=0,
            reference_images=references,
        )

        self.assertEqual(
            payload["reference_images"],
            [{"url": reference} for reference in references[:7]],
        )
        self.assertNotIn("image", payload)
        self.assertEqual(payload["duration"], 10)

    def test_multiple_reference_duration_is_capped_at_ten_seconds(self) -> None:
        payload = build_video_payload(
            model="grok-imagine-video",
            prompt="A scene with references",
            duration=14,
            reference_images=["one", "two"],
        )

        self.assertEqual(payload["duration"], 10)

    def test_model_reference_limit_is_applied_before_mode_selection(self) -> None:
        payload = build_video_payload(
            model="grok-imagine-video",
            prompt="A scene",
            reference_images=["first", "second"],
            max_reference_images=1,
        )

        self.assertEqual(payload["image"], {"url": "first"})
        self.assertNotIn("reference_images", payload)

    def test_negative_truthy_duration_is_not_reinterpreted_by_the_codec(self) -> None:
        payload = build_video_payload(
            model="grok-imagine-video",
            prompt="A scene",
            duration=-2,
            reference_images=[],
        )

        self.assertEqual(payload["duration"], -2)


class ParseCreateResponseTests(unittest.TestCase):
    def test_request_id_is_read_without_using_the_id_alias(self) -> None:
        parsed = parse_create_response(
            200,
            json.dumps({"request_id": "req-17", "id": "other-id"}),
        )

        self.assertEqual(parsed.request_id, "req-17")

    def test_missing_request_id_remains_missing(self) -> None:
        parsed = parse_create_response(200, b'{"id":"not-the-source-field"}')

        self.assertIsNone(parsed.request_id)

    def test_request_id_preserves_raw_json_scalars_and_nested_shape(self) -> None:
        cases = (
            (b'{"request_id":null}', None),
            (b'{"request_id":false}', False),
            (b'{"request_id":42}', 42),
            (b'{"request_id":[1,{"label":"source"}]}', (1, {"label": "source"})),
        )

        for body, expected in cases:
            with self.subTest(body=body):
                parsed = parse_create_response(200, body)
                self.assertEqual(parsed.request_id, expected)

    def test_response_records_copy_and_deep_freeze_constructor_inputs(self) -> None:
        external = {"parts": [{"label": "before"}]}
        create = XaiCreateResponse(request_id=external)
        query = XaiQueryResponse(
            status=external,
            video_url=[{"source": "before"}],
            error_message=external,
        )

        external["parts"][0]["label"] = "after"
        external["parts"].append({"label": "later"})

        self.assertEqual(create.request_id["parts"][0]["label"], "before")
        self.assertEqual(query.status["parts"][0]["label"], "before")
        self.assertEqual(query.error_message["parts"][0]["label"], "before")
        self.assertEqual(query.video_url[0]["source"], "before")
        self.assertIsInstance(create.request_id, Mapping)
        self.assertIsInstance(query.video_url, tuple)

        with self.assertRaises(TypeError):
            create.request_id["parts"][0]["label"] = "mutated"
        with self.assertRaises(AttributeError):
            query.video_url.append({"source": "mutated"})


class ParseQueryResponseTests(unittest.TestCase):
    def test_done_status_accepts_legacy_string_and_object_media_shapes(self) -> None:
        with self.subTest(shape="string"):
            parsed = parse_query_response(
                200,
                b'{"status":"done","video":"https://media.example/video.mp4"}',
            )
            self.assertEqual(parsed.video_url, "https://media.example/video.mp4")

        with self.subTest(shape="object"):
            parsed = parse_query_response(
                200,
                b'{"status":"done","video":{"url":"https://media.example/nested.mp4"}}',
            )
            self.assertEqual(parsed.video_url, "https://media.example/nested.mp4")

    def test_done_without_media_keeps_the_source_empty_value(self) -> None:
        parsed = parse_query_response(200, b'{"status":"done"}')

        self.assertEqual(parsed.status, "done")
        self.assertEqual(parsed.video_url, "")
        self.assertIsNone(parsed.error_message)

    def test_failed_status_preserves_source_error_shapes(self) -> None:
        with self.subTest(shape="object"):
            parsed = parse_query_response(
                200,
                b'{"status":"failed","error":{"message":"source error"}}',
            )
            self.assertEqual(parsed.error_message, "source error")

        with self.subTest(shape="string"):
            parsed = parse_query_response(
                200,
                b'{"status":"failed","error":"source error"}',
            )
            self.assertEqual(parsed.error_message, "source error")

        with self.subTest(shape="missing"):
            parsed = parse_query_response(200, b'{"status":"failed"}')
            self.assertEqual(parsed.error_message, "未知错误")

        for encoded_error, expected in (
            (b"null", "None"),
            (b"false", "False"),
            (b"42", "42"),
            (b'[1,"two"]', "[1, 'two']"),
        ):
            with self.subTest(shape=encoded_error):
                body = b'{"status":"failed","error":' + encoded_error + b"}"
                parsed = parse_query_response(200, body)
                self.assertEqual(parsed.error_message, expected)

    def test_nested_raw_response_fields_are_deep_frozen(self) -> None:
        created = parse_create_response(
            200,
            b'{"request_id":{"parts":[{"label":"source"}]}}',
        )
        queried = parse_query_response(
            200,
            b'{"status":{"parts":[{"label":"source"}]}}',
        )
        media = parse_query_response(
            200,
            b'{"status":"done","video":{"url":{"nested":[]}}}',
        )

        self.assertEqual(created.request_id["parts"][0]["label"], "source")
        self.assertEqual(queried.status["parts"][0]["label"], "source")
        self.assertEqual(media.video_url["nested"], ())
        with self.assertRaises(TypeError):
            created.request_id["parts"][0]["label"] = "mutated"
        with self.assertRaises(TypeError):
            queried.status["parts"][0]["label"] = "mutated"
        with self.assertRaises(AttributeError):
            media.video_url["nested"].append("mutated")

    def test_expired_and_unknown_statuses_remain_raw_provider_values(self) -> None:
        expired = parse_query_response(200, b'{"status":"expired"}')
        unknown = parse_query_response(200, b'{"status":"future-provider-state"}')

        self.assertEqual(expired.status, "expired")
        self.assertEqual(unknown.status, "future-provider-state")
        self.assertEqual(unknown.video_url, "")
        self.assertIsNone(unknown.error_message)

    def test_malformed_json_and_non_object_json_raise_parse_errors(self) -> None:
        with self.assertRaises(XaiResponseParseError):
            parse_query_response(200, b'{"status":')

        with self.assertRaises(XaiResponseParseError):
            parse_create_response(200, b'["request_id"]')

    def test_non_200_response_preserves_raw_http_status_and_body(self) -> None:
        body = b"upstream unavailable"

        with self.assertRaises(XaiHTTPResponseError) as raised:
            parse_create_response(502, body)

        self.assertEqual(raised.exception.status_code, 502)
        self.assertEqual(raised.exception.body, body)


if __name__ == "__main__":
    unittest.main()
