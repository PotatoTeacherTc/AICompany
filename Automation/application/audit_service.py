import uuid
from datetime import datetime
from core.audit_repository import InMemoryAuditRepository

class AuditService:
    sensitive={'password','access_token','refresh_token','token','password_hash','prompt'}
    def __init__(self,repository=None): self.repository=repository or InMemoryAuditRepository()
    def record(self,user_id=None,workspace_id=None,action='',resource_type='',resource_id='',metadata=None):
        try:
            from api.request_context import current_context
            safe={k:v for k,v in (metadata or {}).items() if k.lower() not in self.sensitive and isinstance(v,(str,int,float,bool,type(None)))}
            context=current_context(); self.repository.save({'id':uuid.uuid4().hex,'user_id':user_id,'workspace_id':workspace_id,'action':action,'resource_type':resource_type,'resource_id':resource_id,'created_at':datetime.now().isoformat(),'correlation_id':context.correlation_id if context else None,'metadata':safe})
        except Exception: pass
    def query(self,workspace_id,**filters): return self.repository.query(workspace_id=workspace_id,**filters)
