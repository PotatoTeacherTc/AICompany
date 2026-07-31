class BillingApplicationService:
    def __init__(self, manager, audit_service=None):
        self.manager = manager
        self.audit = audit_service

    def account(self, workspace_id):
        return self.manager.get_account(workspace_id)

    def update_account(self, workspace_id, payload):
        self._exact(payload, {"billing_email", "currency"}, {"billing_email"})
        value = self.manager.upsert_account(workspace_id, **payload)
        self._audit(workspace_id, "BILLING_ACCOUNT_UPDATED", value)
        return value

    def prices(self, workspace_id=None):
        return {"items": self.manager.list_prices()}

    def invoices(self, workspace_id):
        return {"items": self.manager.list_invoices(workspace_id)}

    def invoice(self, workspace_id, invoice_id):
        return self.manager.get_invoice(workspace_id, invoice_id)

    def create_invoice(self, workspace_id, payload):
        self._exact(
            payload,
            {"price_id", "period_start", "period_end", "due_at"},
            {"price_id", "period_start", "period_end"},
        )
        value = self.manager.create_invoice(workspace_id, **payload)
        self._audit(workspace_id, "INVOICE_CREATED", value)
        return value

    def pay(self, workspace_id, invoice_id, payload):
        self._exact(
            payload, {"provider", "status", "amount", "currency"},
            {"provider", "status", "amount", "currency"},
        )
        value = self.manager.record_payment(
            workspace_id, invoice_id, **payload
        )
        self._audit(workspace_id, "MANUAL_PAYMENT_RECORDED", value)
        return value

    @staticmethod
    def _exact(value, allowed, required):
        if (
            not isinstance(value, dict)
            or not set(value).issubset(allowed)
            or not required.issubset(value)
        ):
            raise ValueError("invalid_billing_request")

    def _audit(self, workspace_id, action, value):
        if self.audit:
            self.audit.record(
                workspace_id=workspace_id,
                action=action,
                resource_type=(
                    "invoice" if "invoice_id" in value else "billing_account"
                ),
                resource_id=value.get("invoice_id")
                or value.get("billing_account_id", ""),
                metadata={
                    "status": value.get("status"),
                    "currency": value.get("currency"),
                },
            )
