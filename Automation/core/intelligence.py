from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
import uuid

from providers.intelligence import FakeMeetingProvider, FakeResearchProvider


RESEARCH_TYPES = {"MARKET","TREND","COMPETITOR","PLATFORM","KEYWORD","AUDIENCE","SEASONAL","COMPANY_MEMORY"}
RESEARCH_STATUSES = {"PENDING","RUNNING","COMPLETED","PARTIAL","FAILED"}
MEETING_STATUSES = {"PENDING","RUNNING","COMPLETED","FAILED","USER_INPUT_REQUIRED"}
PARTICIPANT_ROLES = {"CEO","MANAGER","RESEARCHER","PLANNER","CREATOR","REVIEWER"}
CONTRIBUTION_TYPES = {"ANALYSIS","PROPOSAL","RISK","REVIEW","REVISION","RECOMMENDATION"}
SELECTION_METHODS = {"RULE_BASED","USER_SELECTED","MANAGER_SELECTED","CONSENSUS"}
_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,180}$")
_TEXT = re.compile(r"^[^\r\n]{1,500}$")
_FORBIDDEN = ("api_key","oauth","authorization","cookie","password","secret","token")


@dataclass(frozen=True)
class ResearchRequest:
    research_request_id: str; workspace_id: str; project_id: str; assignment_id: str
    organization_metadata: dict; research_types: tuple[str,...]; topic: str
    target_platforms: tuple[str,...]; target_audience: str; region: str; language: str
    time_range: dict; requested_at: str; bible_version_metadata: dict; metadata: dict
    def to_dict(self): return _serialize(self)
    @classmethod
    def from_dict(cls, value): return _load(cls,value,("research_types","target_platforms"))


@dataclass(frozen=True)
class ResearchSource:
    source_id: str; source_type: str; title: str; provider: str; published_at: str
    retrieved_at: str; reference: str; structured_summary: str; relevance: str
    freshness: str; quality: str; access_status: str; metadata: dict
    def to_dict(self): return _serialize(self)
    @classmethod
    def from_dict(cls,value): return _load(cls,value)


@dataclass(frozen=True)
class ResearchFinding:
    finding_id: str; category: str; claim: str; supporting_source_ids: tuple[str,...]
    evidence_summary: str; confidence_level: str; limitations: tuple[str,...]
    observed_at: str; disagreement: str | None; metadata: dict
    def to_dict(self): return _serialize(self)
    @classmethod
    def from_dict(cls,value): return _load(cls,value,("supporting_source_ids","limitations"))


@dataclass(frozen=True)
class Confidence:
    source_count: int; source_quality: str; source_freshness: str
    source_agreement: str; coverage: str; uncertainty_notes: tuple[str,...]
    confidence_level: str
    def to_dict(self): return _serialize(self)
    @classmethod
    def from_dict(cls,value): return _load(cls,value,("uncertainty_notes",))


@dataclass(frozen=True)
class ResearchReport:
    report_id: str; workspace_id: str; project_id: str; research_request_id: str
    assignment_id: str; status: str; generated_at: str; research_window: dict
    executive_summary: str; findings: tuple[ResearchFinding,...]
    market_insights: tuple[str,...]; trend_insights: tuple[str,...]
    platform_insights: tuple[str,...]; competitor_insights: tuple[str,...]
    keyword_insights: tuple[str,...]; audience_insights: tuple[str,...]
    seasonal_insights: tuple[str,...]; company_memory_insights: tuple[str,...]
    recommendations: tuple[str,...]; confidence: Confidence; limitations: tuple[str,...]
    source_ids: tuple[str,...]; bible_version_metadata: dict
    organization_metadata: dict; usage_metadata: dict; metadata: dict
    def to_dict(self): return _serialize(self)
    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,dict): return None
        data=dict(value); data["findings"]=tuple(filter(None,(ResearchFinding.from_dict(v) for v in data.get("findings",())))); data["confidence"]=Confidence.from_dict(data.get("confidence"))
        return _load(cls,data,("findings","market_insights","trend_insights","platform_insights","competitor_insights","keyword_insights","audience_insights","seasonal_insights","company_memory_insights","recommendations","limitations","source_ids"))


