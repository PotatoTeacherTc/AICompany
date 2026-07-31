"""Redis transport for the existing persistent Job contract."""

import os

from core.task_queue import JobStatus, PersistentJobQueue


class QueueConfig:
    def __init__(self, backend="memory", redis_url=None, namespace="aicompany", blocking_timeout=1):
        self.backend = str(backend).lower()
        self.redis_url = redis_url
        self.namespace = namespace
        self.blocking_timeout = blocking_timeout

    @classmethod
    def from_environment(cls, environment=None):
        values = os.environ if environment is None else environment
        try:
            timeout = int(values.get("AICOMPANY_QUEUE_BLOCKING_TIMEOUT", "1"))
        except (TypeError, ValueError):
            raise ValueError("invalid_queue_blocking_timeout") from None
        return cls(
            values.get("AICOMPANY_QUEUE_BACKEND", "memory"),
            values.get("REDIS_URL"),
            values.get("AICOMPANY_QUEUE_NAMESPACE", "aicompany"),
            timeout,
        ).validate()

    def validate(self):
        if self.backend not in {"memory", "redis"}:
            raise ValueError("unsupported_queue_backend")
        if self.backend == "redis" and not self.redis_url:
            raise ValueError("redis_queue_url_required")
        if not isinstance(self.namespace, str) or not self.namespace or not self.namespace.replace("-", "").replace("_", "").isalnum():
            raise ValueError("invalid_queue_namespace")
        if not isinstance(self.blocking_timeout, int) or not 0 <= self.blocking_timeout <= 30:
            raise ValueError("invalid_queue_blocking_timeout")
        return self


class QueueFactory:
    @staticmethod
    def create(config, repository, *, redis_client=None, workspace_ids=(), logger=None):
        config.validate()
        if config.backend == "memory":
            return PersistentJobQueue(repository, workspace_ids=workspace_ids, logger=logger)
        if redis_client is None:
            raise ValueError("redis_queue_client_required")
        return RedisJobQueue(
            repository,
            redis_client,
            namespace=config.namespace,
            blocking_timeout=config.blocking_timeout,
            logger=logger,
        )


class RedisJobQueue(PersistentJobQueue):
    """Redis FIFO/processing lists with PostgreSQL-compatible Job state."""

    def __init__(self, repository, redis_client, namespace="aicompany", blocking_timeout=1, logger=None):
        super().__init__(repository, logger=logger)
        self.redis = redis_client
        self.namespace = namespace
        self.blocking_timeout = blocking_timeout

    def enqueue(self, workspace_id, mission_id, target_id, idempotency_key, retry_state=None):
        try:
            for value in self.repository.list("job", workspace_id):
                restored = self._restore(value)
                if restored is not None and restored.idempotency_key == idempotency_key:
                    self._jobs[restored.job_id] = restored
                    return restored
            job = super().enqueue(workspace_id, mission_id, target_id, idempotency_key, retry_state)
            self.redis.rpush(self._pending(workspace_id), job.job_id)
            return job
        except (TypeError, ValueError):
            raise
        except Exception:
            raise RuntimeError("redis_queue_unavailable") from None

    def get(self, job_id, workspace_id):
        try:
            value = self.repository.get("job", job_id, workspace_id)
            job = self._restore(value) if value else None
            if job is not None:
                self._jobs[job.job_id] = job
            return job
        except Exception:
            raise RuntimeError("job_repository_unavailable") from None

    def list(self, workspace_id):
        try:
            jobs = []
            for value in self.repository.list("job", workspace_id):
                job = self._restore(value)
                if job is not None:
                    self._jobs[job.job_id] = job
                    jobs.append(job)
            return jobs
        except Exception:
            raise RuntimeError("job_repository_unavailable") from None

    def claim(self, workspace_id, worker_id):
        try:
            value = self.redis.blmove(
                self._pending(workspace_id), self._processing(workspace_id),
                self.blocking_timeout, "LEFT", "RIGHT",
            )
            if value is None:
                return None
            job_id = value.decode("utf-8") if isinstance(value, bytes) else str(value)
            payload = self.repository.get("job", job_id, workspace_id)
            job = self._restore(payload) if payload else None
            if job is None or job.status != JobStatus.PENDING:
                self.acknowledge(job_id, workspace_id)
                return None
            self._jobs[job_id] = job
            return super().claim(workspace_id, worker_id)
        except RuntimeError:
            raise
        except Exception:
            raise RuntimeError("redis_queue_unavailable") from None

    def complete(self, job_id, workspace_id, worker_id, result):
        value = super().complete(job_id, workspace_id, worker_id, result)
        self.acknowledge(job_id, workspace_id)
        return value

    def fail(self, job_id, workspace_id, worker_id, result, retry_state=None):
        value = super().fail(job_id, workspace_id, worker_id, result, retry_state)
        self.acknowledge(job_id, workspace_id)
        return value

    def requeue(self, job_id, workspace_id):
        value = super().requeue(job_id, workspace_id)
        try:
            self.redis.rpush(self._pending(workspace_id), job_id)
            return value
        except Exception:
            raise RuntimeError("redis_queue_unavailable") from None

    def cancel(self, job_id, workspace_id):
        value = super().cancel(job_id, workspace_id)
        try:
            self.redis.lrem(self._pending(workspace_id), 0, job_id)
            return value
        except Exception:
            raise RuntimeError("redis_queue_unavailable") from None

    def acknowledge(self, job_id, workspace_id):
        try:
            self.redis.lrem(self._processing(workspace_id), 0, job_id)
        except Exception:
            raise RuntimeError("redis_queue_unavailable") from None

    def health(self):
        try:
            return {"ok": bool(self.redis.ping()), "backend": "redis"}
        except Exception:
            return {"ok": False, "backend": "redis"}

    def close(self):
        try:
            self.redis.close()
        except Exception:
            return None

    def _pending(self, workspace_id):
        return f"{self.namespace}:queue:{workspace_id}:pending"

    def _processing(self, workspace_id):
        return f"{self.namespace}:queue:{workspace_id}:processing"


def connect_redis(redis_url):
    try:
        import redis

        return redis.Redis.from_url(redis_url, decode_responses=False)
    except Exception:
        raise RuntimeError("redis_queue_connection_failed") from None
