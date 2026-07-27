from core.user import User
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
        return self.repository.get(user_id)
