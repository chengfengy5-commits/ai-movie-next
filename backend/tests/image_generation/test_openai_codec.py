"""Focused source-backed checks for the raw OpenAI image codec."""

from __future__ import annotations

import json
import unittest

from haoai_backend.generation_providers.image import (
    OpenAIImageCodecError,
    OpenAIImageOutput,
    build_openai_text_to_image_request,
    parse_openai_image_response,
)


class OpenAIImageRequestCodecTests(unittest.TestCase):
    def test_defaults_match_legacy_json_generation_shape(self) -> None:
        request = build_openai_text_to_image_request(
            base_url=None,
            model=None,
            prompt="a quiet harbor",
            size=None,
        )

        self.assertEqual(
            request.url,
            "https://api.openai.com/v1/images/generations",
        )
        self.assertEqual(request.content_type, "application/json")
        self.assertEqual(
            json.loads(request.body_json),
            {
                "model": "dall-e-3",
                "prompt": "a quiet harbor",
                "n": 1,
                "size": "1024x1024",
            },
        )

    def test_size_mapping_unknown_passthrough_and_custom_endpoint(self) -> None:
        widescreen = build_openai_text_to_image_request(
            base_url="https://images.example/v1",
            model="gpt-image-2",
            prompt="landscape",
            size="16:9",
        )
        custom = build_openai_text_to_image_request(
            base_url="https://images.example/v1/",
            model="custom-image",
            prompt="square",
            size="1536x1024",
        )

        self.assertEqual(
            widescreen.url,
            "https://images.example/v1/images/generations",
        )
        self.assertEqual(json.loads(widescreen.body_json)["size"], "1792x1024")
        self.assertEqual(
            custom.url,
            "https://images.example/v1//images/generations",
        )
        self.assertEqual(json.loads(custom.body_json)["size"], "1536x1024")

    def test_haoai_quality_is_provider_specific_and_falsey_values_are_omitted(self) -> None:
        haoai = build_openai_text_to_image_request(
            base_url="",
            model="",
            prompt="",
            size="",
            provider="HAOAI",
            params='{"quality":{"default":"high"}}',
        )
        ordinary = build_openai_text_to_image_request(
            base_url=None,
            model=None,
            prompt="",
            size="",
            provider="openai",
            params={"quality": "high"},
        )
        malformed_params = build_openai_text_to_image_request(
            base_url=None,
            model=None,
            prompt="",
            size="",
            provider="haoai",
            params="{",
        )

        self.assertEqual(
            json.loads(haoai.body_json),
            {
                "model": "dall-e-3",
                "prompt": "",
                "n": 1,
                "size": "1024x1024",
                "quality": "high",
            },
        )
        self.assertNotIn("quality", json.loads(ordinary.body_json))
        self.assertNotIn("quality", json.loads(malformed_params.body_json))


class OpenAIImageResponseCodecTests(unittest.TestCase):
    def test_truthy_url_takes_precedence_and_is_returned_raw(self) -> None:
        result = parse_openai_image_response(
            200,
            {
                "data": [
                    {
                        "url": "https://media.example/image.png?sig=a%2Fb",
                        "b64_json": "eHl6",
                    }
                ]
            },
        )

        self.assertEqual(
            result,
            OpenAIImageOutput(
                kind="url",
                value="https://media.example/image.png?sig=a%2Fb",
            ),
        )

    def test_falsey_url_falls_back_to_raw_base64_value(self) -> None:
        result = parse_openai_image_response(
            200,
            {"data": [{"url": "", "b64_json": "not-decoded-by-codec"}]},
        )

        self.assertEqual(
            result,
            OpenAIImageOutput(kind="base64", value="not-decoded-by-codec"),
        )

    def test_no_media_and_generic_aliases_keep_legacy_openai_shape(self) -> None:
        for body in (
            {"data": [{}]},
            {"data": [{"url": "", "b64_json": ""}]},
            {"data": [{"image_url": "https://ignored.example/image.png"}]},
        ):
            with self.subTest(body=body):
                with self.assertRaises(OpenAIImageCodecError) as raised:
                    parse_openai_image_response(200, body)
                self.assertEqual(raised.exception.code, "missing_media")

    def test_malformed_success_shapes_are_local_parse_errors(self) -> None:
        for body in (None, [], {"data": None}, {"data": []}, {"data": ["image"]}):
            with self.subTest(body=body):
                with self.assertRaises(OpenAIImageCodecError) as raised:
                    parse_openai_image_response(200, body)
                self.assertEqual(raised.exception.code, "invalid_response")

    def test_non_200_keeps_legacy_error_message_and_status(self) -> None:
        with self.assertRaises(OpenAIImageCodecError) as raised:
            parse_openai_image_response(429, {"error": {"message": "ignored"}})

        self.assertEqual(raised.exception.code, "http_error")
        self.assertEqual(raised.exception.status_code, 429)
        self.assertEqual(raised.exception.message, "图片生成失败 (429)")


if __name__ == "__main__":
    unittest.main()
