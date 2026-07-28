import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.persistence import InMemoryStateRepository, JsonStateRepository
from core.structured_logging import InMemoryLogger
from core.usage_engine import UsageEngine
from providers.models import UsageMetadata


class FixedClock:
    def __init__(self):
        self.current = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self):
        value = self.current
        self.current += timedelta(seconds=1)
        return value


class UsageEngineTests(unittest.TestCase):
    def setUp(self):
        self.clock = FixedClock()
        self.repository = InMemoryStateRepository()
        self.logger = InMemoryLogger(clock=self.clock)
        self.engine = UsageEngine(self.repository, self.logger, self.clock)

    def test_records_common_usage_metadata_and_logs(self):
        record = self.engine.record(
            "workspace-a",
            "execution-a",
            UsageMetadata(2, 3, 0.0),
            mission_id="mission-a",
        )
        self.assertEqual(5, record.total_tokens)
        self.assertEqual(0.0, record.estimated_cost_usd)
        event = self.logger.query("workspace-a")[0]
        self.assertEqual("USAGE_RECORDED", event["event_type"])
        self.assertEqual(5, event["usage"]["total_tokens"])

    def test_partial_and_missing_usage_are_not_invented(self):
        partial = self.engine.record(
            "workspace-a",
            "partial",
            {"provider": "fake", "input_tokens": 2},
        )
        missing = self.engine.record("workspace-a", "missing", None)
        self.assertEqual(
            {"provider": "fake", "input_tokens": 2}, partial.usage
        )
        self.assertIsNone(missing.usage)
        summary = self.engine.summary("workspace-a")
        self.assertEqual(2, summary["input_tokens"])
        self.assertNotIn("output_tokens", summary)

    def test_input_validation_and_safe_error(self):
        for usage in (
            {"input_tokens": -1},
            {"input_tokens": True},
            {"prompt": "private"},
        ):
            result = self.engine.record_safe(
                "workspace-a", "invalid", usage
            )
            self.assertFalse(result["ok"])
            self.assertEqual("UsageError: ValueError", result["error"])
            self.assertNotIn("private", repr(result))

    def test_workspace_isolation_and_cross_workspace_get(self):
        self.engine.record("workspace-a", "shared", {"total_tokens": 2})
        self.assertIsNone(self.engine.get("shared", "workspace-b"))
        self.engine.record(
            "workspace-b", "shared", {"total_tokens": 7},
        )
        self.assertEqual(1, len(self.engine.query("workspace-a")))
        self.assertEqual(1, len(self.engine.query("workspace-b")))
        self.assertEqual(2, self.engine.get("shared", "workspace-a")["total_tokens"])
        self.assertEqual(7, self.engine.get("shared", "workspace-b")["total_tokens"])

    def test_json_restart_and_idempotency(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usage-state.json"
            first = UsageEngine(JsonStateRepository(path), clock=self.clock)
            first.record(
                "workspace-a", "execution-a",
                {"provider": "fake", "total_tokens": 4},
            )
            second = UsageEngine(JsonStateRepository(path), clock=self.clock)
            restored = second.record(
                "workspace-a", "execution-a",
                {"provider": "fake", "total_tokens": 4},
            )
            self.assertEqual(4, restored.total_tokens)
            self.assertEqual(1, len(second.query("workspace-a")))

    def test_conflicting_idempotency_key_is_rejected(self):
        self.engine.record("workspace-a", "execution-a", {"total_tokens": 1})
        result = self.engine.record_safe(
            "workspace-a", "execution-b", {"total_tokens": 2},
            usage_id="execution-a",
        )
        self.assertFalse(result["ok"])

    def test_query_filters_summary_and_recent_limit(self):
        self.engine.record(
            "workspace-a", "one",
            {"provider": "fake", "model": "a", "total_tokens": 2},
        )
        boundary = self.clock.current.isoformat()
        self.engine.record(
            "workspace-a", "two",
            {"provider": "fake", "model": "b", "total_tokens": 3},
        )
        self.assertEqual(2, self.engine.summary("workspace-a")["record_count"])
        self.assertEqual(5, self.engine.summary("workspace-a")["total_tokens"])
        self.assertEqual(1, len(self.engine.query(
            "workspace-a", model="b", start_at=boundary
        )))
        self.assertEqual("two", self.engine.query(
            "workspace-a", limit=1
        )[0]["execution_id"])

    def test_corrupt_records_are_ignored(self):
        self.repository.save(
            "usage", "broken", "workspace-a",
            {"usage_id": "broken", "workspace_id": "workspace-a"},
        )
        self.assertEqual([], self.engine.query("workspace-a"))

    def test_repository_dependency_injection_and_safe_failure(self):
        class FailingRepository:
            def save(self, *_):
                raise OSError("private path")

            def get(self, *_):
                return None

            def list(self, *_):
                raise OSError("private path")

        engine = UsageEngine(
            FailingRepository(), InMemoryLogger(fail_writes=True), self.clock
        )
        result = engine.record_safe(
            "workspace-a", "execution-a",
            {"provider": "fake", "total_tokens": 1},
        )
        self.assertEqual(
            {"ok": False, "error": "UsageError: OSError"}, result
        )
        self.assertEqual([], engine.query("workspace-a"))

    def test_sensitive_values_and_paths_cannot_enter_record(self):
        result = self.engine.record_safe(
            "workspace-a", "execution-a",
            {
                "provider": r"C:\private\result.txt",
            },
        )
        self.assertFalse(result["ok"])
        serialized = repr(result)
        self.assertNotIn("value", serialized)
        self.assertNotIn(r"C:\private", serialized)


if __name__ == "__main__":
    unittest.main()
