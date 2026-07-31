class OnboardingService:
    """Explicit local Beta composition; never creates production seed data."""

    def __init__(self, workspace_service, subscription_service, plan_service=None):
        self.workspaces = workspace_service
        self.subscriptions = subscription_service
        self.plans = plan_service

    def ensure_workspace(self, workspace_id):
        workspace = self.workspaces.get(workspace_id)
        if workspace is None:
            raise KeyError("workspace_not_found")
        subscription = self.subscriptions.current(workspace_id)
        created = subscription is None
        if created:
            subscription = self.subscriptions.create(
                workspace_id, {"plan_id": "FREE", "status": "ACTIVE"}
            )
        return {
            "workspace": workspace,
            "subscription": subscription,
            "plan": (
                self.plans.current(workspace_id)
                if self.plans else {"plan_id": subscription["plan_id"]}
            ),
            "created": created,
            "mode": "LOCAL_FAKE_OFFLINE",
        }
