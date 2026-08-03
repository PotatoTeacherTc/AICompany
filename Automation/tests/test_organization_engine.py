import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from application.credential_service import CredentialService
from application.login_service import LoginService
from application.organization_service import OrganizationService
from application.persistent_execution_service import PersistentExecutionService
from application.product_workflow_service import ProductWorkflowService
from application.session_service import SessionService
from application.user_service import UserService
from application.workspace_membership_service import WorkspaceMembershipService
from application.workspace_service import WorkspaceService
from core.access_token_provider import SignedAccessTokenProvider
from core.artifact_manager import ArtifactManager
from core.department import DepartmentManager, WorkerDirectory
from core.execution_history import ExecutionHistory
from core.organization_engine import (
    ORGANIZATION_DEPARTMENT_TYPES, ORGANIZATION_ROLE_TYPES,
    ORGANIZATION_TASK_TYPES, RUNTIME_STATUSES, OrganizationEngine,
)
from core.persistence import InMemoryStateRepository, JsonStateRepository
from core.task_queue import InProcessJobWorker, PersistentJobQueue
from core.usage_engine import UsageEngine


def services(repository):
    directory = WorkerDirectory()
    departments = DepartmentManager(repository, directory, ORGANIZATION_TASK_TYPES)
    queue = PersistentJobQueue(repository)
    execution = PersistentExecutionService(
        queue, InProcessJobWorker(queue),
        ExecutionHistory(state_repository=repository), ArtifactManager(),
        UsageEngine(repository),
    )

    def runner(stage, workspace_id, product_id, request_text, record):
        return {"status": "COMPLETED", "result": {"stage": stage, "safe_ref": product_id}}

    workflow = ProductWorkflowService(repository, execution, runner)
    engine = OrganizationEngine(repository, departments, workflow)
    return directory, departments, workflow, engine


def fixture(repository, workspace="workspace-a"):
    directory, departments, workflow, engine = services(repository)
    department_ids = []
    for department_type in ORGANIZATION_DEPARTMENT_TYPES:
        department_id = department_type.lower()
        departments.create(
            workspace, department_type.title(), f"{department_type.title()} company department",
            department_type, supported_task_types=ORGANIZATION_TASK_TYPES,
            department_id=department_id,
        )
        department_ids.append(department_id)
    company_id = "company-a"
    ceo = engine.create_employee(workspace, company_id, "CEO", employee_id="ceo-a")
    manager_employee = engine.create_employee(workspace, company_id, "MANAGER", employee_id="manager-employee-a")
    manager = engine.create_manager(workspace, company_id, manager_employee.employee_id, department_ids, manager_id="manager-a")
    roles = {
        "research": "RESEARCHER", "music": "CREATOR", "design": "CREATOR",
        "video": "CREATOR", "marketing": "PLANNER", "qa": "QA", "file": "CREATOR",
    }
    for department_id, role in roles.items():
        engine.create_employee(
            workspace, company_id, role, department_id=department_id,
            manager_id=manager.manager_id, employee_id=f"{department_id}-employee",
        )
    engine.create_employee(
        workspace, company_id, "REVIEWER", department_id="qa",
        manager_id=manager.manager_id, employee_id="qa-reviewer",
    )
    company = engine.create_company(
        workspace, "Test Company", ceo.employee_id, manager.manager_id,
        department_ids, company_id=company_id,
    )
    return directory, departments, workflow, engine, company


