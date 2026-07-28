from dataclasses import asdict, dataclass, field
from typing import Any

from core.mission import Mission


SENSITIVE_CONTEXT_KEYS = {
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "token",
    "password",
    "password_hash",
    "prompt",
    "raw_prompt",
}


@dataclass(frozen=True)
class WorkerContext:
    mission_id: str
    workspace_id: str
    title: str
    objective: str
    requested_by: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        for field_name in (
            "mission_id",
            "workspace_id",
            "title",
            "objective",
            "requested_by",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
            object.__setattr__(self, field_name, value.strip())
        if not isinstance(self.metadata, dict):
            raise ValueError("metadata must be a dictionary")
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self):
        return asdict(self)


class ContextBuilder:
    """Build the minimum safe, workspace-scoped context needed by a Worker."""

    def build(self, mission):
        if not isinstance(mission, Mission):
            raise ValueError("mission must use the Mission contract")
        safe_metadata = {
            key: value
            for key, value in mission.metadata.items()
            if (
                isinstance(key, str)
                and key.lower() not in SENSITIVE_CONTEXT_KEYS
                and isinstance(value, (str, int, float, bool, type(None)))
            )
        }
        return WorkerContext(
            mission_id=mission.id,
            workspace_id=mission.workspace_id,
            title=mission.title,
            objective=mission.objective,
            requested_by=mission.requested_by,
            metadata=safe_metadata,
        )
