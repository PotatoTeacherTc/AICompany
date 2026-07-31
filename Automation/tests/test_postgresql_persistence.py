import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from application.production import (
    create_production_app,
    create_state_repository_from_environment,
)
from core.infrastructure import InfrastructureConfig, PostgreSQLStateRepository
from core.migrations import LATEST_VERSION, PostgreSQLMigrationManager


class MigrationCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []

    def __enter__(self): return self
    def __exit__(self, *_): return None

    def execute(self, query, values=None):
        compact = " ".join(query.split())
        if compact.startswith("SELECT COALESCE"):
            self.rows = [(max(self.connection.versions, default=0),)]
        elif compact == "SELECT version FROM aicompany_schema_migrations":
            self.rows = [(value,) for value in self.connection.versions]
        elif compact.startswith("INSERT INTO aicompany_schema_migrations"):
            self.connection.versions.add(values[0])
        elif compact == "SELECT 1":
            self.rows = [(1,)]

    def fetchone(self): return self.rows[0] if self.rows else None
    def fetchall(self): return list(self.rows)


class MigrationConnection:
    def __init__(self):
        self.versions = set()
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self): return MigrationCursor(self)
    def commit(self): self.commits += 1
    def rollback(self): self.rollbacks += 1
    def close(self): self.closed = True


class PostgreSQLPersistenceTests(unittest.TestCase):
    def test_backend_selection_aliases_and_invalid_value(self):
        self.assertEqual(
            "postgresql",
            InfrastructureConfig.from_environment({
                "AICOMPANY_REPOSITORY_ADAPTER": "postgres",
                "DATABASE_URL": "postgresql://user:private@db/app",
            }).adapter,
        )
        with self.assertRaisesRegex(ValueError, "unsupported_repository_adapter"):
            InfrastructureConfig.from_environment({
                "AICOMPANY_REPOSITORY_ADAPTER": "unknown",
            })

    def test_migration_upgrade_is_versioned_and_idempotent(self):
        connection = MigrationConnection()
        manager = PostgreSQLMigrationManager(connection)
        self.assertEqual(LATEST_VERSION, manager.upgrade())
        commits = connection.commits
        self.assertEqual(LATEST_VERSION, manager.upgrade())
        self.assertEqual(commits + 2, connection.commits)
        self.assertEqual("current", manager.status())

    def test_production_composition_migrates_and_exposes_safe_health(self):
        connection = MigrationConnection()
        environment = {
            "AICOMPANY_REPOSITORY_ADAPTER": "postgresql",
            "DATABASE_URL": "postgresql://user:private@db/app",
        }
        with patch("application.production.connect_postgresql", return_value=connection):
            app = create_production_app(environment)
        self.assertIsInstance(app.state.state_repository, PostgreSQLStateRepository)
        self.assertIs(app.state.plan_service.manager.repository, app.state.state_repository)
        with TestClient(app) as client:
            health = client.get("/health").json()
            self.assertEqual("available", health["checks"]["persistence"])
            self.assertEqual("current", health["details"]["persistence"]["migration"])
            self.assertNotIn("private", repr(health))
        self.assertTrue(connection.closed)

    def test_memory_and_json_composition_remain_available(self):
        repository, resources = create_state_repository_from_environment({
            "AICOMPANY_REPOSITORY_ADAPTER": "memory",
        })
        repository.save("mission", "m1", "ws-a", {"status": "PENDING"})
        self.assertEqual("PENDING", repository.get("mission", "m1", "ws-a")["status"])
        self.assertTrue(resources.health()["ok"])
        with tempfile.TemporaryDirectory() as root:
            json_repository, json_resources = create_state_repository_from_environment({
                "AICOMPANY_REPOSITORY_ADAPTER": "json",
                "AICOMPANY_STATE_FILE": str(Path(root) / "state.json"),
            })
            json_repository.save("mission", "m1", "ws-a", {"status": "JSON"})
            self.assertEqual("JSON", json_repository.get("mission", "m1", "ws-a")["status"])
            self.assertTrue(json_resources.health()["ok"])

    def test_connection_failure_is_sanitized(self):
        environment = {
            "AICOMPANY_REPOSITORY_ADAPTER": "postgresql",
            "DATABASE_URL": "postgresql://user:private@db/app",
        }
        with patch(
            "application.production.connect_postgresql",
            side_effect=RuntimeError(r"private C:\\database\\path"),
        ):
            with self.assertRaisesRegex(RuntimeError, "database_connection_failed"):
                create_state_repository_from_environment(environment)

    @unittest.skipUnless(os.environ.get("AICOMPANY_TEST_POSTGRES_URL"), "Docker PostgreSQL integration")
    def test_real_postgresql_restart_and_workspace_isolation(self):
        environment = {
            "AICOMPANY_REPOSITORY_ADAPTER": "postgresql",
            "DATABASE_URL": os.environ["AICOMPANY_TEST_POSTGRES_URL"],
        }
        first, first_resources = create_state_repository_from_environment(environment)
        first.save("mission", "integration-mission", "integration-a", {"status": "A"})
        first.save("mission", "integration-mission", "integration-b", {"status": "B"})
        first_resources.close()
        second, second_resources = create_state_repository_from_environment(environment)
        try:
            self.assertEqual("A", second.get("mission", "integration-mission", "integration-a")["status"])
            self.assertEqual("B", second.get("mission", "integration-mission", "integration-b")["status"])
            self.assertIsNone(second.get("mission", "integration-mission", "integration-c"))
        finally:
            with second.connection.cursor() as cursor:
                cursor.execute(
                    """
                    DELETE FROM aicompany_state
                    WHERE kind = %s AND record_id = %s
                      AND workspace_id IN (%s, %s)
                    """,
                    ("mission", "integration-mission", "integration-a", "integration-b"),
                )
            second.connection.commit()
            second_resources.close()


if __name__ == "__main__":
    unittest.main()
