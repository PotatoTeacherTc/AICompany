from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
import uuid


@dataclass(frozen=True)
class Mission:
    id: str
    title: str
    objective: str
    requested_by: str
    workspace_id: str
    created_at: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        for field_name in (
            "id",
            "title",
            "objective",
            "requested_by",
            "workspace_id",
            "created_at",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        if not isinstance(self.metadata, dict):
            raise ValueError("metadata must be a dictionary")

        try:
            created_at = datetime.fromisoformat(self.created_at)
        except ValueError as error:
            raise ValueError("created_at must be a valid ISO timestamp") from error
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise ValueError("created_at must include timezone information")

        object.__setattr__(self, "id", self.id.strip())
        object.__setattr__(self, "title", self.title.strip())
        object.__setattr__(self, "objective", self.objective.strip())
        object.__setattr__(self, "requested_by", self.requested_by.strip())
        object.__setattr__(self, "workspace_id", self.workspace_id.strip())
        object.__setattr__(self, "metadata", dict(self.metadata))

    @classmethod
    def create(
        cls,
        title,
        objective,
        requested_by,
        workspace_id,
        metadata=None,
    ):
        return cls(
            id=uuid.uuid4().hex,
            title=title,
            objective=objective,
            requested_by=requested_by,
            workspace_id=workspace_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            metadata={} if metadata is None else metadata,
        )

    def to_dict(self):
        return asdict(self)
