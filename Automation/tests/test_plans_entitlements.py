import tempfile
import unittest
from pathlib import Path

from application.artifact_service import ArtifactApplicationService
from core.artifact_manager import ArtifactManager
from core.artifact_repository import FileArtifactRepository
from core.persistence import JsonStateRepository
from core.plans import DEFAULT_PLANS, Plan, PlanManager
from core.quota import QuotaEngine
from core.usage_engine import UsageEngine


class PlansEntitlementsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repository_path = self.root / "state.json"
        self.repository = JsonStateRepository(self.repository_path)
        self.plans = PlanManager(self.repository)

    def tearDown(self):
        self.temp.cleanup()

    def test_default_assignment_is_workspace_scoped_and_restart_safe(self):
        self.assertEqual("FREE", self.plans.current("workspace-a")["plan_id"])
        self.plans.assign("workspace-a", "PRO")
        self.assertEqual("PRO", self.plans.current("workspace-a")["plan_id"])
        self.assertEqual("FREE", self.plans.current("workspace-b")["plan_id"])
        restarted = PlanManager(JsonStateRepository(self.repository_path))
        self.assertEqual("PRO", restarted.current("workspace-a")["plan_id"])

    def test_invalid_and_inactive_plans_are_rejected(self):
        inactive = Plan("PAUSED", "Paused", "Unavailable", False, {})
        manager = PlanManager(
            self.repository,
            plans=DEFAULT_PLANS + (inactive,),
        )
        with self.assertRaisesRegex(ValueError, "plan_not_found"):
            manager.assign("workspace-a", "UNKNOWN")
        with self.assertRaisesRegex(ValueError, "plan_inactive"):
            manager.assign("workspace-a", "PAUSED")

    def test_workspace_quota_override_precedes_plan_defaults(self):
        usage = UsageEngine(self.repository)
        quota = QuotaEngine(
            self.repository, usage, policy_resolver=self.plans.quota_defaults
        )
        self.assertEqual(
            100000, quota.status("workspace-a")["policy"]["token_limit"]
        )
        self.plans.assign("workspace-a", "PRO")
        self.assertEqual(
            1000000, quota.status("workspace-a")["policy"]["token_limit"]
        )
        quota.set_policy("workspace-a", token_limit=7)
        self.assertEqual(7, quota.status("workspace-a")["policy"]["token_limit"])

    def test_artifact_archive_entitlement_is_enforced(self):
        storage = self.root / "storage"
        storage.mkdir()
        path = storage / "result.txt"
        path.write_text("result", encoding="utf-8")
        manager = ArtifactManager(
            FileArtifactRepository(self.root / "artifacts.json", storage)
        )
        artifact = manager.register_file(
            path, "TEXT", "Test", workspace_id="workspace-a"
        )
        denied_plans = PlanManager(
            self.repository,
            plans=(Plan("FREE", "Free", "Restricted", True, {
                "artifact_archive_enabled": False,
            }),),
        )
        service = ArtifactApplicationService(
            manager, entitlement_checker=denied_plans.allows
        )
        with self.assertRaisesRegex(ValueError, "entitlement_denied"):
            service.archive("workspace-a", artifact["artifact_id"])
        self.assertEqual(
            "AVAILABLE", manager.get(artifact["artifact_id"], "workspace-a")["status"]
        )

    def test_catalog_contains_only_active_safe_contracts(self):
        values = self.plans.list_plans()
        self.assertEqual(["FREE", "PRO", "BUSINESS"], [
            value["plan_id"] for value in values
        ])
        self.assertNotIn("price", repr(values).lower())
        self.assertNotIn("subscription", repr(values).lower())


if __name__ == "__main__":
    unittest.main()
