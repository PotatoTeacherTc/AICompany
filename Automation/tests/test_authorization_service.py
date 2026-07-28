import unittest

from application.authorization_service import AuthorizationService
from application.credential_service import CredentialService
from application.login_service import LoginService
from application.session_service import SessionService
from application.user_service import UserService
from application.workspace_membership_service import WorkspaceMembershipService
from application.workspace_service import WorkspaceService
from core.access_token_provider import SignedAccessTokenProvider
from core.workspace_membership import ADMIN, MEMBER, OWNER


class AuthorizationServiceTests(unittest.TestCase):
    def setUp(self):
        self.users = UserService()
        self.owner = self.users.create("owner@example.com")
        self.member = self.users.create("member@example.com")
        credentials = CredentialService(self.users)
        for user in (self.owner, self.member):
            credentials.set_password(user["user_id"], "safe-passphrase")
        self.sessions = SessionService()
        self.login = LoginService(
            self.users,
            credentials,
            SignedAccessTokenProvider(secret="injected-test-secret"),
            self.sessions,
        )
        self.workspaces = WorkspaceService()
        self.memberships = WorkspaceMembershipService(
            self.workspaces, self.users
        )
        self.workspace = self.memberships.create_workspace(
            "Authorized", self.owner["user_id"]
        )
        self.memberships.add(
            self.workspace["workspace_id"], self.member["user_id"], MEMBER
        )
        self.authorization = AuthorizationService(
            self.login, self.workspaces, self.memberships
        )
        self.owner_token = self.login.login(
            "owner@example.com", "safe-passphrase"
        )["access_token"]
        self.member_token = self.login.login(
            "member@example.com", "safe-passphrase"
        )["access_token"]

    def test_owner_admin_member_policy_uses_current_membership(self):
        workspace_id = self.workspace["workspace_id"]
        owner = self.authorization.authorize_workspace(
            self.owner_token, workspace_id, {OWNER, ADMIN}
        )
        denied_member = self.authorization.authorize_workspace(
            self.member_token, workspace_id, {OWNER, ADMIN}
        )
        self.assertTrue(owner.allowed)
        self.assertEqual(OWNER, owner.role)
        self.assertEqual("permission_denied", denied_member.code)

        self.memberships.change_role(workspace_id, self.member["user_id"], ADMIN)
        promoted = self.authorization.authorize_workspace(
            self.member_token, workspace_id, {OWNER, ADMIN}
        )
        self.assertTrue(promoted.allowed)
        self.assertEqual(ADMIN, promoted.role)

        self.memberships.remove(workspace_id, self.member["user_id"])
        removed = self.authorization.authorize_workspace(
            self.member_token, workspace_id, {OWNER, ADMIN, MEMBER}
        )
        self.assertEqual("permission_denied", removed.code)

    def test_user_workspace_and_session_state_are_rechecked(self):
        workspace_id = self.workspace["workspace_id"]
        self.assertEqual(
            "authentication_required",
            self.authorization.authorize_workspace(
                "bad-token", workspace_id, {MEMBER}
            ).code,
        )

        issued = self.login.login("member@example.com", "safe-passphrase")
        self.sessions.revoke(issued["session_id"], self.member["user_id"])
        self.assertEqual(
            "authentication_required",
            self.authorization.authorize_workspace(
                issued["access_token"], workspace_id, {MEMBER}
            ).code,
        )

        updated = self.workspaces.update(
            workspace_id,
            status="INACTIVE",
            expected_revision=self.workspace["revision"],
        )
        self.assertEqual("INACTIVE", updated["status"])
        self.assertEqual(
            "workspace_inactive",
            self.authorization.authorize_workspace(
                self.owner_token, workspace_id, {OWNER}
            ).code,
        )

    def test_workspace_isolation_and_invalid_policy_are_safe(self):
        other = self.memberships.create_workspace("Other", self.owner["user_id"])
        self.assertEqual(
            "permission_denied",
            self.authorization.authorize_workspace(
                self.member_token, other["workspace_id"], {MEMBER}
            ).code,
        )
        self.assertEqual(
            "workspace_not_found",
            self.authorization.authorize_workspace(
                self.owner_token, "missing", {OWNER}
            ).code,
        )
        self.assertEqual(
            "permission_denied",
            self.authorization.authorize_workspace(
                self.owner_token, self.workspace["workspace_id"], {"ROOT"}
            ).code,
        )


if __name__ == "__main__":
    unittest.main()
