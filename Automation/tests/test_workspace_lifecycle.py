import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from application.credential_service import CredentialService
from application.login_service import LoginService
from application.user_service import UserService
from application.workspace_membership_service import WorkspaceMembershipService
from application.workspace_service import WorkspaceService
from core.user_repository import InMemoryUserRepository
from core.workspace import ACTIVE, INACTIVE
from core.workspace_membership_repository import (
    InMemoryWorkspaceMembershipRepository,
)
from core.workspace_repository import (
    FileWorkspaceRepository,
    InMemoryWorkspaceRepository,
)


class WorkspaceLifecycleTests(unittest.TestCase):
    def test_update_revision_deactivate_and_stale_conflict(self):
        service = WorkspaceService(InMemoryWorkspaceRepository())
        workspace = service.create("Initial")
        updated = service.update(
            workspace["workspace_id"],
            name="Updated",
            expected_revision=0,
        )
        self.assertEqual("Updated", updated["name"])
        self.assertEqual(1, updated["revision"])
        with self.assertRaises(ValueError):
            service.update(
                workspace["workspace_id"],
                status=INACTIVE,
                expected_revision=0,
            )
        inactive = service.update(
            workspace["workspace_id"],
            status=INACTIVE,
            expected_revision=1,
        )
        self.assertEqual(INACTIVE, inactive["status"])
        self.assertFalse(service.is_active(workspace["workspace_id"]))

    def test_file_restart_and_legacy_workspace_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "workspaces.json"
            service = WorkspaceService(FileWorkspaceRepository(path))
            workspace = service.create("Persistent")
            service.update(
                workspace["workspace_id"],
                status=INACTIVE,
                expected_revision=0,
            )
            restored = WorkspaceService(
                FileWorkspaceRepository(path)
            ).get(workspace["workspace_id"])
            self.assertEqual(INACTIVE, restored["status"])
            self.assertEqual(1, restored["revision"])

            legacy_path = Path(directory) / "legacy.json"
            legacy_path.write_text(json.dumps([{
                "workspace_id": "legacy",
                "name": "Legacy",
                "created_at": "2025-01-01T00:00:00",
            }]), encoding="utf-8")
            legacy = WorkspaceService(
                FileWorkspaceRepository(legacy_path)
            ).get("legacy")
            self.assertEqual(ACTIVE, legacy["status"])
            self.assertEqual(0, legacy["revision"])

    def test_inactive_workspace_blocks_membership(self):
        users = UserService()
        owner = users.create("owner-workspace@example.com")
        member = users.create("member-workspace@example.com")
        workspaces = WorkspaceService()
        memberships = WorkspaceMembershipService(workspaces, users)
        workspace = memberships.create_workspace("Blocked", owner["user_id"])
        workspaces.update(
            workspace["workspace_id"],
            status=INACTIVE,
            expected_revision=0,
        )
        with self.assertRaises(ValueError):
            memberships.add(workspace["workspace_id"], member["user_id"], "MEMBER")
        with self.assertRaises(ValueError):
            memberships.list(workspace["workspace_id"])

    def test_authenticated_workspace_api_revision_and_inactive_boundary(self):
        users = UserService(InMemoryUserRepository())
        owner = users.create("workspace-api@example.com")
        credentials = CredentialService(users)
        credentials.set_password(owner["user_id"], "safe-passphrase")
        login = LoginService(users, credentials)
        workspaces = WorkspaceService(InMemoryWorkspaceRepository())
        memberships = WorkspaceMembershipService(
            workspaces,
            users,
            InMemoryWorkspaceMembershipRepository(),
        )
        workspace = memberships.create_workspace("API workspace", owner["user_id"])
        client = TestClient(create_app(
            workspace_service=workspaces,
            user_service=users,
            membership_service=memberships,
            credential_service=credentials,
            login_service=login,
            auth_required=True,
        ))
        token = login.login(
            "workspace-api@example.com", "safe-passphrase"
        )["access_token"]
        headers = {
            "Authorization": f"Bearer {token}",
            "Cookie": "private=value",
            "X-Correlation-ID": "workspace_revision_12345678",
        }
        changed = client.patch(
            f"/workspaces/{workspace['workspace_id']}",
            headers=headers,
            json={"name": "Renamed", "expected_revision": 0},
        )
        self.assertEqual(200, changed.status_code)
        stale = client.patch(
            f"/workspaces/{workspace['workspace_id']}",
            headers=headers,
            json={"name": "Stale", "expected_revision": 0},
        )
        self.assertEqual(409, stale.status_code)
        inactive = client.patch(
            f"/workspaces/{workspace['workspace_id']}",
            headers=headers,
            json={"status": INACTIVE, "expected_revision": 1},
        )
        self.assertEqual(200, inactive.status_code)
        self.assertEqual(
            403,
            client.get(
                f"/workspaces/{workspace['workspace_id']}", headers=headers
            ).status_code,
        )
        serialized = repr(changed.json()) + repr(stale.json())
        self.assertNotIn(token, serialized)
        self.assertNotIn("private=value", serialized)


if __name__ == "__main__":
    unittest.main()
