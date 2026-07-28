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
from core.user import ACTIVE, INACTIVE
from core.user_repository import FileUserRepository, InMemoryUserRepository
from core.workspace_membership import MEMBER


class UserLifecycleTests(unittest.TestCase):
    def test_create_deactivate_and_duplicate_email_contract(self):
        service = UserService()
        user = service.create("person@example.com")
        self.assertEqual(ACTIVE, user["status"])
        with self.assertRaises(ValueError):
            service.create("PERSON@example.com")
        inactive = service.deactivate(user["user_id"])
        self.assertEqual(INACTIVE, inactive["status"])
        self.assertEqual(inactive, service.deactivate(user["user_id"]))
        self.assertNotIn("password", repr(inactive).lower())
        self.assertNotIn("token", repr(inactive).lower())

    def test_file_restart_and_legacy_user_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "users.json"
            service = UserService(FileUserRepository(path))
            user = service.create("restart@example.com")
            service.deactivate(user["user_id"])
            restored = UserService(FileUserRepository(path)).get(user["user_id"])
            self.assertEqual(INACTIVE, restored["status"])

            legacy_path = Path(directory) / "legacy-users.json"
            legacy_path.write_text(json.dumps([{
                "user_id": "legacy-user",
                "email": "legacy@example.com",
                "created_at": "2025-01-01T00:00:00",
            }]), encoding="utf-8")
            legacy = UserService(FileUserRepository(legacy_path)).get("legacy-user")
            self.assertEqual(ACTIVE, legacy["status"])
            self.assertEqual(legacy["created_at"], legacy["updated_at"])

    def test_inactive_user_cannot_login_or_receive_new_membership(self):
        users = UserService()
        owner = users.create("owner@example.com")
        member = users.create("member@example.com")
        credentials = CredentialService(users)
        credentials.set_password(member["user_id"], "safe-passphrase")
        login = LoginService(users, credentials)
        workspaces = WorkspaceService()
        memberships = WorkspaceMembershipService(workspaces, users)
        workspace = memberships.create_workspace("Safe team", owner["user_id"])
        users.deactivate(member["user_id"])
        with self.assertRaises(ValueError):
            login.login("member@example.com", "safe-passphrase")
        with self.assertRaises(ValueError):
            memberships.add(workspace["workspace_id"], member["user_id"], MEMBER)

    def test_authenticated_self_deactivation_api_isolated_and_safe(self):
        users = UserService(InMemoryUserRepository())
        user = users.create("api-user@example.com")
        other = users.create("other@example.com")
        credentials = CredentialService(users)
        credentials.set_password(user["user_id"], "safe-passphrase")
        login = LoginService(users, credentials)
        client = TestClient(create_app(
            user_service=users,
            credential_service=credentials,
            login_service=login,
            auth_required=True,
        ))
        token = login.login("api-user@example.com", "safe-passphrase")[
            "access_token"
        ]
        headers = {
            "Authorization": f"Bearer {token}",
            "Cookie": "private-cookie=value",
            "X-Correlation-ID": "user_lifecycle_12345678",
        }
        denied = client.patch(
            f"/users/{other['user_id']}/deactivate", headers=headers
        )
        self.assertEqual(403, denied.status_code)
        response = client.patch(
            f"/users/{user['user_id']}/deactivate", headers=headers
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual(INACTIVE, response.json()["status"])
        self.assertEqual(
            "user_lifecycle_12345678",
            response.headers["X-Correlation-ID"],
        )
        self.assertEqual(
            401,
            client.get("/users/me", headers=headers).status_code,
        )
        serialized = repr(response.json()) + repr(denied.json())
        self.assertNotIn(token, serialized)
        self.assertNotIn("private-cookie", serialized)
        self.assertNotIn("safe-passphrase", serialized)


if __name__ == "__main__":
    unittest.main()
