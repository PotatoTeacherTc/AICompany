class PlatformAdminService:
    """Explicit platform operations, separate from Workspace membership roles."""

    def __init__(
        self,
        admin_user_ids,
        workspace_service,
        user_service,
        subscription_service=None,
        billing_service=None,
        usage_service=None,
        quota_service=None,
        job_service=None,
        audit_query_service=None,
        plan_service=None,
        audit_service=None,
    ):
        values = set(admin_user_ids or ())
        if any(not isinstance(value, str) or not value for value in values):
            raise ValueError("invalid_platform_admin")
        self.admin_user_ids = frozenset(values)
        self.workspaces = workspace_service
        self.users = user_service
        self.subscriptions = subscription_service
        self.billing = billing_service
        self.usage = usage_service
        self.quota = quota_service
        self.jobs = job_service
        self.audit_query = audit_query_service
        self.plans = plan_service
        self.audit = audit_service

    def is_admin(self, user_id):
        return user_id in self.admin_user_ids

    def list_workspaces(self):
        return {"items": self.workspaces.list()}

    def get_workspace(self, workspace_id):
        return self.workspaces.get(workspace_id)

    def list_users(self):
        return {"items": self.users.list()}

    def get_user(self, user_id):
        return self.users.get(user_id)

    def plans_catalog(self):
        return self.plans.list() if self.plans else {"items": []}

    def workspace_operations(self, workspace_id):
        workspace = self.workspaces.get(workspace_id)
        if workspace is None:
            return None
        jobs = (
            self.jobs.list_jobs(workspace_id, status="FAILED", limit=100)
            if self.jobs else {"items": [], "total": 0}
        )
        return {
            "workspace": workspace,
            "subscription": (
                self.subscriptions.current(workspace_id)
                if self.subscriptions else None
            ),
            "invoices": (
                self.billing.invoices(workspace_id)
                if self.billing else {"items": []}
            ),
            "usage": (
                self.usage.summary(workspace_id) if self.usage else None
            ),
            "quota": self.quota.get(workspace_id) if self.quota else None,
            "failed_jobs": jobs,
            "audit": (
                self.audit_query.query(workspace_id, limit=50)
                if self.audit_query else {"items": []}
            ),
        }

    def set_workspace_status(self, workspace_id, status):
        value = self.workspaces.get(workspace_id)
        if value is None:
            raise KeyError("workspace_not_found")
        result = self.workspaces.update(
            workspace_id,
            status=status,
            expected_revision=value["revision"],
        )
        self._audit(
            workspace_id, "PLATFORM_WORKSPACE_STATUS_CHANGED",
            "workspace", workspace_id, {"status": status}
        )
        return result

    def change_subscription_plan(self, workspace_id, plan_id):
        if self.subscriptions is None:
            raise ValueError("subscription_unavailable")
        result = self.subscriptions.change_plan(
            workspace_id, {"plan_id": plan_id}
        )
        self._audit(
            workspace_id, "PLATFORM_SUBSCRIPTION_PLAN_CHANGED",
            "subscription", result["subscription_id"], {"plan_id": plan_id}
        )
        return result

    def retry_failed_job(self, workspace_id, job_id):
        if self.jobs is None:
            raise ValueError("job_service_unavailable")
        job = self.jobs.get_job(workspace_id, job_id)
        if job is None:
            raise KeyError("job_not_found")
        if job["status"] != "FAILED":
            raise ValueError("job_not_failed")
        result = self.jobs.retry(workspace_id, job_id)
        self._audit(
            workspace_id, "PLATFORM_JOB_RETRIED", "job", job_id, {}
        )
        return result

    def void_invoice(self, workspace_id, invoice_id):
        if self.billing is None:
            raise ValueError("billing_unavailable")
        result = self.billing.manager.void_invoice(workspace_id, invoice_id)
        self._audit(
            workspace_id, "PLATFORM_INVOICE_VOIDED",
            "invoice", invoice_id, {"status": "VOID"}
        )
        return result

    def record_fake_payment(self, workspace_id, invoice_id, payload):
        if self.billing is None:
            raise ValueError("billing_unavailable")
        if not isinstance(payload, dict) or payload.get("provider") != "FAKE":
            raise ValueError("fake_payment_required")
        result = self.billing.pay(workspace_id, invoice_id, payload)
        self._audit(
            workspace_id, "PLATFORM_FAKE_PAYMENT_RECORDED",
            "invoice", invoice_id, {"status": result["status"]}
        )
        return result

    def _audit(
        self, workspace_id, action, resource_type, resource_id, metadata
    ):
        if self.audit:
            self.audit.record(
                workspace_id=workspace_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                metadata=metadata,
            )