@dataclass(frozen=True)
class Meeting:
    meeting_id: str; workspace_id: str; project_id: str; research_report_id: str
    bible_version_metadata: dict; status: str; purpose: str
    participant_ids: tuple[str,...]; agenda: tuple[str,...]; started_at: str
    completed_at: str | None; metadata: dict
    def to_dict(self): return _serialize(self)
    @classmethod
    def from_dict(cls,value): return _load(cls,value,("participant_ids","agenda"))


@dataclass(frozen=True)
class MeetingContribution:
    contribution_id: str; meeting_id: str; participant_id: str; role_type: str
    contribution_type: str; summary: str; evidence_source_ids: tuple[str,...]
    referenced_finding_ids: tuple[str,...]; bible_version_metadata: dict
    created_at: str; metadata: dict
    def to_dict(self): return _serialize(self)
    @classmethod
    def from_dict(cls,value): return _load(cls,value,("evidence_source_ids","referenced_finding_ids"))


@dataclass(frozen=True)
class MeetingMinutes:
    minutes_id: str; meeting_id: str; participants: tuple[str,...]
    agenda: tuple[str,...]; evidence_reviewed: tuple[str,...]; proposals: tuple[str,...]
    disagreements: tuple[str,...]; risks: tuple[str,...]; decisions: tuple[str,...]
    unresolved_items: tuple[str,...]; user_report: str; generated_at: str; metadata: dict
    def to_dict(self): return _serialize(self)
    @classmethod
    def from_dict(cls,value): return _load(cls,value,("participants","agenda","evidence_reviewed","proposals","disagreements","risks","decisions","unresolved_items"))


@dataclass(frozen=True)
class Plan:
    plan_id: str; meeting_id: str; title: str; concept: str; target_audience: str
    platform_strategy: str; content_direction: str; music_direction: str
    image_direction: str; video_direction: str; marketing_direction: str
    expected_outputs: tuple[str,...]; supporting_finding_ids: tuple[str,...]
    risks: tuple[str,...]; differentiation: str; feasibility: int
    estimated_usage: dict; metadata: dict
    def to_dict(self): return _serialize(self)
    @classmethod
    def from_dict(cls,value): return _load(cls,value,("expected_outputs","supporting_finding_ids","risks"))


@dataclass(frozen=True)
class Decision:
    decision_id: str; meeting_id: str; selected_plan_id: str; selection_method: str
    rationale: str; supporting_finding_ids: tuple[str,...]; rejected_plan_ids: tuple[str,...]
    rejection_reasons: tuple[str,...]; risks: tuple[str,...]; limitations: tuple[str,...]
    user_approval_required: bool; decided_at: str; metadata: dict
    def to_dict(self): return _serialize(self)
    @classmethod
    def from_dict(cls,value): return _load(cls,value,("supporting_finding_ids","rejected_plan_ids","rejection_reasons","risks","limitations"))


@dataclass(frozen=True)
class ExecutionPlan:
    execution_plan_id: str; workspace_id: str; workflow_id: str; decision_id: str
    selected_plan_id: str; ordered_steps: tuple[str,...]; required_inputs: tuple[str,...]
    expected_outputs: tuple[str,...]; platform_targets: tuple[str,...]
    approval_boundaries: tuple[str,...]; bible_version_metadata: dict
    research_report_id: str; meeting_id: str; created_at: str; metadata: dict
    def to_dict(self): return _serialize(self)
    @classmethod
    def from_dict(cls,value): return _load(cls,value,("ordered_steps","required_inputs","expected_outputs","platform_targets","approval_boundaries"))


