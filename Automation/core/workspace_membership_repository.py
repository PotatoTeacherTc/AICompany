import json
import os
from abc import ABC, abstractmethod
from pathlib import Path


class WorkspaceMembershipRepository(ABC):
    @abstractmethod
    def save(self, membership):
        pass

    @abstractmethod
    def get(self, workspace_id, user_id):
        pass

    @abstractmethod
    def list_by_workspace(self, workspace_id):
        pass
    @abstractmethod
    def list_by_user(self, user_id):
        pass

    @abstractmethod
    def delete(self, workspace_id, user_id):
        pass


class InMemoryWorkspaceMembershipRepository(WorkspaceMembershipRepository):
    def __init__(self, memberships=None):
        self.items = {
            (item["workspace_id"], item["user_id"]): dict(item)
            for item in memberships or []
        }

    def save(self, membership):
        key = (membership["workspace_id"], membership["user_id"])
        self.items[key] = dict(membership)

    def get(self, workspace_id, user_id):
        item = self.items.get((workspace_id, user_id))
        return dict(item) if item else None

    def list_by_workspace(self, workspace_id):
        return [
            dict(item) for item in self.items.values()
            if item["workspace_id"] == workspace_id
        ]

    def list_by_user(self, user_id):
        return [
            dict(item) for item in self.items.values()
            if item["user_id"] == user_id
        ]

    def delete(self, workspace_id, user_id):
        return self.items.pop((workspace_id, user_id), None) is not None


class FileWorkspaceMembershipRepository(InMemoryWorkspaceMembershipRepository):
    def __init__(self, repository_file):
        self.repository_file = Path(repository_file)
        self.repository_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(self.repository_file.read_text(encoding="utf-8")) if self.repository_file.exists() else []
        except (OSError, json.JSONDecodeError):
            data = []
        super().__init__(data if isinstance(data, list) else [])

    def save(self, membership):
        super().save(membership)
        self._write()

    def delete(self, workspace_id, user_id):
        deleted = super().delete(workspace_id, user_id)
        if deleted:
            self._write()
        return deleted

    def _write(self):
        temporary = self.repository_file.with_suffix(self.repository_file.suffix + ".tmp")
        temporary.write_text(
            json.dumps(list(self.items.values()), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, self.repository_file)
