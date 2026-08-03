import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from application.credential_service import CredentialService
from application.intelligence_service import IntelligenceService
from application.login_service import LoginService
from application.session_service import SessionService
from application.user_service import UserService
from application.workspace_membership_service import WorkspaceMembershipService
from application.workspace_service import WorkspaceService
from core.access_token_provider import SignedAccessTokenProvider
from core.company_bible import BibleManager
from core.intelligence import Confidence, IntelligenceEngine, ResearchFinding, ResearchSource
from core.execution_history import ExecutionHistory
from core.persistence import InMemoryStateRepository, JsonStateRepository
from providers.intelligence import FakeMeetingProvider, FakeResearchProvider, IntelligenceProviderResult
from providers.factory import ProviderFactory
from tests.test_company_bible import hierarchy
from tests.test_organization_engine import fixture


def payload(assignment):
    return {
        "idempotency_key":"research-one", "project_id":"project-one",
        "organization_metadata":{key:getattr(assignment,key) for key in ("assignment_id","company_id","manager_id","department_id","employee_id")},
        "research_types":["MARKET","PLATFORM"], "topic":"Public market topic",
        "target_platforms":["youtube","naver"], "target_audience":"Test audience",
        "region":"KR", "language":"ko",
        "time_range":{"start":"2026-01-01T00:00:00+00:00","end":"2026-12-31T00:00:00+00:00"},
        "metadata":{"fixture":True},
    }


def participants():
    return ["ceo-a","manager-employee-a","research-employee","marketing-employee","music-employee","qa-reviewer"]


class IntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.repository=InMemoryStateRepository();_,_,self.workflow,self.organization,_=fixture(self.repository)
        bibles=BibleManager(self.repository);hierarchy(bibles)
        self.engine=IntelligenceEngine(self.repository,self.organization,bibles)
        self.assignment=self.organization.assign("workspace-a","company-a","RESEARCH","intelligence")

    def research(self):
        request=self.engine.create_research("workspace-a",payload(self.assignment))
        return request,self.engine.run_research("workspace-a",request.research_request_id)

    def test_research_validation_integrity_confidence_and_bible_snapshot(self):
        self.assertEqual("fake-research",ProviderFactory.research_from_environment({}).provider.provider_name)
        self.assertEqual("fake-meeting",ProviderFactory.meeting_from_environment({}).provider.provider_name)
        request,report=self.research()
        self.assertEqual("COMPLETED",report.status);self.assertEqual(2,report.confidence.source_count)
        self.assertEqual("DISAGREEMENT",report.confidence.source_agreement)
        self.assertEqual("1",report.bible_version_metadata["constitution_version"])
        self.assertEqual(request.organization_metadata["employee_id"],"research-employee")
        self.assertNotIn("Public market topic",str(report.to_dict()))
        self.assertEqual(2,len(self.engine.list_sources("workspace-a",report.report_id)))
        self.assertIsNone(self.engine.get_report("workspace-b",report.report_id))
        bad=payload(self.assignment);bad["research_types"]=["UNKNOWN"]
        with self.assertRaises(ValueError):self.engine.create_research("workspace-a",{**bad,"idempotency_key":"bad"})

    def test_source_and_finding_contracts_reject_invalid_evidence(self):
        source={"source_id":"s","source_type":"MARKET","title":"Safe","provider":"fake","published_at":"2026-01-02T00:00:00+00:00","retrieved_at":"2026-01-01T00:00:00+00:00","reference":"fixture:s","structured_summary":"Safe summary","relevance":"HIGH","freshness":"CURRENT","quality":"TEST","access_status":"AVAILABLE","metadata":{}}
        self.assertIsNone(ResearchSource.from_dict(source))
        drive_path="C"+chr(58)+chr(92)+"private"+chr(92)+"source.txt"
        safe_source={**source,"published_at":"2026-01-01T00:00:00+00:00","retrieved_at":"2026-01-02T00:00:00+00:00","reference":drive_path}
        self.assertIsNone(ResearchSource.from_dict(safe_source))
        finding={"finding_id":"f","category":"FACT","claim":"Safe claim","supporting_source_ids":[],"evidence_summary":"Safe evidence","confidence_level":"LOW","limitations":["Limited"],"observed_at":"2026-01-01T00:00:00+00:00","disagreement":None,"metadata":{}}
        value=ResearchFinding.from_dict(finding);self.assertIsNotNone(value)
        with self.assertRaises(ValueError):self.engine._findings([finding],[])
        self.assertIsNone(Confidence.from_dict({"source_count":-1,"source_quality":"LOW","source_freshness":"LOW","source_agreement":"LOW","coverage":"NONE","uncertainty_notes":["Limited"],"confidence_level":"LOW"}))

    def test_idempotency_memory_absence_partial_failure_and_timeout(self):
        first=self.engine.create_research("workspace-a",payload(self.assignment));second=self.engine.create_research("workspace-a",{**payload(self.assignment),"topic":"Changed"})
        self.assertEqual(first.research_request_id,second.research_request_id)
        class Empty(FakeResearchProvider):
            def collect(self,request):return IntelligenceProviderResult({"sources":[]})
        partial=IntelligenceEngine(self.repository,self.organization,research_provider=Empty())
        request=partial.create_research("workspace-a",{**payload(self.assignment),"idempotency_key":"partial"})
        self.assertEqual("PARTIAL",partial.run_research("workspace-a",request.research_request_id).status)
        class Slow(FakeResearchProvider):
            def collect(self,request):time.sleep(.05);return super().collect(request)
        timed=IntelligenceEngine(self.repository,self.organization,research_provider=Slow(),timeout_seconds=.001)
        request=timed.create_research("workspace-a",{**payload(self.assignment),"idempotency_key":"timeout"})
        self.assertEqual("FAILED",timed.run_research("workspace-a",request.research_request_id).status)

    def test_meeting_minutes_diverse_plans_decision_and_execution_plan(self):
        _,report=self.research()
        meeting=self.engine.create_meeting("workspace-a",{"research_report_id":report.report_id,"idempotency_key":"meeting-one","purpose":"Choose an evidence plan","participant_ids":participants(),"agenda":["Review evidence","Compare plans"]})
        meeting=self.engine.run_meeting("workspace-a",meeting.meeting_id)
        self.assertEqual("COMPLETED",meeting.status);self.assertEqual(3,len(self.engine.list_plans("workspace-a",meeting.meeting_id)))
        minutes=self.engine.get_minutes("workspace-a",meeting.meeting_id);self.assertEqual(list(report.source_ids),minutes["evidence_reviewed"])
        decision=self.engine.decide("workspace-a",meeting.meeting_id);self.assertEqual("RULE_BASED",decision.selection_method)
        execution=self.engine.get_execution_plan("workspace-a",meeting.meeting_id)
        self.assertEqual(("PLANNING","MUSIC","IMAGE","BLOG","VIDEO","YOUTUBE","NAVER"),execution.ordered_steps)
        self.assertIn("manual_naver_publish",execution.approval_boundaries)

    def test_meeting_rejects_foreign_failed_and_tampered_reports(self):
        _,report=self.research()
        with self.assertRaises(ValueError):self.engine.create_meeting("workspace-b",{"research_report_id":report.report_id})
        meeting=self.engine.create_meeting("workspace-a",{"research_report_id":report.report_id,"idempotency_key":"tamper","purpose":"Review evidence","participant_ids":participants(),"agenda":["Review"]})
        raw=report.to_dict();raw["executive_summary"]="Changed summary";self.repository.save("research_report",report.report_id,"workspace-a",raw)
        with self.assertRaises(ValueError):self.engine.run_meeting("workspace-a",meeting.meeting_id)

    def test_duplicate_plans_provider_failure_and_user_selection(self):
        _,report=self.research()
        class Duplicate(FakeMeetingProvider):
            def deliberate(self,*args):
                value=super().deliberate(*args);plans=value.data["plans"]
                for key in ("concept","target_audience","platform_strategy","image_direction","content_direction","risks"):plans[1][key]=plans[0][key]
                return value
        bad=IntelligenceEngine(self.repository,self.organization,meeting_provider=Duplicate())
        meeting=bad.create_meeting("workspace-a",{"research_report_id":report.report_id,"idempotency_key":"duplicate","purpose":"Review plans","participant_ids":participants(),"agenda":["Compare"]})
        self.assertEqual("FAILED",bad.run_meeting("workspace-a",meeting.meeting_id).status)
        good=self.engine.create_meeting("workspace-a",{"research_report_id":report.report_id,"idempotency_key":"select","purpose":"Review plans","participant_ids":participants(),"agenda":["Compare"]})
        self.engine.run_meeting("workspace-a",good.meeting_id)
        selected=self.engine.decide("workspace-a",good.meeting_id,"plan-2");self.assertEqual("USER_SELECTED",selected.selection_method)

    def test_user_input_required_meeting_timeout_and_safe_history(self):
        _,report=self.research()
        class Equal(FakeMeetingProvider):
            def deliberate(self,*args):
                value=super().deliberate(*args)
                for plan in value.data["plans"]:plan["feasibility"]=1
                return value
        equal=IntelligenceEngine(self.repository,self.organization,meeting_provider=Equal())
        meeting=equal.create_meeting("workspace-a",{"research_report_id":report.report_id,"idempotency_key":"equal","purpose":"Compare equal plans","participant_ids":participants(),"agenda":["Compare"]})
        equal.run_meeting("workspace-a",meeting.meeting_id);self.assertIsNone(equal.decide("workspace-a",meeting.meeting_id))
        self.assertEqual("USER_INPUT_REQUIRED",equal.get_meeting("workspace-a",meeting.meeting_id).status)
        self.assertEqual("USER_SELECTED",equal.decide("workspace-a",meeting.meeting_id,"plan-2").selection_method)
        class Slow(FakeMeetingProvider):
            def deliberate(self,*args):time.sleep(.05);return super().deliberate(*args)
        slow=IntelligenceEngine(self.repository,self.organization,meeting_provider=Slow(),timeout_seconds=.001)
        meeting=slow.create_meeting("workspace-a",{"research_report_id":report.report_id,"idempotency_key":"slow","purpose":"Bounded timeout","participant_ids":participants(),"agenda":["Review"]})
        self.assertEqual("FAILED",slow.run_meeting("workspace-a",meeting.meeting_id).status)
        history=ExecutionHistory(state_repository=self.repository)
        tracked=IntelligenceEngine(self.repository,self.organization,history=history)
        request=tracked.create_research("workspace-a",{**payload(self.assignment),"idempotency_key":"history"});tracked.run_research("workspace-a",request.research_request_id)
        records=self.repository.list("execution","workspace-a")
        self.assertIn("research_report_id",records[-1]["result"]["identifiers"])
        self.assertNotIn("Public market topic",str(records));self.assertNotIn("brand_identity",str(records))

    def test_json_restart_and_product_workflow_compatibility(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository=JsonStateRepository(Path(temporary)/"state.json");_,_,workflow,organization,_=fixture(repository)
            engine=IntelligenceEngine(repository,organization);assignment=organization.assign("workspace-a","company-a","RESEARCH","restart")
            request=engine.create_research("workspace-a",payload(assignment));report=engine.run_research("workspace-a",request.research_request_id)
            _,_,_,restarted,_=fixture(InMemoryStateRepository())
            restored=IntelligenceEngine(JsonStateRepository(Path(temporary)/"state.json"),organization)
            self.assertEqual(report.report_id,restored.get_report("workspace-a",report.report_id).report_id)
        legacy=self.workflow.submit("workspace-a","request","legacy-intelligence")
        self.assertEqual({},legacy["intelligence_metadata"])


class IntelligenceApiTests(unittest.TestCase):
    def test_authenticated_fake_e2e_api(self):
        repository=InMemoryStateRepository();_,_,_,organization,_=fixture(repository)
        engine=IntelligenceEngine(repository,organization);service=IntelligenceService(engine)
        assignment=organization.assign("workspace-a","company-a","RESEARCH","api")
        users=UserService();owner=users.create("owner@example.com");outsider=users.create("out@example.com");credentials=CredentialService(users)
        for user in (owner,outsider):credentials.set_password(user["user_id"],"safe-passphrase")
        sessions=SessionService();login=LoginService(users,credentials,SignedAccessTokenProvider(secret="injected-test-secret"),sessions)
        workspaces=WorkspaceService();memberships=WorkspaceMembershipService(workspaces,users);workspace=memberships.create_workspace("Owned",owner["user_id"]);foreign=memberships.create_workspace("Other",outsider["user_id"])
        # The test organization is deliberately scoped to the authorized Workspace ID.
        repository=InMemoryStateRepository();_,_,_,organization,_=fixture(repository,workspace["workspace_id"]);engine=IntelligenceEngine(repository,organization);service=IntelligenceService(engine);assignment=organization.assign(workspace["workspace_id"],"company-a","RESEARCH","api")
        app=create_app(workspace_service=workspaces,user_service=users,membership_service=memberships,credential_service=credentials,login_service=login,session_service=sessions,intelligence_service=service,auth_required=True)
        client=TestClient(app)
        def auth(email):return {"Authorization":"Bearer "+client.post("/auth/login",json={"email":email,"password":"safe-passphrase"}).json()["access_token"]}
        owner_h,outsider_h=auth("owner@example.com"),auth("out@example.com");base=f"/workspaces/{workspace['workspace_id']}/intelligence"
        self.assertEqual(401,client.post(base+"/research",json={}).status_code)
        created=client.post(base+"/research",json=payload(assignment),headers=owner_h);self.assertEqual(201,created.status_code)
        report=client.post(base+f"/research/{created.json()['research_request_id']}/run",headers=owner_h);self.assertEqual("COMPLETED",report.json()["status"])
        self.assertEqual(1,len(client.get(base+"/reports",headers=owner_h).json()["items"]))
        meeting=client.post(base+"/meetings",json={"research_report_id":report.json()["report_id"],"idempotency_key":"api-meeting","purpose":"Review evidence","participant_ids":participants(),"agenda":["Compare plans"]},headers=owner_h);self.assertEqual(201,meeting.status_code)
        meeting_id=meeting.json()["meeting_id"];self.assertEqual("COMPLETED",client.post(base+f"/meetings/{meeting_id}/run",headers=owner_h).json()["status"])
        self.assertEqual(3,len(client.get(base+f"/meetings/{meeting_id}/plans",headers=owner_h).json()["items"]))
        self.assertEqual("RULE_BASED",client.post(base+f"/meetings/{meeting_id}/decision",json={},headers=owner_h).json()["selection_method"])
        self.assertEqual(200,client.get(base+f"/meetings/{meeting_id}/execution-plan",headers=owner_h).status_code)
        self.assertEqual(403,client.get(base+"/reports",headers=outsider_h).status_code)


if __name__=="__main__":unittest.main()
