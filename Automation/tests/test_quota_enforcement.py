import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from application.persistent_execution_service import PersistentExecutionService
from core.artifact_manager import ArtifactManager
from core.execution_history import ExecutionHistory
from core.persistence import JsonStateRepository
from core.quota import QuotaEngine
from core.task_queue import InProcessJobWorker, PersistentJobQueue
from core.usage_engine import UsageEngine


class QuotaEnforcementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "state.json"
        self.repository = JsonStateRepository(self.path)
        self.usage = UsageEngine(self.repository)
        self.quota = QuotaEngine(self.repository, self.usage)

    def tearDown(self):
        self.temp.cleanup()

    def test_token_cost_limits_and_missing_usage_are_safe(self):
        self.quota.set_policy("workspace-a", token_limit=10, cost_limit=2)
        self.assertTrue(self.quota.status("workspace-a")["allowed"])
        self.usage.record("workspace-a", "run-1", None)
        self.assertTrue(self.quota.status("workspace-a")["allowed"])
        self.usage.record(
            "workspace-a", "run-2",
            {"total_tokens": 10, "estimated_cost_usd": 1},
        )
        status = self.quota.status("workspace-a")
        self.assertFalse(status["allowed"])
        self.assertEqual("quota_tokens_exceeded", status["safe_error"])

        self.quota.set_policy("workspace-b", cost_limit=1)
        self.usage.record(
            "workspace-b", "run-3", {"estimated_cost_usd": 1}
        )
        self.assertEqual(
            "quota_cost_exceeded",
            self.quota.status("workspace-b")["safe_error"],
        )

    def test_execution_reservation_is_idempotent_isolated_and_restart_safe(self):
        self.quota.set_policy("workspace-a", execution_limit=1)
        first = self.quota.reserve("workspace-a", "request-1")
        self.assertEqual(first, self.quota.reserve("workspace-a", "request-1"))
        with self.assertRaisesRegex(ValueError, "quota_executions_exceeded"):
            self.quota.reserve("workspace-a", "request-2")
        self.assertTrue(self.quota.status("workspace-b")["allowed"])

        restarted_repository = JsonStateRepository(self.path)
        restarted = QuotaEngine(
            restarted_repository, UsageEngine(restarted_repository)
        )
        self.assertEqual(1, restarted.status("workspace-a")["execution_count"])

    def test_persistent_submission_enforces_quota_without_changing_queue(self):
        self.quota.set_policy("workspace-a", execution_limit=1)
        queue = PersistentJobQueue(self.repository)
        service = PersistentExecutionService(
            queue,
            InProcessJobWorker(queue),
            ExecutionHistory(),
            ArtifactManager(),
            self.usage,
            quota_engine=self.quota,
        )
        first = service.submit(
            "workspace-a", "mission-a", "content", "request-1"
        )
        self.assertEqual(
            first.job_id,
            service.submit(
                "workspace-a", "mission-a", "content", "request-1"
            ).job_id,
        )
        with self.assertRaisesRegex(ValueError, "quota_executions_exceeded"):
            service.submit("workspace-a", "mission-b", "content", "request-2")
        self.assertEqual(1, len(queue.list("workspace-a")))

    def test_policy_validation_and_disabled_policy(self):
        with self.assertRaises(ValueError):
            self.quota.set_policy("workspace-a", token_limit=-1)
        policy = self.quota.set_policy(
            "workspace-a", execution_limit=0, enabled=False
        )
        self.assertFalse(policy["enabled"])
        self.quota.reserve("workspace-a", "allowed-while-disabled")
        self.assertTrue(self.quota.status("workspace-a")["allowed"])


if __name__ == "__main__":
    unittest.main()
