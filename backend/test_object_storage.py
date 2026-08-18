from __future__ import annotations

import unittest
from datetime import timedelta
from io import BytesIO
from unittest.mock import Mock

from object_storage import ObjectStorage


class ObjectStorageRedirectTests(unittest.TestCase):
    def test_browser_post_policy_is_same_origin_and_exact_size(self) -> None:
        storage = ObjectStorage.__new__(ObjectStorage)
        storage.client = Mock()
        storage.client.presigned_post_policy.return_value = {
            "policy": "signed-policy",
            "x-amz-signature": "signature",
        }

        result = storage.presigned_browser_post(
            "examify-sources",
            "exams/exam/revisions/session/source/source.pdf",
            content_type="application/pdf",
            minimum_size=123,
            maximum_size=123,
        )

        self.assertEqual(result["url"], "/client-uploads/examify-sources")
        self.assertEqual(result["fields"]["key"], "exams/exam/revisions/session/source/source.pdf")
        self.assertEqual(result["fields"]["Content-Type"], "application/pdf")
        self.assertEqual(result["expires_in_seconds"], 900)
        storage.client.presigned_post_policy.assert_called_once()

    def test_put_stream_reuses_seekable_upload_without_copy(self) -> None:
        storage = ObjectStorage.__new__(ObjectStorage)
        storage.client = Mock()
        source = BytesIO(b"pdf-bytes")
        source.seek(4)

        storage.put_stream(
            "examify-sources",
            "jobs/abc/input.pdf",
            source,
            length=9,
            content_type="application/pdf",
        )

        storage.client.put_object.assert_called_once_with(
            "examify-sources",
            "jobs/abc/input.pdf",
            source,
            length=9,
            content_type="application/pdf",
        )
        self.assertEqual(source.tell(), 0)

    def test_internal_redirect_preserves_minio_signature(self) -> None:
        storage = ObjectStorage.__new__(ObjectStorage)
        storage.client = Mock()
        storage.client.get_presigned_url.return_value = (
            "http://minio:9000/examify-assets/jobs/a/crop.webp"
            "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=abc123"
        )

        redirect = storage.presigned_internal_redirect(
            "examify-assets",
            "jobs/a/crop.webp",
            "/_protected_minio",
            method="GET",
            expires=timedelta(minutes=30),
        )

        self.assertEqual(
            redirect,
            "/_protected_minio/examify-assets/jobs/a/crop.webp"
            "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=abc123",
        )
        storage.client.get_presigned_url.assert_called_once_with(
            "GET",
            "examify-assets",
            "jobs/a/crop.webp",
            expires=timedelta(minutes=30),
        )

    def test_internal_redirect_rejects_unsigned_result(self) -> None:
        storage = ObjectStorage.__new__(ObjectStorage)
        storage.client = Mock()
        storage.client.get_presigned_url.return_value = (
            "http://minio:9000/examify-assets/jobs/a/crop.webp"
        )

        with self.assertRaisesRegex(RuntimeError, "signed internal URL"):
            storage.presigned_internal_redirect(
                "examify-assets", "jobs/a/crop.webp", "/_protected_minio"
            )


if __name__ == "__main__":
    unittest.main()
