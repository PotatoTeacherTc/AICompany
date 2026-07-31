class SubscriptionApplicationService:
    def __init__(self, manager, audit_service=None):
        self.manager = manager
        self.audit = audit_service

    def current(self, workspace_id):
        return self.manager.current(workspace_id)

    def create(self, workspace_id, payload):
        payload = self._payload(payload, {"plan_id", "status"})
        value = self.manager.create(workspace_id, **payload)
        self._audit(workspace_id, "SUBSCRIPTION_CREATED", value)
        return value

    def change_plan(self, workspace_id, payload):
        payload = self._payload(payload, {"plan_id"}, required={"plan_id"})
        value = self.manager.change_plan(workspace_id, payload["plan_id"])
        self._audit(workspace_id, "SUBSCRIPTION_PLAN_CHANGED", value)
        return value

    def schedule_cancel(self, workspace_id):
        value = self.manager.schedule_cancel(workspace_id)
        self._audit(workspace_id, "SUBSCRIPTION_CANCEL_SCHEDULED", value)
        return value

    def undo_cancel(self, workspace_id):
        value = self.manager.undo_cancel(workspace_id)
        self._audit(workspace_id, "SUBSCRIPTION_CANCEL_UNDONE", value)
        return value

    def transition(self, workspace_id, payload):
        payload = self._payload(payload, {"status"}, required={"status"})
        value = self.manager.transition(workspace_id, payload["status"])
        self._audit(workspace_id, "SUBSCRIPTION_STATUS_CHANGED", value)
        return value

    @staticmethod
    def _payload(value, allowed, required=frozenset()):
        if not isinstance(value, dict) or not set(value).issubset(allowed):
            raise ValueError("invalid_subscription_request")
        if not required.issubset(value):
            raise ValueError("invalid_subscription_request")
        return dict(value)

    def _audit(self, workspace_id, action, value):
        if self.audit:
            self.audit.record(
                workspace_id=workspace_id,
                action=action,
                resource_type="subscription",
                resource_id=value["subscription_id"],
                metadata={"plan_id": value["plan_id"], "status": value["status"]},
            )