class IntelligenceEngine:
    def __init__(self, repository, organization, bible_resolver=None, research_provider=None, meeting_provider=None, memory_reader=None, timeout_seconds=10, plan_count=3, clock=None, history=None):
        self.repository=repository; self.organization=organization; self.bibles=bible_resolver
        self.research_provider=research_provider or FakeResearchProvider(); self.meeting_provider=meeting_provider or FakeMeetingProvider(); self.memory_reader=memory_reader
        self.history=history
        self.timeout=float(timeout_seconds); self.plan_count=int(plan_count); self.clock=clock or (lambda:datetime.now(timezone.utc))
        if self.timeout<=0 or not 3<=self.plan_count<=10: raise ValueError("intelligence configuration is invalid")

    def create_research(self, workspace_id, payload):
        workspace_id=_id(workspace_id); payload=dict(payload or {}); key=_id(payload.get("idempotency_key")); request_id="research-"+uuid.uuid5(uuid.NAMESPACE_URL,f"{workspace_id}:{key}").hex
        existing=self._get("research_request",workspace_id,request_id,ResearchRequest)
        if existing:return existing
        organization=_ids(payload.get("organization_metadata"),{"assignment_id","company_id","manager_id","department_id","employee_id"})
        if organization.get("department_id")!="research": raise ValueError("research assignment is required")
        bundle=self.bibles.resolve(workspace_id,"RESEARCH","RESEARCHER") if self.bibles else None
        request=ResearchRequest(request_id,workspace_id,_id(payload.get("project_id") or payload.get("workflow_id")),organization["assignment_id"],organization,tuple(_research_types(payload.get("research_types"))),_safe(payload.get("topic")),tuple(_safe(v) for v in payload.get("target_platforms",())),_safe(payload.get("target_audience")),_safe(payload.get("region")),_safe(payload.get("language")),_time_range(payload.get("time_range")),self._now(),bundle.version_metadata() if bundle else {},_metadata(payload.get("metadata",{})))
        self._save("research_request",request_id,workspace_id,request.to_dict()); return request

    def run_research(self, workspace_id, request_id):
        workspace_id=_id(workspace_id); request=self._required("research_request",workspace_id,request_id,ResearchRequest); report_id="report-"+request.research_request_id
        existing=self._get("research_report",workspace_id,report_id,ResearchReport)
        if existing:return existing
        try:
            collected=self._call(self.research_provider.collect,request); sources=self._sources(collected.data.get("sources",()))
            extracted=self._call(self.research_provider.extract_findings,request,[v.to_dict() for v in sources]) if sources else None
            findings=self._findings(extracted.data.get("findings",()),sources) if extracted else []
            memory=[]
            if self.memory_reader:
                try: memory=list(self.memory_reader(workspace_id,request.project_id) or ())[:10]
                except Exception: memory=[]
            status="COMPLETED" if sources and findings else "PARTIAL"
            confidence=_confidence(sources,findings)
            usage=_usage(collected.usage,extracted.usage if extracted else None)
            limitations=list(confidence.uncertainty_notes)
            if not memory: limitations.append("Company memory unavailable")
            report=ResearchReport(report_id,workspace_id,request.project_id,request.research_request_id,request.assignment_id,status,self._now(),request.time_range,"Evidence-backed research summary." if findings else "No supported findings were produced.",tuple(findings),tuple(v.claim for v in findings if v.category=="MARKET"),(),tuple(v.claim for v in findings if v.category=="PLATFORM"),(),(),(),(),tuple(_safe(v) for v in memory),tuple("Review "+v.finding_id for v in findings),confidence,tuple(limitations),tuple(v.source_id for v in sources),dict(request.bible_version_metadata),dict(request.organization_metadata),usage,{"provider":self.research_provider.provider_name,"fingerprint":_fingerprint([v.to_dict() for v in sources])})
        except (TimeoutError,ValueError):
            report=ResearchReport(report_id,workspace_id,request.project_id,request.research_request_id,request.assignment_id,"FAILED",self._now(),request.time_range,"Research could not complete.",(),(),(),(),(),(),(),(),(),(),Confidence(0,"UNKNOWN","UNKNOWN","UNKNOWN","NONE",("Safe provider failure",),"LOW"),("Safe provider failure",),(),dict(request.bible_version_metadata),dict(request.organization_metadata),{}, {"provider":getattr(self.research_provider,"provider_name","unknown")})
        self._save("research_report",report_id,workspace_id,report.to_dict())
        for source in locals().get("sources",()): self._save("research_source",source.source_id,workspace_id,{**source.to_dict(),"report_id":report_id})
        if self.history:self.history.record_intelligence(workspace_id,report_id,"RESEARCH",report.status,{"research_request_id":request.research_request_id,"research_report_id":report.report_id,"assignment_id":request.assignment_id,**request.organization_metadata},report.executive_summary,report.usage_metadata)
        return report

    def create_meeting(self, workspace_id, payload):
        workspace_id=_id(workspace_id); payload=dict(payload or {}); report=self.get_report(workspace_id,_id(payload.get("research_report_id")))
        if report is None or report.status not in {"COMPLETED","PARTIAL"}: raise ValueError("eligible research report is required")
        if report.status=="PARTIAL" and not payload.get("allow_partial"): raise ValueError("partial report requires explicit policy")
        ids=tuple(_id(v) for v in payload.get("participant_ids",()))
        participants=[self.organization.get_employee(workspace_id,v) for v in ids]
        if not ids or any(v is None or v.role_type not in PARTICIPANT_ROLES for v in participants): raise ValueError("meeting participants are invalid")
        meeting_id="meeting-"+uuid.uuid5(uuid.NAMESPACE_URL,f"{workspace_id}:{_id(payload.get('idempotency_key'))}").hex
        existing=self._get("intelligence_meeting",workspace_id,meeting_id,Meeting)
        if existing:return existing
        meeting=Meeting(meeting_id,workspace_id,report.project_id,report.report_id,dict(report.bible_version_metadata),"PENDING",_safe(payload.get("purpose")),ids,tuple(_safe(v) for v in payload.get("agenda",())),self._now(),None,{"report_fingerprint":_fingerprint(report.to_dict())})
        self._save("intelligence_meeting",meeting_id,workspace_id,meeting.to_dict()); return meeting

    def run_meeting(self, workspace_id, meeting_id):
        workspace_id=_id(workspace_id); meeting=self._required("intelligence_meeting",workspace_id,meeting_id,Meeting); report=self.get_report(workspace_id,meeting.research_report_id)
        if report is None or meeting.metadata.get("report_fingerprint")!=_fingerprint(report.to_dict()): raise ValueError("research report changed")
        participants=[self.organization.get_employee(workspace_id,v) for v in meeting.participant_ids]
        try:
            value=self._call(self.meeting_provider.deliberate,meeting,report,participants,self.plan_count)
            contributions=self._contributions(value.data.get("contributions",()),meeting,report)
            plans=self._plans(value.data.get("plans",()),meeting,report)
            if len(plans)!=self.plan_count or not _diverse(plans): raise ValueError("plans are not diverse")
            minutes=MeetingMinutes("minutes-"+meeting.meeting_id,meeting.meeting_id,meeting.participant_ids,meeting.agenda,report.source_ids,tuple(v.summary for v in contributions if v.contribution_type=="PROPOSAL"),tuple(v.disagreement for v in report.findings if v.disagreement),tuple(v.summary for v in contributions if v.contribution_type=="RISK"),(),report.limitations,"Structured meeting summary based on cited evidence.",self._now(),{"provider":self.meeting_provider.provider_name,"usage":_usage(value.usage)})
            for item in contributions:self._save("meeting_contribution",item.contribution_id,workspace_id,item.to_dict())
            for item in plans:self._save("intelligence_plan",item.plan_id+":"+meeting.meeting_id,workspace_id,item.to_dict())
            self._save("meeting_minutes",minutes.minutes_id,workspace_id,minutes.to_dict())
            completed=Meeting(**{**meeting.to_dict(),"status":"COMPLETED","completed_at":self._now()}); self._save("intelligence_meeting",meeting.meeting_id,workspace_id,completed.to_dict())
            if self.history:self.history.record_intelligence(workspace_id,meeting.meeting_id,"MEETING",completed.status,{"meeting_id":meeting.meeting_id,"research_report_id":report.report_id},minutes.user_report,minutes.metadata.get("usage"))
            return completed
        except (TimeoutError,ValueError):
            failed=Meeting(**{**meeting.to_dict(),"status":"FAILED","completed_at":self._now()}); self._save("intelligence_meeting",meeting.meeting_id,workspace_id,failed.to_dict()); return failed

    def decide(self, workspace_id, meeting_id, plan_id=None):
        workspace_id=_id(workspace_id); meeting=self._required("intelligence_meeting",workspace_id,meeting_id,Meeting)
        if meeting.status!="COMPLETED" and not (meeting.status=="USER_INPUT_REQUIRED" and plan_id is not None): raise ValueError("meeting is incomplete")
        plans=self.list_plans(workspace_id,meeting_id); report=self.get_report(workspace_id,meeting.research_report_id)
        if plan_id is None:
            ranked=sorted(plans,key=lambda v:(-v.feasibility,v.plan_id)); selected=ranked[0]; method="RULE_BASED"
            if len(ranked)>1 and ranked[0].feasibility==ranked[1].feasibility:
                pending=Meeting(**{**meeting.to_dict(),"status":"USER_INPUT_REQUIRED"}); self._save("intelligence_meeting",meeting.meeting_id,workspace_id,pending.to_dict()); return None
        else:
            selected=next((v for v in plans if v.plan_id==plan_id),None); method="USER_SELECTED"
            if selected is None: raise ValueError("plan not found")
        rejected=[v for v in plans if v.plan_id!=selected.plan_id]
        decision=Decision("decision-"+meeting.meeting_id,meeting.meeting_id,selected.plan_id,method,"Selected by bounded feasibility rule with cited findings.",selected.supporting_finding_ids,tuple(v.plan_id for v in rejected),tuple("Lower bounded feasibility" for _ in rejected),selected.risks,report.limitations,False,self._now(),{})
        self._save("intelligence_decision",decision.decision_id,workspace_id,decision.to_dict())
        execution=ExecutionPlan("execution-"+meeting.meeting_id,workspace_id,meeting.project_id,decision.decision_id,selected.plan_id,("PLANNING","MUSIC","IMAGE","BLOG","VIDEO","YOUTUBE","NAVER"),("manual_suno_audio",),selected.expected_outputs,("youtube","naver"),("manual_suno","private_youtube","manual_naver_publish"),dict(meeting.bible_version_metadata),report.report_id,meeting.meeting_id,self._now(),{})
        self._save("intelligence_execution_plan",execution.execution_plan_id,workspace_id,execution.to_dict()); return decision

    def get_request(self,w,r): return self._get("research_request",w,r,ResearchRequest)
    def get_report(self,w,r): return self._get("research_report",w,r,ResearchReport)
    def list_reports(self,w,project_id=None): return [v for v in self._list("research_report",w,ResearchReport) if project_id is None or v.project_id==project_id]
    def list_sources(self,w,report_id): return [v for raw in self.repository.list("research_source",_id(w)) if raw.get("report_id")==report_id and (v:=ResearchSource.from_dict({k:x for k,x in raw.items() if k!="report_id"}))]
    def get_meeting(self,w,m): return self._get("intelligence_meeting",w,m,Meeting)
    def list_meetings(self,w): return self._list("intelligence_meeting",w,Meeting)
    def get_minutes(self,w,m):
        value=MeetingMinutes.from_dict(self.repository.get("meeting_minutes","minutes-"+_id(m),_id(w)))
        return value.to_dict() if value else None
    def list_plans(self,w,m): return [v for v in self._list("intelligence_plan",w,Plan) if v.meeting_id==m]
    def get_decision(self,w,m): return self._get("intelligence_decision",w,"decision-"+m,Decision)
    def get_execution_plan(self,w,m): return self._get("intelligence_execution_plan",w,"execution-"+m,ExecutionPlan)
    def _sources(self,values):
        result={}
        for raw in values:
            value=ResearchSource.from_dict(raw)
            if value: result[value.reference]=value
        return list(result.values())
    def _findings(self,values,sources):
        ids={v.source_id for v in sources}; result=[]
        for raw in values:
            value=ResearchFinding.from_dict(raw)
            if value is None or not value.supporting_source_ids or not set(value.supporting_source_ids)<=ids: raise ValueError("finding sources are invalid")
            result.append(value)
        return result
    def _contributions(self,values,meeting,report):
        source_ids=set(report.source_ids); finding_ids={v.finding_id for v in report.findings}; result=[]
        for raw in values:
            value=MeetingContribution.from_dict(raw)
            if value is None or value.meeting_id!=meeting.meeting_id or value.participant_id not in meeting.participant_ids or value.role_type not in PARTICIPANT_ROLES or value.contribution_type not in CONTRIBUTION_TYPES or not value.evidence_source_ids or not set(value.evidence_source_ids)<=source_ids or not set(value.referenced_finding_ids)<=finding_ids: raise ValueError("contribution evidence is invalid")
            result.append(value)
        return result
    def _plans(self,values,meeting,report):
        ids={v.finding_id for v in report.findings}; result=[]
        for raw in values:
            value=Plan.from_dict(raw)
            if value is None or value.meeting_id!=meeting.meeting_id or not value.supporting_finding_ids or not set(value.supporting_finding_ids)<=ids: raise ValueError("plan evidence is invalid")
            result.append(value)
        return result
    def _call(self,function,*args):
        executor=ThreadPoolExecutor(max_workers=1)
        try:return executor.submit(function,*args).result(timeout=self.timeout)
        except FutureTimeout: raise TimeoutError("provider timeout") from None
        except Exception: raise ValueError("safe provider failure") from None
        finally:executor.shutdown(wait=False,cancel_futures=True)
    def _save(self,k,i,w,v): self.repository.save(k,i,w,v)
    def _get(self,k,w,i,c):
        value=c.from_dict(self.repository.get(k,_id(i),_id(w)))
        return value if value and getattr(value,"workspace_id",w)==w else None
    def _required(self,k,w,i,c):
        value=self._get(k,w,i,c)
        if value is None:raise ValueError("record not found")
        return value
    def _list(self,k,w,c): return sorted((v for raw in self.repository.list(k,_id(w)) if (v:=c.from_dict(raw))),key=lambda v:getattr(v,"generated_at",getattr(v,"started_at","")),reverse=True)
    def _now(self):
        value=self.clock()
        if value.tzinfo is None:raise ValueError("clock must be aware")
        return value.isoformat()


