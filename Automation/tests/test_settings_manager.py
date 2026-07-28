import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.persistence import InMemoryStateRepository, JsonStateRepository
from core.settings_manager import SettingsManager
from core.structured_logging import InMemoryLogger
from providers.factory import ProviderFactory


class FixedClock:
    def __init__(self):
        self.current = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self):
        value = self.current
        self.current += timedelta(seconds=1)
        return value


class SettingsManagerTests(unittest.TestCase):
    def setUp(self):
        self.clock = FixedClock()
        self.repository = InMemoryStateRepository()
        self.logger = InMemoryLogger(clock=self.clock)
        self.manager = SettingsManager(self.repository, self.logger, self.clock)

    def test_safe_defaults_and_normal_update(self):
        defaults = self.manager.get("workspace-a")
        self.assertEqual("mock", defaults.provider)
        self.assertEqual("fake", defaults.music_provider)
        self.assertFalse(defaults.allow_paid_provider)
        updated = self.manager.update(
            "workspace-a",
            {
                "provider_timeout_seconds": 12,
                "retry_max_attempts": 4,
                "log_level": "WARNING",
            },
            expected_revision=0,
        )
        self.assertEqual(1, updated.revision)
        self.assertEqual(12, updated.provider_timeout_seconds)
        self.assertEqual(
            "SETTINGS_UPDATED",
            self.logger.query("workspace-a")[0]["event_type"],
        )

    def test_invalid_unknown_sensitive_and_path_values(self):
        for changes in (
            {"prompt": "private"},
            {"api_key": "secret"},
            {"provider": r"C:\private\provider"},
            {"provider_timeout_seconds": -1},
            {"retry_max_attempts": True},
        ):
            result = self.manager.update_safe("workspace-a", changes)
            self.assertEqual(
                {"ok": False, "error": "SettingsError: ValueError"}, result
            )
            serialized = repr(result)
            self.assertNotIn("private", serialized)
            self.assertNotIn("secret", serialized)

    def test_paid_provider_cannot_be_enabled(self):
        result = self.manager.update_safe(
            "workspace-a", {"allow_paid_provider": True}
        )
        self.assertFalse(result["ok"])
        self.assertFalse(self.manager.get("workspace-a").allow_paid_provider)

    def test_workspace_isolation_with_same_storage_contract(self):
        self.manager.update("workspace-a", {"batch_max_items": 10})
        self.manager.update("workspace-b", {"batch_max_items": 20})
        self.assertEqual(10, self.manager.get("workspace-a").batch_max_items)
        self.assertEqual(20, self.manager.get("workspace-b").batch_max_items)
        self.assertIsNone(self.repository.get(
            "settings", "workspace-a:settings", "workspace-b"
        ))

    def test_json_restart_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            first = SettingsManager(JsonStateRepository(path), clock=self.clock)
            first.update("workspace-a", {"retry_backoff_seconds": 2.5})
            second = SettingsManager(JsonStateRepository(path), clock=self.clock)
            self.assertEqual(
                2.5, second.get("workspace-a").retry_backoff_seconds
            )

    def test_corrupt_or_unsupported_persistence_uses_defaults(self):
        self.repository.save(
            "settings", "workspace-a:settings", "workspace-a",
            {
                "workspace_id": "workspace-a",
                "revision": 1,
                "updated_at": "broken",
                "provider": "paid",
            },
        )
        settings = self.manager.get("workspace-a")
        self.assertEqual(0, settings.revision)
        self.assertEqual("mock", settings.provider)

    def test_revision_conflict_and_idempotent_read(self):
        self.manager.update("workspace-a", {"batch_max_items": 5})
        result = self.manager.update_safe(
            "workspace-a",
            {"batch_max_items": 6},
            expected_revision=0,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(5, self.manager.get("workspace-a").batch_max_items)

    def test_provider_factory_and_retry_policy_integration(self):
        self.manager.update(
            "workspace-a",
            {
                "provider_timeout_seconds": 7,
                "retry_max_attempts": 2,
                "retry_backoff_seconds": 1.5,
            },
        )
        environment = self.manager.provider_environment(
            "workspace-a", "provider"
        )
        selection = ProviderFactory.from_environment(environment)
        self.assertEqual("mock", selection.provider.name)
        self.assertEqual(7, selection.timeout_seconds)
        policy = self.manager.retry_policy("workspace-a")
        self.assertEqual(2, policy.max_attempts)
        self.assertEqual(1.5, policy.backoff_seconds)

    def test_logger_failure_does_not_change_persisted_settings(self):
        manager = SettingsManager(
            self.repository, InMemoryLogger(fail_writes=True), self.clock
        )
        updated = manager.update("workspace-a", {"batch_max_items": 9})
        self.assertEqual(9, updated.batch_max_items)
        self.assertEqual(9, manager.get("workspace-a").batch_max_items)

    def test_repository_dependency_failure_is_safe_for_reads_and_writes(self):
        class FailingRepository:
            def get(self, *_):
                raise OSError("private path")

            def save(self, *_):
                raise OSError("private path")

        manager = SettingsManager(FailingRepository(), clock=self.clock)
        self.assertEqual("mock", manager.get("workspace-a").provider)
        result = manager.update_safe(
            "workspace-a", {"batch_max_items": 4}
        )
        self.assertEqual(
            {"ok": False, "error": "SettingsError: OSError"}, result
        )


if __name__ == "__main__":
    unittest.main()
