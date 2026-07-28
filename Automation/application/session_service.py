import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from core.session_repository import InMemorySessionRepository

class SessionService:
    def __init__(self, repository=None, lifetime_seconds=86400, now=None):
        self.repository = repository or InMemorySessionRepository()
        self.lifetime_seconds = lifetime_seconds
        self.now = now or (lambda: datetime.now(timezone.utc))

    def create(self, user_id):
        token = secrets.token_urlsafe(32)
        current = self._now()
        session = {
            "session_id": secrets.token_hex(16),
            "user_id": user_id,
            "refresh_token_hash": self._hash(token),
            "created_at": current.isoformat(),
            "expires_at": (current + timedelta(seconds=self.lifetime_seconds)).isoformat(),
            "revoked": False,
            "revision": 0,
        }
        self.repository.save(session)
        return session, token

    def rotate(self, token):
        if not isinstance(token, str) or not token:
            return None
        session = self.repository.find_by_refresh_digest(self._hash(token))
        if not self.is_active(session):
            return None
        replacement = secrets.token_urlsafe(32)
        updated = dict(session)
        expected_revision = session.get("revision", 0)
        updated["refresh_token_hash"] = self._hash(replacement)
        updated["revision"] = expected_revision + 1
        updated["rotated_at"] = self._now().isoformat()
        if not self.repository.save_if_revision(updated, expected_revision):
            return None
        return updated, replacement

    def revoke(self, session_id, user_id):
        session = self.repository.get(session_id)
        if not session or session["user_id"] != user_id:
            return False
        if session["revoked"]:
            return True
        session["revoked"] = True
        session["revision"] = session.get("revision", 0) + 1
        session["revoked_at"] = self._now().isoformat()
        self.repository.save(session)
        return True

    def revoke_all(self, user_id):
        count = 0
        for session in self.repository.list_by_user(user_id):
            if not session["revoked"]:
                self.revoke(session["session_id"], user_id)
                count += 1
        return count

    def is_active(self, session):
        if not session or session.get("revoked"):
            return False
        try:
            expires_at = datetime.fromisoformat(session["expires_at"])
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            return expires_at > self._now()
        except (KeyError, TypeError, ValueError):
            return False

    def get_active(self, session_id, user_id):
        session = self.repository.get(session_id)
        if not session or session.get("user_id") != user_id or not self.is_active(session):
            return None
        return session

    def list(self, user_id):
        return [
            {key: value for key, value in session.items() if key != "refresh_token_hash"}
            for session in self.repository.list_by_user(user_id)
        ]

    def _now(self):
        value = self.now()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value

    @staticmethod
    def _hash(token):
        return hashlib.sha256(token.encode()).hexdigest()
