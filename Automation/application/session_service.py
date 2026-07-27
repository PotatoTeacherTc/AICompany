import hashlib, secrets
from datetime import datetime, timedelta
from core.session_repository import InMemorySessionRepository

class SessionService:
    def __init__(self, repository=None, lifetime_seconds=86400, now=None):
        self.repository=repository or InMemorySessionRepository(); self.lifetime_seconds=lifetime_seconds; self.now=now or datetime.now
    def create(self,user_id):
        token=secrets.token_urlsafe(32); session={'session_id':secrets.token_hex(16),'user_id':user_id,'refresh_token_hash':self._hash(token),'created_at':self.now().isoformat(),'expires_at':(self.now()+timedelta(seconds=self.lifetime_seconds)).isoformat(),'revoked':False,'rotated':False}; self.repository.save(session); return session,token
    def rotate(self,token):
        session=next((s for s in self.repository.items.values() if s['refresh_token_hash']==self._hash(token)),None)
        if not session or session['revoked'] or session['rotated'] or session['expires_at']<=self.now().isoformat(): return None
        session=dict(session); session['rotated']=True; self.repository.save(session); return self.create(session['user_id'])
    def revoke(self,session_id,user_id):
        s=self.repository.get(session_id)
        if not s or s['user_id']!=user_id:return False
        s['revoked']=True; self.repository.save(s); return True
    def list(self,user_id): return [{k:v for k,v in s.items() if k!='refresh_token_hash'} for s in self.repository.list_by_user(user_id)]
    @staticmethod
    def _hash(token): return hashlib.sha256(token.encode()).hexdigest()
