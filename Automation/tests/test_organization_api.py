import unittest

from fastapi.testclient import TestClient

from api.app import create_app
from application.credential_service import CredentialService
from application.login_service import LoginService
from application.organization_service import OrganizationService
from application.session_service import SessionService
from application.user_service import UserService
from application.workspace_membership_service import WorkspaceMembershipService
from application.workspace_service import WorkspaceService
from core.access_token_provider import SignedAccessTokenProvider
from core.collaboration_worker import FunctionWorker
from core.department import DepartmentManager, WorkerDirectory
from core.persistence import InMemoryStateRepository
from core.status import PipelineStatus
from core.worker_result import WorkerResult
from core.workspace_membership import MEMBER


class _Unused:
    pass


def _worker(name):
    return FunctionWorker(
        name,
        lambda context: WorkerResult.create(
            PipelineStatus.SUCCESS, name, context
        ),
    )


class OrganizationApiTests(unittest.TestCase):
    def setUp(self):
        users = UserService()
        self.owner = users.create("owner@example.com")
        self.member = users.create("member@example.com")
        self.outsider = users.create("outsider@example.com")
        credentials = CredentialService(users)
        for user in (self.owner, self.member, self.outsider):
            credentials.set_password(user["user_id"], "safe-passphrase")
        sessions = SessionService()
        login = LoginService(
            users, credentials,
            SignedAccessTokenProvider(secret="injected-test-secret"), sessions,
        )
        workspaces = WorkspaceService()
        memberships = WorkspaceMembershipService(workspaces, users)
        self.workspace = memberships.create_workspace("Owned", self.owner["user_id"])
        memberships.add(self.workspace["workspace_id"], self.member["user_id"], MEMBER)
        self.foreign = memberships.create_workspace("Other", self.outsider["user_id"])
        self.directory = WorkerDirectory()
        self.directory.register(
            _worker("research-worker"), self.workspace["workspace_id"],
            ("RESEARCH",),
        )
        self.directory.register(
            _worker("foreign-worker"), self.foreign["workspace_id"],
            ("RESEARCH",),
        )
        self.manager = DepartmentManager(
            InMemoryStateRepository(), self.directory, ("RESEARCH", "CONTENT")
        )
        organization = OrganizationService(self.manager, self.directory)
        app = create_app(
            automation_service=_Unused(), task_query_service=_Unused(),
            workspace_service=workspaces, user_service=users,
            membership_service=memberships, credential_service=credentials,
            login_service=login, session_service=sessions,
            organization_service=organization, auth_required=True,
        )
        self.client = TestClient(app)
        self.owner_headers = self._login("owner@example.com")
        self.member_headers = self._login("member@example.com")
        self.outsider_headers = self._login("outsider@example.com")

    def _login(self, email):
        value = self.client.post("/auth/login", json={
            "email": email, "password": "safe-passphrase",
        }).json()
        return {"Authorization": "Bearer " + value["access_token"]}

    def _base(self):
        return f"/workspaces/{self.workspace['workspace_id']}"

    def _department(self):
        return {
            "department_id": "research",
            "name": "Research",
            "safe_summary": "Research offline department",
            "department_type": "RESEARCH",
            "supported_task_types": ["RESEARCH"],
        }

    def test_auth_rbac_and_department_lifecycle(self):
        url = self._base() + "/departments"
        self.assertEqual(401, self.client.get(url).status_code)
        created = self.client.post(url, json=self._department(), headers=self.owner_headers)
        self.assertEqual(201, created.status_code)
        self.assertEqual(200, self.client.get(url, headers=self.member_headers).status_code)
        self.assertEqual(
            403,
            self.client.post(
                url, json={**self._department(), "department_id": "other"},
                headers=self.member_headers,
            ).status_code,
        )
        patched = self.client.patch(
            url + "/research",
            json={"enabled": False, "expected_revision": 0},
            headers=self.owner_headers,
        )
        self.assertFalse(patched.json()["enabled"])
        self.assertEqual(409, self.client.patch(
            url + "/research",
            json={"enabled": True, "expected_revision": 0},
            headers=self.owner_headers,
        ).status_code)

    def test_worker_capabilities_are_read_only_and_safe(self):
        response = self.client.get(
            self._base() + "/workers", headers=self.member_headers
        )
        self.assertEqual(1, len(response.json()["items"]))
        worker = response.json()["items"][0]
        self.assertEqual("research-worker", worker["worker_id"])
        self.assertNotIn("worker", worker)
        paths = {route.path for route in self.client.app.routes}
        self.assertNotIn("/workspaces/{workspace_id}/workers", {
            route.path for route in self.client.app.routes
            if "POST" in getattr(route, "methods", set())
        })

    def test_assignment_removal_duplicate_and_foreign_worker(self):
        departments = self._base() + "/departments"
        self.client.post(departments, json=self._department(), headers=self.owner_headers)
        assignment = departments + "/research/workers"
        assigned = self.client.post(
            assignment,
            json={"worker_id": "research-worker", "expected_revision": 0, "lead": True},
            headers=self.owner_headers,
        )
        self.assertEqual(["research-worker"], assigned.json()["worker_ids"])
        self.assertEqual(409, self.client.post(
            assignment,
            json={"worker_id": "research-worker", "expected_revision": 1},
            headers=self.owner_headers,
        ).status_code)
        self.assertEqual(409, self.client.post(
            assignment,
            json={"worker_id": "foreign-worker", "expected_revision": 1},
            headers=self.owner_headers,
        ).status_code)
        removed = self.client.delete(
            assignment + "/research-worker",
            params={"expected_revision": 1},
            headers=self.owner_headers,
        )
        self.assertEqual([], removed.json()["worker_ids"])

    def test_cross_workspace_is_non_disclosing(self):
        self.client.post(
            self._base() + "/departments",
            json=self._department(), headers=self.owner_headers,
        )
        self.assertEqual(403, self.client.get(
            self._base() + "/departments/research",
            headers=self.outsider_headers,
        ).status_code)
        foreign_url = f"/workspaces/{self.foreign['workspace_id']}/departments/research"
        self.assertEqual(404, self.client.get(
            foreign_url, headers=self.outsider_headers
        ).status_code)

    def test_invalid_sensitive_data_and_missing_targets_are_safe(self):
        url = self._base() + "/departments"
        response = self.client.post(
            url,
            json={
                **self._department(),
                "department_id": "bad",
                "safe_summary": "secret token prompt",
            },
            headers=self.owner_headers,
        )
        self.assertEqual(400, response.status_code)
        self.assertNotIn("secret token prompt", response.text)
        self.assertEqual(404, self.client.get(
            url + "/missing", headers=self.owner_headers
        ).status_code)
        self.assertEqual(404, self.client.get(
            self._base() + "/workers/missing", headers=self.owner_headers
        ).status_code)


if __name__ == "__main__":
    unittest.main()
