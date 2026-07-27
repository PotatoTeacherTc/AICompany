import hashlib
import hmac
import secrets
from abc import ABC, abstractmethod


class PasswordHasher(ABC):
    @abstractmethod
    def hash(self, password):
        pass

    @abstractmethod
    def verify(self, password, password_hash):
        pass


class PBKDF2PasswordHasher(PasswordHasher):
    """Portable password hasher; password values are never persisted directly."""

    algorithm = "sha256"
    iterations = 200_000

    def hash(self, password):
        self._validate_password(password)
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac(self.algorithm, password.encode("utf-8"), salt, self.iterations)
        return "pbkdf2_{0}${1}${2}${3}".format(
            self.algorithm,
            self.iterations,
            salt.hex(),
            digest.hex(),
        )

    def verify(self, password, password_hash):
        if not isinstance(password, str) or not isinstance(password_hash, str):
            return False
        try:
            scheme, iterations, salt_hex, digest_hex = password_hash.split("$")
            if scheme != "pbkdf2_sha256":
                return False
            expected = hashlib.pbkdf2_hmac(
                self.algorithm,
                password.encode("utf-8"),
                bytes.fromhex(salt_hex),
                int(iterations),
            ).hex()
            return hmac.compare_digest(expected, digest_hex)
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _validate_password(password):
        if not isinstance(password, str) or len(password) < 8:
            raise ValueError("invalid_password")
