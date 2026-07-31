import unittest

from application.dashboard_service import DashboardService


class _Workspace:
    def get(self, workspace_id):
        return {"workspace_id": workspace_id, "name": "Safe", "status": "ACTIVE"}


class _Jobs:
    def list_jobs(self, workspace_id, **kwargs):
        return {"items": [
            {"job_id": "j2", "status": "FAILED"},
            {"job_id": "j1", "status": "PENDING"},
        ]}

    def list_executions(self, workspace_id, **kwargs):
        return {"items": [
            {"task_id": "j2", "status": "FAILED", "error": "JobError: Timeout"},
            {"task_id": "j1", "status": "SUCCESS"},
        ]}


class _Artifacts:
    def list(self, workspace_id, **kwargs):
        return {"items": [
            {"artifact_id": "a2", "status": "ARCHIVED"},
            {"artifact_id": "a1", "status": "AVAILABLE"},
        ]}


class _Usage:
    def summary(self, workspace_id):
        return {"workspace_id": workspace_id, "total_tokens": 3}


class _Quota:
    def get(self, workspace_id):
        return {"workspace_id": workspace_id, "allowed": True}


class _Plans:
    def current(self, workspace_id):
        return {
            "plan_id": "FREE", "name": "Free",
            "entitlements": {"batch_enabled": False},
            "description": "not exposed",
        }


class _Organization:
    def list_departments(self, workspace_id):
        return {"items": [
            {"department_id": "d1", "enabled": True, "worker_ids": ["w1"]},
            {"department_id": "d2", "enabled": False, "worker_ids": []},
        ]}

    def list_workers(self, workspace_id):
        return {"items": [{"worker_id": "w1"}]}


class DashboardApiTests(unittest.TestCase):
    def setUp(self):
        self.service = DashboardService(
            _Workspace(), _Jobs(), _Artifacts(), _Usage(), _Quota(), _Plans(),
            _Organization(),
        )

    def test_workspace_overview_aggregates_existing_read_models(self):
        value = self.service.overview("workspace-a", recent_limit=1)
        self.assertEqual(2, value["jobs"]["counts"]["total"])
        self.assertEqual(1, value["jobs"]["counts"]["pending"])
        self.assertEqual(1, value["executions"]["counts"]["failed"])
        self.assertEqual(1, value["artifacts"]["counts"]["archived"])
        self.assertEqual(2, value["organization"]["department_count"])
        self.assertEqual(1, value["organization"]["worker_capability_count"])
        self.assertEqual(1, len(value["jobs"]["recent"]))
        self.assertNotIn("description", value["plan"])

    def test_missing_usage_fields_and_empty_sources_are_safe(self):
        self.service.usage.summary = lambda workspace_id: {
            "workspace_id": workspace_id
        }
        value = self.service.overview("workspace-a")
        self.assertEqual({"workspace_id": "workspace-a"}, value["usage"])

    def test_invalid_recent_limit_is_rejected(self):
        with self.assertRaises(ValueError):
            self.service.overview("workspace-a", recent_limit=0)


if __name__ == "__main__":
    unittest.main()
