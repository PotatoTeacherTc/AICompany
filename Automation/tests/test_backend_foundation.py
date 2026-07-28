import unittest

from fastapi.testclient import TestClient

from application.backend import (
    BackendDependencies,
    BackendHealthService,
    create_backend_app,
)
from application.workspace_service import WorkspaceService
from core.structured_logging import InMemoryLogger
from core.workspace_repository import InMemoryWorkspaceRepository


class BackendFoundationTests(unittest.TestCase):
    def test_backend_app_creation_and_safe_health_contract(self):
        health = BackendHealthService(
            persistence_probe=lambda: True,
            queue_probe=lambda: {"ok": True},
            monitor_probe=lambda: "healthy",
        )
        client = TestClient(create_backend_app(
            BackendDependencies(health_service=health)
        ))
        response = client.get("/health")
        self.assertEqual(200, response.status_code)
        self.assertEqual("ok", response.json()["status"])
        self.assertEqual("1", response.json()["schema_version"])
        self.assertFalse(response.json()["paid_provider_enabled"])

    def test_probe_failures_are_sanitized_and_logger_failure_isolated(self):
        def failing_probe():
            raise OSError(r"C:\private\secret-token")

        health = BackendHealthService(
            persistence_probe=failing_probe,
            queue_probe=lambda: False,
            monitor_probe=lambda: {"ok": False},
            logger=InMemoryLogger(fail_writes=True),
        )
        response = TestClient(create_backend_app(
            BackendDependencies(health_service=health)
        )).get("/health")
        self.assertEqual("degraded", response.json()["status"])
        self.assertNotIn("private", repr(response.json()))
        self.assertNotIn("secret-token", repr(response.json()))
        self.assertNotIn(":\\", repr(response.json()))

    def test_injected_workspace_repository_and_app_instances_are_isolated(self):
        first_service = WorkspaceService(InMemoryWorkspaceRepository())
        second_service = WorkspaceService(InMemoryWorkspaceRepository())
        first = TestClient(create_backend_app(
            BackendDependencies(workspace_service=first_service)
        ))
        second = TestClient(create_backend_app(
            BackendDependencies(workspace_service=second_service)
        ))
        created = first.post("/workspaces", json={"name": "Tenant A"}).json()
        workspace_id = created["workspace_id"]
        self.assertEqual(
            404, second.get(f"/workspaces/{workspace_id}").status_code
        )

    def test_correlation_id_is_safe_and_request_headers_are_not_reflected(self):
        client = TestClient(create_backend_app())
        response = client.get(
            "/health",
            headers={
                "X-Correlation-ID": "safe_trace_12345678",
                "Authorization": "Bearer private-token",
                "Cookie": "secret=value",
            },
        )
        self.assertEqual("safe_trace_12345678", response.headers["X-Correlation-ID"])
        self.assertNotIn("private-token", repr(response.json()))
        self.assertNotIn("secret=value", repr(response.json()))


if __name__ == "__main__":
    unittest.main()
