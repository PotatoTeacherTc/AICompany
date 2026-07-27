import json, os
from abc import ABC, abstractmethod
from pathlib import Path

class SessionRepository(ABC):
    @abstractmethod
    def save(self, session): pass
    @abstractmethod
    def get(self, session_id): pass
    @abstractmethod
    def list_by_user(self, user_id): pass

class InMemorySessionRepository(SessionRepository):
    def __init__(self, sessions=None): self.items={s['session_id']:dict(s) for s in sessions or []}
    def save(self, session): self.items[session['session_id']]=dict(session)
    def get(self, session_id): return dict(self.items[session_id]) if session_id in self.items else None
    def list_by_user(self, user_id): return [dict(s) for s in self.items.values() if s['user_id']==user_id]

class FileSessionRepository(InMemorySessionRepository):
    def __init__(self,path):
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
        try: data=json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else []
        except (OSError,json.JSONDecodeError): data=[]
        super().__init__(data if isinstance(data,list) else [])
    def save(self,session):
        super().save(session); temp=self.path.with_suffix(self.path.suffix+'.tmp'); temp.write_text(json.dumps(list(self.items.values())),encoding='utf-8'); os.replace(temp,self.path)
