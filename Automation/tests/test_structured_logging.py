import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent.manager import Manager
from core.base_pipeline import BasePipeline
from core.persistence import InMemoryStateRepository
from core.registry import PipelineRegistry
from core.result import PipelineResult
from core.retry_recovery import RetryExecutor, RetryPolicy
from core.status import PipelineStatus
from core.structured_logging import (
    InMemoryLogger,
    LocalFileLogger,
    LogLevel,
)
from core.task import Task
from core.task_queue import PersistentJobQueue
from providers.factory import ProviderFactory


class FixedClock:
    def __init__(self):
        self.current = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self):
        value = self.current
        self.current += timedelta(seconds=1)
        return value


class LoggingPipeline(BasePipeline):
    def __init__(self, status=PipelineStatus.SUCCESS, usage=None, raises=False):
        super().__init__("Logging Pipeline")
        self.status = status
        self.usage = usage
        self.raises = raises

    def run(self, task):
        if self.raises:
            raise RuntimeError("raw provider secret")
        return PipelineResult(
            status=self.status,
            pipeline=self.name,
            task=task,
            task_type="FILE",
            data={} if self.usage is None else {"provider_usage": self.usage},
            error=None if self.status == PipelineStatus.SUCCESS else "ProviderError: TimeoutError",
        ).to_dict()


class PaidProvider:
    is_paid = True


