import uuid
from datetime import datetime, timezone


SUBSCRIPTION_STATUSES = {
    "TRIALING", "ACTIVE", "PAST_DUE", "CANCELLED", "EXPIRED"
}
_ACTIVE_STATUSES = {"TRIALING", "ACTIVE", "PAST_DUE"}
_TRANSITIONS = {
    "TRIALING": {"ACTIVE", "PAST_DUE", "CANCELLED", "EXPIRED"},
    "ACTIVE": {"PAST_DUE", "CANCELLED", "EXPIRED"},
    "PAST_DUE": {"ACTIVE", "CANCELLED", "EXPIRED"},
    "CANCELLED": set(),
    "EXPIRED": set(),
}


class SubscriptionManager:
    """Workspace-scoped product lifecycle; it performs no payment operation."""

    def __init__(self, repository, plan_manager, clock=None):
        self.repository = repository
        self.plans = plan_manager
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def create(
        self, workspace_id, plan_id="FREE", *, status="ACTIVE",
        current_period_start=None, current_period_end=None, metadata=None,
    ):
        self._validate_workspace(workspace_id)
        self._validate_status(status)
        if status not in _ACTIVE_STATUSES:
            raise ValueError("invalid_initial_status")
        existing = self.current(workspace_id)
        if existing and existing["status"] in _ACTIVE_STATUSES:
            raise ValueError("active_subscription_exists")
        self._plan(plan_id)
        now = self.clock().isoformat()
        value = {
            "subscription_id": uuid.uuid4().hex,
            "workspace_id": workspace_id,
            "plan_id": plan_id,
            "status": status,
            "current_period_start": current_period_start or now,
            "current_period_end": current_period_end,
            "cancel_at_period_end": False,
            "created_at": now,
            "updated_at": now,
            "metadata": self._safe_metadata(metadata),
        }
        self.repository.save(
            "subscription", workspace_id, workspace_id, value
        )
        self.plans.assign(workspace_id, plan_id)
        return dict(value)

    def current(self, workspace_id):
        self._validate_workspace(workspace_id)
        return self.repository.get("subscription", workspace_id, workspace_id)

    def change_plan(self, workspace_id, plan_id):
        value = self._required(workspace_id)
        if value["status"] not in _ACTIVE_STATUSES:
            raise ValueError("subscription_inactive")
        self._plan(plan_id)
        value["plan_id"] = plan_id
        value["updated_at"] = self.clock().isoformat()
        self._save(value)
        self.plans.assign(workspace_id, plan_id)
        return value

    def schedule_cancel(self, workspace_id):
        value = self._required(workspace_id)
        if value["status"] not in _ACTIVE_STATUSES:
            raise ValueError("subscription_inactive")
        value["cancel_at_period_end"] = True
        value["updated_at"] = self.clock().isoformat()
        return self._save(value)

    def undo_cancel(self, workspace_id):
        value = self._required(workspace_id)
        if value["status"] not in _ACTIVE_STATUSES:
            raise ValueError("subscription_inactive")
        value["cancel_at_period_end"] = False
        value["updated_at"] = self.clock().isoformat()
        return self._save(value)

    def transition(self, workspace_id, status):
        self._validate_status(status)
        value = self._required(workspace_id)
        current = value["status"]
        if status == current:
            return value
        if status not in _TRANSITIONS[current]:
            raise ValueError("invalid_subscription_transition")
        value["status"] = status
        value["updated_at"] = self.clock().isoformat()
        if status in {"CANCELLED", "EXPIRED"}:
            value["cancel_at_period_end"] = False
            self.plans.assign(workspace_id, self.plans.default_plan_id)
        else:
            self.plans.assign(workspace_id, value["plan_id"])
        return self._save(value)

    def _save(self, value):
        self.repository.save(
            "subscription", value["workspace_id"], value["workspace_id"], value
        )
        return dict(value)

    def _required(self, workspace_id):
        value = self.current(workspace_id)
        if value is None:
            raise KeyError("subscription_not_found")
        return value

    def _plan(self, plan_id):
        value = self.plans.get_plan(plan_id)
        if value is None:
            raise ValueError("plan_not_found")
        if not value["is_active"]:
            raise ValueError("plan_inactive")
        return value

    @staticmethod
    def _validate_workspace(workspace_id):
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ValueError("invalid_workspace")

    @staticmethod
    def _validate_status(status):
        if status not in SUBSCRIPTION_STATUSES:
            raise ValueError("invalid_subscription_status")

    @staticmethod
    def _safe_metadata(metadata):
        if metadata is None:
            return {}
        if not isinstance(metadata, dict):
            raise ValueError("invalid_metadata")
        safe = {}
        for key, value in metadata.items():
            lowered = str(key).lower()
            if any(token in lowered for token in (
                "prompt", "objective", "token", "secret", "password", "key"
            )):
                continue
            if isinstance(value, (str, int, float, bool, type(None))):
                safe[str(key)] = value
        return safe
