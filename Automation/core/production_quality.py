from concurrent.futures import ThreadPoolExecutor,TimeoutError as FutureTimeout
from dataclasses import asdict,dataclass,is_dataclass
from datetime import datetime,timezone
import hashlib,json,re,uuid
from providers.production_quality import FakeProductionProvider,FakeQualityProvider

TYPES={"TEXT","IMAGE","VIDEO","METADATA","PACKAGE"};STATUSES={"PENDING","GENERATED","REJECTED","APPROVED","IMPROVED"}
CRITERIA=("order_fulfillment","bible_alignment","research_alignment","originality","platform_fit","technical_validity","safety","consistency")
_ID=re.compile(r"^[A-Za-z0-9_.:-]{1,180}$");_PATH=re.compile(r"(?:[A-Za-z]:[\\/]|^/)");_BAD=("prompt","secret","token","password","cookie","authorization","api_key","oauth")
_SAFE_USAGE={"provider","model","input_tokens","output_tokens","total_tokens","estimated_cost","estimated_cost_usd"}
def now():return datetime.now(timezone.utc).isoformat()
def ident(v):
 if not isinstance(v,str) or not _ID.fullmatch(v):raise ValueError("invalid identifier")
 return v
def safe(v):
 if not isinstance(v,str) or not v.strip() or len(v)>500 or _PATH.search(v) or any(x in v.lower() for x in _BAD):raise ValueError("unsafe text")
 return v.strip()
def meta(v):
 if not isinstance(v,dict):raise ValueError("unsafe metadata")
 def clean(value):
  if isinstance(value,dict):
   if any(str(k).lower() not in _SAFE_USAGE and any(x in str(k).lower() for x in _BAD) for k in value):raise ValueError("unsafe metadata")
   return {str(k):clean(item) for k,item in value.items()}
  if isinstance(value,(list,tuple)):return [clean(item) for item in value]
  if isinstance(value,str):
   if _PATH.search(value) or any(x in value.lower() for x in ("bearer ","client_secret")):raise ValueError("unsafe metadata")
   return value
  if isinstance(value,(int,float,bool,type(None))):return value
  raise ValueError("unsafe metadata")
 return clean(v)
def serial(v):
 x=asdict(v)
 for k,i in list(x.items()):
  if isinstance(i,tuple):x[k]=list(i)
 return x
def load(cls,v,tuples=()):
 if not isinstance(v,dict):return None
 try:
  x=dict(v)
  for k in tuples:x[k]=tuple(x.get(k,()))
  return cls(**x)
 except (TypeError,ValueError):return None

@dataclass(frozen=True)
class ProductionBrief:
 production_brief_id:str;workspace_id:str;project_id:str;execution_plan_id:str;research_report_id:str;meeting_id:str;decision_id:str;bible_version_metadata:dict;target_platforms:tuple[str,...];target_audience:str;concept:str;tone:str;required_outputs:tuple[str,...];prohibited_patterns:tuple[str,...];diversity_requirements:dict;quality_requirements:dict;created_at:str;metadata:dict
 def to_dict(self):return serial(self)
 @classmethod
 def from_dict(c,v):return load(c,v,("target_platforms","required_outputs","prohibited_patterns"))
@dataclass(frozen=True)
class Candidate:
 candidate_id:str;workspace_id:str;production_brief_id:str;candidate_type:str;variant_index:int;concept_variant:str;text_direction:str;visual_direction:str;platform_strategy:str;prompt_metadata:dict;diversity_profile:dict;artifact_ids:tuple[str,...];usage_metadata:dict;status:str;created_at:str;metadata:dict
 def to_dict(self):return serial(self)
 @classmethod
 def from_dict(c,v):return load(c,v,("artifact_ids",))
@dataclass(frozen=True)
class QualityRubric:
 rubric_id:str;workspace_id:str;department_type:str;output_type:str;version:str;criteria:dict;threshold:float;hard_fail_rules:tuple[str,...];bible_version_metadata:dict;created_at:str;metadata:dict
 def to_dict(self):return serial(self)
 @classmethod
 def from_dict(c,v):return load(c,v,("hard_fail_rules",))
@dataclass(frozen=True)
class QualityScore:
 score_id:str;workspace_id:str;candidate_id:str;rubric_id:str;criterion_scores:dict;total_score:float;threshold:float;passed:bool;hard_failures:tuple[str,...];evidence:dict;limitations:tuple[str,...];reviewer_id:str;created_at:str;metadata:dict
 def to_dict(self):return serial(self)
 @classmethod
 def from_dict(c,v):return load(c,v,("hard_failures","limitations"))
