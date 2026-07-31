from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from threading import RLock


@dataclass(frozen=True)
class QuotaPolicy:
    workspace_id: str
    token_limit: int | None = None
    cost_limit: float | None = None
    execution_limit: int | None = None
    period: str = "ALL_TIME"
    enabled: bool = True
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self):
        return {key: value for key, value in asdict(self).items() if value is not None}


class QuotaEngine:
    """Workspace quota decisions over UsageEngine and shared persistence."""

    def __init__(self, repository, usage_engine, clock=None):
        self.repository = repository
        self.usage_engine = usage_engine
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = RLock()

    def get_policy(self, workspace_id):
        value = self.repository.get("quota", workspace_id, workspace_id)
        if not isinstance(value, dict):
            return None
        result = dict(value)
        if "total_units_limit" in result:
            result["token_limit"] = result.pop("total_units_limit")
        return result

    def set_policy(
        self, workspace_id, *, token_limit=None, cost_limit=None,
        execution_limit=None, enabled=True,
    ):
        limits = {
            "token_limit": _limit(token_limit, integer=True),
            "cost_limit": _limit(cost_limit, integer=False),
            "execution_limit": _limit(execution_limit, integer=True),
        }
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be boolean")
        now = self.clock().isoformat()
        existing = self.get_policy(workspace_id) or {}
        policy = QuotaPolicy(
            workspace_id=workspace_id,
            enabled=enabled,
            created_at=existing.get("created_at", now),
            updated_at=now,
            **limits,
        )
        stored = policy.to_dict()
        if "token_limit" in stored:
            stored["total_units_limit"] = stored.pop("token_limit")
        self.repository.save("quota", workspace_id, workspace_id, stored)
        return policy.to_dict()

    def status(self, workspace_id):
        policy = self.get_policy(workspace_id)
        usage = self.usage_engine.summary(workspace_id)
        executions = len(self.repository.list("quota_reservation", workspace_id))
        result = {
            "workspace_id": workspace_id,
            "policy": policy,
            "usage": usage,
            "execution_count": executions,
            "allowed": True,
        }
        violation = self._violation(policy, usage, executions)
        if violation:
            result.update({"allowed": False, "safe_error": violation})
        return result

    def reserve(self, workspace_id, reservation_id):
        with self._lock:
            existing = self.repository.get(
                "quota_reservation", reservation_id, workspace_id
            )
            if existing is not None:
                return dict(existing)
            status = self.status(workspace_id)
            if not status["allowed"]:
                raise ValueError(status["safe_error"])
            value = {
                "workspace_id": workspace_id,
                "reservation_id": reservation_id,
                "created_at": self.clock().isoformat(),
            }
            self.repository.save(
                "quota_reservation", reservation_id, workspace_id, value
            )
            return value

    def assert_allowed(self, workspace_id):
        status = self.status(workspace_id)
        policy = status["policy"]
        usage = status["usage"]
        violation = self._violation(policy, usage, 0, include_executions=False)
        if violation:
            raise ValueError(violation)
        return status

    @staticmethod
    def _violation(policy, usage, executions, include_executions=True):
        if not policy or not policy.get("enabled", True):
            return None
        checks = (
            ("token_limit", usage.get("total_tokens", 0), "quota_tokens_exceeded"),
            ("cost_limit", usage.get("estimated_cost_usd", 0), "quota_cost_exceeded"),
            ("execution_limit", executions, "quota_executions_exceeded"),
        )
        for field, current, error in checks:
            if field == "execution_limit" and not include_executions:
                continue
            limit = policy.get(field)
            if limit is not None and current >= limit:
                return error
        return None


def _limit(value, *, integer):
    if value is None:
        return None
    expected = int if integer else (int, float)
    if not isinstance(value, expected) or isinstance(value, bool) or value < 0:
        raise ValueError("quota limit must be non-negative")
    return value
