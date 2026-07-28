from dataclasses import asdict, dataclass
from datetime import datetime
import uuid

ACTIVE = "ACTIVE"
INACTIVE = "INACTIVE"
USER_STATUSES = {ACTIVE, INACTIVE}


@dataclass(frozen=True)
class User:
    user_id: str
    email: str
    created_at: str
    status: str = ACTIVE
    updated_at: str | None = None

    @classmethod
    def create(cls, email):
        if not isinstance(email, str) or not email.strip() or "@" not in email:
            raise ValueError("email must be valid")
        now = datetime.now().isoformat()
        return cls(uuid.uuid4().hex, email.strip().lower(), now, ACTIVE, now)

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            raise ValueError("invalid_user")
        status = value.get("status", ACTIVE)
        if status not in USER_STATUSES:
            raise ValueError("invalid_user")
        return cls(
            value["user_id"],
            value["email"],
            value["created_at"],
            status,
            value.get("updated_at") or value["created_at"],
        )

    def deactivate(self):
        if self.status == INACTIVE:
            return self
        return User(
            self.user_id,
            self.email,
            self.created_at,
            INACTIVE,
            datetime.now().isoformat(),
        )

    def to_dict(self):
        return asdict(self)
