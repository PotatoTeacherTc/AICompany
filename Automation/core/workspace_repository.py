import json
import os
from abc import ABC, abstractmethod
from pathlib import Path


class WorkspaceRepository(ABC):
    @abstractmethod
    def save(self, workspace): pass
    @abstractmethod
    def get(self, workspace_id): pass
    @abstractmethod
    def list(self): pass


class InMemoryWorkspaceRepository(WorkspaceRepository):
    def __init__(self, workspaces=None): self.items = {w["workspace_id"]: dict(w) for w in workspaces or []}
    def save(self, workspace): self.items[workspace["workspace_id"]] = dict(workspace)
    def get(self, workspace_id): return dict(self.items[workspace_id]) if workspace_id in self.items else None
    def list(self): return [dict(item) for item in self.items.values()]


class FileWorkspaceRepository(InMemoryWorkspaceRepository):
    def __init__(self, repository_file):
        self.repository_file = Path(repository_file); self.repository_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(self.repository_file.read_text(encoding="utf-8")) if self.repository_file.exists() else []
        except (OSError, json.JSONDecodeError): data = []
        super().__init__(data if isinstance(data, list) else [])
    def save(self, workspace):
        super().save(workspace); temporary = self.repository_file.with_suffix(self.repository_file.suffix + ".tmp")
        temporary.write_text(json.dumps(self.list(), ensure_ascii=False, indent=2), encoding="utf-8"); os.replace(temporary, self.repository_file)
