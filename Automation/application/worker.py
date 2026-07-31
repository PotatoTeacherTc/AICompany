"""Bounded-polling production Worker entrypoint."""

import os
import signal

from application.persistent_execution_service import PersistentExecutionService
from application.production import create_state_repository_from_environment
from core.artifact_manager import ArtifactManager
from core.distributed_lock import RedisDistributedLock
from core.distributed_worker import DistributedJobWorker
from core.distributed_recovery import DistributedRecovery
from core.execution_history import ExecutionHistory
from core.redis_job_queue import QueueConfig, QueueFactory, connect_redis
from core.status import PipelineStatus
from core.retry_recovery import RetryPolicy
from core.usage_engine import UsageEngine


def build_worker(environment=None):
    values = os.environ if environment is None else environment
    repository, repository_resources = create_state_repository_from_environment(values)
    config = QueueConfig.from_environment(values)
    if config.backend != "redis":
        repository_resources.close()
        raise RuntimeError("distributed_worker_requires_redis")
    client = connect_redis(config.redis_url)
    queue = QueueFactory.create(config, repository, redis_client=client)
    recovery = DistributedRecovery(
        queue, client, config.namespace,
        RetryPolicy(
            int(values.get("AICOMPANY_WORKER_MAX_ATTEMPTS", "3")),
            float(values.get("AICOMPANY_WORKER_BACKOFF_SECONDS", "1")),
        ),
    )
    worker = DistributedJobWorker(
        queue, RedisDistributedLock(client, config.namespace),
        values.get("AICOMPANY_WORKER_ID", "worker"),
        int(values.get("AICOMPANY_WORKER_LOCK_TTL", "30")), recovery,
    )
    history = ExecutionHistory(state_repository=repository)
    service = PersistentExecutionService(
        queue, worker, history, ArtifactManager(), UsageEngine(repository)
    )
    service.register_target("offline-success", lambda _job: {
        "status": PipelineStatus.SUCCESS,
        "pipeline": "Offline Distributed Pipeline",
        "task_type": "JOB",
        "data": {"provider_usage": {"provider": "fake", "model": "offline", "estimated_cost_usd": 0}},
        "artifacts": [],
        "error": None,
    })
    service.register_target("offline-failure", lambda _job: {
        "status": PipelineStatus.FAILED,
        "pipeline": "Offline Distributed Pipeline",
        "task_type": "JOB",
        "data": {"retry": {"retryable": True, "failure_category": "provider_transient", "last_safe_error": "RetryError: provider_transient"}},
        "artifacts": [],
        "error": "ProviderError: ConnectionError",
    })
    return service, repository_resources


def main():
    service, resources = build_worker()
    workspaces = tuple(filter(None, os.environ.get("AICOMPANY_WORKER_WORKSPACES", "default").split(",")))
    running = {"value": True}
    def stop(*_): running["value"] = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while running["value"]:
            for workspace_id in workspaces:
                service.run_once(workspace_id)
    finally:
        resources.close()
        close = getattr(service.queue, "close", None)
        if close: close()


if __name__ == "__main__": main()
