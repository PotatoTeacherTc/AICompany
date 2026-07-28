from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from typing import Any
import uuid


class MissionState:
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    ALL = {PENDING, IN_PROGRESS, COMPLETED, FAILED, CANCELLED}
    TERMINAL = {COMPLETED, FAILED, CANCELLED}
    TRANSITIONS = {
        PENDING: {IN_PROGRESS, CANCELLED},
        IN_PROGRESS: TERMINAL,
        COMPLETED: set(),
        FAILED: set(),
        CANCELLED: set(),
    }


@dataclass(frozen=True)
class Mission:
    id: str
    title: str
    objective: str
    requested_by: str
    workspace_id: str
    created_at: str
    metadata: dict[str, Any] = field(default_factory=dict)
    state: str = MissionState.PENDING
    locked_by: str | None = None
    locked_at: str | None = None

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
        if self.state not in MissionState.ALL:
            raise ValueError("invalid mission state")
        if (self.locked_by is None) != (self.locked_at is None):
            raise ValueError("mission lock owner and timestamp must be set together")
        if self.locked_by is not None:
            if not isinstance(self.locked_by, str) or not self.locked_by.strip():
                raise ValueError("locked_by must be a non-empty string")
            self._validate_timestamp(self.locked_at, "locked_at")

        self._validate_timestamp(self.created_at, "created_at")

        object.__setattr__(self, "id", self.id.strip())
        object.__setattr__(self, "title", self.title.strip())
        object.__setattr__(self, "objective", self.objective.strip())
        object.__setattr__(self, "requested_by", self.requested_by.strip())
        object.__setattr__(self, "workspace_id", self.workspace_id.strip())
        object.__setattr__(self, "metadata", dict(self.metadata))
        if self.locked_by is not None:
            object.__setattr__(self, "locked_by", self.locked_by.strip())

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

    @property
    def is_locked(self):
        return self.locked_by is not None

    @property
    def is_terminal(self):
        return self.state in MissionState.TERMINAL

    def transition_to(self, state):
        if state == self.state:
            return self
        if state not in MissionState.ALL:
            raise ValueError("invalid mission state")
        if state not in MissionState.TRANSITIONS[self.state]:
            raise ValueError("invalid mission state transition")
        return replace(self, state=state)

    def acquire_lock(self, owner):
        owner = self._validate_owner(owner)
        if self.locked_by == owner:
            return self
        if self.is_locked:
            raise ValueError("mission is already locked")
        return replace(
            self,
            locked_by=owner,
            locked_at=datetime.now(timezone.utc).isoformat(),
        )

    def release_lock(self, owner):
        owner = self._validate_owner(owner)
        if not self.is_locked:
            return self
        if self.locked_by != owner:
            raise ValueError("mission lock is owned by another worker")
        return replace(self, locked_by=None, locked_at=None)

    @staticmethod
    def _validate_owner(owner):
        if not isinstance(owner, str) or not owner.strip():
            raise ValueError("lock owner must be a non-empty string")
        return owner.strip()

    @staticmethod
    def _validate_timestamp(value, field_name):
        try:
            timestamp = datetime.fromisoformat(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{field_name} must be a valid ISO timestamp") from error
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError(f"{field_name} must include timezone information")
