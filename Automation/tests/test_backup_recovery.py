import json
import unittest

from core.artifact_repository import InMemoryArtifactRepository
from core.backup import BackupService, InMemoryBackupStore
from core.persistence import InMemoryStateRepository
from core.workspace_repository import InMemoryWorkspaceRepository


def workspace(workspace_id):
    return {
        "workspace_id": workspace_id,
        "name": f"Workspace {workspace_id}",
        "created_at": "2026-01-01T00:00:00+00:00",
        "status": "ACTIVE",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "revision": 0,
    }


class BackupRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.workspaces = InMemoryWorkspaceRepository([
            workspace("ws-a"), workspace("ws-b"),
        ])
        self.artifacts = InMemoryArtifactRepository([{
            "artifact_id": "artifact-a",
            "workspace_id": "ws-a",
            "filename": "result.txt",
            "status": "AVAILABLE",
            "internal_ref": "ws-a/result.txt",
            "path": r"C:\private\result.txt",
        }, {
            "artifact_id": "artifact-b",
            "workspace_id": "ws-b",
            "filename": "other.txt",
        }])
        self.states = InMemoryStateRepository()
        self.states.save("subscription", "ws-a", "ws-a", {
            "subscription_id": "sub-a",
            "workspace_id": "ws-a",
            "plan_id": "FREE",
            "status": "ACTIVE",
            "metadata": {"prompt": "private", "mode": "FAKE"},
        })
        self.states.save("billing_account", "ws-a", "ws-a", {
            "billing_account_id": "account-a",
            "workspace_id": "ws-a",
            "billing_email": "private@example.test",
            "currency": "USD",
            "status": "ACTIVE",
        })
        self.states.save("invoice", "invoice-a", "ws-a", {
            "invoice_id": "invoice-a",
            "workspace_id": "ws-a",
            "status": "OPEN",
            "total": 0,
        })

    def service(self, store=None):
        return BackupService(
            self.workspaces, self.artifacts, self.states, store=store
        )

    def test_json_export_is_workspace_scoped_and_sanitized(self):
        result = self.service().export("ws-a")
        value = json.loads(result["payload"])
        self.assertEqual("ws-a", value["workspace_id"])
        self.assertEqual(["artifact-a"], [
            item["artifact_id"] for item in value["artifacts"]
        ])
        text = result["payload"].lower()
        self.assertNotIn("private@example.test", text)
        self.assertNotIn("prompt", text)
        self.assertNotIn(":\\\\", result["payload"])

    def test_restore_rehydrates_workspace_artifact_and_billing_metadata(self):
        exported = self.service().export("ws-a")
        target_workspaces = InMemoryWorkspaceRepository()
        target_artifacts = InMemoryArtifactRepository()
        target_states = InMemoryStateRepository()
        target = BackupService(
            target_workspaces, target_artifacts, target_states
        )
        result = target.restore("ws-a", exported["payload"])
        self.assertEqual("RESTORED", result["status"])
        self.assertIsNotNone(target_workspaces.get("ws-a"))
        self.assertEqual(
            "ws-a", target_artifacts.get("artifact-a")["workspace_id"]
        )
        self.assertEqual(
            "FREE",
            target_states.get("subscription", "ws-a", "ws-a")["plan_id"],
        )
        self.assertEqual(
            "OPEN",
            target_states.get("invoice", "invoice-a", "ws-a")["status"],
        )

    def test_fake_store_enforces_workspace_boundary(self):
        store = InMemoryBackupStore()
        service = self.service(store)
        exported = service.export("ws-a")
        with self.assertRaisesRegex(KeyError, "backup_not_found"):
            service.restore_saved("ws-b", exported["backup_id"], overwrite=True)

    def test_corrupt_cross_workspace_and_unsafe_path_restore_are_rejected(self):
        exported = json.loads(self.service().export("ws-a")["payload"])
        exported["workspace_id"] = "ws-b"
        with self.assertRaisesRegex(ValueError, "invalid_backup"):
            self.service().restore(
                "ws-a", json.dumps(exported), overwrite=True
            )
        exported["workspace_id"] = "ws-a"
        exported["artifacts"][0]["workspace_id"] = "ws-b"
        with self.assertRaisesRegex(ValueError, "invalid_artifact_backup"):
            self.service().restore(
                "ws-a", json.dumps(exported), overwrite=True
            )

    def test_restore_requires_explicit_overwrite(self):
        payload = self.service().export("ws-a")["payload"]
        with self.assertRaisesRegex(ValueError, "workspace_exists"):
            self.service().restore("ws-a", payload)
        result = self.service().restore("ws-a", payload, overwrite=True)
        self.assertEqual("RESTORED", result["status"])


if __name__ == "__main__":
    unittest.main()
