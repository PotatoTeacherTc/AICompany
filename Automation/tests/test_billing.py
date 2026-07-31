import tempfile
import unittest
from pathlib import Path

from application.billing_service import BillingApplicationService
from core.billing import BillingManager, Price
from core.persistence import JsonStateRepository
from core.plans import PlanManager
from core.subscription import SubscriptionManager


class BillingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "state.json"
        self.repository = JsonStateRepository(self.path)
        self.plans = PlanManager(self.repository)
        self.subscriptions = SubscriptionManager(self.repository, self.plans)
        self.subscriptions.create("workspace-a", "PRO")
        self.manager = BillingManager(self.repository, self.subscriptions)
        self.service = BillingApplicationService(self.manager)
        self.manager.upsert_account(
            "workspace-a", billing_email="billing@example.test", currency="USD"
        )
        self.period = {
            "price_id": "dev-pro-month",
            "period_start": "2026-07-01T00:00:00+00:00",
            "period_end": "2026-08-01T00:00:00+00:00",
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_billing_account_and_development_price_contract(self):
        account = self.manager.get_account("workspace-a")
        self.assertEqual("billing@example.test", account["billing_email"])
        price = self.manager.list_prices()[1]
        self.assertIsInstance(price["amount"], int)
        self.assertTrue(price["development_only"])
        self.assertEqual("DEVELOPMENT_MANUAL", account["metadata"]["mode"])

    def test_invoice_is_idempotent_and_uses_integer_minor_units(self):
        first = self.manager.create_invoice("workspace-a", **self.period)
        second = self.manager.create_invoice("workspace-a", **self.period)
        self.assertEqual(first["invoice_id"], second["invoice_id"])
        self.assertEqual(1000, first["total"])
        self.assertIsInstance(first["total"], int)
        self.assertEqual(1, len(self.manager.list_invoices("workspace-a")))

    def test_fake_payment_marks_invoice_paid_and_blocks_duplicate(self):
        invoice = self.manager.create_invoice("workspace-a", **self.period)
        payment = self.manager.record_payment(
            "workspace-a",
            invoice["invoice_id"],
            provider="FAKE",
            status="SUCCEEDED",
            amount=1000,
            currency="USD",
        )
        self.assertEqual("FAKE", payment["provider"])
        self.assertEqual(
            "PAID",
            self.manager.get_invoice(
                "workspace-a", invoice["invoice_id"]
            )["status"],
        )
        with self.assertRaisesRegex(ValueError, "invoice_already_paid"):
            self.manager.record_payment(
                "workspace-a", invoice["invoice_id"], provider="MANUAL",
                status="SUCCEEDED", amount=1000, currency="USD"
            )

    def test_failed_payment_is_recorded_without_paid_transition(self):
        invoice = self.manager.create_invoice("workspace-a", **self.period)
        self.manager.record_payment(
            "workspace-a", invoice["invoice_id"], provider="MANUAL",
            status="FAILED", amount=1000, currency="USD"
        )
        self.assertEqual(
            "OPEN",
            self.manager.get_invoice(
                "workspace-a", invoice["invoice_id"]
            )["status"],
        )

    def test_amount_currency_plan_and_period_validation(self):
        with self.assertRaisesRegex(ValueError, "negative_amount"):
            Price("bad", "PRO", "USD", -1, "MONTH")
        invoice = self.manager.create_invoice("workspace-a", **self.period)
        with self.assertRaisesRegex(ValueError, "currency_mismatch"):
            self.manager.record_payment(
                "workspace-a", invoice["invoice_id"], provider="FAKE",
                status="SUCCEEDED", amount=1000, currency="EUR"
            )
        wrong = dict(self.period, price_id="dev-business-month")
        with self.assertRaisesRegex(ValueError, "price_plan_mismatch"):
            self.manager.create_invoice("workspace-a", **wrong)
        reversed_period = dict(
            self.period,
            period_start=self.period["period_end"],
            period_end=self.period["period_start"],
        )
        with self.assertRaisesRegex(ValueError, "invalid_invoice_period"):
            self.manager.create_invoice("workspace-a", **reversed_period)

    def test_restart_and_workspace_isolation(self):
        invoice = self.manager.create_invoice("workspace-a", **self.period)
        restarted_repository = JsonStateRepository(self.path)
        restarted_plans = PlanManager(restarted_repository)
        restarted_subscriptions = SubscriptionManager(
            restarted_repository, restarted_plans
        )
        restarted = BillingManager(
            restarted_repository, restarted_subscriptions
        )
        self.assertEqual(
            invoice["invoice_id"],
            restarted.get_invoice("workspace-a", invoice["invoice_id"])["invoice_id"],
        )
        self.assertIsNone(
            restarted.get_invoice("workspace-b", invoice["invoice_id"])
        )

    def test_sensitive_payment_fields_are_rejected_or_absent(self):
        with self.assertRaisesRegex(ValueError, "invalid_billing_request"):
            self.service.update_account(
                "workspace-a",
                {"billing_email": "x@example.test", "card_number": "4111"},
            )
        text = self.path.read_text(encoding="utf-8").lower()
        self.assertNotIn("card_number", text)
        self.assertNotIn("account_number", text)


if __name__ == "__main__":
    unittest.main()
