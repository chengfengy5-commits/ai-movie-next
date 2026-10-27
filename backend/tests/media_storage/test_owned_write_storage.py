from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import io
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any

from haoai_backend.generation_contracts import ReferenceInput, TransferFailure
from haoai_backend.media_storage.owned_write_values import (
    OwnedObjectMaterial,
    OwnedObjectPlan,
    OwnedObjectPutResult,
    OwnedObjectVerification,
)
from haoai_backend.media_storage.r2_storage import R2Storage
from haoai_backend.media_storage.remote_staging import RemoteStaging
from haoai_backend.media_storage.remote_transfer import RemoteMediaTransfer
from haoai_backend.media_storage.transfer_types import (
    MediaTypeRule,
    R2Configuration,
    SafeMediaPolicy,
    S3ObjectRead,
    SecretValue,
    WriteReceipt,
)


class _Secret:
    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> SecretValue:
        return SecretValue(self._value)


class _Permit:
    def __init__(self, plan: OwnedObjectPlan, *, allowed: bool = True) -> None:
        self.plan = plan
        self.allowed = allowed
        self.used = False
        self.claim_count = 0

    def claim(self, plan: OwnedObjectPlan) -> bool:
        self.claim_count += 1
        if self.used or not self.allowed or plan != self.plan:
            return False
        self.used = True
        return True


class _ReadBody(io.BytesIO):
    def __init__(self, payload: bytes, *, close_error: BaseException | None = None) -> None:
        super().__init__(payload)
        self.close_error = close_error

    def close(self) -> None:
        if self.close_error is not None:
            raise self.close_error
        super().close()


class _UnreadableBody:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _S3Error(OSError):
    def __init__(self, code: str, status: int, request_id: str = "read-error") -> None:
        super().__init__(code)
        self.response = {
            "Error": {"Code": code},
            "ResponseMetadata": {"HTTPStatusCode": status, "RequestId": request_id},
        }


class _PlannedClient:
    def __init__(self, factory: _Factory) -> None:
        self.factory = factory
        self.closed = False

    def put_object(self, **kwargs: Any) -> WriteReceipt:
        raise AssertionError("the planned path must use the conditional method")

    def put_object_if_absent(self, **kwargs: Any) -> WriteReceipt:
        self.factory.put_calls += 1
        self.factory.put_settings.append(self.factory.last_settings)
        self.factory.put_request = {
            "Bucket": kwargs["Bucket"],
            "Key": kwargs["Key"],
            "ContentType": kwargs["ContentType"],
            "CacheControl": kwargs["CacheControl"],
            "IfNoneMatch": "*",
        }
        content = kwargs["Body"].read()
        if self.factory.store_on_put:
            self.factory.objects[(kwargs["Bucket"], kwargs["Key"])] = content
        return self.factory.put_receipt or WriteReceipt(
            kwargs["Key"], 200, "put-request"
        )

    def get_object_stream(self, *, Bucket: str, Key: str) -> S3ObjectRead:
        self.factory.get_calls += 1
        self.factory.get_requests.append((Bucket, Key))
        if self.factory.get_error is not None:
            raise self.factory.get_error
        payload = self.factory.objects.get((Bucket, Key), self.factory.read_payload)
        body = self.factory.read_body or _ReadBody(
            payload,
            close_error=self.factory.body_close_error,
        )
        return S3ObjectRead(
            status_code=self.factory.read_status,
            content_length=(
                self.factory.read_content_length
                if self.factory.read_content_length is not None
                else len(payload)
            ),
            content_type=(
                self.factory.read_content_type
                if self.factory.read_content_type is not None
                else self.factory.expected_media_type
            ),
            content_range=self.factory.read_content_range,
            body=body,  # type: ignore[arg-type]
            etag='"opaque-etag"',
            request_id="get-request",
        )

    def close(self) -> None:
        self.closed = True


