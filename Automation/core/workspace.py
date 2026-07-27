from dataclasses import asdict, dataclass
from datetime import datetime
import uuid

DEFAULT_WORKSPACE_ID = "default"


@dataclass(frozen=True)
class Workspace:
    workspace_id: str
    name: str
    created_at: str

    @classmethod
    def create(cls, name):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("workspace name must be a non-empty string")
        return cls(uuid.uuid4().hex, name.strip(), datetime.now().isoformat())

    @classmethod
    def default(cls):
        return cls(DEFAULT_WORKSPACE_ID, "Default Workspace", "")

    def to_dict(self):
        return asdict(self)