class StructuredLoggingTests(unittest.TestCase):
    def setUp(self):
        self.clock = FixedClock()
        self.logger = InMemoryLogger(clock=self.clock)

    def test_structured_record_and_recursive_redaction(self):
        self.assertTrue(self.logger.emit(
            "PIPELINE_COMPLETED",
            "Pipeline",
            workspace_id="workspace-a",
            mission_id="mission-a",
            execution_id="execution-a",
            status="SUCCESS",
            safe_message=r"Completed C:\private\result.txt Authorization: Bearer-secret",
            usage={
                "provider": "fake", "model": "offline",
                "input_tokens": 2, "output_tokens": 3, "total_tokens": 5,
                "estimated_cost_usd": 0.0,
            },
            metadata={
                "nested": {
                    "prompt": "private",
                    "oauth_token": "token",
                    "authorization": "Bearer secret",
                    "cookie": "private",
                    "safe": "value",
                },
                "path": r"C:\private\artifact.txt",
            },
        ))
        event = self.logger.query("workspace-a")[0]
        self.assertEqual("INFO", event["level"])
        self.assertEqual(5, event["usage"]["total_tokens"])
        serialized = repr(event)
        for value in ("private", "Bearer secret", "Bearer-secret", r"C:\private"):
            self.assertNotIn(value, serialized)
        self.assertEqual("value", event["metadata"]["nested"]["safe"])

    def test_level_workspace_component_time_and_recent_filters(self):
        self.logger.emit(
            "DEBUG_EVENT", "one", level=LogLevel.DEBUG,
            workspace_id="workspace-a",
        )
        self.logger.emit("ONE", "one", workspace_id="workspace-a")
        boundary = self.clock.current.isoformat()
        self.logger.emit(
            "TWO", "two", level=LogLevel.ERROR, workspace_id="workspace-a",
        )
        self.logger.emit("FOREIGN", "one", workspace_id="workspace-b")
        self.assertEqual(2, len(self.logger.query("workspace-a")))
        self.assertEqual(1, len(self.logger.query("workspace-a", component="two")))
        self.assertEqual(1, len(self.logger.query("workspace-a", level="ERROR")))
        self.assertEqual(1, len(self.logger.query("workspace-a", start_at=boundary)))
        self.assertEqual("TWO", self.logger.query("workspace-a", limit=1)[0]["event_type"])
        self.assertEqual([], self.logger.query("workspace-b", component="two"))

    def test_minimum_level(self):
        logger = InMemoryLogger(minimum_level=LogLevel.WARNING)
        self.assertFalse(logger.emit("LOW", "test", workspace_id="workspace-a"))
        self.assertTrue(logger.emit(
            "HIGH", "test", level=LogLevel.ERROR, workspace_id="workspace-a"
        ))

    def test_local_file_restart_and_corrupt_line(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "events.jsonl"
            first = LocalFileLogger(path, clock=self.clock)
            first.emit("SAVED", "file", workspace_id="workspace-a")
            with path.open("a", encoding="utf-8") as stream:
                stream.write("{broken\n")
                unsafe = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "level": "ERROR",
                    "event_type": "UNSAFE",
                    "component": "file",
                    "workspace_id": "workspace-a",
                    "safe_error": "raw provider private detail",
                    "metadata": {"authorization": "Bearer secret"},
                }
                stream.write(json.dumps(unsafe) + "\n")
            second = LocalFileLogger(path)
            values = second.query("workspace-a")
            self.assertEqual("SAVED", values[0]["event_type"])
            self.assertEqual(
                "LoggingError: ReportedFailure", values[1]["safe_error"]
            )
            self.assertNotIn("Bearer secret", repr(values))

    def test_usage_partial_and_missing(self):
        self.logger.emit(
            "PARTIAL", "test", workspace_id="workspace-a",
            usage={"provider": "fake", "input_tokens": 2},
        )
        self.logger.emit("MISSING", "test", workspace_id="workspace-a")
        values = self.logger.query("workspace-a")
        self.assertEqual(
            {"provider": "fake", "input_tokens": 2}, values[0]["usage"]
        )
        self.assertIsNone(values[1]["usage"])

    def test_pipeline_success_failure_and_logger_failure_isolation(self):
        success = self.run_pipeline(LoggingPipeline(
            usage={"provider": "fake", "input_tokens": 1}
        ), self.logger)
        self.assertEqual(PipelineStatus.SUCCESS, success["status"])
        self.assertEqual(
            ["PIPELINE_STARTED", "PIPELINE_COMPLETED"],
            [item["event_type"] for item in self.logger.query("default")],
        )
        failure_logger = InMemoryLogger(fail_writes=True)
        failed = self.run_pipeline(LoggingPipeline(raises=True), failure_logger)
        self.assertEqual(PipelineStatus.FAILED, failed["status"])
        self.assertEqual([], failure_logger.events)

    def test_pipeline_failure_log_hides_raw_provider_error(self):
        result = self.run_pipeline(LoggingPipeline(raises=True), self.logger)
        self.assertEqual(PipelineStatus.FAILED, result["status"])
        event = self.logger.query("default")[-1]
        self.assertEqual("PIPELINE_FAILED", event["event_type"])
        self.assertEqual("LoggingError: RuntimeError", event["safe_error"])
        self.assertNotIn("raw provider secret", repr(event))

    def test_queue_and_retry_events(self):
        queue = PersistentJobQueue(InMemoryStateRepository(), logger=self.logger)
        job = queue.enqueue("workspace-a", "mission-a", "target", "key")
        queue.claim("workspace-a", "worker")
        queue.fail(
            job.job_id, "workspace-a", "worker",
            {"status": "FAILED", "error": "ProviderError: TimeoutError"},
            retry_state={"retryable": True},
        )
        attempts = {"count": 0}

        def operation():
            attempts["count"] += 1
            if attempts["count"] == 1:
                return {"status": "FAILED", "error": "ProviderError: TimeoutError"}
            return {"status": "SUCCESS", "error": None}

        result, _ = RetryExecutor(
            RetryPolicy(max_attempts=2), logger=self.logger
        ).execute(operation, workspace_id="workspace-a", mission_id="mission-a")
        self.assertEqual("SUCCESS", result["status"])
        events = [item["event_type"] for item in self.logger.query("workspace-a")]
        for expected in (
            "QUEUE_ENQUEUED", "QUEUE_CLAIMED", "QUEUE_FAILED",
            "RETRY_ATTEMPT", "RETRY_SUCCEEDED",
        ):
            self.assertIn(expected, events)

    def test_paid_provider_block_is_logged_without_call(self):
        with self.assertRaises(ValueError):
            ProviderFactory.ensure_provider_allowed(
                PaidProvider(),
                environment={"ALLOW_PAID_PROVIDER": "false"},
                logger=self.logger,
                workspace_id="workspace-a",
            )
        event = self.logger.query("workspace-a")[0]
        self.assertEqual("PROVIDER_BLOCKED", event["event_type"])
        self.assertEqual("ProviderError: CostPolicy", event["safe_error"])

    def test_invalid_query_and_raw_error_are_safe(self):
        self.logger.emit(
            "FAILED", "test", level=LogLevel.ERROR,
            workspace_id="workspace-a", error="provider response and private detail",
        )
        event = self.logger.query("workspace-a")[0]
        self.assertEqual("LoggingError: ReportedFailure", event["safe_error"])
        self.assertNotIn("private detail", repr(event))
        self.assertEqual([], self.logger.query(
            "workspace-a",
            start_at="2026-01-02T00:00:00+00:00",
            end_at="2026-01-01T00:00:00+00:00",
        ))

    @staticmethod
    def run_pipeline(pipeline, logger):
        registry = PipelineRegistry()
        registry.register("FILE", pipeline)
        task = Task("private user input")
        task.task_type = "FILE"
        return Manager(registry, logger=logger).handle(task)


if __name__ == "__main__":
    unittest.main()
