import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from application.credential_service import CredentialService
from application.job_execution_api_service import JobExecutionApiService
from application.login_service import LoginService
from application.persistent_execution_service import PersistentExecutionService
from application.session_service import SessionService
from application.user_service import UserService
from application.workspace_membership_service import WorkspaceMembershipService
from application.workspace_service import WorkspaceService
from core.access_token_provider import SignedAccessTokenProvider
from core.artifact_manager import ArtifactManager
from core.batch import BatchManager
from core.execution_history import ExecutionHistory
from core.execution_history_repository import JsonFileExecutionHistoryRepository
from core.persistence import JsonStateRepository
from core.status import PipelineStatus
from core.task_queue import InProcessJobWorker, JobStatus, PersistentJobQueue
from core.usage_engine import UsageEngine


class _Unused:
    pass


class JobExecutionApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.state_path = root / "state.json"
        self.history_path = root / "history.json"
        self.state = JsonStateRepository(self.state_path)
        self.queue = PersistentJobQueue(self.state)
        self.history = ExecutionHistory(repository=JsonFileExecutionHistoryRepository(self.history_path))
        self.artifacts = ArtifactManager()
        self.usage = UsageEngine(self.state)
        self.execution = PersistentExecutionService(
            self.queue, InProcessJobWorker(self.queue), self.history,
            self.artifacts, self.usage,
        )
        self.batches = BatchManager(self.queue, self.state)
        self.service = JobExecutionApiService(
            self.execution, self.history, self.artifacts, self.usage,
            self.batches,
        )
        self.users = UserService()
        self.owner = self.users.create("owner@example.com")
        self.other = self.users.create("other@example.com")
        credentials = CredentialService(self.users)
        for user in (self.owner, self.other):
            credentials.set_password(user["user_id"], "safe-passphrase")
        sessions = SessionService()
        login = LoginService(
            self.users, credentials,
            SignedAccessTokenProvider(secret="injected-test-secret"), sessions,
        )
        workspaces = WorkspaceService()
        memberships = WorkspaceMembershipService(workspaces, self.users)
        self.workspace = memberships.create_workspace("Owned", self.owner["user_id"])
        self.foreign = memberships.create_workspace("Foreign", self.other["user_id"])
        app = create_app(
            automation_service=_Unused(), task_query_service=_Unused(),
            workspace_service=workspaces, user_service=self.users,
            membership_service=memberships, credential_service=credentials,
            login_service=login, session_service=sessions,
            job_execution_api_service=self.service, auth_required=True,
        )
        self.client = TestClient(app)
        self.owner_headers = self._login("owner@example.com")
        self.other_headers = self._login("other@example.com")

    def tearDown(self):
        self.temp.cleanup()

    def _login(self, email):
        value = self.client.post("/auth/login", json={
            "email": email, "password": "safe-passphrase",
        }).json()
        return {"Authorization": "Bearer " + value["access_token"]}

    def _base(self):
        return f"/workspaces/{self.workspace['workspace_id']}"

    def _payload(self, key="key"):
        return {
            "mission_id": "mission-a", "target_id": "content",
            "idempotency_key": key,
        }

    def test_auth_workspace_isolation_and_idempotent_submission(self):
        url = self._base() + "/jobs"
        self.assertEqual(401, self.client.get(url).status_code)
        self.assertEqual(403, self.client.get(url, headers=self.other_headers).status_code)
        first = self.client.post(url, json=self._payload(), headers=self.owner_headers)
        second = self.client.post(url, json=self._payload(), headers=self.owner_headers)
        self.assertEqual(201, first.status_code)
        self.assertEqual(first.json()["job_id"], second.json()["job_id"])
        self.assertEqual(1, self.client.get(url, headers=self.owner_headers).json()["total"])

    def test_job_detail_cancel_retry_and_invalid_transitions(self):
        url = self._base() + "/jobs"
        job = self.client.post(url, json=self._payload(), headers=self.owner_headers).json()
        detail = url + "/" + job["job_id"]
        self.assertEqual(200, self.client.get(detail, headers=self.owner_headers).status_code)
        self.assertEqual(200, self.client.post(detail + "/cancel", headers=self.owner_headers).status_code)
        self.assertEqual(409, self.client.post(detail + "/cancel", headers=self.owner_headers).status_code)

        retry_job = self.service.submit(self.workspace["workspace_id"], self._payload("retry"))
        claimed = self.queue.claim(self.workspace["workspace_id"], "worker")
        self.queue.fail(
            claimed.job_id, self.workspace["workspace_id"], "worker",
            {"status": "FAILED", "error": "ProviderError: TimeoutError"},
            {"retryable": True, "current_attempt": 1},
        )
        retry_url = url + "/" + retry_job["job_id"] + "/retry"
        self.assertEqual(200, self.client.post(retry_url, headers=self.owner_headers).status_code)

    def test_execution_result_artifact_usage_and_safe_response(self):
        workspace_id = self.workspace["workspace_id"]
        self.execution.register_target("content", lambda job: {
            "status": PipelineStatus.SUCCESS, "pipeline": "Test Pipeline",
            "task_type": "CONTENT",
            "data": {"provider_usage": {"provider": "fake", "total_tokens": 2}},
            "artifacts": [], "error": None,
        })
        job = self.service.submit(workspace_id, self._payload())
        self.execution.run_once(workspace_id)
        detail = self.client.get(
            self._base() + "/jobs/" + job["job_id"],
            headers=self.owner_headers,
        )
        self.assertEqual(200, detail.status_code)
        self.assertEqual(2, detail.json()["usage"][0]["total_tokens"])
        self.assertNotIn("Persistent job execution", detail.text)
        executions = self.client.get(
            self._base() + "/executions", headers=self.owner_headers
        ).json()["items"]
        self.assertEqual(job["job_id"], executions[0]["task_id"])
        self.assertNotIn("task", executions[0])

    def test_restart_restores_job_for_api_query(self):
        workspace_id = self.workspace["workspace_id"]
        job = self.service.submit(workspace_id, self._payload())
        restarted_queue = PersistentJobQueue(
            JsonStateRepository(self.state_path), workspace_ids=(workspace_id,)
        )
        restarted_execution = PersistentExecutionService(
            restarted_queue, InProcessJobWorker(restarted_queue),
            ExecutionHistory(repository=JsonFileExecutionHistoryRepository(self.history_path)),
            self.artifacts, UsageEngine(JsonStateRepository(self.state_path)),
        )
        self.service.execution = restarted_execution
        self.service.queue = restarted_queue
        self.assertEqual(job["job_id"], self.service.get_job(workspace_id, job["job_id"])["job_id"])

    def test_batch_list_and_detail_reuse_existing_manager(self):
        workspace_id = self.workspace["workspace_id"]
        batch = self.batches.create(workspace_id, [{
            "mission_id": "mission-a", "target_id": "content",
        }], "batch-key")
        listed = self.client.get(self._base() + "/batches", headers=self.owner_headers)
        detail = self.client.get(
            self._base() + "/batches/" + batch.batch_id,
            headers=self.owner_headers,
        )
        self.assertEqual(1, len(listed.json()["items"]))
        self.assertEqual(batch.batch_id, detail.json()["batch_id"])
        self.assertEqual(404, self.client.get(
            self._base() + "/batches/missing", headers=self.owner_headers
        ).status_code)


if __name__ == "__main__":
    unittest.main()
