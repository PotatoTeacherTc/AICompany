class IntelligenceService:
    """Safe application DTO boundary for Research and Meeting Intelligence."""
    def __init__(self, engine): self.engine=engine
    def create_research(self,w,p): return self.engine.create_research(w,p).to_dict()
    def run_research(self,w,r): return self.engine.run_research(w,r).to_dict()
    def get_research(self,w,r):
        value=self.engine.get_request(w,r); return value.to_dict() if value else None
    def get_report(self,w,r):
        value=self.engine.get_report(w,r); return value.to_dict() if value else None
    def list_reports(self,w,project_id=None): return {"items":[v.to_dict() for v in self.engine.list_reports(w,project_id)]}
    def list_sources(self,w,r): return {"items":[v.to_dict() for v in self.engine.list_sources(w,r)]}
    def create_meeting(self,w,p): return self.engine.create_meeting(w,p).to_dict()
    def run_meeting(self,w,m): return self.engine.run_meeting(w,m).to_dict()
    def get_meeting(self,w,m):
        value=self.engine.get_meeting(w,m); return value.to_dict() if value else None
    def list_meetings(self,w): return {"items":[v.to_dict() for v in self.engine.list_meetings(w)]}
    def get_minutes(self,w,m): return self.engine.get_minutes(w,m)
    def list_plans(self,w,m): return {"items":[v.to_dict() for v in self.engine.list_plans(w,m)]}
    def decide(self,w,m,plan_id=None):
        value=self.engine.decide(w,m,plan_id); return value.to_dict() if value else {"status":"USER_INPUT_REQUIRED"}
    def get_decision(self,w,m):
        value=self.engine.get_decision(w,m); return value.to_dict() if value else None
    def get_execution_plan(self,w,m):
        value=self.engine.get_execution_plan(w,m); return value.to_dict() if value else None
