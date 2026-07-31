import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone


INVOICE_STATUSES = {"DRAFT", "OPEN", "PAID", "VOID", "UNCOLLECTIBLE"}
PAYMENT_STATUSES = {"SUCCEEDED", "FAILED", "CANCELLED"}
PAYMENT_PROVIDERS = {"MANUAL", "FAKE"}


@dataclass(frozen=True)
class Price:
    price_id: str
    plan_id: str
    currency: str
    amount: int
    billing_interval: str
    is_active: bool = True
    development_only: bool = True

    def __post_init__(self):
        if not isinstance(self.amount, int) or isinstance(self.amount, bool):
            raise ValueError("amount_must_be_minor_units")
        if self.amount < 0:
            raise ValueError("negative_amount")
        if not (
            isinstance(self.currency, str)
            and len(self.currency) == 3
            and self.currency.isalpha()
            and self.currency.isupper()
        ):
            raise ValueError("invalid_currency")
        if self.billing_interval not in {"MONTH", "YEAR"}:
            raise ValueError("invalid_billing_interval")

    def to_dict(self):
        return asdict(self)


DEVELOPMENT_PRICES = (
    Price("dev-free-month", "FREE", "USD", 0, "MONTH"),
    Price("dev-pro-month", "PRO", "USD", 1000, "MONTH"),
    Price("dev-business-month", "BUSINESS", "USD", 3000, "MONTH"),
)


class BillingManager:
    """Local Manual/Fake billing records; never contacts a payment provider."""

    def __init__(
        self, repository, subscription_manager, prices=None, clock=None
    ):
        self.repository = repository
        self.subscriptions = subscription_manager
        self.prices = {value.price_id: value for value in (
            prices or DEVELOPMENT_PRICES
        )}
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def get_account(self, workspace_id):
        return self.repository.get("billing_account", workspace_id, workspace_id)

    def upsert_account(self, workspace_id, *, billing_email, currency="USD"):
        if not _email(billing_email) or not _currency(currency):
            raise ValueError("invalid_billing_account")
        existing = self.get_account(workspace_id)
        now = self.clock().isoformat()
        value = {
            "billing_account_id": (
                existing["billing_account_id"] if existing else uuid.uuid4().hex
            ),
            "workspace_id": workspace_id,
            "billing_email": billing_email.strip().lower(),
            "currency": currency,
            "status": "ACTIVE",
            "created_at": existing["created_at"] if existing else now,
            "updated_at": now,
            "metadata": {"mode": "DEVELOPMENT_MANUAL"},
        }
        self.repository.save(
            "billing_account", workspace_id, workspace_id, value
        )
        return value

    def list_prices(self):
        return [
            value.to_dict() for value in self.prices.values() if value.is_active
        ]

    def create_invoice(
        self, workspace_id, *, price_id, period_start, period_end, due_at=None
    ):
        subscription = self.subscriptions.current(workspace_id)
        if subscription is None:
            raise ValueError("subscription_not_found")
        price = self.prices.get(price_id)
        if price is None or not price.is_active:
            raise ValueError("price_not_found")
        if price.plan_id != subscription["plan_id"]:
            raise ValueError("price_plan_mismatch")
        account = self.get_account(workspace_id)
        if account is None or account["currency"] != price.currency:
            raise ValueError("currency_mismatch")
        start = _datetime(period_start)
        end = _datetime(period_end)
        if end <= start:
            raise ValueError("invalid_invoice_period")
        key = f"{subscription['subscription_id']}:{start.isoformat()}:{end.isoformat()}"
        for existing in self.repository.list("invoice", workspace_id):
            if existing.get("idempotency_key") == key:
                return existing
        now = self.clock().isoformat()
        invoice_id = uuid.uuid4().hex
        value = {
            "invoice_id": invoice_id,
            "workspace_id": workspace_id,
            "subscription_id": subscription["subscription_id"],
            "status": "OPEN",
            "currency": price.currency,
            "subtotal": price.amount,
            "total": price.amount,
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "issued_at": now,
            "due_at": due_at or end.isoformat(),
            "paid_at": None,
            "line_items": [{
                "price_id": price.price_id,
                "plan_id": price.plan_id,
                "amount": price.amount,
                "quantity": 1,
                "description": "Development-only plan period",
            }],
            "metadata": {"mode": "DEVELOPMENT_MANUAL"},
            "idempotency_key": key,
        }
        self.repository.save("invoice", invoice_id, workspace_id, value)
        return value

    def list_invoices(self, workspace_id):
        return self.repository.list("invoice", workspace_id)

    def get_invoice(self, workspace_id, invoice_id):
        return self.repository.get("invoice", invoice_id, workspace_id)

    def record_payment(
        self, workspace_id, invoice_id, *, provider, status, amount, currency
    ):
        if provider not in PAYMENT_PROVIDERS or status not in PAYMENT_STATUSES:
            raise ValueError("invalid_payment")
        if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
            raise ValueError("invalid_payment_amount")
        invoice = self.get_invoice(workspace_id, invoice_id)
        if invoice is None:
            raise KeyError("invoice_not_found")
        if invoice["status"] == "PAID":
            raise ValueError("invoice_already_paid")
        if invoice["status"] not in {"OPEN", "UNCOLLECTIBLE"}:
            raise ValueError("invoice_not_payable")
        if currency != invoice["currency"]:
            raise ValueError("currency_mismatch")
        if amount != invoice["total"]:
            raise ValueError("payment_mismatch")
        successful = [
            item for item in self.repository.list("payment", workspace_id)
            if item.get("invoice_id") == invoice_id
            and item.get("status") == "SUCCEEDED"
        ]
        if successful:
            raise ValueError("duplicate_payment")
        now = self.clock().isoformat()
        value = {
            "payment_id": uuid.uuid4().hex,
            "invoice_id": invoice_id,
            "workspace_id": workspace_id,
            "provider": provider,
            "status": status,
            "amount": amount,
            "currency": currency,
            "created_at": now,
            "completed_at": now,
            "metadata": {"mode": "DEVELOPMENT_MANUAL"},
        }
        self.repository.save(
            "payment", value["payment_id"], workspace_id, value
        )
        if status == "SUCCEEDED":
            invoice["status"] = "PAID"
            invoice["paid_at"] = now
            self.repository.save("invoice", invoice_id, workspace_id, invoice)
        return value

    def void_invoice(self, workspace_id, invoice_id):
        invoice = self.get_invoice(workspace_id, invoice_id)
        if invoice is None:
            raise KeyError("invoice_not_found")
        if invoice["status"] not in {"DRAFT", "OPEN", "UNCOLLECTIBLE"}:
            raise ValueError("invoice_not_voidable")
        invoice["status"] = "VOID"
        self.repository.save("invoice", invoice_id, workspace_id, invoice)
        return invoice


def _currency(value):
    return isinstance(value, str) and len(value) == 3 and value.isalpha() and value.isupper()


def _email(value):
    return (
        isinstance(value, str)
        and 3 <= len(value) <= 254
        and "@" in value
        and not any(char.isspace() for char in value)
    )


def _datetime(value):
    if not isinstance(value, str):
        raise ValueError("invalid_datetime")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("invalid_datetime") from error
    if result.tzinfo is None:
        raise ValueError("timezone_required")
    return result
