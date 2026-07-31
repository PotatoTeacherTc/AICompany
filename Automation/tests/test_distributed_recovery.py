import unittest
from datetime import datetime, timedelta, timezone

from core.distributed_recovery import DistributedRecovery
from core.distributed_lock import InMemoryDistributedLock
from core.distributed_worker import DistributedJobWorker
from core.persistence import InMemoryStateRepository
from core.redis_job_queue import RedisJobQueue
from core.retry_recovery import RetryPolicy
from core.task_queue import JobStatus
from tests.test_redis_job_queue import FakeRedis


class Clock:
    def __init__(self): self.value = datetime(2026, 1, 1, tzinfo=timezone.utc)
    def __call__(self): return self.value


class DistributedRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.repository = InMemoryStateRepository(); self.redis = FakeRedis(); self.clock = Clock()
        self.queue = RedisJobQueue(self.repository, self.redis, namespace="recovery", blocking_timeout=0)
        self.recovery = DistributedRecovery(self.queue, self.redis, "recovery", RetryPolicy(3, 2), self.clock)

    def failed(self, retryable=True):
        job = self.queue.enqueue("ws", "m", "target", str(len(self.queue.list("ws"))))
        claimed = self.queue.claim("ws", "worker")
        return self.queue.fail(claimed.job_id, "ws", "worker", {"status": "FAILED", "error": "ProviderError: ConnectionError"}, {"retryable": retryable, "failure_category": "provider_transient"})

    def test_transient_failure_backoff_and_retry_success_contract(self):
        pending = self.recovery.after_failure(self.failed())
        self.assertEqual(JobStatus.PENDING, pending.status)
        self.assertEqual(1, pending.retry_state["current_attempt"])
        self.assertEqual(0, self.recovery.promote_due("ws"))
        self.clock.value += timedelta(seconds=2)
        self.assertEqual(1, self.recovery.promote_due("ws"))
        self.assertIsNotNone(self.queue.claim("ws", "next"))

    def test_non_retryable_and_max_attempts_go_to_workspace_dlq(self):
        final = self.recovery.after_failure(self.failed(False))
        self.assertEqual(JobStatus.FAILED, final.status)
        self.assertEqual([final.job_id], [job.job_id for job in self.recovery.list_dlq("ws")])
        self.assertEqual([], self.recovery.list_dlq("other"))

    def test_max_attempts_is_bounded(self):
        job = self.failed(True)
        for expected in (1, 2):
            job = self.recovery.after_failure(job)
            self.clock.value += timedelta(seconds=10)
            self.recovery.promote_due("ws")
            claimed = self.queue.claim("ws", "worker")
            job = self.queue.fail(claimed.job_id, "ws", "worker", {"status": "FAILED", "error": "ProviderError: ConnectionError"}, job.retry_state)
        final = self.recovery.after_failure(job)
        self.assertFalse(final.retry_state["retryable"])
        self.assertEqual(3, final.retry_state["current_attempt"])
        self.assertEqual(1, len(self.recovery.list_dlq("ws")))

    def test_redis_failure_is_safe(self):
        self.redis.fail = True
        with self.assertRaisesRegex(RuntimeError, "distributed_recovery_unavailable"):
            self.recovery.promote_due("ws")

    def test_worker_preserves_attempt_count_between_pipeline_failures(self):
        job = self.queue.enqueue("ws", "m", "target", "worker-retry")
        worker = DistributedJobWorker(
            self.queue, InMemoryDistributedLock(), "worker", recovery=self.recovery
        )
        worker.register_target("target", lambda _job: {
            "status": "FAILED", "error": "ProviderError: ConnectionError",
            "data": {"retry": {"retryable": True, "failure_category": "provider_transient"}},
        })
        for delay in (2, 4):
            worker.run_once("ws")
            self.clock.value += timedelta(seconds=delay)
        final = worker.run_once("ws")
        self.assertEqual(JobStatus.FAILED, final.status)
        self.assertEqual(3, final.retry_state["current_attempt"])
        self.assertEqual([job.job_id], [value.job_id for value in self.recovery.list_dlq("ws")])


if __name__ == "__main__": unittest.main()
