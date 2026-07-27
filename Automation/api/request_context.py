import contextvars, re, uuid
from dataclasses import dataclass

_context=contextvars.ContextVar('request_context',default=None)
_safe=re.compile(r'^[A-Za-z0-9_-]{8,128}$')
@dataclass(frozen=True)
class RequestContext:
    correlation_id:str
    @classmethod
    def create(cls,value=None): return cls(value if isinstance(value,str) and _safe.fullmatch(value) else uuid.uuid4().hex)
def set_context(context): return _context.set(context)
def reset_context(token): _context.reset(token)
def current_context(): return _context.get()
