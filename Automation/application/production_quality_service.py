class ProductionQualityService:
    """Safe DTO boundary for Mission 146-147."""
    def __init__(self, engine): self.engine=engine
    def create_brief(self,w,p): return self.engine.create_brief(w,p).to_dict()
    def list_briefs(self,w): return {"items":[v.to_dict() for v in self.engine.list_briefs(w)]}
    def get_brief(self,w,i):
        v=self.engine.get_brief(w,i); return v.to_dict() if v else None
    def generate(self,w,i,p): return {"items":[v.to_dict() for v in self.engine.generate(w,i,(p or {}).get("candidate_type","PACKAGE"))]}
    def candidates(self,w,i): return {"items":[v.to_dict() for v in self.engine.list_candidates(w,i)]}
    def compare(self,w,i): return self.engine.compare(self.engine.list_candidates(w,i))
    def create_rubric(self,w,p): return self.engine.create_rubric(w,p).to_dict()
    def review(self,w,c,p): return self.engine.review(w,c,p["rubric_id"],p["reviewer_id"],p["review_type"])
    def reviews(self,w,c): return {"items":self.engine.list_reviews(w,c)}
    def score(self,w,c,p): return self.engine.score(w,c,p["rubric_id"],p["reviewer_id"]).to_dict()
    def get_score(self,w,c):
        v=self.engine.get_score(w,c); return v.to_dict() if v else None
    def improve(self,w,c): return self.engine.improve(w,c).to_dict()
    def improvements(self,w,c): return {"items":self.engine.list_improvements(w,c)}
    def final(self,w,b):
        v=self.engine.get_final(w,b) or self.engine.final_report(w,b); return v.to_dict()
    def select(self,w,b,c): return self.engine.select(w,b,c).to_dict()
