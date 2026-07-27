import json, os, uuid
from abc import ABC, abstractmethod
from pathlib import Path
from datetime import datetime

class AuditRepository(ABC):
    @abstractmethod
    def save(self,event): pass
    @abstractmethod
    def query(self,**filters): pass

class InMemoryAuditRepository(AuditRepository):
    def __init__(self,events=None): self.items=[dict(e) for e in events or []]
    def save(self,event): self.items.append(dict(event))
    def query(self,workspace_id=None,action=None,start_at=None,end_at=None,limit=None,offset=0,resource_type=None,resource_id=None,user_id=None):
        actions={action} if isinstance(action,str) else set(action or [])
        items=[dict(e) for e in self.items if (workspace_id is None or e['workspace_id']==workspace_id) and (not actions or e['action'] in actions) and (resource_type is None or e['resource_type']==resource_type) and (resource_id is None or e['resource_id']==resource_id) and (user_id is None or e['user_id']==user_id) and (start_at is None or e['created_at']>=start_at) and (end_at is None or e['created_at']<=end_at)]
        items.sort(key=lambda e:e['created_at'],reverse=True); return items[max(0,offset):None if limit is None else max(0,offset)+max(0,limit)]

class FileAuditRepository(InMemoryAuditRepository):
    def __init__(self,path):
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
        try:data=json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else []
        except (OSError,json.JSONDecodeError):data=[]
        super().__init__(data if isinstance(data,list) else [])
    def save(self,event):
        super().save(event); temp=self.path.with_suffix(self.path.suffix+'.tmp'); temp.write_text(json.dumps(self.items),encoding='utf-8'); os.replace(temp,self.path)
