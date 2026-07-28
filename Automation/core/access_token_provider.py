import base64
import hashlib
import hmac
import json
import secrets
import time
from abc import ABC, abstractmethod


class AccessTokenProvider(ABC):
    @abstractmethod
    def issue(self, user_id, session_id=None):
        pass

    @abstractmethod
    def verify(self, token):
        pass


class SignedAccessTokenProvider(AccessTokenProvider):
    """Small signed token provider with no token persistence or JWT dependency."""

    def __init__(
        self,
        secret=None,
        expires_in_seconds=3600,
        clock=None,
        issuer="aicompany",
        audience="aicompany-api",
    ):
        self.secret = secret.encode("utf-8") if isinstance(secret, str) else (secret or secrets.token_bytes(32))
        self.expires_in_seconds = expires_in_seconds
        self.clock = clock or time.time
        self.issuer = issuer
        self.audience = audience

    def issue(self, user_id, session_id=None):
        if not isinstance(user_id, str) or not user_id:
            raise ValueError("invalid_user")
        now = int(self.clock())
        payload = {
            "sub": user_id,
            "iat": now,
            "exp": now + self.expires_in_seconds,
            "typ": "access",
            "ver": 1,
            "iss": self.issuer,
            "aud": self.audience,
        }
        if isinstance(session_id, str) and session_id:
            payload["sid"] = session_id
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
            now = self.clock()
            if (
                not isinstance(payload, dict)
                or not isinstance(payload.get("sub"), str)
                or payload.get("typ") != "access"
                or payload.get("ver") != 1
                or payload.get("iss") != self.issuer
                or payload.get("aud") != self.audience
                or not isinstance(payload.get("iat"), int)
                or not isinstance(payload.get("exp"), int)
                or payload["iat"] > now
                or payload["exp"] <= now
                or (
                    "sid" in payload
                    and (not isinstance(payload["sid"], str) or not payload["sid"])
                )
            ):
                return None
            claims = {"user_id": payload["sub"]}
            if "sid" in payload:
                claims["session_id"] = payload["sid"]
            return claims
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    @staticmethod
    def _encode(payload):
        return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("ascii").rstrip("=")
