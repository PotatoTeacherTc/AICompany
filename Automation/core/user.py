from dataclasses import asdict, dataclass
from datetime import datetime
import uuid

@dataclass(frozen=True)
class User:
    user_id: str
    email: str
    created_at: str

    @classmethod
    def create(cls, email):
        if not isinstance(email, str) or not email.strip() or "@" not in email:
            raise ValueError("email must be valid")
        return cls(uuid.uuid4().hex, email.strip().lower(), datetime.now().isoformat())

    def to_dict(self):
        return asdict(self)
