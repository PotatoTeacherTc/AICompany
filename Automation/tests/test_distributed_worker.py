import unittest

from application.persistent_execution_service import PersistentExecutionService
from core.artifact_manager import ArtifactManager
from core.distributed_lock import InMemoryDistributedLock
from core.distributed_worker import DistributedJobWorker
from core.execution_history import ExecutionHistory
from core.persistence import InMemoryStateRepository
from core.redis_job_queue import RedisJobQueue
from core.status import PipelineStatus
from core.usage_engine import UsageEngine
from tests.test_redis_job_queue import FakeRedis


class DistributedWorkerTests(unittest.TestCase):
    def build(self, repository=None, redis=None, worker_id="worker"):
        repository = repository or InMemoryStateRepository(); redis = redis or FakeRedis()
        queue = RedisJobQueue(repository, redis, namespace="worker", blocking_timeout=0)
        worker = DistributedJobWorker(queue, InMemoryDistributedLock(), worker_id, lock_ttl=2)
        history = ExecutionHistory(state_repository=repository)
        service = PersistentExecutionService(queue, worker, history, ArtifactManager(), UsageEngine(repository))
        return service, repository, redis

    def test_independent_worker_executes_and_records_shared_history(self):
        api, repository, redis = self.build(worker_id="api-local")
        job = api.submit("ws", "mission", "offline", "key")
        worker, _, _ = self.build(repository, redis, "external")
        worker.register_target("offline", lambda _job: {
            "status": PipelineStatus.SUCCESS, "pipeline": "Fake Pipeline",
            "task_type": "JOB", "data": {}, "artifacts": [], "error": None,
        })
        completed = worker.run_once("ws")
        self.assertEqual(job.job_id, completed.job_id)
        self.assertEqual("COMPLETED", completed.status)
        self.assertEqual(job.job_id, ExecutionHistory(state_repository=repository).query(workspace_id="ws")[0]["task_id"])

    def test_two_workers_do_not_duplicate_completion(self):
        api, repository, redis = self.build()
        api.submit("ws", "mission", "offline", "key")
        first, _, _ = self.build(repository, redis, "one")
        second, _, _ = self.build(repository, redis, "two")
        result = {"status": PipelineStatus.SUCCESS, "pipeline": "Fake", "task_type": "JOB", "data": {}, "artifacts": [], "error": None}
        first.register_target("offline", lambda _: result); second.register_target("offline", lambda _: result)
        self.assertIsNotNone(first.run_once("ws"))
        self.assertIsNone(second.run_once("ws"))
        self.assertEqual(1, len(repository.list("execution", "ws")))

    def test_pipeline_failure_and_missing_target_are_recorded(self):
        service, repository, _ = self.build()
        service.submit("ws", "mission", "missing", "key")
        failed = service.run_once("ws")
        self.assertEqual("FAILED", failed.status)
        self.assertEqual("JobError: TargetUnavailable", failed.result["error"])

    def test_abandoned_processing_job_is_recovered_after_ttl(self):
        service, repository, redis = self.build()
        job = service.submit("ws", "mission", "offline", "key")
        service.queue.claim("ws", "crashed")
        recovered, _, _ = self.build(repository, redis, "new")
        recovered.register_target("offline", lambda _: {"status": "SUCCESS", "pipeline": "Fake", "task_type": "JOB", "data": {}, "artifacts": [], "error": None})
        self.assertEqual(job.job_id, recovered.run_once("ws").job_id)


if __name__ == "__main__": unittest.main()
