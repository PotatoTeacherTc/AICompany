import tempfile
import unittest
from pathlib import Path

from application.artifact_service import ArtifactApplicationService
from application.production import create_artifact_manager
from core.persistence import InMemoryStateRepository


class ArtifactStorageIntegrationTests(unittest.TestCase):
    def test_store_read_restart_and_workspace_isolation(self):
        with tempfile.TemporaryDirectory() as root:
            states = InMemoryStateRepository()
            values = {"AICOMPANY_ARTIFACT_STORAGE": "local", "AICOMPANY_ARTIFACT_ROOT": root}
            manager = create_artifact_manager(values, states)
            source = Path(root) / "source.txt"; source.write_text("safe body", encoding="utf-8")
            artifact = manager.register_file(source, "TEXT", "Pipeline", workspace_id="ws-a")
            source.unlink()
            service = ArtifactApplicationService(manager)
            self.assertEqual("safe body", service.content("ws-a", artifact["artifact_id"])["content"])
            self.assertIsNone(service.get("ws-b", artifact["artifact_id"]))
            restarted = ArtifactApplicationService(create_artifact_manager(values, states))
            self.assertEqual("safe body", restarted.content("ws-a", artifact["artifact_id"])["content"])
            self.assertNotIn(str(Path(root).resolve()), repr(restarted.get("ws-a", artifact["artifact_id"])))

    def test_same_filename_isolated_and_missing_object_reported(self):
        with tempfile.TemporaryDirectory() as root:
            states = InMemoryStateRepository(); values = {"AICOMPANY_ARTIFACT_STORAGE": "local", "AICOMPANY_ARTIFACT_ROOT": root}
            manager = create_artifact_manager(values, states)
            source = Path(root) / "same.txt"; source.write_text("one", encoding="utf-8")
            one = manager.register_file(source, "TEXT", "P", workspace_id="one")
            source.write_text("two", encoding="utf-8")
            two = manager.register_file(source, "TEXT", "P", workspace_id="two")
            self.assertNotEqual(one["internal_ref"], two["internal_ref"])
            key_path = Path(root) / one["internal_ref"]
            key_path.unlink()
            self.assertEqual("MISSING", manager.get(one["artifact_id"], "one")["status"])
            self.assertEqual("AVAILABLE", manager.get(two["artifact_id"], "two")["status"])

    def test_storage_failure_does_not_create_metadata(self):
        class FailingStorage:
            def put(self, key, content): raise OSError("private storage failure")
            def exists(self, key): return False
            def get(self, key): return None
        from core.artifact_manager import ArtifactManager
        from core.artifact_repository import InMemoryArtifactRepository
        from core.object_storage import ArtifactStorageAdapter
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "value.txt"; source.write_text("value", encoding="utf-8")
            repository = InMemoryArtifactRepository()
            manager = ArtifactManager(repository, ArtifactStorageAdapter(FailingStorage(), repository))
            with self.assertRaises(OSError): manager.register_file(source, "TEXT", "P", workspace_id="ws")
            self.assertEqual([], repository.list("ws"))


if __name__ == "__main__": unittest.main()
