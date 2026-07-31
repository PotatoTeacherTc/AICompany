class QuotaApplicationService:
    def __init__(self, quota_engine):
        self.engine = quota_engine

    def get(self, workspace_id):
        return self.engine.status(workspace_id)

    def update(self, workspace_id, payload):
        if not isinstance(payload, dict):
            raise ValueError("invalid payload")
        allowed = {"token_limit", "cost_limit", "execution_limit", "enabled"}
        if set(payload) - allowed:
            raise ValueError("invalid quota field")
        return self.engine.set_policy(workspace_id, **payload)
