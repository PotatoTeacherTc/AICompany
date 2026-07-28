import unittest

from fastapi.testclient import TestClient

from api.app import create_app
from application.credential_service import CredentialService
from application.login_service import LoginService
from application.session_service import SessionService
from application.user_service import UserService
from application.workspace_membership_service import WorkspaceMembershipService
from application.workspace_service import WorkspaceService
from core.access_token_provider import SignedAccessTokenProvider
from core.workspace_membership import MEMBER


class _TaskApi:
    def __init__(self, tasks=None):
        self.tasks = tasks or {}

    def get_task(self, task_id, workspace_id=None):
        task = self.tasks.get(task_id)
        return {"found": bool(task), "task": task}

    def list_tasks(self, _):
        return {
            "items": [
                {"task_id": task_id, "task": dict(task)}
                for task_id, task in self.tasks.items()
            ]
        }

    def cancel_task(self, task_id):
        return {"task_id": task_id}

    def retry_task(self, task_id):
        return {"task_id": task_id}


class _Unused:
    pass


class AuthenticatedApiContextTests(unittest.TestCase):
    def setUp(self):
        users = UserService()
        self.owner = users.create("owner@example.com")
        self.other = users.create("other@example.com")
        credentials = CredentialService(users)
        for user in (self.owner, self.other):
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
        self.workspace = memberships.create_workspace(
            "Owned", self.owner["user_id"]
        )
        self.other_workspace = memberships.create_workspace(
            "Other", self.other["user_id"]
        )
        task_api = _TaskApi(
            {
                "owned-task": {
                    "id": "owned-task",
                    "workspace_id": self.workspace["workspace_id"],
                },
                "other-task": {
                    "id": "other-task",
                    "workspace_id": self.other_workspace["workspace_id"],
                },
            }
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
            auth_required=True,
        )
        app.state.task_api = task_api
        self.client = TestClient(app)
        self.owner_headers = self._login("owner@example.com")
        self.other_headers = self._login("other@example.com")

    def _login(self, email):
        result = self.client.post(
            "/auth/login",
            json={"email": email, "password": "safe-passphrase"},
        ).json()
        return {"Authorization": "Bearer " + result["access_token"]}

    def test_me_and_user_routes_require_current_principal(self):
        self.assertEqual(401, self.client.get("/auth/me").status_code)
        response = self.client.get("/auth/me", headers=self.owner_headers)
        self.assertEqual(self.owner["user_id"], response.json()["user_id"])
        self.assertEqual(
            403,
            self.client.get(
                "/users/" + self.other["user_id"], headers=self.owner_headers
            ).status_code,
        )

    def test_workspace_collection_is_filtered_to_current_memberships(self):
        response = self.client.get("/workspaces", headers=self.owner_headers)
        ids = {item["workspace_id"] for item in response.json()["items"]}
        self.assertEqual({self.workspace["workspace_id"]}, ids)
        self.assertNotIn(self.other_workspace["workspace_id"], ids)

    def test_task_list_and_control_enforce_workspace_context(self):
        self.assertEqual(
            400,
            self.client.get("/tasks", headers=self.owner_headers).status_code,
        )
        response = self.client.get(
            "/tasks",
            params={"workspace_id": self.workspace["workspace_id"]},
            headers=self.owner_headers,
        )
        self.assertEqual(["owned-task"], [item["task_id"] for item in response.json()["items"]])
        self.assertEqual(
            403,
            self.client.post(
                "/tasks/other-task/cancel", headers=self.owner_headers
            ).status_code,
        )
        self.assertEqual(
            200,
            self.client.post(
                "/tasks/owned-task/retry", headers=self.owner_headers
            ).status_code,
        )

    def test_authorization_header_is_not_reflected(self):
        response = self.client.get(
            "/tasks",
            params={"workspace_id": self.other_workspace["workspace_id"]},
            headers=self.owner_headers,
        )
        self.assertEqual(403, response.status_code)
        self.assertNotIn(self.owner_headers["Authorization"], response.text)


if __name__ == "__main__":
    unittest.main()
