import tempfile,time,unittest
from pathlib import Path
from fastapi.testclient import TestClient

from api.app import create_app
from application.production_quality_service import ProductionQualityService
from application.credential_service import CredentialService
from application.login_service import LoginService
from application.session_service import SessionService
from application.user_service import UserService
from application.workspace_membership_service import WorkspaceMembershipService
from application.workspace_service import WorkspaceService
from core.access_token_provider import SignedAccessTokenProvider
from core.execution_history import ExecutionHistory
from core.intelligence import IntelligenceEngine
from core.persistence import InMemoryStateRepository,JsonStateRepository
from core.production_quality import Candidate,ProductionQualityEngine
from providers.production_quality import FakeProductionProvider
from providers.factory import ProviderFactory
from tests.test_intelligence import payload,participants
from tests.test_organization_engine import fixture


def prepared(repository=None,workspace="workspace-a",history=None):
    repository=repository or InMemoryStateRepository();_,_,_,organization,_=fixture(repository,workspace)
    intelligence=IntelligenceEngine(repository,organization)
    assignment=organization.assign(workspace,"company-a","RESEARCH","production-quality")
    request=intelligence.create_research(workspace,payload(assignment));report=intelligence.run_research(workspace,request.research_request_id)
    meeting=intelligence.create_meeting(workspace,{"research_report_id":report.report_id,"idempotency_key":"pq-meeting","purpose":"Select evidence based production","participant_ids":participants(),"agenda":["Compare plans"]})
    intelligence.run_meeting(workspace,meeting.meeting_id);intelligence.decide(workspace,meeting.meeting_id)
    engine=ProductionQualityEngine(repository,intelligence,organization,history=history)
    plan=intelligence.get_execution_plan(workspace,meeting.meeting_id)
    brief=engine.create_brief(workspace,{"meeting_id":meeting.meeting_id,"execution_plan_id":plan.execution_plan_id,"idempotency_key":"brief-one","target_audience":"Local audience","concept":"Evidence based package","tone":"Calm","prohibited_patterns":["Copied slogans"],"diversity_requirements":{"structural":True},"quality_requirements":{"evidence":True}})
    return repository,organization,intelligence,engine,brief


def rubric(engine,workspace="workspace-a",threshold=.88):
    criteria={name:{"weight":.125,"description":f"Evidence for {name}"} for name in ("order_fulfillment","bible_alignment","research_alignment","originality","platform_fit","technical_validity","safety","consistency")}
    return engine.create_rubric(workspace,{"rubric_id":"rubric-1","output_type":"PACKAGE","version":"1","criteria":criteria,"threshold":threshold,"hard_fail_rules":["technical_validation"]})


