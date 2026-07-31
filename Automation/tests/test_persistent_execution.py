import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from application.backend import BackendDependencies, create_backend_app
from application.persistent_execution_service import PersistentExecutionService
from core.artifact_manager import ArtifactManager
from core.artifact_repository import FileArtifactRepository
from core.execution_history import ExecutionHistory
from core.execution_history_repository import JsonFileExecutionHistoryRepository
from core.persistence import JsonStateRepository
from core.status import PipelineStatus
from core.task_queue import (
    InProcessJobWorker,
    JobStatus,
    PersistentJobQueue,
)
from core.usage_engine import UsageEngine


class PersistentExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.storage = self.root / "artifacts"
        self.storage.mkdir()
        self.state_path = self.root / "state.json"
        self.history_path = self.root / "history.json"
        self.artifact_path = self.root / "artifacts.json"
        self.service = self._service()

    def tearDown(self):
        self.temp.cleanup()

    def _service(self, workspace_ids=()):
        state = JsonStateRepository(self.state_path)
        queue = PersistentJobQueue(state, workspace_ids=workspace_ids)
        worker = InProcessJobWorker(queue)
        history = ExecutionHistory(
            repository=JsonFileExecutionHistoryRepository(self.history_path)
        )
        artifacts = ArtifactManager(
            FileArtifactRepository(self.artifact_path, self.storage)
        )
        usage = UsageEngine(state)
        return PersistentExecutionService(
            queue, worker, history, artifacts, usage
        )

    def _success(self, job, *, tokens=3):
        path = self.storage / job.workspace_id / f"{job.job_id}.txt"
        path.parent.mkdir(exist_ok=True)
        path.write_text("safe result", encoding="utf-8")
        artifact = self.service.artifact_manager.register_file(
            path,
            "TEXT",
            "Test Pipeline",
            workspace_id=job.workspace_id,
            mission_id=job.mission_id,
        )
        return {
            "status": PipelineStatus.SUCCESS,
            "pipeline": "Test Pipeline",
            "task_type": "CONTENT",
            "data": {
                "provider_usage": {
                    "provider": "fake",
                    "model": "offline",
                    "total_tokens": tokens,
                    "estimated_cost_usd": 0,
                }
            },
            "artifacts": [artifact],
            "error": None,
        }

    def test_restart_recovers_pending_job_and_runs_pipeline(self):
        job = self.service.submit(
            "workspace-a", "mission-a", "content", "request-a"
        )
        restarted = self._service(("workspace-a",))
        self.service = restarted
        restarted.register_target("content", self._success)
        completed = restarted.run_once("workspace-a")
        self.assertEqual(job.job_id, completed.job_id)
        self.assertEqual(JobStatus.COMPLETED, completed.status)

    def test_duplicate_submission_is_idempotent_within_workspace(self):
        first = self.service.submit(
            "workspace-a", "mission-a", "content", "same-key"
        )
        second = self.service.submit(
            "workspace-a", "mission-a", "content", "same-key"
        )
        other = self.service.submit(
            "workspace-b", "mission-b", "content", "same-key"
        )
        self.assertEqual(first.job_id, second.job_id)
        self.assertNotEqual(first.job_id, other.job_id)

    def test_concurrent_workers_claim_job_only_once(self):
        job = self.service.submit(
            "workspace-a", "mission-a", "content", "claim-key"
        )
        with ThreadPoolExecutor(max_workers=8) as pool:
            claims = list(pool.map(
                lambda index: self.service.queue.claim(
                    "workspace-a", f"worker-{index}"
                ),
                range(8),
            ))
        claimed = [value for value in claims if value is not None]
        self.assertEqual([job.job_id], [value.job_id for value in claimed])

    def test_execution_connects_history_artifact_and_usage(self):
        self.service.register_target("content", self._success)
        job = self.service.submit(
            "workspace-a", "mission-a", "content", "integration-key"
        )
        completed = self.service.run_once("workspace-a")
        self.assertEqual(JobStatus.COMPLETED, completed.status)

        history = self.service.execution_history.query(
            workspace_id="workspace-a"
        )
        self.assertEqual(job.job_id, history[0]["task_id"])
        self.assertEqual("SUCCESS", history[0]["status"])
        self.assertNotIn("path", repr(history[0]))

        artifacts = self.service.artifact_manager.find(
            "workspace-a", "mission-a"
        )
        self.assertEqual(1, len(artifacts))
        self.assertEqual(
            artifacts[0]["artifact_id"],
            history[0]["result"]["artifacts"][0]["artifact_id"],
        )

        usage = self.service.usage_engine.query("workspace-a")
        self.assertEqual(3, usage[0]["total_tokens"])
        self.assertEqual(0, usage[0]["estimated_cost_usd"])
        self.assertEqual([], self.service.usage_engine.query("workspace-b"))

    def test_retry_preserves_failure_then_recovers(self):
        attempts = {"count": 0}

        def target(job):
            attempts["count"] += 1
            if attempts["count"] == 1:
                return {
                    "status": PipelineStatus.FAILED,
                    "pipeline": "Test Pipeline",
                    "task_type": "CONTENT",
                    "data": {
                        "retry": {
                            "retryable": True,
                            "current_attempt": 1,
                        }
                    },
                    "artifacts": [],
                    "error": "ProviderError: TimeoutError",
                }
            return self._success(job, tokens=5)

        self.service.register_target("content", target)
        job = self.service.submit(
            "workspace-a", "mission-a", "content", "retry-key"
        )
        failed = self.service.run_once("workspace-a")
        self.assertEqual(JobStatus.FAILED, failed.status)
        self.assertTrue(failed.retry_state["retryable"])
        self.service.queue.requeue(job.job_id, "workspace-a")
        completed = self.service.run_once("workspace-a")
        self.assertEqual(JobStatus.COMPLETED, completed.status)
        records = self.service.execution_history.query(
            workspace_id="workspace-a"
        )
        self.assertEqual(1, len(records))
        self.assertEqual(PipelineStatus.SUCCESS, records[0]["status"])

    def test_running_job_recovers_after_process_restart(self):
        job = self.service.submit(
            "workspace-a", "mission-a", "content", "running-key"
        )
        self.service.queue.claim("workspace-a", "interrupted-worker")
        restarted = self._service(("workspace-a",))
        recovered = restarted.queue.get(job.job_id, "workspace-a")
        self.assertEqual(JobStatus.PENDING, recovered.status)
        self.assertIsNone(recovered.claimed_by)

    def test_invalid_or_foreign_artifact_fails_safely(self):
        def target(_job):
            return {
                "status": PipelineStatus.SUCCESS,
                "pipeline": "Test Pipeline",
                "task_type": "CONTENT",
                "data": {},
                "artifacts": [{"artifact_id": "foreign"}],
                "error": None,
            }

        self.service.register_target("content", target)
        self.service.submit(
            "workspace-a", "mission-a", "content", "invalid-artifact"
        )
        failed = self.service.run_once("workspace-a")
        self.assertEqual(JobStatus.FAILED, failed.status)
        self.assertEqual("JobError: ValueError", failed.result["error"])

    def test_backend_composition_accepts_service_without_job_api(self):
        app = create_backend_app(BackendDependencies(
            persistent_execution_service=self.service
        ))
        self.assertIs(self.service, app.state.persistent_execution_service)
        paths = {route.path for route in app.routes}
        self.assertFalse(any(path.startswith("/jobs") for path in paths))


if __name__ == "__main__":
    unittest.main()