class _Factory:
    def __init__(self) -> None:
        self.put_calls = 0
        self.get_calls = 0
        self.store_on_put = True
        self.put_receipt: WriteReceipt | None = None
        self.put_request: dict[str, object] | None = None
        self.put_settings: list[object] = []
        self.last_settings: object | None = None
        self.get_requests: list[tuple[str, str]] = []
        self.objects: dict[tuple[str, str], bytes] = {}
        self.read_payload = b""
        self.read_status = 200
        self.read_content_length: int | None = None
        self.read_content_type: str | None = None
        self.expected_media_type = "image/png"
        self.read_content_range: str | None = None
        self.body_close_error: BaseException | None = None
        self.read_body: object | None = None
        self.get_error: BaseException | None = None
        self.created_clients: list[_PlannedClient] = []

    def create(self, settings, access_key, secret_key):
        if access_key.value != "access" or secret_key.value != "secret":
            raise AssertionError("explicit root secrets were not used")
        self.last_settings = settings
        client = _PlannedClient(self)
        self.created_clients.append(client)
        return client


def _configuration(
    directory: Path,
    *,
    endpoint: str = "https://account.r2.example.test",
) -> R2Configuration:
    return R2Configuration(
        endpoint_url=endpoint,
        bucket="media-bucket",
        public_base_url="https://media.example.test",
        transfer_tmp_dir=directory,
        business_type="generation",
        environment="production",
        connect_timeout_seconds=7,
        read_timeout_seconds=19,
    )


def _storage(
    directory: Path,
    factory: _Factory,
    executor: concurrent.futures.Executor,
    *,
    endpoint: str = "https://account.r2.example.test",
) -> R2Storage:
    return R2Storage(
        _configuration(directory, endpoint=endpoint),
        SafeMediaPolicy((MediaTypeRule("image/png", ".png"),)),
        access_key=_Secret("access"),
        secret_key=_Secret("secret"),
        client_factory=factory,
        executor=executor,
    )


class OwnedWriteStorageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

    def tearDown(self) -> None:
        self.executor.shutdown(wait=True)
        self.temp.cleanup()

    def _storage(
        self,
        factory: _Factory,
        *,
        endpoint: str = "https://account.r2.example.test",
    ) -> R2Storage:
        return _storage(self.directory, factory, self.executor, endpoint=endpoint)

    def _material(
        self,
        storage: R2Storage,
        content: bytes = b"actual-media-bytes",
    ) -> OwnedObjectMaterial:
        result = storage.plan_bytes(
            content,
            "image/png",
            owner_ref="owner-42",
            submission_ref="submission-77",
            slot="primary",
            provider_result_digest=hashlib.sha256(b"provider URL result").hexdigest(),
        )
        self.assertIsInstance(result, OwnedObjectMaterial)
        return result

    def test_plan_is_no_put_and_a_fresh_permit_allows_one_conditional_put(self) -> None:
        factory = _Factory()
        storage = self._storage(factory)
        material = self._material(storage)
        permit = _Permit(material.plan)

        self.assertEqual(factory.put_calls, 0)
        self.assertNotEqual(
            material.plan.provider_result_digest,
            material.plan.material_sha256,
        )
        outcome = storage.put_planned(material, permit)

        self.assertEqual(outcome, OwnedObjectPutResult("created", request_id="put-request"))
        self.assertEqual(factory.put_calls, 1)
        self.assertEqual(factory.put_request["IfNoneMatch"], "*")
        self.assertEqual(factory.put_request["Key"], material.plan.object_key)
        self.assertEqual(factory.put_settings[0].operation_policy, "owned_once")
        self.assertEqual(factory.put_settings[0].total_max_attempts, 1)
        self.assertIsInstance(storage.put_planned(material, permit), TransferFailure)
        self.assertEqual(factory.put_calls, 1)
        self.assertEqual(permit.claim_count, 2)

    def test_put_ack_unknown_can_only_be_reconciled_by_getting_the_original_key(self) -> None:
        factory = _Factory()
        factory.put_receipt = WriteReceipt("", 0, "")
        storage = self._storage(factory)
        material = self._material(storage)

        outcome = storage.put_planned(material, _Permit(material.plan))
        verified = storage.verify_planned(material.plan)

        self.assertIsInstance(outcome, OwnedObjectPutResult)
        self.assertEqual(outcome.status, "unknown")
        self.assertEqual(factory.put_calls, 1)
        self.assertEqual(factory.get_requests, [(material.plan.bucket, material.plan.object_key)])
        self.assertEqual(verified.status, "verified")
        self.assertEqual(
            verified.stored_media.content_digest,
            hashlib.sha256(material.content).hexdigest(),
        )

    def test_late_object_is_verified_at_the_original_key_without_a_second_put(self) -> None:
        factory = _Factory()
        factory.store_on_put = False
        factory.put_receipt = WriteReceipt("", 0, "")
        factory.read_status = 404
        storage = self._storage(factory)
        material = self._material(storage)

        outcome = storage.put_planned(material, _Permit(material.plan))
        first_check = storage.verify_planned(material.plan)
        factory.objects[(material.plan.bucket, material.plan.object_key)] = material.content
        factory.read_status = 200
        second_check = storage.verify_planned(material.plan)

        self.assertEqual(outcome.status, "unknown")
        self.assertEqual(first_check.status, "not_found_pending")
        self.assertEqual(second_check.status, "verified")
        self.assertEqual(factory.put_calls, 1)
        self.assertEqual(
            factory.get_requests,
            [
                (material.plan.bucket, material.plan.object_key),
                (material.plan.bucket, material.plan.object_key),
            ],
        )

    def test_only_explicit_412_is_already_exists_and_409_stays_unknown(self) -> None:
        for status, expected in ((412, "already_exists"), (409, "unknown")):
            with self.subTest(status=status):
                factory = _Factory()
                factory.put_receipt = WriteReceipt("", status, f"request-{status}")
                storage = self._storage(factory)
                material = self._material(storage)

                outcome = storage.put_planned(material, _Permit(material.plan))

                self.assertEqual(outcome.status, expected)
                self.assertEqual(factory.put_calls, 1)

    def test_verification_rejects_partial_truncated_extra_wrong_mime_and_wrong_digest(
        self,
    ) -> None:
        cases = (
            {"read_status": 206, "read_content_range": "bytes 0-2/3"},
            {"read_content_length": 1},
            {"read_payload": b"actual-media-bytes-extra"},
            {"read_content_type": "image/jpeg"},
            {"read_content_type": "IMAGE/PNG"},
            {"read_payload": b"different-media-byte"},
        )
        for options in cases:
            with self.subTest(options=options):
                factory = _Factory()
                storage = self._storage(factory)
                material = self._material(storage)
                factory.objects[(material.plan.bucket, material.plan.object_key)] = (
                    options.get("read_payload", material.content)
                )
                for key, value in options.items():
                    setattr(factory, key, value)

                verification = storage.verify_planned(material.plan)

                self.assertIn(verification.status, {"content_conflict", "unavailable"})
                self.assertIsNone(verification.stored_media)

    def test_404_is_pending_but_403_and_body_cleanup_failure_are_unavailable(self) -> None:
        for error, expected in (
            (_S3Error("NoSuchKey", 404), "not_found_pending"),
            (_S3Error("OpaqueResponseCode", 404), "not_found_pending"),
            (_S3Error("AccessDenied", 403), "unavailable"),
        ):
            with self.subTest(error=error):
                factory = _Factory()
                factory.get_error = error
                storage = self._storage(factory)
                material = self._material(storage)

                self.assertEqual(storage.verify_planned(material.plan).status, expected)

        factory = _Factory()
        factory.body_close_error = OSError("close failed")
        storage = self._storage(factory)
        material = self._material(storage)
        factory.objects[(material.plan.bucket, material.plan.object_key)] = material.content

        self.assertEqual(storage.verify_planned(material.plan).status, "unavailable")
        self.assertEqual(storage.pending_resource_cleanup_count, 1)

    def test_unreadable_body_is_closed_and_cannot_be_verified(self) -> None:
        factory = _Factory()
        body = _UnreadableBody()
        factory.read_body = body
        storage = self._storage(factory)
        material = self._material(storage)
        factory.objects[(material.plan.bucket, material.plan.object_key)] = material.content

        verification = storage.verify_planned(material.plan)

        self.assertEqual(verification.status, "unavailable")
        self.assertIsNone(verification.stored_media)
        self.assertTrue(body.closed)
        self.assertEqual(storage.pending_resource_cleanup_count, 0)

    def test_config_fingerprint_mismatch_stops_before_client_creation(self) -> None:
        factory = _Factory()
        original_storage = self._storage(factory)
        material = self._material(original_storage)
        changed_storage = self._storage(
            factory,
            endpoint="https://other-account.r2.example.test",
        )

        verification = changed_storage.verify_planned(material.plan)
        put_result = changed_storage.put_planned(material, _Permit(material.plan))

        self.assertEqual(verification.status, "unavailable")
        self.assertIsInstance(put_result, TransferFailure)
        self.assertEqual(factory.created_clients, [])

    def test_tampered_bucket_key_public_base_and_fingerprint_never_create_a_client(
        self,
    ) -> None:
        factory = _Factory()
        storage = self._storage(factory)
        material = self._material(storage)
        changed_plans = (
            replace(material.plan, bucket="other-media-bucket"),
            replace(
                material.plan,
                object_key=(
                    "2026/10/28/generation/"
                    "ffffffffffffffffffffffffffffffff.png"
                ),
            ),
            replace(material.plan, public_base="https://other.example.test"),
            replace(
                material.plan,
                config_fingerprint=hashlib.sha256(b"tampered").hexdigest(),
            ),
        )

        for index, plan in enumerate(changed_plans):
            with self.subTest(plan_number=index + 1):
                tampered_material = OwnedObjectMaterial(plan, material.content)
                self.assertEqual(storage.verify_planned(plan).status, "unavailable")
                self.assertIsInstance(
                    storage.put_planned(tampered_material, _Permit(plan)),
                    TransferFailure,
                )

        self.assertEqual(factory.created_clients, [])

    async def test_remote_prepare_returns_actual_bytes_and_releases_spool_without_put(
        self,
    ) -> None:
        factory = _Factory()
        storage = self._storage(factory)
        staging = RemoteStaging(self.directory)

        class _Fetcher:
            async def fetch_to_owned(self, url, *, max_bytes, timeout_seconds):
                self.asserted_url = url
                download = staging.create(max_bytes)
                download.write_chunk(b"fetched-png", max_bytes=max_bytes)
                download.seal("image/png")
                return download

        fetcher = _Fetcher()
        slots = asyncio.Semaphore(1)
        transfer = RemoteMediaTransfer(
            fetcher,
            staging,
            storage,
            shared_slots=slots,
        )
        material = await transfer.prepare_owned(
            ReferenceInput(
                kind="public_http",
                locator="https://provider.example.test/output.png",
            ),
            owner_ref="owner-42",
            submission_ref="submission-77",
            slot="primary",
            provider_result_digest=hashlib.sha256(b"provider URL result").hexdigest(),
            max_bytes=1024,
            timeout_seconds=2,
        )

        self.assertIsInstance(material, OwnedObjectMaterial)
        self.assertEqual(material.content, b"fetched-png")
        self.assertEqual(material.plan.material_sha256, hashlib.sha256(b"fetched-png").hexdigest())
        self.assertEqual(factory.put_calls, 0)
        self.assertEqual(staging.pending_cleanup_count, 0)
        self.assertEqual(slots._value, 1)


if __name__ == "__main__":
    unittest.main()
