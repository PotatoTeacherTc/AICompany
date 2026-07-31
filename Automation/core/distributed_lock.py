"""Owner-verified TTL locks for local and Redis execution."""

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
import time
import uuid


_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""
_RENEW_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('pexpire', KEYS[1], ARGV[2])
end
return 0
"""


@dataclass(frozen=True)
class LockLease:
    workspace_id: str
    job_id: str
    owner_token: str
    ttl_seconds: float


class InMemoryDistributedLock:
    def __init__(self, clock=None):
        self.clock = clock or time.monotonic
        self._values = {}
        self._lock = RLock()

    def acquire(self, workspace_id, job_id, ttl_seconds, owner_token=None):
        _validate(workspace_id, job_id, ttl_seconds)
        token = owner_token or uuid.uuid4().hex
        key = (workspace_id, job_id)
        with self._lock:
            current = self._values.get(key)
            if current and current[1] > self.clock():
                return None
            self._values[key] = (token, self.clock() + ttl_seconds)
        return LockLease(workspace_id, job_id, token, ttl_seconds)

    def release(self, lease):
        with self._lock:
            current = self._values.get((lease.workspace_id, lease.job_id))
            if not current or current[0] != lease.owner_token:
                return False
            del self._values[(lease.workspace_id, lease.job_id)]
            return True

    def renew(self, lease):
        with self._lock:
            key = (lease.workspace_id, lease.job_id)
            current = self._values.get(key)
            if not current or current[0] != lease.owner_token or current[1] <= self.clock():
                return False
            self._values[key] = (lease.owner_token, self.clock() + lease.ttl_seconds)
            return True


class RedisDistributedLock:
    def __init__(self, redis_client, namespace="aicompany"):
        self.redis = redis_client
        self.namespace = namespace

    def acquire(self, workspace_id, job_id, ttl_seconds, owner_token=None):
        _validate(workspace_id, job_id, ttl_seconds)
        token = owner_token or uuid.uuid4().hex
        try:
            acquired = self.redis.set(
                self._key(workspace_id, job_id), token,
                nx=True, px=max(1, int(ttl_seconds * 1000)),
            )
            return LockLease(workspace_id, job_id, token, ttl_seconds) if acquired else None
        except Exception:
            raise RuntimeError("distributed_lock_unavailable") from None

    def release(self, lease):
        try:
            return bool(self.redis.eval(
                _RELEASE_SCRIPT, 1,
                self._key(lease.workspace_id, lease.job_id), lease.owner_token,
            ))
        except Exception:
            raise RuntimeError("distributed_lock_unavailable") from None

    def renew(self, lease):
        try:
            return bool(self.redis.eval(
                _RENEW_SCRIPT, 1,
                self._key(lease.workspace_id, lease.job_id), lease.owner_token,
                max(1, int(lease.ttl_seconds * 1000)),
            ))
        except Exception:
            raise RuntimeError("distributed_lock_unavailable") from None

    def _key(self, workspace_id, job_id):
        return f"{self.namespace}:lock:{workspace_id}:{job_id}"


def _validate(workspace_id, job_id, ttl_seconds):
    if not isinstance(workspace_id, str) or not workspace_id:
        raise ValueError("workspace_id is invalid")
    if not isinstance(job_id, str) or not job_id:
        raise ValueError("job_id is invalid")
    if not isinstance(ttl_seconds, (int, float)) or isinstance(ttl_seconds, bool) or ttl_seconds <= 0 or ttl_seconds > 3600:
        raise ValueError("lock ttl is invalid")
