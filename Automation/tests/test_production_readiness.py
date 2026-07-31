import time
import unittest

from application.backend import BackendHealthService
from core.readiness import RedisWorkerReadiness


class FakeRedis:
    def __init__(self): self.values = {}; self.fail = False
    def set(self, key, value, ex=None):
        if self.fail: raise OSError("private redis failure")
        self.values[key] = value
    def scan_iter(self, match=None, count=None):
        if self.fail: raise OSError("private redis failure")
        prefix = match[:-1]
        return iter(key for key in self.values if key.startswith(prefix))


class ProductionReadinessTests(unittest.TestCase):
    def test_required_probes_ready_and_shutdown_blocks_readiness(self):
        health = BackendHealthService(
            persistence_probe=lambda: True, queue_probe=lambda: True,
            monitor_probe=lambda: {}, worker_probe=lambda: {"ok": True},
            required_checks=("persistence", "queue", "monitor", "worker"),
        )
        self.assertEqual("ready", health.readiness()["status"])
        health.begin_shutdown()
        self.assertEqual("not_ready", health.readiness()["status"])

    def test_dependency_failure_and_timeout_are_safe(self):
        failed = BackendHealthService(
            persistence_probe=lambda: {"ok": False, "url": "private"},
            required_checks=("persistence",),
        ).readiness()
        self.assertEqual("not_ready", failed["status"])
        self.assertNotIn("private", repr(failed))
        slow = BackendHealthService(
            persistence_probe=lambda: time.sleep(0.1),
            required_checks=("persistence",), probe_timeout_seconds=0.01,
        )
        self.assertEqual("not_ready", slow.readiness()["status"])

    def test_worker_heartbeat_count_and_failure(self):
        redis = FakeRedis(); probe = RedisWorkerReadiness(redis, "test", 2)
        probe.touch("one")
        self.assertFalse(probe.health()["ok"])
        probe.touch("two")
        self.assertTrue(probe.health()["ok"])
        redis.fail = True
        self.assertFalse(probe.health()["ok"])
        with self.assertRaisesRegex(RuntimeError, "worker_readiness_unavailable"):
            probe.touch("three")


if __name__ == "__main__": unittest.main()
