import threading
import unittest

from application.backend import BackendHealthService
from application.worker import run_worker_cycle
from core.distributed_lock import InMemoryDistributedLock
from core.distributed_worker import DistributedJobWorker
from core.persistence import InMemoryStateRepository
from core.redis_job_queue import RedisJobQueue
from tests.test_redis_job_queue import FakeRedis


class MultiInstanceExecutionTests(unittest.TestCase):
    def build_worker(self, repository, redis, worker_id):
        queue = RedisJobQueue(repository, redis, namespace="multi", blocking_timeout=0)
        worker = DistributedJobWorker(queue, InMemoryDistributedLock(), worker_id)
        worker.register_target("offline", lambda _job: {
            "status": "SUCCESS", "pipeline": "Offline", "data": {},
            "artifacts": [], "error": None,
        })
        return worker

    def test_two_worker_instances_complete_one_shared_job_once(self):
        repository = InMemoryStateRepository(); redis = FakeRedis()
        queue = RedisJobQueue(repository, redis, namespace="multi", blocking_timeout=0)
        job = queue.enqueue("ws", "mission", "offline", "same")
        workers = [self.build_worker(repository, redis, value) for value in ("one", "two")]
        barrier = threading.Barrier(2); results = []
        def run(worker):
            barrier.wait(); results.append(worker.run_once("ws"))
        threads = [threading.Thread(target=run, args=(worker,)) for worker in workers]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(1, len([value for value in results if value is not None]))
        self.assertEqual("COMPLETED", queue.get(job.job_id, "ws").status)

    def test_multiple_workers_preserve_workspace_boundaries(self):
        repository = InMemoryStateRepository(); redis = FakeRedis()
        queue = RedisJobQueue(repository, redis, namespace="multi", blocking_timeout=0)
        first = queue.enqueue("ws-a", "m", "offline", "a")
        second = queue.enqueue("ws-b", "m", "offline", "b")
        worker_a = self.build_worker(repository, redis, "worker-a")
        worker_b = self.build_worker(repository, redis, "worker-b")
        self.assertEqual(first.job_id, worker_a.run_once("ws-a").job_id)
        self.assertEqual(second.job_id, worker_b.run_once("ws-b").job_id)
        self.assertIsNone(queue.get(first.job_id, "ws-b"))

    def test_backend_health_exposes_only_safe_instance_identity(self):
        health = BackendHealthService(
            persistence_probe=lambda: True, queue_probe=lambda: True,
            instance_id="backend-2",
        )
        self.assertEqual("backend-2", health.snapshot()["instance_id"])
        self.assertEqual("backend-2", health.readiness()["instance_id"])
        unsafe = BackendHealthService(instance_id="C:\\private\\token")
        self.assertNotIn("instance_id", unsafe.snapshot())

    def test_worker_cycle_contains_transient_shared_service_failure(self):
        class Service:
            def __init__(self): self.fail = True; self.calls = 0
            def run_once(self, _workspace_id):
                self.calls += 1
                if self.fail: raise RuntimeError("redis_queue_unavailable")
        service = Service()
        self.assertFalse(run_worker_cycle(service, ("ws-a", "ws-b")))
        service.fail = False
        self.assertTrue(run_worker_cycle(service, ("ws-a", "ws-b")))
        self.assertEqual(3, service.calls)


if __name__ == "__main__":
    unittest.main()
