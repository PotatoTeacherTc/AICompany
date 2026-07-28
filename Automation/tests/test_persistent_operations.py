import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.artifact_manager import ArtifactManager
from core.artifact_repository import FileArtifactRepository
from core.batch import BatchManager
from core.execution_history import ExecutionHistory
from core.execution_history_repository import JsonFileExecutionHistoryRepository
from core.mission import Mission
from core.persistence import (
    InMemoryStateRepository,
    JsonStateRepository,
    PersistenceService,
)
from core.retry_recovery import RetryState
from core.scheduler import FakeClock, InMemoryScheduler, Recurrence
from core.task_queue import JobStatus, PersistentJobQueue, InProcessJobWorker


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_in_memory_and_json_workspace_persistence_are_safe(self):
        for repository in (
            InMemoryStateRepository(),
            JsonStateRepository(self.root / "state.json"),
        ):
            saved = repository.save(
                "test", "one", "workspace-a",
                {
                    "value": 1,
                    "raw_prompt": "secret",
                    "api_key": "hidden",
                    "path": "C:\\private\\artifact.txt",
                },
            )
            self.assertNotIn("raw_prompt", saved["payload"])
            self.assertEqual(
                {"value": 1, "path": "[internal reference omitted]"},
                repository.get("test", "one", "workspace-a"),
            )
            self.assertIsNone(repository.get("test", "one", "workspace-b"))

    def test_mission_schedule_retry_history_and_restart(self):
        path = self.root / "state.json"
        repository = JsonStateRepository(path)
        service = PersistenceService(repository)
        mission = Mission.create(
            "safe title", "private objective", "user-a", "workspace-a"
        )
        service.save_mission(mission)
        state = RetryState(3, 1, True, None, "timeout", "RetryError: timeout")
        service.save_retry_state("retry-a", "workspace-a", state)
        service.save_history_record({
            "task_id": "task-a", "workspace_id": "workspace-a",
            "status": "SUCCESS", "task": "safe task",
        })
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        scheduler = InMemoryScheduler(FakeClock(now), repository)
        schedule = scheduler.schedule(
            "workspace-a", "target-a", now + timedelta(minutes=1),
            recurrence=Recurrence(60),
        )
        restarted = JsonStateRepository(path)
        restored_scheduler = InMemoryScheduler(
            FakeClock(now), restarted, workspace_ids=("workspace-a",)
        )
        self.assertEqual(schedule.schedule_id, restored_scheduler.list("workspace-a")[0].schedule_id)
        self.assertNotIn(
            "objective", restarted.get("mission", mission.id, "workspace-a")
        )
        self.assertEqual(
            "timeout", restarted.get("retry", "retry-a", "workspace-a")["failure_category"]
        )
        self.assertEqual(
            "SUCCESS", restarted.get("history", "task-a", "workspace-a")["status"]
        )
        self.assertNotIn("private objective", path.read_text(encoding="utf-8"))

    def test_corrupt_missing_and_old_schema_are_ignored(self):
        path = self.root / "state.json"
        path.write_text("{", encoding="utf-8")
        self.assertEqual([], JsonStateRepository(path).list("job", "workspace-a"))
        path.write_text(
            json.dumps({"schema_version": 0, "records": [
                {"kind": "job", "record_id": "bad", "workspace_id": "workspace-a", "payload": {}}
            ]}),
            encoding="utf-8",
        )
        self.assertEqual([], JsonStateRepository(path).list("job", "workspace-a"))

    def test_existing_history_repository_recovers_after_restart(self):
        path = self.root / "history.json"
        first = ExecutionHistory(
            repository=JsonFileExecutionHistoryRepository(path)
        )
        first.records = [{"task_id": "one", "workspace_id": "workspace-a", "status": "SUCCESS"}]
        first.save()
        second = ExecutionHistory(
            repository=JsonFileExecutionHistoryRepository(path)
        )
        self.assertEqual("one", second.records[0]["task_id"])


class PersistentArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.storage = self.root / "storage"
        self.storage.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_relative_metadata_restart_query_and_safe_delete(self):
        repository_file = self.root / "artifacts.json"
        path = self.storage / "workspace-a" / "result.txt"
        path.parent.mkdir()
        path.write_text("result", encoding="utf-8")
        manager = ArtifactManager(FileArtifactRepository(repository_file, self.storage))
        artifact = manager.register_file(
            path, "TEXT", "Test", workspace_id="workspace-a",
            mission_id="mission-a", stage="test",
        )
        restarted = ArtifactManager(
            FileArtifactRepository(repository_file, self.storage)
        )
        restored = restarted.find("workspace-a", "mission-a", artifact["artifact_id"])[0]
        self.assertNotIn("path", restored)
        self.assertEqual("workspace-a/result.txt", restored["internal_ref"])
        self.assertEqual([], restarted.find("workspace-b", "mission-a"))
        self.assertTrue(restarted.delete_metadata(artifact["artifact_id"], "workspace-a"))
        self.assertTrue(path.exists())

    def test_escape_missing_and_corrupt_metadata_are_safe(self):
        repository_file = self.root / "artifacts.json"
        outside = self.root / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        manager = ArtifactManager(FileArtifactRepository(repository_file, self.storage))
        with self.assertRaises(ValueError):
            manager.register_file(outside, "TEXT", "Test", workspace_id="workspace-a")
        repository_file.write_text(json.dumps([{
            "artifact_id": "bad", "workspace_id": "workspace-a",
            "internal_ref": "../outside.txt",
        }]), encoding="utf-8")
        self.assertEqual(
            [], ArtifactManager(FileArtifactRepository(repository_file, self.storage)).list("workspace-a")
        )
        missing_file = self.storage / "missing.txt"
        missing_file.write_text("x", encoding="utf-8")
        valid = ArtifactManager(FileArtifactRepository(repository_file, self.storage))
        artifact = valid.register_file(missing_file, "TEXT", "Test", workspace_id="workspace-a")
        missing_file.unlink()
        restarted = ArtifactManager(FileArtifactRepository(repository_file, self.storage))
        self.assertEqual("MISSING", restarted.get(artifact["artifact_id"], "workspace-a")["status"])


class QueueBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "operations.json"
        self.repository = JsonStateRepository(self.path)
        self.queue = PersistentJobQueue(self.repository)

    def tearDown(self):
        self.temp.cleanup()

    def test_queue_claim_completion_failure_retry_and_restart(self):
        first = self.queue.enqueue("workspace-a", "mission-a", "target-a", "key-a")
        self.assertEqual(first.job_id, self.queue.enqueue(
            "workspace-a", "mission-a", "target-a", "key-a"
        ).job_id)
        claimed = self.queue.claim("workspace-a", "worker-a")
        self.assertEqual(JobStatus.RUNNING, claimed.status)
        self.assertIsNone(self.queue.claim("workspace-a", "worker-b"))
        with self.assertRaises(ValueError):
            self.queue.complete(claimed.job_id, "workspace-a", "worker-b", {"status": "SUCCESS"})
        failed = self.queue.fail(
            claimed.job_id, "workspace-a", "worker-a",
            {"status": "FAILED", "error": "raw private provider message"},
            retry_state={"retryable": True, "current_attempt": 1},
        )
        self.assertEqual("JobError: ReportedFailure", failed.result["error"])
        self.queue.requeue(failed.job_id, "workspace-a")
        reclaimed = self.queue.claim("workspace-a", "worker-a")
        self.queue.complete(reclaimed.job_id, "workspace-a", "worker-a", {"status": "SUCCESS"})
        restarted = PersistentJobQueue(
            JsonStateRepository(self.path), workspace_ids=("workspace-a",)
        )
        self.assertEqual(JobStatus.COMPLETED, restarted.get(first.job_id, "workspace-a").status)
        self.assertIsNone(restarted.get(first.job_id, "workspace-b"))

    def test_running_job_recovers_to_pending(self):
        job = self.queue.enqueue("workspace-a", "mission-a", "target-a", "key")
        self.queue.claim("workspace-a", "worker-a")
        restarted = PersistentJobQueue(
            JsonStateRepository(self.path), workspace_ids=("workspace-a",)
        )
        self.assertEqual(JobStatus.PENDING, restarted.get(job.job_id, "workspace-a").status)

    def test_batch_success_partial_failure_and_retry(self):
        batches = BatchManager(self.queue, self.repository, max_items=3)
        batch = batches.create("workspace-a", [
            {"mission_id": "one", "target_id": "target"},
            {"mission_id": "two", "target_id": "target"},
        ], "batch-key")
        self.assertEqual(batch.batch_id, batches.create("workspace-a", [
            {"mission_id": "one", "target_id": "target"},
            {"mission_id": "two", "target_id": "target"},
        ], "batch-key").batch_id)
        first = self.queue.claim("workspace-a", "worker")
        self.queue.complete(first.job_id, "workspace-a", "worker", {"status": "SUCCESS"})
        second = self.queue.claim("workspace-a", "worker")
        self.queue.fail(
            second.job_id, "workspace-a", "worker",
            {"status": "FAILED", "error": "ProviderError: TimeoutError"},
            retry_state={"retryable": True},
        )
        summary = batches.summary(batch.batch_id, "workspace-a")
        self.assertEqual(JobStatus.FAILED, summary["status"])
        self.assertEqual(JobStatus.COMPLETED, summary["items"][0]["status"])
        self.queue.requeue(second.job_id, "workspace-a")
        retry = self.queue.claim("workspace-a", "worker")
        self.queue.complete(retry.job_id, "workspace-a", "worker", {"status": "SUCCESS"})
        self.assertEqual(JobStatus.COMPLETED, batches.summary(batch.batch_id, "workspace-a")["status"])
        self.assertIsNone(batches.summary(batch.batch_id, "workspace-b"))

    def test_batch_validates_limits(self):
        batches = BatchManager(self.queue, self.repository, max_items=1)
        with self.assertRaises(ValueError):
            batches.create("workspace-a", [], "empty")
        with self.assertRaises(ValueError):
            batches.create("workspace-a", [
                {"mission_id": "one", "target_id": "target"},
                {"mission_id": "two", "target_id": "target"},
            ], "large")

    def test_in_process_worker_runs_offline_target_with_missing_usage(self):
        worker = InProcessJobWorker(self.queue)
        worker.register_target(
            "personal-ai-company",
            lambda job: {"status": JobStatus.COMPLETED, "data": {}},
        )
        job = self.queue.enqueue(
            "workspace-a", "mission-a", "personal-ai-company", "personal-key"
        )
        completed = worker.run_once("workspace-a")
        self.assertEqual(job.job_id, completed.job_id)
        self.assertEqual(JobStatus.COMPLETED, completed.status)
        self.assertNotIn("usage", completed.result)


if __name__ == "__main__":
    unittest.main()
