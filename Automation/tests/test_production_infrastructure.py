import json
import unittest

from fastapi.testclient import TestClient

from application.backend import BackendDependencies, create_backend_app
from core.infrastructure import (
    InfrastructureConfig,
    InfrastructureResources,
    PostgreSQLStateRepository,
    RedisStateRepository,
    RepositoryFactory,
)


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.closed = False

    def set(self, key, value): self.values[key] = value
    def get(self, key): return self.values.get(key)
    def scan_iter(self, match):
        prefix = match[:-1]
        return [key for key in self.values if key.startswith(prefix)]
    def ping(self): return True
    def close(self): self.closed = True


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []
    def __enter__(self): return self
    def __exit__(self, *_): return None
    def execute(self, query, values=None):
        compact = " ".join(query.split())
        if compact == "SELECT 1":
            self.rows = [(1,)]
        elif compact.startswith("INSERT"):
            kind, record_id, workspace_id, version, payload = values
            self.connection.values[(kind, workspace_id, record_id)] = (
                workspace_id, version, payload
            )
        elif "record_id = %s" in compact:
            kind, record_id, workspace_id = values
            key = (kind, workspace_id, record_id)
            self.rows = [self.connection.values[key]] if key in self.connection.values else []
        else:
            kind, workspace_id = values
            self.rows = [
                (record_id, row[1], row[2])
                for (row_kind, row_workspace, record_id), row in self.connection.values.items()
                if row_kind == kind and row_workspace == workspace_id
            ]
    def fetchone(self): return self.rows[0] if self.rows else None
    def fetchall(self): return self.rows


class FakeConnection:
    def __init__(self):
        self.values = {}
        self.closed = False
        self.commits = 0
    def cursor(self): return FakeCursor(self)
    def commit(self): self.commits += 1
    def close(self): self.closed = True


class ProductionInfrastructureTests(unittest.TestCase):
    def test_environment_adapter_selection_and_validation(self):
        config = InfrastructureConfig.from_environment({
            "AICOMPANY_REPOSITORY_ADAPTER": "memory",
        })
        self.assertIsNotNone(RepositoryFactory.create_state(config))
        with self.assertRaisesRegex(ValueError, "invalid_infrastructure_url"):
            RepositoryFactory.create_state(InfrastructureConfig(
                "postgres", database_url="http://external.test"
            ), postgres_connection=FakeConnection())
        with self.assertRaisesRegex(ValueError, "redis_client_required"):
            RepositoryFactory.create_state(InfrastructureConfig(
                "redis", redis_url="redis://redis:6379/0"
            ))

    def test_redis_adapter_is_workspace_isolated_and_health_checked(self):
        client = FakeRedis()
        repository = RedisStateRepository(client)
        repository.save("mission", "same", "ws-a", {"value": "a"})
        self.assertEqual("a", repository.get("mission", "same", "ws-a")["value"])
        self.assertIsNone(repository.get("mission", "same", "ws-b"))
        self.assertTrue(repository.health()["ok"])

    def test_postgres_adapter_uses_parameterized_contract(self):
        connection = FakeConnection()
        repository = PostgreSQLStateRepository(connection)
        repository.save("mission", "m1", "ws-a", {"status": "PENDING"})
        self.assertEqual(
            "PENDING", repository.get("mission", "m1", "ws-a")["status"]
        )
        self.assertEqual(1, connection.commits)
        self.assertTrue(repository.health()["ok"])
        repository.save("mission", "m1", "ws-b", {"status": "OTHER"})
        self.assertEqual(
            "PENDING", repository.get("mission", "m1", "ws-a")["status"]
        )
        self.assertEqual(
            "OTHER", repository.get("mission", "m1", "ws-b")["status"]
        )

    def test_graceful_shutdown_closes_injected_resources(self):
        redis = RedisStateRepository(FakeRedis())
        resources = InfrastructureResources(redis)
        with TestClient(create_backend_app(BackendDependencies(
            infrastructure_resources=resources
        ))) as client:
            self.assertEqual(200, client.get("/health").status_code)
        self.assertTrue(resources.closed)
        self.assertTrue(redis.client.closed)

    def test_health_resources_report_failure_without_raw_error(self):
        class Failed:
            def health(self): return {"ok": False, "error": r"C:\secret"}
            def close(self): pass
        value = InfrastructureResources(Failed()).health()
        self.assertEqual({"ok": False}, value)
        self.assertNotIn("secret", repr(value))


if __name__ == "__main__":
    unittest.main()
