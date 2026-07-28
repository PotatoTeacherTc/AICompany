from dataclasses import dataclass

from core.workspace_membership import MEMBERSHIP_ROLES


@dataclass(frozen=True)
class AuthorizationDecision:
    allowed: bool
    code: str | None = None
    user_id: str | None = None
    role: str | None = None


class AuthorizationService:
    """Evaluates current User, Workspace, Membership, and role state."""

    def __init__(
        self,
        login_service,
        workspace_service,
        membership_service,
    ):
        self.login_service = login_service
        self.workspace_service = workspace_service
        self.membership_service = membership_service

    def authorize_workspace(self, token, workspace_id, allowed_roles):
        if (
            not isinstance(workspace_id, str)
            or not workspace_id
            or not isinstance(allowed_roles, (set, frozenset))
            or not allowed_roles
            or not allowed_roles.issubset(MEMBERSHIP_ROLES)
        ):
            return AuthorizationDecision(False, "permission_denied")

        user = self.login_service.current_user(token)
        if user is None:
            return AuthorizationDecision(False, "authentication_required")
        if self.workspace_service.get(workspace_id) is None:
            return AuthorizationDecision(False, "workspace_not_found", user["user_id"])
        if not self.workspace_service.is_active(workspace_id):
            return AuthorizationDecision(False, "workspace_inactive", user["user_id"])

        membership = self.membership_service.repository.get(
            workspace_id, user["user_id"]
        )
        if membership is None or membership.get("role") not in allowed_roles:
            return AuthorizationDecision(False, "permission_denied", user["user_id"])
        return AuthorizationDecision(
            True,
            user_id=user["user_id"],
            role=membership["role"],
        )
