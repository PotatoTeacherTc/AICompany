import json
import unittest

from core.persistence import InMemoryStateRepository
from core.redis_job_queue import QueueConfig, QueueFactory, RedisJobQueue
from core.task_queue import JobStatus, PersistentJobQueue


class FakeRedis:
    def __init__(self, fail=False):
        self.lists = {}
        self.sorted = {}
        self.fail = fail
        self.closed = False

    def _check(self):
        if self.fail:
            raise OSError("redis://user:private@host")

    def rpush(self, key, value):
        self._check(); self.lists.setdefault(key, []).append(value)

    def blmove(self, source, destination, timeout, src, dest):
        self._check()
        values = self.lists.setdefault(source, [])
        if not values:
            return None
        value = values.pop(0)
        self.lists.setdefault(destination, []).append(value)
        return value

    def lrem(self, key, count, value):
        self._check()
        values = self.lists.setdefault(key, [])
        self.lists[key] = [item for item in values if item != value]
    def lrange(self, key, start, end):
        self._check(); return list(self.lists.get(key, []))
    def zadd(self, key, mapping):
        self._check(); self.sorted.setdefault(key, {}).update(mapping); return len(mapping)
    def zrangebyscore(self, key, minimum, maximum):
        self._check(); return [item for item, score in self.sorted.get(key, {}).items() if score <= float(maximum)]
    def zrem(self, key, value):
        self._check(); return 1 if self.sorted.setdefault(key, {}).pop(value, None) is not None else 0

    def ping(self): self._check(); return True
    def close(self): self.closed = True


class RedisJobQueueTests(unittest.TestCase):
    def test_factory_preserves_memory_default_and_validates_settings(self):
        repository = InMemoryStateRepository()
        queue = QueueFactory.create(QueueConfig(), repository)
        self.assertIsInstance(queue, PersistentJobQueue)
        with self.assertRaisesRegex(ValueError, "unsupported_queue_backend"):
            QueueConfig("other").validate()
        with self.assertRaisesRegex(ValueError, "redis_queue_url_required"):
            QueueConfig("redis").validate()

    def test_redis_fifo_workspace_and_payload_contract(self):
        repository = InMemoryStateRepository()
        redis = FakeRedis()
        queue = RedisJobQueue(repository, redis, namespace="test")
        first = queue.enqueue("ws-a", "m1", "content", "one", {"retryable": True})
        second = queue.enqueue("ws-a", "m2", "content", "two")
        queue.enqueue("ws-b", "m3", "content", "three")
        claimed = queue.claim("ws-a", "worker")
        self.assertEqual(first.job_id, claimed.job_id)
        self.assertEqual("ws-a", claimed.workspace_id)
        self.assertEqual({"retryable": True}, claimed.retry_state)
        queue.complete(first.job_id, "ws-a", "worker", {"status": "SUCCESS"})
        self.assertEqual(second.job_id, queue.claim("ws-a", "worker").job_id)

    def test_recreation_keeps_pending_job_and_idempotency(self):
        repository = InMemoryStateRepository()
        redis = FakeRedis()
        first = RedisJobQueue(repository, redis, namespace="restart")
        job = first.enqueue("ws-a", "m1", "content", "same")
        second = RedisJobQueue(repository, redis, namespace="restart")
        duplicate = second.enqueue("ws-a", "m1", "content", "same")
        self.assertEqual(job.job_id, duplicate.job_id)
        self.assertEqual(job.job_id, second.get(job.job_id, "ws-a").job_id)
        self.assertEqual([job.job_id], [value.job_id for value in second.list("ws-a")])
        self.assertEqual(job.job_id, second.claim("ws-a", "worker").job_id)

    def test_namespace_isolation(self):
        repository = InMemoryStateRepository()
        redis = FakeRedis()
        one = RedisJobQueue(repository, redis, namespace="one", blocking_timeout=0)
        two = RedisJobQueue(repository, redis, namespace="two", blocking_timeout=0)
        one.enqueue("ws", "m", "target", "key")
        self.assertIsNone(two.claim("ws", "worker"))

    def test_redis_failure_is_safe(self):
        queue = RedisJobQueue(InMemoryStateRepository(), FakeRedis(fail=True))
        with self.assertRaisesRegex(RuntimeError, "redis_queue_unavailable") as raised:
            queue.enqueue("ws", "m", "target", "key")
        self.assertNotIn("private", str(raised.exception))

    def test_job_payload_remains_serializable(self):
        repository = InMemoryStateRepository()
        queue = RedisJobQueue(repository, FakeRedis())
        job = queue.enqueue("ws", "m", "target", "key")
        self.assertEqual(job.job_id, json.loads(json.dumps(job.to_dict()))["job_id"])


if __name__ == "__main__":
    unittest.main()
