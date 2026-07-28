import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from application.personal_ai_company import PersonalAICompany
from core.artifact_manager import ArtifactManager
from core.collaboration_orchestrator import CollaborationOrchestrator
from core.collaboration_worker import FunctionWorker
from core.content_orchestrator import ContentOrchestrator
from core.execution_history import ExecutionHistory
from core.media_pipeline import ImagePipeline, VideoPipeline
from core.music_pipeline import MusicPipeline
from core.retry_recovery import RetryExecutor, RetryPolicy
from core.scheduler import FakeClock, InMemoryScheduler, Recurrence
from core.status import PipelineStatus
from core.task import Task
from core.worker_result import WorkerResult
from providers.content_media import FakeImageProvider, FakeYouTubeProvider


class MemoryHistoryRepository:
    def __init__(self):
        self.records = []

    def load(self):
        return list(self.records)

    def save(self, records):
        self.records = list(records)


class FailOnceImageProvider(FakeImageProvider):
    def __init__(self):
        self.calls = 0

    def generate_image(self, request):
        self.calls += 1
        if self.calls == 1:
            raise TimeoutError("raw provider detail")
        return super().generate_image(request)


class AlwaysFailImageProvider(FakeImageProvider):
    def generate_image(self, request):
        raise TimeoutError("raw provider detail")


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.clock = FakeClock(self.now)
        self.scheduler = InMemoryScheduler(self.clock)

    def test_one_time_fake_clock_disable_and_duplicate_prevention(self):
        calls = []
        self.scheduler.register_target("target-a", lambda schedule: calls.append(schedule.schedule_id))
        item = self.scheduler.schedule(
            "workspace-a", "target-a", self.now + timedelta(seconds=10)
        )
        self.assertEqual([], self.scheduler.run_due("workspace-a"))
        self.clock.advance(10)
        self.assertEqual("SUCCESS", self.scheduler.run_due("workspace-a")[0]["status"])
        self.assertEqual([], self.scheduler.run_due("workspace-a"))
        self.assertFalse(self.scheduler.get(item.schedule_id, "workspace-a").enabled)
        self.assertEqual(1, len(calls))

    def test_recurring_timezone_workspace_and_enabled_contract(self):
        self.scheduler.register_target("target-a", lambda _: "ok")
        item = self.scheduler.schedule(
            "workspace-a",
            "target-a",
            self.now + timedelta(seconds=5),
            recurrence=Recurrence(5),
            enabled=False,
            metadata={"prompt": "secret", "kind": "content"},
        )
        self.assertIsNotNone(datetime.fromisoformat(item.run_at).tzinfo)
        self.assertNotIn("prompt", item.metadata)
        self.assertEqual([], self.scheduler.list("workspace-b"))
        self.clock.advance(5)
        self.assertEqual([], self.scheduler.run_due("workspace-a"))
        self.scheduler.set_enabled(item.schedule_id, True, "workspace-a")
        self.assertEqual(1, len(self.scheduler.run_due("workspace-a")))
        self.clock.advance(5)
        self.assertEqual(1, len(self.scheduler.run_due("workspace-a")))

    def test_invalid_time_scope_and_missing_target_are_safe(self):
        with self.assertRaises(ValueError):
            self.scheduler.schedule("workspace-a", "target", self.now)
        with self.assertRaises(ValueError):
            self.scheduler.schedule("../escape", "target", self.now + timedelta(seconds=1))
        item = self.scheduler.schedule(
            "workspace-a", "missing", self.now + timedelta(seconds=1)
        )
        self.clock.advance(1)
        result = self.scheduler.run_due("workspace-a")[0]
        self.assertEqual("ScheduleError: TargetUnavailable", result["error"])
        self.assertFalse(self.scheduler.get(item.schedule_id, "workspace-a").enabled)


