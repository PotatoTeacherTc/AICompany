"""Environment-driven production composition without import-time I/O."""

import os

from application.backend import BackendDependencies, BackendHealthService, create_backend_app
from application.plan_service import PlanApplicationService
from application.job_execution_api_service import JobExecutionApiService
from application.persistent_execution_service import PersistentExecutionService
from core.artifact_manager import ArtifactManager
from core.batch import BatchManager
from core.execution_history import ExecutionHistory
from core.infrastructure import (
    InfrastructureConfig,
    InfrastructureResources,
    PostgreSQLStateRepository,
    RepositoryFactory,
)
from core.migrations import PostgreSQLMigrationManager, connect_postgresql
from core.plans import PlanManager
from core.redis_job_queue import QueueConfig, QueueFactory, connect_redis
from core.task_queue import InProcessJobWorker
from core.usage_engine import UsageEngine


def create_state_repository_from_environment(environment=None):
    values = os.environ if environment is None else environment
    config = InfrastructureConfig.from_environment(values)
    if config.adapter != "postgresql":
        repository = RepositoryFactory.create_state(config)
        return repository, InfrastructureResources(repository)

    try:
        connection = connect_postgresql(config.database_url)
    except Exception:
        raise RuntimeError("database_connection_failed") from None
    try:
        migrations = PostgreSQLMigrationManager(connection)
        migrations.upgrade()
        repository = PostgreSQLStateRepository(connection, migrations)
        return repository, InfrastructureResources(repository)
    except Exception:
        connection.close()
        raise


def create_production_app(environment=None):
    repository, resources = create_state_repository_from_environment(environment)
    values = os.environ if environment is None else environment
    queue_config = QueueConfig.from_environment(values)
    redis_client = None
    if queue_config.backend == "redis":
        try:
            redis_client = connect_redis(queue_config.redis_url)
        except Exception:
            resources.close()
            raise RuntimeError("redis_queue_connection_failed") from None
    queue = QueueFactory.create(queue_config, repository, redis_client=redis_client)
    worker = InProcessJobWorker(queue)
    history = ExecutionHistory(state_repository=repository)
    artifacts = ArtifactManager()
    usage = UsageEngine(repository)
    execution = PersistentExecutionService(queue, worker, history, artifacts, usage)
    job_api = JobExecutionApiService(
        execution, history, artifacts, usage, BatchManager(queue, repository)
    )
    resources = InfrastructureResources(repository, queue)
    return create_backend_app(BackendDependencies(
        state_repository=repository,
        plan_service=PlanApplicationService(PlanManager(repository)),
        persistent_execution_service=execution,
        job_execution_api_service=job_api,
        health_service=BackendHealthService(
            persistence_probe=repository.health,
            queue_probe=queue.health if hasattr(queue, "health") else lambda: True,
        ),
        infrastructure_resources=resources,
    ))
