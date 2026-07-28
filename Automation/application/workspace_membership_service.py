from core.workspace_membership import OWNER, WorkspaceMembership
from core.workspace_membership_repository import InMemoryWorkspaceMembershipRepository


class WorkspaceMembershipService:
    def __init__(self, workspace_service, user_service, repository=None):
        self.workspace_service = workspace_service
        self.user_service = user_service
        self.repository = repository or InMemoryWorkspaceMembershipRepository()

    def create_workspace(self, name, owner_user_id):
        self._require_user(owner_user_id)
        workspace = self.workspace_service.create(name)
        self.add(workspace["workspace_id"], owner_user_id, OWNER)
        return workspace

    def add(self, workspace_id, user_id, role):
        self._require_workspace(workspace_id)
        self._require_user(user_id)
        if self.repository.get(workspace_id, user_id):
            raise ValueError("duplicate_membership")
        membership = WorkspaceMembership.create(workspace_id, user_id, role)
        self.repository.save(membership.to_dict())
        return membership.to_dict()

    def list(self, workspace_id):
        self._require_workspace(workspace_id)
        return self.repository.list_by_workspace(workspace_id)

    def change_role(self, workspace_id, user_id, role):
        membership = self._require_membership(workspace_id, user_id)
        updated = WorkspaceMembership.create(workspace_id, user_id, role).to_dict()
        updated["created_at"] = membership["created_at"]
        if membership["role"] == OWNER and role != OWNER:
            self._require_another_owner(workspace_id, user_id)
        self.repository.save(updated)
        return updated

    def remove(self, workspace_id, user_id):
        membership = self._require_membership(workspace_id, user_id)
        if membership["role"] == OWNER:
            self._require_another_owner(workspace_id, user_id)
        self.repository.delete(workspace_id, user_id)

    def _require_workspace(self, workspace_id):
        if self.workspace_service.get(workspace_id) is None:
            raise KeyError("workspace_not_found")
        if not self.workspace_service.is_active(workspace_id):
            raise ValueError("inactive_workspace")

    def _require_user(self, user_id):
        user = self.user_service.get(user_id)
        if user is None:
            raise KeyError("user_not_found")
        if not self.user_service.is_active(user_id):
            raise ValueError("inactive_user")

    def _require_membership(self, workspace_id, user_id):
        self._require_workspace(workspace_id)
        membership = self.repository.get(workspace_id, user_id)
        if membership is None:
            raise KeyError("membership_not_found")
        return membership

    def _require_another_owner(self, workspace_id, user_id):
        owners = [
            item for item in self.repository.list_by_workspace(workspace_id)
            if item["role"] == OWNER and item["user_id"] != user_id
        ]
        if not owners:
            raise ValueError("last_owner")
