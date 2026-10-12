from __future__ import annotations

import concurrent.futures
import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import ANY

from boto3.session import Session
from botocore.config import Config
from botocore.stub import Stubber

from haoai_backend.generation_contracts import PreparedReference, StoredMedia
from haoai_backend.media_storage.r2_sdk_factory import Boto3S3ClientFactory
from haoai_backend.media_storage.r2_storage import R2Storage
from haoai_backend.media_storage.transfer_types import (
    MediaTypeRule,
    R2ClientSettings,
    R2Configuration,
    SafeMediaPolicy,
    SecretValue,
    WriteReceipt,
)


def _settings() -> R2ClientSettings:
    return R2ClientSettings(
        endpoint_url="https://account.r2.example.test",
        bucket="media-bucket",
        connect_timeout_seconds=7,
        read_timeout_seconds=19,
    )


class _FakeRawClient:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []
        self.close_count = 0

    def put_object(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return self.response

    def close(self) -> None:
        self.close_count += 1


class _FakeSession:
    def __init__(self, client: _FakeRawClient) -> None:
        self.client_value = client
        self.client_calls: list[tuple[str, dict[str, object]]] = []

    def client(self, service: str, **kwargs: object) -> _FakeRawClient:
        self.client_calls.append((service, kwargs))
        return self.client_value


class R2SdkFactoryTests(unittest.TestCase):
    def test_factory_uses_explicit_credentials_fixed_retries_and_proxy_policy(self) -> None:
        raw_client = _FakeRawClient(
            {"ResponseMetadata": {"HTTPStatusCode": 200, "RequestId": "req-test"}}
        )
        session = _FakeSession(raw_client)
        config_calls: list[dict[str, object]] = []
        session_calls: list[dict[str, str]] = []

        def config_factory(**kwargs: object) -> object:
            config_calls.append(kwargs)
            return "explicit-config"

        def session_factory(**kwargs: str) -> _FakeSession:
            session_calls.append(kwargs)
            return session

        factory = Boto3S3ClientFactory(
            session_factory=session_factory,
            config_factory=config_factory,
        )
        client = factory.create(
            _settings(),
            SecretValue("test-access-key"),
            SecretValue("test-secret-key"),
        )
        body = io.BytesIO(b"payload")
        receipt = client.put_object(
            Bucket="media-bucket",
            Key="2026/10/12/generation/original.png",
            Body=body,
            ContentType="image/png",
            CacheControl="public, max-age=31536000, immutable",
        )

        self.assertEqual(
            session_calls,
            [
                {
                    "aws_access_key_id": "test-access-key",
                    "aws_secret_access_key": "test-secret-key",
                    "region_name": "auto",
                }
            ],
        )
        self.assertEqual(
            config_calls,
            [
                {
                    "signature_version": "s3v4",
                    "retries": {"mode": "standard", "total_max_attempts": 3},
                    "connect_timeout": 7,
                    "read_timeout": 19,
                    "proxies": {},
                }
            ],
        )
        self.assertEqual(session.client_calls[0][0], "s3")
        self.assertEqual(
            session.client_calls[0][1],
            {
                "endpoint_url": "https://account.r2.example.test",
                "config": "explicit-config",
            },
        )
        self.assertIsInstance(receipt, WriteReceipt)
        self.assertTrue(receipt.is_positive)
        self.assertEqual(receipt.key, "2026/10/12/generation/original.png")
        self.assertEqual(raw_client.calls[0]["Key"], receipt.key)
        client.close()
        self.assertEqual(raw_client.close_count, 1)

    def test_incomplete_metadata_and_wrong_bucket_never_make_positive_receipt(self) -> None:
        raw_client = _FakeRawClient(
            {"ResponseMetadata": {"HTTPStatusCode": 200, "RequestId": ""}}
        )
        session = _FakeSession(raw_client)
        factory = Boto3S3ClientFactory(
            session_factory=lambda **_kwargs: session,
            config_factory=lambda **_kwargs: object(),
        )
        client = factory.create(
            _settings(),
            SecretValue("dummy-access"),
            SecretValue("dummy-secret"),
        )
        with self.assertRaisesRegex(ValueError, "configured bucket"):
            client.put_object(
                Bucket="other-bucket",
                Key="key.png",
                Body=io.BytesIO(b"x"),
                ContentType="image/png",
                CacheControl="immutable",
            )
        self.assertEqual(raw_client.calls, [])
        receipt = client.put_object(
            Bucket="media-bucket",
            Key="same-key.png",
            Body=io.BytesIO(b"x"),
            ContentType="image/png",
            CacheControl="immutable",
        )
        self.assertFalse(receipt.is_positive)
        self.assertEqual(receipt.key, "")

    def test_actual_boto3_stub_maps_positive_response_and_preserves_key(self) -> None:
        captured: dict[str, Any] = {}

        class CapturingSession:
            def __init__(
                self,
                *,
                aws_access_key_id: str,
                aws_secret_access_key: str,
                region_name: str,
            ) -> None:
                captured["session"] = Session(
                    aws_access_key_id=aws_access_key_id,
                    aws_secret_access_key=aws_secret_access_key,
                    region_name=region_name,
                )

            def client(self, service: str, **kwargs: object):
                client_value = captured["session"].client(service, **kwargs)
                captured["client"] = client_value
                return client_value

        factory = Boto3S3ClientFactory(
            session_factory=CapturingSession,
            config_factory=Config,
        )
        client = factory.create(
            _settings(),
            SecretValue("test-access-key"),
            SecretValue("test-secret-key"),
        )
        raw_client = captured["client"]
        self.assertEqual(raw_client.meta.config.signature_version, "s3v4")
        self.assertEqual(raw_client.meta.config.retries["mode"], "standard")
        self.assertEqual(raw_client.meta.config.retries["total_max_attempts"], 3)
        key = "2026/10/12/generation/original.png"
        with Stubber(raw_client) as stubber:
            stubber.add_response(
                "put_object",
                {
                    "ETag": '"not-a-sha256"',
                    "ResponseMetadata": {
                        "HTTPStatusCode": 200,
                        "RequestId": "stub-request-id",
                    },
                },
                expected_params={
                    "Bucket": "media-bucket",
                    "Key": key,
                    "Body": ANY,
                    "ContentType": "image/png",
                    "CacheControl": "public, max-age=31536000, immutable",
                },
            )
            receipt = client.put_object(
                Bucket="media-bucket",
                Key=key,
                Body=io.BytesIO(b"png"),
                ContentType="image/png",
                CacheControl="public, max-age=31536000, immutable",
            )
        self.assertTrue(receipt.is_positive)
        self.assertEqual(receipt.key, key)
        self.assertEqual(receipt.request_id, "stub-request-id")
        client.close()

    def test_storage_config_is_built_with_fixed_retry_and_signing_contract(self) -> None:
        class _Secret:
            def __init__(self, value: str) -> None:
                self._value = value

            def reveal(self) -> SecretValue:
                return SecretValue(self._value)

        class _Client:
            def __init__(self) -> None:
                self.request = None

            def put_object(self, **kwargs: object) -> dict[str, object]:
                self.request = kwargs
                return {
                    "ResponseMetadata": {
                        "HTTPStatusCode": 200,
                        "RequestId": "storage-request",
                    }
                }

            def close(self) -> None:
                pass

        with TemporaryDirectory() as raw:
            directory = Path(raw)
            configuration = R2Configuration(
                endpoint_url="https://account.r2.example.test",
                bucket="media-bucket",
                public_base_url="https://media.example.test",
                transfer_tmp_dir=directory,
                business_type="generation",
                environment="production",
                connect_timeout_seconds=7,
                read_timeout_seconds=19,
            )
            client_value = _Client()
            session = _FakeSession(client_value)  # type: ignore[arg-type]
            config_values: list[dict[str, object]] = []
            factory = Boto3S3ClientFactory(
                session_factory=lambda **_kwargs: session,
                config_factory=lambda **kwargs: config_values.append(kwargs) or object(),
            )
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
            storage = R2Storage(
                configuration,
                SafeMediaPolicy((MediaTypeRule("image/png", ".png"),)),
                access_key=_Secret("access"),
                secret_key=_Secret("secret"),
                client_factory=factory,
                executor=executor,
            )
            try:
                result = storage.store_prepared(
                    PreparedReference(
                        kind="bytes",
                        locator=None,
                        content=b"png",
                        media_type="image/png",
                    )
                )
            finally:
                executor.shutdown(wait=True)
            self.assertEqual(config_values[0]["retries"], {"mode": "standard", "total_max_attempts": 3})
            self.assertEqual(config_values[0]["signature_version"], "s3v4")
            self.assertIsInstance(result, StoredMedia)
            self.assertTrue(result.locator.endswith(client_value.request["Key"]))


if __name__ == "__main__":
    unittest.main()
