from __future__ import annotations

import hashlib
import unittest
from dataclasses import replace

from haoai_backend.generation_contracts import StoredMedia
from haoai_backend.media_storage.owned_write_values import (
    OwnedObjectMaterial,
    OwnedObjectPlan,
    OwnedObjectVerification,
)


def _plan(content: bytes = b"material") -> OwnedObjectPlan:
    return OwnedObjectPlan(
        write_id="0123456789abcdef0123456789abcdef",
        owner_ref="owner-123",
        submission_ref="submission-456",
        slot="primary",
        provider_result_digest=hashlib.sha256(b"provider result").hexdigest(),
        material_sha256=hashlib.sha256(content).hexdigest(),
        media_type="image/png",
        size_bytes=len(content),
        config_fingerprint=hashlib.sha256(b"trusted config").hexdigest(),
        config_version=1,
        bucket="media-bucket",
        object_key="2026/10/28/generation/0123456789abcdef0123456789abcdef.png",
        public_base="https://media.example.test",
    )


class OwnedWriteValueTests(unittest.TestCase):
    def test_plan_keeps_provider_and_actual_material_digests_separate(self) -> None:
        plan = _plan()

        self.assertNotEqual(plan.provider_result_digest, plan.material_sha256)
        self.assertEqual(plan.size_bytes, len(b"material"))

    def test_material_rejects_bytes_that_do_not_match_persisted_facts(self) -> None:
        plan = _plan()

        with self.assertRaisesRegex(ValueError, "size does not match"):
            OwnedObjectMaterial(plan, b"different-size")

        with self.assertRaisesRegex(ValueError, "digest does not match"):
            OwnedObjectMaterial(plan, b"matereal")

    def test_material_repr_does_not_include_media_bytes(self) -> None:
        secret_bytes = b"private-image-bytes"
        material = OwnedObjectMaterial(_plan(secret_bytes), secret_bytes)

        self.assertNotIn("private-image-bytes", repr(material))

    def test_plan_repr_hides_identity_digests_and_object_location(self) -> None:
        plan = _plan()

        rendered = repr(plan)

        for value in (
            plan.write_id,
            plan.owner_ref,
            plan.submission_ref,
            plan.provider_result_digest,
            plan.material_sha256,
            plan.config_fingerprint,
            plan.bucket,
            plan.object_key,
            plan.public_base,
        ):
            self.assertNotIn(value, rendered)

    def test_plan_rejects_malformed_typed_fields_as_values(self) -> None:
        plan = _plan()

        with self.assertRaisesRegex(ValueError, "write id is invalid"):
            replace(plan, write_id=42)
        with self.assertRaisesRegex(ValueError, "material digest"):
            replace(plan, material_sha256=None)
        with self.assertRaisesRegex(ValueError, "config fingerprint"):
            replace(plan, config_fingerprint=None)

    def test_verified_outcome_requires_the_actual_read_receipt(self) -> None:
        media = StoredMedia(
            locator="https://media.example.test/object.png",
            content_digest=hashlib.sha256(b"material").hexdigest(),
            media_type="image/png",
            size_bytes=len(b"material"),
        )

        verified = OwnedObjectVerification(
            status="verified",
            stored_media=media,
            observed_sha256=media.content_digest,
            observed_size_bytes=media.size_bytes,
        )
        self.assertEqual(verified.stored_media, media)
        with self.assertRaisesRegex(ValueError, "requires an actual"):
            OwnedObjectVerification(status="verified")
        with self.assertRaisesRegex(ValueError, "cannot expose stored media"):
            OwnedObjectVerification(status="unavailable", stored_media=media)


if __name__ == "__main__":
    unittest.main()