def _serialize(value):
    result=asdict(value)
    for key,item in list(result.items()):
        if isinstance(item,tuple):result[key]=list(item)
    return result
def _load(cls,value,tuple_fields=()):
    if not isinstance(value,dict):return None
    try:
        data=dict(value)
        for key in tuple_fields:data[key]=tuple(data.get(key,()))
        item=cls(**data); _validate(item); return item
    except (KeyError,TypeError,ValueError):return None
def _validate(value):
    for key,item in asdict(value).items():
        if key.endswith("_id") and item is not None:_id(item)
        elif key.endswith("_at") and item is not None:
            parsed=datetime.fromisoformat(item)
            if parsed.tzinfo is None:raise ValueError
        elif key=="status":
            allowed=RESEARCH_STATUSES|MEETING_STATUSES
            if item not in allowed:raise ValueError
        elif key=="metadata":_metadata(item)
        elif key in {"title","provider","reference","structured_summary","claim","evidence_summary","disagreement","purpose","user_report","rationale","concept","target_audience","platform_strategy","content_direction","music_direction","image_direction","video_direction","marketing_direction","differentiation"} and item is not None:
            _safe(item)
    if isinstance(value,ResearchSource) and value.published_at>value.retrieved_at:raise ValueError("source time is invalid")
    if isinstance(value,Confidence) and (value.source_count<0 or not value.uncertainty_notes):raise ValueError("confidence basis is invalid")
    if isinstance(value,Decision) and value.selection_method not in SELECTION_METHODS:raise ValueError("selection method is invalid")
