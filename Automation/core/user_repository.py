import json
import os
from abc import ABC, abstractmethod
from pathlib import Path

class UserRepository(ABC):
    @abstractmethod
    def save(self, user):
        pass

    @abstractmethod
    def get(self, user_id):
        pass

    @abstractmethod
    def get_by_email(self, email):
        pass

class InMemoryUserRepository(UserRepository):
    def __init__(self, users=None):
        self.items = {user["user_id"]: dict(user) for user in users or []}

    def save(self, user):
        self.items[user["user_id"]] = dict(user)

    def get(self, user_id):
        return dict(self.items[user_id]) if user_id in self.items else None

    def get_by_email(self, email):
        return next((dict(user) for user in self.items.values() if user["email"] == email), None)

    def list(self):
        return [dict(user) for user in self.items.values()]


class FileUserRepository(InMemoryUserRepository):
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else []
        except (OSError, json.JSONDecodeError):
            data = []
        super().__init__(data if isinstance(data, list) else [])

    def save(self, user):
        super().save(user)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.list(), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)
