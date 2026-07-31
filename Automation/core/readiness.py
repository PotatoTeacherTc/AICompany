"""Safe production readiness probes over existing local dependencies."""


class RedisWorkerReadiness:
    def __init__(self, redis_client, namespace="aicompany", required_workers=1, ttl_seconds=15):
        if not isinstance(required_workers, int) or required_workers < 0:
            raise ValueError("invalid_required_workers")
        if not isinstance(ttl_seconds, int) or not 2 <= ttl_seconds <= 300:
            raise ValueError("invalid_worker_heartbeat_ttl")
        self.redis = redis_client
        self.namespace = namespace
        self.required_workers = required_workers
        self.ttl_seconds = ttl_seconds

    def touch(self, worker_id):
        if not isinstance(worker_id, str) or not worker_id:
            raise ValueError("invalid_worker_id")
        try:
            self.redis.set(self._key(worker_id), "available", ex=self.ttl_seconds)
        except Exception:
            raise RuntimeError("worker_readiness_unavailable") from None

    def health(self):
        try:
            count = sum(1 for _ in self.redis.scan_iter(match=self._key("*"), count=100))
            return {
                "ok": count >= self.required_workers,
                "available_workers": count,
                "required_workers": self.required_workers,
            }
        except Exception:
            return {"ok": False, "available_workers": 0, "required_workers": self.required_workers}

    def _key(self, worker_id):
        return f"{self.namespace}:readiness:worker:{worker_id}"