def _id(v):
    if not isinstance(v,str) or not _ID.fullmatch(v):raise ValueError("identifier is invalid")
    return v
def _safe(v):
    if not isinstance(v,str) or not _TEXT.fullmatch(v) or any(x in v.lower() for x in _FORBIDDEN) or re.search(r"[A-Za-z]:[\\/]",v):raise ValueError("safe text is invalid")
    return v.strip()
def _metadata(v):
    usage_keys={"input_tokens","output_tokens","total_tokens","estimated_cost_usd"}
    if not isinstance(v,dict) or any(str(k).lower() not in usage_keys and any(x in str(k).lower() for x in _FORBIDDEN) for k in v):raise ValueError("metadata is invalid")
    return {str(key):_meta_value(value) for key,value in v.items()}
def _meta_value(value):
    if isinstance(value,str):return _safe(value)
    if isinstance(value,(int,float,bool,type(None))):return value
    if isinstance(value,dict):return _metadata(value)
    if isinstance(value,(list,tuple)):return [_meta_value(item) for item in value]
    raise ValueError("metadata is invalid")
def _ids(v,required):
    if not isinstance(v,dict) or set(v)!=required:raise ValueError("organization metadata is invalid")
    return {k:_id(x) for k,x in v.items()}
def _research_types(v):
    values={_id(x).upper() for x in (v or ())}
    if not values or not values<=RESEARCH_TYPES:raise ValueError("research types are invalid")
    return sorted(values)
