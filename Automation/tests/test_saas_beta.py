import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from application.backend import (
    BackendDependencies,
    BackendHealthService,
    create_backend_app,
)
from application.onboarding_service import OnboardingService
from application.plan_service import PlanApplicationService
from application.subscription_service import SubscriptionApplicationService
from application.workspace_service import WorkspaceService
from config.settings import ALLOW_PAID_PROVIDER
from core.persistence import JsonStateRepository
from core.plans import PlanManager
from core.subscription import SubscriptionManager
from core.workspace_repository import FileWorkspaceRepository


class SaaSBetaTests(unittest.TestCase):
    def test_health_readiness_and_safe_cors_defaults(self):
        health = BackendHealthService(
            persistence_probe=lambda: True,
            queue_probe=lambda: True,
            monitor_probe=lambda: True,
        )
        client = TestClient(create_backend_app(
            BackendDependencies(health_service=health)
        ))
        self.assertEqual("ok", client.get("/health").json()["status"])
        ready = client.get("/ready").json()
        self.assertEqual("ready", ready["status"])
        self.assertFalse(ready["paid_provider_enabled"])
        allowed = client.options(
            "/health",
            headers={
                "Origin": "http://127.0.0.1:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        self.assertEqual(
            "http://127.0.0.1:5173",
            allowed.headers.get("access-control-allow-origin"),
        )
        denied = client.options(
            "/health",
            headers={
                "Origin": "https://external.example",
                "Access-Control-Request-Method": "GET",
            },
        )
        self.assertIsNone(denied.headers.get("access-control-allow-origin"))

    def test_readiness_requires_all_local_dependencies(self):
        value = BackendHealthService(
            persistence_probe=lambda: True,
            queue_probe=None,
            monitor_probe=lambda: True,
        ).readiness()
        self.assertEqual("not_ready", value["status"])
        self.assertEqual("not_configured", value["checks"]["queue"])

    def test_explicit_onboarding_is_idempotent_and_restart_safe(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace_service = WorkspaceService(
                FileWorkspaceRepository(root / "workspaces.json")
            )
            workspace = workspace_service.create("Beta Tenant")
            repository = JsonStateRepository(root / "state.json")
            plans = PlanManager(repository)
            subscriptions = SubscriptionManager(repository, plans)
            service = OnboardingService(
                workspace_service,
                SubscriptionApplicationService(subscriptions),
                PlanApplicationService(plans),
            )
            first = service.ensure_workspace(workspace["workspace_id"])
            second = service.ensure_workspace(workspace["workspace_id"])
            self.assertTrue(first["created"])
            self.assertFalse(second["created"])
            self.assertEqual("FREE", second["plan"]["plan_id"])
            restarted = SubscriptionManager(
                JsonStateRepository(root / "state.json"),
                PlanManager(JsonStateRepository(root / "state.json")),
            )
            self.assertEqual(
                first["subscription"]["subscription_id"],
                restarted.current(workspace["workspace_id"])["subscription_id"],
            )

    def test_offline_cost_policy_and_public_contract_are_safe(self):
        self.assertFalse(ALLOW_PAID_PROVIDER)
        health = BackendHealthService().snapshot()
        rendered = repr(health).lower()
        self.assertNotIn("secret", rendered)
        self.assertNotIn("token", rendered)
        self.assertNotIn(":\\", rendered)
        self.assertNotIn("/users/", rendered)


if __name__ == "__main__":
    unittest.main()
