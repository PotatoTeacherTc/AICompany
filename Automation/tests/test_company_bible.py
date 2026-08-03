import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from application.credential_service import CredentialService
from application.login_service import LoginService
from application.session_service import SessionService
from application.user_service import UserService
from application.workspace_membership_service import WorkspaceMembershipService
from application.workspace_service import WorkspaceService
from core.access_token_provider import SignedAccessTokenProvider
from core.artifact_manager import ArtifactManager
from core.company_bible import (
    BibleManager, CompanyConstitution, DEPARTMENT_BIBLE_TYPES,
    EMPLOYEE_ROLE_TYPES,
)
from core.execution_history import ExecutionHistory
from core.persistence import InMemoryStateRepository, JsonStateRepository
from core.task_queue import InProcessJobWorker, PersistentJobQueue
from core.usage_engine import UsageEngine
from core.workspace_membership import MEMBER
from application.persistent_execution_service import PersistentExecutionService
from application.product_workflow_service import ProductWorkflowService


def constitution(version="1", status="DRAFT", marker="safe principle"):
    return {"name":"Test Company","version":version,"status":status,
            "principles":[marker],"prohibited_actions":["unsafe action"],
            "approval_rules":["owner approval"],"security_rules":["safe data"],
            "quality_rules":["review output"],"metadata":{"fixture":True}}

def company(version="1", parent="1", status="DRAFT"):
    return {"name":"Test Bible","version":version,"status":status,
            "brand_identity":["test identity"],"audience":["test audience"],
            "tone":["clear"],"global_quality_rules":["review"],
            "prohibited_patterns":["unsafe pattern"],"required_outputs":["result"],
            "constitution_version":parent,"metadata":{}}

def department(version="1", parent="1", status="DRAFT"):
    return {"department_type":"RESEARCH","version":version,"status":status,
            "purpose":["research purpose"],"responsibilities":["collect facts"],
            "input_contract":["request"],"output_contract":["report"],
            "professional_rules":["cite facts"],"review_rules":["review"],
            "prohibited_patterns":["unsupported claim"],"company_bible_version":parent,"metadata":{}}

def employee(version="1", parent="1", status="DRAFT"):
    return {"role_type":"RESEARCHER","department_type":"RESEARCH","version":version,"status":status,
            "role":["researcher"],"responsibilities":["research"],"authority":["recommend"],
            "required_inputs":["request"],"output_contract":["report"],
            "decision_rules":["use facts"],"collaboration_rules":["share summary"],
            "self_review_rules":["check sources"],"escalation_rules":["ask manager"],
            "department_bible_version":parent,"metadata":{}}

def hierarchy(manager, workspace="workspace-a"):
    manager.create_constitution(workspace, constitution(status="ACTIVE"))
    manager.create_company_bible(workspace, company(status="ACTIVE"))
    manager.create_department_bible(workspace, department(status="ACTIVE"))
    manager.create_employee_bible(workspace, employee(status="ACTIVE"))


