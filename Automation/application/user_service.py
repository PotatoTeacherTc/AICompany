from core.user import ACTIVE, User
from core.user_repository import InMemoryUserRepository


class UserService:
    def __init__(self, repository=None):
        self.repository = repository or InMemoryUserRepository()

    def create(self, email):
        user=User.create(email)
        if self.repository.get_by_email(user.email):
            raise ValueError("duplicate_email")
        self.repository.save(user.to_dict())
        return user.to_dict()

    def get(self, user_id):
        value = self.repository.get(user_id)
        return self._normalize(value)

    def get_by_email(self, email):
        value = (
            self.repository.get_by_email(email.strip().lower())
            if isinstance(email, str)
            else None
        )
        return self._normalize(value)

    def deactivate(self, user_id):
        value = self.repository.get(user_id)
        if value is None:
            raise KeyError("user_not_found")
        user = User.from_dict(value).deactivate()
        self.repository.save(user.to_dict())
        return user.to_dict()

    def is_active(self, user_id):
        user = self.get(user_id)
        return bool(user and user["status"] == ACTIVE)

    @staticmethod
    def _normalize(value):
        if value is None:
            return None
        try:
            return User.from_dict(value).to_dict()
        except (KeyError, TypeError, ValueError):
            return None