class RetryRecoveryTests(unittest.TestCase):
    def test_retry_categories_limits_and_safe_errors(self):
        retry = RetryExecutor(RetryPolicy(max_attempts=2))
        calls = []

        def timeout_then_success():
            calls.append(1)
            if len(calls) == 1:
                return {"status": "FAILED", "error": "ProviderError: TimeoutError", "data": {}}
            return {"status": "SUCCESS", "error": None, "data": {}}

        result, state = retry.execute(timeout_then_success)
        self.assertEqual("SUCCESS", result["status"])
        self.assertEqual(2, state.current_attempt)
        for error, category in (
            ("ValidationError: InvalidInput", "validation"),
            ("PolicyError: PaidProvider", "cost_policy"),
            ("WorkspaceError: Mismatch", "workspace"),
            ("AuthError: CredentialMissing", "authentication"),
        ):
            value, state = retry.execute(
                lambda error=error: {"status": "FAILED", "error": error, "data": {}}
            )
            self.assertFalse(state.retryable)
            self.assertEqual(category, state.failure_category)
            self.assertNotIn(error, state.last_safe_error)

        attempts = []
        _, exhausted = retry.execute(
            lambda: (
                attempts.append(1)
                or {"status": "FAILED", "error": "ProviderError: ConnectionError", "data": {}}
            )
        )
        self.assertEqual(2, len(attempts))
        self.assertFalse(exhausted.retryable)

    def test_content_recovery_reuses_artifacts_and_rejects_foreign_workspace(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = ArtifactManager()
            provider = FailOnceImageProvider()
            orchestrator = ContentOrchestrator(
                MusicPipeline(root / "music", artifact_manager=manager),
                ImagePipeline(root / "image", provider=provider, artifact_manager=manager),
                VideoPipeline(root / "video", artifact_manager=manager),
                youtube_provider=FakeYouTubeProvider(),
            )
            task = Task(
                "private prompt", {"mission_id": "mission-a"}, workspace_id="workspace-a"
            )
            task.task_type = "CONTENT"
            first = orchestrator.run(task)
            self.assertEqual("FAILED", first["status"])
            recovered = orchestrator.run(task, recovery=first)
            self.assertEqual("SUCCESS", recovered["status"])
            music_ids = [
                artifact["artifact_id"]
                for artifact in first["artifacts"]
                if artifact["producer_pipeline"] == "Music Pipeline"
            ]
            self.assertTrue(set(music_ids).issubset(
                {artifact["artifact_id"] for artifact in recovered["artifacts"]}
            ))
            foreign = dict(first)
            foreign["data"] = dict(first["data"], workspace_id="workspace-b")
            with self.assertRaises(ValueError):
                orchestrator.run(task, recovery=foreign)


class PersonalAICompanyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        repository = MemoryHistoryRepository()
        self.history = ExecutionHistory(repository=repository)
        self.clock = FakeClock(datetime(2026, 1, 1, tzinfo=timezone.utc))

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def worker(fail=False):
        def handler(context):
            return WorkerResult.create(
                PipelineStatus.FAILED if fail else PipelineStatus.SUCCESS,
                "local-worker",
                context,
                usage=None,
                error="WorkerError: TestFailure" if fail else None,
            )
        return FunctionWorker("local-worker", handler)

    def company(self, image_provider=None, fail_worker=False):
        manager = ArtifactManager()
        content = ContentOrchestrator(
            MusicPipeline(self.root / "music", artifact_manager=manager, execution_history=self.history),
            ImagePipeline(
                self.root / "image",
                provider=image_provider or FakeImageProvider(),
                artifact_manager=manager,
                execution_history=self.history,
            ),
            VideoPipeline(self.root / "video", artifact_manager=manager, execution_history=self.history),
            youtube_provider=FakeYouTubeProvider(),
            execution_history=self.history,
        )
        collaboration = CollaborationOrchestrator(
            [self.worker(fail_worker)], execution_history=self.history
        )
        return PersonalAICompany(
            collaboration,
            content,
            InMemoryScheduler(self.clock),
            RetryExecutor(RetryPolicy(max_attempts=2)),
        )

    def test_immediate_offline_flow_retry_recovery_and_safe_result(self):
        provider = FailOnceImageProvider()
        result = self.company(provider).execute(
            "private prompt with token", "workspace-a"
        ).to_dict()
        self.assertEqual("SUCCESS", result["content"]["status"])
        self.assertEqual(2, result["retry"]["current_attempt"])
        self.assertEqual(2, provider.calls)
        serialized = repr(result)
        self.assertNotIn("private prompt", serialized)
        self.assertNotIn(str(self.root), serialized)
        self.assertGreater(len(self.history.records), 0)

    def test_scheduled_flow_and_worker_failure(self):
        company = self.company()
        scheduled = company.schedule(
            "scheduled private request",
            "workspace-a",
            self.clock.now() + timedelta(seconds=5),
        )
        self.clock.advance(5)
        result = company.scheduler.run_due("workspace-a")[0]
        self.assertEqual("SUCCESS", result["status"])
        self.assertEqual(
            "SUCCESS", result["result"]["content"]["status"]
        )
        failed = self.company(fail_worker=True).execute(
            "private failed request", "workspace-a"
        ).to_dict()
        self.assertEqual("FAILED", failed["content"]["status"])
        self.assertFalse(failed["retry"]["retryable"])

    def test_content_failure_exhausts_retry_safely(self):
        result = self.company(AlwaysFailImageProvider()).execute(
            "never expose this", "workspace-a"
        ).to_dict()
        self.assertEqual("FAILED", result["content"]["status"])
        self.assertEqual(2, result["retry"]["current_attempt"])
        self.assertFalse(result["retry"]["retryable"])
        self.assertNotIn("never expose this", repr(result))


if __name__ == "__main__":
    unittest.main()