def _time_range(v):
    if not isinstance(v,dict) or set(v)!={"start","end"}:raise ValueError("time range is invalid")
    start=datetime.fromisoformat(v["start"]);end=datetime.fromisoformat(v["end"])
    if start.tzinfo is None or end.tzinfo is None or start>end:raise ValueError("time range is invalid")
    return dict(v)
def _confidence(sources,findings):
    count=len(sources); agreement="DISAGREEMENT" if any(v.disagreement for v in findings) else "CONSISTENT"
    return Confidence(count,"TEST_FIXTURE" if count else "UNKNOWN","CURRENT" if count else "UNKNOWN",agreement,"PARTIAL" if count<3 else "BROAD",("Confidence is evidence-limited and not a fact guarantee.",),"MODERATE" if count and findings else "LOW")
def _usage(*values):
    result={"provider":"fake-intelligence","model":"deterministic-v1","estimated_cost_usd":0.0}
    tokens=[getattr(v,"total_tokens",None) if v is not None else None for v in values]
    if any(v is not None for v in tokens):result["total_tokens"]=sum(v or 0 for v in tokens)
    return result
def _fingerprint(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()).hexdigest()
def _diverse(plans):
    signatures={(v.concept,v.target_audience,v.platform_strategy,v.image_direction,v.content_direction,tuple(v.risks)) for v in plans}
    return len(signatures)==len(plans)
