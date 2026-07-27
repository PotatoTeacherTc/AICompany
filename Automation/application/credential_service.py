from core.credential_repository import InMemoryCredentialRepository
from core.password_hasher import PBKDF2PasswordHasher


class CredentialService:
    """Owns password verification while keeping credentials outside User records."""

    def __init__(self, user_service, repository=None, password_hasher=None):
        self.user_service = user_service
        self.repository = repository or InMemoryCredentialRepository()
        self.password_hasher = password_hasher or PBKDF2PasswordHasher()

    def set_password(self, user_id, password):
        if self.user_service.get(user_id) is None:
            raise KeyError("user_not_found")
        self.repository.save({"user_id": user_id, "password_hash": self.password_hasher.hash(password)})

    def verify_password(self, user_id, password):
        credential = self.repository.get(user_id)
        return bool(credential and self.password_hasher.verify(password, credential.get("password_hash")))
