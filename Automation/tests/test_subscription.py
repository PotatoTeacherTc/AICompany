import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from application.subscription_service import SubscriptionApplicationService
from core.persistence import JsonStateRepository
from core.plans import PlanManager
from core.subscription import SubscriptionManager


class _Audit:
    def __init__(self):
        self.events = []

    def record(self, **value):
        self.events.append(value)


class SubscriptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "state.json"
        self.repository = JsonStateRepository(self.path)
        self.plans = PlanManager(self.repository)
        self.audit = _Audit()
        self.manager = SubscriptionManager(
            self.repository,
            self.plans,
            clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        self.service = SubscriptionApplicationService(self.manager, self.audit)

    def tearDown(self):
        self.temp.cleanup()

    def test_create_change_cancel_undo_and_restart(self):
        value = self.service.create("workspace-a", {"plan_id": "PRO"})
        self.assertEqual("ACTIVE", value["status"])
        self.assertEqual("PRO", self.plans.current("workspace-a")["plan_id"])
        self.service.schedule_cancel("workspace-a")
        self.assertTrue(self.manager.current("workspace-a")["cancel_at_period_end"])
        self.service.undo_cancel("workspace-a")
        self.service.change_plan("workspace-a", {"plan_id": "BUSINESS"})

        restarted = SubscriptionManager(
            JsonStateRepository(self.path),
            PlanManager(JsonStateRepository(self.path)),
        )
        self.assertEqual("BUSINESS", restarted.current("workspace-a")["plan_id"])
        self.assertFalse(restarted.current("workspace-a")["cancel_at_period_end"])
        self.assertEqual(4, len(self.audit.events))

    def test_terminal_status_falls_back_to_free_and_preserves_record(self):
        self.manager.create("workspace-a", "PRO")
        value = self.manager.transition("workspace-a", "EXPIRED")
        self.assertEqual("EXPIRED", value["status"])
        self.assertEqual("FREE", self.plans.current("workspace-a")["plan_id"])
        self.assertEqual("PRO", self.manager.current("workspace-a")["plan_id"])
        with self.assertRaisesRegex(ValueError, "invalid_subscription_transition"):
            self.manager.transition("workspace-a", "ACTIVE")

    def test_one_active_subscription_and_workspace_isolation(self):
        self.manager.create("workspace-a")
        with self.assertRaisesRegex(ValueError, "active_subscription_exists"):
            self.manager.create("workspace-a")
        self.assertIsNone(self.manager.current("workspace-b"))

    def test_invalid_plan_status_and_payload_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "plan_not_found"):
            self.manager.create("workspace-a", "UNKNOWN")
        with self.assertRaisesRegex(ValueError, "invalid_initial_status"):
            self.manager.create("workspace-a", status="CANCELLED")
        with self.assertRaisesRegex(ValueError, "invalid_subscription_request"):
            self.service.create("workspace-a", {"plan_id": "FREE", "secret": "x"})

    def test_sensitive_metadata_is_not_persisted(self):
        value = self.manager.create(
            "workspace-a",
            metadata={
                "source": "manual",
                "api_key": "hidden",
                "nested_prompt": "hidden",
            },
        )
        self.assertEqual({"source": "manual"}, value["metadata"])
        self.assertNotIn("hidden", self.path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
