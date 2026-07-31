import tempfile
import unittest
from pathlib import Path

from application.admin_service import PlatformAdminService
from application.billing_service import BillingApplicationService
from application.subscription_service import SubscriptionApplicationService
from application.user_service import UserService
from application.workspace_service import WorkspaceService
from core.billing import BillingManager
from core.persistence import JsonStateRepository
from core.plans import PlanManager
from core.subscription import SubscriptionManager
from core.user_repository import FileUserRepository
from core.workspace_repository import FileWorkspaceRepository


class _Audit:
    def __init__(self):
        self.events = []

    def record(self, **value):
        self.events.append(value)


class _Jobs:
    def __init__(self):
        self.status = "FAILED"

    def list_jobs(self, workspace_id, **kwargs):
        return {"items": [{"job_id": "job-1", "status": self.status}], "total": 1}

    def get_job(self, workspace_id, job_id):
        return {"job_id": job_id, "status": self.status}

    def retry(self, workspace_id, job_id):
        self.status = "PENDING"
        return {"job_id": job_id, "status": self.status}


class AdminOperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.state = JsonStateRepository(root / "state.json")
        self.workspaces = WorkspaceService(FileWorkspaceRepository(root / "workspaces.json"))
        self.users = UserService(FileUserRepository(root / "users.json"))
        self.admin_user = self.users.create("admin@example.test")
        self.member_user = self.users.create("member@example.test")
        self.workspace = self.workspaces.create("Tenant")
        plans = PlanManager(self.state)
        subscriptions = SubscriptionManager(self.state, plans)
        subscriptions.create(self.workspace["workspace_id"], "PRO")
        self.subscription = SubscriptionApplicationService(subscriptions)
        billing = BillingManager(self.state, subscriptions)
        billing.upsert_account(
            self.workspace["workspace_id"],
            billing_email="billing@example.test",
        )
        invoice = billing.create_invoice(
            self.workspace["workspace_id"],
            price_id="dev-pro-month",
            period_start="2026-07-01T00:00:00+00:00",
            period_end="2026-08-01T00:00:00+00:00",
        )
        self.invoice_id = invoice["invoice_id"]
        self.audit = _Audit()
        self.jobs = _Jobs()
        self.service = PlatformAdminService(
            {self.admin_user["user_id"]},
            self.workspaces,
            self.users,
            subscription_service=self.subscription,
            billing_service=BillingApplicationService(billing),
            job_service=self.jobs,
            audit_service=self.audit,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_platform_admin_is_separate_from_workspace_roles(self):
        self.assertTrue(self.service.is_admin(self.admin_user["user_id"]))
        self.assertFalse(self.service.is_admin(self.member_user["user_id"]))
        self.assertFalse(self.service.is_admin("OWNER"))
        self.assertEqual(2, len(self.service.list_users()["items"]))

    def test_operational_summary_is_workspace_scoped(self):
        value = self.service.workspace_operations(self.workspace["workspace_id"])
        self.assertEqual("PRO", value["subscription"]["plan_id"])
        self.assertEqual(1, len(value["invoices"]["items"]))
        self.assertEqual(1, value["failed_jobs"]["total"])
        self.assertIsNone(self.service.workspace_operations("other"))

    def test_workspace_deactivation_preserves_data_and_is_audited(self):
        value = self.service.set_workspace_status(
            self.workspace["workspace_id"], "INACTIVE"
        )
        self.assertEqual("INACTIVE", value["status"])
        self.assertEqual(
            "PLATFORM_WORKSPACE_STATUS_CHANGED", self.audit.events[-1]["action"]
        )
        self.assertIsNotNone(
            self.subscription.current(self.workspace["workspace_id"])
        )

    def test_subscription_plan_job_retry_and_invoice_void_are_limited_actions(self):
        changed = self.service.change_subscription_plan(
            self.workspace["workspace_id"], "BUSINESS"
        )
        self.assertEqual("BUSINESS", changed["plan_id"])
        self.assertEqual(
            "PENDING",
            self.service.retry_failed_job(
                self.workspace["workspace_id"], "job-1"
            )["status"],
        )
        self.assertEqual(
            "VOID",
            self.service.void_invoice(
                self.workspace["workspace_id"], self.invoice_id
            )["status"],
        )

    def test_non_failed_job_and_non_fake_payment_are_rejected(self):
        self.jobs.status = "COMPLETED"
        with self.assertRaisesRegex(ValueError, "job_not_failed"):
            self.service.retry_failed_job(
                self.workspace["workspace_id"], "job-1"
            )
        with self.assertRaisesRegex(ValueError, "fake_payment_required"):
            self.service.record_fake_payment(
                self.workspace["workspace_id"], self.invoice_id,
                {"provider": "MANUAL", "status": "SUCCEEDED",
                 "amount": 1000, "currency": "USD"},
            )


if __name__ == "__main__":
    unittest.main()