class OrganizationEngineTests(unittest.TestCase):
    def test_validation_registry_and_reporting_lines(self):
        repository = InMemoryStateRepository()
        _, _, _, engine, company = fixture(repository)
        snapshot = engine.snapshot("workspace-a")
        self.assertEqual(company.company_id, snapshot["company"]["company_id"])
        self.assertEqual(set(ORGANIZATION_DEPARTMENT_TYPES), {item["department_type"] for item in snapshot["departments"]})
        self.assertTrue(set(ORGANIZATION_ROLE_TYPES).issuperset({item["role_type"] for item in snapshot["employees"]}))
        self.assertEqual(10, len(snapshot["reporting_lines"]))
        self.assertEqual({"IDLE", "ASSIGNED", "RUNNING", "WAITING", "COMPLETED", "FAILED"}, RUNTIME_STATUSES)
        with self.assertRaises(ValueError):
            engine.create_employee("workspace-a", "company-a", "UNKNOWN")
        with self.assertRaises(ValueError):
            engine.create_employee("workspace-a", "company-a", "REVIEWER", department_id="qa", manager_id="missing")
        with self.assertRaises(ValueError):
            engine.create_company("workspace-a", "Other", "ceo-a", "manager-a", company.department_ids, company_id="other")

    def test_fixed_assignment_rules_and_idempotency(self):
        _, _, _, engine, _ = fixture(InMemoryStateRepository())
        cases = {
            "RESEARCH": ("research", "research-employee"),
            "MUSIC": ("music", "music-employee"),
            "IMAGE": ("design", "design-employee"),
            "VIDEO": ("video", "video-employee"),
            "PRODUCT": ("marketing", "marketing-employee"),
            "QA": ("qa", "qa-employee"),
            "FILE": ("file", "file-employee"),
        }
        for task_type, expected in cases.items():
            value = engine.assign("workspace-a", "company-a", task_type, task_type.lower())
            self.assertEqual(expected, (value.department_id, value.employee_id))
        first = engine.assign("workspace-a", "company-a", "PRODUCT", "same")
        second = engine.assign("workspace-a", "company-a", "RESEARCH", "same")
        self.assertEqual(first.assignment_id, second.assignment_id)
        self.assertEqual("PRODUCT", second.task_type)
        with self.assertRaises(ValueError):
            engine.assign("workspace-a", "company-a", "UNKNOWN", "unknown")

    def test_execution_routes_to_workflow_and_history_has_ids_only(self):
        repository = InMemoryStateRepository()
        _, _, workflow, engine, _ = fixture(repository)
        assignment = engine.execute("workspace-a", "company-a", "private user input", "PRODUCT", "route-one")
        self.assertEqual("RUNNING", assignment.status)
        workflow.run_once("workspace-a")
        runtime = engine.runtime("workspace-a", assignment.assignment_id)
        self.assertEqual("COMPLETED", runtime["employee"]["status"])
        result = workflow.get("workspace-a", assignment.workflow_id)
        self.assertEqual("marketing-employee", result["organization_metadata"]["employee_id"])
        history = repository.list("execution", "workspace-a")
        metadata = history[0]["result"]["organization_metadata"]
        self.assertEqual("manager-a", metadata["manager_id"])
        self.assertEqual("company-a", metadata["company_id"])
        self.assertNotIn("private user input", str(repository._records))
        self.assertNotIn("bible", str(metadata).lower())

    def test_runtime_tracks_waiting_and_failed_workflow_states(self):
        repository = InMemoryStateRepository()
        _, _, workflow, engine, _ = fixture(repository)
        workflow.stage_runner = lambda stage, *_: (
            {"status": "WAITING_FOR_INPUT"} if stage == "MUSIC"
            else {"status": "COMPLETED"}
        )
        waiting = engine.execute("workspace-a", "company-a", "request", "PRODUCT", "waiting")
        workflow.run_once("workspace-a")
        self.assertEqual("WAITING", engine.runtime("workspace-a", waiting.assignment_id)["employee"]["status"])
        workflow.stage_runner = lambda stage, *_: (
            {"status": "FAILED", "safe_error": "STAGE_FAILED"} if stage == "IMAGE"
            else {"status": "COMPLETED"}
        )
        failed = engine.execute("workspace-a", "company-a", "request", "PRODUCT", "failed")
        workflow.run_once("workspace-a")
        self.assertEqual("FAILED", engine.runtime("workspace-a", failed.assignment_id)["employee"]["status"])

    def test_workspace_isolation_and_json_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state.json"
            repository = JsonStateRepository(path)
            _, _, _, engine, _ = fixture(repository)
            assignment = engine.assign("workspace-a", "company-a", "PRODUCT", "persist")
            _, departments, workflow, restarted = services(JsonStateRepository(path))
            self.assertEqual("marketing-employee", restarted.get_assignment("workspace-a", assignment.assignment_id).employee_id)
            self.assertIsNone(restarted.get_assignment("workspace-b", assignment.assignment_id))
            self.assertIsNone(restarted.get_company("workspace-b"))
            self.assertEqual([], departments.list("workspace-b"))

    def test_workflow_remains_optional_and_backward_compatible(self):
        repository = InMemoryStateRepository()
        directory = WorkerDirectory()
        departments = DepartmentManager(repository, directory, ORGANIZATION_TASK_TYPES)
        engine = OrganizationEngine(repository, departments)
        with self.assertRaises(ValueError):
            engine.execute("workspace-a", "company-a", "request", "PRODUCT", "no-workflow")
        _, _, workflow, _, _ = fixture(InMemoryStateRepository())
        legacy = workflow.submit("workspace-a", "request", "legacy")
        self.assertEqual({}, legacy["organization_metadata"])


