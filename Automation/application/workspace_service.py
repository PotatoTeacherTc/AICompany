from core.workspace import DEFAULT_WORKSPACE_ID, Workspace
from core.workspace_repository import InMemoryWorkspaceRepository


class WorkspaceService:
    def __init__(self, repository=None):
        self.repository = repository or InMemoryWorkspaceRepository()
        if self.repository.get(DEFAULT_WORKSPACE_ID) is None: self.repository.save(Workspace.default().to_dict())
    def create(self, name):
        workspace = Workspace.create(name); self.repository.save(workspace.to_dict()); return workspace.to_dict()
    def get(self, workspace_id): return self.repository.get(workspace_id)
    def list(self): return self.repository.list()
