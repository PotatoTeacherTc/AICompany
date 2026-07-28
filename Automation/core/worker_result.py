from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from core.status import PipelineStatus
from core.worker_context import WorkerContext


WORKER_RESULT_STATUSES = {
    PipelineStatus.SUCCESS,
    PipelineStatus.FAILED,
    PipelineStatus.NOT_IMPLEMENTED,
    PipelineStatus.CANCELLED,
    PipelineStatus.TIMED_OUT,
}

USAGE_FIELDS = {
    "provider",
    "model",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "estimated_cost_usd",
}


@dataclass(frozen=True)
class WorkerResult:
    status: str
    worker: str
    mission_id: str
    workspace_id: str
    created_at: str
    data: dict[str, Any] = field(default_factory=dict)
    artifacts: tuple[dict[str, Any], ...] = ()
    usage: dict[str, Any] | None = None
    error: str | None = None

    def __post_init__(self):
        if self.status not in WORKER_RESULT_STATUSES:
            raise ValueError("invalid worker result status")
        for field_name in ("worker", "mission_id", "workspace_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
            object.__setattr__(self, field_name, value.strip())
        self._validate_timestamp(self.created_at)
        if not isinstance(self.data, dict):
            raise ValueError("data must be a dictionary")
        if not isinstance(self.artifacts, (list, tuple)) or not all(
            isinstance(artifact, dict) for artifact in self.artifacts
        ):
            raise ValueError("artifacts must contain dictionaries")
        if self.usage is not None:
            if not isinstance(self.usage, dict):
                raise ValueError("usage must be a dictionary")
            unknown_fields = set(self.usage) - USAGE_FIELDS
            if unknown_fields:
                raise ValueError("usage contains unsupported fields")
        if self.error is not None and not isinstance(self.error, str):
            raise ValueError("error must be a string")

        object.__setattr__(self, "data", dict(self.data))
        object.__setattr__(
            self, "artifacts", tuple(dict(artifact) for artifact in self.artifacts)
        )
        if self.usage is not None:
            object.__setattr__(self, "usage", dict(self.usage))

    @classmethod
    def create(
        cls,
        status,
        worker,
        context,
        data=None,
        artifacts=None,
        usage=None,
        error=None,
    ):
        if not isinstance(context, WorkerContext):
            raise ValueError("context must use the WorkerContext contract")
        return cls(
            status=status,
            worker=worker,
            mission_id=context.mission_id,
            workspace_id=context.workspace_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            data={} if data is None else data,
            artifacts=() if artifacts is None else tuple(artifacts),
            usage=usage,
            error=error,
        )

    def to_dict(self):
        result = asdict(self)
        result["artifacts"] = [dict(artifact) for artifact in self.artifacts]
        return result

    @staticmethod
    def _validate_timestamp(value):
        try:
            timestamp = datetime.fromisoformat(value)
        except (TypeError, ValueError) as error:
            raise ValueError("created_at must be a valid ISO timestamp") from error
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("created_at must include timezone information")
