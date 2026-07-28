from dataclasses import asdict, dataclass
from pathlib import Path
import re

from core.mission import Mission


_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class MissionWorkspace:
    mission_id: str
    workspace_id: str
    path: str

    def to_dict(self):
        return asdict(self)


class MissionWorkspaceManager:
    """Creates isolated mission directories below an injected root."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, mission):
        if not isinstance(mission, Mission):
            raise ValueError("mission must use the Mission contract")
        self._validate_id(mission.workspace_id, "workspace_id")
        self._validate_id(mission.id, "mission_id")
        path = (self.root / mission.workspace_id / mission.id).resolve()
        if self.root not in path.parents:
            raise ValueError("mission workspace must stay within configured root")
        path.mkdir(parents=True, exist_ok=True)
        return MissionWorkspace(mission.id, mission.workspace_id, str(path))

    def resolve_file(self, workspace, relative_path):
        if not isinstance(workspace, MissionWorkspace):
            raise ValueError("workspace must use the MissionWorkspace contract")
        if not isinstance(relative_path, (str, Path)) or not str(relative_path).strip():
            raise ValueError("relative_path must be non-empty")
        workspace_root = Path(workspace.path).resolve()
        target = (workspace_root / relative_path).resolve()
        if workspace_root != target and workspace_root not in target.parents:
            raise ValueError("workspace path escapes mission boundary")
        return target

    @staticmethod
    def _validate_id(value, field_name):
        if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
            raise ValueError(f"{field_name} contains unsupported characters")
