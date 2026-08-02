"""Environment-driven production composition without import-time I/O."""

import os
import socket

from application.backend import BackendDependencies, BackendHealthService, create_backend_app
from application.plan_service import PlanApplicationService
from application.job_execution_api_service import JobExecutionApiService
from application.persistent_execution_service import PersistentExecutionService
from application.product_workflow_service import ProductWorkflowService
from application.product_content_runner import ProductContentRunner
from core.artifact_manager import ArtifactManager
from core.artifact_repository import StateArtifactRepository
from core.object_storage import ArtifactStorageAdapter, StorageFactory
from application.artifact_service import ArtifactApplicationService
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
from core.readiness import RedisWorkerReadiness
from core.operational_metrics import InMemoryOperationalMetrics
from core.production_config import validate_production_configuration
from core.security import SecuritySettings
from core.task_queue import InProcessJobWorker
from core.usage_engine import UsageEngine
from core.secure_token_store import WindowsLocalSecureTokenStore
from core.youtube_publishing import YouTubeConnectionRepository, YouTubeConnectionService
from providers.factory import ProviderFactory


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
    values = validate_production_configuration(os.environ if environment is None else environment)
    repository, resources = create_state_repository_from_environment(values)
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
    artifacts = create_artifact_manager(values, repository)
    usage = UsageEngine(repository)
    execution = PersistentExecutionService(queue, worker, history, artifacts, usage)
    youtube_connections = None
    if os.name == "nt":
        try:
            youtube_connections = YouTubeConnectionService(
                YouTubeConnectionRepository(repository), WindowsLocalSecureTokenStore()
            )
        except Exception:
            youtube_connections = None
    try:
        naver_browser = ProviderFactory.naver_blog_from_environment(values).provider
    except Exception:
        naver_browser = None
    product_runner = ProductContentRunner(
        values.get("AICOMPANY_PRODUCT_ROOT", str(os.path.join(os.getcwd(), "product-data"))),
        repository, artifacts, history, usage, values,
        youtube_connections=youtube_connections, naver_browser=naver_browser,
    )
    product = ProductWorkflowService(
        repository, execution, product_runner,
        connection_probes={
            "comfyui": lambda _workspace: "CONFIGURED" if values.get("AICOMPANY_COMFYUI_ENDPOINT") else "NOT_CONFIGURED",
            "youtube": lambda _workspace: "CONFIGURED" if values.get("AICOMPANY_YOUTUBE_PROVIDER") == "youtube" else "NOT_CONFIGURED",
            "naver": lambda _workspace: "CONFIGURED" if values.get("AICOMPANY_NAVER_PROFILE_DIR") else "NOT_CONFIGURED",
        },
        # The raw request is intentionally never persisted. Consume this
        # first local product job in-process while the existing queue still
        # provides idempotency and single-claim protection.
        auto_run=True,
    )
    job_api = JobExecutionApiService(
        execution, history, artifacts, usage, BatchManager(queue, repository)
    )
    resources = InfrastructureResources(repository, queue)
    metrics = InMemoryOperationalMetrics()
    worker_probe = lambda: True
    required_checks = ["persistence", "queue", "monitor"]
    if redis_client is not None:
        required_workers = int(values.get("AICOMPANY_REQUIRED_WORKERS", "1"))
        worker_probe = RedisWorkerReadiness(
            redis_client, queue_config.namespace, required_workers
        ).health
        required_checks.append("worker")
    return create_backend_app(BackendDependencies(
        state_repository=repository,
        plan_service=PlanApplicationService(PlanManager(repository)),
        artifact_service=ArtifactApplicationService(artifacts),
        persistent_execution_service=execution,
        job_execution_api_service=job_api,
        product_workflow_service=product,
        health_service=BackendHealthService(
            persistence_probe=repository.health,
            queue_probe=queue.health if hasattr(queue, "health") else lambda: True,
            monitor_probe=metrics.snapshot,
            worker_probe=worker_probe,
            storage_probe=artifacts.storage_adapter.health,
            required_checks=required_checks + ["storage"],
            instance_id=values.get("AICOMPANY_INSTANCE_ID") or socket.gethostname(),
        ),
        metrics=metrics,
        security_settings=SecuritySettings.from_environment(
            {key: value for key, value in values.items() if not key.endswith("_FILE")}
        ),
        infrastructure_resources=resources,
    ))


def create_artifact_manager(values, repository):
    provider = values.get("AICOMPANY_ARTIFACT_STORAGE", "fake_s3")
    storage = StorageFactory.create(
        provider,
        root=values.get("AICOMPANY_ARTIFACT_ROOT", "/data/artifacts"),
        bucket=values.get("AICOMPANY_ARTIFACT_BUCKET"),
    )
    metadata = StateArtifactRepository(repository)
    adapter = ArtifactStorageAdapter(storage, metadata)
    return ArtifactManager(metadata, adapter)
