from core.workspace import ACTIVE, DEFAULT_WORKSPACE_ID, Workspace
from core.workspace_repository import InMemoryWorkspaceRepository


class WorkspaceService:
    def __init__(self, repository=None):
        self.repository = repository or InMemoryWorkspaceRepository()
        if self.repository.get(DEFAULT_WORKSPACE_ID) is None: self.repository.save(Workspace.default().to_dict())
    def create(self, name):
        workspace = Workspace.create(name); self.repository.save(workspace.to_dict()); return workspace.to_dict()
    def get(self, workspace_id):
        return self._normalize(self.repository.get(workspace_id))
    def list(self):
        return [
            normalized for item in self.repository.list()
            if (normalized := self._normalize(item)) is not None
        ]
    def update(self, workspace_id, *, name=None, status=None, expected_revision=None):
        if name is None and status is None:
            raise ValueError("invalid_workspace")
        value = self.repository.get(workspace_id)
        if value is None:
            raise KeyError("workspace_not_found")
        workspace = Workspace.from_dict(value)
        if (
            not isinstance(expected_revision, int)
            or isinstance(expected_revision, bool)
            or expected_revision != workspace.revision
        ):
            raise ValueError("revision_conflict")
        updated = workspace.update(name=name, status=status)
        self.repository.save(updated.to_dict())
        return updated.to_dict()
    def is_active(self, workspace_id):
        workspace = self.get(workspace_id)
        return bool(workspace and workspace["status"] == ACTIVE)
    @staticmethod
    def _normalize(value):
        if value is None:
            return None
        try:
            return Workspace.from_dict(value).to_dict()
        except (KeyError, TypeError, ValueError):
            return None
