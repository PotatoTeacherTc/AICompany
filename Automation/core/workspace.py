from dataclasses import asdict, dataclass
from datetime import datetime
import uuid

DEFAULT_WORKSPACE_ID = "default"
ACTIVE = "ACTIVE"
INACTIVE = "INACTIVE"
WORKSPACE_STATUSES = {ACTIVE, INACTIVE}


@dataclass(frozen=True)
class Workspace:
    workspace_id: str
    name: str
    created_at: str
    status: str = ACTIVE
    updated_at: str | None = None
    revision: int = 0

    @classmethod
    def create(cls, name):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("workspace name must be a non-empty string")
        now = datetime.now().isoformat()
        return cls(uuid.uuid4().hex, name.strip(), now, ACTIVE, now, 0)

    @classmethod
    def default(cls):
        return cls(DEFAULT_WORKSPACE_ID, "Default Workspace", "", ACTIVE, "", 0)

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            raise ValueError("invalid_workspace")
        status = value.get("status", ACTIVE)
        revision = value.get("revision", 0)
        if (
            status not in WORKSPACE_STATUSES
            or not isinstance(revision, int)
            or isinstance(revision, bool)
            or revision < 0
        ):
            raise ValueError("invalid_workspace")
        return cls(
            value["workspace_id"],
            value["name"],
            value["created_at"],
            status,
            value.get("updated_at") or value["created_at"],
            revision,
        )

    def update(self, name=None, status=None):
        next_name = self.name if name is None else name
        next_status = self.status if status is None else status
        if not isinstance(next_name, str) or not next_name.strip():
            raise ValueError("invalid_workspace")
        if next_status not in WORKSPACE_STATUSES:
            raise ValueError("invalid_workspace")
        return Workspace(
            self.workspace_id,
            next_name.strip(),
            self.created_at,
            next_status,
            datetime.now().isoformat(),
            self.revision + 1,
        )

    def to_dict(self):
        return asdict(self)
