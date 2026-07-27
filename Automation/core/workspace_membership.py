from dataclasses import asdict, dataclass
from datetime import datetime


OWNER = "OWNER"
ADMIN = "ADMIN"
MEMBER = "MEMBER"
MEMBERSHIP_ROLES = {OWNER, ADMIN, MEMBER}


@dataclass(frozen=True)
class WorkspaceMembership:
    workspace_id: str
    user_id: str
    role: str
    created_at: str

    @classmethod
    def create(cls, workspace_id, user_id, role=MEMBER):
        if role not in MEMBERSHIP_ROLES:
            raise ValueError("invalid_role")
        return cls(workspace_id, user_id, role, datetime.now().isoformat())

    def to_dict(self):
        return asdict(self)
