"""Environment-driven production composition without import-time I/O."""

import os

from application.backend import BackendDependencies, create_backend_app
from application.plan_service import PlanApplicationService
from core.infrastructure import (
    InfrastructureConfig,
    InfrastructureResources,
    PostgreSQLStateRepository,
    RepositoryFactory,
)
from core.migrations import PostgreSQLMigrationManager, connect_postgresql
from core.plans import PlanManager


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
    return create_backend_app(BackendDependencies(
        state_repository=repository,
        plan_service=PlanApplicationService(PlanManager(repository)),
        infrastructure_resources=resources,
    ))
