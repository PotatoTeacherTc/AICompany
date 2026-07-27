import base64
import hashlib
import hmac
import json
import secrets
import time
from abc import ABC, abstractmethod


class AccessTokenProvider(ABC):
    @abstractmethod
    def issue(self, user_id):
        pass

    @abstractmethod
    def verify(self, token):
        pass


class SignedAccessTokenProvider(AccessTokenProvider):
    """Small signed token provider with no token persistence or JWT dependency."""

    def __init__(self, secret=None, expires_in_seconds=3600, clock=None):
        self.secret = secret.encode("utf-8") if isinstance(secret, str) else (secret or secrets.token_bytes(32))
        self.expires_in_seconds = expires_in_seconds
        self.clock = clock or time.time

    def issue(self, user_id):
        if not isinstance(user_id, str) or not user_id:
            raise ValueError("invalid_user")
        payload = {"user_id": user_id, "exp": int(self.clock()) + self.expires_in_seconds}
        encoded = self._encode(payload)
        signature = hmac.new(self.secret, encoded.encode("ascii"), hashlib.sha256).hexdigest()
        return "{}.{}".format(encoded, signature)

    def verify(self, token):
        if not isinstance(token, str):
            return None
        try:
            encoded, signature = token.split(".")
            expected = hmac.new(self.secret, encoded.encode("ascii"), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, signature):
                return None
            payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
            if not isinstance(payload.get("user_id"), str) or payload.get("exp", 0) <= self.clock():
                return None
            return {"user_id": payload["user_id"]}
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    @staticmethod
    def _encode(payload):
        return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("ascii").rstrip("=")
