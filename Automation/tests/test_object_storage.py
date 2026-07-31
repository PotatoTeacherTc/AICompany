from datetime import datetime, timezone
import tempfile
import unittest

from core.artifact_repository import InMemoryArtifactRepository
from core.object_storage import (
    ArtifactStorageAdapter,
    FakeS3StorageProvider,
    SignedUrlService,
    StorageFactory,
)


class ObjectStorageTests(unittest.TestCase):
    def test_local_storage_round_trip_and_path_escape_rejection(self):
        with tempfile.TemporaryDirectory() as root:
            storage = StorageFactory.create("local", root=root)
            reference = storage.put("ws/artifact/result.txt", "hello")
            self.assertEqual(b"hello", storage.get("ws/artifact/result.txt"))
            self.assertEqual(
                "local-storage://ws/artifact/result.txt", reference
            )
            with self.assertRaisesRegex(ValueError, "storage_path_escape"):
                storage.put("../private.txt", b"private")

    def test_fake_s3_is_offline_and_bucket_scoped(self):
        storage = StorageFactory.create("fake_s3", bucket="test-bucket")
        value = storage.put("ws-a/a.txt", b"value")
        self.assertEqual("s3-fake://test-bucket/ws-a/a.txt", value)
        self.assertTrue(storage.exists("ws-a/a.txt"))

    def test_artifact_adapter_persists_safe_metadata_and_workspace_isolation(self):
        storage = FakeS3StorageProvider()
        repository = InMemoryArtifactRepository()
        adapter = ArtifactStorageAdapter(storage, repository)
        value = adapter.store(
            "ws-a", "artifact-a", "result.txt", "content",
            artifact_type="TEXT", mime_type="text/plain",
        )
        self.assertEqual("ws-a/artifact-a/result.txt", value["internal_ref"])
        self.assertEqual(b"content", adapter.read("ws-a", "artifact-a"))
        self.assertIsNone(adapter.read("ws-b", "artifact-a"))
        self.assertNotIn(":\\", repr(value))

    def test_signed_url_contract_is_bounded_and_contains_no_absolute_path(self):
        clock = lambda: datetime(2026, 1, 1, tzinfo=timezone.utc)
        service = SignedUrlService(b"offline-test-key-material", clock)
        value = service.create("ws-a", "ws-a/artifact/result.txt", 60)
        self.assertTrue(value["url"].startswith("storage://"))
        self.assertIn("signature=", value["url"])
        self.assertNotIn(":\\", value["url"])
        with self.assertRaisesRegex(ValueError, "invalid_expiration"):
            service.create("ws-a", "key", 0)

    def test_storage_factory_rejects_external_or_unknown_provider(self):
        with self.assertRaisesRegex(ValueError, "unsupported_storage_provider"):
            StorageFactory.create("aws")


if __name__ == "__main__":
    unittest.main()
