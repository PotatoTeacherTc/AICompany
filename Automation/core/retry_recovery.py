from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from core.status import PipelineStatus


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    backoff_seconds: float = 0.0

    def __post_init__(self):
        if not isinstance(self.max_attempts, int) or self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if not isinstance(self.backoff_seconds, (int, float)) or self.backoff_seconds < 0:
            raise ValueError("backoff_seconds must be non-negative")


@dataclass(frozen=True)
class RetryState:
    max_attempts: int
    current_attempt: int
    retryable: bool
    next_retry_at: str | None
    failure_category: str | None
    last_safe_error: str | None

    def to_dict(self):
        return asdict(self)


class RetryExecutor:
    RETRYABLE = {"timeout", "provider_transient", "history"}
    NON_RETRYABLE = {
        "validation", "workspace", "cost_policy", "authentication", "unknown"
    }

    def __init__(self, policy=None, clock=None):
        self.policy = policy or RetryPolicy()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def execute(self, operation, recovery=None):
        previous = None
        for attempt in range(1, self.policy.max_attempts + 1):
            try:
                result = operation(previous) if recovery else operation()
            except Exception as error:
                result = {
                    "status": PipelineStatus.FAILED,
                    "error": f"TaskError: {type(error).__name__}",
                    "data": {},
                    "artifacts": [],
                }
            category = self.classify(result)
            if result.get("status") == PipelineStatus.SUCCESS:
                return result, RetryState(
                    self.policy.max_attempts, attempt, False, None, None, None
                )
            retryable = category in self.RETRYABLE and attempt < self.policy.max_attempts
            next_at = (
                self.clock() + timedelta(seconds=self.policy.backoff_seconds * attempt)
            ).isoformat() if retryable else None
            state = RetryState(
                self.policy.max_attempts,
                attempt,
                retryable,
                next_at,
                category,
                self._safe_error(result.get("error"), category),
            )
            result.setdefault("data", {})["retry"] = state.to_dict()
            if not retryable:
                return result, state
            previous = result
        return previous, state

    @classmethod
    def classify(cls, result):
        error = str(result.get("error") or "")
        stages = (result.get("data") or {}).get("stages") or {}
        if error == "ContentFlowError":
            error = " ".join(
                str(stage.get("error") or "") for stage in stages.values()
            )
        lowered = error.lower()
        if "timeout" in lowered:
            return "timeout"
        if "history" in lowered:
            return "history"
        if "workspace" in lowered:
            return "workspace"
        if "paid" in lowered or "cost" in lowered:
            return "cost_policy"
        if "auth" in lowered or "credential" in lowered:
            return "authentication"
        if "valueerror" in lowered or "validation" in lowered:
            return "validation"
        if "connection" in lowered or "providererror" in lowered:
            return "provider_transient"
        return "unknown"

    @staticmethod
    def _safe_error(error, category):
        return f"RetryError: {category}" if error else None
