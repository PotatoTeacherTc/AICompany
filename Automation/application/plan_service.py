class PlanApplicationService:
    def __init__(self, plan_manager, quota_engine=None):
        self.manager = plan_manager
        self.quota = quota_engine

    def list(self):
        return {"items": self.manager.list_plans()}

    def current(self, workspace_id):
        value = self.manager.current(workspace_id)
        value["quota"] = self.quota.status(workspace_id) if self.quota else None
        return value

    def entitlements(self, workspace_id):
        return {
            "workspace_id": workspace_id,
            "entitlements": self.manager.entitlements(workspace_id),
        }

    def assign(self, workspace_id, payload):
        if not isinstance(payload, dict) or set(payload) != {"plan_id"}:
            raise ValueError("invalid plan request")
        return self.manager.assign(workspace_id, payload["plan_id"])