class CompanyBibleTests(unittest.TestCase):
    def test_serialization_validation_and_sensitive_rejection(self):
        manager=BibleManager(InMemoryStateRepository()); value=manager.create_constitution("workspace-a",constitution())
        self.assertEqual(value, CompanyConstitution.from_dict(value.to_dict()))
        self.assertIsNone(CompanyConstitution.from_dict({**value.to_dict(),"status":"INVALID"}))
        with self.assertRaises(ValueError): manager.create_constitution("workspace-a",constitution("2",marker="raw prompt text"))

    def test_required_department_and_employee_types_are_bounded(self):
        self.assertEqual({"RESEARCH","MUSIC","DESIGN","VIDEO","MARKETING","QA","FILE"},DEPARTMENT_BIBLE_TYPES)
        self.assertEqual({"CEO","MANAGER","RESEARCHER","PLANNER","CREATOR","REVIEWER","QA"},EMPLOYEE_ROLE_TYPES)
        manager=BibleManager(InMemoryStateRepository()); hierarchy(manager)
        with self.assertRaises(ValueError):
            manager.create_department_bible("workspace-a",{**department("2"),"department_type":"UNKNOWN"})
        with self.assertRaises(ValueError):
            manager.create_employee_bible("workspace-a",{**employee("2"),"role_type":"UNKNOWN"})

    def test_workspace_isolation_active_singleton_and_archive(self):
        manager=BibleManager(InMemoryStateRepository())
        manager.create_constitution("workspace-a",constitution("1", "ACTIVE")); manager.create_constitution("workspace-a",constitution("2"))
        manager.activate_constitution("workspace-a","2")
        self.assertEqual("2",manager.get_constitution("workspace-a").version)
        self.assertEqual("ARCHIVED",manager.get_constitution("workspace-a","1").status)
        self.assertIsNone(manager.get_constitution("workspace-b"))

    def test_parent_references_and_missing_bundle(self):
        manager=BibleManager(InMemoryStateRepository())
        with self.assertRaises(ValueError): manager.create_company_bible("workspace-a",company(parent="missing"))
        empty=manager.resolve("workspace-a","RESEARCH","RESEARCHER")
        self.assertEqual({},empty.version_metadata())
        with self.assertRaises(ValueError):
            manager.resolve("workspace-a",versions={"constitution_version":"missing"})
        manager.create_constitution("workspace-a",constitution(status="ACTIVE"))
        with self.assertRaises(ValueError): manager.create_department_bible("workspace-a",department(parent="missing"))

    def test_bundle_order_explicit_versions_and_snapshot_fixed(self):
        manager=BibleManager(InMemoryStateRepository()); hierarchy(manager)
        bundle=manager.resolve("workspace-a","RESEARCH","RESEARCHER")
        self.assertEqual(["constitution","company_bible","department_bible","employee_bible"],bundle.to_dict()["order"])
        self.assertEqual({"constitution_version":"1","company_bible_version":"1","department_bible_version":"1","employee_bible_version":"1"},bundle.version_metadata())
        manager.create_constitution("workspace-a",constitution("2"));manager.activate_constitution("workspace-a","2")
        self.assertEqual("1",bundle.constitution.version)
        explicit=manager.resolve("workspace-a",versions={"constitution_version":"1"})
        self.assertEqual("1",explicit.constitution.version)

    def test_json_restart_persistence(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/"state.json";manager=BibleManager(JsonStateRepository(path));hierarchy(manager)
            restarted=BibleManager(JsonStateRepository(path))
            self.assertEqual("1",restarted.resolve("workspace-a","RESEARCH","RESEARCHER").employee_bible.version)

    def test_activation_failure_is_atomic(self):
        class FailRepository(InMemoryStateRepository):
            fail=False
            def save(self,*args):
                if self.fail: raise OSError("private failure")
                return super().save(*args)
        repository=FailRepository();manager=BibleManager(repository)
        manager.create_constitution("workspace-a",constitution("1","ACTIVE"));manager.create_constitution("workspace-a",constitution("2"))
        repository.fail=True
        with self.assertRaises(OSError): manager.activate_constitution("workspace-a","2")
        repository.fail=False
        self.assertEqual("1",manager.get_constitution("workspace-a").version)

    def test_workflow_is_backward_compatible_and_records_versions_only(self):
        repository=InMemoryStateRepository(); queue=PersistentJobQueue(repository)
        execution=PersistentExecutionService(queue,InProcessJobWorker(queue),ExecutionHistory(state_repository=repository),ArtifactManager(),UsageEngine(repository))
        manager=BibleManager(repository);hierarchy(manager)
        service=ProductWorkflowService(repository,execution,lambda *_:{"status":"COMPLETED"},bible_resolver=manager)
        item=service.submit("workspace-a","private request","bible-flow"); snapshot=service.bible_snapshot("workspace-a",item["product_id"])
        self.assertEqual("1",snapshot.constitution.version);service.run_once("workspace-a")
        history=repository.list("execution","workspace-a")
        self.assertEqual("1",history[0]["result"]["bible_versions"]["constitution_version"])
        self.assertNotIn("safe principle",str(history));self.assertNotIn("private request",str(repository._records))
        self.assertNotIn("safe principle",str(service.get("workspace-a",item["product_id"])))
        legacy=ProductWorkflowService(InMemoryStateRepository(),execution,lambda *_:{"status":"COMPLETED"})
        self.assertEqual({},legacy.submit("workspace-b","request","legacy")["bible_versions"])


class BibleApiTests(unittest.TestCase):
    def setUp(self):
        users=UserService();self.owner=users.create("owner@example.com");self.member=users.create("member@example.com");self.outsider=users.create("out@example.com")
        credentials=CredentialService(users)
        for user in (self.owner,self.member,self.outsider):credentials.set_password(user["user_id"],"safe-passphrase")
        sessions=SessionService();login=LoginService(users,credentials,SignedAccessTokenProvider(secret="injected-test-secret"),sessions)
        workspaces=WorkspaceService();memberships=WorkspaceMembershipService(workspaces,users)
        self.workspace=memberships.create_workspace("Owned",self.owner["user_id"]);memberships.add(self.workspace["workspace_id"],self.member["user_id"],MEMBER)
        self.foreign=memberships.create_workspace("Other",self.outsider["user_id"])
        self.manager=BibleManager(InMemoryStateRepository())
        app=create_app(automation_service=object(),task_query_service=object(),workspace_service=workspaces,user_service=users,membership_service=memberships,credential_service=credentials,login_service=login,session_service=sessions,bible_service=self.manager,auth_required=True)
        self.client=TestClient(app);self.owner_headers=self._login("owner@example.com");self.member_headers=self._login("member@example.com");self.out_headers=self._login("out@example.com")
    def _login(self,email):
        token=self.client.post("/auth/login",json={"email":email,"password":"safe-passphrase"}).json()["access_token"]
        return {"Authorization":"Bearer "+token}
    def test_auth_permissions_lifecycle_and_bundle(self):
        base=f"/workspaces/{self.workspace['workspace_id']}/bibles"
        self.assertEqual(401,self.client.get(base+"/constitution").status_code)
        self.assertEqual(403,self.client.post(base+"/constitution",json=constitution(),headers=self.member_headers).status_code)
        self.assertEqual(201,self.client.post(base+"/constitution",json=constitution("1","ACTIVE"),headers=self.owner_headers).status_code)
        self.assertEqual(200,self.client.get(base+"/constitution",headers=self.member_headers).status_code)
        self.assertEqual(403,self.client.get(base+"/constitution",headers=self.out_headers).status_code)
        self.assertEqual(201,self.client.post(base+"/company",json=company(status="ACTIVE"),headers=self.owner_headers).status_code)
        self.assertEqual(201,self.client.post(base+"/departments/RESEARCH",json=department(status="ACTIVE"),headers=self.owner_headers).status_code)
        self.assertEqual(201,self.client.post(base+"/employees/RESEARCH/RESEARCHER",json=employee(status="ACTIVE"),headers=self.owner_headers).status_code)
        bundle=self.client.get(base+"/bundle?department_type=RESEARCH&role_type=RESEARCHER",headers=self.member_headers)
        self.assertEqual(200,bundle.status_code);self.assertEqual("1",bundle.json()["items"]["employee_bible"]["version"])


if __name__ == "__main__": unittest.main()