@dataclass(frozen=True)
class ImprovementRequest:
 improvement_request_id:str;workspace_id:str;candidate_id:str;score_id:str;failed_criteria:tuple[str,...];required_changes:tuple[str,...];prohibited_changes:tuple[str,...];attempt_number:int;max_attempts:int;created_at:str;metadata:dict
 def to_dict(self):return serial(self)
 @classmethod
 def from_dict(c,v):return load(c,v,("failed_criteria","required_changes","prohibited_changes"))
@dataclass(frozen=True)
class FinalQualityReport:
 final_report_id:str;workspace_id:str;production_brief_id:str;bible_version_metadata:dict;research_report_id:str;selected_plan_id:str;candidate_count:int;candidate_differences:dict;reviews:tuple[dict,...];scores:tuple[dict,...];improvement_count:int;limitations:tuple[str,...];selected_candidate_id:str|None;approval_status:str;created_at:str
 def to_dict(self):return serial(self)
 @classmethod
 def from_dict(c,v):return load(c,v,("reviews","scores","limitations"))

class ProductionQualityEngine:
 def __init__(self,repository,intelligence,organization,production_provider=None,quality_provider=None,timeout_seconds=10,candidate_count=3,max_attempts=2,usage_limit=1000,history=None):
  self.r=repository;self.i=intelligence;self.o=organization;self.p=production_provider or FakeProductionProvider();self.q=quality_provider or FakeQualityProvider();self.timeout=float(timeout_seconds);self.count=int(candidate_count);self.max=int(max_attempts);self.limit=float(usage_limit);self.history=history
  if self.timeout<=0 or not 3<=self.count<=10 or not 1<=self.max<=5:raise ValueError("invalid production quality configuration")
 def create_brief(self,w,p):
  w=ident(w);p=dict(p or {});execution=self.i.get_execution_plan(w,ident(p.get("meeting_id")))
  if execution is None or execution.execution_plan_id!=p.get("execution_plan_id"):raise ValueError("execution plan required")
  bid="brief-"+uuid.uuid5(uuid.NAMESPACE_URL,f"{w}:{ident(p.get('idempotency_key'))}").hex;old=self.get_brief(w,bid)
  if old:return old
  b=ProductionBrief(bid,w,execution.workflow_id,execution.execution_plan_id,execution.research_report_id,execution.meeting_id,execution.decision_id,dict(execution.bible_version_metadata),tuple(safe(x) for x in p.get("target_platforms",execution.platform_targets)),safe(p.get("target_audience")),safe(p.get("concept")),safe(p.get("tone")),tuple(safe(x) for x in p.get("required_outputs",execution.expected_outputs)),tuple(safe(x) for x in p.get("prohibited_patterns",())),meta(p.get("diversity_requirements",{})),meta(p.get("quality_requirements",{})),now(),meta(p.get("metadata",{})));self._save("production_brief",bid,w,b.to_dict());return b
 def generate(self,w,bid,kind="PACKAGE"):
  w=ident(w);b=self._req("production_brief",w,bid,ProductionBrief);kind=ident(kind).upper()
  if kind not in TYPES:raise ValueError("candidate type invalid")
  existing=self.list_candidates(w,b.production_brief_id)
  if existing:return existing
  result=self._call(self.p.generate,b,self.count);values=[]
  for index,raw in enumerate(result.get("candidates",())):
   profile={"concept":safe(raw["concept_variant"]),"wording":safe(raw["text_direction"]),"title_pattern":f"title-{index+1}","opening_pattern":f"opening-{index+1}","tone":b.tone,"visual_subject":raw["visual_direction"].split(":")[0],"composition":f"composition-{index+1}","camera_angle":f"angle-{index+1}","palette":f"palette-{index+1}","lighting":f"lighting-{index+1}","platform_strategy":safe(raw["platform_strategy"]),"variation_metadata":raw.get("variation_metadata",{})}
   usage=raw.get("usage_metadata") or result.get("usage") or {}
   if hasattr(usage,"to_dict"):usage=usage.to_dict()
   elif is_dataclass(usage):usage=asdict(usage)
   usage={"provider":self.p.provider_name,"model":"fake-structured-v1",**usage}
   c=Candidate(f"candidate-{b.production_brief_id}-{index+1}",w,b.production_brief_id,kind,index+1,safe(raw["concept_variant"]),safe(raw["text_direction"]),safe(raw["visual_direction"]),safe(raw["platform_strategy"]),{"fingerprint":safe(raw["prompt_fingerprint"]),"bible_versions":dict(b.bible_version_metadata)},profile,(),meta(usage),"GENERATED",now(),{});values.append(c)
  if self.compare(values)["status"]!="acceptable":raise ValueError("candidate diversity failed")
  for c in values:
   self._save("production_candidate",c.candidate_id,w,c.to_dict());self._history(w,c.candidate_id,"PRODUCTION","COMPLETED",{"candidate_id":c.candidate_id,"production_brief_id":b.production_brief_id},"Candidate generated",c.usage_metadata)
  return values
 def compare(self,candidates,recent=()):
  if len(candidates)<2:return {"status":"insufficient_data","differences":[]}
  profiles=[c.diversity_profile for c in candidates];keys=("concept","wording","title_pattern","opening_pattern","visual_subject","composition","camera_angle","palette","platform_strategy")
  duplicate=any(all(a.get(k)==b.get(k) for k in keys) for n,a in enumerate(profiles) for b in profiles[n+1:])
  seed_only=any(all(a.get(k)==b.get(k) for k in keys) and a.get("variation_metadata")!=b.get("variation_metadata") for n,a in enumerate(profiles) for b in profiles[n+1:])
  artifact_repeat=any(set(c.artifact_ids)&set(recent) for c in candidates)
  title_repeat=len({p.get("title_pattern") for p in profiles})<len(profiles);visual_repeat=len({(p.get("visual_subject"),p.get("composition"),p.get("palette")) for p in profiles})<len(profiles)
  status="duplicate" if duplicate or seed_only or artifact_repeat else "near_duplicate" if title_repeat or visual_repeat else "acceptable"
  return {"status":status,"differences":[{k:len({p.get(k) for p in profiles}) for k in keys}],"artifact_reuse":artifact_repeat}
 def create_rubric(self,w,p):
  w=ident(w);p=dict(p or {});criteria=p.get("criteria",{});weights={k:float(criteria.get(k,{}).get("weight",0)) for k in CRITERIA}
  if set(criteria)!=set(CRITERIA) or abs(sum(weights.values())-1)>1e-6 or any(not criteria[k].get("description") for k in CRITERIA):raise ValueError("rubric criteria invalid")
  x=QualityRubric(ident(p.get("rubric_id")),w,"QA",safe(p.get("output_type")),safe(p.get("version")),criteria,float(p.get("threshold",.8)),tuple(safe(v) for v in p.get("hard_fail_rules",())),dict(p.get("bible_version_metadata",{})),now(),{});self._save("quality_rubric",x.rubric_id,w,x.to_dict());return x
 def review(self,w,cid,rubric_id,reviewer_id,kind):
  w=ident(w);c=self._req("production_candidate",w,cid,Candidate);reviewer=self.o.get_employee(w,ident(reviewer_id));kind=ident(kind).upper()
  if reviewer is None or (kind=="SELF" and reviewer.role_type!="CREATOR") or (kind=="CROSS" and reviewer.role_type not in {"REVIEWER","QA","PLANNER"}):raise ValueError("reviewer role invalid")
  value=self._call(self.q.review,c,kind);record={"review_id":f"{kind.lower()}-{cid}","workspace_id":w,"candidate_id":cid,"review_type":kind,"reviewer_id":reviewer.employee_id,"summary":safe(value["summary"]),"checks":value["checks"],"created_at":now()};self._save("quality_review",record["review_id"],w,record);return record
 def score(self,w,cid,rubric_id,reviewer_id):
  w=ident(w);c=self._req("production_candidate",w,cid,Candidate);rubric=self._req("quality_rubric",w,rubric_id,QualityRubric);reviewer=self.o.get_employee(w,ident(reviewer_id))
  if reviewer is None or reviewer.role_type!="QA":raise ValueError("QA reviewer required")
  self_review=self.r.get("quality_review","self-"+cid,w);cross=self.r.get("quality_review","cross-"+cid,w)
  if not self_review or not cross:raise ValueError("reviews required")
  criterion={};evidence={};hard=[]
  for name in CRITERIA:
   value=.65 if name=="originality" and c.variant_index==1 and c.status!="IMPROVED" else .9;criterion[name]=value;evidence[name]=f"Structured {name} evidence from reviews."
  if not cross["checks"].get("technical_validation",False):hard.append("technical_validation")
  total=sum(criterion[k]*float(rubric.criteria[k]["weight"]) for k in CRITERIA);passed=total>=rubric.threshold and not hard
  score=QualityScore("score-"+cid,w,cid,rubric.rubric_id,criterion,total,rubric.threshold,passed,tuple(hard),evidence,("Quality score evaluates configured evidence and does not guarantee performance.",),reviewer.employee_id,now(),{});self._save("quality_score",score.score_id,w,score.to_dict())
  self._history(w,score.score_id,"QUALITY","COMPLETED",{"candidate_id":cid,"rubric_id":rubric.rubric_id,"score_id":score.score_id,"employee_id":reviewer.employee_id},"Quality evidence evaluated")
  return score
 def improve(self,w,cid):
  w=ident(w);c=self._req("production_candidate",w,cid,Candidate);score=self._req("quality_score",w,"score-"+cid,QualityScore);previous=self.r.list("improvement_request",w);attempt=1+sum(1 for v in previous if v.get("candidate_id")==cid)
  failed=tuple(k for k,v in score.criterion_scores.items() if v<score.threshold)
  if score.passed or not failed or attempt>self.max or attempt*100>self.limit or any(tuple(v.get("failed_criteria",()))==failed for v in previous):raise ValueError("improvement stopped")
  req=ImprovementRequest(f"improvement-{cid}-{attempt}",w,cid,score.score_id,failed,tuple("Improve "+v for v in failed),("Do not change approval boundaries",),attempt,self.max,now(),{});self._save("improvement_request",req.improvement_request_id,w,req.to_dict())
  self._history(w,req.improvement_request_id,"IMPROVEMENT","COMPLETED",{"candidate_id":cid,"score_id":score.score_id,"improvement_request_id":req.improvement_request_id},"Bounded improvement requested")
  improved=Candidate(**{**c.to_dict(),"artifact_ids":tuple(c.artifact_ids),"text_direction":c.text_direction+" revised","status":"IMPROVED","metadata":{"improvement_attempt":attempt}});self._save("production_candidate",cid,w,improved.to_dict());return req
 def final_report(self,w,bid,selected_id=None):
  w=ident(w);b=self._req("production_brief",w,bid,ProductionBrief);candidates=self.list_candidates(w,bid);scores=[self.get_score(w,c.candidate_id) for c in candidates];approved=[c for c,s in zip(candidates,scores) if s and s.passed]
  selected=next((c for c in approved if selected_id in (None,c.candidate_id)),None);status="APPROVED" if selected else "NOT_APPROVED"
  value=FinalQualityReport("quality-report-"+bid,w,bid,b.bible_version_metadata,b.research_report_id,self.i.get_execution_plan(w,b.meeting_id).selected_plan_id,len(candidates),self.compare(candidates),tuple(v for v in self.r.list("quality_review",w) if v.get("candidate_id") in {c.candidate_id for c in candidates}),tuple(s.to_dict() for s in scores if s),len([v for v in self.r.list("improvement_request",w) if v.get("candidate_id") in {c.candidate_id for c in candidates}]),("Quality evaluation does not guarantee external performance.",),selected.candidate_id if selected else None,status,now());self._save("final_quality_report",value.final_report_id,w,value.to_dict());return value
 def get_brief(self,w,i):return self._get("production_brief",w,i,ProductionBrief)
 def list_candidates(self,w,b):return [v for raw in self.r.list("production_candidate",ident(w)) if (v:=Candidate.from_dict(self._candidate_read(raw))) and v.production_brief_id==b]
 def get_candidate(self,w,i):return self._get("production_candidate",w,i,Candidate)
 def get_score(self,w,c):return self._get("quality_score",w,"score-"+c,QualityScore)
 def get_final(self,w,b):return self._get("final_quality_report",w,"quality-report-"+ident(b),FinalQualityReport)
 def select(self,w,b,c):
  value=self.final_report(w,b,c)
  if value.selected_candidate_id!=c:raise ValueError("candidate is not approved")
  return value
 def list_briefs(self,w):return [v for raw in self.r.list("production_brief",ident(w)) if (v:=ProductionBrief.from_dict(raw))]
 def list_reviews(self,w,c):return [v for v in self.r.list("quality_review",ident(w)) if v.get("candidate_id")==c]
 def list_improvements(self,w,c):return [v for v in self.r.list("improvement_request",ident(w)) if v.get("candidate_id")==c]
 def _history(self,w,r,t,s,ids,summary,usage=None):
  if self.history:
   try:self.history.record_intelligence(w,r,t,s,ids,summary,usage)
   except Exception:pass
 def _call(self,f,*a):
  x=ThreadPoolExecutor(max_workers=1)
  try:return x.submit(f,*a).result(timeout=self.timeout)
  except FutureTimeout:raise ValueError("safe provider timeout") from None
  except Exception:raise ValueError("safe provider failure") from None
  finally:x.shutdown(wait=False,cancel_futures=True)
 def _save(self,k,i,w,v):
  if k=="production_candidate":
   v=dict(v);v["generation_fingerprint_metadata"]=v.pop("prompt_metadata",{})
  self.r.save(k,i,w,v)
 def _get(self,k,w,i,c):
  raw=self.r.get(k,ident(i),ident(w));v=c.from_dict(self._candidate_read(raw) if k=="production_candidate" else raw);return v if v and v.workspace_id==w else None
 def _candidate_read(self,v):
  if not isinstance(v,dict):return v
  v=dict(v);v["prompt_metadata"]=v.pop("generation_fingerprint_metadata",{});return v
 def _req(self,k,w,i,c):
  v=self._get(k,w,i,c)
  if not v:raise ValueError("record not found")
  return v
