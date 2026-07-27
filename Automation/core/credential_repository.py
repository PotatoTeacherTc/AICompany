import json
import os
from abc import ABC, abstractmethod
from pathlib import Path


class CredentialRepository(ABC):
    @abstractmethod
    def save(self, credential):
        pass

    @abstractmethod
    def get(self, user_id):
        pass


class InMemoryCredentialRepository(CredentialRepository):
    def __init__(self, credentials=None):
        self.items = {item["user_id"]: dict(item) for item in credentials or []}

    def save(self, credential):
        self.items[credential["user_id"]] = dict(credential)

    def get(self, user_id):
        item = self.items.get(user_id)
        return dict(item) if item else None


class FileCredentialRepository(InMemoryCredentialRepository):
    def __init__(self, repository_file):
        self.repository_file = Path(repository_file)
        self.repository_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(self.repository_file.read_text(encoding="utf-8")) if self.repository_file.exists() else []
        except (OSError, json.JSONDecodeError):
            data = []
        super().__init__(data if isinstance(data, list) else [])

    def save(self, credential):
        super().save(credential)
        temporary = self.repository_file.with_suffix(self.repository_file.suffix + ".tmp")
        temporary.write_text(
            json.dumps(list(self.items.values()), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, self.repository_file)
