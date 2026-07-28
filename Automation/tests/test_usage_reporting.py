import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from application.credential_service import CredentialService
from application.login_service import LoginService
from application.session_service import SessionService
from application.usage_reporting_service import UsageReportingService
from application.user_service import UserService
from application.workspace_membership_service import WorkspaceMembershipService
from application.workspace_service import WorkspaceService
from core.access_token_provider import SignedAccessTokenProvider
from core.persistence import InMemoryStateRepository, JsonStateRepository
from core.usage_engine import UsageEngine


class _Clock:
    def __init__(self):
        self.value = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self):
        value = self.value
        self.value += timedelta(seconds=1)
        return value


class _Unused:
    pass


class UsageReportingTests(unittest.TestCase):
    def setUp(self):
        self.clock = _Clock()
        self.engine = UsageEngine(InMemoryStateRepository(), clock=self.clock)
        self.service = UsageReportingService(self.engine)
        self.engine.record(
            "workspace-a",
            "execution-a",
            {
                "provider": "fake",
                "model": "model-a",
                "input_tokens": 2,
                "total_tokens": 2,
                "estimated_cost_usd": 0,
            },
            mission_id="mission-a",
        )
        self.engine.record(
            "workspace-a",
            "execution-b",
            {"provider": "ollama", "output_tokens": 3},
            mission_id="mission-b",
        )
        self.engine.record("workspace-a", "execution-c", None)
        self.engine.record(
            "workspace-b", "execution-other", {"total_tokens": 100}
        )

    def test_list_filters_latest_pagination_and_missing_usage(self):
        result = self.service.list("workspace-a", limit=2)
        self.assertEqual(3, result["total"])
        self.assertEqual("execution-c", result["items"][0]["execution_id"])
        self.assertNotIn("provider", result["items"][0])
        filtered = self.service.list(
            "workspace-a", provider="fake", mission_id="mission-a"
        )
        self.assertEqual(1, filtered["total"])
        self.assertEqual("execution-a", filtered["items"][0]["execution_id"])
        self.assertNotIn("workspace-b", repr(result))

    def test_summary_preserves_only_present_usage_and_zero_cost(self):
        summary = self.service.summary("workspace-a")
        self.assertEqual(3, summary["record_count"])
        self.assertEqual(2, summary["input_tokens"])
        self.assertEqual(3, summary["output_tokens"])
        self.assertEqual(2, summary["total_tokens"])
        self.assertEqual(0, summary["estimated_cost_usd"])
        self.assertFalse(summary["estimated_cost_is_billed_amount"])
        self.assertEqual({"fake": 1, "ollama": 1}, summary["provider_distribution"])

    def test_invalid_ranges_pagination_and_abnormal_values_are_rejected(self):
        with self.assertRaises(ValueError):
            self.service.list("workspace-a", limit=101)
        with self.assertRaises(ValueError):
            self.service.list("workspace-a", start_at="2026-01-01T00:00:00")
        with self.assertRaises(ValueError):
            self.service.list(
                "workspace-a",
                start_at="2026-01-02T00:00:00+00:00",
                end_at="2026-01-01T00:00:00+00:00",
            )
        self.assertFalse(
            self.engine.record_safe(
                "workspace-a",
                "too-large",
                {"total_tokens": 10**12 + 1},
            )["ok"]
        )
        self.assertFalse(
            self.engine.record_safe(
                "workspace-a", "numeric-string", {"total_tokens": "2"}
            )["ok"]
        )

    def test_json_restart_and_idempotency_remain_workspace_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usage.json"
            first = UsageEngine(JsonStateRepository(path), clock=self.clock)
            first.record(
                "workspace-a",
                "execution-a",
                {"provider": "fake", "total_tokens": 4},
            )
            restarted = UsageReportingService(
                UsageEngine(JsonStateRepository(path), clock=self.clock)
            )
            result = restarted.list("workspace-a")
            self.assertEqual(1, result["total"])
            self.assertEqual(4, restarted.summary("workspace-a")["total_tokens"])
            self.assertEqual([], restarted.list("workspace-b")["items"])

    def test_api_enforces_workspace_rbac_and_safe_errors(self):
        users = UserService()
        owner = users.create("owner@example.com")
        outsider = users.create("outsider@example.com")
        credentials = CredentialService(users)
        for user in (owner, outsider):
            credentials.set_password(user["user_id"], "safe-passphrase")
        sessions = SessionService()
        login = LoginService(
            users,
            credentials,
            SignedAccessTokenProvider(secret="injected-test-secret"),
            sessions,
        )
        workspaces = WorkspaceService()
        memberships = WorkspaceMembershipService(workspaces, users)
        workspace = memberships.create_workspace("Usage", owner["user_id"])
        self.engine.record(
            workspace["workspace_id"],
            "api-execution",
            {"provider": "fake", "total_tokens": 5, "estimated_cost_usd": 0},
        )
        app = create_app(
            automation_service=_Unused(),
            task_query_service=_Unused(),
            workspace_service=workspaces,
            user_service=users,
            membership_service=memberships,
            credential_service=credentials,
            login_service=login,
            session_service=sessions,
            usage_service=self.service,
            auth_required=True,
        )
        client = TestClient(app)

        def headers(email):
            result = client.post(
                "/auth/login",
                json={"email": email, "password": "safe-passphrase"},
            ).json()
            return {"Authorization": "Bearer " + result["access_token"]}

        owner_headers = headers("owner@example.com")
        outsider_headers = headers("outsider@example.com")
        base = "/workspaces/{}/usage".format(workspace["workspace_id"])
        self.assertEqual(401, client.get(base).status_code)
        self.assertEqual(403, client.get(base, headers=outsider_headers).status_code)
        listed = client.get(base, headers=owner_headers)
        summary = client.get(base + "/summary", headers=owner_headers)
        invalid = client.get(base, params={"limit": 101}, headers=owner_headers)
        self.assertEqual(200, listed.status_code)
        self.assertEqual(5, summary.json()["total_tokens"])
        self.assertFalse(summary.json()["estimated_cost_is_billed_amount"])
        self.assertEqual(400, invalid.status_code)
        for response in (listed, summary, invalid):
            self.assertNotIn(owner_headers["Authorization"], response.text)
            self.assertNotIn("Traceback", response.text)


if __name__ == "__main__":
    unittest.main()
