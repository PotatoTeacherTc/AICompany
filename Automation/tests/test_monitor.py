import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.artifact_manager import ArtifactManager
from core.artifact_repository import FileArtifactRepository, InMemoryArtifactRepository
from core.batch import BatchManager
from core.execution_history import ExecutionHistory
from core.execution_history_repository import InMemoryExecutionHistoryRepository
from core.mission import Mission
from core.monitor import MonitorStatus, WorkspaceMonitor
from core.persistence import InMemoryStateRepository, JsonStateRepository, PersistenceService
from core.scheduler import FakeClock, InMemoryScheduler
from core.task_queue import JobStatus, PersistentJobQueue


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state_file = self.root / "state.json"
        self.state = JsonStateRepository(self.state_file)
        self.queue = PersistentJobQueue(self.state)
        self.now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.scheduler = InMemoryScheduler(FakeClock(self.now), self.state)
        self.artifacts = ArtifactManager(InMemoryArtifactRepository())
        self.history = ExecutionHistory(
            repository=InMemoryExecutionHistoryRepository()
        )
        self.monitor = WorkspaceMonitor(
            self.state, self.queue, self.scheduler, self.artifacts, self.history
        )

    def tearDown(self):
        self.temp.cleanup()

    def add_mission(self, workspace="workspace-a"):
        mission = Mission.create(
            "Safe mission", "private objective", "user-a", workspace
        )
        PersistenceService(self.state).save_mission(mission)
        return mission

    def test_workspace_summary_and_mission_status(self):
        mission = self.add_mission()
        summary = self.monitor.workspace_summary("workspace-a")
        self.assertTrue(summary["ok"])
        self.assertEqual(MonitorStatus.HEALTHY, summary["status"])
        self.assertEqual(1, summary["counts"]["missions"])
        snapshot = summary["entities"]["missions"][0]
        self.assertEqual(mission.id, snapshot["entity_id"])
        self.assertEqual("PENDING", snapshot["status"])
        self.assertNotIn("private objective", repr(summary))

    def test_queue_counts_retry_waiting_and_workspace_isolation(self):
        self.queue.enqueue("workspace-a", "mission-a", "target", "one")
        running = self.queue.enqueue("workspace-a", "mission-b", "target", "two")
        self.queue.claim("workspace-a", "worker")
        retry_job = self.queue.enqueue(
            "workspace-a", "mission-c", "target", "three",
            retry_state={"retryable": True, "current_attempt": 1, "next_retry_at": self.now.isoformat()},
        )
        self.queue.enqueue("workspace-b", "mission-x", "target", "foreign")
        jobs = self.monitor.jobs("workspace-a")
        self.assertEqual(1, jobs["counts"][JobStatus.RUNNING])
        self.assertEqual(2, jobs["counts"][JobStatus.PENDING])
        retry = next(item for item in jobs["items"] if item["entity_id"] == retry_job.job_id)
        self.assertEqual(MonitorStatus.RETRY_WAITING, retry["status"])
        self.assertNotIn("mission-x", repr(jobs))
        self.assertNotEqual(running.job_id, retry_job.job_id)
        failed = self.queue.claim("workspace-a", "worker")
        self.queue.fail(
            failed.job_id, "workspace-a", "worker",
            {"status": "FAILED", "error": "raw provider secret detail"},
        )
        monitored = self.monitor.jobs("workspace-a")
        failed_snapshot = next(
            item for item in monitored["items"] if item["entity_id"] == failed.job_id
        )
        self.assertEqual(
            "JobError: ReportedFailure", failed_snapshot["safe_error"]
        )
        self.assertNotIn("raw provider secret detail", repr(monitored))

    def test_schedule_enabled_disabled_and_read_does_not_mutate(self):
        enabled = self.scheduler.schedule(
            "workspace-a", "target-a", self.now + timedelta(minutes=1)
        )
        disabled = self.scheduler.schedule(
            "workspace-a", "target-b", self.now + timedelta(minutes=2),
            enabled=False, metadata={"token_value": "secret", "kind": "safe"},
        )
        before = self.state_file.read_bytes()
        values = self.monitor.schedules("workspace-a")
        after = self.state_file.read_bytes()
        statuses = {item["entity_id"]: item["status"] for item in values}
        self.assertEqual("ENABLED", statuses[enabled.schedule_id])
        self.assertEqual("DISABLED", statuses[disabled.schedule_id])
        self.assertNotIn("secret", repr(values))
        self.assertEqual(before, after)

    def test_batch_partial_failure_progress(self):
        batches = BatchManager(self.queue, self.state)
        batch = batches.create("workspace-a", [
            {"mission_id": "one", "target_id": "target"},
            {"mission_id": "two", "target_id": "target"},
        ], "batch")
        first = self.queue.claim("workspace-a", "worker")
        self.queue.complete(first.job_id, "workspace-a", "worker", {"status": "SUCCESS"})
        second = self.queue.claim("workspace-a", "worker")
        self.queue.fail(
            second.job_id, "workspace-a", "worker",
            {"status": "FAILED", "error": "ProviderError: TimeoutError"},
            retry_state={"retryable": True, "current_attempt": 1},
        )
        snapshot = self.monitor.batches("workspace-a")[0]
        self.assertEqual(batch.batch_id, snapshot["entity_id"])
        self.assertEqual(MonitorStatus.PARTIAL_FAILURE, snapshot["status"])
        self.assertEqual(0.5, snapshot["summary"]["progress"])

    def test_artifact_available_missing_and_absolute_path_hidden(self):
        storage = self.root / "storage"
        storage.mkdir()
        repository_file = self.root / "artifacts.json"
        path = storage / "workspace-a" / "artifact.txt"
        path.parent.mkdir()
        path.write_text("safe", encoding="utf-8")
        artifacts = ArtifactManager(
            FileArtifactRepository(repository_file, storage)
        )
        artifact = artifacts.register_file(
            path, "TEXT", "Test", workspace_id="workspace-a",
            mission_id="mission-a", stage="pipeline",
        )
        monitor = WorkspaceMonitor(
            self.state, self.queue, self.scheduler, artifacts, self.history
        )
        available = monitor.artifacts("workspace-a")[0]
        self.assertEqual("AVAILABLE", available["status"])
        self.assertNotIn(str(storage), repr(available))
        path.unlink()
        restored = ArtifactManager(FileArtifactRepository(repository_file, storage))
        missing_monitor = WorkspaceMonitor(
            self.state, self.queue, self.scheduler, restored, self.history
        )
        missing = missing_monitor.artifacts("workspace-a")[0]
        self.assertEqual("MISSING", missing["status"])
        self.assertEqual(artifact["artifact_id"], missing["entity_id"])

    def test_history_usage_complete_partial_and_missing(self):
        self.history.records = [
            self.history_record("one", {
                "provider": "fake", "model": "model-a", "input_tokens": 2,
                "output_tokens": 3, "total_tokens": 5, "estimated_cost_usd": 0.0,
            }),
            self.history_record("two", {"provider": "fake", "input_tokens": 4}),
            self.history_record("three", None),
        ]
        values = self.monitor.history("workspace-a", limit=10)
        by_id = {item["entity_id"]: item for item in values}
        self.assertEqual(5, by_id["one"]["usage"]["total_tokens"])
        self.assertEqual({"provider": "fake", "input_tokens": 4}, by_id["two"]["usage"])
        self.assertIsNone(by_id["three"]["usage"])
        aggregate = self.monitor.workspace_summary("workspace-a")["usage"]
        self.assertEqual(6, aggregate["input_tokens"])
        self.assertNotIn("output_tokens", by_id["two"]["usage"])

    def test_recent_history_safe_error_and_sensitive_data_removed(self):
        record = self.history_record("one", None)
        record["task"] = "private prompt"
        record["result"] = {
            "error": "raw provider secret detail",
            "data": {"prompt": "private prompt", "oauth_token": "token"},
        }
        self.history.records = [record]
        value = self.monitor.history("workspace-a", limit=1)[0]
        serialized = repr(value)
        self.assertNotIn("private prompt", serialized)
        self.assertNotIn("token", serialized)
        self.assertEqual("MonitorError: ReportedFailure", value["safe_error"])

    def test_restart_corrupt_data_and_cross_workspace_entity_rejection(self):
        mission = self.add_mission()
        restarted_state = JsonStateRepository(self.state_file)
        restarted = WorkspaceMonitor(
            restarted_state,
            PersistentJobQueue(restarted_state, workspace_ids=("workspace-a",)),
            InMemoryScheduler(FakeClock(self.now), restarted_state, workspace_ids=("workspace-a",)),
            self.artifacts,
            self.history,
        )
        self.assertEqual(1, len(restarted.missions("workspace-a")))
        denied = restarted.entity("mission", mission.id, "workspace-b")
        self.assertEqual("MonitorError: EntityNotFound", denied["error"])
        corrupt = self.root / "corrupt.json"
        corrupt.write_text("{", encoding="utf-8")
        safe = WorkspaceMonitor(
            JsonStateRepository(corrupt),
            PersistentJobQueue(JsonStateRepository(corrupt)),
            InMemoryScheduler(FakeClock(self.now)),
            self.artifacts,
            self.history,
        ).workspace_summary("workspace-a")
        self.assertTrue(safe["ok"])
        self.assertEqual(0, safe["counts"]["missions"])

    def test_invalid_filters_and_repository_failure_are_safe(self):
        self.assertFalse(self.monitor.workspace_summary("workspace-a", history_limit=-1)["ok"])
        invalid = self.monitor.history(
            "workspace-a",
            start_at="2026-01-02T00:00:00+00:00",
            end_at="2026-01-01T00:00:00+00:00",
        )
        self.assertEqual("MonitorError: ValueError", invalid["error"])

        class FailingRepository:
            def list(self, *_):
                raise OSError("private path")

            def get(self, *_):
                raise OSError("private path")

        failing = WorkspaceMonitor(
            FailingRepository(), self.queue, self.scheduler,
            self.artifacts, self.history,
        ).workspace_summary("workspace-a")
        self.assertEqual("MonitorError: OSError", failing["error"])
        self.assertNotIn("private path", repr(failing))

    @staticmethod
    def history_record(task_id, usage):
        return {
            "task_id": task_id,
            "mission_id": f"mission-{task_id}",
            "workspace_id": "workspace-a",
            "status": "SUCCESS",
            "completed_at": f"2026-01-01T00:00:0{1 if task_id == 'one' else 2}+00:00",
            "pipeline": "Fake Pipeline",
            "task_type": "CONTENT",
            "result": {"usage": usage},
        }


if __name__ == "__main__":
    unittest.main()