class OrganizationEngineApiTests(unittest.TestCase):
    def setUp(self):
        users = UserService()
        owner = users.create("owner@example.com")
        outsider = users.create("outsider@example.com")
        credentials = CredentialService(users)
        credentials.set_password(owner["user_id"], "safe-passphrase")
        credentials.set_password(outsider["user_id"], "safe-passphrase")
        sessions = SessionService()
        login = LoginService(users, credentials, SignedAccessTokenProvider(secret="injected-test-secret"), sessions)
        workspaces = WorkspaceService()
        memberships = WorkspaceMembershipService(workspaces, users)
        workspace = memberships.create_workspace("Owned", owner["user_id"])
        foreign = memberships.create_workspace("Other", outsider["user_id"])
        repository = InMemoryStateRepository()
        directory, departments, _, engine, _ = fixture(repository, workspace["workspace_id"])
        organization = OrganizationService(departments, directory, engine)
        app = create_app(
            workspace_service=workspaces, user_service=users,
            membership_service=memberships, credential_service=credentials,
            login_service=login, session_service=sessions,
            organization_service=organization, auth_required=True,
        )
        self.client = TestClient(app)
        self.owner = self._login("owner@example.com")
        self.outsider = self._login("outsider@example.com")
        self.workspace_id = workspace["workspace_id"]
        self.assignment = engine.assign(self.workspace_id, "company-a", "PRODUCT", "api")

    def _login(self, email):
        token = self.client.post("/auth/login", json={"email": email, "password": "safe-passphrase"}).json()["access_token"]
        return {"Authorization": "Bearer " + token}

    def test_read_only_organization_assignment_and_runtime_api(self):
        base = f"/workspaces/{self.workspace_id}/organization"
        self.assertEqual(401, self.client.get(base).status_code)
        snapshot = self.client.get(base, headers=self.owner)
        self.assertEqual("company-a", snapshot.json()["company"]["company_id"])
        self.assertEqual(10, len(self.client.get(base + "/employees", headers=self.owner).json()["items"]))
        self.assertEqual(1, len(self.client.get(base + "/assignments", headers=self.owner).json()["items"]))
        assignment_url = base + "/assignments/" + self.assignment.assignment_id
        self.assertEqual(200, self.client.get(assignment_url, headers=self.owner).status_code)
        self.assertEqual("ASSIGNED", self.client.get(assignment_url + "/runtime", headers=self.owner).json()["employee"]["status"])
        self.assertEqual(403, self.client.get(base, headers=self.outsider).status_code)


if __name__ == "__main__":
    unittest.main()
