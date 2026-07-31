from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Plan:
    plan_id: str
    name: str
    description: str
    is_active: bool
    entitlements: dict

    def to_dict(self):
        return {
            "plan_id": self.plan_id,
            "name": self.name,
            "description": self.description,
            "is_active": self.is_active,
            "entitlements": dict(self.entitlements),
        }


DEFAULT_PLANS = (
    Plan("FREE", "Free", "Offline personal workspace", True, {
        "max_tokens": 100000,
        "max_executions": 100,
        "artifact_archive_enabled": True,
        "batch_enabled": False,
    }),
    Plan("PRO", "Pro", "Expanded non-billing product contract", True, {
        "max_tokens": 1000000,
        "max_executions": 1000,
        "artifact_archive_enabled": True,
        "batch_enabled": True,
    }),
    Plan("BUSINESS", "Business", "Team non-billing product contract", True, {
        "max_tokens": 10000000,
        "max_executions": 10000,
        "artifact_archive_enabled": True,
        "batch_enabled": True,
    }),
)


class PlanManager:
    """Injected plan catalog and persistent Workspace assignment."""

    def __init__(self, repository, plans=None, default_plan_id="FREE", clock=None):
        self.repository = repository
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        values = plans or DEFAULT_PLANS
        self._plans = {plan.plan_id: plan for plan in values}
        if default_plan_id not in self._plans:
            raise ValueError("default plan is invalid")
        self.default_plan_id = default_plan_id

    def list_plans(self):
        return [plan.to_dict() for plan in self._plans.values() if plan.is_active]

    def get_plan(self, plan_id):
        plan = self._plans.get(plan_id)
        return plan.to_dict() if plan else None

    def assign(self, workspace_id, plan_id):
        plan = self._plans.get(plan_id)
        if plan is None:
            raise ValueError("plan_not_found")
        if not plan.is_active:
            raise ValueError("plan_inactive")
        value = {
            "workspace_id": workspace_id,
            "plan_id": plan_id,
            "updated_at": self.clock().isoformat(),
        }
        self.repository.save("plan_assignment", workspace_id, workspace_id, value)
        return self.current(workspace_id)

    def current(self, workspace_id):
        assignment = self.repository.get(
            "plan_assignment", workspace_id, workspace_id
        )
        plan_id = (
            assignment.get("plan_id")
            if isinstance(assignment, dict)
            else self.default_plan_id
        )
        plan = self._plans.get(plan_id)
        if plan is None or not plan.is_active:
            plan = self._plans[self.default_plan_id]
        result = plan.to_dict()
        result["workspace_id"] = workspace_id
        return result

    def entitlements(self, workspace_id):
        return dict(self.current(workspace_id)["entitlements"])

    def quota_defaults(self, workspace_id):
        value = self.entitlements(workspace_id)
        return {
            "workspace_id": workspace_id,
            "token_limit": value.get("max_tokens"),
            "cost_limit": value.get("max_estimated_cost"),
            "execution_limit": value.get("max_executions"),
            "period": "ALL_TIME",
            "enabled": True,
            "source": "PLAN",
        }

    def allows(self, workspace_id, feature):
        return bool(self.entitlements(workspace_id).get(feature, False))
