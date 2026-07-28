import json
import os
import threading
from abc import ABC, abstractmethod
from pathlib import Path

class SessionRepository(ABC):
    @abstractmethod
    def save(self, session): pass
    @abstractmethod
    def get(self, session_id): pass
    @abstractmethod
    def list_by_user(self, user_id): pass
    @abstractmethod
    def find_by_refresh_digest(self, digest): pass
    @abstractmethod
    def save_if_revision(self, session, expected_revision): pass

class InMemorySessionRepository(SessionRepository):
    def __init__(self, sessions=None):
        self._lock = threading.Lock()
        self.items = {}
        for session in sessions or []:
            normalized = self._normalize(session)
            if normalized is not None:
                self.items[normalized["session_id"]] = normalized

    def save(self, session):
        normalized = self._normalize(session)
        if normalized is None:
            raise ValueError("invalid_session")
        with self._lock:
            self.items[normalized["session_id"]] = normalized

    def get(self, session_id):
        with self._lock:
            return dict(self.items[session_id]) if session_id in self.items else None

    def list_by_user(self, user_id):
        with self._lock:
            return [dict(s) for s in self.items.values() if s["user_id"] == user_id]

    def find_by_refresh_digest(self, digest):
        with self._lock:
            return next(
                (dict(s) for s in self.items.values() if s["refresh_token_hash"] == digest),
                None,
            )

    def save_if_revision(self, session, expected_revision):
        normalized = self._normalize(session)
        if normalized is None:
            return False
        with self._lock:
            current = self.items.get(normalized["session_id"])
            if current is None or current.get("revision", 0) != expected_revision:
                return False
            self.items[normalized["session_id"]] = normalized
            return True

    @staticmethod
    def _normalize(session):
        if not isinstance(session, dict):
            return None
        required = {"session_id", "user_id", "refresh_token_hash", "created_at", "expires_at"}
        if not required.issubset(session):
            return None
        value = dict(session)
        value["revoked"] = bool(value.get("revoked", False))
        value["revision"] = value.get("revision", 0)
        if not isinstance(value["revision"], int) or value["revision"] < 0:
            return None
        value.pop("rotated", None)
        return value

class FileSessionRepository(InMemorySessionRepository):
    def __init__(self,path):
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
        try: data=json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else []
        except (OSError,json.JSONDecodeError): data=[]
        super().__init__(data if isinstance(data,list) else [])
    def save(self, session):
        super().save(session)
        self._persist()

    def save_if_revision(self, session, expected_revision):
        saved = super().save_if_revision(session, expected_revision)
        if saved:
            self._persist()
        return saved

    def _persist(self):
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(json.dumps(list(self.items.values())), encoding="utf-8")
        os.replace(temp, self.path)