class ProductionQualityTests(unittest.TestCase):
    def test_fake_e2e_diversity_reviews_improvement_and_selection(self):
        repository=InMemoryStateRepository();history=ExecutionHistory(state_repository=repository)
        repository,_,_,engine,brief=prepared(repository=repository,history=history)
        self.assertEqual("fake-production",ProviderFactory.production_from_environment({}).provider.provider_name)
        self.assertEqual("fake-quality",ProviderFactory.quality_from_environment({}).provider.provider_name)
        candidates=engine.generate("workspace-a",brief.production_brief_id)
        self.assertEqual(3,len(candidates));self.assertEqual("acceptable",engine.compare(candidates)["status"])
        self.assertNotIn("prompt",str(candidates[0].prompt_metadata).lower());self.assertEqual(0,candidates[0].usage_metadata["estimated_cost_usd"])
        q=rubric(engine);first=candidates[0]
        engine.review("workspace-a",first.candidate_id,q.rubric_id,"music-employee","SELF")
        engine.review("workspace-a",first.candidate_id,q.rubric_id,"qa-reviewer","CROSS")
        score=engine.score("workspace-a",first.candidate_id,q.rubric_id,"qa-employee")
        self.assertFalse(score.passed);self.assertEqual(set(q.criteria),set(score.evidence))
        improvement=engine.improve("workspace-a",first.candidate_id);self.assertEqual(1,improvement.attempt_number)
        rescored=engine.score("workspace-a",first.candidate_id,q.rubric_id,"qa-employee");self.assertTrue(rescored.passed)
        report=engine.select("workspace-a",brief.production_brief_id,first.candidate_id)
        self.assertEqual("APPROVED",report.approval_status);self.assertEqual(first.candidate_id,report.selected_candidate_id)
        self.assertNotIn("Evidence based package",str(repository.list("final_quality_report","workspace-a")))
        records=repository.list("execution","workspace-a");self.assertTrue(any(v.get("task_type")=="QUALITY" for v in records));self.assertNotIn("prompt",str(records).lower())

    def test_workspace_idempotency_restart_and_safe_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            repository=JsonStateRepository(Path(directory)/"state.json");_,_,_,engine,brief=prepared(repository)
            same=engine.create_brief("workspace-a",{"meeting_id":brief.meeting_id,"execution_plan_id":brief.execution_plan_id,"idempotency_key":"brief-one","target_audience":"Changed","concept":"Changed","tone":"Changed"})
            self.assertEqual(brief.production_brief_id,same.production_brief_id)
            engine.generate("workspace-a",brief.production_brief_id)
            self.assertIsNone(engine.get_brief("workspace-b",brief.production_brief_id))
            restored=JsonStateRepository(Path(directory)/"state.json")
            self.assertEqual(3,len(restored.list("production_candidate","workspace-a")))
            with self.assertRaises(ValueError):engine.create_brief("workspace-a",{"meeting_id":brief.meeting_id,"execution_plan_id":brief.execution_plan_id,"idempotency_key":"unsafe","target_audience":"Audience","concept":"Concept","tone":"Tone","metadata":{"nested":{"authorization":"Bearer private"}}})

    def test_duplicate_seed_only_artifact_and_rubric_validation(self):
        _,_,_,engine,brief=prepared();values=engine.generate("workspace-a",brief.production_brief_id)
        duplicate=Candidate(**{**values[1].to_dict(),"artifact_ids":tuple(values[1].artifact_ids),"diversity_profile":{**values[0].diversity_profile,"variation_metadata":{"seed":999}}})
        self.assertEqual("duplicate",engine.compare([values[0],duplicate])["status"])
        reused=Candidate(**{**values[1].to_dict(),"artifact_ids":("artifact-old",)})
        self.assertEqual("duplicate",engine.compare([values[0],reused],("artifact-old",))["status"])
        with self.assertRaises(ValueError):engine.create_rubric("workspace-a",{"rubric_id":"bad","criteria":{}})

    def test_role_separation_limits_and_safe_provider_failures(self):
        repository,organization,intelligence,engine,brief=prepared();candidate=engine.generate("workspace-a",brief.production_brief_id)[0];q=rubric(engine)
        with self.assertRaises(ValueError):engine.review("workspace-a",candidate.candidate_id,q.rubric_id,"music-employee","CROSS")
        class Slow(FakeProductionProvider):
            def generate(self,*args):time.sleep(.05);return super().generate(*args)
        timed=ProductionQualityEngine(repository,intelligence,organization,Slow(),timeout_seconds=.001)
        other=timed.create_brief("workspace-a",{"meeting_id":brief.meeting_id,"execution_plan_id":brief.execution_plan_id,"idempotency_key":"slow","target_audience":"Audience","concept":"Concept","tone":"Tone"})
        with self.assertRaisesRegex(ValueError,"safe provider timeout"):timed.generate("workspace-a",other.production_brief_id)
        class Broken(FakeProductionProvider):
            def generate(self,*args):raise RuntimeError("private provider detail")
        broken=ProductionQualityEngine(repository,intelligence,organization,Broken())
        third=broken.create_brief("workspace-a",{"meeting_id":brief.meeting_id,"execution_plan_id":brief.execution_plan_id,"idempotency_key":"broken","target_audience":"Audience","concept":"Concept","tone":"Tone"})
        with self.assertRaisesRegex(ValueError,"safe provider failure"):broken.generate("workspace-a",third.production_brief_id)

    def test_missing_usage_hard_fail_and_repeated_improvement_stop(self):
        repository,organization,intelligence,_,base=prepared()
        class MissingUsage(FakeProductionProvider):
            def generate(self,brief,count):
                value=super().generate(brief,count);value.pop("usage",None);return value
        class HardFailQuality:
            def review(self,candidate,kind):return {"summary":"Structured technical review.","checks":{"technical_validation":False}}
        engine=ProductionQualityEngine(repository,intelligence,organization,MissingUsage(),HardFailQuality())
        brief=engine.create_brief("workspace-a",{"meeting_id":base.meeting_id,"execution_plan_id":base.execution_plan_id,"idempotency_key":"missing-usage","target_audience":"Audience","concept":"Concept","tone":"Tone"})
        candidate=engine.generate("workspace-a",brief.production_brief_id)[0]
        self.assertNotIn("input_tokens",candidate.usage_metadata)
        q=rubric(engine,threshold=.91)
        engine.review("workspace-a",candidate.candidate_id,q.rubric_id,"music-employee","SELF");engine.review("workspace-a",candidate.candidate_id,q.rubric_id,"qa-reviewer","CROSS")
        score=engine.score("workspace-a",candidate.candidate_id,q.rubric_id,"qa-employee");self.assertFalse(score.passed);self.assertIn("technical_validation",score.hard_failures)
        engine.improve("workspace-a",candidate.candidate_id)
        with self.assertRaisesRegex(ValueError,"improvement stopped"):engine.improve("workspace-a",candidate.candidate_id)


class ProductionQualityApiTests(unittest.TestCase):
    def test_authenticated_fake_api_and_workspace_isolation(self):
        users=UserService();owner=users.create("owner@example.com");outsider=users.create("out@example.com");credentials=CredentialService(users)
        for user in (owner,outsider):credentials.set_password(user["user_id"],"safe-passphrase")
        sessions=SessionService();login=LoginService(users,credentials,SignedAccessTokenProvider(secret="injected-test-secret"),sessions)
        workspaces=WorkspaceService();memberships=WorkspaceMembershipService(workspaces,users);workspace=memberships.create_workspace("Owned",owner["user_id"]);memberships.create_workspace("Other",outsider["user_id"])
        repository,_,_,engine,brief=prepared(workspace=workspace["workspace_id"]);service=ProductionQualityService(engine)
        client=TestClient(create_app(workspace_service=workspaces,user_service=users,membership_service=memberships,credential_service=credentials,login_service=login,session_service=sessions,production_quality_service=service,auth_required=True))
        def auth(email):return {"Authorization":"Bearer "+client.post("/auth/login",json={"email":email,"password":"safe-passphrase"}).json()["access_token"]}
        base=f"/workspaces/{workspace['workspace_id']}";owner_h=auth("owner@example.com");out_h=auth("out@example.com")
        self.assertEqual(403,client.get(base+"/production/briefs",headers=out_h).status_code)
        generated=client.post(base+f"/production/briefs/{brief.production_brief_id}/candidates",json={},headers=owner_h);self.assertEqual(3,len(generated.json()["items"]))
        self.assertEqual("acceptable",client.get(base+f"/production/briefs/{brief.production_brief_id}/comparison",headers=owner_h).json()["status"])


if __name__=="__main__":unittest.main()
